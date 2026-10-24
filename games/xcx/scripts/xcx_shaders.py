#!/usr/bin/env python3
"""Every NVN shader in Xenoblade Chronicles X's RomFS, keyed as the runtime keys it (docs/shaders-in-romfs.md).

The game keeps almost all its data in sts.ard, an archive without names: sts.arh lists its entries (an 8-byte path hash,
the stored size, the unpacked size) in the order they sit in sts.ard, each 16-byte aligned. An entry is an xbc1 frame
(zlib or Zstandard) or stored as it is. Shaders sit in Monolith shader packages (magic HCPS): standalone, inside effects,
models and the map files' frames, and inside the xbc1 streams of the model stream files (DRSM). A package's header names its control
section block (records of the size at control+0x20, 0x850, each starting 0x98761234) and its code block (each code
starting with the 0x30-byte NVN header 0x12345678, compute code without it, 0x100-aligned, in the same order as the
controls). The key is SHA-256(control[:control_key_len] + code[:control+0x704]), exactly as nvn_mtl.m prog_get computes
it at run time, and the stage is control+0x714 (0 vertex, 1 fragment, 5 compute; 2, geometry, has no translation chain
and is listed with stage None). Packages whose controls are not version 1 hold another GPU's shaders and are skipped.

Read-only: game bytes stay in memory and are never written. Stdlib only (Python 3.14 for compression.zstd).
usage: xcx_shaders.py <work>/extracted/<v101|v200> [--check <folder with shader requests or a shader cache>]
"""
import argparse
from collections import namedtuple
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import time
import zlib
try:
    from compression import zstd   # Python 3.14; without it every Zstandard entry would be silently skipped
except ImportError:
    zstd = None   # importing still works (the tests, prepare_shader_seed.py's helpers); inventory() and main() refuse
NEEDS_ZSTD = 'xcx_shaders.py needs Python 3.14 (compression.zstd), as the rest of the build (requirements.txt)'

Shader = namedtuple('Shader', 'key stage control code source')
CONTROL = struct.pack('<I', 0x98761234)
CODE = struct.pack('<I', 0x12345678)
STAGES = {0: 'vs', 1: 'ps', 5: 'cs'}
KEY = re.compile(r'^([0-9a-f]{64})(?:-[0-9a-f]{12})?')


def control_key_len(control):
    """How many bytes of a control section the runtime hashes (nvn_mtl.m ctl_key_len): the word at +0x20 when it is in
    0x7cc..0x87f (0x850 in this game), else 0x880."""
    size = struct.unpack_from('<I', control, 0x20)[0] if len(control) >= 0x24 else 0
    return size if 0x7cc <= size < 0x880 else 0x880


def unpack_xbc1(data, at=0):
    """The xbc1 frame at data[at:]: magic, type (1 zlib, 3 Zstandard), unpacked size, packed size, hash, 0x1c-byte name,
    data at +0x30."""
    kind, size, packed = struct.unpack_from('<3I', data, at + 4)
    body = data[at+0x30:at+0x30+packed]
    if len(body) != packed or size > 1 << 30:
        raise ValueError('truncated or unbounded xbc1 frame')
    if kind == 3:
        out = zstd.decompress(body)
    elif kind == 1:
        out = zlib.decompress(body)
    else:
        raise ValueError(f'unknown xbc1 type {kind}')
    if len(out) != size:
        raise ValueError('xbc1 size mismatch')
    return out


class OtherGpu(ValueError):
    """A package for another GPU: its control records are not the 0x850-byte NVN ones (version 1 at +4) and its code is
    not Maxwell code with the 0x30-byte NVN header (docs/shaders-in-romfs.md: half the packages; never bound here)."""


