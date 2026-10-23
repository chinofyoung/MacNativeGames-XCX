#!/usr/bin/env python3
"""The Mac app's bundle and the supported game versions, in one place for every build: the version table, the app's
name, identifier, Info.plist, and the files a self-contained app carries. Standard library only (the root build_app.py
imports it before the build's Python exists).

Versions (VERSIONS): each supported update of Xenoblade Chronicles X: Definitive Edition has a folder name (`v101`,
`v200`), its display version and the Patch meta version inside the update (65536, 196608), and its pinned values in
assets/expected-<display>.json. The work folder keeps every version apart: extracted/<v>, rewritten/<v>,
cache/verify_<v>, build/<v>/ (the generated headers), build/xcx-native-<v>, build/launcher-<v> and
build/data-bundles/GameData-<v>.bundle; the shader tools, the shader cache and the fonts are shared.

Two kinds of app share the launcher (app/launcher.m):
- development (`make app`): a small bundle whose Info.plist names the work folder (XCXWorkFolder); the launcher runs
  <work>/build/xcx-native-<v> from there. Written by `app_bundle.py dev`, signed by the Makefile.
- self-contained (root build_app.py): the game folder is inside the app, Contents/Resources/xcx (GAME_ROOT), laid out
  like a work folder (game_files(), SHARED_FONTS) except that the binary is build/xcx-native; the RomFS image is a signed
  data bundle in Contents/Helpers (data_bundle()) that extracted/<v>/romfs/romfs.bin links to. The launcher picks this
  mode when GAME_ROOT holds the game binary.
Info.plist records the version: CFBundleShortVersionString (the display version) and XCXGameVersion (the folder name).

usage: app_bundle.py dev --app <.app> --launcher <launcher> --work <work folder> [--icon <AppIcon.icns>] [--version v101]
"""
import argparse
import json
import plistlib
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# folder name -> display version and the update's Patch meta version (runtime/game_version.h has the same table)
VERSIONS = {
    'v101': dict(display='1.0.1', update_version=65536),
    'v200': dict(display='2.0.0', update_version=196608),
}
DEFAULT_VERSION = 'v101'                         # the Makefile's VERSION ?= v101
APP_NAME = 'Xenoblade Chronicles X'
BUNDLE_ID = 'org.macnativegames.xcx'
EXECUTABLE = APP_NAME                            # Contents/MacOS/<this>
DATA_FOLDER = 'XCX'                              # ~/Library/Application Support/MacNativeGames/<this>
PROFILE = f'Library/Application Support/MacNativeGames/{DATA_FOLDER}/profile'   # the saves, under the home folder
GAME_ROOT = 'Contents/Resources/xcx'             # the launcher's game folder in a self-contained app
APP_BINARY = 'build/xcx-native'                  # the game binary inside a self-contained app (the launcher runs it)
SHARED_FONTS = 'extracted/firmware/shared-fonts'  # the launcher sets TOTK_SHARED_FONTS to it (runtime/shared_font.c)


def check_version(version):
    if version not in VERSIONS:
        raise ValueError(f'unknown game version {version!r}; supported: {", ".join(VERSIONS)}')
    return version


def display(version):
    return VERSIONS[check_version(version)]['display']


def expected(version):
    """The pinned values of a version: games/xcx/assets/expected-<display>.json."""
    return json.loads((HERE / f'assets/expected-v{display(version)}.json').read_text())


def by_update_version(number):
    """The folder name of the version whose update has this Patch meta version, else None."""
    return next((v for v, row in VERSIONS.items() if row['update_version'] == number), None)


def describe(number):
    """An update's Patch meta version for a message: '2.0.0 (v196608)' for a known one. The number is a release counter
    (65536 per release), not the display version, which only the update's control data holds; an unknown one is named
    by its number and release index."""
    folder = by_update_version(number)
    return f'{display(folder)} (v{number})' if folder else f'v{number} (release {number >> 16})'


def supported():
    return ', '.join(f'{row["display"]} (v{row["update_version"]})' for row in VERSIONS.values())


def romfs(version):
    return f'extracted/{check_version(version)}/romfs'


def data_bundle(version):
    return f'GameData-{check_version(version)}.bundle'   # Contents/Helpers/<this>/Contents/Resources/romfs.bin


def binary(version):
    return f'build/xcx-native-{check_version(version)}'   # in the work folder (Makefile: make runtime VERSION=<v>)


def launcher(version):
    return f'build/launcher-{check_version(version)}'


def generated(version):
    return f'build/{check_version(version)}'   # runtime_tables.h, romfs_config.h, shared_fonts_config.h


def game_files(version):
    """The files the game reads in a self-contained app, {path in the game folder: path in the work folder}: the binary,
    the shader tools totk_shader_rt.m starts from <binary>/../build, the rewritten code (loader.c:
    <argv[1]>/<module>.patched.bin) and the RomFS index (romfs.c). romfs.bin is the data bundle's."""
    files = {APP_BINARY: binary(version), 'build/spirv-cross/spirv-cross': 'build/spirv-cross/spirv-cross',
             'build/shader-service/totk_xlate': 'build/shader-service/totk_xlate'}
    for name in ('main', 'sdk', 'subsdk0'):
        files[f'rewritten/{version}/{name}.patched.bin'] = f'rewritten/{version}/{name}.patched.bin'
    files[f'{romfs(version)}/runtime-index.bin'] = f'{romfs(version)}/runtime-index.bin'
    return files


def app_version(app):
    """(folder name, display version) an app's Info.plist records, else None (no app, or one this table does not know).
    Apps from before XCXGameVersion existed are 1.0.1 builds; they are recognised by their display version."""
    try:
        info = plistlib.loads((Path(app) / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    folder = info.get('XCXGameVersion') or next((v for v, row in VERSIONS.items()
                                                 if row['display'] == info.get('CFBundleShortVersionString')), None)
    return (folder, VERSIONS[folder]['display']) if folder in VERSIONS else None


def info_plist(version, *, work=None, icon=False):
    """Info.plist for either kind of app: work names the work folder of a development app (None: self-contained)."""
    pins = expected(version)
    info = dict(CFBundleDevelopmentRegion='en', CFBundleExecutable=EXECUTABLE, CFBundleIdentifier=BUNDLE_ID,
                CFBundleName=APP_NAME, CFBundleDisplayName=APP_NAME, CFBundlePackageType='APPL',
                CFBundleShortVersionString=pins['display_version'], CFBundleVersion=str(pins['version'] + 1),
                XCXGameVersion=version,
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
    dev.add_argument('--version', default=DEFAULT_VERSION, choices=list(VERSIONS))
    a = ap.parse_args()
    icon = a.icon if a.icon and a.icon.is_file() else None
    if a.app.exists():
        shutil.rmtree(a.app)
    write_skeleton(a.app, a.launcher, icon, info_plist(a.version, work=a.work, icon=bool(icon)))


if __name__ == '__main__':
    main()
