#!/usr/bin/env python3
"""Turn your own copy of Xenoblade Chronicles X: Definitive Edition 1.0.1 into a native Mac app, in one command (or,
experimentally, an unsigned iPhone / iPad IPA with --platform ios).

    python3 build_app.py <game.xci | base.nsp> -k <prod.keys> [--update <update.nsp>] [--firmware <folder>]
                         [-o <output .app | .ipa>] [--work <folder>] [--title-keys <title.keys>] [--platform macos|ios]

Supported: Xenoblade Chronicles X: Definitive Edition, base 0100453019AA8000 (v0) with update 1.0.1 0100453019AA8800
(v65536), as
  - one XCI holding the base and the update (as the game card does): nothing else is needed;
  - a base NSP (or a base-only XCI) with --update <the 1.0.1 update NSP>.
The game is recognised by the title IDs and versions inside the files (games/xcx/scripts/identify_game.py), never by
the file name; other titles and other versions are refused with a message naming what the file is. NCAs of an NSP are
decrypted with the NSP's common ticket, or, when it has none (or a personalized one), with --title-keys <title.keys>.

Steps, each one checked against games/xcx/assets/expected-v1.0.1.json, so a damaged dump or another version stops the
build at the first wrong file (a different dump of the same version is accepted: main.npdm by Nintendo's ACID signature
or, re-signed by a dump tool, once the code modules match; shared fonts by a verified, valid BFTTF structure):
  1. code: the 1.0.1 ExeFS (games/xcx/scripts/extract_code.py: FS header, hash table and every block verified), the NSO
     segments checked and inventoried (prepare_modules.py), the three code modules rewritten for Apple silicon
     (games/xcx/third_party/d2r/patch_nso.py) and the rewrite checked twice: structure (structck.py) and semantics in
     an emulator with random states and mutation controls (equiv.py)
  2. fonts: the six system shared fonts, from the system update the XCI carries (its update partition) or from
     --firmware <unpacked firmware NCA folder> (needed for an NSP); every NCA level and each font verified
     (extract_shared_fonts.py)
  3. data: the base RomFS and the 1.0.1 BKTR patch merged into one 14.3 GB image, all six IVFC levels verified
     (merge_romfs.py)
  4. prepare: the runtime's generated tables, RomFS index and fonts config (make prepare: scripts/prepare_runtime.py)
  5. runtime: the game binary (make runtime)
  6. shader tools: the prebuilt shader translator, checked against its SHA-256, and SPIRV-Cross at its pinned commit,
     compiled with clang (make shader-tools)
  7. app: the launcher and the icon from the game's own control data (make launcher icon), then a self-contained app:
     the code, fonts and RomFS index cloned into it, the RomFS image as a data bundle signed once in the work folder and
     cloned into Contents/Helpers (APFS clones: no extra space on the same volume), ad-hoc signed, registered, and run
     once in its check-only mode so that macOS's first-run scan happens now and not at the player's first start.
The Makefile in games/xcx stays the developer path (make prepare runtime app): this script runs the same targets.

Every intermediate file is in the work folder (default: "<game file folder>/XCX_build"); a second run reuses what is
already done and correct. The app needs no work folder once built and may be moved, also to /Applications. Saves, the
shader cache, the RomFS check record and the log go to the user's Library (~/Library/Application Support/
MacNativeGames/XCX, ~/Library/Logs/MacNativeGames/xcx.log), so the signed app stays read-only. Nothing from the game is
in this repository; everything is made from your files on your Mac. Key values are never printed.

Needs: macOS 26 or later on Apple silicon, the Xcode command line tools (Xcode for llvm-objdump), Homebrew with
    brew install opus libvpx python@3.14
and network access on the first run (the build's Python packages, SPIRV-Cross).
--platform ios (EXPERIMENTAL) makes an unsigned IPA for sideloading instead (default "<game file folder>/Xenoblade
Chronicles X.ipa"): steps 1-6 as above (the Mac runtime exports the patched code for the device), then
games/xcx/ios/tools/build_ipa.py: device Opus / libvpx (CMake, network once), the Xcode project (xcodegen), the build,
and the IPA. It needs Xcode 26 or later with the iOS SDK, xcodegen and CMake (brew install xcodegen cmake). The IPA
carries no game image: the 14.3 GB RomFS is installed once into the app's Documents (ios/tools/install_device.py
--game-data, or Finder's file sharing). Shaders are not translated ahead of time yet and a phone cannot translate them,
so only the variants this Mac has already translated are carried and most of the picture is missing.
"""
import argparse
import contextlib
import glob
import hashlib
import json
import os
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
GAME = REPO / 'games/xcx'
sys.path.insert(0, str(GAME / 'scripts'))
import app_bundle   # noqa: E402  standard library only: the app's name, Info.plist and the files it carries

EXPECTED = json.loads((GAME / 'assets/expected-v1.0.1.json').read_text())
VERSION = app_bundle.VERSION                     # 'v101': the work folder's version folder (runtime/game_version.h)
BASE_TITLE = EXPECTED['title_id']                # 0100453019AA8000
UPDATE_TITLE = EXPECTED['update_title_id']       # 0100453019AA8800
UPDATE_VERSION = EXPECTED['version']             # 65536 = 1.0.1
DISPLAY = EXPECTED['display_version']
GAME_NAME = 'Xenoblade Chronicles X: Definitive Edition'
# Other games a file may turn out to be, for the message when it is not this one (title IDs of the base games)
OTHER_TITLES = {'0100F2C0115B6000': 'The Legend of Zelda: Tears of the Kingdom',
                '01007EF00011E000': 'The Legend of Zelda: Breath of the Wild', '01004A4010FEA000': 'Bayonetta 3',
                '0100E95004038000': 'Xenoblade Chronicles 2', '0100FF500E34A000': 'Xenoblade Chronicles: Definitive Edition',
                '010074F013262000': 'Xenoblade Chronicles 3'}
WORK_NAME = 'XCX_build'
SPIRV_CROSS_COMMIT = 'aa217aeb6c9f0ace7a0ab233b28807edf45eb165'   # the Makefile's SPIRV_CROSS_COMMIT
# The Python packages of the build: games/xcx/requirements.txt, plus unicorn for the rewrite's semantic check (equiv.py)
EXTRA_PACKAGES = ['unicorn==2.1.4']
TOOL_PATHS = ['/opt/homebrew/bin', '/usr/local/bin']
LSREGISTER = '/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister'
APP_NAME = app_bundle.APP_NAME
GB = 1 << 30

# Nintendo's public ACID signing key (generation 0): a dump's main.npdm is genuine when this key verifies its ACID
# signature, which is the check the Horizon loader itself makes before it starts a title (Atmosphere
# libraries/libstratosphere/source/fssystem/fssystem_crypto_configuration.cpp). From the original project's build_app.py.
NINTENDO_ACID_MODULUS = bytes.fromhex(
    'DDC8DDF24E6DF0CA9EC75DC77BADFE7D238969B6F206A20288E15591ABCB4D50'
    '2EFC9D9476D64CD8FF10FA5E930AB457AC51C71666F41A54C2C5043D1BFE3020'
    '8AAC6F6FF5C7B668B8C9406B42AD1121E78BE9750186E4489B0A0AF87FE887F2'
    '8201E6A30FE466AE833F4E9F5E0130A400B99AAE5F03CC1860E5EF3B5E1516FE'
    '1C8278B52F477C0666885D35A2672010E76C4368D3E45A682A5AE26D73B03153'
    '1C200944F51A9D22BE12A17711E2A1CD409AA28B609BEFA0D34863A2F8A32C08'
    '56522E6019675AA79FDC3F3F692B316AB7884A148480333C9D44B73F4CE175EA'
    '37EAE81E7C77B7C61AA2F09F1061CD7B5B324C37EFB17168530AED517D3522FD'
)


