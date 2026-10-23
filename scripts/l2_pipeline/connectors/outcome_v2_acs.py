"""MOE-preserving ACS1 state outcomes, using free public summary files.

The tiny keyless Census data API request checked on 2026-10-09 returned a
Missing Key HTML page. Metadata remained public. Acquisition therefore defaults
to the no-account table-based summary files (2021+). Earlier archived API JSON
can be read locally; this adapter never registers for or reads an API key.
No policy/treatment information or estimation enters this module.
"""
from __future__ import annotations

import csv
import json
import math
import hashlib
import io
import zipfile
import re
from pathlib import Path
from urllib.parse import urlencode

from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData,
                     STATES, NAMES, inventory, metadata, row)

LICENSE = 'US federal government public domain; cite US Census Bureau ACS'
LICENSE_URL = 'https://www.census.gov/about/policies/quality/copyright.html'
API = 'https://api.census.gov/data/{year}/acs/acs1'
SUMMARY = ('https://www2.census.gov/programs-surveys/acs/summary_file/{year}/'
           'table-based-SF/data/1YRData/acsdt1y{year}-{table}.dat')
SERIES: dict[str, dict] = {}


def _cells(table, positions):
    return tuple(f'{table}_{i:03d}E' for i in positions)


def _register(identifier, table, numerator, denominator, population, filters, first=2005):
    SERIES[identifier] = dict(series_id=identifier, geography='US state and DC',
        years=[y for y in range(first, 2025) if y != 2020], frequency='annual',
        units='percent' if denominator else 'USD in source-year inflation-adjusted dollars',
        population=population, population_filters=filters,
        release_lag='Approximately 9 months after reference-year end (September ACS1 release)',
        source_url=API + '/groups/' + table + '.json',
        public_summary_url=SUMMARY.format(year='{year}', table=table.lower()),
        table=table, numerator_variables=_cells(table, numerator),
        denominator_variables=_cells(table, denominator),
        public_download_years=list(range(max(first, 2010), 2020)) + [2021, 2022, 2023, 2024],
        table_based_download_years=[2021, 2022, 2023, 2024],
        sequence_based_download_years=list(range(max(first, 2010), 2020)),
        archived_local_only_years=list(range(first, 2010)), license=LICENSE, license_url=LICENSE_URL,
        keyless_api_status='Tiny live request returned Missing Key HTML; default uses public summary DAT')


# B17001 disjoint sex/age counts. Poverty denominator = below + at/above;
# never sum age-specific percentages. 5-11 combines published age5 and age6-11.
POVERTY_BINS = {'under5': (4,), '5_11': (5, 6), '12_14': (7,), '15': (8,),
    '16_17': (9,), '18_24': (10,), '25_34': (11,), '35_44': (12,),
    '45_54': (13,), '55_64': (14,), '65_74': (15,), '75_plus': (16,)}
for age, male in POVERTY_BINS.items():
    below = male + tuple(i + 14 for i in male)
    above = tuple(i + 29 for i in below)
    _register('acs1_poverty_rate_age_' + age, 'B17001', below, below + above,
        'Population for whom poverty status is determined; both sexes; published age ' + age,
        {'age_group': age, 'sex': 'all'})
_register('acs1_poverty_rate_age_all', 'B17001', (2,), (1,),
          'Population for whom poverty status is determined', {'age_group': 'all', 'sex': 'all'})
for age, cell in {'all': 1, 'under25': 2, '25_44': 3, '45_64': 4, '65_plus': 5}.items():
    _register('acs1_median_household_income_age_' + age, 'B19049', (cell,), (),
        'Households classified by age of householder: ' + age,
        {'householder_age_group': age})
_register('acs1_single_mother_median_family_income', 'B19126', (10,), (),
    'Families with female householder, no spouse present, and own children under18; includes cohabiting families',
    {'female_householder': True, 'spouse_present': False, 'own_children_under18': True})
