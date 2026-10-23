#!/usr/bin/env python3
"""Negative control for merge_romfs.py: one flipped byte in the XCI must fail the IVFC check at the predicted block.

Two runs, each flipping one stored (encrypted) byte as it is read from the XCI: one in a range the indirect table takes
from the update's data, one in a range it takes from the base section. Each run must fail with exactly
`IVFC level 5 block <n> failed` for the data block holding that byte, after every earlier block and level passed.
Nothing is written outside a temporary folder, which is removed. Keys stay in memory. No executable launch, no network.
"""
import argparse
import struct
import tempfile
from pathlib import Path
from merge_romfs import open_merged, verify_ivfc, load_keys, require   # also puts this folder on sys.path


def flip_one(ctr, absolute):
    """Make ctr.raw return the stored bytes with the byte at NCA offset `absolute` inverted."""
    raw = ctr.raw
    def flipped(at, size):
        data = raw(at, size)
        if at <= absolute < at + size:
            data = bytearray(data); data[absolute - at] ^= 0xff; data = bytes(data)
        return data
    ctr.raw = flipped


def run(game, keys, storage, skip):
    handles, _, _, fs, patched = open_merged(game, keys)
    try:
        offset, _, power, _ = struct.unpack_from('<QQII', fs, 0x18 + 5 * 24)
        # The first range of this storage that starts inside the data level (past the first `skip` ones).
        rows = [r for r in patched.ind.rows if r[3] == storage and r[0] >= offset][skip:]
        require(rows, 'No range of that storage in the data level')
        start, end, target, _ = rows[0]
        virtual = start + min(0x123, end - start - 1)
        within = target + virtual - start
        if storage == 1:
            ctr, absolute = patched.ctr, patched.start + within
            require(all(r[2] == 0 for r in patched.ex.rows), 'Expected only AES-CTR update data')
        else:
            ctr, absolute = patched.base.ctr, patched.base.start + within
        flip_one(ctr, absolute)
        expected = f'IVFC level 5 block {(virtual - offset) >> power} failed'
        with tempfile.TemporaryDirectory() as td:
            try:
                verify_ivfc(fs, patched.total, patched.read, Path(td) / 'romfs.bin')
            except ValueError as e:
                caught = str(e)
            else:
                caught = None
        name = 'update data' if storage == 1 else 'base section'
        ok = caught == expected
        print(f'{name}: flipped NCA byte {absolute:#x} (merged offset {virtual:#x}); expected "{expected}"; got "{caught}": '
              f'{"caught" if ok else "NOT CAUGHT"}', flush=True)
        return ok
    finally:
        for h in handles:
            h.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', type=Path, help='the XCI (base v0 + update 1.0.1)')
    parser.add_argument('--keys', required=True, type=Path)
    args = parser.parse_args()
    keys = load_keys(args.keys)
    results = [run(args.game, keys, 1, 0), run(args.game, keys, 0, 0)]
    print('ROMFS CONTROL', 'OK' if all(results) else 'FAIL', f'{sum(results)}/{len(results)} caught', flush=True)
    raise SystemExit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
