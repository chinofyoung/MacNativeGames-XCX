#!/usr/bin/env python3
"""Add the shaders a player shared in a GitHub issue (the "Shader list" issue form) to games/xcx/assets/shader_list.tsv.

.github/workflows/shader-list.yml runs this on every issue titled "Shader list: ..." (or labelled shader-list). Anyone on
the internet can write such an issue, so its text is only ever data: the workflow hands this script the event file
GitHub wrote, and nothing from the issue reaches a shell. Rows come from text pasted into the issue and from files
attached to it (links under https://github.com/user-attachments/files/, at most 5 MB each); every other link is
ignored, and only valid shader rows (shader_list.parse_row, kind V) are kept.
Limits: the account is at least 7 days old (an event naming no account id is refused), one accepted issue per account
(by its numeric id) per UTC day; editing that issue again the same day does not count, on a later day it adds nothing.
At most 2,000 new rows per issue, counted across its edits. Each accepted issue gets one line in
games/xcx/assets/shader_list_log.tsv (user: <id>:<login>) and the rows it added in
games/xcx/assets/shader_list_issues/<issue>.tsv, so `--undo <issue>` can take them out again.
usage: process_shader_issue.py --event <GITHUB_EVENT_PATH> --author-created <ISO date> --out-comment <file>
       process_shader_issue.py --undo <issue>
"""
import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_list import ShaderList, parse_row

ASSETS = Path(__file__).resolve().parents[1]/'assets'
LIST = ASSETS/'shader_list.tsv'
LOG = ASSETS/'shader_list_log.tsv'
RECORDS = 'shader_list_issues'   # next to the log: <issue>.tsv, the rows that issue added
LOG_HEADER = '# issue\tuser\tdate_utc\trows_added\n'
GUIDE = 'https://github.com/chinofyoung/XC-Native/blob/main/docs/sharing-shaders.md'

MAX_ATTACHMENT = 5 * 1024 * 1024
FETCH_TIMEOUT = 15
MAX_ATTACHMENTS = 5        # a player attaches one file; more links than this are not read
MAX_NEW_ROWS = 2000
MIN_ACCOUNT_AGE = timedelta(days=7)
KINDS = ('V',)             # what a player's contribution file holds (totk_shader_contrib.h); other kinds are not taken
LINK = re.compile(r'https?://[^\s<>()\[\]"\'`]+')
ATTACHMENT_PATH = re.compile(r'^/user-attachments/files/\d+/[A-Za-z0-9._-]+$')
LOGIN = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$')   # GitHub's own rule; anything else is not written

Decision = namedtuple('Decision', 'accept rows reason')

COMMENTS = {
    'ok': 'Thanks! {n} new shaders from your file are now in the shared list. Everyone\'s next build will include them.',
    'daily_limit': 'Thanks! You already shared today. Please send this again tomorrow and it will be added. '
                   '(Editing this issue tomorrow is enough.)',
    'no_account': 'Thanks for wanting to help! This issue could not be matched to a GitHub account, so it cannot be '
                  'added. Please open a new issue while signed in to GitHub.',
    'new_account': 'Thanks for wanting to help! To keep the list free of spam, a GitHub account has to be at least 7 '
                   'days old to share shaders. Please send this again once your account is a week old.',
    'too_many': 'Thanks! This is more than 2,000 new shaders in one go. Please split it into smaller parts over several '
                'days.',
    'old_issue': 'Thanks! This issue was already added on {date}. To share more shaders, please open a new issue.',
    'nothing_new': 'Thanks! Every shader in your file is already in the shared list, so there was nothing to add.',
    'no_rows': 'Thanks! No shaders were found in this issue. Please drag shader-list-contribution.txt into the issue '
               f'(edit it, or open a new one). How to find the file: {GUIDE}',
}


def comment(reason, added, date=''):
    """The reply for a decision: plain words, nothing taken from the issue (date: the day an old issue was added)."""
    return COMMENTS[reason].format(n=added, date=date)


def _attachment(url):
    """Whether a link is a file attached to a GitHub issue: https, host exactly github.com, /user-attachments/files/."""
    try:
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.netloc == 'github.com' and not parts.query and not parts.fragment
                and ATTACHMENT_PATH.match(parts.path) is not None and '..' not in parts.path)
    except ValueError:
        return False