_register('acs1_snap_household_receipt_rate', 'B22003', (2,), (1,),
          'Households reporting receipt of Food Stamps/SNAP in past12 months', {})
INSURANCE_BINS = {'under6': 3, '6_18': 6, '19_25': 9, '26_34': 12,
                 '35_44': 15, '45_54': 18, '55_64': 21, '65_74': 24, '75_plus': 27}
for age, start in INSURANCE_BINS.items():
    _register('acs1_insurance_coverage_age_' + age, 'B27001', (start + 1, start + 29),
        (start, start + 28),
        'Civilian noninstitutionalized population; both sexes; published age ' + age,
        {'age_group': age, 'sex': 'all'}, first=2017 if age in ('6_18', '19_25', '26_34') else 2008)
# Census changed these published bins in2017. Keep earlier populations separate.
for age, start in {'6_17': 6, '18_24': 9, '25_34': 12}.items():
    identifier = 'acs1_insurance_coverage_age_' + age
    _register(identifier, 'B27001', (start + 1, start + 29), (start, start + 28),
        'Civilian noninstitutionalized population; both sexes; historical published age ' + age,
        {'age_group': age, 'sex': 'all'}, first=2008)
    SERIES[identifier]['years'] = list(range(2008, 2017))
    SERIES[identifier]['public_download_years'] = list(range(2010, 2017))
    SERIES[identifier]['table_based_download_years'] = []
    SERIES[identifier]['sequence_based_download_years'] = list(range(2010, 2017))
    SERIES[identifier]['schema_break'] = 'B27001 shifted6-17/18-24/25-34 to6-18/19-25/26-34 in2017; separate populations'


def _series(series_id):
    try:
        return SERIES[series_id]
    except KeyError as exc:
        raise ConnectorNotReady('Unknown ACS1 outcome series: ' + str(series_id)) from exc


def variables(series_id):
    spec = _series(series_id)
    estimates = tuple(dict.fromkeys(spec['numerator_variables'] + spec['denominator_variables']))
    return tuple(v for e in estimates for v in (e, e[:-1] + 'M'))


def source_url(series_id, year, source='summary'):
    spec = _series(series_id)
    if source == 'summary':
        return SUMMARY.format(year=year, table=spec['table'].lower())
    if source == 'api':
        return API.format(year=year) + '?' + urlencode({'get': ','.join(('NAME',) + variables(series_id)), 'for': 'state:*'})
    raise ConnectorError('ACS source must be summary or api')


def acquire(series_id, years, destination, client=None, *, source='summary', units=None):
    """Fetch one table/year with rate-limited PublicClient and SHA receipts.

    Standard2020 remains absent. Public sequence files cover2010-2019 and
    table-based files cover2021+. Optional API mode is strictly
    keyless and recognizes HTTP200 Missing Key HTML as a blocked data acquisition.
    """
    spec = _series(series_id)
    years = tuple(years)
    if not years or len(set(years)) != len(years):
        raise ConnectorError('ACS acquisition years must be nonempty and unique')
    if any(y != 2020 and y not in spec['years'] for y in years):
        raise ConnectorNotReady('ACS year outside declared series coverage')
    if source == 'summary' and any(y != 2020 and y < 2010 for y in years):
        raise ConnectorNotReady('Pre2010 requires archived local API JSON; historical sequence layout pending')
    if client is None:
        from .outcome_v2_core import PublicClient
        client = PublicClient()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    receipts = []
    for year in years:
        if year == 2020:
            continue
        if source == 'summary' and year < 2020:
            receipts.extend(_acquire_sequence(spec, year, destination, client, units))
            continue
        suffix = '.dat' if source == 'summary' else '.json'
        path = destination / f'acsdt1y{year}-{spec["table"].lower()}{suffix}'
        receipt = client.fetch(source_url(series_id, year, source), path,
                               license=LICENSE, license_url=LICENSE_URL)
        _load(path, variables(series_id))  # Reject error HTML and wrong table before returning receipt.
        receipts.append(receipt)
    return tuple(receipts)


