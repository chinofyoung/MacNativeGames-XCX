#!/usr/bin/env python3
"""Bring the shaders an iPhone / iPad met that the shared shader list lacks to the Mac, so they can be shared.

The app writes each such shader once as a list V row (the shader key and the bind state, no game data) to
Documents/shader-list-contribution.txt (TOTK_SHADER_CONTRIB, set by ios/Sources/main.m). `pull` copies that file from the
device with devicectl (the way fill_shaders.py copies the requests) and appends the valid rows the Mac's contribution file
(~/Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt, scripts/contribution.py) does not have yet.
Rows already there stay as they are; a comment, a cut-off line or anything else that is not a valid row (shader_list.parse_row)
is not taken over. build_app.py --platform ios runs this first when XCX_IOS_DEVICE is set; the builder window and the game's
prompt then offer the rows to share. A sideloaded app's bundle id has the team suffix (org.macnativegames.xcx.<team id,
lowercase>; the install log prints it): pass it as --bundle-id or XCX_BUNDLE_ID.
The original project's check / install / uninstall (a LaunchAgent) are gone; they print a pointer to `pull`.
usage: shader_contrib_watch.py pull --device <id> [--bundle-id <id>] [--store <file>]"""
import argparse, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from shader_list import parse_row

STORE = Path.home()/'Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt'
REMOTE = 'Documents/shader-list-contribution.txt'
DEFAULT_BUNDLE = 'org.macnativegames.xcx'


def devicectl(cmd):
    """Run an `xcrun devicectl ...` command with the Xcode that has the iOS SDK (as fill_shaders.py does)."""
    from xcode_dir import developer_dir
    return subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, DEVELOPER_DIR=developer_dir()))


def rows_of(path):
    """The valid rows of a contribution file in its order, once each; no file: none."""
    path = Path(path)
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    return list(dict.fromkeys(r for r in map(parse_row, text.split('\n')) if r is not None))


def merge(copy, store):
    """Append the valid rows of `copy` that the store lacks to the store (written whole, then moved into place)."""
    have = set(rows_of(store))
    new = [r for r in rows_of(copy) if r not in have]
    if not new:
        return 0
    store = Path(store)
    store.parent.mkdir(parents=True, exist_ok=True)
    old = store.read_bytes() if store.is_file() else b''
    if old and not old.endswith(b'\n'):
        old += b'\n'
    tmp = store.with_name(store.name + '.tmp')
    tmp.write_bytes(old + ''.join('\t'.join(r) + '\n' for r in new).encode('utf-8'))
    os.replace(tmp, store)
    return len(new)


def pull(device, bundle_id, store=STORE, run=devicectl, report=None):
    """Copy the device's Documents/shader-list-contribution.txt and merge its valid rows into `store`; the number of rows
    added. A device that cannot be reached, an app without the file or a failed merge is reported (report(message), by
    default on stderr) and adds none: it does not raise. `run` runs a command and returns what subprocess.run does."""
    report = report or (lambda m: print(f'shader_contrib_watch.py: {m}', file=sys.stderr))
    try:
        with tempfile.TemporaryDirectory(prefix='xcx-contrib-') as tmp:
            copy = Path(tmp)/'shader-list-contribution.txt'
            r = run(['xcrun', 'devicectl', 'device', 'copy', 'from', '--device', device, '--domain-type', 'appDataContainer',
                     '--domain-identifier', bundle_id, '--source', REMOTE, '--destination', str(copy)])
            if r.returncode or not copy.is_file():
                lines = (r.stderr or r.stdout or '').strip().splitlines()
                why = next((l.strip() for l in lines if 'ERROR' in l), lines[-1].strip() if lines else 'devicectl failed')
                report(f'iPhone {device} not connected (or {bundle_id} is not installed there): {why}')
                return 0
            return merge(copy, store)
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        report(f'iPhone {device} not connected (or {bundle_id} is not installed there): {e}')
        return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd')
    p = sub.add_parser('pull', help='copy the device\'s contribution file and merge its rows into the Mac\'s')
    p.add_argument('--device', default=os.environ.get('XCX_IOS_DEVICE'), help='devicectl device id (or XCX_IOS_DEVICE)')
    p.add_argument('--bundle-id', default=os.environ.get('XCX_BUNDLE_ID', DEFAULT_BUNDLE),
                   help='the installed app\'s identifier (the install log prints it; or XCX_BUNDLE_ID)')
    p.add_argument('--store', type=Path, default=STORE, help=f'the Mac contribution file (default: {STORE})')
    for old in ('check', 'install', 'uninstall'):
        sub.add_parser(old)
    args = ap.parse_args(argv)
    if args.cmd in ('check', 'install', 'uninstall'):
        print('shader_contrib_watch.py: nothing to install any more; run `shader_contrib_watch.py pull --device <id>` '
              'to bring a device\'s new shaders to this Mac.')
        return 0
    if args.cmd != 'pull':
        ap.print_usage(sys.stderr)
        return 2
    if not args.device:
        print('shader_contrib_watch.py: give --device (or XCX_IOS_DEVICE)', file=sys.stderr)
        return 2
    problems = []
    n = pull(args.device, args.bundle_id, args.store, report=problems.append)
    for m in problems:
        print(f'shader_contrib_watch.py: {m}', file=sys.stderr)
    if problems:
        return 1
    print(f'{n} new shaders from the iPhone; share them from the builder window or the game\'s prompt')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
