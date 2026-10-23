"""Keyless first-order labor outcomes; no policy or treatment inputs.

BLS uses public flat files rather than consuming the unregistered API's 25/day
quota. CPS reuses local Basic Monthly public-use ZIPs and binds every selected
field to its vintage dictionary. Annual values require all twelve months.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import math
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData,
                     STATES, inventory, metadata, row, sha)
from .outcome_v2_core import _public_path

LICENSE = 'US federal government public-use data; public domain (17 USC 105); retain attribution'
LICENSE_URL = 'https://www.bls.gov/bls/linksite.htm'
LAUS_URL = 'https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU'
CES_URL = 'https://download.bls.gov/pub/time.series/sm/sm.data.56.TotalPrivate.Current'
QCEW_URL = 'https://data.bls.gov/cew/data/api/{year}/a/industry/10.csv'
CPS_URL = 'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html'
MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()


def _spec(series_id, years, units, population, release_lag, frequency, source_url, **extra):
    return dict(series_id=series_id, geography='US state and DC', years=list(years),
                units=units, population=population, release_lag=release_lag,
                frequency=frequency, source_url=source_url, license=LICENSE, **extra)


SERIES = {
    'bls_laus_employment': _spec('bls_laus_employment', (1976, 2025), 'persons',
        'civilian noninstitutional residents age16+; employed', 'about3–5weeks; annual revisions',
        ['annual', 'monthly'], LAUS_URL, bls_measure='05', seasonal_adjustment='not seasonally adjusted'),
    'bls_laus_unemployment_rate': _spec('bls_laus_unemployment_rate', (1976, 2025), 'percent',
        'civilian labor force age16+', 'about3–5weeks; annual revisions',
        ['annual', 'monthly'], LAUS_URL, bls_measure='03', seasonal_adjustment='not seasonally adjusted'),
    'bls_ces_private_employment': _spec('bls_ces_private_employment', (2007, 2025), 'thousands of payroll jobs',
        'all employees on private nonfarm establishment payrolls; workplace state',
        'about3–5weeks; revisions and annual benchmark', ['annual', 'monthly'], CES_URL,
        bls_measure='01', seasonal_adjustment='not seasonally adjusted'),
    'bls_ces_private_weekly_earnings': _spec('bls_ces_private_weekly_earnings', (2007, 2025), 'nominal USD per week',
        'all employees on private nonfarm establishment payrolls; workplace state',
        'about3–5weeks; revisions and annual benchmark', ['annual', 'monthly'], CES_URL,
        bls_measure='11', seasonal_adjustment='not seasonally adjusted'),
    'bls_qcew_private_weekly_wage': _spec('bls_qcew_private_weekly_wage', (2014, 2025), 'nominal USD per week',
        'private ownership5 UI-covered jobs, total industry10; workplace state',
        'about6–9months; annual revisions', ['annual'], QCEW_URL,
        ownership='5', industry='10'),
}
for _group, _ages in [('teen', (16, 19)), ('young_adult', (20, 24)), ('single_mother', (18, 54))]:
    for _measure, _unit in [('employment', 'percent employed'), ('hours', 'usual hours per week, all jobs'),
                            ('weekly_earnings', 'nominal USD per week, main job')]:
        _id = f'cps_{_group}_{_measure}'
        _population = f'civilian noninstitutional residents age{_ages[0]}–{_ages[1]}'
        if _group == 'single_mother':
            _population += '; women PEMARITL3–6 with CPS-observed own child under18 PRCHLD1–15'
        if _measure == 'hours':
            _population += '; employed with fixed usual hours (hours-varies excluded and counted)'
        if _measure == 'weekly_earnings':
            _population += '; wage/salary workers in outgoing rotation HRMIS4/8, PRERELG1'
        SERIES[_id] = _spec(_id, (2000 if _group == 'single_mother' else 1994, 2025),
            _unit, _population, 'about1month; population controls and earnings disclosure changes',
            ['annual', 'monthly'], CPS_URL, group=_group, measure=_measure, age_min=_ages[0], age_max=_ages[1])


def acquire(series_id, years, destination, client=None, *, source='flat', units=None,
            external_daily_queries=0):
    """Return receipts; optionally use documented unregistered BLS APIv1.

    The destination ledger conservatively reserves every potential retry. The
    25query/day quota is per public IP: callers must share the destination ledger
    or declare queries made elsewhere with external_daily_queries. No API key.
    """
    if series_id not in SERIES:
        raise ConnectorError('Unknown labor series: ' + series_id)
    if not years or any(type(y) is not int for y in years):
        raise ConnectorError('Declare integer years')
    if min(years) < SERIES[series_id]['years'][0] or max(years) > SERIES[series_id]['years'][1]:
        raise ConnectorNotReady('Acquisition years outside declared source coverage')
    if series_id.startswith('cps_'):
        raise ConnectorNotReady('Reuse scripts/l2_batch1/raw/monthly and metadata; declare local paths and CPS dictionaries')
    if source not in ('flat', 'api_v1'):
        raise ConnectorError('BLS source must be flat or api_v1')
    if source == 'api_v1' and 'qcew' in series_id:
        raise ConnectorNotReady('QCEW wage acquisition uses CSV slices, not BLS timeseries API')
    units = tuple(STATES.values()) if units is None else tuple(units)
    if not units or len(set(units)) != len(units) or not set(units) <= set(STATES.values()):
        raise ConnectorError('Acquisition units require unique US state abbreviations')
    if client is None:
        from .outcome_v2_core import PublicClient
        client = PublicClient()
    destination = Path(destination)
    if source == 'api_v1':
        return _acquire_api(series_id, years, units, destination, client, external_daily_queries)
    urls = [QCEW_URL.format(year=y) for y in sorted(set(years))] if 'qcew' in series_id else [SERIES[series_id]['source_url']]
    receipts = []
    for url in urls:
        name = 'qcew_' + url.split('/api/')[1].split('/')[0] + '_industry10.csv' if 'qcew' in series_id else url.rsplit('/', 1)[-1]
        receipts.append(client.fetch(url, destination / name, LICENSE, LICENSE_URL))
    return receipts


def _acquire_api(series_id, years, units, destination, client, external_daily_queries, spec=None):
    if type(external_daily_queries) is not int or not 0 <= external_daily_queries <= 25:
        raise ConnectorError('Declare external daily BLS queries as integer0–25')
    # NAMED_PANEL Q2: a caller may pass the measure spec of a LAUS series that is not in SERIES (population, measure 09).
    spec = spec if spec is not None else SERIES[series_id]
    fips = {unit: code for code, unit in STATES.items()}
    prefix, middle = ('LAUST', '00000000000') if 'laus' in series_id else ('SMU', '0000005000000')
    identifiers = [prefix + fips[unit] + middle + spec['bls_measure'] for unit in units]
    payloads = []
    for start in range(min(years), max(years) + 1, 10):
        end = min(start + 9, max(years))
        for offset in range(0, len(identifiers), 25):
            payloads.append(dict(seriesid=identifiers[offset:offset+25], startyear=str(start), endyear=str(end)))
    retries = getattr(client, 'retries', 0)
    if type(retries) is not int or retries not in range(3):
        raise ConnectorError('BLS API retries must be0–2 for quota accounting')
    reservation = len(payloads) * (1 + retries)
    destination.mkdir(parents=True, exist_ok=True)
    quota = destination / '.bls_v1_quota.json'
    _public_path(quota)
    if quota.is_symlink():
        raise ConnectorError('BLS quota ledger cannot be a symlink')
    today = datetime.now(timezone.utc).date().isoformat()
    # Lock across concurrent workers sharing this destination; reserve before HTTP.
    with quota.open('a+', encoding='utf-8') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        text = handle.read()
        previous = json.loads(text) if text else {}
        used = previous.get('reserved_queries', 0) if previous.get('utc_date') == today else 0
        prior_external = previous.get('external_daily_queries', 0) if previous.get('utc_date') == today else 0
        if type(used) is not int or used < 0 or type(prior_external) is not int or not 0 <= prior_external <= 25:
            raise ConnectorError('Invalid BLS quota ledger')
        external_daily_queries = max(external_daily_queries, prior_external)
        if used + external_daily_queries + reservation > 25:
            raise ConnectorNotReady('BLSv1 daily25query limit including possible retries; request fewer years/states or resume nextUTCday')
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(dict(utc_date=today, reserved_queries=used+reservation,
            external_daily_queries=external_daily_queries, scope='destination; publicIP quota also includes external callers'), indent=2) + '\n')
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    receipts = []
    for number, payload in enumerate(payloads):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        path = destination / f'bls_v1_{series_id}_{payload["startyear"]}_{payload["endyear"]}_{number}_{digest}.json'
        receipts.append(client.fetch('https://api.bls.gov/publicAPI/v1/timeseries/data/', path,
                                     LICENSE, LICENSE_URL, json_body=payload))
    return receipts


def _source_inventory(path, request, spec, *, dictionary=False):
    record = inventory(path, request)
    record.update(license=LICENSE, license_url=LICENSE_URL if spec['series_id'].startswith('bls_') else CPS_URL)
    receipt_path = path.with_name(path.name + '.receipt.json')
    _public_path(receipt_path)
    if receipt_path.is_symlink():
        raise ConnectorError('Labor receipt cannot be a symlink')
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('sha256') != record['sha256'] or receipt.get('size_bytes') != record['size_bytes']:
            raise ConnectorError('Labor byte receipt does not bind source bytes')
        record.update(url=receipt.get('url', ''), license=receipt.get('license', LICENSE),
                      license_url=receipt.get('license_url', ''))
    if spec['series_id'].startswith('cps_'):
        ledger = request.base_dir / 'scripts/l2_batch1/DOWNLOADS.csv'
        _public_path(ledger)
        if ledger.is_file():
            with ledger.open(newline='') as handle:
                bindings = {r['path']: r for r in csv.DictReader(handle)}
            try:
                relative = str(path.resolve().relative_to((request.base_dir / 'scripts/l2_batch1').resolve()))
            except ValueError:
                relative = ''
            binding = bindings.get(relative) if relative else None
            if binding:
                if binding['sha256'] != record['sha256'] or int(binding['size_bytes']) != record['size_bytes']:
                    raise ConnectorError('Local CPS download ledger hash or size mismatch')
                record.update(url=binding['url'], license=binding['license'])
    if not record.get('url'):
        if dictionary:
            record['url'] = CPS_URL
        elif 'qcew' in spec['series_id']:
            match = re.search(r'(?:qcew_)?(\d{4})', path.name)
            record['url'] = QCEW_URL.format(year=match[1]) if match else QCEW_URL
        else:
            record['url'] = spec['source_url']
    if dictionary:
        record['role'] = 'vintage dictionary'
    return record


def _number(value):
    if str(value).strip() in ('', '-', '(NA)', 'NA'):
        return None
    try:
        value = float(str(value).strip().replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Invalid numeric public labor value') from exc
    if not math.isfinite(value) or value < 0:
        raise ConnectorError('Invalid nonnegative public labor value')
    return value


def _bls_records(path):
    if path.suffix == '.json':
        source = json.loads(path.read_text())
        if source.get('status') != 'REQUEST_SUCCEEDED':
            raise ConnectorNotReady('BLS API response did not succeed')
        results = source.get('Results', {})
        if isinstance(results, list):
            results = results[0] if len(results) == 1 else {}
        for series in results.get('series', []):
            for record in series.get('data', []):
                yield dict(record, series_id=series['seriesID'])
    else:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle, delimiter='\t')
            if not reader.fieldnames or not {'series_id', 'year', 'period', 'value'} <= {x.strip() for x in reader.fieldnames}:
                raise ConnectorError('BLS flat file requires series_id/year/period/value TSV header')
            for record in reader:
                yield {k.strip(): (v or '').strip() for k, v in record.items() if k is not None}


def _public_panel(request, spec, cells, paths):
    output, support = [], []
    for unit in request.units:
        for year in request.years:
            months = range(1, 13) if request.frequency == 'monthly' else [None]
            for month in months:
                if month is None:
                    group = [cells.get((unit, year, m)) for m in range(1, 13)]
                    direct = cells.get((unit, year, 13))
                    # Published annual rates need their own denominator; never average monthly unemployment rates.
                    if direct is not None:
                        value = direct['value']
                    elif spec['series_id'] == 'bls_laus_unemployment_rate' or 'qcew' in spec['series_id']:
                        value = None
                    elif all(c is not None and c['value'] is not None for c in group):
                        value = sum(c['value'] for c in group) / 12
                    else:
                        value = None
                    count = sum(c is not None for c in group)
                    preliminary = any(c and c.get('preliminary') for c in group) or bool(direct and direct.get('preliminary'))
                else:
                    cell = cells.get((unit, year, month))
                    value = cell['value'] if cell else None
                    count, preliminary = int(cell is not None), bool(cell and cell.get('preliminary'))
                record = row(unit, year, '' if value is None else value, spec['units'], request.frequency, month,
                             series_id=spec['series_id'], population=spec['population'])
                output.append(record)
                support.append(metadata(record, value is not None, months_present=count, preliminary=int(preliminary)))
    return PanelData(tuple(output), tuple(support), tuple(_source_inventory(p, request, spec) for p in paths),
        ('BLS revised data are download-vintage observations; acquisition is not an historical-vintage archive.',),
        dict(series=spec, aggregation='published annual value if present; otherwise full12month mean except unemployment rate'))


def _published(request, spec):
    paths, cells = request.paths(), {}
    is_qcew = 'qcew' in spec['series_id']
    for path in paths:
        if is_qcew:
            with path.open(encoding='utf-8-sig', newline='') as handle:
                sources = list(csv.DictReader(handle))
            needed = {'area_fips', 'own_code', 'industry_code', 'size_code', 'year', 'qtr', 'disclosure_code', 'annual_avg_wkly_wage'}
            if not sources or not needed <= sources[0].keys():
                raise ConnectorError('QCEW annual industry slice schema mismatch')
        else:
            sources = _bls_records(path)
        for source in sources:
            if is_qcew:
                area = source['area_fips'].zfill(5)
                if area[:2] not in STATES or area[2:] != '000' or source['own_code'] != '5' or source['industry_code'] != '10' or source['size_code'] != '0':
                    continue
                if source['qtr'] != 'A':
                    raise ConnectorError('QCEW annual series requires qtr A')
                unit, month = STATES[area[:2]], 13
                disclosure = source['disclosure_code'].strip()
                if disclosure not in ('', 'N'):
                    raise ConnectorError('Unknown QCEW disclosure code')
                value = _number(source['annual_avg_wkly_wage']) if not disclosure else None
                preliminary = False
            else:
                sid = source['series_id'].strip()
                measure = spec['bls_measure']
                if spec['series_id'].startswith('bls_laus'):
                    match = re.fullmatch(r'LAUST(\d{2})00000000000' + measure, sid)
                else:
                    match = re.fullmatch(r'SMU(\d{2})0000005000000' + measure, sid)
                if not match or match[1] not in STATES:
                    continue
                unit = STATES[match[1]]
                if not re.fullmatch(r'M(?:0[1-9]|1[0-3])', source['period']):
                    raise ConnectorError('Unknown BLS monthly/annual period')
                month = int(source['period'][1:])
                value = _number(source['value'])
                if spec['series_id'] == 'bls_laus_unemployment_rate' and value is not None and value > 100:
                    raise ConnectorError('Invalid unemployment percentage')
                preliminary = 'P' in source.get('footnote_codes', '') or any(f.get('code') == 'P' for f in source.get('footnotes', []))
            year = int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year, month
            if key in cells:
                raise ConnectorError('Duplicate labor source state/period')
            cells[key] = dict(value=value, preliminary=preliminary)
    return _public_panel(request, spec, cells, paths)


def dictionary_for(year, month, directory):
    """Reuse audited epoch selection without running or rewriting the batch1 audit."""
    from scripts.l2_batch1.dictionary_audit import EPOCHS
    eligible = [(start, name) for start, name in EPOCHS if start <= year * 100 + month]
    if not 1994 <= year <= 2025 or not eligible:
        raise ConnectorNotReady('CPS vintage outside audited1994–2025 layouts')
    path = Path(directory) / max(eligible)[1]
    if not path.is_file():
        raise ConnectorNotReady('Missing CPS vintage dictionary: ' + str(path))
    return path


def verify_dictionary(path, measure, single_mother=False):
    _public_path(path)
    text = path.read_text(errors='replace').replace('\ufffd', '-')
    modern_year = bool(re.search(r'^\s*(?:D\s+)?HRYEAR4\s+', text, re.M))
    if single_mother and not re.search(r'^\s*(?:D\s+)?PRCHLD\s+', text, re.M):
        supplement = path.parent / '2003_jan03dd.txt'
        _public_path(supplement)
        if not supplement.is_file():
            raise ConnectorNotReady('Early CPS own-child series requires2003 retrospective dictionary supplement')
        extra = supplement.read_text(errors='replace')
        block = re.search(r'^PRCHLD\s+.*?(?=^PRNMCHLD)', extra, re.M | re.S)
        if not block or '(STARTING NOVEMBER 1999)' not in block[0]:
            raise ConnectorNotReady('PRCHLD retrospective introduction date unverified')
        text += '\n' + block[0]
    fields = {'GESTFIPS': (93, 94), 'age': (122, 123), 'PRPERTYP': (161, 162),
              'PEMLR': (180, 181), 'PWSSWGT': (613, 622),
              'interview_month': (16, 17) if modern_year else (65, 66),
              'interview_year': (18, 21) if modern_year else (67, 68)}
    if single_mother:
        fields.update(PESEX=(129, 130), PEMARITL=(125, 126), PRCHLD=(633, 634))
    if measure == 'hours':
        fields['PEHRUSLT'] = (224, 226)
    if measure == 'weekly_earnings':
        fields.update(HRMIS=(63, 64), PRERELG=(498, 499), PEIO1COW=(432, 433),
                      earnings=(527, 534), PWORWGT=(603, 612))
    names = {}
    for field, expected in fields.items():
        term = {'age': '(?:PEAGE|PRTAGE)', 'earnings': '(?:PRERNWA|PTERNWA)',
                'interview_month': 'HRMONTH', 'interview_year': 'HRYEAR4' if modern_year else 'HRYEAR'}.get(field, field)
        match = re.search(r'^\s*(' + term + r')\s+\d+[^\n]*?\(?\s*(\d+)\s*[-–]\s*(\d+)\s*\)?\s*$', text, re.M)
        if match:
            name, bounds = match[1], (int(match[2]), int(match[3]))
        else:
            match = re.search(r'^D\s+(' + term + r')\s+(\d+)\s+(\d+)\s*$', text, re.M)
            name, bounds = (match[1], (int(match[3]), int(match[3]) + int(match[2]) - 1)) if match else ('', None)
        if bounds != expected:
            raise ConnectorNotReady(f'CPS dictionary field/offset unverified: {field} in {path.name}')
        names[field] = name
    # Decimal and universe conventions must be explicit in the selected dictionary.
    for field, decimals in [('PWSSWGT', 4)] + ([('PWORWGT', 4), ('earnings', 2)] if measure == 'weekly_earnings' else []):
        name = names[field]
        block = re.search(r'^\s*(?:D\s+)?' + name + r'\s+[^\n]*\n(?:(?!^\s*(?:D\s+)?[A-Z][A-Z0-9_]{3,}\s+\d).*(?:\n|$)){0,25}', text, re.M)
        content = block[0] if block else ''
        if not re.search(rf'{decimals}\s*(?:IMPLIED\s+)?DECIMAL', content, re.I) and not (field == 'earnings' and '2 implied' in content.lower()):
            raise ConnectorNotReady('CPS implied decimals unverified: ' + name)
    return fields


def cps_month(path, year, month, spec, request, dictionary):
    import numpy as np
    fields = verify_dictionary(dictionary, spec['measure'], spec['group'] == 'single_mother')
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if not n.endswith('/')]
        if len(members) != 1:
            raise ConnectorError('CPS ZIP requires exactly one public-use data member')
        raw = archive.read(members[0])
    size = raw.find(b'\n') + 1
    if size <= max(b for a, b in fields.values()) or len(raw) % size:
        raise ConnectorError('CPS record width is invalid or nonrectangular')
    dtype = np.dtype(dict(names=list(fields), formats=['S' + str(b-a+1) for a, b in fields.values()],
                         offsets=[a-1 for a, b in fields.values()], itemsize=size))
    source = np.frombuffer(raw, dtype=dtype)
    try:
        data = {k: source[k].astype(np.int64) for k in fields}
    except ValueError as exc:
        raise ConnectorError('CPS selected field is not an integer') from exc
    expected_year = year if fields['interview_year'] == (18, 21) else year % 100
    if not np.all(data['interview_year'] == expected_year) or not np.all(data['interview_month'] == month):
        raise ConnectorError('CPS interview year/month disagrees with declared filename vintage')
    target = (data['PRPERTYP'] == 2) & (data['age'] >= spec['age_min']) & (data['age'] <= spec['age_max'])
    linkage_missing = np.zeros(len(source), dtype=bool)
    if spec['group'] == 'single_mother':
        target &= data['PESEX'] == 2
        linkage_missing = target & (~np.isin(data['PEMARITL'], range(1, 7)) | ~np.isin(data['PRCHLD'], range(-1, 16)))
        target &= np.isin(data['PEMARITL'], [3, 4, 5, 6]) & np.isin(data['PRCHLD'], range(1, 16))
    measure = spec['measure']
    eligible = target.copy()
    if measure == 'employment':
        weights = data['PWSSWGT'] / 10000
        values = np.isin(data['PEMLR'], [1, 2]).astype(float) * 100
        valid = eligible & np.isin(data['PEMLR'], range(1, 8)) & (weights > 0)
    elif measure == 'hours':
        eligible &= np.isin(data['PEMLR'], [1, 2])
        weights = data['PWSSWGT'] / 10000
        values = data['PEHRUSLT'].astype(float)
        valid = eligible & (values >= 0) & (values <= 198) & (weights > 0)
    else:
        eligible &= np.isin(data['PEMLR'], [1, 2]) & np.isin(data['HRMIS'], [4, 8]) & (data['PRERELG'] == 1) & np.isin(data['PEIO1COW'], range(1, 6))
        weights = data['PWORWGT'] / 10000
        values = data['earnings'] / 100
        valid = eligible & (values >= 0) & (weights > 0)
    results = []
    for fips, unit in STATES.items():
        if unit not in request.units:
            continue
        state = data['GESTFIPS'] == int(fips)
        sample = valid & state
        results.append(dict(unit=unit, year=year, month=month, weight_sum=float(weights[sample].sum()),
            weighted_sum=float((weights[sample] * values[sample]).sum()), n_valid=int(sample.sum()),
            n_eligible=int((eligible & state).sum()), n_missing=int((eligible & state & ~valid).sum()),
            n_linkage_unknown=int((state & linkage_missing).sum())))
    return results


def _cps(request, spec):
    paths = request.paths()
    dictionary_dir = request.options.get('dictionary_dir', 'scripts/l2_batch1/raw/metadata')
    dictionary_dir = request.base_dir / dictionary_dir
    monthly, dictionaries, seen = [], set(), set()
    for path in paths:
        match = re.fullmatch(r'(\d{4})_([a-z]{3})\.zip', path.name)
        if not match or match[2] not in MONTHS:
            raise ConnectorError('CPS local files require YYYY_mon.zip names')
        year, month = int(match[1]), MONTHS.index(match[2]) + 1
        if (year, month) in seen:
            raise ConnectorError('Duplicate CPS source month')
        seen.add((year, month))
        if year not in request.years:
            continue
        if spec['group'] == 'single_mother' and year < 2000:
            raise ConnectorNotReady('Official own-child recode annual series requires2000+')
        dictionary = dictionary_for(year, month, dictionary_dir)
        dictionaries.add(dictionary)
        if spec['group'] == 'single_mother' and year < 2003:
            dictionaries.add(dictionary_dir / '2003_jan03dd.txt')
        monthly.extend(cps_month(path, year, month, spec, request, dictionary))
    groups = defaultdict(list)
    for record in monthly:
        groups[record['unit'], record['year']].append(record)
    output, support = [], []
    for unit in request.units:
        for year in request.years:
            records = groups[unit, year]
            for month in range(1, 13) if request.frequency == 'monthly' else [None]:
                group = [r for r in records if month is None or r['month'] == month]
                complete = {r['month'] for r in group} == ({month} if month is not None else set(range(1, 13)))
                weight = sum(r['weight_sum'] for r in group)
                missing = sum(r['n_missing'] for r in group)
                linked = not any(r['n_linkage_unknown'] for r in group)
                usable = complete and linked and weight > 0 and all(r['n_valid'] > 0 for r in group)
                if request.options.get('missing_person_rule', 'exclude_and_count') == 'require_all_valid':
                    usable &= missing == 0
                value = sum(r['weighted_sum'] for r in group) / weight if usable else ''
                record = row(unit, year, value, spec['units'], request.frequency, month, series_id=spec['series_id'],
                             population=spec['population'], n_person_months=sum(r['n_valid'] for r in group))
                output.append(record)
                support.append(metadata(record, usable, linked, complete_period=int(complete), months_present=len(group),
                    n_valid=sum(r['n_valid'] for r in group), n_missing=missing, weight_sum=weight,
                    n_linkage_unknown=sum(r['n_linkage_unknown'] for r in group)))
    return PanelData(tuple(output), tuple(support), tuple(_source_inventory(p, request, spec) for p in paths) + tuple(
        _source_inventory(p, request, spec, dictionary=True) for p in sorted(dictionaries)),
        ('CPS repeated monthly cross sections; no longitudinal person linkage.',
         'Earnings retain public-use topcoding, allocation and2023/2024 disclosure changes; no Pareto adjustment.',
         'Single mothers mean unmarried women with CPS-observed own children;2007 ascertainment change retained.',
         'Basic CPS weekly earnings are individual earnings; they do not measure household income.'),
        dict(series=spec, aggregation='pooled valid person-month weighted mean; annual requires12months',
             earnings_weight='PWORWGT/10000 for outgoing rotations4/8', final_weight='PWSSWGT/10000',
             earnings_divisor=100, dictionary_paths=[str(p) for p in sorted(dictionaries)]))


def build(request: ConnectorRequest) -> PanelData:
    series_id = request.options.get('series_id')
    if series_id not in SERIES:
        raise ConnectorError('Declare a registered labor series_id')
    spec = SERIES[series_id]
    if request.filters:
        raise ConnectorNotReady('V2 labor series have fixed populations; custom population filters require a separate series')
    if request.frequency not in spec['frequency']:
        raise ConnectorNotReady('Frequency unavailable for labor series')
    if not request.years or min(request.years) < spec['years'][0] or max(request.years) > spec['years'][1]:
        raise ConnectorNotReady('Requested years outside declared labor coverage')
    if request.options.get('missing_person_rule', 'exclude_and_count') not in ('exclude_and_count', 'require_all_valid'):
        raise ConnectorError('Unknown missing_person_rule')
    return _cps(request, spec) if series_id.startswith('cps_') else _published(request, spec)
