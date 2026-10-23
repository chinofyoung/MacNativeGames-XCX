#!/usr/bin/env python3
"""The Mac app's bundle, in one place for both builds: its name, identifier, Info.plist, and the files a
self-contained app carries. Standard library only (the root build_app.py imports it before the build's Python exists).

Two kinds of app share the launcher (app/launcher.m):
- development (`make app`): a small bundle whose Info.plist names the work folder (XCXWorkFolder); the launcher runs
  <work>/build/xcx-native from there. Written by `app_bundle.py dev`, signed by the Makefile.
- self-contained (root build_app.py): the game folder is inside the app, Contents/Resources/xcx (GAME_ROOT), laid out
  like a work folder (GAME_FILES, SHARED_FONTS); the RomFS image is a signed data bundle in Contents/Helpers
  (DATA_BUNDLE) that extracted/v101/romfs/romfs.bin links to. The launcher picks this mode when GAME_ROOT holds the
  game binary.

usage: app_bundle.py dev --app <.app> --launcher <launcher> --work <work folder> [--icon <AppIcon.icns>]
"""
import argparse
import json
import plistlib
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
EXPECTED = json.loads((HERE / 'assets/expected-v1.0.1.json').read_text())
VERSION = 'v101'                                 # runtime/game_version.h TOTK_GAME_DIR
APP_NAME = 'Xenoblade Chronicles X'
BUNDLE_ID = 'org.macnativegames.xcx'
EXECUTABLE = APP_NAME                            # Contents/MacOS/<this>
DATA_FOLDER = 'XCX'                              # ~/Library/Application Support/MacNativeGames/<this>
GAME_ROOT = 'Contents/Resources/xcx'             # the launcher's game folder in a self-contained app
DATA_BUNDLE = f'GameData-{VERSION}.bundle'       # Contents/Helpers/<this>/Contents/Resources/romfs.bin
ROMFS = f'extracted/{VERSION}/romfs'
# The files the game reads in a self-contained app, relative to the game folder (and to the work folder they come
# from): the binary, the shader tools totk_shader_rt.m starts from <binary>/../build, the rewritten code
# (loader.c: <argv[1]>/<module>.patched.bin) and the RomFS index (romfs.c). romfs.bin is the data bundle's.
GAME_FILES = ['build/xcx-native', 'build/spirv-cross/spirv-cross', 'build/shader-service/totk_xlate',
              f'rewritten/{VERSION}/main.patched.bin', f'rewritten/{VERSION}/sdk.patched.bin',
              f'rewritten/{VERSION}/subsdk0.patched.bin', f'{ROMFS}/runtime-index.bin']
SHARED_FONTS = 'extracted/firmware/shared-fonts'  # the launcher sets TOTK_SHARED_FONTS to it (runtime/shared_font.c)


def info_plist(*, work=None, icon=False):
    """Info.plist for either kind of app: work names the work folder of a development app (None: self-contained)."""
    info = dict(CFBundleDevelopmentRegion='en', CFBundleExecutable=EXECUTABLE, CFBundleIdentifier=BUNDLE_ID,
                CFBundleName=APP_NAME, CFBundleDisplayName=APP_NAME, CFBundlePackageType='APPL',
                CFBundleShortVersionString=EXPECTED['display_version'], CFBundleVersion=str(EXPECTED['version'] + 1),
                LSMinimumSystemVersion='26.0', LSApplicationCategoryType='public.app-category.role-playing-games',
                NSHighResolutionCapable=True, LSSupportsGameMode=True, GCSupportsControllerUserInteraction=True)
    if icon:
        info['CFBundleIconFile'] = 'AppIcon'
    if work:
        info['XCXWorkFolder'] = str(Path(work).resolve())
    return info


def write_skeleton(app, launcher, icon, info):
    """Contents/MacOS/<launcher>, Resources/AppIcon.icns (when there is one), Info.plist and PkgInfo in `app`."""
    (app / 'Contents/MacOS').mkdir(parents=True, exist_ok=True)
    (app / 'Contents/Resources').mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher, app / 'Contents/MacOS' / EXECUTABLE)
    if icon:
        shutil.copy2(icon, app / 'Contents/Resources/AppIcon.icns')
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (app / 'Contents/PkgInfo').write_bytes(b'APPL????')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='kind', required=True)
    dev = sub.add_parser('dev', help='the development app of `make app` (unsigned; the Makefile signs it)')
    dev.add_argument('--app', type=Path, required=True)
    dev.add_argument('--launcher', type=Path, required=True)
    dev.add_argument('--work', type=Path, required=True)
    dev.add_argument('--icon', type=Path)
    a = ap.parse_args()
    icon = a.icon if a.icon and a.icon.is_file() else None
    if a.app.exists():
        shutil.rmtree(a.app)
    write_skeleton(a.app, a.launcher, icon, info_plist(work=a.work, icon=bool(icon)))


if __name__ == '__main__':
    main()
