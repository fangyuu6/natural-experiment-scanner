"""NHTSA national FARS CSV archives; fatal crashes/persons, no treatment input.

1982–2022 audited field/coding contract. The archive remains immutable. Person
fatalities join to the crash census and must reproduce FATALS before age/role
selection. Unknown ages remain counted in support, never assigned to an age bin.
"""
from __future__ import annotations
import csv
import io
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from .common import ConnectorError, ConnectorNotReady, STATES, exact_filters
from .public_annual import _licensed
from .published import _finalize

SOURCE_YEARS = (1982, 2022)
MEASURES = {'traffic_deaths_count', 'fatal_crashes_count', 'child_traffic_deaths_count', 'teen_traffic_deaths_count', 'child_bicycle_deaths_count'}
LICENSE = 'US federal government public domain; cite NHTSA'
MANUAL_URL = 'https://rosap.ntl.bts.gov/view/dot/78035/dot_78035_DS1.pdf'


def _contract(request):
    if request.frequency != 'annual' or min(request.years) < SOURCE_YEARS[0] or max(request.years) > SOURCE_YEARS[1]:
        raise ConnectorNotReady('FARS national CSV audited annual range1982–2022 only')
    measure = request.options.get('measure_id')
    if measure not in MEASURES:
        raise ConnectorNotReady('Unsupported FARS fatality measure')
    filters = request.filters
    if measure in ('traffic_deaths_count', 'fatal_crashes_count'):
        exact_filters(request, {'population': 'all_traffic_fatalities'})
        return measure, None
    required = {'population', 'age_min', 'age_max'}
    if set(filters) not in (required, required | {'person_type'}) or filters.get('population') != 'traffic_fatalities':
        raise ConnectorNotReady('FARS age count requires exact traffic_fatalities age_min/max and optional person_type')
    lo, hi = filters.get('age_min'), filters.get('age_max')
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 96:
        raise ConnectorNotReady('FARS age range requires integer bounds0–96; top-coded elderly ages unavailable')
    if (measure == 'child_traffic_deaths_count' and hi > 14) or (measure == 'teen_traffic_deaths_count' and not 15 <= lo <= hi <= 20):
        raise ConnectorError('FARS child/teen measure does not bind declared age range')
    role = filters.get('person_type', 'all')
    if measure == 'child_bicycle_deaths_count' and (hi > 17 or role != 'bicyclist'):
        raise ConnectorError('FARS child bicycle measure requires age0–17 and exact bicyclist role')
    if role not in ('all', 'driver', 'occupant', 'bicyclist'):
        raise ConnectorNotReady('FARS person_type supports all, driver, occupant or exact bicyclist only')
    return measure, (lo, hi, role)


def fars_download_plan(request):
    measure, ages = _contract(request)
    return tuple(dict(url=f'https://static.nhtsa.gov/nhtsa/downloads/FARS/{year}/National/FARS{year}NationalCSV.zip',
        filename=f'FARS{year}NationalCSV.zip', license=LICENSE, source_agency='NHTSA FARS',
        format='National CSV ZIP; accident plus person when age selected', measure_id=measure,
        required_members=['accident.csv'] + (['person.csv'] if ages else []),
        size_bytes=None, sha256=None, status='planned_not_downloaded') for year in request.years)


def _int(raw, label):
    value = str(raw).strip()
    if not re.fullmatch(r'\d+', value):
        raise ConnectorError('FARS invalid integer ' + label)
    return int(value)


def _rows(archive, name, required):
    members = [n for n in archive.namelist() if Path(n).name.casefold() == name]
    if len(members) != 1:
        raise ConnectorError('FARS national archive needs exactly one ' + name)
    with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = [str(k).upper().strip() for k in reader.fieldnames or []]
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ConnectorError('FARS required source schema mismatch for ' + name)
        for record in reader:
            if None in record or any(v is None for v in record.values()):
                raise ConnectorError('FARS malformed CSV row')
            yield {str(k).upper().strip(): v.strip() for k, v in record.items()}


def _state(raw):
    fips = _int(raw, 'STATE')
    if str(fips).zfill(2) not in STATES:
        raise ConnectorError('FARS unknown crash state')
    return STATES[str(fips).zfill(2)]