def _load(path, required):
    text = path.read_text(encoding='utf-8-sig')
    if text.lstrip().startswith('<'):
        reason = 'Census API requires a key; use no-account summary DAT' if 'Missing Key' in text else 'Source returned HTML instead of ACS data'
        raise ConnectorNotReady(reason)
    if text.lstrip().startswith('['):
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ConnectorError('Malformed ACS API JSON') from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ConnectorError('ACS API JSON must contain header and rows')
        header = payload[0]
        if len(header) != len(set(header)) or any(not isinstance(r, list) or len(r) != len(header) for r in payload[1:]):
            raise ConnectorError('Duplicate columns or malformed ACS API row')
        if not set(required).issubset(header) or 'state' not in header:
            raise ConnectorError('Required ACS estimate/MOE/state columns missing')
        return [dict(zip(header, r)) for r in payload[1:]]
    reader = csv.DictReader(text.splitlines(), delimiter='|')
    header = reader.fieldnames or []
    mapped = {v: v.split('_')[0] + '_' + v[-1] + v.split('_')[1][:-1] for v in required}
    if len(header) != len(set(header)) or 'GEO_ID' not in header or not set(mapped.values()).issubset(header):
        raise ConnectorError('Required ACS summary estimate/MOE/GEO_ID columns missing')
    result = []
    for source in reader:
        if None in source or any(v is None for v in source.values()):
            raise ConnectorError('Malformed ACS summary row')
        match = re.fullmatch(r'0400000US(\d{2})', source['GEO_ID'])
        if match:
            result.append(dict(state=match[1], **{v: source[field] for v, field in mapped.items()}))
    return result


SENTINELS = {-111111111, -222222222, -333333333, -555555555,
             -666666666, -888888888, -999999999}


def _numeric(token):
    if token is None or str(token).strip() in ('', 'null', '(X)', 'N', '-', '**', '***', '*****'):
        return None
    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise ConnectorError('Unknown ACS numeric token: ' + repr(token)) from exc
    if not math.isfinite(value):
        raise ConnectorError('Nonfinite ACS value')
    if value in SENTINELS:
        return None
    if value < 0:
        raise ConnectorError('Unexpected negative ACS estimate/MOE')
    return value


def _year(path, request):
    declared = request.options.get('source_years', {}).get(str(path.relative_to(request.base_dir))
        if path.is_relative_to(request.base_dir) else str(path))
    match = re.search(r'acsdt1y(\d{4})|(?:^|[_-])(\d{4})(?:[_-]|\.)', path.name, re.I)
    inferred = int(next(g for g in match.groups() if g)) if match else None
    if declared is None and inferred is None:
        raise ConnectorError('Declare ACS source year in filename or source_years')
    if declared is not None and inferred is not None and int(declared) != inferred:
        raise ConnectorError('ACS filename year disagrees with source_years')
    return int(declared) if declared is not None else inferred


