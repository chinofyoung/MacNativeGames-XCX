"""Build-time shader preparation: every shader of the RomFS translated for the default bind state, then the listed ones."""
import concurrent.futures
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
import prepare_shader_seed as seed
from shader_list import HEADER
from xcx_shaders import Shader

# Stands in for build/shader-service/totk_xlate --service: one request per line, one reply per line. It records each
# request in $FAKE_XLATE_LOG, fails the variants of the keys in $FAKE_XLATE_FAIL, and reports as deps the answers it was
# given over one bound slot of each kind (as the real translator reports the slots a shader uses).
FAKE_TRANSLATOR = textwrap.dedent('''\
    import json, os, sys
    args = sys.argv[1:]
    out, chain = args[args.index('--out')+1], args[args.index('--chain')+1]
    fail = set(filter(None, os.environ.get('FAKE_XLATE_FAIL', '').split(',')))
    for line in sys.stdin:
        rq = json.loads(line)
        with open(os.environ['FAKE_XLATE_LOG'], 'a') as log:
            log.write(json.dumps(rq) + '\\n')
        if rq['variant'].split('-')[0] in fail:
            print(json.dumps({'ok': False, 'error': 'RuntimeError: fake failure'}), flush=True)
            continue
        assert os.path.isfile(rq['code'])
        deps = {'tex': {'0': '2d', **rq['tex']}, 'img': dict(rq['img']), 'attr': {'0': 'float', **rq['attr']},
                'fout': {'0': 'float', **rq['fout']}}
        with open(os.path.join(out, rq['variant'] + '_b.metal'), 'w') as f:
            f.write('// fake MSL\\n')
        with open(os.path.join(out, rq['variant'] + '_b.json'), 'w') as f:
            json.dump({'key': rq['variant'] + '_b', 'chain': chain, 'stage': rq['stage'], 'deps': deps,
                       'compare_slots': []}, f)
        print(json.dumps({'ok': True}), flush=True)
''')


def shader(n, stage='ps'):
    control = bytearray(0x850)
    struct.pack_into('<II', control, 0x700, 0x40, 0x100)
    struct.pack_into('<I', control, 0x714, {'vs': 0, 'ps': 1, 'cs': 5, None: 2}[stage])
    struct.pack_into('<III', control, 0x748, 16, 1, 1)
    code = bytes([n % 256])*0x100
    key = hashlib.sha256(bytes(control)+code).hexdigest()
    return Shader(key, stage, bytes(control), code, f'sts.ard:{n:016x}/HCPS@0x0')


SHADERS = [shader(1, 'vs'), shader(2, 'ps'), shader(3, 'ps'), shader(4, 'cs')]
GEOMETRY = shader(5, None)
OTHER_KEY = 'f'*64   # a shader of the other game version: not in this RomFS


def fake_inventory(romfs_dir, workers=None):
    fake_inventory.calls += 1
    return {s.key: s for s in SHADERS + [GEOMETRY]}


class Prepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root/'extracted/v200/romfs').mkdir(parents=True)
        (root/'extracted/v200/romfs/index.json').write_text(json.dumps(
            dict(status='pass', romfs_sha256='ab'*32, files=[])))
        self.romfs = root/'extracted/v200'
        self.output = root/'build/shader-prep/rt-chain'
        self.translator = root/'totk_xlate'
        self.translator.write_text(f'#!{sys.executable}\n' + FAKE_TRANSLATOR)
        self.translator.chmod(0o755)
        self.spirv = root/'spirv-cross'
        self.spirv.write_text('spirv-cross')
        self.log = root/'requests.log'
        self.list = root/'shader_list.tsv'
        fake_inventory.calls = 0
        for patch in (mock.patch.object(seed, 'inventory', fake_inventory), mock.patch.object(seed, 'MIN_SHADERS', 2),
                      mock.patch.object(seed, 'CHAIN', 'chain'),
                      mock.patch.dict(os.environ, FAKE_XLATE_LOG=str(self.log), FAKE_XLATE_FAIL=''),
                      mock.patch('sys.stdout', io.StringIO())):   # its progress lines: not in the test output
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write_list(self, rows):
        self.list.write_text(HEADER + ''.join('\t'.join(r) + '\n' for r in rows))

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def prepare(self, shader_list=None):
        return seed.prepare(self.romfs, self.output, self.translator, self.spirv, 'v200', shader_list)

    def variants(self, key):
        return json.loads((self.output/f'{key}.variants').read_text())

    def test_translates_default_and_listed(self):
        sint = '{"attr":{"0":"sint"},"fout":{},"img":{},"tex":{}}'
        self.write_list([('V', SHADERS[0].key, sint)])
        result = self.prepare(self.list)
        self.assertEqual(result['shaders_found'], 4)   # the geometry shader has no translation chain
        self.assertEqual(result['default_translated'], 4)
        self.assertEqual((result['listed_rows'], result['listed_matched'], result['listed_translated']), (1, 1, 1))
        self.assertEqual((result['skipped_other_version'], result['reused']), (0, False))
        sent = self.requests()
        self.assertEqual(len(sent), 5)
        self.assertNotIn(GEOMETRY.key, {rq['variant'].split('-')[0] for rq in sent})
        for rq in sent:
            self.assertTrue(rq['depth_minus_w'])
            self.assertEqual(set(rq) - {'local'}, {'variant', 'stage', 'code', 'c1', 'depth_minus_w', 'tex', 'img', 'attr', 'fout'})
        cs = next(rq for rq in sent if rq['stage'] == 'cs')
        self.assertEqual(cs['local'], [16, 1, 1])
        # the runtime cache layout: <variant>_b.metal / _b.json and per shader its .variants, the default variant first
        for s in SHADERS:
            default = s.key + '-' + seed.SUFFIX
            self.assertTrue((self.output/f'{default}_b.metal').is_file())
            self.assertTrue((self.output/f'{default}_b.json').is_file())
            self.assertEqual(self.variants(s.key)[0]['variant'], default)
        listed = seed.answers_for(json.loads(sint))
        rows = self.variants(SHADERS[0].key)
        self.assertEqual([r['variant'] for r in rows], [SHADERS[0].key+'-'+seed.SUFFIX, SHADERS[0].key+'-'+seed.suffix(listed)])
        self.assertEqual(rows[1]['deps']['attr'], {'0': 'sint'})
        self.assertFalse(list(self.output.glob('*.code')))
        self.assertTrue(seed.builtin_serves(self.output, SHADERS[0].key, '{"attr":{},"fout":{},"img":{},"tex":{}}'))
        self.assertFalse(seed.builtin_serves(self.output, SHADERS[0].key, sint))

    def test_skips_keys_not_in_romfs(self):
        self.write_list([('V', OTHER_KEY, '{"attr":{"0":"sint"},"fout":{},"img":{},"tex":{}}')])
        result = self.prepare(self.list)
        self.assertEqual((result['listed_rows'], result['listed_matched'], result['skipped_other_version']), (1, 0, 1))
        self.assertEqual(result['listed_translated'], 0)
        self.assertNotIn(OTHER_KEY, {rq['variant'].split('-')[0] for rq in self.requests()})
        self.assertFalse((self.output/f'{OTHER_KEY}.variants').exists())

    def test_unchanged_inputs_reuse(self):
        self.write_list([('V', SHADERS[1].key, '{"attr":{},"fout":{"0":"uint"},"img":{},"tex":{}}')])
        first = self.prepare(self.list)
        calls = len(self.requests())
        second = self.prepare(self.list)
        self.assertEqual(len(self.requests()), calls)
        self.assertEqual(fake_inventory.calls, 1)
        self.assertTrue(second['reused'])
        self.assertEqual({k: v for k, v in second.items() if k != 'reused'}, {k: v for k, v in first.items() if k != 'reused'})
        self.assertTrue((self.output.parent/'prep-stamp.json').is_file())

    def test_changed_list_translates_only_the_new_rows(self):
        self.write_list([])
        self.prepare(self.list)
        calls = len(self.requests())
        self.write_list([('V', SHADERS[2].key, '{"attr":{},"fout":{},"img":{},"tex":{"0":"cube"}}')])
        result = self.prepare(self.list)
        self.assertFalse(result['reused'])
        self.assertEqual(len(self.requests()), calls + 1)   # the defaults come from the translation cache
        self.assertEqual(result['default_translated'], 4)
        for s in SHADERS:
            self.assertTrue((self.output/f'{s.key}-{seed.SUFFIX}_b.metal').is_file())

    def test_empty_or_missing_list_still_translates_defaults(self):
        for shader_list in (None, self.list):
            with self.subTest(shader_list=shader_list):
                self.write_list([])
                result = self.prepare(shader_list)
                self.assertEqual(result['default_translated'], 4)
                self.assertEqual((result['listed_rows'], result['listed_translated']), (0, 0))
                self.assertEqual(len(list(self.output.glob('*.variants'))), 4)
        missing = self.prepare(self.list.with_name('nothing-here.tsv'))
        self.assertEqual(missing['default_translated'], 4)

    def test_translator_failure_on_one_shader_continues(self):
        with mock.patch.dict(os.environ, FAKE_XLATE_FAIL=SHADERS[1].key):
            result = self.prepare()
        self.assertEqual(result['default_translated'], 3)
        self.assertEqual(result['failed'], 1)
        self.assertFalse((self.output/f'{SHADERS[1].key}.variants').exists())
        self.assertFalse(list(self.output.glob(SHADERS[1].key + '*')))
        for s in (SHADERS[0], SHADERS[2], SHADERS[3]):
            self.assertTrue((self.output/f'{s.key}-{seed.SUFFIX}_b.metal').is_file())

    def test_ctrl_c_stops_the_workers(self):
        many = [shader(n) for n in range(10, 40)]
        with mock.patch.object(seed, 'inventory', lambda romfs_dir, workers=None: {s.key: s for s in many}), \
                mock.patch.object(seed.os, 'cpu_count', lambda: 1), \
                mock.patch.object(concurrent.futures.Future, 'result', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.prepare()
        self.assertLess(len(self.requests()), 5)   # the worker stopped after its current shader, not after all 30

    def test_too_few_translated_shaders_is_refused(self):
        with mock.patch.object(seed, 'MIN_SHADERS', 5):
            with self.assertRaisesRegex(ValueError, 'at least 5'):
                self.prepare()
        self.assertFalse(self.output.exists())


class Chain(unittest.TestCase):
    def test_chain_is_read_from_dmsh_chain_h(self):
        header = (Path(seed.__file__).resolve().parents[1]/'third_party/d2r_gfx/gfx/nvn/dmsh_chain.h').read_text()
        self.assertIn(f'#define DMSH_CHAIN "{seed.chain()}"', header)


class ImportWithoutZstd(unittest.TestCase):
    def test_importing_xcx_shaders_never_exits(self):
        code = ('import sys; sys.modules["compression.zstd"] = None; sys.path.insert(0, sys.argv[1]); import xcx_shaders\n'
                'try:\n    xcx_shaders.inventory(".")\nexcept RuntimeError as e:\n    print("refused:", e)')
        run = subprocess.run([sys.executable, '-c', code, str(SCRIPTS)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('refused: xcx_shaders.py needs Python 3.14', run.stdout)


if __name__ == '__main__':
    unittest.main()
