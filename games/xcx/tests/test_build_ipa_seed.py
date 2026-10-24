"""build_ipa.py's shader seed: reused only once it was compiled for the device (compile_seed.py)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'ios'/'tools'))
import build_ipa
import compile_seed

KEY = 'a'*64
VARIANT = f'{KEY}-0123456789ab'
FLAGS = ' '.join(compile_seed.METAL_FLAGS)


def cache_with_one_variant(folder):
    folder.mkdir(parents=True)
    (folder/f'{KEY}.variants').write_text(json.dumps([dict(variant=VARIANT, deps={})]))
    (folder/f'{VARIANT}_b.metal').write_text('kernel void f() {}\n')
    (folder/f'{VARIANT}_b.json').write_text(json.dumps(dict(chain=build_ipa.chain())))


def compiled(seed, flags):
    """What compile_seed.py leaves in the manifest."""
    manifest = json.loads((seed/'seed-manifest.json').read_text())
    manifest['ios_metal_flags'] = flags
    (seed/'seed-manifest.json').write_text(json.dumps(manifest))


class SeedReuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cache, self.seed, self.log = root/'cache', root/'ios-seed/shader-seed', root/'ios-seed.log'
        self.seed.parent.mkdir(parents=True)
        cache_with_one_variant(self.cache)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, toolchain=True):
        with mock.patch.object(compile_seed, 'metal_version', lambda sdk: 'metal 1' if toolchain else None):
            return build_ipa.collect_seed([self.cache], self.seed, self.log, 'iphoneos')

    def test_stamp_is_written_only_after_compiling(self):
        count, fresh, stamp = self.collect()
        self.assertEqual((count, fresh), (1, True))
        self.assertFalse(self.seed.with_name('shader-seed.json').exists())   # the compile has not run yet
        self.assertTrue(self.collect()[1])   # a compile that failed or never ran: made again
        count, fresh, stamp = self.collect()
        compiled(self.seed, FLAGS)
        build_ipa.record_seed(self.seed, stamp)
        self.assertEqual(self.collect()[:2], (1, False))

    def test_uncompiled_seed_is_not_reused_with_the_metal_toolchain(self):
        count, fresh, stamp = self.collect(toolchain=False)
        compiled(self.seed, 'none')   # compile_seed.py without the Metal toolchain
        build_ipa.record_seed(self.seed, stamp)
        self.assertFalse(self.collect(toolchain=False)[1])   # still no toolchain: the MSL-only seed is current
        self.assertTrue(self.collect(toolchain=True)[1])     # the toolchain is there now: compile it

    def test_other_metal_flags_are_not_reused(self):
        count, fresh, stamp = self.collect()
        compiled(self.seed, '-fmetal-math-mode=fast')
        build_ipa.record_seed(self.seed, stamp)
        self.assertTrue(self.collect()[1])


if __name__ == '__main__':
    unittest.main()