class BuildError(Exception):
    pass


def say(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def run(cmd, log, cwd=None, env=None, quiet=True, timeout=None):
    """Run a step with its whole output in log; on failure show the end of it. quiet: a line a minute meanwhile."""
    cmd = [str(c) for c in cmd]
    log.parent.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def beat():
        t0 = time.time()
        while not done.wait(60):
            say(f'   ... still running ({(time.time() - t0) / 60:.0f} min)')
    if quiet:
        threading.Thread(target=beat, daemon=True).start()
    try:
        with open(log, 'w') as f:
            p = subprocess.run(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
    finally:
        done.set()
    if p.returncode:
        tail = log.read_text(errors='replace').splitlines()[-25:]
        name = Path(cmd[1] if Path(cmd[0]).name.startswith('python') else cmd[0]).name
        raise BuildError(f'{name} failed (exit {p.returncode}); full output: {log}\n' + '\n'.join(tail))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def check(what, got, want):
    if got != want:
        raise BuildError(f'{what}: SHA-256 {str(got)[:16]}... is not the expected {str(want)[:16]}... '
                         f'(is this {GAME_NAME} {DISPLAY}, and is the dump complete?)')


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def last_json(log):
    """The tool's report: its last line that is a JSON object."""
    for line in reversed(log.read_text(errors='replace').splitlines()):
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return None


# ---- step timing ----
TIMES = []   # (step, seconds, 'built' | 'reused' | 'checked')


@contextlib.contextmanager
def timed(name):
    """Time one step; the step says whether it did the work (state['built'] = True), reused what was there (False), or
    only checks (a string: how it is reported)."""
    state = dict(built=False)
    t0 = time.time()
    yield state
    TIMES.append((name, time.time() - t0, state['built'] if isinstance(state['built'], str) else
                  'built' if state['built'] else 'reused'))


def say_times(work, total):
    width = max(len(n) for n, _, _ in TIMES)
    say('step times:')
    for name, seconds, how in TIMES:
        say(f'   {name:<{width}}  {seconds:7.1f} s  {how}')
    say(f'   {"total":<{width}}  {total:7.1f} s')
    path = work / 'logs/build-times.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(when=time.strftime('%Y-%m-%d %H:%M:%S'), total_s=round(total, 1),
                                    steps=[dict(step=n, seconds=round(s, 1), result=h) for n, s, h in TIMES]), indent=2) + '\n')


