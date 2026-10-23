"""Keyless CDC/NCHS outcome panels, independent of policy/treatment tables.

Acquisition is explicit and rate limited by PublicClient. Build is offline.
NCHS summary PDF extraction requires the optional public package pypdf >= 5:
``python -m pip install pypdf``. Suppressed/absent cells stay missing. VSRR
counts measure occurrence and rolling twelve months, with NY + NYC combined.
"""
from __future__ import annotations

import calendar
import csv
import json
import math
from pathlib import Path
import re
import urllib.parse

from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest,
                     NAMES, STATES, PanelData, metadata, row)
from .outcome_v2_core import PublicClient, PUBLIC_DOMAIN, file_receipt, _public_path

LICENSE_URL = 'https://www.cdc.gov/other/agencymaterials.html'
BIRTH_REPORT_IDS = {2019: 231899, 2020: 231914, 2021: 122047,
                    2022: 145588, 2023: 175204, 2024: 252440}
BIRTH_REPORT_URLS = {year: f'https://stacks.cdc.gov/view/cdc/{i}/cdc_{i}_DS1.pdf'
                     for year, i in BIRTH_REPORT_IDS.items()}
AGES = {'10-14': 3, '15-19': 4, '15-17': 5, '18-19': 6, '20-24': 7,
        '25-29': 8, '30-34': 9, '35-39': 10, '40-44': 11, '45-49': 12}
CAUSES = ('All causes', "Alzheimer's disease", 'CLRD', 'Cancer', 'Diabetes',
          'Heart disease', 'Influenza and pneumonia', 'Kidney disease', 'Stroke',
          'Suicide', 'Unintentional injuries')


def _series(dataset, years, units, population, lag, *, frequency='annual',
            geography='state_of_residence', **extra):
    return dict(connector='outcome_v2_cdc', module=__name__, dataset_id=dataset,
                geography=geography, years=list(years), units=units,
                population=population, release_lag=lag, frequency=frequency,
                source_url=('https://data.cdc.gov/resource/' + dataset + '.csv'
                            if dataset != 'nchs_natality_summary_pdf' else
                            'https://stacks.cdc.gov/view/cdc/252440'),
                license=PUBLIC_DOMAIN, license_url=LICENSE_URL,
                access='public_no_account_no_key', status='ready', **extra)


SERIES = {
    'cdc_vsrr_overdose_deaths_reported': _series('xkb8-kh2a', (2015, 2026),
        'deaths in trailing 12 months', 'All deaths occurring in jurisdiction, including foreign residents',
        '4 months since 2022; previously 6 months; monthly revisions', frequency='monthly',
        geography='state_of_occurrence', value_field='data_value',
        supported_frequencies=['annual', 'monthly'], period_semantics='12 month-ending; annual takes December only'),
    'cdc_vsrr_overdose_deaths_predicted': _series('xkb8-kh2a', (2015, 2026),
        'predicted deaths in trailing 12 months', 'All deaths occurring in jurisdiction, including foreign residents',
        '4 months since 2022; previously 6 months; monthly revisions', frequency='monthly',
        geography='state_of_occurrence', value_field='predicted_value',
        supported_frequencies=['annual', 'monthly'], period_semantics='12 month-ending; annual takes December only'),
    'cdc_firearm_deaths': _series('fpsi-y8tj', (2019, 2024), 'deaths', 'Resident deaths, all ages and all firearm intents',
        'Approximately 4 months for firearm data; retain data_as_of; TTM records excluded', value_field='count_sup',
        definition_url='https://data.cdc.gov/api/views/fpsi-y8tj/files/165ef96f-aac7-4677-835d-7c7a809a51ff?download=true'),
    'cdc_firearm_crude_rate': _series('fpsi-y8tj', (2019, 2024), 'deaths per 100,000 population, crude',
        'Resident deaths, all ages and all firearm intents', 'Approximately 4 months for firearm data; retain data_as_of; TTM excluded', value_field='rate',
        definition_url='https://data.cdc.gov/api/views/fpsi-y8tj/files/165ef96f-aac7-4677-835d-7c7a809a51ff?download=true'),
    'cdc_leading_cause_deaths': _series('bi63-dtpu', (1999, 2017), 'deaths',
        'Resident deaths, all ages; select exact cause_name', 'Final annual publication; legacy source ends 2017',
        value_field='deaths', required_filters=['cause_name'], causes=list(CAUSES)),
    'cdc_leading_cause_age_adjusted_rate': _series('bi63-dtpu', (1999, 2017), 'deaths per 100,000, age adjusted to 2000 US standard population',
        'Resident deaths; select exact cause_name', 'Final annual publication; legacy source ends 2017',
        value_field='aadr', required_filters=['cause_name'], causes=list(CAUSES)),
    'cdc_teen_birth_rate': _series('y268-sna3', (1990, 2019), 'births per 1,000 females in selected age group',
        'Maternal residence, ages 15–17, 18–19 or 15–19', 'Final annual publication; legacy source ends 2019',
        geography='maternal_residence_state', value_field='state_rate', required_filters=['age_group'], age_groups=['15-17','18-19','15-19']),
    'cdc_teen_births': _series('y268-sna3', (1990, 2019), 'live births',
        'Maternal residence, ages 15–17, 18–19 or 15–19', 'Final annual publication; legacy source ends 2019',
        geography='maternal_residence_state', value_field='state_births', required_filters=['age_group'], age_groups=['15-17','18-19','15-19']),
    'nchs_summary_births': _series('nchs_natality_summary_pdf', (2019, 2024), 'live births',
        'All live births by maternal residence, all maternal ages/races/origins', 'Final annual reports 13–18 months after year end',
        geography='maternal_residence_state', source_urls=BIRTH_REPORT_URLS, table='Births, by race and Hispanic origin of mother; all races and origins column'),
    'nchs_summary_fertility_rate': _series('nchs_natality_summary_pdf', (2019, 2024), 'births per 1,000 females aged 15–44',
        'All live births divided by resident females aged 15–44', 'Final annual reports 13–18 months after year end',
        geography='maternal_residence_state', source_urls=BIRTH_REPORT_URLS, table='Birth rates, by age of mother; fertility rate column'),
    'nchs_summary_age_specific_birth_rate': _series('nchs_natality_summary_pdf', (2019, 2024), 'births per 1,000 females in selected age group',
        'Maternal residence, published maternal age bin', 'Final annual reports 13–18 months after year end',
        geography='maternal_residence_state', source_urls=BIRTH_REPORT_URLS, required_filters=['age_group'], age_groups=list(AGES),
        table='Birth rates, by age of mother', age_45_49_note='Numerator includes maternal ages 45 and older; denominator females aged 45–49'),
}


