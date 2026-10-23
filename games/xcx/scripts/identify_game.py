#!/usr/bin/env python3
"""What a game file holds, by the IDs inside it (never by its name): one JSON line per file with the container kind, the
title ID and content type of each NCA, every content meta (title, type, version) and the rights IDs of its tickets.

build_app.py uses it to recognise Xenoblade Chronicles X: Definitive Edition (0100453019AA8000) and its update
(0100453019AA8800: 1.0.1 = v65536, 2.0.0 = v196608; app_bundle.VERSIONS) before any long step, and to name what a wrong
file is instead. Each Patch meta of this game also carries `game_version` (v101 / v200, or null for an unsupported one)
and `display_version`. Only IDs and versions are
printed; keys and title keys stay in memory. No executable launch, no network.
usage: identify_game.py <file> [<file> ...] --keys <prod.keys>
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_exefs import container_entries, load_keys, read_at, read_nca_header, nca_ctr_key   # noqa: E402
from merge_romfs import Ctr, PATCH_TITLE, parse_cnmt, read_pfs, sections                          # noqa: E402
import app_bundle                                                                                  # noqa: E402

TICKET_BODIES = {0x10000: 0x240, 0x10001: 0x140, 0x10002: 0x80, 0x10003: 0x240, 0x10004: 0x140, 0x10005: 0x80}


def tickets(f, entries):
    """The rights ID, its title part and 'common' | 'personalized' of each .tik; the title key is not read."""
    out = []
    for name, offset, size in entries:
        if not name.lower().endswith('.tik') or size < 4:
            continue
        body = TICKET_BODIES.get(struct.unpack('<I', read_at(f, offset, 4))[0])
        if body is None or size < body + 0x180:
            out.append(dict(rights_id=None, title_id=None, kind='unreadable'))
            continue
        data = read_at(f, offset + body, 0x180)
        out.append(dict(rights_id=data[0x160:0x170].hex().upper(), title_id=data[0x160:0x168].hex().upper(),
                        kind='personalized' if data[0x141] else 'common'))
    return out


def identify(path, keys):
    with path.open('rb') as f:
        kind, entries = container_entries(f, path.stat().st_size)
        ncas, metas = [], []
        for name, offset, length in entries:
            if not name.lower().endswith('.nca'):
                continue
            hdr = read_nca_header(f, offset, length, keys)
            title = f'{struct.unpack_from("<Q", hdr, 0x210)[0]:016X}'
            rights = hdr[0x230:0x240] != bytes(16)
            ncas.append(dict(name=name, title_id=title, content_type=hdr[0x205], rights_managed=rights,
                             rights_id=hdr[0x230:0x240].hex().upper() if rights else None))
            if hdr[0x205] != 1 or rights:   # content metas use the key area
                continue
            ctr = Ctr(f, offset, length, nca_ctr_key(hdr, keys, {}))
            for _, start, end, fs in sections(hdr):
                for fname, data in read_pfs(ctr, start, end, fs):
                    if fname.endswith('.cnmt'):
                        meta = parse_cnmt(data) if data[0xc] in (0x80, 0x81) else dict(
                            title_id=f'{struct.unpack_from("<Q", data, 0)[0]:016X}',
                            version=struct.unpack_from('<I', data, 8)[0], type=data[0xc])
                        row = dict(title_id=meta['title_id'], version=meta['version'], type=meta['type'])
                        if meta['type'] == 0x81 and meta['title_id'] == PATCH_TITLE:
                            row['game_version'] = app_bundle.by_update_version(meta['version'])
                            row['display_version'] = app_bundle.display(row['game_version']) if row['game_version'] else None
                        metas.append(row)
        return dict(file=path.name, container=kind, ncas=ncas, metas=metas, tickets=tickets(f, entries))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', type=Path, nargs='+')
    ap.add_argument('--keys', type=Path, required=True)
    a = ap.parse_args()
    keys = load_keys(a.keys)
    for path in a.files:
        try:
            print(json.dumps(identify(path, keys)), flush=True)
        except ValueError as e:   # the scripts' own checks (require): a damaged or foreign file, or wrong keys
            print(json.dumps(dict(file=path.name, error=str(e))), flush=True)


if __name__ == '__main__':
    main()