# ---- genuine dumps (from the original project's build_app.py) ----
def _mgf1_sha256(seed, length):
    blocks = b''
    for counter in range((length + 31) // 32):
        blocks += hashlib.sha256(seed + counter.to_bytes(4, 'big')).digest()
    return blocks[:length]


def _pss_sha256_valid(signature, modulus, message):
    """RSA-2048 PSS/SHA-256 (32-byte salt, MGF1-SHA256), the scheme of an ACID block's signature."""
    size = len(modulus)
    if size < 256 or len(signature) != size:
        return False
    modulus_number = int.from_bytes(modulus, 'big')
    number = int.from_bytes(signature, 'big')
    if number >= modulus_number:
        return False
    encoded = pow(number, 0x10001, modulus_number).to_bytes(size, 'big')
    if encoded[-1] != 0xBC:
        return False
    hashed, mask = encoded[size - 33:size - 1], _mgf1_sha256(encoded[size - 33:size - 1], size - 33)
    db = bytes([(encoded[0] ^ mask[0]) & 0x7F]) + bytes(a ^ b for a, b in zip(encoded[1:size - 33], mask[1:]))
    if db[:-33].strip(b'\x00') or db[-33] != 0x01:
        return False
    return hashlib.sha256(b'\x00' * 8 + hashlib.sha256(message).digest() + db[-32:]).digest() == hashed


def npdm_signed(path):
    """True when the npdm's ACID block carries a valid Nintendo signature over its own access control."""
    data = path.read_bytes()
    if len(data) < 0x80 or data[:4] != b'META' or int.from_bytes(data[0x04:0x08], 'little') != 0:
        return False
    acid, acid_size = struct.unpack_from('<II', data, 0x78)
    if acid_size < 0x200 or acid + acid_size > len(data) or data[acid + 0x200:acid + 0x204] != b'ACID':
        return False
    size = struct.unpack_from('<I', data, acid + 0x204)[0]
    if size < 0x100 or acid + 0x100 + size > len(data):
        return False
    return _pss_sha256_valid(data[acid:acid + 0x100], NINTENDO_ACID_MODULUS, data[acid + 0x100:acid + 0x100 + size])


def npdm_program_id(path):
    """The program ID in a well-formed npdm's ACI0 block (16 upper-case hex digits), else None."""
    data = path.read_bytes()
    if len(data) < 0x80 or data[:4] != b'META':
        return None
    aci, aci_size = struct.unpack_from('<II', data, 0x70)
    if aci_size < 0x20 or aci + aci_size > len(data) or data[aci:aci + 4] != b'ACI0':
        return None
    return f'{struct.unpack_from("<Q", data, aci + 0x10)[0]:016X}'


def npdm_kind(path):
    """How a main.npdm that is not the reference dump's file is accepted: 'signed' (Nintendo's ACID signature: another
    genuine dump), 'resigned' (a well-formed npdm of this title whose ACID a dump tool signed again), else None. The game
    never reads main.npdm here and the code modules are checked by hash, so a re-signed one is accepted only alongside
    matching code."""
    if npdm_signed(path):
        return 'signed'
    return 'resigned' if npdm_program_id(path) == BASE_TITLE else None


def exefs_matches(exefs):
    """True when the extracted ExeFS holds the expected code modules and a main.npdm of this version."""
    want = EXPECTED['exefs_sha256']
    for name, digest in want.items():
        if name != 'main.npdm' and (not (exefs / name).is_file() or sha256(exefs / name) != digest):
            return False
    npdm = exefs / 'main.npdm'
    return npdm.is_file() and (sha256(npdm) == want['main.npdm'] or npdm_kind(npdm) is not None)


def bfttf_valid(path):
    """A structurally valid shared font, by the same size check scripts/extract_shared_fonts.py makes."""
    data = path.read_bytes()
    if len(data) < 12:
        return False
    magic, size = struct.unpack_from('>II', data)
    key = magic ^ 0x7f9a0218
    if (size ^ key) + 8 != len(data):
        return False
    return struct.unpack_from('>I', data, 8)[0] ^ key in (0x00010000, 0x4F54544F)


# ---- files ----
_libc = None


def clonefile(src, dst):
    """macOS clonefile(2): an APFS clone of a file or a whole folder (no extra space) in one call. False when it
    cannot: another volume or file system, or dst exists."""
    global _libc
    if _libc is None:
        import ctypes
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    return _libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0


def clone(src, dst):
    """APFS clone when on the same volume (no extra space), else a copy (cp -c falls back the same way); an existing
    dst is replaced."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if not clonefile(src, dst):
        shutil.copy2(src, dst)


def copy_tree(src, dst):
    """The files of src in dst: one clone of the whole folder when dst does not exist yet, else file by file."""
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if clonefile(src, dst):
            return
    for p in sorted(src.rglob('*')):
        if p.is_file():
            clone(p, dst / p.relative_to(src))


def remove_tree(path):
    """Remove a bundle. Finder can put .DS_Store back while it is being deleted, which makes the last rmdir fail."""
    for _ in range(5):
        try:
            shutil.rmtree(path)
            return
        except OSError as error:
            if error.errno != 66 or not path.exists():
                raise
            time.sleep(0.2)
    shutil.rmtree(path)


def existing(path):
    """The nearest existing folder of path (for the free space of a folder not made yet)."""
    path = path.resolve()
    while not path.exists():
        path = path.parent
    return path


def free_gb(path):
    return shutil.disk_usage(existing(path)).free / GB


def file_stamp(path):
    st = path.stat()
    return [st.st_size, st.st_mtime_ns]


# ---- inputs ----
def input_kind(path):
    """'xci' or 'nsp' by the file's own header (HEAD at 0x100, PFS0 at 0), never by its name."""
    try:
        with path.open('rb') as f:
            head = f.read(0x104)
    except OSError as e:
        raise BuildError(f'cannot read {path}: {e}') from None
    if head[:4] == b'PFS0':
        return 'nsp'
    if head[0x100:0x104] == b'HEAD':
        return 'xci'
    raise BuildError(f'{path.name} is not an XCI or NSP game file (expected the game card image or an NSP of '
                     f'{GAME_NAME}; the file is recognised by its contents, not its name)')


def title_key_rights(path):
    """The rights IDs (upper-case hex) listed in a title.keys file; the keys themselves are not read into anything."""
    rights = set()
    for line in path.read_text(errors='replace').splitlines():
        name = line.split('#', 1)[0].split('=', 1)[0].strip()
        if len(name) == 32 and all(c in '0123456789abcdefABCDEF' for c in name):
            rights.add(name.upper())
    return rights


def source_matches(manifest, source):
    data = read_json(manifest)
    try:
        stat = source.stat()
        return (data['source'] == str(source.resolve()) and data['source_size'] == stat.st_size
                and data['source_mtime_ns'] == stat.st_mtime_ns)
    except (OSError, TypeError, KeyError):
        return False


# ---- requirements ----
def prerequisites():
    """The Mac's tools, checked at the start with what to do about each one missing (from the original project's)."""
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise BuildError('needs a Mac with Apple silicon (this is '
                         f'{platform.system()} on {platform.machine()})')
    release = platform.mac_ver()[0]
    if int(release.split('.')[0] or 0) < 26:
        raise BuildError(f'needs macOS 26 or later; this is macOS {release}')
    if subprocess.run(['xcrun', '--find', 'clang'], capture_output=True).returncode:
        raise BuildError('the Xcode command line tools are missing: xcode-select --install')
    cxx = subprocess.run(['xcrun', 'clang++', '-std=c++11', '-x', 'c++', '-fsyntax-only', '-'], input='#include <string>\n',
                         capture_output=True, text=True)
    if cxx.returncode:   # SPIRV-Cross is C++; without the SDK's libc++ headers it fails much later
        raise BuildError('clang++ finds no C++ standard library headers, so the developer tools or their SDK are incomplete. '
                         'With Xcode: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer; with the command line '
                         'tools only: sudo rm -rf /Library/Developer/CommandLineTools && xcode-select --install. '
                         f'clang++ said: {cxx.stderr.strip()[:300]}')
    if subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True).returncode:
        raise BuildError('llvm-objdump not found (xcrun --find llvm-objdump): the rewrite check needs it; install Xcode '
                         'and select it: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer')
    for tool in ('make', 'codesign', 'git', 'rsync'):
        if not shutil.which(tool, path=os.pathsep.join(TOOL_PATHS + ['/usr/bin', '/bin'])):
            raise BuildError(f'{tool} not found: xcode-select --install')
    brew = Path('/opt/homebrew/opt')   # the Makefile links these static libraries
    missing = [p for p, lib in (('opus', 'libopus.a'), ('libvpx', 'libvpx.a')) if not (brew / p / 'lib' / lib).is_file()]
    if missing:
        raise BuildError(f'Homebrew packages missing: brew install {" ".join(missing)} (https://brew.sh for Homebrew)')
    py = next((p for p in (shutil.which(n, path=os.pathsep.join(TOOL_PATHS)) for n in ('python3.14', 'python3.15')) if p), None)
    if not py and sys.version_info >= (3, 14):
        py = sys.executable
    if not py:
        raise BuildError('Python 3.14 not found (games/xcx/requirements.txt is pinned for it): brew install python@3.14')
    return Path(py)


def make_safe(work):
    """make splits paths at spaces: a work folder whose path has one is passed to make through a link without any."""
    if not any(c in str(work) for c in ' #$:=%\\\'"'):
        return work
    link = Path.home() / f'Library/Caches/MacNativeGames/xcx-work-{hashlib.sha256(str(work).encode()).hexdigest()[:12]}'
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and link.resolve() == work.resolve():
        return link
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(work)
    return link


