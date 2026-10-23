#!/usr/bin/env python3
"""Merge the base Program NCA and its BKTR update (both inside one XCI) into one RomFS image; verify all six IVFC levels; index it.

Xenoblade Chronicles X: Definitive Edition ships as one XCI holding the complete base (v0) and the 1.0.1 update
(0100453019AA8800 v65536) with its common ticket. The base can also come from a base NSP or a base-only XCI and the
update from its own NSP (`--update`); NCAs of an NSP are decrypted with the NSP's common ticket, or with `--title-keys`
(title.keys) when it has none. Both Program NCAs carry the base title ID, so each is found through its
own content meta (CNMT) instead: the Application meta names the base Program, the Patch meta the update Program.
Unlike Bayonetta 3's, the base section is complete (not sparse, not compressed). The layers are, from the bottom up:
  base:   NCA body -> AES-CTR (the section's own upper IV)
  update: NCA body -> AES-CTR-Ex (per-entry generation) ; indirect table -> base section or update data
The IVFC header of the update section covers the merged image, so a wrong layer fails a block hash.
Keys stay in memory. No executable launch, no network.
"""
import argparse
import bisect
import hashlib
import io
import json
import struct
import sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import modes

sys.path.insert(0, str(Path(__file__).resolve().parent))   # extract_exefs / extract_romfs: this folder's copies of games/totk/scripts
from extract_exefs import (decrypt, read_at, require, load_keys, load_title_keys, container_entries, common_tickets,
                           nca_ctr_key, read_nca_header, partition_entries)
from extract_romfs import index_romfs

BASE_TITLE = '0100453019AA8000'
PATCH_TITLE = '0100453019AA8800'
PATCH_VERSION = 65536   # 1.0.1
NODE = 0x4000
CHUNK = 8 << 20


