#!/usr/bin/env python3
"""Library for reading an XCI or NSP in place: container and partition tables, prod.keys / title.keys, common tickets,
NCA3 headers and their key-area or common-ticket AES-CTR keys (merge_romfs.py, extract_code.py, identify_game.py,
extract_shared_fonts.py and extract_app_icon.py use it).

Adapted from the original MacNativeGames TotK scripts; its TotK-only command line (one base Program of TotK's title)
was removed: this port's ExeFS comes from extract_code.py. Keys stay in memory and are never printed.
"""
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decrypt(key, mode, data):
    ctx = Cipher(algorithms.AES(key), mode).decryptor()
    return ctx.update(data) + ctx.finalize()


def read_at(f, offset, length):
    require(0 <= offset and 0 <= length <= 512 * 1024 * 1024, 'Invalid/big read')
    f.seek(offset)
    data = f.read(length)
    require(len(data) == length, 'Truncated input')
    return data


def hfs(f, offset, bound):
    return partition_entries(f, offset, bound, b'HFS0', 64)


def partition_entries(f, offset, bound, magic=b'PFS0', stride=24):
    """Read a bounded PFS0/HFS0 table without loading its file payloads."""
    require(bound >= 16, 'Truncated partition header')
    hdr = read_at(f, offset, 16)
    require(hdr[:4] == magic, f'Expected {magic.decode()}')
    count, strings_size = struct.unpack_from('<II', hdr, 4)
    require(count <= 4096 and strings_size <= 1024 * 1024, 'Invalid partition table')
    size = 16 + count * stride + strings_size
    require(size <= bound, 'Partition table out of bounds')
    raw = read_at(f, offset, size)
    strings = raw[16 + count * stride:]
    entries = []
    names = set()
    for i in range(count):
        off, length, nameoff = struct.unpack_from('<QQI', raw, 16 + i * stride)
        require(nameoff < len(strings), 'Invalid partition filename offset')
        end = strings.find(b'\0', nameoff)
        require(end >= 0, 'Unterminated partition filename')
        name = strings[nameoff:end].decode('utf-8')
        require(name not in ('', '.', '..') and '/' not in name and '\\' not in name, 'Unsafe partition filename')
        require(name not in names, 'Duplicate partition filename')
        names.add(name)
        require(size + off + length <= bound, 'Partition entry out of bounds')
        entries.append((name, offset + size + off, length))
    end = offset + size
    for _, start, length in sorted(entries, key=lambda e: e[1]):
        require(start >= end, 'Overlapping partition entries')
        end = start + length
    return entries


def container_entries(f, size):
    if read_at(f, 0, 4) == b'PFS0':
        return 'nsp', partition_entries(f, 0, size)
    require(size >= 0x138 and read_at(f, 0x100, 4) == b'HEAD', 'Expected an XCI or NSP (PFS0) game file')
    root = struct.unpack('<Q', read_at(f, 0x130, 8))[0]
    partitions = hfs(f, root, size - root)
    secure = [e for e in partitions if e[0] == 'secure']
    require(len(secure) == 1, 'Expected one XCI secure partition')
    return 'xci', hfs(f, secure[0][1], secure[0][2])


def load_keys(path):
    keys = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if '=' in line:
            name, value = line.split('=', 1)
            try:
                keys[name.strip().lower()] = bytes.fromhex(value.strip())
            except ValueError:
                raise ValueError(f'Invalid key encoding at line {number}') from None
    return keys


def load_title_keys(path):
    """title.keys (`<rights ID> = <encrypted title key>`, 32 hex digits each) as {rights ID: key}, in memory only.

    For NSPs whose tickets are missing or personalized: the keys are the same encrypted title keys a common ticket
    holds, so nca_ctr_key decrypts them with titlekek the same way. Values are never printed."""
    keys = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.split('#', 1)[0].strip()
        if '=' not in line:
            continue
        name, value = (part.strip() for part in line.split('=', 1))
        try:
            rights, key = bytes.fromhex(name), bytes.fromhex(value)
        except ValueError:
            raise ValueError(f'Invalid title key encoding at line {number}') from None
        require(len(rights) == 16 and len(key) == 16, f'Invalid title key length at line {number}')
        keys[rights] = key
    return keys