def prepare_venv(work, py, logs):
    """<work>/venv with games/xcx/requirements.txt and unicorn; installed again only when the list changes."""
    venv = work / 'venv'
    vpy = venv / 'bin/python'
    req = GAME / 'requirements.txt'
    want = dict(requirements=sha256(req), extra=EXTRA_PACKAGES, python=str(py))
    stamp = venv / 'xcx-packages.json'
    probe = [vpy, '-c', 'import cryptography, lz4.block, capstone, unicorn']
    if vpy.exists() and read_json(stamp) == want and subprocess.run(probe, capture_output=True).returncode == 0:
        return vpy, False
    if not vpy.exists():
        say('Python environment for the build tools')
        run([py, '-m', 'venv', venv], logs / 'venv.log')
    say('installing the build\'s Python packages (games/xcx/requirements.txt, unicorn)')
    try:
        run([vpy, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', '-r', req, *EXTRA_PACKAGES], logs / 'pip.log')
    except BuildError as e:
        raise BuildError(f'the Python packages could not be installed (network access is needed once): {e}') from None
    if subprocess.run(probe, capture_output=True).returncode:
        raise BuildError(f'the Python packages do not import after installing them; see {logs / "pip.log"}')
    stamp.write_text(json.dumps(want))
    return vpy, True


# ---- identification ----
def describe_titles(report):
    titles = sorted({n['title_id'] for n in report.get('ncas', [])} | {m['title_id'] for m in report.get('metas', [])})
    named = [f'{OTHER_TITLES[t]} ({t})' for t in titles if t in OTHER_TITLES]
    # an update (...800) or add-on content of a known game: its base title is the 0x1000 block's start (DLC: one below)
    for t in titles:
        value = int(t, 16)
        base = value & ~0xFFF if value & 0xFFF in (0, 0x800) else (value & ~0xFFF) - 0x1000
        if t not in OTHER_TITLES and f'{base:016X}' in OTHER_TITLES:
            named.append(f'{OTHER_TITLES[f"{base:016X}"]} ({t}, {"update" if value & 0xFFF == 0x800 else "add-on content"})')
    return ', '.join(named) if named else 'title ' + ', '.join(titles) if titles else 'no game content'


def identify(vpy, logs, game, update, keys, title_keys):
    """Check that the files are this game, base v0 and update 1.0.1, before any long step. Returns the update's source
    file (the game file itself for an XCI holding both)."""
    log = logs / 'identify.log'
    run([vpy, GAME / 'scripts/identify_game.py', game, *([update] if update else []), '--keys', keys], log, quiet=False)
    reports = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
    if len(reports) != (2 if update else 1):
        raise BuildError(f'could not read the game files (see {log})')
    for path, r in zip([game] + ([update] if update else []), reports):
        if 'error' in r:
            raise BuildError(f'{path.name}: {r["error"]} (is it {GAME_NAME}, and is -k this Switch\'s prod.keys?)')
    g = reports[0]
    titles = {n['title_id'] for n in g['ncas']}
    if not titles & {BASE_TITLE, UPDATE_TITLE}:
        raise BuildError(f'{game.name} is not {GAME_NAME} ({BASE_TITLE}): it holds {describe_titles(g)}')
    base_metas = [m for m in g['metas'] if m['type'] == 0x80 and m['title_id'] == BASE_TITLE]
    if not base_metas:
        if any(m['type'] == 0x81 and m['title_id'] == UPDATE_TITLE for m in g['metas']):
            raise BuildError(f'{game.name} is the update of {GAME_NAME}, not the base game: pass the base game as the '
                             f'game file and this file with --update')
        raise BuildError(f'{game.name} holds no base game of {GAME_NAME} (Application meta {BASE_TITLE}); '
                         f'add-on content and other packages are not supported')
    if base_metas[0]['version'] != 0:
        raise BuildError(f'{game.name}: base game version {base_metas[0]["version"]}; only the original base (v0) is supported')
    if update:
        u = reports[1]
        patches = [m for m in u['metas'] if m['type'] == 0x81]
        if any(m['type'] == 0x80 for m in u['metas']) and not patches:
            raise BuildError(f'--update {update.name} is a base game, not the update: pass the 1.0.1 update NSP')
        mine = [m for m in patches if m['title_id'] == UPDATE_TITLE]
        if not mine:
            raise BuildError(f'--update {update.name} is not the update of {GAME_NAME} ({UPDATE_TITLE}): it holds '
                             f'{describe_titles(u)}')
        source, report, version = update, u, mine[0]['version']
    else:
        mine = [m for m in g['metas'] if m['type'] == 0x81 and m['title_id'] == UPDATE_TITLE]
        if not mine:
            raise BuildError(f'{game.name} holds only the base game (v0); the port runs {DISPLAY}: pass the {DISPLAY} '
                             f'update NSP with --update <update.nsp>')
        source, report, version = game, g, mine[0]['version']
    if version != UPDATE_VERSION:
        shown = f'{version >> 26}.{(version >> 20) & 0x3f}.{(version >> 16) & 0xf}'
        raise BuildError(f'{source.name}: update version {shown} (v{version}); only {DISPLAY} (v{UPDATE_VERSION}) is '
                         f'supported')
    # Rights-managed NCAs need their title key: a common ticket in the same file, else title.keys
    for path, r in zip([game] + ([update] if update else []), reports):
        common = {t['rights_id'] for t in r['tickets'] if t['kind'] == 'common'}
        needed = {n['rights_id'] for n in r['ncas'] if n['rights_managed'] and n['title_id'] in (BASE_TITLE, UPDATE_TITLE)}
        missing = needed - common - (title_key_rights(title_keys) if title_keys else set())
        if missing:
            raise BuildError(f'{path.name} has no common ticket for {len(missing)} of its NCAs'
                             + (' and title.keys has no key for them' if title_keys else
                                ': pass --title-keys <title.keys> (dumped from your own Switch)'))
    say(f'{GAME_NAME}: base v0 from {game.name}, update {DISPLAY} from {source.name} (by the IDs inside the files)')
    return source


# ---- steps ----
class Build:
    def __init__(self, args, work, vpy, logs, env):
        self.game, self.update, self.keys = args.game, args.update, args.keys
        self.title_keys, self.firmware = args.title_keys, args.firmware
        self.work, self.vpy, self.logs, self.env = work, vpy, logs, env
        self.make_work = make_safe(work)
        self.kind = input_kind(self.game)

    def py(self, script, *args, log, env=None, cwd=None):
        run([self.vpy, script, *args], self.logs / log, cwd=cwd or GAME, env=env or self.env)

    def make(self, *targets, log, extra=()):
        jobs = os.cpu_count() or 4
        run(['make', f'-j{jobs}', *targets, f'WORK={self.make_work}', f'PY={self.vpy}', *extra], self.logs / log,
            cwd=GAME, env=self.env)

    def tk_args(self):
        return ['--title-keys', self.title_keys] if self.title_keys else []

    # 1. code
    def step_code(self, update_source):
        exefs = self.work / f'extracted/{VERSION}/exefs'
        manifest = exefs.parent / 'extraction-manifest.json'
        with timed('code: ExeFS') as t:
            if not (source_matches(manifest, update_source) and exefs_matches(exefs)):
                say(f'1/7 code: extracting and verifying the {DISPLAY} ExeFS')
                if exefs.exists():
                    shutil.rmtree(exefs)
                self.py('scripts/extract_code.py', self.game, '--keys', self.keys, '--output', exefs,
                        *(['--update', self.update] if self.update else []), *self.tk_args(), log='extract_code.log')
                t['built'] = True
            for name, digest in EXPECTED['exefs_sha256'].items():
                if not (exefs / name).is_file():
                    raise BuildError(f'ExeFS {name} is missing; not {GAME_NAME} {DISPLAY}')
                got = sha256(exefs / name)
                if got == digest:
                    continue
                kind = npdm_kind(exefs / name) if name == 'main.npdm' else None
                if kind == 'signed':
                    say(f'1/7 code: main.npdm is a Nintendo-signed {DISPLAY} file from another dump; using it')
                elif kind == 'resigned':
                    say(f'1/7 code: main.npdm of this dump was signed again by a dump tool; the game does not use it and '
                        f'the code modules match {DISPLAY}; using it')
                else:
                    check(f'ExeFS {name}', got, digest)
        with timed('code: NSO segments') as t:
            elf = self.work / f'extracted/{VERSION}/elf'
            if not all((elf / f'{m}-inventory.json').is_file() for m in ('rtld', 'main', 'sdk', 'subsdk0')):
                say('1/7 code: checking the NSO segments of the four code modules')
                self.py('scripts/prepare_modules.py', exefs, '--output', elf, log='prepare_modules.log')
                t['built'] = True
        out = self.work / f'rewritten/{VERSION}'
        out.mkdir(parents=True, exist_ok=True)
        with timed('code: rewrite') as t:
            for name in ('sdk', 'subsdk0', 'main'):
                image = out / f'{name}.patched.bin'
                want = EXPECTED['rewritten_sha256'][f'{name}.patched.bin']
                tables = [out / f'{name}.{k}.tsv' for k in ('fixups', 'gates', 'sites', 'cuts')]
                if image.is_file() and all(p.is_file() for p in tables) and sha256(image) == want:
                    continue
                say(f'1/7 code: rewriting {name} for Apple silicon' + (' (the big one: about a minute)' if name == 'main' else ''))
                self.py('third_party/d2r/patch_nso.py', exefs / name, out, name, log=f'rewrite_{name}.log',
                        env=dict(self.env, DM_RULESET='native', DM_PRIVATE_GATES='0'))
                check(f'rewritten {name}', sha256(image), want)
                t['built'] = True
        with timed('code: rewrite checks') as t:
            self.rewrite_checks(exefs, out, t)

    def rewrite_checks(self, exefs, out, t):
        """structck.py (structure) and equiv.py (semantics, emulator) for each module, as the port was verified with.
        Recorded with the image and checker digests, so a later build with the same rewrite does not repeat them."""
        record_path = self.work / 'analysis/rewrite-checks.json'
        record = read_json(record_path) or {}
        checkers = {n: sha256(GAME / 'third_party/d2r' / n) for n in ('structck.py', 'equiv.py', 'patch_nso.py')}
        llvm = subprocess.run(['xcrun', '--find', 'llvm-objdump'], capture_output=True, text=True).stdout.strip()
        env = dict(self.env, LLVM_BIN=str(Path(llvm).parent), DM_VCACHE=str(self.work / f'cache/verify_{VERSION}'))
        for name in ('sdk', 'subsdk0', 'main'):
            want = dict(image=EXPECTED['rewritten_sha256'][f'{name}.patched.bin'], checkers=checkers, structck='pass', equiv='pass')
            if record.get(name) == want:
                continue
            say(f'1/7 code: checking the rewrite of {name}: structure (structck.py), then semantics (equiv.py)')
            self.py('third_party/d2r/structck.py', out, name, exefs / name, log=f'structck_{name}.log', env=env)
            if f'STRUCT {name} OK' not in (self.logs / f'structck_{name}.log').read_text(errors='replace'):
                raise BuildError(f'structck.py did not report {name} OK; see {self.logs / f"structck_{name}.log"}')
            self.py('third_party/d2r/equiv.py', out, name, exefs / name, '--seed', '1', '--jobs', str(os.cpu_count() or 8),
                    '--tsv', self.logs / f'equiv_{name}.tsv', log=f'equiv_{name}.log', env=env)
            text = (self.logs / f'equiv_{name}.log').read_text(errors='replace')
            if f'EQUIV {name} OK' not in text or '分歧站点=0' not in text:
                raise BuildError(f'equiv.py did not report {name} OK with 0 divergent sites; see {self.logs / f"equiv_{name}.log"}')
            record[name] = want
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps(record, indent=2) + '\n')
            t['built'] = True

    # 2. fonts
    def font_report(self, folder):
        """The six fonts bound into the runtime: the pinned hashes name the fonts of the reference dump's system
        update; a valid font of another firmware version is bound by the SHA-256 of its own bytes."""
        pins = EXPECTED['shared_fonts_sha256']
        manifest = read_json(folder / 'manifest.json') or {}
        rows = manifest.get('fonts') or []
        if manifest.get('status') != 'pass' or [r.get('type') for r in rows] != list(range(6)) \
                or [r.get('name') for r in rows] != list(pins):
            return None
        other = []
        for row, (name, known) in zip(rows, pins.items()):
            path = folder / f'{row["type"]}-{name}.bfttf'
            if not path.is_file() or path.stat().st_size != row['bfttf_bytes'] or not bfttf_valid(path):
                return None
            digest = sha256(path)
            if digest != row['sha256']:
                return None
            if digest not in known:
                other.append(name)
        return other

    def step_fonts(self):
        folder = self.work / 'extracted/firmware/shared-fonts'
        with timed('fonts') as t:
            other = self.font_report(folder)
            if other is None:
                if folder.exists():
                    shutil.rmtree(folder)
                if self.firmware:
                    say(f'2/7 fonts: extracting the six system shared fonts from {self.firmware}')
                    source = ['--firmware', self.firmware]
                else:
                    say('2/7 fonts: extracting the six system shared fonts from the system update in the card image')
                    source = ['--xci', self.game]
                try:
                    self.py('scripts/extract_shared_fonts.py', '--keys', self.keys, *source, '--output', folder,
                            log='shared_fonts.log')
                except BuildError as e:
                    hint = '' if self.firmware else (' The card image may carry no system update with the fonts: pass '
                                                     '--firmware <folder of your firmware\'s NCA files>.')
                    raise BuildError(f'{e}{hint}') from None
                other = self.font_report(folder)
                if other is None:
                    raise BuildError(f'the extracted shared fonts in {folder} do not check out (see {self.logs / "shared_fonts.log"})')
                t['built'] = True
            if other:
                say(f'2/7 fonts: {", ".join(other)} come{"s" if len(other) == 1 else ""} from another firmware version '
                    f'than the pinned ones (verified NCA, valid BFTTF); bound by their own SHA-256')

    # 3. data
    def step_data(self):
        romfs = self.work / f'extracted/{VERSION}/romfs'
        image, index = romfs / 'romfs.bin', romfs / 'index.json'
        want = EXPECTED['romfs']
        with timed('data: RomFS') as t:
            report = read_json(index) or {}
            ok = (report.get('status') == 'pass' and report.get('romfs_sha256') == want['sha256'] and
                  report.get('romfs_size') == want['size'] and len(report.get('files', [])) == want['files'] and
                  image.is_file() and image.stat().st_size == want['size'])
            if ok and report.get('romfs_mtime_ns') not in (None, image.stat().st_mtime_ns):
                say('2/7 data: the RomFS image changed since it was verified; checking its SHA-256 (14.3 GB)')
                ok = sha256(image) == want['sha256']
                if ok:
                    report['romfs_mtime_ns'] = image.stat().st_mtime_ns
                    index.write_text(json.dumps(report, indent=2) + '\n')
            if not ok:
                need = want['size'] / GB + 1
                if free_gb(romfs.parent) < need:
                    raise BuildError(f'needs about {need:.0f} GB free for the merged RomFS in {romfs.parent} '
                                     f'({free_gb(romfs.parent):.1f} GB free)')
                if romfs.exists():
                    shutil.rmtree(romfs)
                say(f'3/7 data: merging the base RomFS and the {DISPLAY} patch into one 14.3 GB image, verifying all six '
                    'IVFC levels (a few minutes)')
                self.py('scripts/merge_romfs.py', self.game, '--keys', self.keys, '--output', romfs,
                        *(['--update', self.update] if self.update else []), *self.tk_args(), log='merge_romfs.log')
                report = read_json(index) or {}
                check('RomFS', report.get('romfs_sha256'), want['sha256'])
                for key, what in (('romfs_size', 'size'), ('bytes_from_base', 'bytes_from_base'),
                                  ('bytes_from_update', 'bytes_from_update')):
                    if report.get(key) != want[what]:
                        raise BuildError(f'RomFS {what}: {report.get(key)} is not the expected {want[what]}')
                if len(report.get('files', [])) != want['files']:
                    raise BuildError(f'RomFS holds {len(report.get("files", []))} files, not the expected {want["files"]}')
                t['built'] = True

    # 4.-6. the Makefile's targets
    def step_prepare(self):
        with timed('prepare') as t:
            build = self.work / 'build'
            before = {p.name: file_stamp(p) for p in build.glob('*.h')} if build.is_dir() else {}
            say('4/7 prepare: the runtime\'s tables, RomFS index and fonts config (make prepare)')
            self.make('prepare', log='prepare.log')
            t['built'] = {p.name: file_stamp(p) for p in build.glob('*.h')} != before

    def step_runtime(self):
        binary = self.work / 'build/xcx-native'
        with timed('runtime') as t:
            before = file_stamp(binary) if binary.is_file() else None
            say('5/7 runtime: the game binary (make runtime; a few minutes the first time)')
            self.make('runtime', log='runtime.log')
            t['built'] = file_stamp(binary) != before

    def step_shader_tools(self):
        xlate = self.work / 'build/shader-service/totk_xlate'
        spirv = self.work / 'build/spirv-cross/spirv-cross'
        with timed('shader tools') as t:
            before = [file_stamp(p) if p.is_file() else None for p in (xlate, spirv)]
            say('6/7 shader tools: the prebuilt shader translator (SHA-256 checked) and SPIRV-Cross at its pinned commit '
                '(make shader-tools)')
            self.make('shader-tools', log='shader-tools.log')
            want = (GAME / 'prebuilt/macos-arm64/totk_xlate.sha256').read_text().split()[0]
            check('shader translator (build/shader-service/totk_xlate)', sha256(xlate), want)
            head = subprocess.run(['git', '-C', self.work / 'src/SPIRV-Cross', 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True).stdout.strip()
            if head != SPIRV_CROSS_COMMIT:
                raise BuildError(f'SPIRV-Cross in {self.work / "src/SPIRV-Cross"} is at {head[:12] or "?"}, not the '
                                 f'pinned {SPIRV_CROSS_COMMIT[:12]}; delete that folder and build again')
            t['built'] = [file_stamp(p) for p in (xlate, spirv)] != before

    def step_launcher(self):
        with timed('launcher and icon') as t:
            paths = [self.work / 'build/launcher', self.work / 'build/AppIcon.icns']
            before = [file_stamp(p) if p.is_file() else None for p in paths]
            self.make('launcher', log='launcher.log')
            if not paths[1].is_file():
                try:   # the game's own icon from its control data; the app goes without one if it cannot be read
                    self.make('icon', log='icon.log', extra=[f'GAME={self.game}', f'KEYS={self.keys}',
                                                             *([f'TITLE_KEYS={self.title_keys}'] if self.title_keys else [])])
                except BuildError as e:
                    say(f'   no app icon, the app gets the default one ({str(e).splitlines()[0]})')
            t['built'] = [file_stamp(p) if p.is_file() else None for p in paths] != before


# ---- the macOS app ----
def data_bundle(work, logs):
    """The RomFS image (14.3 GB) as a bundle of its own, signed once and kept in the work folder (build/data-bundles):
    the app carries a clone of it in Contents/Helpers, and the app's seal records only its signature, so packaging does
    not hash 14 GB each time. The stamp names the image (size, mtime and the SHA-256 the merge verified). From the
    original project's build_app.py."""
    image = work / f'{app_bundle.ROMFS}/romfs.bin'
    bundle = work / f'build/data-bundles/{app_bundle.DATA_BUNDLE}'
    stamp = bundle.with_name(bundle.name + '.json')
    st = image.stat()
    want = dict(format=2, size=st.st_size, mtime_ns=st.st_mtime_ns, sha256=(read_json(image.parent / 'index.json') or {}).get('romfs_sha256'))
    if read_json(stamp) == want and (bundle / 'Contents/_CodeSignature/CodeResources').is_file() and \
            (bundle / 'Contents/Resources/romfs.bin').is_file():
        return bundle, False
    say('7/7 app: signing the game data once (a minute or two; later builds reuse it)')
    tmp = bundle.with_name(bundle.name + '.partial')
    if tmp.exists():
        shutil.rmtree(tmp)
    clone(image, tmp / 'Contents/Resources/romfs.bin')
    (tmp / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=f'{app_bundle.BUNDLE_ID}.data.{VERSION}', CFBundleName='Game data', CFBundlePackageType='BNDL',
        CFBundleShortVersionString=DISPLAY, CFBundleVersion='1')))
    run(['codesign', '--force', '--sign', '-', tmp], logs / 'sign-data.log')
    run(['codesign', '--verify', '--strict', tmp], logs / 'verify-data.log')   # here, where the 14 GB are hashed anyway
    stamp.unlink(missing_ok=True)
    if bundle.exists():
        shutil.rmtree(bundle)
    tmp.rename(bundle)
    stamp.write_text(json.dumps(want))
    return bundle, True


