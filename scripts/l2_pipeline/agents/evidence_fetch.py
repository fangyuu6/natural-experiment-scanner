"""NAMED_PANEL Q3 (2026-10-16): official-page fetching and quote verification inside the queue module.

Ruling: docs/plans/l2-pipeline/HUB_RULING_20261016_NAMED_PANEL.md, Q3. A history worker has no shell
beyond the queue commands, so the queue module fetches for it:

* ``fetch(...)``: GET one http/https page for a claimed job (stdlib only, normal user agent, timeout,
  size cap, no credentials, no cookies, no form posts, no private addresses). When the live page fails,
  the Wayback Machine snapshot is fetched instead. The bytes are saved in the job's own output directory
  (``<output_dir>/evidence/<sha256>.<ext>``); URL, final URL, time, live/Wayback and SHA256 are recorded
  in ``EVIDENCE_LOG.json`` inside the queue packet, which a worker cannot write.
* ``verify_quote(...)``: the quoted text must appear verbatim (whitespace, quotation marks and dashes
  normalised) in the saved bytes. PDFs use pypdf, then pdfminer.six, then pypdfium2, then pdftotext (the
  libraries live in the user site; nothing is installed). A PDF that opens but has no text layer (a scan) is
  ``no_text_layer``; a PDF no reader can open is ``unavailable``.
* ``evidence_text(...)``: the same extraction, returned to the worker page by page, so a worker that has
  no PDF tool can read a fetched statute and copy an exact quote.
* ``apply_evidence_rule(job, value)``: for a packet claimed under this rule (``EVIDENCE_RULE.json`` in
  the packet) a known history row stays known only with a fetched-and-hashed official source whose
  quote verifies, or, for a citation-only official source, a second independent fetched-and-hashed
  source with a verified quote that agrees on the year. Every other known row becomes ``unknown`` with
  the original retained. Packets claimed before this window carry no rule file and are unchanged.
"""
from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import random
import re
import socket
import time
import unicodedata
import uuid
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

RULE = 'NAMED_PANEL_Q3'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) '
              'Version/17.0 Safari/605.1.15')
TIMEOUT = 30
SIZE_CAP = 25 * 1024 * 1024
MAX_FETCHES_PER_PACKET = 600
MIN_QUOTE_CHARS = 25
WAYBACK_API = 'https://archive.org/wayback/available?url='
ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
KNOWN_STATUSES = {'treated', 'not_yet_treated', 'never_treated'}


class FetchError(ValueError):
    retry_after = None   # seconds a server asked us to wait (Retry-After header on HTTP 429/503), when it said


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# --- URL rules -------------------------------------------------------------------------------

def check_url(url):
    """http/https only, no credentials, ordinary port, no literal private address."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2048 or any(c.isspace() for c in url.strip()):
        raise FetchError('URL missing or malformed')
    parts = urlsplit(url.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise FetchError('Only http/https URLs are fetched')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise FetchError('URLs with credentials are refused')
    try:
        port = parts.port
    except ValueError as exc:
        raise FetchError('Invalid URL port') from exc
    if port not in ALLOWED_PORTS:
        raise FetchError('URL port not allowed')
    host = parts.hostname.lower()
    if host in ('localhost',) or host.endswith(('.localhost', '.local', '.internal')):
        raise FetchError('Local hosts are refused')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise FetchError('Non-public addresses are refused')
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))


def _public_host(host):
    """DNS check at fetch time; every resolved address must be public."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError('Host does not resolve: ' + str(exc)) from exc
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise FetchError('Host resolves to a non-public address')


def canonical_url(url):
    """Identity of a document for independence tests: scheme-less, www-less, no fragment, no trailing slash."""
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host + (parts.path.rstrip('/') or '') + ('?' + parts.query if parts.query else '')


# --- network ---------------------------------------------------------------------------------

def _urllib_get(url, timeout, cap, agent):
    """The stdlib GET (redirects checked hop by hop). Raises FetchError."""
    import urllib.error
    import urllib.request

    class _Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check_url(newurl)
            _public_host(urlsplit(newurl).hostname)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    _public_host(urlsplit(url).hostname)
    opener = urllib.request.build_opener(_Redirect)  # no cookie jar, no auth handler
    request = urllib.request.Request(url, method='GET', headers={
        'User-Agent': agent, 'Accept': 'text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5',
        'Accept-Language': 'en-US,en;q=0.9'})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(cap + 1)
            if len(data) > cap:
                raise FetchError('Response exceeds the size cap')
            return dict(status=response.status, final_url=response.geturl(),
                        content_type=(response.headers.get_content_type() or ''),
                        charset=response.headers.get_content_charset() or '', data=data)
    except urllib.error.HTTPError as exc:
        error = FetchError('HTTP ' + str(exc.code))
        try:
            error.retry_after = float(exc.headers.get('Retry-After')) if exc.headers and exc.headers.get('Retry-After') else None
        except (TypeError, ValueError):
            error.retry_after = None   # a date form or garbage: ignored, the fixed back-off applies
        raise error from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(type(exc).__name__ + ': ' + str(getattr(exc, 'reason', exc))) from exc


def _is_tls_version_error(exc):
    text = str(exc).upper()
    return 'TLSV1_ALERT_PROTOCOL_VERSION' in text or 'PROTOCOL VERSION' in text or 'UNSUPPORTED PROTOCOL' in text


