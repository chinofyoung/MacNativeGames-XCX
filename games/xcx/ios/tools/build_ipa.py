#!/usr/bin/env python3
"""Build an unsigned, sideloadable iPhone / iPad IPA of Xenoblade Chronicles X from a prepared work folder. EXPERIMENTAL.

Restored from the original project's games/totk/ios/tools/build_ipa.py and adapted for this port: the work folder is the
one build_app.py (or the Makefile) made, with the Mac runtime built (build/xcx-native, used once to export the patched
code), and everything generated goes under <work>/build/ios-<sdk> (the Xcode project, its DerivedData, the device
libraries, the shader seed), never into the source tree.

What the app carries: the game code, compiled into the executable's own segments (iOS cannot run code written at run time:
gen_embed.py, runtime/loader.c), the RomFS index, the six shared fonts and a shader seed. The 14.3 GB RomFS image is not
in the IPA: the player installs it once into the app's Documents/GameData (install_device.py --game-data, or Finder's
file sharing), as the original project's latest iOS build does (--ios-game-data documents there).

Shaders: an iPhone or iPad cannot translate shaders at run time (no translator process), and this port has no up-front
shader translation yet. The seed holds the variants the Mac has already translated (--seed-cache folders, read only),
which may be none; any other draw is skipped, so most of the picture is missing.

There is no iOS Simulator build: the graphics layer is Metal 4 (MTL4CommandQueue and the rest), which the iPhoneSimulator
SDK does not have (Xcode 26.6: MTLDevice there has no newMTL4CommandQueue), and MetalFX is missing there too.

usage: build_ipa.py --work <work folder> --out <X.ipa | X.app> --logs <folder>
                    [--app-name <name>] [--bundle-id <id>] [--seed-cache <rt-... folder>]...
"""
import argparse, hashlib, json, os, plistlib, re, shutil, subprocess, sys
from pathlib import Path
from xcode_dir import developer_dir

TOOLS = Path(__file__).resolve().parent
IOS = TOOLS.parent
GAME = IOS.parent                                   # games/xcx
sys.path.insert(0, str(GAME / 'scripts'))
import app_bundle   # noqa: E402  the version folder, names and display version shared with the Mac app

DEPLOYMENT = '26.0'                                 # iOS / iPadOS 26 (Metal 4), as the original project
VARIANT_FILE = re.compile(r'^[0-9a-f]{64}-[0-9a-f]{12}_b\.(?:metal|json)$')
INDEX_FILE = re.compile(r'^[0-9a-f]{64}\.variants$')
FRAMEWORKS = ['UIKit', 'Metal', 'MetalFX', 'Foundation', 'QuartzCore', 'ImageIO', 'CoreGraphics', 'CoreText',
              'UniformTypeIdentifiers', 'CoreFoundation', 'AudioToolbox', 'GameController', 'AVFAudio', 'Security',
              'CoreHaptics']


def run(args, cwd, log, env):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as f:
        p = subprocess.run([str(x) for x in args], cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT)
    if p.returncode:
        tail = log.read_text(errors='replace').splitlines()[-35:]
        raise RuntimeError(f"{Path(str(args[0])).name} failed; see {log}\n" + '\n'.join(tail))