def _info(series_id):
    if series_id not in SERIES:
        raise ConnectorNotReady('Unknown CDC/NCHS V2 series: ' + str(series_id))
    return SERIES[series_id]


def acquire(series_id, years, destination, client=None):
    """Download exact raw official files, returning path/URL/size/license/SHA receipts.

    Destination is a directory. Only documented source years can be acquired;
    build can create missing rows outside coverage without extrapolation.
    Socrata requests select every demographic cell, leaving choice to build.
    """
    info = _info(series_id)
    years = tuple(sorted(set(int(y) for y in years)))
    if not years or min(years) < info['years'][0] or max(years) > info['years'][1]:
        raise ConnectorNotReady('Requested acquisition years exceed published series coverage')
    client = client or PublicClient()
    destination = Path(destination)
    receipts = []
    for year in years:
        if info['dataset_id'] == 'nchs_natality_summary_pdf':
            url, filename = BIRTH_REPORT_URLS[year], f'nchs_natality_{year}.pdf'
        else:
            where = (f"period='{year}' AND intent='FA_Deaths'" if info['dataset_id'] == 'fpsi-y8tj'
                     else f'year={year}')
            if info['dataset_id'] == 'xkb8-kh2a':
                where += " AND indicator='Number of Drug Overdose Deaths'"
            query = urllib.parse.urlencode({'$where': where, '$limit': 50000})
            url = info['source_url'] + '?' + query
            filename = info['dataset_id'] + '_' + str(year) + '.csv'
        receipt = client.fetch(url, destination / filename, info['license'], info['license_url'])
        if filename.endswith('.csv'):
            with Path(receipt['path']).open(newline='', encoding='utf-8-sig') as stream:
                if sum(1 for _ in csv.DictReader(stream)) >= 50000:
                    raise ConnectorError('Socrata row limit reached; refuse possibly truncated source')
        receipts.append(receipt)
    return receipts


_MISSING = {'', '*', '**', '---', '--', '-', '–', '—', '...', 'na', 'n/a', 'null', 'suppressed', 'unreliable', 'not available', '1-9', '-999'}


