"""The player's contribution file: the rows the shared list does not have yet, and the issue link to send them with."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(REPO))
import app_bundle
import build_app
import contribution
from contribution import issue_url, new_rows
from shader_list import HEADER

DEPS = '{"attr":{},"fout":{},"img":{},"tex":{}}'


def vrow(c):
    return ('V', c*64, DEPS)


def line(row):
    return '\t'.join(row) + '\n'


def write(folder, name, text):
    path = Path(folder)/name
    path.write_text(text, encoding='utf-8')
    return path


class NewRows(unittest.TestCase):
    def test_new_rows_excludes_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            listed = write(tmp, 'shader_list.tsv', HEADER + line(vrow('a')) + line(vrow('b')))
            mine = write(tmp, 'contribution.txt', '# a comment\n' + line(vrow('a')) + line(vrow('c')) + line(vrow('d')))
            self.assertEqual(new_rows(mine, listed), [vrow('c'), vrow('d')])

    def test_new_rows_ignores_junk_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            listed = write(tmp, 'shader_list.tsv', HEADER)
            mine = write(tmp, 'contribution.txt',
                         '# Shaders this game met\n\nnot a row\nV\tshort\t{}\n' + f'V\t{"e"*64}\t{{"tex": {{}}}}\n'
                         + line(vrow('c')) + line(vrow('c')) + 'V\t' + 'f'*64)   # the last line cut off mid-write
            self.assertEqual(new_rows(mine, listed), [vrow('c')])

    def test_new_rows_without_a_list_counts_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            mine = write(tmp, 'contribution.txt', line(vrow('a')) + line(vrow('b')))
            self.assertEqual(new_rows(mine, Path(tmp)/'missing.tsv'), [vrow('a'), vrow('b')])
            self.assertEqual(new_rows(mine, None), [vrow('a'), vrow('b')])

    def test_new_rows_without_a_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(new_rows(Path(tmp)/'missing.txt', None), [])


class IssueUrl(unittest.TestCase):
    def test_issue_url_encodes_fields(self):
        self.assertEqual(issue_url(123, '2.0.0', 'Mac'),
                         'https://github.com/chinofyoung/XC-Native/issues/new?template=shader-list.yml'
                         '&title=Shader%20list%3A%20123%20shaders&version=2.0.0&device=Mac')
        query = parse_qs(urlsplit(issue_url(7, '1.0.1', 'iPad & co')).query)
        self.assertEqual(query['device'], ['iPad & co'])
        self.assertEqual(query['title'], ['Shader list: 7 shaders'])
        self.assertEqual(query['version'], ['1.0.1'])


class MacFile(unittest.TestCase):
    def test_mac_file_follows_the_data_folder_in_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(app_bundle.Path, 'home', return_value=home):
                self.assertEqual(contribution.mac_file(), home/'Library/Application Support/XCNative/XCX/shader-list-contribution.txt')
                (home/'Library/Application Support/MacNativeGames/XCX').mkdir(parents=True)   # only the old one
                self.assertEqual(contribution.mac_file(),
                                 home/'Library/Application Support/MacNativeGames/XCX/shader-list-contribution.txt')
                (home/'Library/Application Support/XCNative/XCX').mkdir(parents=True)          # moved: resolved again
                self.assertEqual(contribution.mac_file(), home/'Library/Application Support/XCNative/XCX/shader-list-contribution.txt')


class BuildReport(unittest.TestCase):
    """build_app.py after a build: the window's contribution event, with the rows the list the build cached lacks."""
    def events(self, work, mac_file, *device):
        out = io.StringIO()
        with mock.patch.object(contribution, 'mac_file', lambda: mac_file), mock.patch.object(build_app, 'EVENTS', out), \
                mock.patch.object(build_app, 'DISPLAY', '2.0.0'), mock.patch.object(build_app, 'say'):
            build_app.report_contribution(work, *device)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def test_build_reports_rows_the_cached_list_lacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)/'work'
            (work/'build/shader-list').mkdir(parents=True)
            write(work/'build/shader-list', 'shader_list.tsv', HEADER + line(vrow('a')))
            mine = write(tmp, 'contribution.txt', line(vrow('a')) + line(vrow('b')) + line(vrow('c')))
            [event] = self.events(work, mine)
            self.assertEqual((event['event'], event['rows'], event['file']), ('contribution', 2, str(mine)))
            self.assertEqual(event['url'], issue_url(2, '2.0.0', 'Mac'))
            # an iOS build: iPhone when the rows came from the phone, else the player picks the device
            [event] = self.events(work, mine, 'iPhone')
            self.assertEqual(parse_qs(urlsplit(event['url']).query)['device'], ['iPhone'])
            [event] = self.events(work, mine, None)
            self.assertNotIn('device', parse_qs(urlsplit(event['url']).query))
            self.assertEqual(event['url'], issue_url(2, '2.0.0'))

    def test_build_reports_nothing_without_new_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)/'work'
            (work/'build/shader-list').mkdir(parents=True)
            write(work/'build/shader-list', 'shader_list.tsv', HEADER + line(vrow('a')))
            self.assertEqual(self.events(work, write(tmp, 'contribution.txt', line(vrow('a')))), [])
            self.assertEqual(self.events(work, Path(tmp)/'missing.txt'), [])


if __name__ == '__main__':
    unittest.main()