def spch_shaders(data, at):
    """The shaders of the HCPS package at data[at:]: (stage, control, code) per control/code pair, or ValueError. Header:
    magic, version 0x3f2, then (offset, count/size) pairs; +0x20 the code block, +0x28 the control block."""
    if len(data) < at + 0x38:
        raise ValueError('short HCPS header')
    code_at, code_size, control_at, control_size = struct.unpack_from('<4I', data, at + 0x20)
    base = len(data) - at
    if not (0 < code_at and code_at + code_size <= base and 0 < control_at and control_at + control_size <= base):
        raise ValueError('HCPS blocks outside the data')
    out, pos, end = [], at + control_at, at + control_at + control_size
    code, code_end = at + code_at, at + code_at + code_size
    if data[pos:pos+4] == CONTROL and data[pos+4:pos+8] != b'\x01\0\0\0':
        raise OtherGpu('control version %#x' % struct.unpack_from('<I', data, pos + 4)[0])
    while pos + 0x24 <= end and data[pos:pos+4] == CONTROL:
        record = control_key_len(data[pos:pos+0x24])
        control = data[pos:pos+record]
        if len(control) != record or record < 0x718:
            raise ValueError('short control section')
        c1, length = struct.unpack_from('<II', control, 0x700)
        stage = STAGES.get(struct.unpack_from('<I', control, 0x714)[0])
        # Graphics code starts with the NVN header; compute code (3 shaders) starts with its first instruction.
        if (stage != 'cs' and data[code:code+4] != CODE) or not 0 < length <= code_end - code or c1 > length:
            raise ValueError('control and code out of step')
        out.append((stage, control, data[code:code+length]))
        pos += record
        code = (code + length + 0xff) & ~0xff
    if pos != end and any(data[pos:end]):
        raise ValueError('control block not fully read')
    return out


def visit(data, source, found, problems, depth=0):
    """Shaders in one buffer: every HCPS package in it, then the same inside each xbc1 frame it holds (an entry's own
    frames, the streams of a DRSM, the frames of a map file)."""
    found_here = 0
    at = data.find(b'HCPS') if data[:4] != b'xbc1' else -1   # (packed bytes hold no package)
    while at >= 0:
        if data[at+4:at+8] != b'\xf2\x03\0\0':   # the four letters by chance, not a package
            at = data.find(b'HCPS', at + 4)
            continue
        try:
            for stage, control, code in spch_shaders(data, at):
                key = hashlib.sha256(control + code).hexdigest()
                found.setdefault(key, Shader(key, stage, control, code, f'{source}/HCPS@{at:#x}'))
                found_here += 1
        except OtherGpu:
            problems.append(None)
        except ValueError as e:
            problems.append(f'{source}/HCPS@{at:#x}: {e}')
        at = data.find(b'HCPS', at + 4)
    if depth >= 3:
        return found_here
    at = data.find(b'xbc1')
    while at >= 0:
        try:
            inner = unpack_xbc1(data, at)
        except Exception:   # an xbc1 lookalike inside other data (zlib.error, ZstdError, struct.error, ValueError)
            inner = None
        if inner is not None:
            found_here += visit(inner, f'{source}/xbc1@{at:#x}', found, problems, depth + 1)
        at = data.find(b'xbc1', at + 4)
    return found_here


def romfs_index(romfs_dir):
    """index.json and romfs.bin, in <dir> or <dir>/romfs (extract_romfs.py writes the latter)."""
    romfs_dir = Path(romfs_dir)
    for d in (romfs_dir, romfs_dir/'romfs'):
        if (d/'index.json').is_file():
            index = json.loads((d/'index.json').read_text())
            if index.get('status') != 'pass':
                raise ValueError('RomFS has not passed integrity verification')
            return d/'romfs.bin', {r['path']: r for r in index['files']}
    raise FileNotFoundError(f'no index.json in {romfs_dir} or {romfs_dir}/romfs')


def archive_entries(romfs_bin, files):
    """(path hash, offset in romfs.bin, stored size) of every sts.ard entry, from sts.arh."""
    arh, ard = files['sts.arh'], files['sts.ard']
    with open(romfs_bin, 'rb') as f:
        f.seek(arh['offset'])
        head = f.read(arh['size'])
    if head[:4] != b'arh2':
        raise ValueError('expected an arh2 archive header')
    count, first = struct.unpack_from('<II', head, 4)
    if first + count*16 > len(head):
        raise ValueError('arh2 table outside the file')
    out, pos = [], 0
    for i in range(count):
        path_hash, stored, _ = struct.unpack_from('<QII', head, first + i*16)
        out.append((path_hash, ard['offset'] + pos, stored))
        pos = (pos + stored + 15) & ~15
    if pos > ard['size'] + 15:
        raise ValueError('arh2 entries run past sts.ard')
    return out


