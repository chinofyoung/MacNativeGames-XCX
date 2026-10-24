"""The shared shader list (games/xcx/assets/shader_list.tsv): parsing its rows and keeping the file."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import shader_list
from shader_list import HEADER, ShaderList, canonical, parse_row

KEY = 'a'*64
DEPS = '{"attr":{},"fout":{},"img":{},"tex":{}}'
V_ROW = f'V\t{KEY}\t{DEPS}'


def vrow(key, deps=DEPS):
    return ('V', key, deps)


class ParseRow(unittest.TestCase):
    def test_parse_valid_v_row(self):
        self.assertEqual(parse_row(V_ROW), ('V', KEY, DEPS))

    def test_rejects_non_canonical_deps(self):
        self.assertIsNone(parse_row(f'V\t{KEY}\t{{"attr": {{}}, "fout": {{}}, "img": {{}}, "tex": {{}}}}'))
        self.assertIsNone(parse_row(f'V\t{KEY}\t{{"tex":{{}},"attr":{{}},"fout":{{}},"img":{{}}}}'))
        self.assertIsNone(parse_row(f'V\t{KEY}\t{{"other":{{}}}}'))
        self.assertIsNone(parse_row(f'V\t{KEY}\tnot json'))

    def test_rejects_deps_over_the_size_limit(self):
        big = canonical({'tex': {str(i): '2dmsarray_depth' for i in range(300)}})
        self.assertGreater(len(big), shader_list.MAX_DEPS_BYTES)
        self.assertIsNone(parse_row(f'V\t{KEY}\t{big}'))

    def test_rejects_bad_key(self):
        for key in ('a'*63, 'a'*65, 'A'*64, 'g'*64, ''):
            self.assertIsNone(parse_row(f'V\t{key}\t{DEPS}'), key)

    def test_rejects_unknown_kind_and_junk(self):
        for line in (f'X\t{KEY}\t{DEPS}', '<html>', 'rm -rf /', '', '# comment', '\t\t', 'V', f'V\t{KEY}',
                     f'V\t{KEY}\t{DEPS}\textra', f'V\t{KEY}\t{DEPS}\n\n', '[' * 5000):
            self.assertIsNone(parse_row(line), repr(line))

    def test_never_raises_on_odd_text(self):
        for line in ('\x00', '\ud800', 'V\t' + KEY + '\t' + '[' * 3000, '\t'.join(['B'] * 50), 'é' * 10000):
            self.assertIsNone(parse_row(line))

    def test_crlf_and_bom_tolerated(self):
        self.assertEqual(parse_row('\ufeff' + V_ROW + '\r\n'), ('V', KEY, DEPS))
        self.assertEqual(parse_row(V_ROW + '\n'), ('V', KEY, DEPS))

    def test_other_kinds(self):
        variant = KEY + '-' + 'b'*12 + '_b'
        pipeline = '3'*80
        border = '-'
        for line in (f'L\t{KEY}\tdata/a.sharcb/x\t10\t20\t30', f'S\t{variant}\t1\tt', f'C\t{variant}\tm',
                     f'R\t{pipeline}\ttm', f'B\t{pipeline}\t{border}\t0:1:0x0p+0,0x0p+0,0x0p+0,0x1p+0;\tm'):
            row = parse_row(line)
            self.assertEqual(row, tuple(line.split('\t')), line)
        self.assertIsNone(parse_row(f'L\t{KEY}\t../x\t10\t20\t30'))
        self.assertIsNone(parse_row(f'S\t{variant}\t1\tz'))
        self.assertIsNone(parse_row(f'B\t{pipeline}\t-\t-\tm'))


class Canonical(unittest.TestCase):
    def test_sorted_compact_and_restricted(self):
        self.assertEqual(canonical({'tex': {'1': '2d'}, 'attr': {}}), '{"attr":{},"tex":{"1":"2d"}}')
        self.assertIsNone(canonical({'bad': {}}))
        self.assertIsNone(canonical([]))

    def test_only_the_translators_vocabulary(self):
        good = {'tex': {'0': '2d', '1': '2d_depth', '2': '*', '3': '*_depth', '127': 'cubearray'},
                'img': {'0': {}, '1': {'type': '2d'}, '2': {'type': '3d', 'fmt': 'R11G11B10Float'}},
                'attr': {'0': 'float', '15': 'uint'}, 'fout': {'0': 'sint'}}
        self.assertIsNotNone(canonical(good))
        for deps in ({'tex': {'hello world': 'see https://example.com/anything goes'}},
                     {'tex': {'0': 'texture2d'}}, {'tex': {'0': '2D'}}, {'tex': {'00': '2d'}}, {'tex': {'1000': '2d'}},
                     {'tex': {'-1': '2d'}}, {'tex': {'0': {'type': '2d'}}}, {'attr': {'0': 'double'}},
                     {'fout': {'0': '2d'}}, {'img': {'0': '2d'}}, {'img': {'0': {'type': '2d_depth'}}},
                     {'img': {'0': {'fmt': 'R8Unorm', 'x': 'y'}}}, {'img': {'0': {'fmt': 'Anything'}}},
                     {'img': {'0': {'type': ['2d']}}}, {'tex': {'0': ['2d']}}, {'tex': ['2d']}):
            self.assertIsNone(canonical(deps), deps)

    def test_hostile_free_text_row_rejected(self):
        self.assertIsNone(parse_row(f'V\t{KEY}\t{{"tex":{{"hello world":"see https://example.com/anything goes"}}}}'))

    def test_every_row_of_the_shared_list_parses(self):
        path = Path(__file__).resolve().parents[1]/'assets'/'shader_list.tsv'
        rows = ShaderList.load(path).rows()   # raises on any row parse_row refuses
        self.assertGreater(len(rows), 0)


class Kept(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)/'shader_list.tsv'

    def test_add_returns_only_new(self):
        lst = ShaderList()
        self.assertEqual(lst.add([vrow(KEY), vrow(KEY)]), [vrow(KEY)])
        self.assertEqual(lst.add([vrow(KEY)]), [])
        self.assertEqual(lst.rows(), [vrow(KEY)])

    def test_save_sorts_v_rows_and_keeps_header(self):
        lst = ShaderList()
        pipeline = '3'*80
        lst.add([vrow('c'*64), ('R', pipeline, 'm'), vrow('b'*64), ('R', '4'*80, 't')])
        lst.save(self.path)
        lines = self.path.read_text().splitlines(keepends=True)
        self.assertEqual(lines[0], HEADER)
        self.assertEqual([l.split('\t')[0] + l.split('\t')[1][:1] for l in lines[1:]], ['Vb', 'Vc', 'R3', 'R4'])
        again = ShaderList.load(self.path)
        self.assertEqual(again.rows(), [vrow('b'*64), vrow('c'*64), ('R', pipeline, 'm'), ('R', '4'*80, 't')])

    def test_load_missing_file_is_empty_and_junk_is_an_error(self):
        self.assertEqual(ShaderList.load(self.path).rows(), [])
        self.path.write_text('V\tnope\n')
        with self.assertRaises(ValueError):
            ShaderList.load(self.path)

    def test_load_tolerates_bom(self):
        self.path.write_bytes(b'\xef\xbb\xbf' + HEADER.encode() + V_ROW.encode() + b'\r\n')
        self.assertEqual(ShaderList.load(self.path).rows(), [vrow(KEY)])

    def test_load_line_separator_inside_row_is_rejected_not_split(self):
        for sep in ('\u2028', '\x0b', '\x85'):
            self.path.write_text(f'{V_ROW}{sep}{V_ROW}\n', encoding='utf-8')
            with self.assertRaises(ValueError, msg=repr(sep)):
                ShaderList.load(self.path)

    def test_save_is_atomic_and_lf(self):
        lst = ShaderList()
        lst.add([vrow(KEY)])
        lst.save(self.path)
        data = self.path.read_bytes()
        self.assertTrue(data.endswith(b'\n'))
        self.assertNotIn(b'\r', data)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])

    def test_add_non_str_raises_value_error(self):
        for row in (('V', KEY, 5), ('V', None, DEPS), (1,)):
            with self.assertRaises(ValueError):
                ShaderList().add([row])

    def test_remove_counts(self):
        lst = ShaderList()
        lst.add([vrow(KEY), vrow('b'*64)])
        self.assertEqual(lst.remove([vrow(KEY), vrow('c'*64)]), 1)
        self.assertEqual(lst.rows(), [vrow('b'*64)])

    def test_digest_stable(self):
        lst = ShaderList()
        lst.add([vrow('b'*64), vrow(KEY)])
        other = ShaderList()
        other.add([vrow(KEY), vrow('b'*64)])
        self.assertEqual(lst.digest(), other.digest())
        lst.save(self.path)
        self.assertEqual(lst.digest(), hashlib.sha256(self.path.read_bytes()).hexdigest())
        lst.add([vrow('d'*64)])
        self.assertNotEqual(lst.digest(), other.digest())

    def test_shaders_maps_key_to_deps(self):
        lst = ShaderList()
        other = canonical({'tex': {'0': 'cube'}})
        lst.add([vrow(KEY), vrow(KEY, other), vrow('b'*64), ('R', '3'*80, 'm')])
        shaders = lst.shaders()
        self.assertEqual(set(shaders), {KEY, 'b'*64})
        self.assertEqual(sorted(shaders[KEY], key=str), sorted([{'attr': {}, 'fout': {}, 'img': {}, 'tex': {}},
                                                                {'tex': {'0': 'cube'}}], key=str))


class Import(unittest.TestCase):
    """shader_list.py import: rows from what the owner played, minus what the default bind state serves."""
    ODD = {'attr': {}, 'fout': {}, 'img': {}, 'tex': {'0': 'cube'}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp/'cache'
        self.cache.mkdir()

    def variant(self, folder, key, deps, suffix):
        (folder/f'{key}-{suffix}_b.json').write_text(json.dumps({'key': f'{key}-{suffix}_b', 'deps': deps}))

    def test_import_from_cache_reads_deps_from_json(self):
        self.variant(self.cache, KEY, self.ODD, '0123456789ab')
        (self.cache/'junk_b.json').write_text('not json')
        (self.cache/(KEY+'.variants')).write_text('[]')
        rows = shader_list.import_rows(cache=self.cache)
        self.assertEqual(rows, [('V', KEY, canonical(self.ODD))])
        self.assertIsNotNone(parse_row('\t'.join(rows[0])))

    def test_import_skips_default_variants(self):
        import prepare_shader_seed as prep
        seed = self.tmp/'seed'
        seed.mkdir()
        default = {'tex': {'0': '2d'}, 'img': {}, 'attr': {'0': 'float'}, 'fout': {}}
        self.variant(seed, KEY, default, prep.SUFFIX)
        (seed/f'{KEY}.variants').write_text(json.dumps([{'variant': f'{KEY}-{prep.SUFFIX}', 'deps': default}]))
        self.variant(self.cache, KEY, default, prep.SUFFIX)
        self.variant(self.cache, KEY, self.ODD, '0123456789ab')
        self.variant(self.cache, 'b'*64, default, prep.SUFFIX)
        odd = ('V', KEY, canonical(self.ODD))
        self.assertEqual(shader_list.import_rows(cache=self.cache, seed=seed), [odd, ('V', 'b'*64, canonical(default))])
        # no seed: only what the plain default bind state serves is skipped
        self.assertEqual(shader_list.import_rows(cache=self.cache), [odd])

    def test_import_reads_contribution_and_tolerates_missing_inputs(self):
        contribution = self.tmp/'c.txt'
        odd = canonical(self.ODD)
        contribution.write_text(f'junk\nV\t{KEY}\t{odd}\nV\t{KEY}\n{V_ROW}\n')
        self.assertEqual(shader_list.import_rows(cache=self.tmp/'none', requests=self.tmp/'none2',
                                                 contribution=contribution), [('V', KEY, odd)])
        self.assertEqual(shader_list.import_rows(contribution=self.tmp/'absent'), [])


if __name__ == '__main__':
    unittest.main()