def _curl_get(url, timeout, cap, agent, hops=5):
    """T191: the system Python links LibreSSL 2.8 (TLS 1.2 at most); a host that insists on TLS 1.3 (www.scstatehouse.gov) answers
    ``tlsv1 alert protocol version``. The system curl speaks TLS 1.3. Same rules as the stdlib path: check_url and a public-address check
    on every hop, size cap, no cookies, no credentials, no shell (argument list)."""
    import shutil
    import subprocess
    import tempfile
    program = shutil.which('curl')
    if not program:
        raise FetchError('curl not installed')
    for _ in range(hops + 1):
        url = check_url(url)
        _public_host(urlsplit(url).hostname)
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / 'body'
            command = [program, '-sS', '-g', '--proto', '=http,https', '-m', str(int(timeout)), '--max-filesize', str(cap),
                       '-A', agent, '-H', 'Accept: text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5',
                       '-H', 'Accept-Language: en-US,en;q=0.9', '-o', str(body),
                       '-w', '%{http_code}\n%{content_type}\n%{redirect_url}', url]
            try:
                done = subprocess.run(command, capture_output=True, timeout=timeout + 10, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                raise FetchError(type(exc).__name__ + ': ' + str(exc)) from exc
            if done.returncode != 0:
                reason = done.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or ['curl exit ' + str(done.returncode)]
                if done.returncode == 28:
                    raise FetchError('URLError: timed out (curl)')
                if done.returncode == 63:
                    raise FetchError('Response exceeds the size cap')
                raise FetchError('URLError: ' + reason[0][:200])
            code, ctype, redirect = (done.stdout.decode('utf-8', 'replace').split('\n') + ['', '', ''])[:3]
            data = body.read_bytes() if body.exists() else b''
        status = int(code or 0)
        if status in (301, 302, 303, 307, 308) and redirect:
            url = redirect
            continue
        if status >= 400 or status == 0:
            raise FetchError('HTTP ' + str(status))
        if len(data) > cap:
            raise FetchError('Response exceeds the size cap')
        match = re.search(r'charset=([\w.-]+)', ctype or '', re.I)
        return dict(status=status, final_url=url, content_type=(ctype or '').split(';')[0].strip().lower(),
                    charset=match.group(1) if match else '', data=data)
    raise FetchError('Too many redirects')


def http_get(url, timeout=TIMEOUT, cap=SIZE_CAP):
    """One GET. Returns dict(status, final_url, content_type, charset, data). Tests replace this function.

    T191 additions: a TLS-version failure is retried through the system curl (``_curl_get``); it falls back to the original error when curl fails too. HTTP 403 is not retried (hub ruling: no agent switching)."""
    url = check_url(url)
    try:
        return _urllib_get(url, timeout, cap, USER_AGENT)
    except FetchError as exc:
        first = exc
    if _is_tls_version_error(first):
        try:
            return _curl_get(url, timeout, cap, USER_AGENT)
        except FetchError as again:
            if str(again) == 'HTTP 403':
                first = again
            else:
                raise FetchError(str(first) + ' | curl: ' + str(again)) from again
    raise first


_REAL_HTTP_GET = http_get   # tests replace ``http_get``; pacing applies to the real one only

PAUSE = 6  # seconds before the single retry after HTTP 429/503 (any host but archive.org)
WAYBACK_TRIES = 3          # worker-fix G3: archive.org answers HTTP 429 when several workers ask at once (230 of 450 failed fetches, 2026-10-18)
WAYBACK_BACKOFF = (15, 30)  # seconds before the 2nd and 3rd try on archive.org, plus up to JITTER seconds
JITTER = 5
HOST_GAP = 1.0             # seconds between two requests to one host, over all queue workers on this machine
ARCHIVE_GAP = 5.0          # the same for archive.org and web.archive.org
RETRY_AFTER_CAP = 60       # a server's Retry-After is honoured up to this many seconds


def is_archive(url):
    host = (urlsplit(str(url)).hostname or '').lower()
    return host == 'archive.org' or host.endswith('.archive.org')


def pace(pace_dir, url):
    """Wait until this host has been quiet for its gap. One small lock file per host in ``pace_dir`` (the queue's PACE directory),
    so the workers running in separate processes share the pace. A problem with the directory never fails a fetch."""
    host = (urlsplit(str(url)).hostname or '').lower()
    gap = ARCHIVE_GAP if is_archive(url) else HOST_GAP
    try:
        Path(pace_dir).mkdir(parents=True, exist_ok=True)
        with (Path(pace_dir) / re.sub(r'[^a-z0-9.-]', '_', host)).open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                last = float(handle.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = min(last + gap - time.time(), gap)   # never longer than the gap, even if a clock moved
            if wait > 0:
                time.sleep(wait)
            handle.seek(0)
            handle.truncate()
            handle.write('%.3f' % time.time())
            handle.flush()
    except OSError:
        pass


def polite(get, pace_dir=None):
    """Back-off after a rate limit or a temporary server error. Any host: one retry after PAUSE. archive.org: WAYBACK_TRIES tries
    with 15 s, then 30 s (plus jitter) between them, longer if the server sent Retry-After. With ``pace_dir`` and the real network
    function, requests are also spaced per host (``pace``); tests replace the network function and are not slowed."""
    def call(url, *args, **kwargs):
        archive = is_archive(url)
        tries = WAYBACK_TRIES if archive else 2
        waits = list(WAYBACK_BACKOFF) if archive else [PAUSE]
        for attempt in range(tries):
            if pace_dir is not None and get is _REAL_HTTP_GET:
                pace(pace_dir, url)
            try:
                return get(url, *args, **kwargs)
            except FetchError as exc:
                if str(exc) not in ('HTTP 429', 'HTTP 503') or attempt == tries - 1:
                    raise
                delay = waits[min(attempt, len(waits) - 1)]
                if exc.retry_after:
                    delay = max(delay, min(float(exc.retry_after), RETRY_AFTER_CAP))
                time.sleep(delay + (random.uniform(0, JITTER) if archive else 0))
    return call


WAYBACK_RAW = 'https://web.archive.org/web/2id_/'   # T191: redirects to the snapshot nearest in time, no availability API


def _direct_snapshot(url, get):
    result = dict(get(WAYBACK_RAW + url))
    final = str(result.get('final_url') or '')
    found = re.search(r'/web/(\d{14})id_/', final)
    if not found:
        raise FetchError('Wayback answered without a snapshot address')
    result.update(snapshot_timestamp=found.group(1), snapshot_url=final)
    return result


def wayback_snapshot(url, get=None):
    """Closest Wayback snapshot (raw bytes, id_ form). Returns fetch result plus snapshot fields.

    T191: ask web.archive.org/web/2id_/<url> first (HTTP 429 on 202 of 230 failed fetches came from the archive.org availability API, which
    rate-limits per address); HTTP 404 there means no snapshot. Any other failure falls back to the availability API as before."""
    get = get or http_get
    try:
        return _direct_snapshot(url, get)
    except FetchError as exc:
        if str(exc) == 'HTTP 404':
            raise FetchError('No Wayback snapshot listed') from exc
        if str(exc) == 'HTTP 429':   # the archive is rate-limiting this address; a second archive endpoint only doubles the wait
            raise FetchError('Wayback rate-limited (HTTP 429)') from exc
        direct_error = exc
    try:
        answer = get(WAYBACK_API + quote(url, safe=''))
    except FetchError as exc:
        raise FetchError('direct: ' + str(direct_error) + '; availability API: ' + str(exc)) from exc
    try:
        closest = json.loads(answer['data'].decode('utf-8', 'replace'))['archived_snapshots']['closest']
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError('No Wayback snapshot listed') from exc
    if not isinstance(closest, dict) or closest.get('available') is not True or str(closest.get('status', '200')) != '200':
        raise FetchError('No usable Wayback snapshot')
    stamp = str(closest.get('timestamp', ''))
    if not re.fullmatch(r'\d{14}', stamp):
        raise FetchError('Wayback snapshot timestamp missing')
    raw_url = 'https://web.archive.org/web/' + stamp + 'id_/' + url
    result = dict(get(raw_url))
    result.update(snapshot_timestamp=stamp, snapshot_url=raw_url)
    return result


# --- text and quotes -------------------------------------------------------------------------

class _Visible(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'template', 'head'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ('br', 'p', 'div', 'li', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


_QUOTES = str.maketrans({'‘': "'", '’': "'", '‚': "'", '‛': "'", '“': '"', '”': '"',
                         '„': '"', '–': '-', '—': '-', '−': '-', '‐': '-', '‑': '-',
                         '­': '', '​': '', '‌': '', '‍': '', '⁠': '', '﻿': '',
                         ' ': ' '})


def normalize_text(text):
    text = unicodedata.normalize('NFKC', text).translate(_QUOTES)
    return re.sub(r'\s+', ' ', text).strip()


SITE = Path.home() / 'Library/Python/3.9/lib/python/site-packages'
TEXT_PAGE_CHARS = 20000     # pseudo-page size for HTML and plain text
MAX_TEXT_CHARS = 30000      # most characters one evidence-text call returns
MAX_PAGE_LIST = 400         # longest per-page character list in a reply


def _site():
    """Public-data parsers installed for the user (runbook item 10); nothing is installed and nothing outside it is read."""
    import sys
    if SITE.is_dir() and str(SITE) not in sys.path:
        sys.path.append(str(SITE))


def _pypdf_pages(raw):
    import io
    import logging
    logging.getLogger('pypdf').setLevel(logging.CRITICAL)   # a damaged file is reported by the status, not by library warnings
    from pypdf import PdfReader
    return [(page.extract_text() or '') for page in PdfReader(io.BytesIO(raw)).pages]


def _pdfminer_pages(raw):
    import io
    from pdfminer.high_level import extract_text as mine
    from pdfminer.pdfpage import PDFPage
    count = sum(1 for _ in PDFPage.get_pages(io.BytesIO(raw)))
    return [mine(io.BytesIO(raw), page_numbers=[i]) or '' for i in range(count)]


def _pdfium_pages(raw):
    import pypdfium2
    document = pypdfium2.PdfDocument(raw)
    try:
        pages = []
        for i in range(len(document)):
            textpage = document[i].get_textpage()
            pages.append(textpage.get_text_range() or '')
        return pages
    finally:
        document.close()


def _pdftotext_pages(raw):
    import shutil
    import subprocess
    program = shutil.which('pdftotext')
    if not program:
        raise ImportError('pdftotext not installed')
    result = subprocess.run([program, '-', '-'], input=raw, capture_output=True, timeout=60, check=False)
    if result.returncode != 0:
        raise ValueError('pdftotext failed')
    return result.stdout.decode('utf-8', 'replace').split('\f')


PDF_READERS = (('pypdf', _pypdf_pages), ('pdfminer', _pdfminer_pages), ('pypdfium2', _pdfium_pages), ('pdftotext', _pdftotext_pages))


def _split(text, size=TEXT_PAGE_CHARS):
    """Pseudo-pages of at most ``size`` characters, cut at whitespace."""
    text = text.strip()
    if not text:
        return ['']
    pages = []
    while len(text) > size:
        cut = text.rfind(' ', size // 2, size)
        cut = cut if cut > 0 else size
        pages.append(text[:cut].strip())
        text = text[cut:].strip()
    pages.append(text)
    return pages


def document_pages(raw, content_type='', charset=''):
    """dict(status, method, pages): ``ok`` (text found), ``no_text_layer`` (opened, no text: a scan) or ``unavailable`` (unreadable).

    The one extraction behind ``extract_text``, ``quote_check`` and the ``evidence-text`` command."""
    if raw[:5] == b'%PDF-' or 'pdf' in (content_type or '').lower():
        _site()
        opened = None
        for name, reader in PDF_READERS:
            try:
                pages = reader(raw)
            except Exception:  # noqa: BLE001 - a missing library or an unreadable file: try the next reader
                continue
            if any(p.strip() for p in pages):
                return dict(status='ok', method=name, pages=pages, text='\n'.join(pages))
            opened = opened or (name, pages)
        if opened:
            return dict(status='no_text_layer', method=opened[0] + ': no text layer', pages=opened[1], text='')
        return dict(status='unavailable', method='PDF text extraction unavailable', pages=[], text='')
    encoding = charset or 'utf-8'
    try:
        text = raw.decode(encoding, 'replace')
    except LookupError:
        text = raw.decode('utf-8', 'replace')
    head = text[:3000].lower()
    if 'html' in (content_type or '').lower() or '<html' in head or '<!doctype html' in head:
        parser = _Visible()
        try:
            parser.feed(text)
            parser.close()
        except Exception:  # noqa: BLE001 - malformed HTML still yields what parsed
            pass
        text = ' '.join(parser.parts)
        return dict(status='ok', method='html visible text', pages=_split(text), text=text)
    return dict(status='ok', method='plain text', pages=_split(text), text=text)


def extract_text(raw, content_type='', charset=''):
    """(text or None, method). None means the bytes cannot be read as text with what is installed."""
    doc = document_pages(raw, content_type, charset)
    if doc['status'] != 'ok':
        return None, doc['method']
    return doc['text'], doc['method']


def quote_check(raw, quote_text, content_type='', charset=''):
    """'verified' | 'not_found' | 'no_text_layer' | 'unavailable' | 'too_short'."""
    needle = normalize_text(quote_text or '')
    if len(needle) < MIN_QUOTE_CHARS:
        return 'too_short'
    doc = document_pages(raw, content_type, charset)
    if doc['status'] != 'ok':
        return doc['status']
    haystack = normalize_text(doc['text'])
    if needle in haystack:
        return 'verified'
    # PDF/line-break hyphenation: "adminis- tration" in the text, "administration" in the quote.
    if needle in re.sub(r'(?<=\w)- (?=\w)', '', haystack):
        return 'verified'
    return 'not_found'


# --- queue packet files ----------------------------------------------------------------------

def _read(path):
    return json.loads(Path(path).read_text())


def _atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, path)


def packet_files(packet):
    return Path(packet) / 'EVIDENCE_RULE.json', Path(packet) / 'EVIDENCE_LOG.json'


def mark_claim(packet, worker, now):
    """Called by claim() under the queue lock for a history packet: the packet's submission is checked by this rule."""
    rule, _ = packet_files(packet)
    if not rule.exists():
        _atomic(rule, dict(rule=RULE, worker=worker, set_at=now,
                           note='claimed after the NAMED_PANEL window; known history rows need fetched-and-hashed evidence'))


def history_binding(binding_path):
    """(job, binding) when the packet carries a network-enabled V9 official-history A/B job, else (None, None)."""
    from .jobs import load_job
    from .history_packet import is_history
    binding = _read(binding_path)
    job = load_job(Path(binding['job_path']))
    if is_history(job) and job.contract.get('evidence_standard') == 'HUB_RULING_V9_HISTORY_EVIDENCE' and binding.get('network_access'):
        return job, binding
    return None, None


def _entries(packet):
    _, log = packet_files(packet)
    return _read(log).get('entries', []) if log.exists() else []


def _archive_dir(job):
    return Path(job.output_dir) / 'evidence'


def _extension(raw, content_type):
    if raw[:5] == b'%PDF-' or 'pdf' in (content_type or '').lower():
        return '.pdf'
    if 'html' in (content_type or '').lower() or raw[:2000].lower().find(b'<html') >= 0:
        return '.html'
    return '.txt'


def record_fetch(packet, job, url, result, via, live_error=None, now=None):
    """Save the bytes in the job's evidence archive and append the log entry. Caller holds the queue lock."""
    now = time.time() if now is None else now
    raw = result['data']
    digest = sha256_bytes(raw)
    archive = _archive_dir(job)
    archive.mkdir(parents=True, exist_ok=True)
    name = digest + _extension(raw, result.get('content_type', ''))
    target = archive / name
    if target.exists():
        if sha256_file(target) != digest:
            raise FetchError('Evidence archive file differs from its hash name')
    else:
        tmp = archive / ('.' + name + '.' + uuid.uuid4().hex + '.tmp')
        tmp.write_bytes(raw)
        os.replace(tmp, target)
        target.chmod(0o444)
    entry = dict(url=url, final_url=result.get('final_url') or url, fetched_at=now,
                 fetched_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)), via=via,
                 http_status=result.get('status'), content_type=result.get('content_type', ''),
                 charset=result.get('charset', ''), size_bytes=len(raw), sha256=digest,
                 archive_path='evidence/' + name, archive_abs_path=str(target))
    if via == 'wayback':
        entry.update(snapshot_timestamp=result.get('snapshot_timestamp'), snapshot_url=result.get('snapshot_url'),
                     live_error=live_error)
    _, log = packet_files(packet)
    value = _read(log) if log.exists() else dict(schema='l2_evidence_fetch_log_v1', rule=RULE, entries=[])
    value['entries'].append(entry)
    _atomic(log, value)
    return entry


def staged_entry(packet, url):
    """The hub-imported browser file for this URL (via == 'browser', see agents/browser_evidence.py), or None."""
    wanted = canonical_url(url)
    return next((e for e in _entries(packet) if e.get('via') == 'browser'
                 and wanted in {canonical_url(e['url']), canonical_url(e.get('final_url') or e['url'])}), None)


def fetch_for_packet(packet, job, url, get=None, now=None):
    """Network part and bookkeeping for one URL (no queue lock held while waiting on the network)."""
    get = polite(get or http_get, pace_dir=Path(packet).parents[1] / 'PACE')
    url = check_url(url)
    live_error = None
    try:
        result = get(url)
        if result.get('status') != 200 or not result.get('data'):
            raise FetchError('HTTP ' + str(result.get('status')))
        return dict(result=result, via='live', url=url, live_error=None)
    except FetchError as exc:
        live_error = str(exc)
    try:
        result = wayback_snapshot(url, get)
        if not result.get('data'):
            raise FetchError('Empty Wayback snapshot')
    except FetchError as exc:
        raise FetchError('Live page failed (' + live_error + ') and no Wayback snapshot: ' + str(exc)) from exc
    return dict(result=result, via='wayback', url=url, live_error=live_error)


def verify_entry(job, entry):
    """Re-hash the archived bytes; return them or raise."""
    path = Path(job.output_dir) / entry['archive_path']
    resolved = path.resolve()
    if not resolved.is_relative_to((Path(job.output_dir) / 'evidence').resolve()) or not path.is_file():
        raise FetchError('Archived bytes missing')
    if sha256_file(path) != entry['sha256']:
        raise FetchError('Archived bytes changed after fetch')
    return path.read_bytes()


# --- the validator ---------------------------------------------------------------------------

def _packet_for(job):
    from .sonnet_queue import root_for, identity, locate
    try:
        return locate(root_for(job.repo), identity(job.job_path))
    except (OSError, ValueError):
        return None


def strict_packet(job, packet=None):
    packet = packet if packet is not None else _packet_for(job)
    if packet is None or not packet_files(packet)[0].exists():
        return None
    return packet


def _source_status(source, by_sha, job, cache):
    sha = source.get('sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
        return 'citation_only', None, 'no SHA256'
    entry = by_sha.get(sha)
    if entry is None:
        return 'citation_only', None, 'hash is not in the fetch log'
    shown = {canonical_url(entry['url']), canonical_url(entry.get('final_url', entry['url']))}
    if entry.get('snapshot_url'):
        shown.add(canonical_url(entry['snapshot_url']))
    if canonical_url(source.get('url', '')) not in shown and canonical_url(source.get('final_url', '') or '-') not in shown:
        return 'citation_only', None, 'source URL differs from the fetched URL'
    declared = source.get('archive_path')
    if declared:
        out = Path(job.output_dir).resolve()
        target = (Path(declared) if Path(declared).is_absolute() else out / declared).resolve()
        if target != (out / entry['archive_path']).resolve():
            return 'citation_only', None, 'archive path differs from the fetched file'
    key = ('raw', sha)
    if key not in cache:
        try:
            cache[key] = verify_entry(job, entry)
        except FetchError as exc:
            cache[key] = str(exc)
    if isinstance(cache[key], str):
        return 'citation_only', None, cache[key]
    return 'fetched', entry, None


def _ref_verified(ref, source_state, cache):
    status, entry, _ = source_state
    if status != 'fetched':
        return 'no_bytes'
    key = ('quote', entry['sha256'], ref.get('quote'))
    if key not in cache:
        cache[key] = quote_check(cache[('raw', entry['sha256'])], ref.get('quote', ''), entry.get('content_type', ''),
                                 entry.get('charset', ''))
    return cache[key]


def evaluate_rows(job, value, entries):
    """Per-row decision under the rule. Pure: no writes."""
    sources = {s.get('source_id'): s for s in value.get('sources', []) if isinstance(s, dict)}
    by_sha = {e['sha256']: e for e in entries}
    cache, states, decisions = {}, {}, []
    for sid, source in sources.items():
        states[sid] = _source_status(source, by_sha, job, cache)
    for row in value.get('records', []):
        if row.get('status') not in KNOWN_STATUSES:
            continue
        refs = [r for r in row.get('evidence_refs', []) if isinstance(r, dict) and r.get('source_id') in sources
                and str(r.get('locator', '')).strip() and str(r.get('quote', '')).strip()]
        official = [r for r in refs if sources[r['source_id']].get('source_type') == 'official']
        absent = row['status'] == 'never_treated'
        support = 'absence_code_history' if absent else 'start_year'
        year = None if absent else row.get('best_evidence_start_year')
        wanted = [r for r in official if r.get('supports') == support and (absent or r.get('year') == year)]
        verified = [r for r in wanted if _ref_verified(r, states[r['source_id']], cache) == 'verified']
        decision = dict(unit=row.get('unit'), status=row['status'], known=False, basis=None, flags=[], detail='')
        if verified:
            decision.update(known=True, basis='fetched_official_source_quote_verified')
        elif absent:
            decision['detail'] = 'absence needs a fetched official source whose quote verifies'
        elif wanted:
            # Official citation-only (or unverifiable) start source: needs a second, independent, fetched, verified source.
            first_docs = {canonical_url(sources[r['source_id']].get('url', '')) for r in wanted}
            second = []
            for r in refs:
                if r in wanted or r.get('year') != year or r.get('supports') not in ('start_year', None):
                    continue
                state = states[r['source_id']]
                if state[0] == 'fetched' and _ref_verified(r, state, cache) == 'verified' \
                        and canonical_url(sources[r['source_id']].get('url', '')) not in first_docs \
                        and state[1]['sha256'] not in {states[w['source_id']][1]['sha256'] for w in wanted
                                                       if states[w['source_id']][1]}:
                    second.append(r['source_id'])
            if second:
                decision.update(known=True, basis='citation_only_with_second_fetched_source', flags=['citation_only'],
                                second_sources=sorted(set(second)))
            else:
                quote_states = sorted({_ref_verified(r, states[r['source_id']], cache) for r in wanted})
                decision['detail'] = ('official source not fetched and hashed with a verified quote '
                                      '(' + ','.join(quote_states) + '); no second independent fetched source agrees on the year')
        else:
            decision['detail'] = 'no official evidence ref for the stated ' + ('absence' if absent else 'start year')
        decisions.append(decision)
    return decisions, states


def evidence_report(job, value, packet=None):
    """None for a packet without the rule; otherwise counts and the unit lists (no writes)."""
    packet = strict_packet(job, packet)
    if packet is None:
        return None
    decisions, _ = evaluate_rows(job, value, _entries(packet))
    downgraded = [d for d in decisions if not d['known']]
    return dict(rule=RULE, rows_checked=len(decisions), rows_kept=len(decisions) - len(downgraded),
                rows_downgraded=len(downgraded), downgraded_units=sorted(d['unit'] for d in downgraded),
                downgrade_reasons={d['unit']: d['detail'] for d in downgraded},
                citation_only_with_second_source=sorted(d['unit'] for d in decisions if d['known'] and d['flags']),
                fetch_log_entries=len(_entries(packet)))


def apply_evidence_rule(job, value, packet=None):
    """(value, report). Unchanged value and None report when the packet carries no rule."""
    packet = strict_packet(job, packet)
    if packet is None:
        return value, None
    entries = _entries(packet)
    decisions, _ = evaluate_rows(job, value, entries)
    result = deepcopy(value)
    by_unit = {d['unit']: d for d in decisions}
    for row in result['records']:
        decision = by_unit.get(row.get('unit'))
        if decision is None:
            continue
        if decision['known']:
            row['evidence_verification'] = dict(rule=RULE, basis=decision['basis'], flags=decision['flags'],
                                                **({'second_sources': decision['second_sources']} if decision.get('second_sources') else {}))
            if decision['flags']:
                row['evidence_flags'] = list(decision['flags'])
            continue
        original = deepcopy(row)
        row.update(status='unknown', start_period=None, evidence_status='unverified_downgrade',
                   reason='Evidence not verified under the fetched-and-hashed rule: ' + decision['detail'],
                   evidence_flags=['unverified'], evidence_unverified_original=original)
        row.pop('best_evidence_start_year', None)
        row.pop('pre_window_bound', None)   # T192: a downgraded row keeps no bound meaning
    report = dict(evidence_report(job, value, packet), schema='l2_evidence_verification_v1',
                  decisions=decisions, original_records_retained=True)
    return result, report


# --- queue commands --------------------------------------------------------------------------

def fetch_evidence(root, job_id, token, url, get=None, now=None):
    """Queue command: fetch one URL for the claimed job. Returns archive path, hash and provenance."""
    from .sonnet_queue import locked, owned, heartbeat
    now = time.time() if now is None else now
    with locked(root):
        packet, _ = owned(root, job_id, token, now)
        job, binding = history_binding(packet / 'PACKET.json')
        if job is None:
            raise ValueError('fetch-evidence serves V9 official-history packets with network access only')
        if len(_entries(packet)) >= MAX_FETCHES_PER_PACKET:
            raise ValueError('Fetch limit for this packet reached')
        out = str(_archive_dir(job).parent)
        if out not in binding['allowed_write_paths']:
            raise ValueError('Archive directory is outside the packet write scope')
    heartbeat(root, job_id, token, now)  # the fetch below can take a while; refresh the lease first
    staged = staged_entry(packet, url)
    if staged is not None:  # the hub imported a normal-browser copy of this URL: no network, same fields as a fetch
        doc = document_pages(verify_entry(job, staged), staged['content_type'], staged['charset'])
        return dict(staged, quote_check_available=doc['status'] == 'ok', text_method=doc['method'], text_status=doc['status'],
                    pages_total=len(doc['pages']))
    try:   # T226 P1: a page the hub fetched once in a normal browser is served from the shared pack, no network call
        from . import shared_pack
        pooled = shared_pack.lookup(root, url)
        if pooled is not None:
            result, extra = shared_pack.as_fetch_result(pooled)
            with locked(root):
                packet, _ = owned(root, job_id, token, time.time() if now is None else now)
                entry = record_fetch(packet, job, check_url(url), result, 'browser', now=now)
                _, log = packet_files(packet)
                value = _read(log)
                value['entries'][-1].update(extra)
                _atomic(log, value)
                entry = value['entries'][-1]
            doc = document_pages(result['data'], entry['content_type'], entry['charset'])
            return dict(entry, quote_check_available=doc['status'] == 'ok', text_method=doc['method'], text_status=doc['status'],
                        pages_total=len(doc['pages']))
    except (OSError, ValueError, KeyError):
        pass   # a pack problem never blocks a fetch: the normal route follows
    fetched = fetch_for_packet(packet, job, url, get=get, now=now)
    with locked(root):
        packet, _ = owned(root, job_id, token, time.time() if now is None else now)
        entry = record_fetch(packet, job, fetched['url'], fetched['result'], fetched['via'], fetched['live_error'], now=now)
    doc = document_pages(fetched['result']['data'], entry['content_type'], entry['charset'])
    return dict(entry, quote_check_available=doc['status'] == 'ok', text_method=doc['method'], text_status=doc['status'],
                pages_total=len(doc['pages']))


MAX_URLS_PER_CALL = 12
CALL_BUDGET_SECONDS = 100   # a call stops starting new pages after this long (a worker's shell command has a time limit); the rest come back as not attempted
URLS_FILE_BYTES = 64 * 1024


def read_urls_file(root, job_id, token, path, now=None):
    """Worker-fix G3: the URLs of a list file the worker wrote (one per line; blank lines and lines starting with # are skipped).

    The file must be a regular .txt file inside the packet's own output directory (the worker's write scope), at most 64 KB and 12 URLs.
    Only the count is ever reported back, never file content."""
    from .sonnet_queue import locked, owned
    now = time.time() if now is None else now
    with locked(root):
        packet, _ = owned(root, job_id, token, now)
        job, binding = history_binding(packet / 'PACKET.json')
        if job is None:
            raise ValueError('fetch-evidence serves V9 official-history packets with network access only')
        out = Path(job.output_dir).resolve()
    target = Path(str(path))
    resolved = target.resolve()
    if target.suffix != '.txt' or not resolved.is_relative_to(out) or not resolved.is_file():
        raise ValueError('--urls-file must be an existing .txt file inside the packet output directory')
    if resolved.stat().st_size > URLS_FILE_BYTES:
        raise ValueError('--urls-file is larger than ' + str(URLS_FILE_BYTES) + ' bytes')
    try:
        lines = resolved.read_text(encoding='utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError('--urls-file is not UTF-8 text') from exc
    urls = [line.strip() for line in lines if line.strip() and not line.strip().startswith('#')]
    if not urls:
        raise ValueError('--urls-file lists no URL')
    if len(urls) > MAX_URLS_PER_CALL:
        raise ValueError('At most ' + str(MAX_URLS_PER_CALL) + ' URLs per call; split the list')
    return urls


def fetch_evidence_many(root, job_id, token, urls, get=None, now=None, budget=None):
    """Worker-fix G3: fetch several pages in one allowlisted call, one after the other.

    Every page is fetched, saved, hashed and logged exactly as ``fetch_evidence`` does for one URL (the lease is refreshed before
    each page). A page that cannot be fetched is reported (ok false, the error) and the rest continue; a stale lease or a packet
    that does not serve fetching stops the call. A URL repeated in the list is fetched once. After ``budget`` seconds
    (``CALL_BUDGET_SECONDS``) no new page is started: the rest are reported as not attempted (ok false) so the worker repeats them
    in a new call; the first page is always tried. Returns dict(requested, fetched, failed, not_attempted, results) in the order given."""
    budget = CALL_BUDGET_SECONDS if budget is None else budget
    started = time.monotonic()
    urls = list(urls)
    if not urls:
        raise ValueError('No URL given')
    if len(urls) > MAX_URLS_PER_CALL:
        raise ValueError('At most ' + str(MAX_URLS_PER_CALL) + ' URLs per call; split the list')
    results, first = [], {}
    for url in urls:
        key = str(url).strip()
        if key in first:
            results.append(dict(ok=results[first[key]]['ok'], url=url, duplicate_of=first[key]))   # same outcome as the first time
            continue
        if results and time.monotonic() - started >= budget:
            results.append(dict(ok=False, url=url, not_attempted=True,
                                error='not attempted: the call time budget (%d s) was used up; fetch this page in a new call' % budget))
            continue
        first[key] = len(results)
        try:
            results.append(dict(fetch_evidence(root, job_id, token, url, get=get, now=now), ok=True))
        except FetchError as exc:
            results.append(dict(ok=False, url=url, error=str(exc)[:400]))
    failed = sum(1 for r in results if not r['ok'])
    return dict(requested=len(urls), fetched=len(results) - failed, failed=failed,
                not_attempted=sum(1 for r in results if r.get('not_attempted')), results=results)


def fetch_evidence_cli(root, job_id, token, urls):
    """What the ``fetch-evidence`` command prints. One URL: the same reply as before, or {ok: false, url, error} instead of a traceback
    when the page cannot be fetched. Several URLs: ``fetch_evidence_many``."""
    if len(urls) == 1:
        try:
            return fetch_evidence(root, job_id, token, urls[0])
        except FetchError as exc:
            return dict(ok=False, url=urls[0], error=str(exc)[:400])
    return fetch_evidence_many(root, job_id, token, urls)


def verify_quote(root, job_id, token, sha256, quote_text, now=None):
    """Queue command: is the quote verbatim in the saved bytes with this hash?"""
    from .sonnet_queue import locked, owned
    now = time.time() if now is None else now
    with locked(root):
        packet, _ = owned(root, job_id, token, now)
        job, _ = history_binding(packet / 'PACKET.json')
        if job is None:
            raise ValueError('verify-quote serves V9 official-history packets only')
        entry = next((e for e in _entries(packet) if e['sha256'] == sha256), None)
        if entry is None:
            raise ValueError('No fetched file with this SHA256 in this packet')
        raw = verify_entry(job, entry)
    check = quote_check(raw, quote_text, entry['content_type'], entry['charset'])
    value = dict(sha256=sha256, archive_path=entry['archive_path'], via=entry['via'], quote_check=check,
                 min_quote_chars=MIN_QUOTE_CHARS)
    if check in ('no_text_layer', 'unavailable'):
        value['note'] = NO_TEXT_NOTE[check]
    return value


NO_TEXT_NOTE = {
    'no_text_layer': 'This PDF opens but has no text layer (a scan). Do not release the packet. Look for another official '
                     'copy (an HTML or text version, the session law, the code section on the legislature site) and fetch that; '
                     'if none exists, leave this unit unclassified in an honest incomplete submission (collection_complete false).',
    'unavailable': 'No reader could open these bytes. Do not release the packet. Fetch another copy or another official page; '
                   'if none exists, leave this unit unclassified in an honest incomplete submission (collection_complete false).',
}


def evidence_text(root, job_id, token, sha256, page=None, offset=0, now=None):
    """Queue command: the extracted text of one fetched-and-hashed file, one page per call.

    A PDF page is a real page; HTML and plain text are cut into pages of about 20,000 characters. A call returns at most
    30,000 characters (``truncated`` and ``next_offset`` continue a long page). ``text_status`` is ``ok``, ``no_text_layer``
    (a scan: nothing to read) or ``unavailable`` (no reader opens the file); the verdicts of ``verify-quote`` use the same
    extraction, so a quote copied from this text verifies."""
    from .sonnet_queue import locked, owned
    now = time.time() if now is None else now
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a non-negative integer')
    with locked(root):
        packet, _ = owned(root, job_id, token, now)
        job, _ = history_binding(packet / 'PACKET.json')
        if job is None:
            raise ValueError('evidence-text serves V9 official-history packets only')
        entry = next((e for e in _entries(packet) if e['sha256'] == sha256), None)
        if entry is None:
            raise ValueError('No fetched file with this SHA256 in this packet')
        raw = verify_entry(job, entry)
    doc = document_pages(raw, entry['content_type'], entry['charset'])
    pages = doc['pages']
    value = dict(sha256=sha256, archive_path=entry['archive_path'], via=entry['via'], content_type=entry['content_type'],
                 text_status=doc['status'], text_method=doc['method'], pages_total=len(pages),
                 page_chars=[len(p) for p in pages][:MAX_PAGE_LIST], page=None, offset=0, text='', truncated=False)
    if doc['status'] != 'ok':
        value['note'] = NO_TEXT_NOTE[doc['status']]
        return value
    number = 1 if page is None else page
    if type(number) is not int or not 1 <= number <= len(pages):
        raise ValueError('page must be an integer from 1 to ' + str(len(pages)))
    body = pages[number - 1]
    if offset > len(body):
        raise ValueError('offset is past the end of page ' + str(number))
    chunk = body[offset:offset + MAX_TEXT_CHARS]
    end = offset + len(chunk)
    value.update(page=number, offset=offset, text=chunk, truncated=end < len(body))
    if end < len(body):
        value['next_offset'] = end
    return value