def _number(raw, *, count=False):
    text = str(raw if raw is not None else '').strip()
    if text.lower() in _MISSING:
        return None
    try:
        value = float(text.replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Invalid CDC/NCHS published numeric cell: ' + repr(text)) from exc
    if not math.isfinite(value) or value < 0 or (count and not value.is_integer()):
        raise ConnectorError('Invalid CDC/NCHS number for declared units')
    return int(value) if count else value


def _read_csv(path, required):
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not set(required) <= set(reader.fieldnames or []):
            raise ConnectorError('CDC/NCHS source schema does not match requested series')
        yield from reader


def _put(values, key, value, **extra):
    if key in values:
        raise ConnectorError('Duplicate CDC/NCHS state/period/population cell: ' + repr(key))
    values[key] = dict(value=value, **extra)


def _state(name):
    if name in {'United States', 'Total U.S.', 'Puerto Rico', 'New York City'}:
        return None
    if name not in NAMES:
        raise ConnectorError('Unknown CDC/NCHS residence state: ' + repr(name))
    return NAMES[name]


def _contract(request, info):
    if request.frequency not in info.get('supported_frequencies', ['annual']):
        raise ConnectorNotReady('Series does not publish requested panel frequency')
    filters = dict(request.filters)
    required = set(info.get('required_filters', []))
    if set(filters) != required:
        raise ConnectorNotReady('Published population requires exactly: ' + ', '.join(sorted(required)))
    if 'cause_name' in required and filters['cause_name'] not in CAUSES:
        raise ConnectorNotReady('Requested cause is not in published leading causes source')
    if 'age_group' in required and filters['age_group'] not in info['age_groups']:
        raise ConnectorNotReady('Requested maternal age bin is not published; no approximate rebinning')
    expected = info.get('value_field')
    if expected and request.options.get('value_field', expected) != expected:
        raise ConnectorError('Series/value_field mismatch; count and rate cannot be relabeled')


def _vsrr(request, info, paths):
    components = {}
    months = {name: i for i, name in enumerate(calendar.month_name) if name}
    for path in paths:
        for source in _read_csv(path, {'state','year','month','period','indicator',info['value_field'],'state_name','footnote'}):
            if source['indicator'] != 'Number of Drug Overdose Deaths':
                continue
            if source['period'] != '12 month-ending':
                raise ConnectorError('VSRR period is not the documented rolling twelve months')
            try:
                year, month = int(source['year']), months[source['month']]
            except (ValueError, KeyError) as exc:
                raise ConnectorError('Invalid VSRR ending month/year') from exc
            unit = source['state']
            if unit in {'US','PR'}:
                continue
            if unit not in set(STATES.values()) | {'YC'}:
                raise ConnectorError('Unknown VSRR occurrence jurisdiction')
            expected_name = 'New York City' if unit == 'YC' else next(n for n,u in NAMES.items() if u == unit)
            if source['state_name'] != expected_name:
                raise ConnectorError('VSRR state abbreviation/name disagreement')
            if year not in request.years or (unit if unit != 'YC' else 'NY') not in request.units:
                continue
            if request.frequency == 'annual' and month != 12:
                continue
            quality_missing = bool(re.search(r'suppressed|not shown|low data quality', source['footnote'], re.I))
            value = None if quality_missing else _number(source[info['value_field']], count=info['value_field']=='data_value')
            _put(components, (unit,year,month), value, footnote=source['footnote'],
                 percent_complete=source.get('percent_complete',''),
                 percent_pending_investigation=source.get('percent_pending_investigation',''))
    values = {}
    for unit in request.units:
        for year in request.years:
            for month in (range(1,13) if request.frequency == 'monthly' else [12]):
                parts = [components.get((u,year,month)) for u in (['NY','YC'] if unit == 'NY' else [unit])]
                present = all(p is not None for p in parts)
                valid = present and all(p['value'] is not None for p in parts)
                _put(values, (unit,year,month if request.frequency == 'monthly' else None),
                     sum(p['value'] for p in parts) if valid else None,
                     published_cell_present=int(present), source_components=parts,
                     nyc_combined=int(unit == 'NY'), period_semantics='12 month-ending',
                     annual_calendar_usable=int(request.frequency == 'annual'))
    return values


def _socrata(request, info, paths):
    values = {}
    dataset = info['dataset_id']
    required = {'fpsi-y8tj': {'geoid','name','intent','period','count_sup','rate'},
                'bi63-dtpu': {'state','year','cause_name','deaths','aadr'},
                'y268-sna3': {'state','year','age_years','state_births','state_rate','unit'}}[dataset]
    for path in paths:
        for source in _read_csv(path, required):
            extra = {}
            if dataset == 'fpsi-y8tj':
                if source['intent'] != 'FA_Deaths':
                    continue
                if source['period'] == 'TTM':
                    continue
                if not re.fullmatch(r'\d{4}', source['period']):
                    raise ConnectorError('Unknown injury mortality analysis period')
                year = int(source['period']); unit = STATES.get(source['geoid'].zfill(2))
                if not unit or NAMES.get(source['name']) != unit:
                    raise ConnectorError('Firearm FIPS/state name disagreement')
                extra = dict(data_as_of=source.get('data_as_of',''), suppressed_count_range=source['count_sup'], period_semantics='calendar_year')
            else:
                try:
                    year = int(source['year'])
                except ValueError as exc:
                    raise ConnectorError('Invalid CDC/NCHS calendar year') from exc
                unit = _state(source['state'])
                if dataset == 'bi63-dtpu':
                    if source['cause_name'] != request.filters['cause_name']:
                        continue
                    extra = dict(cause_name=source['cause_name'], icd10_cause=source.get('_113_cause_name',''))
                else:
                    if source['age_years'] != request.filters['age_group'] + ' years':
                        continue
                    if source['unit'] != 'per 1,000':
                        raise ConnectorError('Teen birth rate denominator unit changed')
                    extra = dict(age_group=request.filters['age_group'])
            if unit not in request.units or year not in request.years:
                continue
            if not info['years'][0] <= year <= info['years'][1]:
                raise ConnectorError('Source observation exceeds verified published vintage')
            field = info['value_field']
            value = _number(source[field], count=field in {'count_sup','deaths','state_births'})
            _put(values, (unit,year,None), value, published_cell_present=1, **extra)
    return values


_RATE_HEADER = r'Table\s+\d+\.\s+Birth rates, by age of mother:\s+United States, each state and territory,\s+(\d{4})'
_COUNT_HEADER = r'Table\s+\d+\.\s+Births, by race and Hispanic origin of mother:\s+United States, each state and territory,\s+(\d{4})'


def _pdf_tables(path, counts=False):
    """Fail closed on table/column/51-state coverage changes, including rotation.

    Plain pypdf extraction includes rotated landscape tables. Layout extraction
    defaults omit rotated text and must not be substituted here.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ConnectorNotReady('Raw NCHS summary PDF needs optional pypdf>=5 (python -m pip install pypdf)') from exc
    header = _COUNT_HEADER if counts else _RATE_HEADER
    records = {}
    found_years = set()
    for page_number, page in enumerate(PdfReader(path).pages, start=1):
        text = page.extract_text() or ''
        match = re.search(header, text)
        if not match:
            continue
        year = int(match.group(1)); found_years.add(year)
        if year not in BIRTH_REPORT_URLS:
            raise ConnectorNotReady('Natality summary report year is not certified in this adapter')
        if 'By place of residence' not in text or (not counts and 'Fertility rate' not in text):
            raise ConnectorError('NCHS summary table population/header changed')
        # In the 2021 PDF a handful of state labels wrap before the dot leaders.
        # Join that exact layout pattern only, preserving numeric field order.
        lines = text.splitlines()
        for i in range(len(lines)-1):
            if lines[i].strip() in NAMES and lines[i+1].lstrip().startswith('.'):
                lines[i+1] = lines[i].strip() + ' ' + lines[i+1].strip()
                lines[i] = ''
        for line in lines:
            name = next((n for n in NAMES if re.match(r'^' + re.escape(n) + r'(?:\s|\.)', line.strip())), None)
            if not name:
                continue
            rest = line.strip()[len(name):].lstrip(' .')
            tokens = rest.split()
            # "Alaska Native Asian Black" is a column heading, not Alaska.
            if not tokens or not re.fullmatch(r'[\d,]+(?:\.\d+)?|\*|---', tokens[0]):
                continue
            if len(tokens) != (7 if counts else 13):
                raise ConnectorError('NCHS state summary column count changed: ' + name)
            numbers = [_number(t, count=counts) for t in tokens]
            key = (NAMES[name],year)
            if key in records:
                raise ConnectorError('Duplicate NCHS summary state/year')
            records[key] = dict(numbers=numbers, source_pdf_page=page_number)
    if not found_years:
        raise ConnectorError('NCHS summary required state table missing; refuse national-table substitution')
    for year in found_years:
        if {u for u,y in records if y==year} != set(STATES.values()):
            raise ConnectorError('NCHS summary extraction lacks complete 51-state table; review PDF layout')
    return records


def _summaries(request, series_id, paths):
    values = {}
    for path in paths:
        if path.suffix.lower() != '.pdf':
            raise ConnectorError('NCHS summary adapter requires raw published PDF with byte receipt')
        for (unit,year), cell in _pdf_tables(path, counts=series_id=='nchs_summary_births').items():
            if unit not in request.units or year not in request.years:
                continue
            index = (0 if series_id=='nchs_summary_births' else 1 if series_id=='nchs_summary_fertility_rate'
                     else AGES[request.filters['age_group']])
            _put(values, (unit,year,None), cell['numbers'][index], published_cell_present=1,
                 source_pdf_page=cell['source_pdf_page'], age_group=request.filters.get('age_group','all'))
    return values


def _inventory(path, request, info):
    sidecar = path.with_name(path.name + '.receipt.json')
    if sidecar.is_symlink():
        raise ConnectorError('CDC/NCHS byte receipts cannot be symlinks')
    _public_path(sidecar)
    if sidecar.resolve().parent != path.parent.resolve():
        raise ConnectorError('CDC/NCHS byte receipt must remain beside public source')
    relative = str(path.relative_to(request.base_dir)) if path.is_relative_to(request.base_dir) else str(path)
    url = request.options.get('source_urls', {}).get(relative, info['source_url'])
    if info['dataset_id']=='nchs_natality_summary_pdf' and relative not in request.options.get('source_urls', {}):
        candidates=[y for y,i in BIRTH_REPORT_IDS.items() if str(y) in path.name or str(i) in path.name]
        if len(candidates)==1:
            url=BIRTH_REPORT_URLS[candidates[0]]
    if sidecar.is_file():
        receipt = json.loads(sidecar.read_text())
        current = file_receipt(path, receipt.get('url',''), info['license'], info['license_url'])
        if any(receipt.get(k) != current[k] for k in ('size_bytes','sha256')):
            raise ConnectorError('Source differs from downloaded byte receipt')
        return dict(receipt, license=info['license'], license_url=info['license_url'])
    return file_receipt(path, url, info['license'], info['license_url'], receipt_status='local_inventory_no_download_sidecar')


def build(request: ConnectorRequest) -> PanelData:
    series_id = request.options.get('series_id'); info = _info(series_id)
    _contract(request, info)
    paths = request.paths()
    if info['dataset_id'] == 'xkb8-kh2a':
        values = _vsrr(request, info, paths)
    elif info['dataset_id'] == 'nchs_natality_summary_pdf':
        values = _summaries(request, series_id, paths)
    else:
        values = _socrata(request, info, paths)
    rows, metas = [], []
    for unit in request.units:
        for year in request.years:
            for month in (range(1,13) if request.frequency=='monthly' else [None]):
                cell = values.get((unit,year,month), {})
                value = cell.get('value')
                extra = {k:v for k,v in cell.items() if k!='value'}
                record = row(unit,year,value,info['units'],request.frequency,month,
                             series_id=series_id,geography=info['geography'],**extra)
                rows.append(record)
                meta = metadata(record,value is not None,published_cell_present=cell.get('published_cell_present',0),
                                missing_reason=None if value is not None else 'Absent, suppressed, unreliable or outside published coverage')
                if info['dataset_id']=='xkb8-kh2a':
                    meta['annual_usable'] = int(value is not None and request.frequency=='annual')
                metas.append(meta)
    limitations = ['Missing or suppressed cells are not zero, interpolated or extrapolated.',
                   'Published population bins cannot identify policy beneficiary status.']
    if info['dataset_id']=='xkb8-kh2a':
        limitations += ['Monthly rows are overlapping trailing twelve months, not monthly death flows; annual uses December only.',
                        'Occurrence jurisdiction includes foreign residents; NY requires both NY outside NYC and YC components; no substitution for residence deaths.',
                        'Predicted counts are CDC reporting-delay adjustments; snapshot revisions require retaining byte receipts.']
    if info['dataset_id']=='fpsi-y8tj':
        limitations += ['Crude mortality rate is not age adjusted. TTM records are excluded from annual panels.']
    if series_id=='nchs_summary_age_specific_birth_rate':
        limitations += ['Ages 45–49 rate numerator includes mothers aged 45 and older; denominator ages 45–49.',
                        'Published overlapping teen age totals must not be summed; census denominator revisions may affect historical comparability.']
    return PanelData(tuple(rows),tuple(metas),tuple(_inventory(p,request,info) for p in paths),
                     tuple(limitations),dict(info,series_id=series_id,requested_years=list(request.years),
                         population_filters=dict(request.filters),outcome_association_calls=0,interpolation=False))
