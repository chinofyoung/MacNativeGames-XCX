#!/usr/bin/env python3
"""The shared shader list (games/xcx/assets/shader_list.tsv): one list for the Mac, iPhone and iPad, kept by everyone.

It holds no game data, only identifiers; the build turns them into shaders from the player's own game files.
  V <shader key> <deps>         a shader (SHA-256 of its NVN control section and code) and the bind state it was
                                translated for (the translator's deps: texture, image, attribute and fragment output
                                types, as compact JSON with sorted keys)
  L <shader key> <source> <control> <code> <length>
                                where a shader that is no BNSH file sits in the player's game files: <source> is a RomFS
                                path (a package member after a '/') or module:<name>, the rest hex offsets and the code
                                length
  S <variant>_b <stage> <flags> a shader library the warm-up loads,
  C <variant>_b <flags>         a compute pipeline, and
  R <pipeline key> <flags>      a render pipeline it builds;
  B <pipeline key> <vertex> <fragment> <flags>
                                the same pipeline with the border colours its samplers had
                                Flags t: the title screen's, m: the rest of the warm-up.

parse_row() is strict and never raises: the submission workflow runs it on text anyone can send. A row is only valid in
the exact form the list saves it in.
usage: shader_list.py stats <list>
       shader_list.py import [--cache <dir>] [--requests <dir>] [--contribution <file>] [--seed <dir>] [--list <file>]
                               adds the rows a played game translated (Mac cache, iOS fill requests, contribution file) to
                               the list, minus what the default bind state serves; --seed defaults to the prepared seed
                               in XCX_build/build/shader-prep when it exists
"""
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HEADER = '# Xenoblade Chronicles X shader list v1 (games/xcx/scripts/shader_list.py; docs/sharing-shaders.md). Identifiers only, never game data.\n'
KEY = re.compile(r'^[0-9a-f]{64}$')
VARIANT = re.compile(r'^[0-9a-f]{64}-[0-9a-f]{12}_b$')
PIPELINE = re.compile(r'^(?:[0-9a-f]{2})+$')
HEX = re.compile(r'^[0-9a-f]{1,10}$')
_HEXFLOAT = r'-?0x[0-9a-f]+(?:\.[0-9a-f]*)?p[-+]\d+'
# per sampler "<argument id>:<axes>:<r>,<g>,<b>,<a>", a filtering sampler adding
# ":f<ws>,<wt>,<wr>,<magf>,<minf>,<aniso>,<lod min>,<lod max>" before ';'
_FILTER = rf':f\d+,\d+,\d+,\d+,\d+,{_HEXFLOAT},{_HEXFLOAT},(?:{_HEXFLOAT}|inf)'
BORDER = re.compile(rf'^(?:-|(?:\d+:[0-7]:{_HEXFLOAT},{_HEXFLOAT},{_HEXFLOAT},{_HEXFLOAT}(?:{_FILTER})?;){{1,8}})$')
SOURCE = re.compile(r'^(?:module:[a-z0-9]+|[A-Za-z0-9_.()+-]+(?:/[A-Za-z0-9_.()+-]+)*)$')
FLAGS = ('t', 'm', 'tm')
DEPS_FIELDS = {'tex', 'img', 'attr', 'fout'}
# The translator's own vocabulary (tools/totk_xlate Service.cs deps, nvn_mtl.m rt_answers): slot numbers as written
# by the translator, texture targets (with "_depth" for a depth-format texture, "*" for a slot only its depth-ness
# matters for), image targets and formats (nvn_mtl.m image_format), and attribute / colour output classes. Nothing
# else is a row.
SLOT = re.compile(r'^(?:0|[1-9][0-9]{0,2})$')
TARGETS = frozenset(('1d', '2d', '3d', '1darray', '2darray', '2dms', '2dmsarray', 'cube', 'cubearray', 'buffer'))
TEX_TYPES = TARGETS | {t + '_depth' for t in TARGETS} | {'*', '*_depth'}
IMG_FORMATS = frozenset((
    'R8Unorm', 'R8G8Unorm', 'R8G8B8A8Unorm', 'R8G8B8A8Uint', 'R16Float', 'R16G16Float', 'R16G16B16A16Float', 'R16Uint',
    'R16G16B16A16Uint', 'R32Float', 'R32G32Float', 'R32G32B32A32Float', 'R32Uint', 'R32Sint', 'R32G32Uint',
    'R32G32B32A32Uint', 'R10G10B10A2Unorm', 'R11G11B10Float'))