class _GitHubRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to https on github.com or a *.githubusercontent.com host (where GitHub keeps attached
    files); any other target fails the download."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            parts = urlsplit(newurl)
            host = parts.hostname or ''
            ok = (parts.scheme == 'https' and parts.port is None and parts.username is None
                  and (host == 'github.com' or host.endswith('.githubusercontent.com')))
        except ValueError:
            ok = False
        if not ok:
            if fp is not None:
                fp.close()
            raise urllib.error.URLError(f'redirect to a host that is not GitHub\'s: {newurl!r}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url):
    """An attachment's bytes, at most MAX_ATTACHMENT + 1 of them (more means it is too large). No token is sent."""
    opener = urllib.request.build_opener(_GitHubRedirects)
    with opener.open(urllib.request.Request(url), timeout=FETCH_TIMEOUT) as reply:
        return reply.read(MAX_ATTACHMENT + 1)


def _rows_of(text):
    out = []
    for line in text.removeprefix('﻿').split('\n'):
        row = parse_row(line.strip(' \r'))
        if row is not None and row[0] in KINDS:
            out.append(row)
    return out


def extract_rows(body, fetch=None):
    """The shader rows in an issue body, once each in order: pasted ones (code fences, CRLF and a BOM are fine), then
    those in attached files. A file that cannot be read, is over MAX_ATTACHMENT or is linked anywhere else is skipped."""
    fetch = fetch or _fetch
    body = body if isinstance(body, str) else ''
    rows = _rows_of(body)
    urls = []
    for url in LINK.findall(body):
        if _attachment(url) and url not in urls:
            urls.append(url)
    for url in urls[:MAX_ATTACHMENTS]:
        try:
            data = fetch(url)
        except Exception:   # network, HTTP error, timeout: that file adds nothing
            continue
        if isinstance(data, bytes) and len(data) <= MAX_ATTACHMENT:
            rows += _rows_of(data.decode('utf-8', errors='replace'))
    return list(dict.fromkeys(rows))


def _log_entries(log):
    """(issue, login, date_utc, rows_added) per line of the log; lines that are not in that form are passed over."""
    out = []
    path = Path(log)
    if not path.exists():
        return out
    for line in path.read_text(encoding='utf-8').split('\n'):
        fields = line.rstrip('\r').split('\t')
        if line.startswith('#') or len(fields) != 4 or not fields[0].isdigit():
            continue
        out.append((int(fields[0]), fields[1], fields[2], fields[3]))
    return out


def _user(issue):
    """The log's user column: '<numeric account id>:<login>'. The id is what the daily limit compares (a renamed account
    keeps it); '-' when the event has no id."""
    uid = (issue.get('user') or {}).get('id')
    return f'{uid}:{_login(issue)}' if type(uid) is int and uid > 0 else '-'


def _login(issue):
    login = (issue.get('user') or {}).get('login')
    return login if isinstance(login, str) and LOGIN.match(login) else '-'


def _record(log, number):
    return Path(log).parent/RECORDS/f'{int(number)}.tsv'


def decide(issue, author_created, now, log, shader_list, fetch=None):
    """Whether to take an issue's rows: a Decision with the rows the list does not have yet and the reason (see
    COMMENTS). The account and day checks come first, so a rejected issue's attachments are not even downloaded.
    An issue already in the log may add more rows only on the UTC day of its log line ('old_issue' after that); the
    daily limit compares the account id, so renaming the account does not reset it; an event without a numeric account
    id is not taken at all ('no_account')."""
    number, uid = int(issue['number']), _user(issue).split(':')[0]
    if uid == '-':
        return Decision(False, [], 'no_account')
    if now - author_created < MIN_ACCOUNT_AGE:
        return Decision(False, [], 'new_account')
    today = now.astimezone(timezone.utc).strftime('%Y-%m-%d')
    entries = _log_entries(log)
    if any(n == number and date[:10] != today for n, _, date, _ in entries):
        return Decision(False, [], 'old_issue')
    if any(n != number and user.split(':')[0] == uid and date[:10] == today
           for n, user, date, _ in entries):
        return Decision(False, [], 'daily_limit')
    rows = extract_rows(issue.get('body'), fetch)
    if not rows:
        return Decision(False, [], 'no_rows')
    listed = set(shader_list.rows())
    new = [r for r in rows if r not in listed]
    if not new:
        return Decision(False, [], 'nothing_new')
    already = len(ShaderList.load(_record(log, number)).rows()) if any(n == number for n, *_ in entries) else 0
    if len(new) + already > MAX_NEW_ROWS:
        return Decision(False, [], 'too_many')
    return Decision(True, new, 'ok')


def apply(decision, issue, list_path, log_path, now=None):
    """Add an accepted decision's rows to the list; how many were added. The rows are also recorded for --undo and the
    issue gets one log line, the first time only: running the same issue again adds just what is still missing."""
    if not decision.accept:
        return 0
    number = int(issue['number'])
    lst = ShaderList.load(list_path)
    added = lst.add(decision.rows)
    if not added:
        return 0
    lst.save(list_path)
    record_path = _record(log_path, number)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = ShaderList.load(record_path)
    record.add(added)
    record.save(record_path)
    if not any(n == number for n, *_ in _log_entries(log_path)):
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        log_path = Path(log_path)
        text = log_path.read_text(encoding='utf-8') if log_path.exists() else LOG_HEADER
        if not text.endswith('\n'):
            text += '\n'
        text += f'{number}\t{_user(issue)}\t{now.strftime("%Y-%m-%dT%H:%M:%SZ")}\t{len(added)}\n'
        _write(log_path, text)
    return len(added)


def undo(issue_number, list_path, log_path, rows_source):
    """Take out of the list the rows issue `issue_number` added (rows_source/<issue>.tsv), except those another issue's
    record also holds; how many were removed. The record is deleted; the log keeps its line, so that day stays used."""
    record_path = Path(rows_source)/f'{int(issue_number)}.tsv'
    if not record_path.exists():
        return 0
    others = set()
    for path in Path(rows_source).glob('*.tsv'):
        if path != record_path:
            others.update(ShaderList.load(path).rows())
    lst = ShaderList.load(list_path)
    gone = lst.remove([r for r in ShaderList.load(record_path).rows() if r not in others])
    if gone:
        lst.save(list_path)
    record_path.unlink()
    return gone


def _write(path, text):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _date(text):
    try:
        value = datetime.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        raise SystemExit(f'ERROR: --author-created is not a date: {text!r}')
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def main(argv, now=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--event', type=Path, help='the issues event JSON (GITHUB_EVENT_PATH)')
    ap.add_argument('--author-created', help="the issue author's account creation date (ISO 8601, from the GitHub API)")
    ap.add_argument('--out-comment', type=Path, help='where to write the reply to post on the issue')
    ap.add_argument('--undo', type=int, metavar='ISSUE', help='take out the rows this issue added')
    ap.add_argument('--list', type=Path, default=LIST, help=argparse.SUPPRESS)
    ap.add_argument('--log', type=Path, default=LOG, help=argparse.SUPPRESS)
    ap.add_argument('--github-output', type=Path, default=os.environ.get('GITHUB_OUTPUT') or None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.undo is not None:
        gone = undo(args.undo, args.list, args.log, args.log.parent/RECORDS)
        print(f'issue #{args.undo}: {gone} rows taken out of {args.list}')
        outputs = {'removed': gone}
    else:
        if not (args.event and args.author_created and args.out_comment):
            ap.error('give --event, --author-created and --out-comment (or --undo)')
        issue = json.loads(args.event.read_text(encoding='utf-8'))['issue']
        now = now or datetime.now(timezone.utc)
        decision = decide(issue, _date(args.author_created), now, args.log, ShaderList.load(args.list))
        added = apply(decision, issue, args.list, args.log, now=now)
        logged = next((date[:10] for n, _, date, _ in _log_entries(args.log) if n == int(issue['number'])), '')
        args.out_comment.write_text(comment(decision.reason, added, logged) + '\n', encoding='utf-8')
        close = ('none' if decision.reason == 'daily_limit' else
                 'completed' if decision.accept or decision.reason == 'old_issue' else 'not_planned')
        print(f'issue #{int(issue["number"])}: {decision.reason}, {added} rows added')
        outputs = {'reason': decision.reason, 'added': added, 'close': close, 'issue': int(issue['number']),
                   'login': _login(issue)}
    if args.github_output:
        with open(args.github_output, 'a', encoding='utf-8') as f:
            f.write(''.join(f'{k}={v}\n' for k, v in outputs.items()))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError, KeyError, TypeError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