def clone(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink(): dst.unlink()
    if subprocess.run(['cp', '-c', str(src), str(dst)], capture_output=True).returncode: shutil.copy2(src, dst)


def copy_tree(src, dst):
    src = Path(src); dst = Path(dst)
    if dst.exists(): shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for p in sorted(src.rglob('*')):
        if p.is_file(): clone(p, dst / p.relative_to(src))


def chain():
    for line in (GAME / 'third_party/d2r_gfx/gfx/nvn/dmsh_chain.h').read_text().splitlines():
        if line.startswith('#define DMSH_CHAIN'): return line.split('"')[1]
    raise RuntimeError('DMSH_CHAIN not found in dmsh_chain.h')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---- the shader seed ----
def collect_seed(caches, seed, log):
    """The variants already translated on this Mac, in the startup-seed format the runtime imports (totk_shader_seed.h):
    <variant>_b.metal / _b.json for each variant, and per shader a <shader>.variants index of the rows whose files are
    there. Read only from the caches (files are cloned or copied out of them). Variants the runtime would regenerate on a
    Mac (MSL with doubles or unsupported GLSL.std.450 calls) are left out: an iPhone cannot regenerate them. Returns the
    number of variants (0: no seed, the app then carries none)."""
    ch = chain()
    rows, source = {}, {}   # shader -> {variant: row}; file name -> path
    for cache in caches:
        cache = Path(cache)
        if not cache.is_dir(): continue
        for index in sorted(cache.iterdir()):
            if not INDEX_FILE.match(index.name): continue
            try: entries = json.loads(index.read_text())
            except (OSError, ValueError): continue
            if not isinstance(entries, list): continue
            for row in entries:
                if not isinstance(row, dict) or not isinstance(row.get('variant'), str) or not isinstance(row.get('deps'), dict): continue
                v = row['variant']
                msl, refl = cache / f'{v}_b.metal', cache / f'{v}_b.json'
                if not VARIANT_FILE.match(msl.name) or not msl.is_file() or not refl.is_file(): continue
                if v in rows.get(index.stem, {}): continue
                try:
                    if json.loads(refl.read_text()).get('chain') != ch: continue
                    text = msl.read_text(errors='replace')
                except (OSError, ValueError): continue
                if 'double ' in text or 'unsupported_GLSLstd450' in text: continue
                rows.setdefault(index.stem, {})[v] = {'variant': v, 'deps': row['deps']}
                source[msl.name], source[refl.name] = msl, refl
    count = sum(len(v) for v in rows.values())
    # Reuse the seed when it was made from the same files (names, sizes, times) and the same rows
    stamp = hashlib.sha256(json.dumps([ch, sorted((n, p.stat().st_size, p.stat().st_mtime_ns) for n, p in source.items()),
                                       {s: sorted(r) for s, r in sorted(rows.items())}]).encode()).hexdigest()
    recorded = seed.with_name(seed.name + '.json')
    if count and (seed / 'seed-manifest.json').is_file() and recorded.is_file() and json.loads(recorded.read_text()).get('stamp') == stamp:
        return count, False
    if seed.exists(): shutil.rmtree(seed)
    recorded.unlink(missing_ok=True)
    if not count:
        log.write_text('no translated shader variants in ' + ', '.join(map(str, caches)) + '\n')
        return 0, True
    seed.mkdir(parents=True)
    for name, path in source.items(): clone(path, seed / name)
    for shader, rs in rows.items():
        (seed / f'{shader}.variants').write_text(json.dumps(sorted(rs.values(), key=lambda r: r['variant']), sort_keys=True,
                                                            separators=(',', ':')))
    files = {p.name: sha256(p) for p in sorted(seed.iterdir()) if p.is_file()}
    (seed / 'seed-manifest.json').write_text(json.dumps({'schema': 1, 'chain': ch, 'shaders': count, 'files': files,
                                                         'source': 'shader variants translated on the Mac'}, indent=2) + '\n')
    log.write_text(f'{count} variants of {len(rows)} shaders from ' + ', '.join(map(str, caches)) + '\n')
    recorded.write_text(json.dumps({'stamp': stamp}))
    return count, True


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--work', type=Path, required=True, help='the work folder (build_app.py --work, the Makefile WORK=)')
    p.add_argument('--out', type=Path, required=True, help='X.ipa (unsigned IPA) or X.app (the unsigned app folder)')
    p.add_argument('--logs', type=Path, required=True)
    p.add_argument('--app-name', default=app_bundle.APP_NAME)
    p.add_argument('--bundle-id', default=app_bundle.BUNDLE_ID)
    p.add_argument('--seed-cache', type=Path, action='append', default=[],
                   help='a Mac shader cache folder (rt-<chain>) whose translated variants the app carries; repeatable')
    a = p.parse_args()
    work = a.work.resolve(); logs = a.logs.resolve(); out = a.out.resolve()
    if out.suffix.lower() not in ('.ipa', '.app'): raise RuntimeError('the iOS output must end in .ipa (or .app: the unsigned folder)')
    a.sdk = 'iphoneos'   # a device build only (see above)
    version = app_bundle.VERSION
    root = work / 'build/ios'; gen = root / 'Gen'
    env = dict(os.environ, DEVELOPER_DIR=developer_dir())
    for k in ('XCX_PATCHES',): env.pop(k, None)   # the exported code carries no optional patch
    subprocess.run(['xcrun', '--sdk', a.sdk, '--show-sdk-path'], env=env, check=True, capture_output=True)
    if not shutil.which('xcodegen'): raise RuntimeError('install xcodegen: brew install xcodegen')
    gen.mkdir(parents=True, exist_ok=True)
    for name in ('runtime_tables.h', 'romfs_config.h', 'shared_fonts_config.h'):
        if not (work / 'build' / name).is_file(): raise RuntimeError(f'missing {work / "build" / name}: run make prepare')
        clone(work / 'build' / name, gen / name)

    # 1. The code: the Mac runtime exports each module's text after its patches (runtime/loader.c export-ios); gen_embed.py
    #    puts text, data and patch area into the executable's segments and binds them by SHA-256 (ios_embed.h).
    host = work / 'build/xcx-native'; rewritten = work / f'rewritten/{version}'
    if not host.is_file(): raise RuntimeError(f'missing {host}: build the Mac runtime first (make runtime)')
    modules = gen / 'modules'; shutil.rmtree(modules, ignore_errors=True); modules.mkdir()
    run([host, rewritten, 'export-ios'], work, logs / 'export-ios.log', dict(env, TOTK_EXPORT_IOS=str(modules), TOTK_HEADLESS='1'))
    flags = subprocess.check_output([sys.executable, TOOLS / 'gen_embed.py', rewritten, gen / 'embed.S', '--text-dir', modules,
                                     'main', 'subsdk0', 'sdk'], text=True, env=env).strip()

    # 2. Info.plist (xcodegen fills in its properties). The home screen icon is added after the build (step 6): an asset
    #    catalog needs a Simulator runtime matching the SDK even for a device build (actool's thinning), which Xcode does not
    #    install by itself.
    clone(IOS / 'Sources/Info.plist', gen / 'Info.plist')
    project = (IOS / 'project.yml').read_text()
    (root / 'project.yml').write_text(project)

    # 3. Device libraries: Homebrew's Mac archives cannot be linked into an iOS app (build_deps.py, CMake; network once).
    deps = work / f'build/ios-deps/{a.sdk}'
    run([sys.executable, TOOLS / 'build_deps.py', deps, '--sdk', a.sdk, '--deployment', DEPLOYMENT], work, logs / f'ios-dependencies-{a.sdk}.log', env)

    # 4. The shader seed: what the Mac has translated so far (read only), compiled for the device when the Metal toolchain is there.
    seed = work / f'build/ios-seed/{a.sdk}/shader-seed'
    count, fresh = collect_seed(a.seed_cache, seed, logs / 'ios-seed.log')
    if count and fresh:
        run([sys.executable, TOOLS / 'compile_seed.py', seed, a.sdk], work, logs / f'ios-shaders-{a.sdk}.log', env)
    seed_note = (logs / f'ios-shaders-{a.sdk}.log').read_text().strip().splitlines()[-1:] if count and (logs / f'ios-shaders-{a.sdk}.log').is_file() else []
    print(f'shader seed: {count} variants' + (f' ({seed_note[0]})' if seed_note else '') if count else
          'shader seed: none (no translated variants on this Mac yet)', flush=True)

    # 5. The Xcode project with the resolved identity and the embedded-segment linker flags, then the build.
    q = lambda s: '"' + str(s).replace('"', '\\"') + '"'
    ldflags = ' '.join('-framework ' + x for x in FRAMEWORKS)
    (gen / 'xcx.xcconfig').write_text(f'OTHER_LDFLAGS = $(inherited) {ldflags} {q(deps / "lib/libopus.a")} {q(deps / "lib/libvpx.a")} {flags}\n')
    shutil.rmtree(gen / 'deps', ignore_errors=True); shutil.copytree(deps / 'include', gen / 'deps/include')
    info = app_bundle.EXPECTED
    env.update(XCX_SRC=str(GAME), XCX_GEN=str(gen), XCX_APP_NAME=a.app_name, XCX_BUNDLE_ID=a.bundle_id, XCX_DEPLOYMENT=DEPLOYMENT,
               XCX_SDK=a.sdk, XCX_VERSION=info['display_version'], XCX_BUILD=str(info['version'] + 1),
               XCX_CPU_FLAGS='-mcpu=apple-a15')   # iPhone 14 and newer (A15 and later), as the original project
    run(['xcodegen', 'generate', '--spec', root / 'project.yml', '--project', root], root, logs / f'xcodegen-{a.sdk}.log', env)
    # -target and -sdk instead of -destination: a destination needs the iOS platform component downloaded in Xcode
    # (Settings > Components), which building does not; the SDK is enough.
    derived = work / f'build/ios-derived/{a.sdk}'
    run(['xcodebuild', '-project', root / 'XCXIOS.xcodeproj', '-target', 'XCX', '-configuration', 'Release', '-sdk', a.sdk,
         f'SYMROOT={derived}/Build/Products', f'OBJROOT={derived}/Build/Intermediates',
         'CODE_SIGNING_ALLOWED=NO', 'CODE_SIGNING_REQUIRED=NO', 'CODE_SIGN_IDENTITY='],
        root, logs / f'xcodebuild-{a.sdk}.log', env)
    app = derived / f'Build/Products/Release-{a.sdk}/XCX.app'
    if not app.is_dir(): raise RuntimeError(f'Xcode did not produce {app}')

    # 6. The game data the app carries (GameData, read by ios/Sources/main.m); the RomFS image is installed separately.
    res = app / 'GameData'
    shutil.rmtree(res, ignore_errors=True)
    clone(work / f'{app_bundle.ROMFS}/runtime-index.bin', res / 'romfs/runtime-index.bin')
    copy_tree(work / app_bundle.SHARED_FONTS, res / 'shared-fonts')
    (res / 'shader-seed').mkdir(parents=True)
    if count: copy_tree(seed, res / 'shader-seed')
    (res / 'shader-seed/shader_map.tsv').write_text('# TOTK control-code-sha256-v1\n')   # the format line nvn_mtl.m checks; no rows
    # The home screen icon: the game's own, which the Mac build took from its control data (build/AppIcon.icns), as the
    # bundle's icon files named in Info.plist (CFBundleIcons), so no asset catalog is needed.
    icon = work / 'build/AppIcon.icns'
    if icon.is_file():
        names = []
        for px, name in ((120, 'AppIcon60x60@2x.png'), (180, 'AppIcon60x60@3x.png'), (152, 'AppIcon76x76@2x~ipad.png'),
                         (167, 'AppIcon83.5x83.5@2x~ipad.png')):
            run(['sips', '-s', 'format', 'png', '-z', str(px), str(px), icon, '--out', app / name], work, logs / 'ios-icon.log', env)
            names.append(name)
        plist = app / 'Info.plist'; info_plist = plistlib.loads(plist.read_bytes())
        info_plist['CFBundleIcons'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon60x60'], 'CFBundleIconName': 'AppIcon'}}
        info_plist['CFBundleIcons~ipad'] = {'CFBundlePrimaryIcon': {'CFBundleIconFiles': ['AppIcon60x60', 'AppIcon76x76', 'AppIcon83.5x83.5'],
                                                                    'CFBundleIconName': 'AppIcon'}}
        plist.write_bytes(plistlib.dumps(info_plist, fmt=plistlib.FMT_BINARY))
    # Carry the increased-memory-limit entitlement in the executable's ad-hoc signature so re-signing tools keep it.
    run(['codesign', '--force', '--sign', '-', '--entitlements', IOS / 'Sources/XCX.entitlements', app],
        root, logs / f'ios-entitlements-{a.sdk}.log', env)

    payload = root / 'payload'; shutil.rmtree(payload, ignore_errors=True); (payload / 'Payload').mkdir(parents=True)
    copy_tree(app, payload / 'Payload/XCX.app')
    if out.suffix == '.app':   # the unsigned app as a folder, for install_device.py
        shutil.rmtree(out, ignore_errors=True); copy_tree(payload / 'Payload/XCX.app', out); shutil.rmtree(payload)
        print(f'app ready: {out}; unsigned'); return
    out.parent.mkdir(parents=True, exist_ok=True); out.unlink(missing_ok=True)
    run(['zip', '-qry', '-X', out, 'Payload'], payload, logs / 'ipa-zip.log', env)
    shutil.rmtree(payload)
    print(f'IPA ready: {out} ({out.stat().st_size / 2**20:.0f} MiB); unsigned, sideload it with your own Apple ID or a signing tool')


if __name__ == '__main__':
    try: main()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as e: print(f'ERROR: {e}', file=sys.stderr); sys.exit(1)
