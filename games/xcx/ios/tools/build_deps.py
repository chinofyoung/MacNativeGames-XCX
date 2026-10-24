#!/usr/bin/env python3
"""Build the audio (Opus) and VP9 (libvpx) static libraries for an iPhone / iPad, or for the iOS Simulator.

Homebrew's Mac archives cannot be linked into an iOS app, so the same pinned releases are built here: Opus with CMake
(Ninja when it is installed, else Unix Makefiles), libvpx with its own configure. Restored from the original project's
games/totk/ios/tools/build_deps.py; game-independent. A finished build is reused (deps.json names what was built).

usage: build_deps.py <out folder> [--sdk iphoneos|iphonesimulator] [--deployment 26.0]
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

OPUS = ('https://github.com/xiph/opus.git', 'v1.6.1')
LIBVPX = ('https://chromium.googlesource.com/webm/libvpx.git', 'v1.16.0')

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('out', type=Path)
ap.add_argument('--sdk', choices=('iphoneos', 'iphonesimulator'), default='iphoneos')
ap.add_argument('--deployment', default='26.0')
a = ap.parse_args()
out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
want = dict(opus=OPUS[1], libvpx=LIBVPX[1], sdk=a.sdk, deployment=a.deployment, format=1)
stamp = out / 'deps.json'
if stamp.is_file() and json.loads(stamp.read_text()) == want and all((out / 'lib' / n).is_file() for n in ('libopus.a', 'libvpx.a')):
    print(f'IOS_DEPS={out} (reused)')
    raise SystemExit(0)
# libvpx's configure splits its compiler path and flags on spaces: when the folder has one, sources and build trees go
# under a space-free root instead.
root = out if ' ' not in str(out) else Path(tempfile.gettempdir()).resolve() / f'xcnative-ios-deps-{a.sdk}'
sdk = subprocess.check_output(['xcrun', '--sdk', a.sdk, '--show-sdk-path'], text=True).strip()
platform_dir = 'iPhoneOS.platform' if a.sdk == 'iphoneos' else 'iPhoneSimulator.platform'
src = root / 'src'; build = root / 'build'; src.mkdir(parents=True, exist_ok=True); build.mkdir(exist_ok=True)
sdklink = root / f'sdkroot/Platforms/{platform_dir}/Developer/SDKs' / Path(sdk).name
if sdklink.is_symlink(): sdklink.unlink()
sdklink.parent.mkdir(parents=True, exist_ok=True)
if not sdklink.exists(): sdklink.symlink_to(sdk)
target = f'arm64-apple-ios{a.deployment}' + ('-simulator' if a.sdk == 'iphonesimulator' else '')
min_flag = f'-m{"iphoneos" if a.sdk == "iphoneos" else "ios-simulator"}-version-min={a.deployment}'


def run(cmd, cwd=None, env=None): subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True)


def checkout(url, tag, dest):
    if not (dest / '.git').exists(): run(['git', 'clone', '-q', '--depth', '1', '--branch', tag, url, dest])


opus = src / 'opus'; checkout(*OPUS, opus)
generator = ['-G', 'Ninja'] if shutil.which('ninja') else ['-G', 'Unix Makefiles']
opus_build = build / f'opus-{a.sdk}'
run(['cmake', *generator, '-S', opus, '-B', opus_build, '-DCMAKE_SYSTEM_NAME=iOS', f'-DCMAKE_OSX_SYSROOT={sdklink}',
     '-DCMAKE_OSX_ARCHITECTURES=arm64', f'-DCMAKE_OSX_DEPLOYMENT_TARGET={a.deployment}', '-DOPUS_BUILD_SHARED_LIBRARY=OFF',
     '-DOPUS_BUILD_TESTING=OFF', '-DOPUS_BUILD_PROGRAMS=OFF', '-DOPUS_INSTALL_PKG_CONFIG_MODULE=OFF', '-DCMAKE_BUILD_TYPE=Release',
     *([f'-DCMAKE_C_FLAGS=-target {target}'] if a.sdk == 'iphonesimulator' else [])])
run(['cmake', '--build', opus_build, '--config', 'Release', '-j', str(os.cpu_count() or 4)])

vpx = src / 'libvpx'; checkout(*LIBVPX, vpx)
vpxout = build / f'libvpx-{a.sdk}'; vpxout.mkdir(exist_ok=True)
bindir = build / f'ios-bin-{a.sdk}'; bindir.mkdir(exist_ok=True)
real_tools = Path(os.environ.get('DEVELOPER_DIR', subprocess.check_output(['xcode-select', '-p'], text=True).strip())) / 'Toolchains/XcodeDefault.xctoolchain/usr/bin'
for tool in ('clang', 'clang++', 'ar', 'as', 'strip'):
    link = bindir / tool
    if link.is_symlink(): link.unlink()
    if not link.exists(): link.symlink_to(real_tools / tool)
wrapper = bindir / 'xcrun'
wrapper.write_text(f'''#!/usr/bin/env python3
import subprocess,sys
r=subprocess.run(["/usr/bin/xcrun",*sys.argv[1:]],capture_output=True,text=True)
s=r.stdout.replace({sdk!r},{str(sdklink)!r}).replace({str(real_tools)!r},{str(bindir)!r})
sys.stdout.write(s);sys.stderr.write(r.stderr);sys.exit(r.returncode)
''')
wrapper.chmod(0o755)
vpx_env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get('PATH', ''))
if a.sdk == 'iphoneos':
    vpx_target = '--target=arm64-darwin-gcc'
else:
    # libvpx has no arm64 Simulator target (its arm64-darwin-gcc links against the device SDK): the portable C build,
    # without NEON, which is enough for the Simulator smoke test.
    vpx_target = '--target=generic-gnu'
    vpx_env.update(CC=str(bindir / 'clang'), CXX=str(bindir / 'clang++'), AR=str(bindir / 'ar'), AS=str(bindir / 'as'),
                   LD=str(bindir / 'clang'), STRIP=str(bindir / 'strip'),
                   LDFLAGS=f'-target {target} -isysroot {sdklink}')
run([vpx / 'configure', vpx_target, '--disable-examples', '--disable-tools',
     '--disable-docs', '--disable-unit-tests', '--disable-webm-io', '--enable-pic', '--disable-shared', '--enable-static',
     f'--extra-cflags=-target {target} -isysroot {sdklink} {min_flag}'], cwd=vpxout, env=vpx_env)
run(['make', '-j', str(os.cpu_count() or 4), 'libvpx.a'], cwd=vpxout, env=vpx_env)
lib = out / 'lib'; include = out / 'include'; lib.mkdir(exist_ok=True); include.mkdir(exist_ok=True)
shutil.copy2(opus_build / 'libopus.a', lib / 'libopus.a')
shutil.copy2(vpxout / 'libvpx.a', lib / 'libvpx.a')
shutil.copytree(opus / 'include', include, dirs_exist_ok=True)
shutil.copytree(vpx / 'vpx', include / 'vpx', dirs_exist_ok=True)
shutil.copy2(vpxout / 'vpx_config.h', include / 'vpx_config.h')
stamp.write_text(json.dumps(want))
print(f'IOS_DEPS={out}')