def build(request: ConnectorRequest) -> PanelData:
    spec = _series(request.options.get('series_id'))
    if request.frequency != 'annual':
        raise ConnectorNotReady('ACS1 outcome supports annual frequency')
    if request.filters and dict(request.filters) != spec['population_filters']:
        raise ConnectorNotReady('ACS published population filters do not match series population')
    if any(y != 2020 and y not in spec['years'] for y in request.years):
        raise ConnectorNotReady('Year outside declared ACS1 series coverage')
    paths, cells, sources = request.paths(), {}, {}
    for path in paths:
        if _auxiliary(path):
            continue
        _validate_derived(path)
        year = _year(path, request)
        if year == 2020:
            raise ConnectorNotReady('Standard2020 ACS1 was not released; experimental estimates cannot be spliced')
        if year not in request.years:
            continue
        for source in _load(path, variables(spec['series_id'])):
            fips = str(source['state']).zfill(2)
            if fips not in STATES or STATES[fips] not in request.units:
                continue
            key = STATES[fips], year
            if key in cells:
                raise ConnectorError('Duplicate ACS state-year; no vintage or overlapping-cell splice')
            cells[key] = {v: _numeric(source[v]) for v in variables(spec['series_id'])}
            sources[key] = str(path)
    rows, metas = [], []
    for unit in request.units:
        for year in request.years:
            cell = cells.get((unit, year), {})
            numvars, denvars = spec['numerator_variables'], spec['denominator_variables']
            complete = all(cell.get(v) is not None for v in dict.fromkeys(numvars + denvars))
            numerator = sum(cell[v] for v in numvars) if complete else None
            denominator = sum(cell[v] for v in denvars) if complete and denvars else None
            if complete and denvars and numerator > denominator:
                raise ConnectorError('ACS proportion numerator exceeds population denominator')
            value = (100 * numerator / denominator if denominator else None) if denvars else numerator
            # Direct published median MOE is valid. Ratios have covariance between
            # numerator and denominator; preserve original MOEs without inventing a ratio MOE.
            outcome_moe = cell.get(numvars[0][:-1] + 'M') if not denvars and len(numvars) == 1 else None
            missing = 'standard_2020_not_released' if year == 2020 else ('source_or_estimate_missing' if not complete else ('zero_denominator' if denvars and not denominator else ''))
            record = row(unit, year, value if value is not None else '', spec['units'],
                series_id=spec['series_id'], numerator=numerator, denominator=denominator,
                outcome_moe=outcome_moe, moe_confidence_level=0.90,
                cell_estimates={v: cell.get(v) for v in dict.fromkeys(numvars + denvars)},
                cell_moes={v[:-1] + 'M': cell.get(v[:-1] + 'M') for v in dict.fromkeys(numvars + denvars)},
                missing_reason=missing, source=sources.get((unit, year), ''),
                dollar_price_year=year if not denvars else None, population=spec['population'])
            rows.append(record)
            metas.append(metadata(record, value is not None, series_id=spec['series_id'], missing_reason=missing))
    receipts = []
    for path in paths:
        item = inventory(path, request)
        item['license'] = LICENSE
        item['license_url'] = LICENSE_URL
        sidecar = path.with_name(path.name + '.receipt.json')
        if sidecar.is_symlink():
            raise ConnectorError('ACS byte receipt cannot be a symlink')
        if sidecar.exists():
            verified = json.loads(sidecar.read_text())
            if verified.get('sha256') != item['sha256'] or verified.get('size_bytes') != item['size_bytes']:
                raise ConnectorError('ACS byte receipt mismatch')
            item.update(verified)
        elif not _auxiliary(path):
            year = _year(path, request)
            item['url'] = item['url'] or source_url(spec['series_id'], year, 'api' if path.suffix == '.json' else 'summary')
        receipts.append(item)
    return PanelData(tuple(rows), tuple(metas), tuple(receipts),
        ('ACS1 standard2020 unavailable; no experimental or ACS5 splice.',
         'Source MOEs are90% confidence; derived proportions retain source cell MOEs, with no fabricated ratio MOE.',
         'Income is household/family median in source-year dollars; householder age is not individual age.',
         'SNAP household receipt measures self-reported participation, not food insecurity.',
         'B27001 changed three agebins in2017; historical and current populations remain separate series.',
         'Single-mother family-income population includes cohabiting families and own children under18 only.'),
        {'series': dict(spec), 'coverage_only': True})


def _auxiliary(path):
    return bool(re.fullmatch(r'acs_lookup_\d{4}\.txt|g\d{4}1[a-z]{2}\.csv|\d{4}1[a-z]{2}\d{4}000\.zip', path.name))