def app_seal_valid(app):
    """The app's own seal, which records the data bundle's signature (not --deep: that hashes the 14 GB image)."""
    return subprocess.run(['codesign', '--verify', '--strict', app], capture_output=True).returncode == 0


def keep_game_data(new, old):
    """When the app being replaced carries the same signed game data, move its data bundle into the new app in place of
    the fresh clone: the image keeps its file identity, so the runtime's RomFS check record (runtime/romfs.c, keyed by
    it) still matches and the first start after a rebuild does not hash 14 GB again. If the new app's seal does not
    verify then, the clone goes back. From the original project's build_app.py."""
    image = 'Contents/Resources/romfs.bin'
    kept = False
    for bundle in sorted((new / 'Contents/Helpers').glob('GameData-*.bundle')):
        before = old / 'Contents/Helpers' / bundle.name
        if not all((before / rel).is_file() and (before / rel).read_bytes() == (bundle / rel).read_bytes()
                   for rel in ('Contents/_CodeSignature/CodeResources', 'Contents/Info.plist')):
            continue
        if not (before / image).is_file() or (before / image).stat().st_size != (bundle / image).stat().st_size:
            continue
        fresh = new.with_name(f'{new.name}.{bundle.name}.fresh')   # outside the app: anything extra in it fails the seal
        if fresh.exists():
            remove_tree(fresh)
        bundle.rename(fresh)
        try:
            before.rename(bundle)
        except OSError:
            fresh.rename(bundle)
            continue
        if not app_seal_valid(new):
            bundle.rename(before)
            fresh.rename(bundle)
            continue
        remove_tree(fresh)
        kept = True
    return kept


