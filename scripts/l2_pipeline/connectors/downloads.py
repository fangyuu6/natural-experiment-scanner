"""Explicit, key-free official-source download plans and write-once fetches.

Never fetched as a side effect of construct(). A caller saves returned manifests
with the study's source inventory. Retries are zero; blocked endpoints are not
bypassed. HTML/error bodies are refused, never accepted as public data.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest
from .public_annual import _acs_variables

LICENSE = 'US federal government public domain; cite source agency'
ALLOWED_HOSTS = {'data.bls.gov', 'download.bls.gov', 'www.bls.gov', 'api.census.gov', 'www2.census.gov'}


def download_plan(request: ConnectorRequest) -> tuple[dict[str, Any], ...]:
    """Return URL/filename/rights plans without network or credential access.

    ACS defaults to public table files, with a keyless API alternative. API access
    can require a key; no key is requested and the table files remain usable.
    Table-based summary support covers 2022+; earlier local API extracts may be
    parsed, but their sequence-based public fetch adapter is explicitly parked.
    """
    name = request.options['connector'].lower()
    plans = []
    def add(url: str, filename: str, **extra: Any) -> None:
        plans.append(dict(url=url, filename=filename, license=LICENSE, size_bytes=None,
                          status='planned_not_downloaded', **extra))
    if name in ('qcew', 'qcew_annual'):
        strategy = request.options.get('download_strategy', 'annual_zip')
        if strategy not in ('annual_zip', 'industry_slice'):
            raise ConnectorError('Unknown QCEW download strategy')
        industry = str(request.filters.get('industry', ''))
        if strategy == 'industry_slice' and not re.fullmatch(r'\d{1,6}', industry):
            raise ConnectorError('QCEW industry slice requires an explicit numeric industry')
        for year in request.years:
            if strategy == 'industry_slice':
                if year < 2014:
                    raise ConnectorNotReady('QCEW industry API slices start in 2014; use official annual ZIPs for older years')
                add(f'https://data.bls.gov/cew/data/api/{year}/a/industry/{industry}.csv',
                    f'{year}_{industry}.csv', format='QCEW annual industry CSV slice',
                    license_url='https://www.bls.gov/bls/linksite.htm')
            else:
                add(f'https://data.bls.gov/cew/data/files/{year}/csv/{year}.annual.singlefile.zip',
                    f'{year}.annual.singlefile.zip', format='QCEW annual CSV ZIP',
                    license_url='https://www.bls.gov/bls/linksite.htm')
    elif name in ('laus', 'laus_state'):
        add('https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU',
            'la.data.2.AllStatesU', format='BLS tab-delimited bulk; NSA annual M13',
            license_url='https://www.bls.gov/bls/linksite.htm')
    elif name in ('acs1', 'acs1_state'):
        numerator, denominator = _acs_variables(request)
        variables = list(dict.fromkeys(numerator + denominator))
        if len(variables) > 49:
            raise ConnectorNotReady('ACS keyless API plan permits at most 49 estimates plus NAME')
        tables = sorted({variable.split('_')[0].lower() for variable in variables})
        for year in request.years:
            if year == 2020:
                raise ConnectorNotReady('Standard ACS1 2020 was not released')
            if year < 2022:
                raise ConnectorNotReady('ACS1 pre-2022 public download needs sequence/geography adapter; use archived API JSON')
            query = urllib.parse.urlencode({'get': ','.join(['NAME'] + variables), 'for': 'state:*'})
            alternative = f'https://api.census.gov/data/{year}/acs/acs1?{query}'
            for table in tables:
                filename = f'acsdt1y{year}-{table}.dat'
                add(f'https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/1YRData/{filename}',
                    filename, format='ACS1 pipe-delimited public table summary',
                    keyless_api_alternative=alternative,
                    api_filename=f'acs1_{year}.json',
                    license_url='https://www.census.gov/about/policies/open-government/open-data.html')
    else:
        raise ConnectorNotReady('No public download planner for ' + name)
    return tuple(plans)


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ConnectorError('Downloads require a credential-free official HTTPS source')
    if any(re.search(r'key|token|password|secret|auth', key, re.I) for key in urllib.parse.parse_qs(parsed.query)):
        raise ConnectorError('Download plan cannot carry API keys or credentials')


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(plan: dict[str, Any], destination: Path, *, max_bytes: int = 1_000_000_000,
                 timeout_seconds: float = 30) -> dict[str, Any]:
    """Download one planned official file; return exact URL/size/license/hash.

    No retries, authentication, automatic fallback, or overwriting. All bytes are
    validated before publication. Failure leaves no destination file.
    """
    url = plan['url']
    _check_url(url)
    if not plan.get('license'):
        raise ConnectorError('Public download must declare source rights')
    destination = Path(destination)
    if any(part in ('l2_batch1', 'l2_batch2', 'l2_batch3', 'did_samples', 'insight', 'results',
                    '.codex', '.aws', '.wrangler') for part in destination.resolve().parts):
        raise ConnectorError('Refuse download into frozen, results, or credential directories')
    if destination.name.startswith('.env') or destination.name in ('auth.json', 'credentials', 'credentials.json'):
        raise ConnectorError('Refuse credential/configuration destination')
    if destination.exists():
        raise FileExistsError(destination)
    request = urllib.request.Request(url, headers={'User-Agent': 'L2-public-data-connector/1.0'})
    opener = urllib.request.build_opener(_OfficialRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            _check_url(response.geturl())
            if 'html' in response.headers.get('Content-Type', '').lower():
                raise ConnectorNotReady('Public endpoint returned HTML; do not bypass account or bot checks')
            payload = response.read(max_bytes + 1)
            final_url = response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ConnectorNotReady('Public download unavailable; no retry or authentication attempted') from exc
    if len(payload) > max_bytes:
        raise ConnectorError('Public download exceeds declared maximum size')
    if not payload or payload.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        raise ConnectorNotReady('Public endpoint did not return a data file')
    if destination.suffix == '.json':
        try:
            json.loads(payload)
        except (ValueError, UnicodeError) as exc:
            raise ConnectorNotReady('Public endpoint did not return valid JSON') from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as handle:
        handle.write(payload)
    return dict(plan, path=str(destination.resolve()), final_url=final_url, size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(), status='downloaded', retries=0)