def _validate_derived(path):
    if '-derived.dat' not in path.name:
        return
    if path.is_symlink():
        raise ConnectorError('Derived ACS input cannot be a symlink')
    sidecar = path.with_name(path.name + '.receipt.json')
    if sidecar.is_symlink():
        raise ConnectorError('Derived ACS byte receipt cannot be a symlink')
    if not sidecar.is_file():
        raise ConnectorError('Derived ACS input requires raw source byte receipts')
    receipt = json.loads(sidecar.read_text())
    if receipt.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ConnectorError('Derived ACS input hash mismatch')
    provenance = receipt.get('source_receipts', [])
    if not provenance or receipt.get('artifact_role') != 'derived_panel_input':
        raise ConnectorError('Missing derived ACS raw provenance')
    for source in provenance:
        raw = Path(source['path'])
        # Same directory only: derived manifests cannot direct credential/config reads.
        if raw.is_symlink() or raw.resolve().parent != path.parent.resolve() or not _auxiliary(raw):
            raise ConnectorError('Derived ACS raw provenance must reference sibling public auxiliary files')
        if (not raw.is_file() or raw.stat().st_size != source['size_bytes']
                or hashlib.sha256(raw.read_bytes()).hexdigest() != source['sha256']):
            raise ConnectorError('Derived ACS raw source hash mismatch')


def _lookup(path, table):
    matches = []
    with path.open(encoding='latin-1', newline='') as handle:
        for record in csv.DictReader(handle):
            if record.get('Table ID') == table and record.get('Start Position', '').strip():
                matches.append(record)
    if len(matches) != 1:
        raise ConnectorNotReady('ACS sequence lookup needs exactly one table start (split tables require review)')
    record = matches[0]
    count = re.fullmatch(r'(\d+) CELLS', record['Total Cells in Table'].strip())
    if not count:
        raise ConnectorError('Invalid ACS lookup table cell count')
    seq, start = int(record['Sequence Number']), int(record['Start Position'])
    if not 1 <= seq <= 9999 or start < 7:
        raise ConnectorError('Invalid ACS lookup sequence/start position')
    return seq, start, int(count[1])


