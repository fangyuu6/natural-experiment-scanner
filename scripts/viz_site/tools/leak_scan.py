#!/usr/bin/env python3
"""Scan a built site folder for credentials and personal data before it is published.

    python3 scripts/viz_site/tools/leak_scan.py <dir> [--key-file FILE] [--require-key-file] [--show-emails]

Findings (any one makes the exit code 1):
  api_key          api_key=<8 or more characters> (plain, percent-encoded, JSON- or HTML-escaped)
  exact_key        a govinfo key value taken from the local policy table, found anywhere in any file
  bearer           'Bearer ' followed by a token (8 or more characters with a digit)
  url_password     scheme://user:password@host, or a password=<value> query parameter
  email            an email address other than the public contact fangyu@usc.edu

Output is counts and file names only. The key values are read into memory from the aside copy of the policy table
(data/output/policy_db_20260415_483k.csv.WITH_KEY_ASIDE, or --key-file) and are never printed.
"""
import argparse
import mmap
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KEY_FILE = ROOT / 'data/output/policy_db_20260415_483k.csv.WITH_KEY_ASIDE'
PUBLIC_EMAIL = 'fangyu@usc.edu'

KEY_VALUE = rb'[A-Za-z0-9_\-]{8,}'
PATTERNS = {
    'api_key': re.compile(rb'api[_-]?key(?:=|%3d|&#0*61;|&#x0*3d;|\\u003d)' + KEY_VALUE, re.I),
    'bearer': re.compile(rb'\bBearer\s+(?=[A-Za-z0-9\-._~+/]*\d)[A-Za-z0-9\-._~+/]{8,}'),
    'url_password': re.compile(rb'\b[a-z][a-z0-9+.\-]*://[^/\s:@"\'<>]+:[^/\s@"\'<>]+@[^/\s"\'<>]+|[?&;]pass(?:word|wd)=[^&;\s"\'<>#]+', re.I),
    'email': re.compile(rb'(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}'),
}
# Image, font and style file names written like an address (logo@2x.png) are not mailboxes.
NOT_A_MAILBOX_SUFFIX = {b'png', b'jpg', b'jpeg', b'gif', b'webp', b'svg', b'avif', b'ico', b'css', b'js', b'json', b'woff', b'woff2', b'map',
                        b'pdf', b'html', b'htm', b'xml', b'txt', b'csv', b'doc', b'docx', b'xls', b'xlsx', b'asp', b'aspx', b'jsp', b'php'}
# Binary media: only the exact key is looked for.
BINARY_SUFFIX = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.eot', '.pdf', '.zip', '.gz',
                 '.mp4', '.webm', '.mov', '.parquet', '.xlsx', '.pptx', '.docx'}
ORDER = ['api_key', 'exact_key', 'bearer', 'url_password', 'email']


def load_keys(path):
    """The distinct api_key values written in a file (bytes), read in blocks. Never printed or returned to a caller that prints."""
    keys, tail = set(), b''
    pattern = re.compile(rb'api[_-]?key=(' + KEY_VALUE + rb')', re.I)
    block = 1 << 24
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(block)
            data = tail + chunk
            end = len(data) if not chunk else max(0, len(data) - 512)
            for match in pattern.finditer(data):
                if match.start() < end:  # one that starts later is found again with the next block
                    keys.add(match.group(1))
            if not chunk:
                break
            tail = data[end:]
    return keys


def _emails(data):
    """Mailboxes in `data` other than the public contact (a list of the matched bytes)."""
    found = []
    for match in PATTERNS['email'].finditer(data):
        text = match.group(0)
        if text.lower() == PUBLIC_EMAIL.encode():
            continue
        if match.start() and data[match.start() - 1:match.start()] == b'/':  # a file name inside a web address
            continue
        if text.rsplit(b'.', 1)[1].lower() in NOT_A_MAILBOX_SUFFIX:
            continue
        found.append(text)
    return found


def scan_file(path, keys=()):
    """{pattern: count} for one file (only patterns with a finding), plus the distinct mailboxes found."""
    result, mailboxes = {}, set()
    if path.stat().st_size == 0:
        return result, mailboxes
    with open(path, 'rb') as stream:
        view = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            exact = 0
            for key in keys:
                at = view.find(key)
                while at >= 0:
                    exact += 1
                    at = view.find(key, at + 1)
            if exact:
                result['exact_key'] = exact
            if path.suffix.lower() in BINARY_SUFFIX:
                return result, mailboxes
            for name in ('api_key', 'bearer', 'url_password'):
                count = len(PATTERNS[name].findall(view))
                if count:
                    result[name] = count
            found = _emails(view)
            if found:
                result['email'] = len(found)
                mailboxes.update(found)
        finally:
            view.close()
    return result, mailboxes


def scan_dir(root, keys=()):
    """({pattern: total}, {relative file: {pattern: count}}, files scanned, distinct mailboxes)."""
    root = Path(root)
    totals, per_file, scanned, mailboxes = {}, {}, 0, set()
    for folder, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                continue
            scanned += 1
            found, boxes = scan_file(path, keys)
            mailboxes |= boxes
            if found:
                per_file[str(path.relative_to(root))] = found
                for pattern, count in found.items():
                    totals[pattern] = totals.get(pattern, 0) + count
    return totals, per_file, scanned, mailboxes


def main(argv=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('directory')
    parser.add_argument('--key-file', default=str(DEFAULT_KEY_FILE), help='file that still holds the key(s); read into memory only')
    parser.add_argument('--require-key-file', action='store_true', help='exit 2 when the key file is missing instead of skipping the exact-key check')
    parser.add_argument('--show-emails', action='store_true', help='also print the distinct mailboxes found (they are not secrets)')
    args = parser.parse_args(argv)
    root = Path(args.directory)
    if not root.is_dir():
        print('not a directory: ' + args.directory, file=out)
        return 2
    keys, key_note = set(), ''
    key_path = Path(args.key_file)
    if key_path.is_file():
        keys = load_keys(key_path)
        key_note = f'exact-key check: {len(keys)} key value(s) loaded'
    elif args.require_key_file:
        print('key file missing: ' + str(key_path), file=out)
        return 2
    else:
        key_note = 'exact-key check SKIPPED (key file not found)'
    totals, per_file, scanned, mailboxes = scan_dir(root, keys)
    print(f'leak scan of {root}: {scanned} files; {key_note}', file=out)
    for pattern in ORDER:
        files = [name for name, found in per_file.items() if pattern in found]
        print(f'  {pattern:13s} {totals.get(pattern, 0):6d} hit(s) in {len(files)} file(s)', file=out)
        for name in files[:50]:
            print(f'      {name}  ({per_file[name][pattern]})', file=out)
        if len(files) > 50:
            print(f'      ... {len(files) - 50} more file(s)', file=out)
    if args.show_emails and mailboxes:
        print('  mailboxes: ' + ', '.join(sorted(m.decode('utf-8', 'replace') for m in mailboxes)), file=out)
    if totals:
        print('FAIL', file=out)
        return 1
    print('PASS' + ('' if keys else ' (exact-key check not run)'), file=out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
