"""build_app.py's shader preparation step: its place in the build, the iOS seed's caches and the Mac cache copy."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(REPO))
import build_app

KEY = 'a'*64
VARIANT = f'{KEY}-0123456789ab'
LIST = '# Xenoblade Chronicles X shader list v1\nV\t' + 'b'*64 + '\t{"attr":{},"fout":{},"img":{},"tex":{}}\n'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class StepOrder(unittest.TestCase):
    def test_step_order_has_shader_prep_after_shader_tools(self):
        self.assertEqual(build_app.STEPS.index('shaders: prepare'), build_app.STEPS.index('shader tools') + 1)


class IosSeedCaches(unittest.TestCase):
    def test_ios_seed_caches_puts_prep_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            chain = build_app.shader_chain()
            mine = work/f'build/shader-cache/rt-{chain}'
            mine.mkdir(parents=True)
            prep = work/f'build/shader-prep/rt-{chain}'
            prep.mkdir(parents=True)
            caches = build_app.ios_seed_caches(work, [], prep)
            self.assertEqual(caches[0], prep)
            self.assertIn(mine, caches)
            self.assertNotIn(prep, build_app.ios_seed_caches(work, []))   # a failed preparation: no prep folder given

    def test_missing_prep_folder_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            prep = work/'build/shader-prep/rt-x'
            self.assertNotIn(prep, build_app.ios_seed_caches(work, [], prep))


def index(*variants):
    return json.dumps([dict(variant=v, deps={'tex': {}}) for v in variants], sort_keys=True, separators=(',', ':'))


class MacCopy(unittest.TestCase):
    def test_mac_copy_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            write(prep/f'{VARIANT}.metal', 'x')   # not a runtime cache name: left out
            write(prep/f'{VARIANT}_b.metal', 'prepared msl')
            write(prep/f'{VARIANT}_b.json', 'prepared refl')
            other = f'{"c"*64}-0123456789ab'
            write(prep/f'{other}_b.metal', 'other msl')
            write(prep/f'{other}_b.json', 'other refl')
            write(prep/f'{KEY}.variants', index(VARIANT))
            write(prep/f'{"c"*64}.variants', index(other))
            write(prep/'seed-manifest.json', '{}')
            write(cache/f'{VARIANT}_b.metal', 'played msl')
            write(cache/f'{KEY}.variants', 'not json')   # the game's index, unreadable: left alone
            new = build_app.copy_prepared_shaders(prep, cache)
            self.assertEqual(new, 1)   # only the other shader's variant is new
            self.assertEqual((cache/f'{VARIANT}_b.metal').read_text(), 'played msl')
            self.assertEqual((cache/f'{KEY}.variants').read_text(), 'not json')
            self.assertFalse((cache/f'{VARIANT}_b.json').exists())
            self.assertEqual((cache/f'{other}_b.metal').read_text(), 'other msl')
            self.assertEqual((cache/f'{other}_b.json').read_text(), 'other refl')
            self.assertEqual((cache/f'{"c"*64}.variants').read_text(), index(other))
            self.assertFalse((cache/'seed-manifest.json').exists())
            self.assertFalse((cache/f'{VARIANT}.metal').exists())
            self.assertEqual(sorted(p.name for p in cache.iterdir() if p.name.endswith('.tmp')), [])
            self.assertEqual(build_app.copy_prepared_shaders(prep, cache), 0)   # again: nothing new

    def test_mac_merge_adds_missing_variants_keeps_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            added = f'{KEY}-ba9876543210'
            for v, text in ((VARIANT, 'prepared'), (added, 'added')):
                write(prep/f'{v}_b.metal', f'{text} msl')
                write(prep/f'{v}_b.json', f'{text} refl')
            write(prep/f'{KEY}.variants', index(added, VARIANT))
            played = json.dumps([dict(variant=VARIANT, deps={'tex': {'0': 'cube'}})])
            write(cache/f'{KEY}.variants', played)
            write(cache/f'{VARIANT}_b.metal', 'played msl')
            write(cache/f'{VARIANT}_b.json', 'played refl')
            self.assertEqual(build_app.copy_prepared_shaders(prep, cache), 1)
            merged = json.loads((cache/f'{KEY}.variants').read_text())
            self.assertEqual(merged, json.loads(played) + json.loads(index(added)))   # the existing entry first, kept
            self.assertEqual((cache/f'{VARIANT}_b.metal').read_text(), 'played msl')
            self.assertEqual((cache/f'{VARIANT}_b.json').read_text(), 'played refl')
            self.assertEqual((cache/f'{added}_b.metal').read_text(), 'added msl')
            self.assertEqual((cache/f'{added}_b.json').read_text(), 'added refl')
            self.assertEqual(sorted(p.name for p in cache.iterdir() if p.name.endswith('.tmp')), [])
            self.assertEqual(build_app.copy_prepared_shaders(prep, cache), 0)


class MacCopySafety(unittest.TestCase):
    def prepared(self, prep):
        other = f'{"c"*64}-0123456789ab'
        write(prep/f'{other}_b.metal', 'msl')
        write(prep/f'{other}_b.json', 'refl')
        write(prep/f'{"c"*64}.variants', index(other))

    def test_game_running_skips_the_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)
            with mock.patch.object(build_app, 'game_running', lambda out: True), \
                    mock.patch.object(build_app, 'mac_shader_cache', lambda: cache), \
                    mock.patch.object(build_app, 'say') as said:
                build_app.merge_into_mac_cache(prep, Path(tmp)/'X.app')
            self.assertFalse(cache.exists() and any(cache.iterdir()))
            said.assert_called_once_with('shaders: the game is running; prepared shaders go into the Mac cache on the '
                                         'next build')

    def test_merge_when_the_game_is_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)
            with mock.patch.object(build_app, 'game_running', lambda out: False), \
                    mock.patch.object(build_app, 'mac_shader_cache', lambda: cache), \
                    mock.patch.object(build_app, 'say') as said:
                build_app.merge_into_mac_cache(prep, Path(tmp)/'X.app')
            said.assert_called_once_with('shaders: 1 new prepared shaders for the Mac cache')

    def merge(self, prep, cache, tmp):
        with mock.patch.object(build_app, 'game_running', lambda out: False), \
                mock.patch.object(build_app, 'mac_shader_cache', lambda: cache), \
                mock.patch.object(build_app, 'say') as said:
            build_app.merge_into_mac_cache(prep, Path(tmp)/'X.app')
        return [c.args[0] for c in said.call_args_list]

    def test_unchanged_rebuild_skips_the_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)
            write(prep/'seed-manifest.json', '{"a": 1}')
            stamp = cache.with_name('cache.prep-merge.json')
            self.assertEqual(self.merge(prep, cache, tmp), ['shaders: 1 new prepared shaders for the Mac cache'])
            self.assertTrue(stamp.is_file())
            with mock.patch.object(build_app, 'copy_prepared_shaders', side_effect=AssertionError('merged again')):
                self.assertEqual(self.merge(prep, cache, tmp), ['shaders: the Mac cache already has the prepared shaders'])
            write(prep/'seed-manifest.json', '{"a": 2}')   # prepared again: merged again
            self.assertEqual(self.merge(prep, cache, tmp), ['shaders: 0 new prepared shaders for the Mac cache'])
            for p in cache.iterdir():   # the player deleted the cache: merged again into the new folder
                p.unlink()
            cache.rmdir()
            cache.mkdir()
            self.assertEqual(self.merge(prep, cache, tmp), ['shaders: 1 new prepared shaders for the Mac cache'])

    def test_merge_that_left_an_index_alone_is_not_stamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)
            write(prep/'seed-manifest.json', '{}')
            write(cache/f'{"c"*64}.variants', 'not json')   # unreadable: left alone, so tried again next build
            self.assertEqual(self.merge(prep, cache, tmp), ['shaders: 0 new prepared shaders for the Mac cache'])
            self.assertFalse(cache.with_name('cache.prep-merge.json').exists())

    def test_failed_copy_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)

            def broken(src, dst, *a, **k):
                Path(dst).write_text('partial')
                raise OSError(28, 'No space left on device')
            with mock.patch.object(build_app, 'clonefile', lambda src, dst: False), \
                    mock.patch.object(build_app.shutil, 'copy2', broken):
                with self.assertRaises(OSError):
                    build_app.copy_prepared_shaders(prep, cache)
            self.assertEqual([p.name for p in cache.iterdir()], [])

    def test_failed_index_write_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep, cache = Path(tmp)/'prep', Path(tmp)/'cache'
            self.prepared(prep)
            real = Path.write_text

            def broken(self, text, *a, **k):
                if self.name.endswith('.tmp'):
                    real(self, text[:3])
                    raise OSError(28, 'No space left on device')
                return real(self, text, *a, **k)
            with mock.patch.object(Path, 'write_text', broken):
                with self.assertRaises(OSError):
                    build_app.copy_prepared_shaders(prep, cache)
            self.assertEqual(sorted(p.name for p in cache.iterdir() if p.name.endswith('.tmp')), [])
            self.assertFalse((cache/f'{"c"*64}.variants').exists())


class ShaderList(unittest.TestCase):
    def test_offline_never_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            repo = write(work/'repo.tsv', LIST)

            def opener(*a, **k):
                raise AssertionError('downloaded while offline')
            with mock.patch.object(build_app, 'SHADER_LIST_REPO', repo):
                path, origin, rows = build_app.shader_list_for_build(work, offline=True, opener=opener)
            self.assertEqual((path, origin, rows), (repo, 'repo', 1))

    def test_downloaded_list_is_cached_in_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)

            class Reply:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self, n): return LIST.encode()
            path, origin, rows = build_app.shader_list_for_build(work, offline=False, opener=lambda *a, **k: Reply())
            self.assertEqual((origin, rows), ('downloaded', 1))
            self.assertEqual(path.parent, work/'build/shader-list')

    def test_missing_list_means_defaults_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with mock.patch.object(build_app, 'SHADER_LIST_REPO', work/'none.tsv'):
                path, origin, rows = build_app.shader_list_for_build(work, offline=True)
            self.assertEqual((path, origin, rows), (None, 'repo', 0))


class PrepFailure(unittest.TestCase):
    def test_failed_preparation_is_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, logs = Path(tmp), Path(tmp)/'logs'
            write(work/'repo.tsv', LIST)

            def fail(cmd, log, **k):
                write(log, 'found 3 translatable shaders\nERROR: only 3 shaders translated; at least 12000 are required\n')
                raise build_app.BuildError('prepare_shader_seed.py failed (exit 1)', log=log)
            with mock.patch.object(build_app, 'SHADER_LIST_REPO', work/'repo.tsv'), \
                    mock.patch.object(build_app, 'run', fail), mock.patch.object(build_app, 'VERSION', 'v200'), \
                    mock.patch.object(build_app, 'say') as said:
                self.assertIsNone(build_app.step_shader_prep(work, logs, Path(sys.executable), offline=True))
            text = '\n'.join(c.args[0] for c in said.call_args_list)
            self.assertIn('shaders: preparation failed (only 3 shaders translated; at least 12000 are required)', text)

    def test_reused_preparation_returns_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, logs = Path(tmp), Path(tmp)/'logs'
            prep = work/f'build/shader-prep/rt-{build_app.shader_chain()}'

            def ok(cmd, log, **k):
                prep.mkdir(parents=True)
                write(log, 'shader prep: found 12427, default 12427, listed 0 (matched 0, translated 0, other version 0), '
                           'reused\n')
            with mock.patch.object(build_app, 'SHADER_LIST_REPO', work/'none.tsv'), \
                    mock.patch.object(build_app, 'run', ok), mock.patch.object(build_app, 'VERSION', 'v200'), \
                    mock.patch.object(build_app, 'say'):
                self.assertEqual(build_app.step_shader_prep(work, logs, Path(sys.executable), offline=True), prep)


if __name__ == '__main__':
    unittest.main()
