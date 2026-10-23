#!/usr/bin/env python3
"""Extract and verify the Program ExeFS of Xenoblade Chronicles X: Definitive Edition from its XCI.

The XCI holds the complete base (v0) and the 1.0.1 update; both Program NCAs carry the base title ID, so the program is
chosen through the content metas: by default the one the Patch meta names (the port target: a supported update, 1.0.1 or
2.0.0, and exactly `--version <v>` when given), with `--program base` the one the Application meta names. With
`--update <NSP>` the update program comes from that NSP instead (the 2.0.0 update NSP with the XCI, or any supported
update with a base NSP or a base-only XCI as the game file). FS header, ExeFS hash table and every ExeFS block are verified. Keys stay in memory.
No executable launch, no network.
"""
import argparse
import hashlib
import json
from pathlib import Path
from merge_romfs import open_program, read_pfs, require, load_keys, load_title_keys, sections   # also puts this folder on sys.path
import app_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', type=Path, help='the XCI (base v0 + update 1.0.1)')
    parser.add_argument('--keys', required=True, type=Path)
    parser.add_argument('--program', choices=('update', 'base'), default='update',
                        help='update (default) or base (v0) Program NCA')
    parser.add_argument('--update', type=Path, help='an update NSP, 1.0.1 or 2.0.0 (default: the update inside the game file)')
    parser.add_argument('--version', choices=list(app_bundle.VERSIONS), help='require this update version (v101, v200)')
    parser.add_argument('--title-keys', type=Path, help='title.keys, for NSPs without a common ticket')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), 'Output already exists; choose a new directory')
    source = args.update if args.update and args.program == 'update' else args.game
    title_keys = load_title_keys(args.title_keys) if args.title_keys else None
    f, record, hdr, ctr, meta = open_program(source, load_keys(args.keys), args.program, title_keys,
                                         args.version if args.program == 'update' else None)
    with f:
        for i, start, end, fs in sections(hdr):
            if fs[2:4] != b'\x01\x02':
                continue
            files = read_pfs(ctr, start, end, fs)
            require(any(name == 'main.npdm' for name, _ in files), 'PFS0 is not ExeFS')
            args.output.mkdir(parents=True)
            extracted = []
            for name, data in files:
                (args.output / name).write_bytes(data)
                extracted.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            stat = source.stat()
            manifest = {'source': str(source.resolve()), 'source_size': stat.st_size, 'source_mtime_ns': stat.st_mtime_ns,
                        'version': app_bundle.by_update_version(meta['version']) if args.program == 'update' else None,
                        'container': record['container'], 'program': record, 'meta': meta, 'section_index': i,
                        'integrity': 'FS header, ExeFS hash table, and every ExeFS data block verified; RSA signatures not checked',
                        'files': extracted}
            (args.output.parent / 'extraction-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            print(json.dumps({'integrity': manifest['integrity'], 'meta': meta, 'files': extracted}, indent=2))
            return
    raise ValueError('No ExeFS section found')


if __name__ == '__main__':
    main()