def install_app(new, out):
    """Put the signed app `new` at `out`; the seal is checked before the installed app is touched. The .app folder
    itself stays and takes the new contents (macOS keeps its first-run check of an app with the folder); the replaced
    contents are removed in the background. Returns whether the installed game data was kept."""
    kept = out.exists() and keep_game_data(new, out)
    if not kept and not app_seal_valid(new):
        raise BuildError(f'{new}: the new app\'s signature does not verify (codesign --verify --strict); the installed app is unchanged')
    leftovers = sorted(out.parent.glob(f'{glob.escape(out.name)}.replaced-*'))
    if out.exists():
        old = out.with_name(f'{out.name}.replaced-{os.getpid()}')
        if old.exists():
            remove_tree(old)
        old.mkdir()
        for entry in list(out.iterdir()):
            entry.rename(old / entry.name)
        for entry in list(new.iterdir()):
            entry.rename(out / entry.name)
        new.rmdir()
        leftovers.append(old)
    else:
        new.rename(out)
    for path in leftovers:
        subprocess.Popen(['rm', '-rf', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return kept


def game_running(out):
    binary = str((out / app_bundle.GAME_ROOT / 'build/xcx-native').resolve())
    procs = subprocess.run(['ps', '-axo', 'comm='], capture_output=True, text=True).stdout.splitlines()
    return binary in (p.strip() for p in procs)


def app_inputs(work, bundle_stamp, icon):
    """What the app is made of, for knowing whether an installed app is current: the files cloned into it, the
    launcher, the icon, the data bundle's stamp and the Info.plist."""
    files = app_bundle.GAME_FILES + ['build/launcher'] + sorted(
        f'{app_bundle.SHARED_FONTS}/{p.name}' for p in (work / app_bundle.SHARED_FONTS).iterdir() if p.is_file())
    stamps = {rel: file_stamp(work / rel) for rel in files}
    if icon:
        stamps['build/AppIcon.icns'] = file_stamp(icon)
    return dict(format=1, files=stamps, data=read_json(bundle_stamp), info=app_bundle.info_plist(icon=bool(icon)))


def step_app_macos(work, out, logs):
    icon = work / 'build/AppIcon.icns'
    icon = icon if icon.is_file() else None
    with timed('app: data bundle') as t:
        bundle, t['built'] = data_bundle(work, logs)
    stamp_path = work / 'build/app-stamp.json'
    with timed('app: assemble and sign') as t:
        want = dict(app_inputs(work, bundle.with_name(bundle.name + '.json'), icon), out=str(out))
        if read_json(stamp_path) == want and out.is_dir() and app_seal_valid(out):
            say(f'7/7 app: {out} is current (same files, valid signature)')
        else:
            say(f'7/7 app: assembling {out}')
            tmp = out.with_name(out.name + '.partial')
            if tmp.exists():
                remove_tree(tmp)
            app_bundle.write_skeleton(tmp, work / 'build/launcher', icon, app_bundle.info_plist(icon=bool(icon)))
            root = tmp / app_bundle.GAME_ROOT
            for rel in app_bundle.GAME_FILES:
                clone(work / rel, root / rel)
            copy_tree(work / app_bundle.SHARED_FONTS, root / app_bundle.SHARED_FONTS)
            copy_tree(bundle, tmp / 'Contents/Helpers' / bundle.name)
            link = root / f'{app_bundle.ROMFS}/romfs.bin'   # where the game opens it (runtime/romfs.c)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(tmp / 'Contents/Helpers' / bundle.name / 'Contents/Resources/romfs.bin', link.parent))
            run(['codesign', '--force', '--sign', '-', tmp], logs / 'sign-app.log')
            notified = 0
            while out.exists() and game_running(out):   # replacing the files of a running game is not safe
                if time.monotonic() - notified >= 60:
                    say('7/7 app: the new app is ready; waiting for the running game to close before replacing it')
                    notified = time.monotonic()
                time.sleep(2)
            if install_app(tmp, out):
                say('7/7 app: the game data is unchanged; kept the installed copy, so the first start need not verify it again')
            stamp_path.write_text(json.dumps(want))
            t['built'] = True
    with timed('app: register and first check') as t:
        t['built'] = 'checked'
        run([LSREGISTER, '-f', out], logs / 'register-app.log')
        say('7/7 app: macOS checks the app once (seconds when this app folder was checked before, longer for a new one)')
        r = subprocess.run([out / 'Contents/MacOS' / app_bundle.EXECUTABLE, '--check'], capture_output=True, text=True,
                           timeout=1800)
        if r.returncode:
            raise BuildError(f'the app does not start: {(r.stdout + r.stderr).strip()}')
    # Metal cache warm-up (the original project compiles its startup shader seed into Metal's cache for the app's own
    # path): this port has no startup shader seed yet, so there is nothing to compile ahead; the game translates and
    # caches its shaders as it meets them (~/Library/Application Support/MacNativeGames/XCX/shader-cache).


# ---- the iPhone / iPad IPA (experimental) ----
IOS = GAME / 'ios'
IOS_EXPERIMENTAL = ('EXPERIMENTAL: shaders are not pre-translated, the picture will be mostly missing. An iPhone or iPad '
                    'cannot translate shaders while playing, and this port has no up-front shader translation yet: the IPA '
                    'carries only the shader variants this Mac has already translated, and every other draw is skipped.')


def prerequisites_ios():
    """The Mac's tools (the Mac runtime exports the device's code), plus Xcode 26+ with the iOS SDK, xcodegen and CMake."""
    py = prerequisites()
    sys.path.insert(0, str(IOS / 'tools'))
    from xcode_dir import developer_dir   # noqa: E402  an Xcode with the iOS SDK, also outside /Applications
    try:
        developer = developer_dir()
    except RuntimeError as e:
        raise BuildError(f'--platform ios: {e}') from None
    env = dict(os.environ, DEVELOPER_DIR=developer)
    r = subprocess.run(['xcodebuild', '-version'], capture_output=True, text=True, env=env)
    version = r.stdout.split()[1] if r.returncode == 0 and len(r.stdout.split()) > 1 else '0'
    if int(version.split('.')[0] or 0) < 26:
        raise BuildError(f'--platform ios needs Xcode 26 or later (found {version} in {developer}); install it from the App Store')
    if subprocess.run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], capture_output=True, env=env).returncode:
        raise BuildError(f'the Xcode in {developer} has no iOS SDK: install the iOS platform in Xcode > Settings > Components')
    path = os.pathsep.join(TOOL_PATHS + [os.environ.get('PATH', '')])
    missing = [tool for tool in ('xcodegen', 'cmake') if not shutil.which(tool, path=path)]
    if missing:
        raise BuildError(f'--platform ios needs {" and ".join(missing)}: brew install {" ".join(missing)}')
    say(f'iOS: Xcode {version} ({developer}), {", ".join(f"{t} {shutil.which(t, path=path)}" for t in ("xcodegen", "cmake"))}')
    return py