def fars_state(request):
    measure, age_range = _contract(request)
    request = _licensed(request, 'NHTSA FARS')
    paths, values, seen_years = request.paths(), {}, set()
    for path in paths:
        match = re.fullmatch(r'FARS(\d{4})NationalCSV.zip', path.name, re.I)
        if not match:
            raise ConnectorError('FARS requires original national ZIP filename bound to crash year')
        year = int(match[1])
        if year not in request.years or year in seen_years:
            raise ConnectorError('FARS unexpected or duplicate archive year')
        seen_years.add(year)
        crashes, totals = {}, {u: {'fatalities': 0, 'crashes': 0, 'age_unknown': 0} for u in request.units}
        with zipfile.ZipFile(path) as archive:
            for cell in _rows(archive, 'accident.csv', {'STATE', 'ST_CASE', 'YEAR', 'FATALS'}):
                unit, crash = _state(cell['STATE']), _int(cell['ST_CASE'], 'ST_CASE')
                key = unit, crash
                source_year = _int(cell['YEAR'], 'YEAR')
                # Original pre-1998 releases encode YEAR with two digits.
                if key in crashes or source_year not in ({year, year % 100} if year < 1998 else {year}):
                    raise ConnectorError('FARS duplicate crash or mismatched crash year')
                deaths = _int(cell['FATALS'], 'FATALS')
                if not 1 <= deaths <= 99:
                    raise ConnectorError('FARS fatal crash must have1–99 fatalities')
                crashes[key] = deaths
                if unit in totals:
                    totals[unit]['fatalities'] += deaths; totals[unit]['crashes'] += 1
            if not crashes:
                raise ConnectorError('FARS national crash census is empty; no zero fill')
            if age_range:
                lo, hi, role = age_range
                person_keys, reproduced = set(), {key: 0 for key in crashes}
                selected = {u: 0 for u in request.units}
                for person in _rows(archive, 'person.csv', {'STATE', 'ST_CASE', 'VEH_NO', 'PER_NO', 'AGE', 'PER_TYP', 'INJ_SEV'}):
                    unit, crash = _state(person['STATE']), _int(person['ST_CASE'], 'ST_CASE')
                    key = unit, crash
                    if key not in crashes:
                        raise ConnectorError('FARS person absent from national crash census')
                    pk = (*key, _int(person['VEH_NO'], 'VEH_NO'), _int(person['PER_NO'], 'PER_NO'),
                        _int(person.get('N_MOT_NO', '0'), 'N_MOT_NO'))
                    if pk in person_keys:
                        raise ConnectorError('FARS duplicate person within crash')
                    person_keys.add(pk)
                    if _int(person['INJ_SEV'], 'INJ_SEV') != 4:
                        continue
                    reproduced[key] += 1
                    person_type = _int(person['PER_TYP'], 'PER_TYP')
                    roles = {'driver': {1}, 'occupant': {1, 2, 3, 9}, 'bicyclist': {6}}
                    if role != 'all' and person_type not in roles[role]:
                        continue
                    age = _int(person['AGE'], 'AGE')
                    unknown = age == 99 if year <= 2008 else age in (998, 999)
                    if not unknown and not 0 <= age <= (97 if year <= 2008 else 120):
                        raise ConnectorError('FARS unsupported age code for vintage')
                    if unit in totals:
                        if unknown: totals[unit]['age_unknown'] += 1
                        elif lo <= age <= hi: selected[unit] += 1
                if reproduced != crashes:
                    raise ConnectorError('FARS person fatalities do not reproduce crash FATALS; source incomplete')
        for unit in request.units:
            counts = totals[unit]
            value = selected[unit] if age_range else counts['crashes' if measure == 'fatal_crashes_count' else 'fatalities']
            values[unit, year] = dict(value=value, source=str(path),
                linkage_certified=not age_range or counts['age_unknown'] == 0,
                counts=dict(crashes_present=counts['crashes'], fatal_person_age_unknown=counts['age_unknown'],
                    unknown_age_excluded=int(bool(age_range)), national_census_file=1))
    panel = _finalize(request, values, paths, 'fatal crashes' if measure == 'fatal_crashes_count' else 'traffic deaths', (
        'Crash state, not residence; census of public-trafficway motor vehicle crashes with a death within30 days.',
        'Age unknown fatalities are excluded from age bins and counted in support; any unknown age blocks certified age linkage.',
        'Bicyclist uses exact PER_TYP=6; motor-vehicle collision fatalities only, excluding other cyclists and non-motor-vehicle crashes.',
        'This adapter does not measure nonfatal injury, crash risk per mile, alcohol involvement or protective-device use.'))
    return replace(panel, details=dict(measure_id=measure, source_years=sorted(seen_years),
        population_filters=dict(request.filters), source_definition_url=MANUAL_URL,
        outcome_association_calls=0, interpolation=False))