def _sequence_values(geo_path, zip_path, year, unit, sequence, start, count):
    matches = []
    with geo_path.open(newline='', encoding='latin-1') as handle:
        for record in csv.reader(handle):
            if len(record) >= 10 and record[2:4] == ['040', '00']:
                if record[1].upper() != unit or STATES.get(record[9]) != unit:
                    raise ConnectorError('ACS geography state and FIPS disagree')
                matches.append(record[4])
    if len(matches) != 1:
        raise ConnectorError('ACS geography must have exactly one whole-state component00 record')
    logrec = matches[0]
    values = {}
    with zipfile.ZipFile(zip_path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100_000_000:
            raise ConnectorError('ACS sequence expanded ZIP exceeds100MB')
        for measure in ('e', 'm'):
            expected = f'{measure}{year}1{unit.lower()}{sequence:04d}000.txt'
            if archive.namelist().count(expected) != 1:
                raise ConnectorError('ACS ZIP must contain exact expected estimate/MOE member')
            with archive.open(expected) as raw, io.TextIOWrapper(raw, encoding='latin-1', newline='') as handle:
                selected = []
                for record in csv.reader(handle):
                    if len(record) >= 6 and record[5] == logrec:
                        if record[:5] != ['ACSSF', f'{year}{measure}1', unit.lower(), '000', f'{sequence:04d}']:
                            raise ConnectorError('ACS sequence year/state/iteration mismatch')
                        selected.append(record)
                if len(selected) != 1 or len(selected[0]) < start - 1 + count:
                    raise ConnectorError('ACS sequence missing/duplicate/truncated whole-state record')
                values[measure.upper()] = selected[0][start - 1:start - 1 + count]
    return values


def _acquire_sequence(spec, year, destination, client, units):
    """Download just the needed sequence per selected state, join LOGRECNO.

    Each source file retains its own public URL receipt. A small derived DAT
    carries an empty URL and the full raw hashes; build revalidates those bytes.
    """
    if not 2010 <= year <= 2019:
        raise ConnectorNotReady('Reviewed legacy ACS sequence acquisition supports2010-2019')
    units = tuple(units) if units is not None else tuple(STATES.values())
    if not units or len(units) != len(set(units)) or not set(units) <= set(STATES.values()):
        raise ConnectorError('ACS acquisition units must be unique states/DC')
    base = f'https://www2.census.gov/programs-surveys/acs/summary_file/{year}'
    lookup_path = destination / f'acs_lookup_{year}.txt'
    if year >= 2013:
        lookup_url = base + '/documentation/user_tools/ACS_1yr_Seq_Table_Number_Lookup.txt'
    else:
        lookup_url = base + '/documentation/1_year/user_tools/Sequence_Number_and_Table_Number_Lookup.txt'
    lookup_receipt = client.fetch(lookup_url, lookup_path, license=LICENSE, license_url=LICENSE_URL)
    seq, start, count = _lookup(lookup_path, spec['table'])
    receipts = [dict(lookup_receipt, artifact_role='auxiliary')]
    state_names = {v: k.replace(' ', '') for k, v in NAMES.items()}
    for unit in units:
        directory = base + '/data/1_year_seq_by_state/' + state_names[unit] + '/'
        geo_path = destination / f'g{year}1{unit.lower()}.csv'
        zip_path = destination / f'{year}1{unit.lower()}{seq:04d}000.zip'
        geo_receipt = client.fetch(directory + geo_path.name, geo_path, license=LICENSE, license_url=LICENSE_URL)
        zip_receipt = client.fetch(directory + zip_path.name, zip_path, license=LICENSE, license_url=LICENSE_URL)
        values = _sequence_values(geo_path, zip_path, year, unit, seq, start, count)
        source_receipts = [lookup_receipt, geo_receipt, zip_receipt]
        receipts.extend(dict(r, artifact_role='auxiliary') for r in (geo_receipt, zip_receipt))
        table = spec['table']
        output = destination / f'acsdt1y{year}-{table.lower()}-{unit.lower()}-derived.dat'
        output_receipt = output.with_name(output.name + '.receipt.json')
        if output.is_symlink() or output_receipt.is_symlink():
            raise ConnectorError('Derived ACS output/receipt cannot be symlinks')
        if not output.exists() and output_receipt.exists():
            raise ConnectorError('Orphan derived ACS receipt; no overwrite')
        headers = ['GEO_ID'] + [f'{table}_{measure}{cell:03d}' for cell in range(1, count + 1) for measure in ('E', 'M')]
        fips = next(k for k, v in STATES.items() if v == unit)
        fields = [f'0400000US{fips}'] + [values[m][i] for i in range(count) for m in ('E', 'M')]
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter='|', lineterminator='\n')
        writer.writerow(headers)
        writer.writerow(fields)
        body = buffer.getvalue().encode('utf-8')
        from .outcome_v2_core import file_receipt
        if output.exists():
            _validate_derived(output)
            if output.read_bytes() != body:
                raise ConnectorError('Existing derived ACS input disagrees with raw sequence')
            derived_receipt = json.loads(output.with_name(output.name + '.receipt.json').read_text())
        else:
            output.write_bytes(body)
            derived_receipt = file_receipt(output, '', LICENSE, LICENSE_URL,
                artifact_role='derived_panel_input', derived_from_urls=[r['url'] for r in source_receipts],
                source_receipts=source_receipts, source_sha256={r['url']: r['sha256'] for r in source_receipts},
                construction='Whole-state component00 LOGRECNO join; official table lookup start positions; source E/M preserved')
            output.with_name(output.name + '.receipt.json').write_text(json.dumps(derived_receipt, indent=2) + '\n')
        receipts.append(derived_receipt)
    return receipts