CLASSES = frozenset(('float', 'uint', 'sint'))
MAX_DEPS_BYTES = 4096
MAX_LINE = 16384   # longer than any row the build writes; keeps junk from being parsed at all


def canonical(deps):
    """The deps as one compact, sorted JSON text, or None when they are not the translator's deps."""
    if not isinstance(deps, dict) or not set(deps) <= DEPS_FIELDS:
        return None
    def image(v):   # {"type": <target>, "fmt": <format>}, either part left out when the translator had no answer
        return (isinstance(v, dict) and set(v) <= {'type', 'fmt'} and all(isinstance(x, str) for x in v.values())
                and v.get('type', '2d') in TARGETS and v.get('fmt', 'R8Unorm') in IMG_FORMATS)
    def word(names):
        return lambda v: isinstance(v, str) and v in names
    allowed = {'tex': word(TEX_TYPES), 'img': image, 'attr': word(CLASSES), 'fout': word(CLASSES)}
    for field, value in deps.items():
        if not isinstance(value, dict) or not all(isinstance(k, str) and _match(SLOT, k) and allowed[field](v)
                                                  for k, v in value.items()):
            return None
    return json.dumps(deps, sort_keys=True, separators=(',', ':'))


def _match(pattern, text):
    return pattern.match(text) is not None and '\n' not in text   # '$' also matches before a trailing newline


def _valid(fields):
    kind = fields[0]
    n = len(fields)
    if kind == 'V':
        if n != 3 or not _match(KEY, fields[1]) or len(fields[2]) > MAX_DEPS_BYTES:
            return False
        return canonical(json.loads(fields[2])) == fields[2]
    if kind == 'L':
        return (n == 6 and _match(KEY, fields[1]) and _match(SOURCE, fields[2])
                and all(part not in ('.', '..') for part in fields[2].split('/'))
                and all(_match(HEX, f) for f in fields[3:]))
    if kind == 'S':
        return n == 4 and _match(VARIANT, fields[1]) and fields[2] in ('0', '1', '5') and fields[3] in FLAGS
    if kind == 'C':
        return n == 3 and _match(VARIANT, fields[1]) and fields[2] in FLAGS
    if kind == 'R':
        return n == 3 and _match(PIPELINE, fields[1]) and fields[2] in FLAGS
    if kind == 'B':
        return (n == 5 and _match(PIPELINE, fields[1]) and _match(BORDER, fields[2]) and _match(BORDER, fields[3])
                and fields[2:4] != ['-', '-'] and fields[4] in FLAGS)
    return False


def parse_row(line):
    """A valid list row as the tuple of its fields, or None for anything else (comment, blank, malformed, unknown kind,
    deps that are not canonical or are over MAX_DEPS_BYTES). Any str in, tuple or None out, never an exception."""
    try:
        if not isinstance(line, str) or len(line) > MAX_LINE:
            return None
        line = line.removeprefix('\ufeff')
        if line.endswith('\r\n'):
            line = line[:-2]
        elif line.endswith('\n'):
            line = line[:-1]
        if not line or line.startswith('#') or '\n' in line or '\r' in line:
            return None
        fields = line.split('\t')
        return tuple(fields) if _valid(fields) else None
    except Exception:   # json on hostile text (deep nesting) and anything unforeseen: not a row
        return None


class ShaderList:
    def __init__(self):
        self._rows = {}   # row -> None; a dict keeps insertion order

    @classmethod
    def load(cls, path):
        lst = cls()
        path = Path(path)
        if not path.exists():
            return lst
        # split on '\n' only: a U+2028 or \x0b inside a line makes that row invalid, it does not split it
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').split('\n'), 1):
            line = line.removesuffix('\r')
            if not line or line.startswith('#'):
                continue
            row = parse_row(line)
            if row is None:
                raise ValueError(f'{path}:{number}: not a shader list row')
            lst._rows[row] = None
        return lst

    def rows(self):
        return list(self._rows)

    def add(self, rows):
        """Add rows; the ones that were not in the list yet, in the order given."""
        new = []
        for row in rows:
            row = tuple(row)
            if not all(isinstance(f, str) for f in row):
                raise ValueError(f'not a shader list row: {row!r}')
            if row in self._rows:
                continue
            if parse_row('\t'.join(row)) != row:
                raise ValueError(f'not a shader list row: {row!r}')
            self._rows[row] = None
            new.append(row)
        return new

    def remove(self, rows):
        """Remove rows; how many were in the list."""
        gone = 0
        for row in rows:
            if self._rows.pop(tuple(row), 1) is None:
                gone += 1
        return gone

    def text(self):
        v = sorted(row for row in self._rows if row[0] == 'V')
        rest = [row for row in self._rows if row[0] != 'V']
        return HEADER + ''.join('\t'.join(row) + '\n' for row in v + rest)

    def save(self, path):
        path = Path(path)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(self.text())
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise

    def shaders(self):
        """shader key -> the deps of its listed variants, from the V rows."""
        out = {}
        for row in sorted(r for r in self._rows if r[0] == 'V'):
            out.setdefault(row[1], []).append(json.loads(row[2]))
        return out

    def digest(self):
        return hashlib.sha256(self.text().encode()).hexdigest()


