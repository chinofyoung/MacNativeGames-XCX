#!/usr/bin/env python3
"""The player's contribution file: the shaders this Mac's game met that the shared shader list does not have yet.

The game writes each such shader once as a list V row (the shader key and the bind state, no game data) to
~/Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt (TOTK_SHADER_CONTRIB, set by app/launcher.m;
third_party/d2r_gfx/gfx/nvn/totk_shader_contrib.h). After a build, build_app.py counts the rows the list it used lacks
and the window (games/xcx/builder) offers to share them: a GitHub issue in the shader-list form, with the file attached.
The file is read with shader_list.parse_row, so a comment, a line cut off mid-write or anything else that is not a valid
row is skipped.
usage: contribution.py [<shader list>]   prints the new rows' count and the issue link for this Mac's file
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_list import ShaderList, parse_row

MAC_FILE = Path.home()/'Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt'
ISSUE_NEW = 'https://github.com/chinofyoung/XC-Native/issues/new?template=shader-list.yml'


def new_rows(contribution, shader_list):
    """The valid rows of the contribution file that the shader list does not have, once each, in the file's order.
    No file: none. No list (None or a missing file): every row. A list that is not valid raises ValueError."""
    contribution = Path(contribution)
    if not contribution.is_file():
        return []
    listed = set(ShaderList.load(shader_list).rows()) if shader_list is not None else set()
    out = {}
    for line in contribution.read_text(encoding='utf-8-sig', errors='replace').split('\n'):
        row = parse_row(line.removesuffix('\r'))
        if row is not None and row not in listed:
            out[row] = None
    return list(out)


def issue_url(rows_count, version, device=None):
    """The shader-list issue form, filled in with the title, the game version (2.0.0 or 1.0.1) and the device (left for
    the player to pick when None)."""
    return (f'{ISSUE_NEW}&title={quote(f"Shader list: {int(rows_count)} shaders", safe="")}'
            f'&version={quote(version, safe="")}' + (f'&device={quote(device, safe="")}' if device else ''))


def main(argv):
    if len(argv) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    rows = new_rows(MAC_FILE, Path(argv[0]) if argv else None)
    print(f'{len(rows)} new rows in {MAC_FILE}')
    if rows:
        print(issue_url(len(rows), '<version>', 'Mac'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
