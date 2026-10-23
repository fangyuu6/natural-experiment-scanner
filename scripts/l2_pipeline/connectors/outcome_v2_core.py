"""Explicit keyless downloads and byte receipts for the independent V2 catalog.

No network calls occur during panel construction. Source bytes are data, never
instructions. HTTP blocks and login pages are surfaced rather than bypassed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import ConnectorError, ConnectorNotReady

PUBLIC_DOMAIN = 'US federal government work; public domain under 17 USC 105; cite source agency'
HOSTS = frozenset({'data.cdc.gov', 'www.cdc.gov', 'ftp.cdc.gov', 'stacks.cdc.gov', 'api.census.gov',
                   'www2.census.gov', 'www.census.gov', 'api.bls.gov',
                   'data.bls.gov', 'download.bls.gov', 'www.bls.gov'})


def check_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ConnectorError('Only credential-free official HTTPS data URLs are supported')
    if any(re.search(r'key|token|password|secret|auth', k, re.I)
           for k, _ in urllib.parse.parse_qsl(parsed.query)):
        raise ConnectorError('Public outcome downloads cannot contain credentials')


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def file_receipt(path: Path, url: str, license: str = PUBLIC_DOMAIN,
                 license_url: str = '', **extra) -> dict:
    """Inventory an immutable public source without interpreting its observations."""
    path = Path(path).resolve()
    _public_path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(path=str(path), url=url, size_bytes=path.stat().st_size,
                sha256=digest.hexdigest(), license=license, license_url=license_url, **extra)


def _public_path(path: Path) -> None:
    resolved = Path(path).resolve()
    if (set(resolved.parts) & {'.codex', '.aws', '.wrangler', '.env'}
            or resolved.name.startswith('.env')
            or resolved.name in {'auth.json', 'credentials', 'credentials.json'}):
        raise ConnectorError('Public data paths cannot address credential/configuration files')


class PublicClient:
    """One request/second per process; bounded 429/5xx retries; write-once cache.

    This client never adds an app token or API key. BLS daily/series/year limits
    are enforced by the BLS acquisition adapter. Servers can impose tighter
    limits; their Retry-After header is respected up to a 60-second wait, after
    which the request is parked for a later run.
    """
    _lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, *, max_bytes=100_000_000, timeout=45, retries=2,
                 interval=1.0, opener=None, sleep=time.sleep, clock=time.monotonic):
        if max_bytes <= 0 or retries not in range(3) or not math.isfinite(interval) or interval < 1:
            raise ConnectorError('Require positive size cap, at most 2 retries and >=1-second interval')
        self.max_bytes, self.timeout, self.retries, self.interval = max_bytes, timeout, retries, interval
        self.opener = opener or urllib.request.build_opener(_Redirects())
        self.sleep, self.clock = sleep, clock

    def _throttle(self):
        with self._lock:
            elapsed = self.clock() - type(self)._last_request
            if elapsed < self.interval:
                self.sleep(self.interval - elapsed)
            type(self)._last_request = self.clock()

    def fetch(self, url, destination, license=PUBLIC_DOMAIN, license_url='', *, json_body=None):
        check_url(url)
        body_bytes = None
        body_sha = None
        if json_body is not None:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.hostname != 'api.bls.gov' or parsed.path.rstrip('/') not in
                    {'/publicAPI/v1/timeseries', '/publicAPI/v1/timeseries/data'}
                    or not isinstance(json_body, dict)
                    or set(json_body) - {'seriesid', 'startyear', 'endyear', 'annualaverage'}):
                raise ConnectorError('POST supports only documented keyless BLS v1 series requests')
            ids = json_body.get('seriesid', [])
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 25
                    or any(not isinstance(s, str) or not re.fullmatch(r'[A-Z0-9]{1,30}', s) for s in ids)):
                raise ConnectorError('BLS v1 request requires at most 25 public series identifiers')
            try:
                first, last = int(json_body['startyear']), int(json_body['endyear'])
            except (KeyError, ValueError, TypeError) as exc:
                raise ConnectorError('BLS v1 POST must declare integer startyear/endyear') from exc
            if not 1900 <= first <= last or last - first >= 10:
                raise ConnectorError('BLS v1 permits at most 10 years per query')
            body_bytes = json.dumps(json_body, sort_keys=True, separators=(',', ':')).encode()
            body_sha = hashlib.sha256(body_bytes).hexdigest()
        destination = Path(destination)
        _public_path(destination)
        receipt_path = destination.with_name(destination.name + '.receipt.json')
        _public_path(receipt_path)
        if receipt_path.is_symlink():
            raise ConnectorError('Public byte receipts cannot be symlinks')
        if destination.exists() or receipt_path.exists():
            if not destination.is_file() or not receipt_path.is_file():
                raise ConnectorError('Existing download requires its byte receipt; no overwrite')
            prior = json.loads(receipt_path.read_text())
            current = file_receipt(destination, url, license, license_url)
            if any(prior.get(k) != current[k] for k in ('url', 'size_bytes', 'sha256', 'license', 'license_url')):
                raise ConnectorError('Cached source does not match its receipt/request')
            if prior.get('request_body_sha256') != body_sha:
                raise ConnectorError('Cached source belongs to a different public series request')
            return prior
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                headers = {'User-Agent': 'OutcomePanelV2/1.0', 'Accept': '*/*'}
                if body_bytes is not None:
                    headers['Content-Type'] = 'application/json'
                req = urllib.request.Request(url, data=body_bytes, headers=headers)
                with self.opener.open(req, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    check_url(final_url)
                    content_type = response.headers.get('Content-Type', '')
                    if 'text/html' in content_type.lower():
                        raise ConnectorNotReady('Public data endpoint returned HTML; access may be blocked')
                    body = response.read(self.max_bytes + 1)
                    if len(body) > self.max_bytes:
                        raise ConnectorError('Public download exceeds declared size cap')
                    if not body or body.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                        raise ConnectorNotReady('Empty/HTML response is not outcome data')
                break
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if not retryable or attempt == self.retries:
                    raise ConnectorNotReady(f'Public endpoint HTTP {exc.code}; no bypass attempted') from exc
                raw = exc.headers.get('Retry-After', '')
                try:
                    delay = float(raw)
                except ValueError:
                    try:
                        from email.utils import parsedate_to_datetime
                        delay = (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        delay = 2 ** (attempt + 1)
                if not math.isfinite(delay) or delay > 60:
                    raise ConnectorNotReady('Retry-After requires a later acquisition run') from exc
                self.sleep(max(1, delay))
            except urllib.error.URLError as exc:
                raise ConnectorNotReady('Public endpoint unreachable; retry explicitly later') from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write(body)
        receipt = file_receipt(destination, url, license, license_url,
                               final_url=final_url, content_type=content_type,
                               downloaded_at_utc=datetime.now(timezone.utc).isoformat(),
                               request_method='POST' if body_bytes is not None else 'GET',
                               request_body_sha256=body_sha,
                               access='public_no_account_no_key', source_authority='untrusted_data')
        with receipt_path.open('x') as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write('\n')
        return receipt
