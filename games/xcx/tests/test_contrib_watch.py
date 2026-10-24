"""shader_contrib_watch.pull: the device's contribution rows merged into the Mac's file, with devicectl faked."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'ios/tools'))
import shader_contrib_watch as watch

DEPS = '{"attr":{},"fout":{},"img":{},"tex":{}}'


def line(c):
    return f'V\t{c*64}\t{DEPS}\n'


def fake_device(text, calls=None):
    """A devicectl that 'copies' `text` to the --destination."""
    def run(cmd):
        if calls is not None:
            calls.append(cmd)
        Path(cmd[cmd.index('--destination') + 1]).write_text(text, encoding='utf-8')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    return run


class Pull(unittest.TestCase):
    def test_pull_merges_valid_rows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)/'sub/contribution.txt'
            store.parent.mkdir()
            store.write_text('# mine\n' + line('a') + 'V\t' + 'b'*64, encoding='utf-8')   # last line cut off
            calls = []
            device = '# phone\n' + line('a') + line('c') + 'junk\n' + f'V\t{"d"*64}\t{{"tex": {{}}}}\n' + line('c') + line('e')
            n = watch.pull('DEV', 'org.x', store, run=fake_device(device, calls))
            self.assertEqual(n, 2)
            self.assertEqual(store.read_text(), '# mine\n' + line('a') + 'V\t' + 'b'*64 + '\n' + line('c') + line('e'))
            cmd = calls[0]
            self.assertEqual(cmd[cmd.index('--device') + 1], 'DEV')
            self.assertEqual(cmd[cmd.index('--domain-identifier') + 1], 'org.x')
            self.assertEqual(cmd[cmd.index('--source') + 1], 'Documents/shader-list-contribution.txt')
            self.assertEqual(watch.pull('DEV', 'org.x', store, run=fake_device(device)), 0)   # again: nothing new

    def test_pull_creates_the_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)/'a/b/contribution.txt'
            self.assertEqual(watch.pull('DEV', 'org.x', store, run=fake_device(line('a'))), 1)
            self.assertEqual(store.read_text(), line('a'))

    def test_pull_device_missing_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)/'contribution.txt'
            store.write_text(line('a'), encoding='utf-8')
            said = []

            def gone(cmd):
                return subprocess.CompletedProcess(cmd, 1, '', 'ERROR: device not found\nThe device is not connected.\n')

            def no_tool(cmd):
                raise FileNotFoundError('xcrun')
            self.assertEqual(watch.pull('DEV', 'org.x', store, run=gone, report=said.append), 0)
            self.assertEqual(watch.pull('DEV', 'org.x', store, run=no_tool, report=said.append), 0)
            self.assertEqual(len(said), 2)
            self.assertIn('iPhone DEV not connected', said[0])
            self.assertIn('device not found', said[0])   # the detail devicectl gave
            self.assertIn('iPhone DEV not connected', said[1])
            self.assertEqual(store.read_text(), line('a'))


if __name__ == '__main__':
    unittest.main()
