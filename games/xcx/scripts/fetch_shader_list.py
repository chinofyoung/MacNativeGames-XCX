#!/usr/bin/env python3
"""Download the newest shared shader list (games/xcx/assets/shader_list.tsv) for a build.

The build asks GitHub for the list everyone keeps, checks it and caches it. When that does not work (no network, a
captive portal's web page, a cut-off file) it uses the list it cached last time, and failing that the copy in the
project. A download is only accepted if it starts with the list's header line, is under 64 MB and has at least one
row, or is exactly the header line and nothing else (an emptied list: players then build with the defaults only);
rows that are not valid (see shader_list.parse_row) are dropped and counted, the rest are kept.
usage: fetch_shader_list.py <cache folder> [<list in the project>]
"""
import http.client
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_list import HEADER, ShaderList, parse_row

LIST_URL = 'https://raw.githubusercontent.com/chinofyoung/XC-Native/main/games/xcx/assets/shader_list.tsv'
HEADER_START = HEADER.split(' v1')[0]   # '# Xenoblade Chronicles X shader list'
MAX_BYTES = 64 * 1024 * 1024
CACHE_NAME = 'shader_list.tsv'
REPO_COPY = Path(__file__).resolve().parents[1]/'assets'/'shader_list.tsv'


def _download(url, timeout, opener):
    """The downloaded list as a ShaderList with the number of rows dropped, or None if it is not acceptable."""
    try:
        with opener(url, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        text = data.decode('utf-8-sig')
    except (OSError, ValueError, http.client.HTTPException):   # no network, a timeout, a bad URL, not text, a cut-off or
        # garbled reply (URLError and TimeoutError are OSErrors)
        return None
    lines = text.split('\n')   # '\n' only, as ShaderList.load does
    if not lines[0].startswith(HEADER_START):
        return None
    lst, dropped = ShaderList(), 0
    for line in lines[1:]:
        line = line.removesuffix('\r')
        if not line or line.startswith('#'):
            continue
        row = parse_row(line)
        if row is None:
            dropped += 1
        else:
            lst.add([row])
    if lst.rows() or (not dropped and _header_only(text)):
        return lst, dropped
    return None


def _header_only(text):
    """Whether a list file is the exact header line and nothing else: the list emptied on purpose."""
    return text in (HEADER, HEADER.removesuffix('\n'))


def _usable(path):
    try:
        return Path(path).exists() and (bool(ShaderList.load(path).rows())
                                        or _header_only(Path(path).read_text(encoding='utf-8-sig')))
    except (OSError, ValueError):
        return False


def fetch_list(cache_dir, repo_copy, url=LIST_URL, timeout=10, opener=urllib.request.urlopen):
    """The list to use and where it came from: 'downloaded' (just now, saved to cache_dir/shader_list.tsv), 'cached' (the
    one saved by an earlier download) or 'repo' (repo_copy). Never raises for a network or file problem with the download."""
    cache = Path(cache_dir)/CACHE_NAME
    got = _download(url, timeout, opener)
    if got is not None:
        lst, dropped = got
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            lst.save(cache)
        except OSError as e:   # a cache folder that cannot be written: use what is there
            print(f'shader list: the cache could not be written ({e}); using the saved or project list', file=sys.stderr)
        else:
            if dropped:
                print(f'shader list: dropped {dropped} invalid rows from the download', file=sys.stderr)
            return cache, 'downloaded'
    if _usable(cache):
        return cache, 'cached'
    return Path(repo_copy), 'repo'


def main(argv):
    if len(argv) not in (1, 2):
        print(__doc__, file=sys.stderr)
        return 2
    path, origin = fetch_list(Path(argv[0]), Path(argv[1]) if len(argv) == 2 else REPO_COPY)
    print(f'{origin}: {path} ({len(ShaderList.load(path).rows())} rows)')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
