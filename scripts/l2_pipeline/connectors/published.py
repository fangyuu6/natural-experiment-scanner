"""Copy-and-generalize batch2 published-file and batch3 QCEW adapters.

Local public files only. Suppression stays missing; published population limits
are enforced before values are accessed. Numerical construction has no treatment
labels, contrasts, fitting, or outcome-dependent queue selection.
"""
from __future__ import annotations

import math
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES,
                     PanelData, STATES, exact_filters, flag, inventory, metadata, records, row)


def _numeric(value: str | float, minimum: float | None = 0, maximum: float | None = None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ConnectorError('Invalid published numeric value') from exc
    if not math.isfinite(numeric) or (minimum is not None and numeric < minimum) or (maximum is not None and numeric > maximum):
        raise ConnectorError('Published value outside declared range')
    return numeric


def _finalize(request: ConnectorRequest, values: dict[tuple[str, int], dict[str, Any]],
              paths: tuple, outcome_unit: str, limitations: tuple[str, ...] = ()) -> PanelData:
    if request.frequency not in ('annual', 'biennial'):
        raise ConnectorNotReady('Published adapter has annual/election-year observations only')
    output, support = [], []
    for unit in sorted(request.units):
        for year in request.years:
            source = values.get((unit, year), {})
            value = source.get('value', '')
            record = row(unit, year, value, outcome_unit, request.frequency, source=source.get('source', ''))
            output.append(record)
            support.append(metadata(record, value != '' and source.get('available', True),
                                    bool(source) and source.get('linkage_certified', True),
                                    **source.get('counts', {})))
    return PanelData(tuple(output), tuple(support), tuple(inventory(p, request) for p in paths), limitations)


def standardized_csv(request: ConnectorRequest) -> PanelData:
    """Adopt a documented already-constructed local panel, preserving value text.

    A source-population declaration is required because aggregated panels cannot
    implement person-level filters. The source declaration remains reviewable
    evidence and needs independent verification before final freeze.
    """
    if 'source_population_filters' not in request.options:
        raise ConnectorNotReady('Standardized CSV requires source_population_filters')
    exact_filters(request, request.options['source_population_filters'])
    paths = request.paths()
    fields = request.options.get('columns', {})
    unit_field, year_field = fields.get('unit', 'unit'), fields.get('year', 'year')
    value_field = request.options.get('value_field', fields.get('value', 'outcome_value'))
    minimum = request.options.get('value_min')
    maximum = request.options.get('value_max')
    values = {}
    source_metadata = {}
    metadata_names = request.options.get('metadata_paths', request.options.get('metadata_path'))
    metadata_paths = ()
    if metadata_names:
        metadata_request = ConnectorRequest(dict(request.options, paths=metadata_names), request.filters,
                                            request.years, request.units, request.frequency, request.base_dir)
        metadata_paths = metadata_request.paths()
        for path in metadata_paths:
            for entry in records(path):
                key = entry['unit'], int(entry['year'])
                if key in source_metadata:
                    raise ConnectorError('Duplicate standardized source metadata key')
                source_metadata[key] = entry
    for path in paths:
        for source in records(path):
            unit, year = source[unit_field], int(source[year_field])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate standardized state-period key')
            value = source[value_field]
            if value != '':
                _numeric(value, minimum, maximum)
            if metadata_paths and key not in source_metadata:
                raise ConnectorNotReady('Source panel cell has no declared support metadata')
            entry = source_metadata.get(key, source)
            usable = all(flag(entry[k]) for k in ('available', 'annual_usable') if k in entry)
            linked = flag(entry['linkage_certified']) if 'linkage_certified' in entry else True
            values[key] = dict(value=value, source=str(path), available=usable, linkage_certified=linked,
                               counts={k: entry[k] for k in ('n_person_months', 'weight_sum', 'n_target', 'n_valid', 'n_missing') if k in entry})
    return _finalize(request, values, paths + metadata_paths,
                     request.options.get('outcome_unit', 'source published units'))


def qcew(request: ConnectorRequest) -> PanelData:
    exact_filters(request, {'ownership': 'private', 'industry': str(request.filters.get('industry', ''))})
    industry = request.filters['industry']
    if not industry or not industry.isdigit():
        raise ConnectorNotReady('QCEW requires a declared NAICS industry code')
    measure = request.options.get('value_field', 'annual_avg_emplvl')
    if measure not in ('annual_avg_emplvl', 'avg_wkly_wage', 'total_annual_wages'):
        raise ConnectorNotReady('Unsupported QCEW annual measure')
    paths, values = request.paths(), {}
    for path in paths:
        for source in records(path):
            area = source['area_fips'].zfill(5)
            if (len(area) != 5 or area[2:] != '000' or area[:2] not in STATES or source['own_code'] != '5'
                    or source['industry_code'] != industry or source['size_code'] != '0'):
                continue
            unit, year = STATES[area[:2]], int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate QCEW state/industry/year')
            value = '' if source['disclosure_code'].strip() else source[measure]
            if value != '':
                _numeric(value)
            values[key] = dict(value=value, source=str(path), counts={'suppression_flag': int(value == '')})
    return _finalize(request, values, paths, 'private UI-covered ' + measure,
                     ('Disclosure suppression is missing; annual NAICS revisions retain their source vintage.',))


def qcew_constructed(request: ConnectorRequest) -> PanelData:
    """Generalize batch3's text-preserving QCEW wage reuse path."""
    exact_filters(request, {'ownership': 'private', 'industry': str(request.filters.get('industry', ''))})
    paths, values = request.paths(), {}
    measure = request.options.get('value_field', 'y_wage')
    if measure != 'y_wage':
        raise ConnectorNotReady('Constructed QCEW adapter supports frozen log y_wage only')
    for path in paths:
        for source in records(path):
            if source['industry'] != request.filters['industry']:
                continue
            unit, year = source['unit'], int(source['period'])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate constructed QCEW cell')
            usable = not flag(source['disclosure_N']) and not flag(source['suppressed_or_zero']) and source[measure] != ''
            values[key] = dict(value=source[measure] if usable else '', source=str(path),
                               counts={'suppression_flag': int(not usable)})
    return _finalize(request, values, paths, 'log nominal average weekly wage; private QCEW jobs')


def cdc_public(request: ConnectorRequest) -> PanelData:
    exact_filters(request, {'sex': 'all', 'age': 'all', 'race_ethnicity': 'all'})
    if request.options.get('value_field', 'age_adjusted_rate') != 'age_adjusted_rate':
        raise ConnectorNotReady('CDC adapter supports final all-drug age_adjusted_rate only')
    paths, values = request.paths(), {}
    for path in paths:
        for source in records(path):
            if (source['state'] not in NAMES or source['sex'] != 'Both Sexes' or source['age_group'] != 'All Ages'
                    or source['race_and_hispanic_origin'] != 'All Races-All Origins'):
                continue
            unit, year = NAMES[source['state']], int(source['year'])
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate CDC final demographic cell; no source-vintage splice')
            value = source['age_adjusted_rate']
            # Explicit suppression tokens are not numeric zeros.
            if value.strip().lower() in ('', 'suppressed', 'unreliable', 'not available'):
                value = ''
            else:
                _numeric(value)
            values[key] = dict(value=value, source=str(path))
    if not values:
        raise ConnectorError('CDC demographic labels changed or no requested rows found')
    return _finalize(request, values, paths, 'age-adjusted drug-poisoning deaths per 100000 residents',
                     ('Final public-file observations only; no provisional/final splicing.',))


def acs_b14004(request: ConnectorRequest) -> PanelData:
    exact_filters(request, {'age_min': 15, 'enrollment': 'public_college_or_graduate', 'residency': 'resident'})
    if request.options.get('value_field', 'public_enrollment') != 'public_enrollment':
        raise ConnectorNotReady('ACS summary adapter supports B14004 E003+E019 only')
    paths, values = request.paths(), {}
    for path in paths:
        match = re.search(r'acsdt1y(\d{4})-b14004', path.name, re.I) or re.search(r'(\d{4})_b14004', path.name)
        if not match:
            raise ConnectorError('ACS B14004 filename must declare the source year')
        year = int(match[1])
        if year not in request.years:
            continue
        for source in records(path, '|'):
            geography = source['GEO_ID']
            if not re.fullmatch(r'0400000US\d{2}', geography) or geography[-2:] not in STATES:
                continue
            unit = STATES[geography[-2:]]
            if unit not in request.units:
                continue
            key = unit, year
            if key in values:
                raise ConnectorError('Duplicate ACS state-year')
            a, b = float(source['B14004_E003']), float(source['B14004_E019'])
            if not math.isfinite(a) or not math.isfinite(b):
                raise ConnectorError('Invalid ACS estimate')
            value = a + b if min(a, b) >= 0 else ''
            values[key] = dict(value=value, source=str(path))
    return _finalize(request, values, paths, 'residents15plus enrolled in public college or graduate school',
                     ('Undocumented immigration status is unobserved; that target requires a different connector.',))


def published_sheet(path):
    """Read cached official XLSX values using stdlib; never evaluate formulas."""
    namespace = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheet = list(workbook.find('m:sheets', namespace))[0]
        rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        target = next(r.attrib['Target'] for r in rels if r.attrib['Id'] == rid)
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(x.itertext()) for x in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        xml = ET.fromstring(archive.read(target))
        cells, width, height = {}, 0, 0
        for source in xml.findall('m:sheetData/m:row', namespace):
            index = int(source.attrib['r'])
            height = max(height, index)
            for cell in source:
                letters = ''.join(x for x in cell.attrib['r'] if x.isalpha())
                column = 0
                for letter in letters:
                    column = 26 * column + ord(letter) - 64
                width = max(width, column)
                content = cell.find('m:v', namespace)
                kind = cell.attrib.get('t')
                value = None
                if kind == 'inlineStr':
                    value = ''.join(cell.find('m:is', namespace).itertext())
                elif content is not None:
                    value = content.text
                    if kind == 's':
                        value = shared[int(value)]
                    elif kind not in ('str', 'e'):
                        value = float(value)
                        value = int(value) if value.is_integer() else value
                cells[index, column] = value
        return sheet.attrib['name'], [[cells.get((r, c)) for c in range(1, width + 1)] for r in range(1, height + 1)]


def cps_voting(request: ConnectorRequest) -> PanelData:
    exact_filters(request, {'civilian_noninstitutional': True, 'citizens': True, 'age_min': 18})
    if request.frequency != 'biennial' or any(year % 2 for year in request.years):
        raise ConnectorNotReady('CPS voting requires observed even election years, frequency=biennial')
    paths, values = request.paths(), {}
    for path in paths:
        title, sheet = published_sheet(path)
        columns = []
        for index, year in enumerate(sheet[3]):
            if isinstance(year, int) and year in request.years:
                if sheet[4][index:index+2] != ['Total', 'Citizen']:
                    raise ConnectorError('Citizen turnout columns changed')
                columns.append((year, index + 1))
        for source in sheet[5:]:
            if source[0] not in NAMES or NAMES[source[0]] not in request.units:
                continue
            unit = NAMES[source[0]]
            for year, column in columns:
                key = unit, year
                if key in values:
                    raise ConnectorError('Duplicate state election')
                value = _numeric(source[column], 0, 100)
                values[key] = dict(value=value, source=str(path), counts={'observed_election': 1})
    expected = {(unit, year) for unit in request.units for year in request.years}
    if set(values) != expected:
        raise ConnectorNotReady('Incomplete declared election-state inventory')
    return _finalize(request, values, paths, 'published citizen18plus turnout percentage',
                     ('Published rounding retained; odd-year observations are never imputed.',))
