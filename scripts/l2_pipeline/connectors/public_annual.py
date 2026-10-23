"""Treatment-free public QCEW, ACS1 and LAUS annual constructions.

Construction uses declared local files. Download planning is separate and never
creates an account, reads a key, or makes a network call during panel building.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData, STATES, exact_filters
from .published import _finalize, _numeric


def _csv_rows(path: Path, delimiter: str = ',') -> Iterator[dict[str, str]]:
    """Read ZIP members directly; never extract archive paths into the workspace."""
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = sorted(n for n in archive.namelist() if n.lower().endswith(('.csv', '.dat')))
            if not members:
                raise ConnectorError('Public ZIP has no CSV/DAT members')
            for name in members:
                with archive.open(name) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as handle:
                    for record in csv.DictReader(handle, delimiter=delimiter):
                        yield {str(k).strip(): str(v).strip() for k, v in record.items()}
    else:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for record in csv.DictReader(handle, delimiter=delimiter):
                yield {str(k).strip(): str(v).strip() for k, v in record.items()}


def _licensed(request: ConnectorRequest, agency: str) -> ConnectorRequest:
    options = dict(request.options)
    options.setdefault('license', 'US federal government public domain; cite ' + agency)
    return replace(request, options=options)


def qcew_annual(request: ConnectorRequest) -> PanelData:
    """State annual CSV/ZIP, or declared complete county roster → state annual.

    State totals are preferred because county totals can omit unallocated jobs.
    County aggregation is explicit and remains labeled as the covered roster.
    Suppressed or absent counties invalidate the state cell; never impute zero.
    """
    ownership = str(request.filters.get('ownership', ''))
    industry = str(request.filters.get('industry', ''))
    exact_filters(request, {'ownership': ownership, 'industry': industry})
    if ownership == 'total' and request.options.get('ownership_aggregation') == 'certified_sum':
        from .qcew_total import total_ownership
        return total_ownership(request)
    codes = {'total': '0', 'federal': '1', 'state': '2', 'local': '3', 'private': '5'}
    if ownership not in codes or not re.fullmatch(r'\d{1,6}', industry):
        raise ConnectorNotReady('Declare QCEW ownership and a NAICS/total industry code')
    measure = request.options.get('value_field', 'annual_avg_emplvl')
    # Historical cards may use avg_wkly_wage; canonical annual CSV field differs.
    measure = {'avg_wkly_wage': 'annual_avg_wkly_wage'}.get(measure, measure)
    if measure not in {'annual_avg_emplvl', 'annual_avg_estabs_count', 'total_annual_wages',
                       'annual_avg_wkly_wage', 'avg_annual_pay'}:
        raise ConnectorNotReady('Unsupported QCEW annual measure')
    geography = request.options.get('source_geography', 'state')
    if geography not in ('state', 'county'):
        raise ConnectorNotReady('QCEW source_geography must be state or county')
    roster = request.options.get('county_fips', {})
    if geography == 'county':
        for unit in request.units:
            fips = next(k for k, v in STATES.items() if v == unit)
            counties = roster.get(unit, [])
            if (not counties or len(counties) != len(set(counties))
                    or any(not re.fullmatch(fips + r'\d{3}', c) or c.endswith('000') for c in counties)):
                raise ConnectorNotReady('County QCEW requires a unique complete declared county_fips roster per state')
    request = _licensed(request, 'Bureau of Labor Statistics')
    paths, seen = request.paths(), {}
    for path in paths:
        for source in _csv_rows(path):
            area = source['area_fips'].zfill(5)
            if (area[:2] not in STATES or source['own_code'] != codes[ownership]
                    or source['industry_code'] != industry or source['size_code'] != '0'):
                continue
            unit, year = STATES[area[:2]], int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            if geography == 'state' and area[2:] != '000':
                continue
            if geography == 'county' and area not in roster[unit]:
                continue
            if source.get('qtr', 'A') != 'A':
                raise ConnectorError('QCEW annual request contains quarterly rows')
            key = area, year
            if key in seen:
                raise ConnectorError('Duplicate QCEW area/ownership/industry/year')
            suppressed = source['disclosure_code'] != ''
            if source['disclosure_code'] not in ('', 'N'):
                raise ConnectorError('Unknown QCEW disclosure code')
            field = measure
            if field not in source and measure == 'annual_avg_wkly_wage' and 'avg_wkly_wage' in source:
                field = 'avg_wkly_wage'
            if field not in source:
                raise ConnectorError('QCEW annual field absent: ' + measure)
            value = '' if suppressed else source[field]
            if value != '':
                _numeric(value)
            seen[key] = dict(value=value, source=str(path), raw=source, suppressed=suppressed)
    values = {}
    for unit in request.units:
        fips = next(k for k, v in STATES.items() if v == unit)
        for year in request.years:
            if geography == 'state':
                source = seen.get((fips + '000', year))
                if source:
                    values[unit, year] = dict(value=source['value'], source=source['source'],
                                              counts={'suppression_flag': int(source['suppressed'])})
                continue
            selected = [seen[c, year] for c in roster[unit] if (c, year) in seen]
            complete = len(selected) == len(roster[unit])
            usable = complete and all(s['value'] != '' for s in selected)
            value: Any = ''
            if usable:
                if measure in ('annual_avg_wkly_wage', 'avg_annual_pay'):
                    employment = sum(_numeric(s['raw']['annual_avg_emplvl']) for s in selected)
                    wages = sum(_numeric(s['raw']['total_annual_wages']) for s in selected)
                    value = wages / employment / (52 if measure == 'annual_avg_wkly_wage' else 1) if employment else ''
                else:
                    value = sum(_numeric(s['value']) for s in selected)
            values[unit, year] = dict(value=value, source=';'.join(sorted({s['source'] for s in selected})),
                linkage_certified=complete, counts={'counties_expected': len(roster[unit]),
                'counties_present': len(selected), 'suppression_flag': int(any(s['suppressed'] for s in selected))})
    panel = _finalize(request, values, paths, ownership + ' UI-covered ' + measure,
        ('County aggregation covers only the declared roster; state totals include separately allocated jobs.',
         'Disclosure suppression and missing counties remain missing; NAICS/vintage changes require review.'))
    return replace(panel, details={'source_geography': geography, 'county_fips': roster,
        'measure': measure, 'dictionary': 'https://www.bls.gov/cew/about-data/downloadable-file-layouts/annual/naics-based-annual-layout.htm'})


def _acs_variables(request: ConnectorRequest) -> tuple[tuple[str, ...], tuple[str, ...]]:
    numerator = tuple(request.options.get('variables', request.options.get('numerator_variables', [])))
    denominator = tuple(request.options.get('denominator_variables', []))
    if (not numerator or len(set(numerator)) != len(numerator) or len(set(denominator)) != len(denominator)
            or any(not isinstance(v, str) or not re.fullmatch(r'[BC]\d{5}[A-I]?_\d{3}E', v)
                   for v in numerator + denominator)):
        raise ConnectorNotReady('ACS1 requires unique explicit detailed-table estimate variables')
    return numerator, denominator


def _acs_year(path: Path, request: ConnectorRequest) -> int:
    declared = request.options.get('source_years', {}).get(str(path.relative_to(request.base_dir))
        if path.is_relative_to(request.base_dir) else str(path))
    match = re.search(r'acsdt1y(\d{4})|(?:^|[_-])(\d{4})(?:[_-]|\.)', path.name, re.I)
    if declared is None and not match:
        raise ConnectorError('ACS filename or source_years must declare the source year')
    return int(declared if declared is not None else next(g for g in match.groups() if g))


def acs1_state(request: ConnectorRequest) -> PanelData:
    """Generic ACS1 detailed-table sums or ratios, JSON API or public summary DAT.

    Published tables cannot apply additional microdata filters. A reviewed exact
    source_population_filters declaration is mandatory for the selected cells.
    """
    if 'source_population_filters' not in request.options:
        raise ConnectorNotReady('ACS1 requires source_population_filters for selected table cells')
    exact_filters(request, request.options['source_population_filters'])
    numerator, denominator = _acs_variables(request)
    request = _licensed(request, 'US Census Bureau')
    paths, cells, sources = request.paths(), {}, {}
    for path in paths:
        year = _acs_year(path, request)
        if year not in request.years:
            continue
        if year == 2020:
            raise ConnectorNotReady('Standard 2020 ACS1 estimates were not released; no experimental splice')
        if path.suffix.lower() == '.json':
            raw = json.loads(path.read_text())
            if not isinstance(raw, list) or not raw or not isinstance(raw[0], list):
                raise ConnectorError('ACS API response must be header-plus-rows JSON')
            if any(len(r) != len(raw[0]) for r in raw[1:]) or len(set(raw[0])) != len(raw[0]):
                raise ConnectorError('Malformed ACS API column/row inventory')
            entries = (dict(zip(raw[0], r)) for r in raw[1:])
        else:
            entries = _csv_rows(path, '|')
        for source in entries:
            if 'state' in source:
                fips = str(source['state']).zfill(2)
            else:
                match = re.fullmatch(r'0400000US(\d{2})', source.get('GEO_ID', ''))
                if not match:
                    continue
                fips = match[1]
            if fips not in STATES or STATES[fips] not in request.units:
                continue
            key = STATES[fips], year
            target = cells.setdefault(key, {})
            sources.setdefault(key, set()).add(str(path))
            for variable in dict.fromkeys(numerator + denominator):
                table, position = variable.split('_')
                summary_name = table + '_E' + position[:-1]
                field = variable if variable in source else summary_name
                if field not in source:
                    continue
                if variable in target:
                    raise ConnectorError('Duplicate ACS state/year/variable; no vintage splice')
                token = source[field]
                # All published special numeric codes are negative; null/annotations remain missing.
                if token is None or str(token).strip() in ('', 'null', '(X)', 'N', '-', '**', '***', '*****'):
                    target[variable] = ''
                else:
                    try:
                        numeric = float(token)
                    except (TypeError, ValueError) as exc:
                        raise ConnectorError('Unknown ACS estimate token') from exc
                    _numeric(token, minimum=None)  # Refuse NaN/Infinity before sentinel interpretation.
                    special = {-111111111, -222222222, -333333333, -555555555,
                               -666666666, -888888888, -999999999}
                    target[variable] = '' if numeric in special else str(token)
                    if numeric not in special:
                        _numeric(token, minimum=request.options.get('value_min', 0))
    values = {}
    scale = _numeric(request.options.get('scale', 100 if denominator else 1))
    for key, cell in cells.items():
        complete = all(cell.get(v, '') != '' for v in numerator + denominator)
        value: Any = ''
        if complete:
            total = sum(float(cell[v]) for v in numerator)
            divisor = sum(float(cell[v]) for v in denominator) if denominator else 1
            if divisor:
                value = cell[numerator[0]] if len(numerator) == 1 and not denominator and scale == 1 else scale * total / divisor
        values[key] = dict(value=value, source=';'.join(sorted(sources[key])),
                          counts={'variables_expected': len(set(numerator + denominator)), 'variables_present': len(cell)})
    panel = _finalize(request, values, paths, request.options.get('outcome_unit', 'ACS published estimate'),
        ('ACS1 published population only; special-value codes remain missing; standard 2020 is unavailable.',))
    return replace(panel, details={'numerator_variables': numerator, 'denominator_variables': denominator,
        'scale': scale, 'source_population_filters': dict(request.filters)})


def laus_state(request: ConnectorRequest) -> PanelData:
    """BLS LAUS full-state NSA official annual average (M13), measure 03.

    Reads official tab-delimited bulk files or archived BLS API JSON. Does not
    substitute a mean of monthly unemployment rates for the official M13 rate.
    """
    exact_filters(request, {'age_min': 16, 'civilian_noninstitutional': True})
    if request.options.get('seasonal_adjustment', 'not_seasonally_adjusted') != 'not_seasonally_adjusted':
        raise ConnectorNotReady('LAUS annual averages require not_seasonally_adjusted series')
    if request.options.get('value_field', 'unemployment_rate') != 'unemployment_rate':
        raise ConnectorNotReady('LAUS state adapter supports official unemployment_rate only')
    request = _licensed(request, 'Bureau of Labor Statistics')
    paths, values = request.paths(), {}
    for path in paths:
        if path.suffix.lower() == '.json':
            payload = json.loads(path.read_text())
            if payload.get('status') != 'REQUEST_SUCCEEDED':
                raise ConnectorError('BLS API archived response did not succeed')
            entries = (dict(record, series_id=series['seriesID'])
                for series in payload['Results']['series'] for record in series['data'])
        else:
            entries = _csv_rows(path, '\t')
        for source in entries:
            match = re.fullmatch(r'LAUST(\d{2})0{12}3', source['series_id'].strip())
            if not match or match[1] not in STATES or source['period'].strip() != 'M13':
                continue
            unit, year = STATES[match[1]], int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate LAUS official state annual rate; no vintage splice')
            value = source['value'].strip()
            if value in ('', '-', 'NA', 'N/A'):
                value = ''
            else:
                _numeric(value, 0, 100)
            footnotes = source.get('footnotes', source.get('footnote_codes', ''))
            values[key] = dict(value=value, source=str(path), counts={'official_annual_period': 'M13',
                                                                    'footnotes': json.dumps(footnotes, sort_keys=True)})
    return _finalize(request, values, paths, 'percent of civilian labor force unemployed; NSA official annual average',
        ('LAUS is place of residence, age 16+; annual M13 only; source vintage and revisions remain explicit.',))