def ios_seed_caches(work, extra):
    """The Mac shader caches whose translated variants the IPA carries (read only): the player's (what the self-contained
    app translated), the work folder's own (make boot, the development app), the iPhone's earlier misses translated by
    ios/tools/fill_shaders.py, and any --ios-seed-cache folders."""
    chain = next(line.split('"')[1] for line in (GAME / 'third_party/d2r_gfx/gfx/nvn/dmsh_chain.h').read_text().splitlines()
                 if line.startswith('#define DMSH_CHAIN'))
    folders = [Path.home() / f'Library/Application Support/MacNativeGames/{app_bundle.DATA_FOLDER}/shader-cache/rt-{chain}',
               work / f'build/shader-cache/rt-{chain}', work / f'build/ios-fill/rt-{chain}']
    for folder in extra:
        folder = folder.expanduser().resolve()
        folders.append(folder / f'rt-{chain}' if (folder / f'rt-{chain}').is_dir() else folder)
    return [f for f in dict.fromkeys(folders) if f.is_dir()]


def step_ipa(work, out, logs, args=None):
    with timed('ipa: device build and package') as t:
        say('7/7 ipa: the device code, Opus and libvpx for iOS, the Xcode project and the IPA '
            '(games/xcx/ios/tools/build_ipa.py; the first time several minutes and network access)')
        caches = ios_seed_caches(work, (args.ios_seed_cache if args else None) or [])
        seed = [a for c in caches for a in ('--seed-cache', c)]
        run([sys.executable, IOS / 'tools/build_ipa.py', '--work', work, '--out', out, '--logs', logs,
             '--app-name', APP_NAME, '--bundle-id', app_bundle.BUNDLE_ID, *seed], logs / 'build-ipa.log',
            env=dict(os.environ, PATH=os.pathsep.join(TOOL_PATHS + [os.environ.get('PATH', '/usr/bin:/bin')])))
        for line in (logs / 'build-ipa.log').read_text(errors='replace').splitlines():
            if line.startswith(('shader seed:', 'IPA ready:', 'app ready:')):
                say(f'7/7 ipa: {line}')
        t['built'] = True