def _variant_dir(folder):
    """The folder holding <variant>_b.json files: the folder itself, or its rt-<chain> folder for this build's chain."""
    from prepare_shader_seed import CHAIN
    folder = Path(folder)
    return folder/f'rt-{CHAIN}' if (folder/f'rt-{CHAIN}').is_dir() else folder


def import_rows(cache=None, requests=None, contribution=None, seed=None):
    """V rows (key, canonical deps) for what a player's game translated: the <variant>_b.json files of a shader cache and of
    the iOS fill requests (deps are in the translator's reflection), and the valid rows of a contribution file; a folder or
    file that is missing adds none. A row the default bind state serves is left out, since every build makes it itself:
    with a prepared seed folder (prepare_shader_seed.builtin_serves), without one only deps that are the plain default themselves.
    Each row once, in the order read."""
    from prepare_shader_seed import DEFAULTS, builtin_serves
    rows = {}
    for folder in (cache, requests):
        if folder is None or not Path(folder).is_dir():
            continue
        for path in sorted(_variant_dir(folder).glob('*_b.json')):
            key = path.name.split('-')[0]
            try:
                deps = canonical(json.loads(path.read_text())['deps'])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            row = ('V', key, deps)
            if deps is not None and parse_row('\t'.join(row)) == row:
                rows[row] = None
    if contribution is not None and Path(contribution).is_file():
        for line in Path(contribution).read_text(encoding='utf-8-sig', errors='replace').split('\n'):
            row = parse_row(line.removesuffix('\r'))
            if row is not None and row[0] == 'V':
                rows[row] = None
    if seed is not None:
        return [r for r in rows if not builtin_serves(seed, r[1], r[2])]
    def plain(deps):   # every slot the plain default bind state: 2D textures, float inputs and outputs, no images
        return all(v == DEFAULTS[kind] for kind, slots in deps.items() for v in slots.values())
    return [r for r in rows if not plain(json.loads(r[2]))]


def _import(argv):
    from prepare_shader_seed import CHAIN
    here = Path(__file__).resolve().parent
    default_seed = here.parents[2]/'XCX_build/build/shader-prep'/f'rt-{CHAIN}'
    args = {'--cache': None, '--requests': None, '--contribution': None, '--seed': default_seed if default_seed.is_dir() else None,
            '--list': here.parent/'assets/shader_list.tsv'}
    if len(argv) % 2 or any(a not in args for a in argv[::2]):
        return None
    args.update({a: Path(v) for a, v in zip(argv[::2], argv[1::2])})
    lst = ShaderList.load(args['--list'])
    before = len(lst.rows())
    found = import_rows(args['--cache'], args['--requests'], args['--contribution'], args['--seed'])
    added = lst.add(found)
    lst.save(args['--list'])
    print(f"read {len(found)} rows not served by the default bind state (seed: {args['--seed'] or 'none'}); "
          f"{len(added)} new, {before + len(added)} rows in {args['--list']}")
    return 0


def main(argv):
    if argv and argv[0] == 'import':
        code = _import(argv[1:])
        if code is not None:
            return code
    if len(argv) == 2 and argv[0] == 'stats':
        lst = ShaderList.load(argv[1])
        kinds = {}
        for row in lst.rows():
            kinds[row[0]] = kinds.get(row[0], 0) + 1
        print(f'{len(lst.rows())} rows over {len(lst.shaders())} shaders: ' + ', '.join(f'{n} {k}' for k, n in sorted(kinds.items())))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
