"""Downloading the newest shared shader list: what is accepted, and what the build falls back to."""
import contextlib
import http.client
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import fetch_shader_list
from fetch_shader_list import LIST_URL, fetch_list
from shader_list import HEADER, ShaderList

DEPS = '{"attr":{},"fout":{},"img":{},"tex":{}}'


def row(c):
    return f'V\t{c*64}\t{DEPS}'


def listing(*chars, extra=''):
    return (HEADER + ''.join(row(c) + '\n' for c in chars) + extra).encode()


class Response(io.BytesIO):
    pass


class Opener:
    """Stands in for urllib.request.urlopen: serves data, or raises error, and remembers how it was called."""
    def __init__(self, data=b'', error=None):
        self.data, self.error, self.calls = data, error, []

    def __call__(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return Response(self.data)


class FetchList(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)/'cache'
        self.repo = Path(tmp.name)/'shader_list.tsv'
        self.repo.write_bytes(listing('1'))

    def cached(self):
        return self.cache/'shader_list.tsv'

    def test_download_ok_is_cached(self):
        opener = Opener(listing('a', 'b'))
        path, origin = fetch_list(self.cache, self.repo, opener=opener)
        self.assertEqual((path, origin), (self.cached(), 'downloaded'))
        self.assertEqual(len(ShaderList.load(path).rows()), 2)
        self.assertEqual(opener.calls[0][0], LIST_URL)
        self.assertEqual([p.name for p in self.cache.iterdir()], ['shader_list.tsv'])   # no temp file left

    def test_html_falls_back_to_cache(self):
        self.cache.mkdir()
        self.cached().write_bytes(listing('c'))
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(b'<!DOCTYPE html><html>Sign in to continue</html>'))
        self.assertEqual((path, origin), (self.cached(), 'cached'))
        self.assertEqual(self.cached().read_bytes(), listing('c'))

    def test_truncated_or_empty_falls_back(self):
        for data in (b'', b'# Xenoblade Chronicles X shad', b'\n\n', HEADER.encode() + b'V\tjunk\n',
                     HEADER.replace('v1', 'v9').encode()):
            with self.subTest(data=data):
                path, origin = fetch_list(self.cache, self.repo, opener=Opener(data))
                self.assertEqual((path, origin), (self.repo, 'repo'))
        self.assertFalse(self.cached().exists())

    def test_header_only_list_is_accepted_and_cached(self):
        # the owner emptied the list: players must get the empty list, not keep stale rows
        self.cache.mkdir()
        self.cached().write_bytes(listing('c'))
        for data in (HEADER.encode(), HEADER.rstrip('\n').encode(), b'\xef\xbb\xbf' + HEADER.encode()):
            with self.subTest(data=data):
                path, origin = fetch_list(self.cache, self.repo, opener=Opener(data))
                self.assertEqual((path, origin), (self.cached(), 'downloaded'))
                self.assertEqual(ShaderList.load(path).rows(), [])
        # offline later: the emptied cached list is still the one used, not the project's copy
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(error=OSError('offline')))
        self.assertEqual((path, origin), (self.cached(), 'cached'))

    def test_network_error_uses_repo_when_no_cache(self):
        for error in (OSError('no route'), TimeoutError(), ValueError('bad url')):
            with self.subTest(error=error):
                path, origin = fetch_list(self.cache, self.repo, opener=Opener(error=error))
                self.assertEqual((path, origin), (self.repo, 'repo'))

    def test_bad_rows_dropped_good_kept(self):
        data = listing('a', extra='V\tnothex\t{}\nX\tjunk\n' + row('b') + '\r\n\n# note\n')
        said = io.StringIO()
        with contextlib.redirect_stderr(said):
            path, origin = fetch_list(self.cache, self.repo, opener=Opener(data))
        self.assertEqual(origin, 'downloaded')
        self.assertEqual(sorted(r[1] for r in ShaderList.load(path).rows()), ['a'*64, 'b'*64])
        self.assertEqual(said.getvalue(), 'shader list: dropped 2 invalid rows from the download\n')

    def test_bom_is_tolerated(self):
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(b'\xef\xbb\xbf' + listing('a')))
        self.assertEqual(origin, 'downloaded')

    def test_over_the_size_limit_is_refused(self):
        original = fetch_shader_list.MAX_BYTES
        fetch_shader_list.MAX_BYTES = 100
        self.addCleanup(setattr, fetch_shader_list, 'MAX_BYTES', original)
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(listing('a', 'b')))
        self.assertEqual(origin, 'repo')

    def test_not_utf8_falls_back(self):
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(listing('a') + b'\xff\xfe'))
        self.assertEqual((path, origin), (self.repo, 'repo'))

    def test_unreadable_cache_falls_back_to_repo(self):
        self.cache.mkdir()
        self.cached().write_text('V\tgarbage\n')
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(error=OSError()))
        self.assertEqual((path, origin), (self.repo, 'repo'))

    def test_incomplete_read_falls_back(self):
        class Cut(Response):
            def read(self, n=-1):
                raise http.client.IncompleteRead(b'')
        opener = Opener()
        opener.__class__ = type('CutOpener', (Opener,), {'__call__': lambda self, url, timeout=None: Cut()})
        self.assertEqual(fetch_list(self.cache, self.repo, opener=opener), (self.repo, 'repo'))

    def test_bad_status_line_falls_back(self):
        path, origin = fetch_list(self.cache, self.repo, opener=Opener(error=http.client.BadStatusLine('')))
        self.assertEqual((path, origin), (self.repo, 'repo'))

    def test_unwritable_cache_says_so(self):
        self.cache.parent.joinpath('cache').write_text('a file, not a folder')
        stderr = io.StringIO()
        real, sys.stderr = sys.stderr, stderr
        try:
            path, origin = fetch_list(self.cache, self.repo, opener=Opener(listing('a')))
        finally:
            sys.stderr = real
        self.assertEqual((path, origin), (self.repo, 'repo'))
        self.assertIn('cache could not be written', stderr.getvalue())

    def test_timeout_is_passed(self):
        opener = Opener(listing('a'))
        fetch_list(self.cache, self.repo, timeout=3.5, opener=opener)
        self.assertEqual(opener.calls, [(LIST_URL, 3.5)])
        opener = Opener(listing('a'))
        fetch_list(self.cache, self.repo, opener=opener)
        self.assertEqual(opener.calls[0][1], 10)


if __name__ == '__main__':
    unittest.main()