# The platforms: each one checks its own requirements and packages the build. The steps before packaging (code, fonts,
# data, prepare) are shared; the Mac runtime, shader tools and launcher are built for both (the iOS build exports the
# device's code with the Mac runtime, takes its icon from the launcher step, and fill_shaders.py uses the shader tools).
PLATFORMS = {
    'macos': dict(output_suffix='.app', requirements=prerequisites, package=step_app_macos, runtime=True),
    'ios': dict(output_suffix='.ipa', requirements=prerequisites_ios, package=step_ipa, runtime=True),
}


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description=f'Build a native Mac app from your own {GAME_NAME} {DISPLAY}: an XCI holding the base game and the '
                    f'update, or a base NSP with --update <the {DISPLAY} update NSP>.',
        epilog='example: python3 build_app.py "personal/Xenoblade Chronicles X Definitive Edition.xci" -k personal/prod.keys')
    ap.add_argument('game', type=Path, help='the game file: an XCI holding base v0 and update 1.0.1, or the base game NSP / XCI')
    ap.add_argument('-k', '--keys', type=Path, required=True, help='your prod.keys')
    ap.add_argument('--update', type=Path, help=f'the {DISPLAY} update NSP (not needed for an XCI that holds it)')
    ap.add_argument('--firmware', type=Path, help='folder of your firmware\'s NCA files, for the system shared fonts '
                                                   '(needed for an NSP; an XCI carries them in its system update)')
    ap.add_argument('--title-keys', type=Path, help='your title.keys, for NSPs without a common ticket')
    ap.add_argument('-o', '--out', type=Path, help=f'the app or IPA to write (default: "<game file folder>/{APP_NAME}.app", '
                                                   f'or .ipa with --platform ios)')
    ap.add_argument('--work', type=Path, help=f'work folder (default: {WORK_NAME} next to the game file)')
    ap.add_argument('--platform', choices=('macos', 'ios'), default='macos',
                    help='macos (default), or ios: an unsigned iPhone / iPad IPA for sideloading (EXPERIMENTAL: most of '
                         'the picture is missing until shaders are translated ahead of time)')
    ap.add_argument('--ios-seed-cache', type=Path, action='append',
                    help='iOS: another Mac shader cache folder whose translated variants the IPA carries (read only); repeatable')
    return ap.parse_args(argv)


def main(argv):
    t0 = time.time()
    args = parse_args(argv)
    plat = PLATFORMS[args.platform]
    for name in ('game', 'keys', 'update', 'title_keys'):
        path = getattr(args, name)
        if path is not None:
            path = path.expanduser().resolve()
            if not path.is_file():
                raise BuildError(f'no such file: {path}')
            setattr(args, name, path)
    if args.firmware:
        args.firmware = args.firmware.expanduser().resolve()
        if not args.firmware.is_dir():
            raise BuildError(f'--firmware must be a folder of firmware NCA files: {args.firmware}')
    kind = input_kind(args.game)
    if args.update:
        if args.update == args.game:
            raise BuildError('--update is the game file itself: an XCI that holds the update needs no --update')
        if input_kind(args.update) != 'nsp':
            raise BuildError(f'--update must be the {DISPLAY} update NSP: {args.update.name}')
    if kind == 'nsp' and not args.update:
        raise BuildError(f'{args.game.name} is an NSP: a base NSP needs the {DISPLAY} update, --update <update.nsp>')
    out = args.out.expanduser().resolve() if args.out else args.game.parent / f'{APP_NAME}{plat["output_suffix"]}'
    if out.suffix != plat['output_suffix']:
        raise BuildError(f'the {args.platform} output must end in {plat["output_suffix"]}: {out}')
    work = args.work.expanduser().resolve() if args.work else args.game.parent / WORK_NAME
    fonts_cached = (work / 'extracted/firmware/shared-fonts/manifest.json').is_file()
    if kind == 'nsp' and not args.firmware and not fonts_cached:
        raise BuildError('NSPs do not carry the system shared fonts: pass --firmware <folder of your firmware\'s NCA files>')

    if args.platform == 'ios':
        say(IOS_EXPERIMENTAL)
    py = plat['requirements']()
    logs = work / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    say(f'work folder {work} ({free_gb(work):.0f} GB free on its volume)')
    romfs_done = (work / f'{app_bundle.ROMFS}/romfs.bin').is_file()
    need = 3 if romfs_done else EXPECTED['romfs']['size'] / GB + 3
    if free_gb(work) < need:
        raise BuildError(f'needs about {need:.0f} GB free for the work folder ({free_gb(work):.1f} GB free); the app '
                         'itself is an APFS clone of it when on the same volume')
    if args.platform == 'macos' and os.stat(existing(out.parent)).st_dev != os.stat(existing(work)).st_dev and \
            free_gb(out.parent) < EXPECTED['romfs']['size'] / GB + 1:
        raise BuildError(f'{out.parent} is on another volume than the work folder, so the app is a full copy there '
                         f'(about 15 GB); it has {free_gb(out.parent):.1f} GB free')
    env = dict(os.environ, PATH=os.pathsep.join(TOOL_PATHS + [os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')]))
    with timed('setup: Python environment') as t:
        vpy, t['built'] = prepare_venv(work, py, logs)
    with timed('identify') as t:
        update_source = identify(vpy, logs, args.game, args.update, args.keys, args.title_keys)
        t['built'] = 'checked'
    build = Build(args, work, vpy, logs, env)
    build.step_code(update_source)
    build.step_fonts()
    build.step_data()
    build.step_prepare()
    build.step_runtime()
    build.step_shader_tools()
    build.step_launcher()
    if args.platform == 'ios':
        step_ipa(work, out, logs, args)
        total = time.time() - t0
        say_times(work, total)
        say(f'done in {total / 60:.1f} min: {out}')
        say(IOS_EXPERIMENTAL)
        say('The IPA is unsigned: sign and install it with your own Apple ID (python3 games/xcx/ios/tools/install_device.py '
            f'"{out}" --device "<device name>" --game-data "{work / app_bundle.ROMFS / "romfs.bin"}") or with a sideloading '
            'tool that keeps its entitlements. It holds no game image: install_device.py --game-data copies the 14.3 GB '
            'RomFS once into the app\'s Documents/GameData, or drag romfs.bin onto the app in Finder (the device > Files). '
            'Needs iOS / iPadOS 26 and an iPhone 14 or newer. The app holds the game code: keep it for your own use, never share it.')
        return
    plat['package'](work, out, logs)
    total = time.time() - t0
    say_times(work, total)
    say(f'done in {total / 60:.1f} min: {out}')
    say('Open it from Finder (it may be moved, also to /Applications). Saves, the shader cache and the log go to '
        '~/Library/Application Support/MacNativeGames/XCX and ~/Library/Logs/MacNativeGames/xcx.log. The first start '
        'verifies the game data once (about 10 s) and keeps a record; shaders are translated as the game meets them '
        'and cached. The app holds the game: keep it for your own use, never share it.')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except BuildError as e:
        print(f'\nERROR: {e}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