class Ctr:
    """AES-CTR over the NCA body at absolute NCA offsets; `upper` is the 8-byte big-endian upper IV."""
    def __init__(self, f, nca_offset, nca_size, key):
        self.f, self.base, self.size, self.key = f, nca_offset, nca_size, key

    def raw(self, absolute, size):
        require(0 <= absolute and absolute + size <= self.size, 'Read past the stored NCA')
        return read_at(self.f, self.base + absolute, size)

    def read(self, absolute, size, upper):
        aligned = absolute & ~15
        data = self.raw(aligned, size + absolute - aligned)
        return decrypt(self.key, modes.CTR(upper + (aligned // 16).to_bytes(8, 'big')), data)[absolute - aligned:]


def upper_iv(generation, secure):
    return struct.pack('>II', secure, generation)


def sections(hdr):
    """(index, start, end, fs header) of every present section, each FS header checked against the NCA header's hash."""
    out = []
    for i in range(4):
        start, end = struct.unpack_from('<II', hdr, 0x240 + 16 * i)
        if not start:
            continue
        fs = hdr[0x400 + 512 * i:0x600 + 512 * i]
        require(hashlib.sha256(fs).digest() == hdr[0x280 + 32 * i:0x2a0 + 32 * i], 'FS header hash mismatch')
        out.append((i, start * 512, end * 512, fs))
    return out


def read_pfs(ctr, start, end, fs):
    """The files of a hashed PFS0 section (ExeFS or CNMT), with its hash table and every block verified."""
    require(fs[2:4] == b'\x01\x02' and fs[4] == 3, 'Expected an AES-CTR PFS0 section')
    blocksize, levels = struct.unpack_from('<II', fs, 0x28)
    hashoff, hashsize, pfsoff, pfssize = struct.unpack_from('<QQQQ', fs, 0x30)
    require(levels == 2 and blocksize > 0 and pfssize <= 512 * 1024 * 1024, 'Unsupported PFS0 hash layout')
    require(hashoff + hashsize <= end - start and pfsoff + pfssize <= end - start, 'PFS0 out of bounds')
    upper = fs[0x140:0x148][::-1]
    hashes = ctr.read(start + hashoff, hashsize, upper)
    require(hashlib.sha256(hashes).digest() == fs[8:40], 'PFS0 hash-table mismatch')
    pfs = ctr.read(start + pfsoff, pfssize, upper)
    for index, pos in enumerate(range(0, pfssize, blocksize)):
        require(hashlib.sha256(pfs[pos:pos + blocksize]).digest() == hashes[index * 32:(index + 1) * 32],
                f'PFS0 block hash mismatch: {index}')
    return [(name, pfs[off:off + size]) for name, off, size in partition_entries(io.BytesIO(pfs), 0, len(pfs))]


def parse_cnmt(data):
    """Title, version, type and Program content ID of a packaged content meta."""
    tid, version, kind = struct.unpack_from('<QIB', data, 0)
    ext_size, count = struct.unpack_from('<HH', data, 0xe)
    programs = []
    for k in range(count):
        row = data[0x20 + ext_size + 0x38 * k:0x20 + ext_size + 0x38 * (k + 1)]
        require(len(row) == 0x38, 'Truncated content meta')
        if row[0x36] == 1 and row[0x37] == 0:   # Program, id offset 0
            programs.append(row[0x20:0x30].hex())
    require(len(programs) == 1, 'Expected one Program content per meta')
    return dict(title_id=f'{tid:016X}', version=version, type=kind, program=programs[0])


def open_program(path, keys, which, title_keys=None):
    """The base ('base') or update ('update') Program NCA of an XCI or NSP: (file, record, header, Ctr, meta).

    Both Program NCAs carry the base title ID; the Application meta (base) and the Patch meta 1.0.1 (update) name them.
    title_keys ({rights ID: encrypted title key}, load_title_keys) fills in for missing or personalized tickets; a
    common ticket in the file wins."""
    require(which in ('base', 'update'), 'which: base or update')
    f = path.open('rb')
    kind, entries = container_entries(f, path.stat().st_size)
    tickets = {**(title_keys or {}), **common_tickets(f, entries, 'skip' if title_keys else 'error')}
    ncas = {}
    for name, offset, length in entries:
        if name.lower().endswith('.nca'):
            ncas[name.lower()] = (dict(name=name, nca_offset=offset, size=length), read_nca_header(f, offset, length, keys))
    metas = []
    for record, hdr in ncas.values():
        if hdr[0x205] != 1:
            continue
        ctr = Ctr(f, record['nca_offset'], record['size'], nca_ctr_key(hdr, keys, tickets))
        for _, start, end, fs in sections(hdr):
            for fname, data in read_pfs(ctr, start, end, fs):
                if fname.endswith('.cnmt'):
                    metas.append(dict(nca=record['name'], **parse_cnmt(data)))
    if which == 'base':
        found = [m for m in metas if m['type'] == 0x80 and m['title_id'] == BASE_TITLE]
        require(len(found) == 1, f'Expected one Application meta for {BASE_TITLE}')
    else:
        found = [m for m in metas if m['type'] == 0x81 and m['title_id'] == PATCH_TITLE]
        require(len(found) == 1, f'Expected one Patch meta for {PATCH_TITLE} (the 1.0.1 update)')
        require(found[0]['version'] == PATCH_VERSION, f'Update version {found[0]["version"]}; only 1.0.1 (v{PATCH_VERSION}) is supported')
    meta = found[0]
    require(f'{meta["program"]}.nca' in ncas, f'Program NCA {meta["program"]} named by the meta is missing')
    record, hdr = ncas[f'{meta["program"]}.nca']
    require(hdr[0x205] == 0 and f'{struct.unpack_from("<Q", hdr, 0x210)[0]:016X}' == BASE_TITLE,
            'The meta names an NCA that is not a Program of this game')
    romfs = [fs for _, _, _, fs in sections(hdr) if fs[2:4] == b'\x00\x03']
    require(len(romfs) == 1 and romfs[0][4] == (4 if which == 'update' else 3),
            'Expected a BKTR RomFS in the update and a plain AES-CTR RomFS in the base')
    record['container'] = kind
    return f, record, hdr, Ctr(f, record['nca_offset'], record['size'], nca_ctr_key(hdr, keys, tickets)), meta


def romfs_section(hdr):
    for _, start, end, fs in sections(hdr):
        if fs[2:4] == b'\x00\x03':
            return start, end, fs
    raise ValueError('No RomFS section')


def bucket_tree(read, size, header, entry_size, unpack):
    """Entries of a single-level bucket tree (L1 node + entry sets), each with its [start, end) virtual range."""
    magic, version, count = struct.unpack_from('<4sIi', header, 0)
    require(magic == b'BKTR' and version == 1 and count >= 0, 'Unsupported bucket tree header')
    if count == 0:
        return [], 0
    per_set = (NODE - 16) // entry_size
    sets = -(-count // per_set)
    require(sets <= (NODE - 16) // 8, 'Bucket trees with L2 nodes are unsupported')
    require(size == NODE * (1 + sets), 'Bucket tree size does not match its entry count')
    l1 = read(0, NODE)
    index, l1_count, total = struct.unpack_from('<iiq', l1, 0)
    require(index == 0 and l1_count == sets, 'Bucket tree L1 node')
    out = []
    for s in range(sets):
        node = read(NODE * (1 + s), NODE)
        index, n, end = struct.unpack_from('<iiq', node, 0)
        require(index == s and 0 < n <= per_set, 'Bucket tree entry set')
        first = struct.unpack_from('<q', l1, 16 + 8 * s)[0] if s else 0
        rows = [unpack(node, 16 + entry_size * k) for k in range(n)]
        require(rows[0][0] == first, 'Bucket tree entry set offset')
        for k, row in enumerate(rows):
            stop = rows[k + 1][0] if k + 1 < n else end
            require(row[0] < stop, 'Bucket tree entries out of order')
            out.append((row[0], stop) + tuple(row[1:]))
    require(len(out) == count and out[-1][1] == total, 'Bucket tree entry count / end offset')
    return out, total


def indirect_row(node, at):
    return struct.unpack_from('<qqi', node, at)


def ctr_ex_row(node, at):
    offset, encryption, generation = struct.unpack_from('<qB3xi', node, at)
    return offset, encryption, generation


class Ranges:
    def __init__(self, rows):
        self.rows, self.starts = rows, [r[0] for r in rows]

    def split(self, offset, size):
        """(row, offset within the row's range, length) pieces covering [offset, offset + size)."""
        i = bisect.bisect_right(self.starts, offset) - 1
        while size:
            require(0 <= i < len(self.rows) and self.rows[i][0] <= offset < self.rows[i][1], 'Read outside the table')
            row = self.rows[i]
            n = min(size, row[1] - offset)
            yield row, offset - row[0], n
            offset += n; size -= n; i += 1


class PlainBase:
    """The base RomFS section: complete and AES-CTR encrypted with the section's own upper IV."""
    def __init__(self, ctr, start, end, fs):
        require(fs[4] == 3, 'Expected an AES-CTR base RomFS section')
        require(struct.unpack_from('<Q', fs, 0x150)[0] == 0, 'Base RomFS is sparse; this game\'s base is expected complete')
        require(not any(fs[0x178:0x188]), 'Compressed base RomFS sections are unsupported')
        gen0, secure = struct.unpack_from('<II', fs, 0x140)
        self.ctr, self.start, self.size, self.upper = ctr, start, end - start, upper_iv(gen0, secure)
        self.stored = 0

    def read(self, offset, size):
        require(0 <= offset and offset + size <= self.size, 'Base section read out of bounds')
        self.stored += size
        return self.ctr.read(self.start + offset, size, self.upper)


class Patched:
    """The update's RomFS section: indirect table over (base section, AES-CTR-Ex update data)."""
    def __init__(self, ctr, start, end, fs, base):
        ind_off, ind_size = struct.unpack_from('<QQ', fs, 0x100)
        ex_off, ex_size = struct.unpack_from('<QQ', fs, 0x120)
        gen0, self.secure = struct.unpack_from('<II', fs, 0x140)
        require(fs[4] == 4, 'Expected an AES-CTR-Ex (BKTR) update section')
        require(ind_off + ind_size <= ex_off and ex_off + ex_size <= end - start, 'Patch tables out of bounds')
        # The AES-CTR-Ex table itself is plain AES-CTR; it covers everything before it, the indirect table included.
        plain = upper_iv(gen0, self.secure)
        ex_rows, ex_total = bucket_tree(lambda o, n: ctr.read(start + ex_off + o, n, plain), ex_size,
                                        fs[0x130:0x140], 0x10, ctr_ex_row)
        require(ex_total == ex_off, 'AES-CTR-Ex table must cover the section up to itself')
        self.ctr, self.start, self.base, self.ex = ctr, start, base, Ranges(ex_rows)
        ind_rows, total = bucket_tree(lambda o, n: self.update_data(ind_off + o, n), ind_size,
                                      fs[0x110:0x120], 0x14, indirect_row)
        require(all(r[3] in (0, 1) for r in ind_rows), 'Indirect storage index')
        self.total, self.ind, self.data_size = total, Ranges(ind_rows), ind_off
        self.from_base = self.from_update = 0

    def update_data(self, offset, size):
        out = bytearray()
        for (_, _, encryption, generation), _, n in self.ex.split(offset, size):
            absolute = self.start + offset + len(out)
            if encryption == 1:
                out += self.ctr.raw(absolute, n)
            else:
                require(encryption == 0, 'AES-CTR-Ex encryption value')
                out += self.ctr.read(absolute, n, upper_iv(generation, self.secure))
        return bytes(out)

    def read(self, offset, size):
        out = bytearray()
        for (_, _, target, storage), within, n in self.ind.split(offset, size):
            if storage == 0:
                out += self.base.read(target + within, n); self.from_base += n
            else:
                require(target + within + n <= self.data_size, 'Update data out of bounds')
                out += self.update_data(target + within, n); self.from_update += n
        return bytes(out)


def verify_ivfc(fs, size, read, image_path):
    """All six IVFC levels (as games/totk/scripts/extract_romfs.py); level 6 is streamed to image_path."""
    require(fs[8:12] == b'IVFC' and struct.unpack_from('<III', fs, 12) == (0x20000, 32, 7), 'Unsupported IVFC layout')
    levels = [struct.unpack_from('<QQII', fs, 0x18 + j * 24) for j in range(6)]
    parent, counts, digest = fs[0xc8:0xe8], [], hashlib.sha256()
    with image_path.open('wb') as image:
        for level, (offset, length, power, reserved) in enumerate(levels):
            require(12 <= power <= 24 and reserved == 0 and offset + length <= size, 'IVFC level layout')
            block = 1 << power
            require(len(parent) >= (length + block - 1) // block * 32, 'IVFC parent size')
            captured = bytearray() if level < 5 else None
            index = 0
            for pos in range(0, length, CHUNK):
                chunk = read(offset + pos, min(CHUNK, length - pos))
                for j in range(0, len(chunk), block):
                    payload = chunk[j:j + block]
                    require(hashlib.sha256(payload + bytes(block - len(payload))).digest() == parent[index * 32:index * 32 + 32],
                            f'IVFC level {level} block {index} failed')
                    index += 1
                if captured is not None:
                    captured.extend(chunk)
                else:
                    image.write(chunk); digest.update(chunk)
                    if pos % (1 << 30) < CHUNK:
                        print(f'IVFC level {level}: {pos >> 30} / {length >> 30} GiB', flush=True)
            counts.append(index)
            if captured is not None:
                parent = bytes(captured)
            print(f'IVFC level {level}: {index} blocks verified', flush=True)
    return counts, levels[-1][1], digest.hexdigest()


def open_merged(game, keys, update=None, title_keys=None):
    """(files, base record, update record, update FS header, Patched): the base from `game`, the update from `update`
    (an NSP) or, without it, from `game` too (the XCI holding both); the caller closes the files."""
    f, brec, bhdr, bctr, bmeta = open_program(game, keys, 'base', title_keys)
    _, urec, uhdr, uctr, umeta = open_program(update or game, keys, 'update', title_keys)   # a second handle
    base = PlainBase(bctr, *romfs_section(bhdr))
    ustart, uend, ufs = romfs_section(uhdr)
    return (f, uctr.f), dict(brec, meta=bmeta), dict(urec, meta=umeta), ufs, Patched(uctr, ustart, uend, ufs, base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', type=Path, help='the XCI (base v0 + update 1.0.1), or the base game (XCI or NSP) with --update')
    parser.add_argument('--keys', required=True, type=Path)
    parser.add_argument('--update', type=Path, help='the 1.0.1 update NSP (default: the update inside the game file)')
    parser.add_argument('--title-keys', type=Path, help='title.keys, for NSPs without a common ticket')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), 'Output already exists; choose a new directory')
    title_keys = load_title_keys(args.title_keys) if args.title_keys else None
    handles, brec, urec, ufs, patched = open_merged(args.game, load_keys(args.keys), args.update, title_keys)
    try:
        args.output.mkdir(parents=True)
        image = args.output / 'romfs.bin'
        counts, size, sha = verify_ivfc(ufs, patched.total, patched.read, image)
        files = index_romfs(image)
        stat = args.game.stat()
        report = dict(status='pass', source=str(args.game.resolve()), source_size=stat.st_size, source_mtime_ns=stat.st_mtime_ns,
                      update_source=str(args.update.resolve()) if args.update else None,
                      romfs_mtime_ns=image.stat().st_mtime_ns, base=brec, update=urec, ivfc_blocks=counts, romfs_size=size, romfs_sha256=sha,
                      bytes_from_base=patched.from_base, bytes_from_update=patched.from_update,
                      files=files, integrity='FS headers, content metas and all six IVFC levels of the merged image verified; RSA signatures not checked')
        (args.output / 'index.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(dict(status='pass', file_count=len(files), size=size, sha256=sha, ivfc_blocks=counts,
                              bytes_from_base=patched.from_base, bytes_from_update=patched.from_update)), flush=True)
    finally:
        for h in handles:
            h.close()


if __name__ == '__main__':
    main()