def common_tickets(f, entries, personalized='error'):
    """Keep encrypted title keys in memory only, as nxnative's NSP importer does.

    Ticket offsets below are relative to the signed body; accept the standard RSA/ECDSA layouts.
    A personalized ticket cannot provide a common title key and is never treated as one: it stops the read
    (personalized='error'), or is passed over when the title key comes from elsewhere (personalized='skip': title.keys).
    """
    tickets = {}
    bodies = {0x10000: 0x240, 0x10001: 0x140, 0x10002: 0x80,
              0x10003: 0x240, 0x10004: 0x140, 0x10005: 0x80}
    for name, offset, size in entries:
        if not name.lower().endswith('.tik'):
            continue
        require(size >= 4, 'Truncated NSP ticket')
        # Switch ticket signature type is little-endian: RSA2048/SHA256 = 04 00 01 00.
        sig = struct.unpack('<I', read_at(f, offset, 4))[0]
        require(sig in bodies, 'Unsupported NSP ticket signature type')
        body = bodies[sig]
        require(size >= body + 0x180, 'Truncated NSP ticket body')
        data = read_at(f, offset + body, 0x180)
        if data[0x141] != 0:
            require(personalized == 'skip', 'Personalized NSP tickets are unsupported; a common ticket (or title.keys) is required')
            continue
        rights, key = data[0x160:0x170], data[0x40:0x50]
        require(rights not in tickets or tickets[rights] == key, 'Conflicting NSP tickets')
        tickets[rights] = key
    return tickets


def read_nca_header(f, offset, size, keys):
    require(size >= 0xc00, 'Truncated NCA header')
    require('header_key' in keys, 'Missing header_key in prod.keys')
    raw = read_at(f, offset, 0xc00)
    hdr = b''.join(decrypt(keys['header_key'], modes.XTS(i.to_bytes(16, 'big')),
                           raw[i * 512:(i + 1) * 512]) for i in range(6))
    require(hdr[0x200:0x204] == b'NCA3', 'Expected NCA3 (check prod.keys and the game file)')
    require(struct.unpack_from('<Q', hdr, 0x208)[0] == size, 'NCA size does not match container entry')
    for i in range(4):
        start, end = struct.unpack_from('<II', hdr, 0x240 + i * 16)
        require((start == end == 0) or 0xc00 <= start * 512 < end * 512 <= size, 'NCA section out of bounds')
    return hdr


def nca_ctr_key(hdr, keys, tickets):
    generation = max(max(hdr[0x206], hdr[0x220]) - 1, 0)
    rights = hdr[0x230:0x240]
    if rights != bytes(16):
        require(rights in tickets, 'Missing common ticket for rights-managed NCA in the NSP (no title key for it in title.keys either)')
        keyname = f'titlekek_{generation:02x}'
        require(keyname in keys, f'Missing {keyname} in prod.keys')
        return decrypt(keys[keyname], modes.ECB(), tickets[rights])
    require(hdr[0x207] < 3, 'Unsupported NCA key area')
    area = ('application', 'ocean', 'system')[hdr[0x207]]
    keyname = f'key_area_key_{area}_{generation:02x}'
    require(keyname in keys, f'Missing {keyname} in prod.keys')
    return decrypt(keys[keyname], modes.ECB(), hdr[0x300:0x340])[32:48]


def nca_inventory(f, entries, keys):
    records = []
    for name, offset, length in entries:
        if not name.lower().endswith('.nca'):
            continue
        hdr = read_nca_header(f, offset, length, keys)
        record = dict(name=name, nca_offset=offset, size=length, content_type=hdr[0x205],
                      title_id=f'{struct.unpack_from("<Q", hdr, 0x210)[0]:016X}')
        records.append((record, hdr))
    return records
