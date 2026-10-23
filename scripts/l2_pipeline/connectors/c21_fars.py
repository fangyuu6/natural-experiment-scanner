"""Prospective FARS occupant/pedalcyclist rates; outcome-only, no fitting.

National person files must reconcile every crash's FATALS. Official manual
1975–2024, P7/NM7: motor vehicle occupants 1/2/3/9; pedalcyclists 6/7.
The pre-existing fars_state adapter and its exact-bicyclist contract stay intact.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import io
import json
from pathlib import Path
import re
import zipfile

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES, PanelData, STATES, sha
from .fars import _int, _state, LICENSE
from .rates import _census_total

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'data/l2_pipeline/connectors_v2/fars'
EVIDENCE = ROOT / 'data/l2_pipeline/evidence/20261009/c21'
EXISTING = ROOT / 'data/l4/insight_inputs/downloads/gastax/nhtsa_fars_national_csv/raw'
MANUAL = ROOT / 'data/l4/insight_inputs/downloads/gastax/nhtsa_fars_documentation/raw/DOT_HS_813794_FARS_Analytical_Users_Manual_1975-2024.pdf'
MANUAL_URL = 'https://crashstats.nhtsa.dot.gov/Api/Public/ViewPublication/813794'
FILTERS = {
    'fars_motor_vehicle_occupant_fatality_rate': {'population': 'all_traffic_fatalities', 'person_type': 'occupant'},
    'fars_pedalcyclist_fatality_rate_age5_14': {'population': 'traffic_fatalities', 'age_min': 5, 'age_max': 14, 'person_type': 'pedalcyclist'},
    'fars_all_road_fatality_rate': {'population': 'all_traffic_fatalities'},
}
AGE_FILES = {
    'st-99-08.txt': 'https://www2.census.gov/programs-surveys/popest/tables/1990-2000/state/asrh/st-99-08.txt',
    'st-est00int-agesex.csv': 'https://www2.census.gov/programs-surveys/popest/datasets/2000-2010/intercensal/state/st-est00int-agesex.csv',
    'SC-EST2020-ALLDATA6.csv': 'https://www2.census.gov/programs-surveys/popest/datasets/2010-2020/state/asrh/SC-EST2020-ALLDATA6.csv',
    'sc-est2025-alldata6.csv': 'https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/state/asrh/sc-est2025-alldata6.csv',
}


def _rows(archive, name, required):
    """Numeric contract only; legacy archives contain non-UTF8 label columns.

    Latin1 is a lossless byte mapping for ancillary labels. Selected header and
    numeric cells must be ASCII, so label encoding cannot change measurement.
    """
    members = [n for n in archive.namelist() if Path(n).name.casefold() == name]
    if len(members) != 1:
        raise ConnectorError('FARS archive requires exactly one ' + name)
    with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='latin1', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = [k.strip().removeprefix('\xef\xbb\xbf').upper() for k in reader.fieldnames or []]
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ConnectorError('FARS required source schema mismatch')
        for cell in reader:
            if None in cell or any(v is None for v in cell.values()):
                raise ConnectorError('Malformed FARS row')
            normalized = {k.strip().removeprefix('\xef\xbb\xbf').upper(): v.strip() for k, v in cell.items()}
            for key in required | ({'N_MOT_NO'} if 'N_MOT_NO' in normalized else set()):
                if not normalized[key].isascii():
                    raise ConnectorError('Non-ASCII FARS numeric contract cell')
            yield normalized


def census_age5_14(raw=BASE / 'raw'):
    """Exact resident ages 5–14, both sexes/all origins; no interpolation.

    1999 published 5–9 + 10–14; 2000–2009 single ages; 2010–2019
    vintage2020 and 2020+ vintage2025 sum six mutually exclusive races.
    Vintage boundaries are retained explicitly in the output inventory.
    """
    result, inv = {}, []
    for name, url in AGE_FILES.items():
        path = raw / name
        if not path.is_file():
            raise ConnectorNotReady('Missing exact age denominator: ' + str(path))
        inv.append({'path': str(path), 'url': url, 'size_bytes': path.stat().st_size,
                    'sha256': sha(path), 'license': 'US Census Bureau federal government public domain; cite Census'})
        if name.endswith('.txt'):
            unit, selected = None, {}
            for line in path.read_text().splitlines():
                label = line.strip()
                if label in NAMES:
                    unit = NAMES[label]; selected.setdefault(unit, {})
                match = re.match(r'^\s*(5\s+to\s+9|10\s+to\s+14) years\s+(\d+)', line)
                if unit and match and len(selected[unit]) < 2:
                    # First appearance is both sexes, preceding male/female blocks.
                    selected[unit].setdefault(re.sub(r'\s+', '', match[1]), int(match[2]))
            if set(selected) != set(STATES.values()) or any(len(v) != 2 for v in selected.values()):
                raise ConnectorError('1999 Census 5–9/10–14 state inventory differs')
            result.update({(u, 1999): sum(v.values()) for u, v in selected.items()})
            continue
        first, last = (2000, 2009) if name.startswith('st-est') else ((2010, 2019) if '2020' in name else (2020, 2025))
        seen, sums = set(), {}
        with path.open(newline='', encoding='utf-8-sig') as handle:
            for cell in csv.DictReader(handle):
                fips = cell['STATE'].zfill(2)
                if fips not in STATES or cell['SEX'] != '0' or not 5 <= int(cell['AGE']) <= 14:
                    continue
                race = cell.get('RACE', '0')
                if 'RACE' in cell and (cell['ORIGIN'] != '0' or race not in {'1', '2', '3', '4', '5', '6'}):
                    continue
                key = (fips, int(cell['AGE']), race)
                if key in seen:
                    raise ConnectorError('Duplicate Census state/age/race denominator')
                seen.add(key)
                for year in range(first, last + 1):
                    stateyear = (STATES[fips], year)
                    sums[stateyear] = sums.get(stateyear, 0) + _int(cell['POPESTIMATE' + str(year)], 'population')
        expected = 51 * 10 * (1 if first == 2000 else 6)
        if len(seen) != expected or len(sums) != 51 * (last - first + 1) or any(v <= 0 for v in sums.values()):
            raise ConnectorError('Census exact age/race denominator census incomplete')
        result.update(sums)
    return result, inv


def parse_archive(path, year):
    """Return a complete crash-state census, including certified zero counts."""
    if not 1999 <= year <= 2024 or path.name != f'FARS{year}NationalCSV.zip':
        raise ConnectorError('Prospective FARS coding verified for 1999–2024 only')
    counts = {u: {'occupant': 0, 'pedalcyclist_age5_14': 0, 'all_road': 0,
                   'pedalcyclist_age_unknown': 0, 'occupant_not_in_transport': 0,
                   'pedalcyclist_other_cycle': 0, 'crashes': 0} for u in STATES.values()}
    crashes, persons = {}, set()
    with zipfile.ZipFile(path) as archive:
        for cell in _rows(archive, 'accident.csv', {'STATE', 'ST_CASE', 'YEAR', 'FATALS'}):
            u = _state(cell['STATE']); key = (u, _int(cell['ST_CASE'], 'ST_CASE'))
            if key in crashes or _int(cell['YEAR'], 'YEAR') != year:
                raise ConnectorError('FARS duplicate crash or wrong crash year')
            deaths = _int(cell['FATALS'], 'FATALS')
            if not 1 <= deaths <= 99:
                raise ConnectorError('Invalid fatal-crash death count')
            crashes[key] = deaths; counts[u]['all_road'] += deaths; counts[u]['crashes'] += 1
        if not crashes or any(v['crashes'] == 0 for v in counts.values()):
            raise ConnectorError('National FARS crash census missing a jurisdiction; no zero fill')
        reproduced = {key: 0 for key in crashes}
        for cell in _rows(archive, 'person.csv', {'STATE', 'ST_CASE', 'VEH_NO', 'PER_NO', 'INJ_SEV', 'PER_TYP', 'AGE'}):
            u = _state(cell['STATE']); key = (u, _int(cell['ST_CASE'], 'ST_CASE'))
            pk = (*key, _int(cell['VEH_NO'], 'VEH_NO'), _int(cell['PER_NO'], 'PER_NO'), _int(cell.get('N_MOT_NO', '0'), 'N_MOT_NO'))
            if key not in crashes or pk in persons:
                raise ConnectorError('FARS person linkage absent or duplicate')
            persons.add(pk)
            if _int(cell['INJ_SEV'], 'INJ_SEV') != 4:
                continue
            reproduced[key] += 1
            role = _int(cell['PER_TYP'], 'PER_TYP')
            if role in {1, 2, 3, 9}:
                counts[u]['occupant'] += 1
                counts[u]['occupant_not_in_transport'] += int(role == 3)
            if role in {6, 7}:
                age = _int(cell['AGE'], 'AGE')
                unknown = age == 99 if year <= 2008 else age in {998, 999}
                if not unknown and not 0 <= age <= (97 if year <= 2008 else 120):
                    raise ConnectorError('FARS unsupported vintage age code')
                counts[u]['pedalcyclist_age_unknown'] += int(unknown)
                if not unknown and 5 <= age <= 14:
                    counts[u]['pedalcyclist_age5_14'] += 1
                    counts[u]['pedalcyclist_other_cycle'] += int(role == 7)
        if reproduced != crashes:
            raise ConnectorError('FARS fatal persons do not reproduce every crash FATALS')
    return counts


def construct(request, catalog_data=None):
    sid = request.options.get('series_id')
    if sid not in FILTERS or dict(request.filters) != FILTERS[sid] or request.frequency != 'annual':
        raise ConnectorNotReady('Exact prospective FARS series/population/annual contract required')
    paths = request.paths()
    if len(paths) != 1:
        raise ConnectorError('Prospective FARS requires one hash-bound parsed artifact')
    name = str(paths[0].relative_to(request.base_dir)) if paths[0].is_relative_to(request.base_dir) else str(paths[0])
    supplied = request.options['paths']
    supplied = supplied if isinstance(supplied, str) else supplied[0]
    keys = {name, str(supplied), str(paths[0]), str(paths[0].resolve())}
    declared = request.options.get('source_sha256', {})
    expected = {declared[key] for key in keys if key in declared}
    if expected != {sha(paths[0])}:
        raise ConnectorError('Prospective FARS parsed artifact requires exact source SHA-256')
    data = json.loads(paths[0].read_text())
    if data['series_id'] != sid or data['population_filters'] != dict(request.filters):
        raise ConnectorError('Prospective FARS artifact series/population differs')
    if not set(request.years) <= set(data['years']):
        raise ConnectorNotReady('Prospective FARS requested year unavailable')
    def subset(rows):
        selected = tuple(r for r in rows if r['unit'] in request.units and r['year'] in request.years)
        keys = {(r['unit'], r['year']) for r in selected}
        if len(keys) != len(selected) or len(selected) != len(request.units) * len(request.years):
            raise ConnectorError('Incomplete/duplicate prospective FARS artifact cells')
        return selected
    # Bind every underlying raw and denominator input as well as parsed bytes.
    for item in data['inventory']:
        if sha(ROOT / item['path']) != item['sha256']:
            raise ConnectorError('Prospective FARS upstream source changed: ' + item['path'])
    return PanelData(subset(data['rows']), subset(data['metadata']), tuple(data['inventory']),
                     tuple(data['limitations']), data['details'])


def build(latest=2024):
    if latest != 2024:
        raise ConnectorNotReady('Latest verified FARS release is 2024; audit next vintage first')
    years = list(range(1999, latest + 1)); units = tuple(STATES.values())
    request = ConnectorRequest({}, {}, tuple(years), units, 'annual', ROOT)
    total, total_inv = _census_total(request)
    age, age_inv = census_age5_14()
    inventory = list(total_inv) + age_inv
    parsed = {}
    source_paths = []
    for year in years:
        name = f'FARS{year}NationalCSV.zip'; path = EXISTING / name
        if not path.is_file():
            path = BASE / 'raw' / name
        source_paths.append(path)
        inventory.append({'path': str(path), 'url': f'https://static.nhtsa.gov/nhtsa/downloads/FARS/{year}/National/{name}',
                          'size_bytes': path.stat().st_size, 'sha256': sha(path), 'license': LICENSE,
                          'status': 'downloaded_c21' if year == 2024 else 'reused_existing'})
    with ProcessPoolExecutor(max_workers=4) as pool:
        for year, counts in zip(years, pool.map(parse_archive, source_paths, years)):
            parsed[year] = counts
            print('Reconciled FARS', year, flush=True)
    inventory.append({'path': str(MANUAL), 'url': MANUAL_URL, 'size_bytes': MANUAL.stat().st_size,
                      'sha256': sha(MANUAL), 'license': LICENSE, 'status': 'reused_existing'})
    for item in inventory:
        item['path'] = str(Path(item['path']).relative_to(ROOT))
    limitations = [
        'Crash-state deaths within 30 days in public trafficway crashes involving a motor vehicle; numerator is not residence based.',
        'Resident Census July population denominator; rates per 100,000, not distance travelled or cycling exposure.',
        'Occupants include all motor vehicle types (including motorcycles) and PER_TYP=3 not-in-transport occupants under official C5 definition; no passenger-vehicle-only restriction.',
        'Pedalcyclists include PER_TYP=6 and 7, other cyclists before 2022 / other pedalcyclists from 2022; ages exactly 5–14. Wider legal coverage can dilute the effect.',
        'Unknown pedalcyclist age excluded and counted in support; such state-years are available but not certified for exact age linkage.',
        '1999 age population vintage1999; 2000–2009 intercensal; 2010–2019 age vintage2020; 2020–2024 age vintage2025. No interpolation or age-bin approximation.',
    ]
    fragments = {}
    BASE.joinpath('parsed').mkdir(parents=True, exist_ok=True)
    for sid, filters in FILTERS.items():
        cyclist = 'pedalcyclist' in sid
        field = 'pedalcyclist_age5_14' if cyclist else ('all_road' if 'all_road' in sid else 'occupant')
        rows, meta = [], []
        for unit in sorted(units):
            for year in years:
                count = parsed[year][unit]; denominator = (age if cyclist else total)[unit, year]
                row = {'unit': unit, 'year': year, 'period': str(year), 'frequency': 'annual',
                       'outcome_value': count[field] / denominator * 100000,
                       'outcome_unit': 'deaths per 100,000 residents aged 5–14' if cyclist else 'deaths per 100,000 residents',
                       'outcome_scale': 'rate', 'fatality_count': count[field], 'population_denominator': denominator}
                rows.append(row)
                meta.append({'unit': unit, 'state': unit, 'year': year, 'period': str(year), 'frequency': 'annual',
                             'available': 1, 'annual_usable': 1, 'linkage_certified': int(not cyclist or count['pedalcyclist_age_unknown'] == 0),
                             'fatality_count': count[field], 'denominator_available': 1, **count})
        output = {'series_id': sid, 'population_filters': filters, 'years': years, 'rows': rows, 'metadata': meta,
                  'inventory': inventory, 'limitations': limitations,
                  'details': {'source_definition_url': MANUAL_URL, 'coding_manual_sha256': sha(MANUAL),
                              'person_codes': [6, 7] if cyclist else ([1, 2, 3, 9] if field == 'occupant' else 'all'),
                              'fatal_injury_code': 4, 'crash_FATALS_reconciled': True,
                              'outcome_association_calls': 0, 'interpolation': False, 'denominator_back_calculation': False}}
        path = BASE / 'parsed' / ('c21_' + sid + '.json')
        path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
        for label, records in [('panel', rows), ('metadata', meta)]:
            with path.with_suffix('.' + label + '.csv').open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)
        fragments[sid] = {'series_id': sid, 'adapter': 'c21_fars', 'years': years, 'geography': 'US states plus DC; crash state',
                          'population': ('Pedalcyclists aged 5–14; crash state' if cyclist else ('All road users; crash state' if field == 'all_road' else 'All-age motor vehicle occupants; crash state')),
                          'population_filters': filters, 'units': rows[0]['outcome_unit'], 'license': LICENSE,
                          'frequency': ['annual'], 'source_url': 'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars',
                          'release_lag': 'latest verified annual census2024', 'public_available': True,
                          'status': 'implemented_local_adapter', 'source_validation_required': True,
                          'parsed_path': str(path.relative_to(ROOT)), 'parsed_sha256': sha(path),
                          'panel_path': str(path.relative_to(ROOT)), 'panel_sha256': sha(path),
                          'outcome_scale': 'rate', 'requested_year_range': [1999, 2024],
                          'legal_certification_granted': False}
    (EVIDENCE / 'fars_catalog_fragment.json').write_text(json.dumps({'series': fragments}, indent=2) + '\n')
    (EVIDENCE / 'fars_input_inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
    (EVIDENCE / 'fars_build_receipt.json').write_text(json.dumps({'years': years, 'state_years_per_series': len(years)*51,
          'series': {sid: {'path': s['parsed_path'], 'sha256': s['parsed_sha256']} for sid, s in fragments.items()},
          'raw_crash_person_reconciliation': 'every crash in every vintage', 'outcome_association_calls': 0}, indent=2) + '\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--latest', type=int, default=2024)
    args = parser.parse_args(argv); build(args.latest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