def scan(romfs_bin, chunk):
    """visit() each entry of chunk: ({key: Shader}, problems, the path hashes of the entries holding shaders)."""
    found, problems, holding = {}, [], set()
    with open(romfs_bin, 'rb') as f:
        for path_hash, offset, stored in chunk:
            f.seek(offset)
            raw = f.read(stored)
            # The entry as stored: an xbc1 frame (some, the map files, carry more frames after the first) or raw data.
            if visit(raw, f'sts.ard:{path_hash:016x}', found, problems):
                holding.add(path_hash)
    return found, problems, holding


def inventory(romfs_dir, workers=None):
    """Every shader in the RomFS -> {key: Shader}. romfs_dir: <work>/extracted/<v101|v200>/ (or its romfs/). A shader
    in several files keeps the first source found. Also sets inventory.files (entries holding shaders), .other_gpu
    (packages skipped) and .problems (packages that could not be read). Uses worker processes: call it from under
    `if __name__ == '__main__':`."""
    if zstd is None:
        raise RuntimeError(NEEDS_ZSTD)
    romfs_bin, files = romfs_index(romfs_dir)
    entries = archive_entries(romfs_bin, files)
    entries.sort(key=lambda e: -e[2])   # the large map files first, so no worker is left with one at the end
    chunks = [entries[i::512] for i in range(512)]
    shaders, problems, holding = {}, [], set()
    with concurrent.futures.ProcessPoolExecutor(workers or os.cpu_count()) as pool:
        for found, bad, files_here in pool.map(scan, [romfs_bin]*len(chunks), chunks):
            for key, shader in found.items():
                shaders.setdefault(key, shader)
            problems += bad
            holding |= files_here
    inventory.other_gpu = problems.count(None)
    inventory.problems = [p for p in problems if p]
    inventory.files = len(holding)
    return shaders


def ground_truth(folder):
    """{key: guest code after the runtime's skip, or None}: shader requests (variant, stage, code_hex) and cached
    translations (<key>-<deps>.code), anywhere under folder."""
    keys = {}
    for path in Path(folder).rglob('*'):
        m = KEY.match(path.name)
        if not m or not path.is_file():
            continue
        if path.suffix == '.code':
            keys[m.group(1)] = path.read_bytes()
        elif path.suffix == '.json' and not path.name.endswith('_b.json'):
            try:
                rq = json.loads(path.read_text())
            except ValueError:
                continue
            if isinstance(rq, dict) and isinstance(rq.get('code_hex'), str):
                keys[m.group(1)] = bytes.fromhex(rq['code_hex'])
        else:
            keys.setdefault(m.group(1), None)
    return keys


def main():
    ap = argparse.ArgumentParser(description='List the NVN shaders in the RomFS, keyed as the runtime keys them.')
    ap.add_argument('romfs', type=Path, help='<work>/extracted/<v101|v200>')
    ap.add_argument('--check', type=Path, action='append', default=[],
                    help='a folder of shader requests or a shader cache; its keys must be found with the same code')
    args = ap.parse_args()
    if zstd is None:
        sys.exit(NEEDS_ZSTD)
    started = time.monotonic()
    shaders = inventory(args.romfs)
    stages = {}
    for s in shaders.values():
        stages[s.stage] = stages.get(s.stage, 0) + 1
    print(f'found {len(shaders)} shaders in {inventory.files} files '
          f'({", ".join(f"{k} {v}" for k, v in sorted(stages.items(), key=str))}) in {time.monotonic() - started:.0f} s; '
          f'{inventory.other_gpu} packages for another GPU skipped, {len(inventory.problems)} unreadable', flush=True)
    for line in inventory.problems[:10]:
        print('  ' + line)
    for folder in args.check:
        truth = ground_truth(folder)
        recovered = mismatched = 0
        for key, code in truth.items():
            s = shaders.get(key)
            if not s:
                continue
            if code is not None and s.code[0 if s.stage == 'cs' else 0x30:] != code:
                mismatched += 1
                continue
            recovered += 1
        pct = 100 * recovered / len(truth) if truth else 0
        print(f'{folder}: found {len(shaders)} shaders in {inventory.files} files; ground truth {len(truth)} keys, '
              f'recovered {recovered} ({pct:.1f}%)' + (f'; {mismatched} with different code' if mismatched else ''))


if __name__ == '__main__':
    sys.exit(main())
