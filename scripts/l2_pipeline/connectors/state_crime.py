"""No-key California DOJ UCR summary source; explicit CA geography only.

Reported offenses are distinct from FBI estimated totals. State aggregation
requires an independently certified exact agency-year roster, with citations.
Absent agency/cells remain missing; annual reporting completeness is not guessed
from a count, a state's presence or a positive outcome.
"""
from __future__ import annotations
import csv
import re
from dataclasses import replace
from .common import ConnectorError, ConnectorNotReady, exact_filters
from .published import _finalize

SOURCE_URL = 'https://data-openjustice.doj.ca.gov/sites/default/files/dataset/2026-07/Crimes_and_Clearances_with_Arson-1985-2025.csv'
CONTEXT_URL = 'https://data-openjustice.doj.ca.gov/data/crimes-and-clearances-including-arson'
LICENSE = 'CA DOJ publicly downloadable government statistics; explicit redistribution license not located; confirm rights before freeze'
FIELDS = {'violent_crime_count': 'Violent_sum', 'homicide_count': 'Homicide_sum',
    'rape_count': 'ForRape_sum', 'robbery_count': 'Robbery_sum',
    'aggravated_assault_count': 'AggAssault_sum', 'property_crime_count': 'Property_sum',
    'burglary_count': 'Burglary_sum', 'larceny_count': 'LTtotal_sum',
    'motor_vehicle_theft_count': 'VehicleTheft_sum'}


def _contract(request):
    exact_filters(request, {'series': 'reported_agency_offenses', 'state': 'CA'})
    if request.frequency != 'annual' or set(request.units) != {'CA'}:
        raise ConnectorNotReady('CA DOJ crime source is California annual only; never substitute for other states')
    if min(request.years) < 1985 or max(request.years) > 2025:
        raise ConnectorNotReady('CA DOJ fixed public source vintage covers1985–2025')
    measure = request.options.get('measure_id')
    if measure not in FIELDS:
        raise ConnectorNotReady('CA DOJ offense measure unavailable; larceny does not isolate retail theft')
    if request.options.get('value_field', FIELDS[measure]) != FIELDS[measure]:
        raise ConnectorError('CA DOJ measure/source field mismatch')
    if measure == 'rape_count' and min(request.years) < 2014 <= max(request.years):
        raise ConnectorNotReady('CA DOJ rape2014 definition break requires a separately reviewed common-definition source')
    return measure


def ca_crime_download_plan(request):
    measure = _contract(request)
    return (dict(url=SOURCE_URL, filename='Crimes_and_Clearances_with_Arson-1985-2025.csv',
        source_agency='California DOJ CJSC', license=LICENSE,
        license_status='public_access_explicit_reuse_rights_unverified', format='Annual agency offense summary CSV',
        geographic_scope=['CA'], source_definition_url=CONTEXT_URL, measure_id=measure,
        size_bytes=None, sha256=None, status='planned_not_downloaded'),)


def _rosters(request):
    rosters = request.options.get('expected_agencies_by_year')
    refs = request.options.get('agency_coverage_refs')
    if not isinstance(rosters, dict) or not isinstance(refs, list) or not refs or any(
            not isinstance(r, dict) or not r.get('source_ref') or not r.get('locator') for r in refs):
        raise ConnectorNotReady('CA state totals need independently certified agency-year roster and located coverage citations')
    selected = {}
    for year in request.years:
        roster = rosters.get(str(year), rosters.get(year))
        if not isinstance(roster, list) or not roster or len(roster) != len(set(roster)) or any(
                not isinstance(x, str) or not re.fullmatch(r'CA[0-9A-Z]{7}', x) for x in roster):
            raise ConnectorNotReady('CA certified agency roster missing/invalid for year' + str(year))
        selected[year] = set(roster)
    return selected


def ca_doj_crime(request):
    measure = _contract(request)
    rights = request.options.get('license_ref')
    if request.options.get('license_reviewed') is not True or not request.options.get('license') or not isinstance(rights, dict) or not rights.get('source_ref') or not rights.get('locator'):
        raise ConnectorNotReady('CA DOJ explicit reuse rights need reviewed license and located license_ref before construction')
    rosters = _rosters(request)
    paths, seen, groups = request.paths(), set(), {y: {} for y in request.years}
    required = {'Year', 'County', 'NCICCode', *FIELDS.values()}
    for path in paths:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            if not required <= set(reader.fieldnames or []):
                raise ConnectorError('CA DOJ source is not the annual crime/clearance summary schema')
            for source in reader:
                if None in source or any(v is None for v in source.values()):
                    raise ConnectorError('CA DOJ malformed summary row')
                try: year = int(source['Year'])
                except ValueError as exc: raise ConnectorError('CA DOJ invalid year') from exc
                if not 1985 <= year <= 2025:
                    raise ConnectorError('CA DOJ source exceeds fixed annual vintage')
                agency = source['NCICCode'].strip()
                if not re.fullmatch(r'CA[0-9A-Z]{7}', agency):
                    raise ConnectorError('CA DOJ unrecognized/non-California agency code')
                key = year, agency
                if key in seen:
                    raise ConnectorError('CA DOJ duplicate agency-year')
                seen.add(key)
                if year not in groups:
                    continue
                raw = source[FIELDS[measure]].strip()
                if raw in ('', '*', '**', 'Suppressed', 'NA', 'N/A'):
                    value = None
                elif re.fullmatch(r'\d+', raw):
                    value = int(raw)
                else:
                    raise ConnectorError('CA DOJ invalid offense count')
                groups[year][agency] = (value, str(path))
    values = {}
    for year, roster in rosters.items():
        actual = groups[year]
        if set(actual) - roster:
            raise ConnectorError('CA DOJ source agencies exceed certified annual roster')
        complete = set(actual) == roster and all(value is not None for value, _ in actual.values())
        values['CA', year] = dict(value=sum(value for value, _ in actual.values()) if complete else '',
            source=';'.join(sorted({p for _, p in actual.values()})), linkage_certified=complete,
            counts=dict(agencies_expected=len(roster), agencies_present=len(actual),
                agencies_suppressed=sum(v is None for v, _ in actual.values())))
    panel = _finalize(request, values, paths, 'reported offenses known to California police', (
        'CA agency-reported UCR offenses; police reporting and coverage affect counts; these are not FBI estimated state totals.',
        'Independent roster certification must establish reporting completeness; agency absence or suppressed cells remains missing.',
        'Rape definition changes in2014; this adapter refuses cross-break rape panels.',
        LICENSE))
    return replace(panel, inventory=tuple(dict(item, license=request.options.get('license', LICENSE),
        url=item['url'] or SOURCE_URL) for item in panel.inventory),
        details=dict(measure_id=measure, geographic_scope=['CA'],
            source_definition_url=CONTEXT_URL, source_population_filters=dict(request.filters),
            license_status='public_access_explicit_reuse_rights_unverified', outcome_association_calls=0))
