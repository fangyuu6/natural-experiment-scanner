"""C35 historical FARS extension; exact age denominators, no fitting/network.

The C21 1999+ artifact rows remain verbatim. Historical roles are explicitly
mapped from NHTSA P7/NM7 (manual pp298–299): 1975–81 cyclist=4,
occupants=1/2/9; 1982+ cyclists=6/7, occupants=1/2/3/9.
"""
from __future__ import annotations
import csv
import io
import hashlib
import json
from pathlib import Path
import re
import zipfile
from concurrent.futures import ProcessPoolExecutor

from .common import ConnectorError, ConnectorNotReady, STATES, NAMES, sha
from .fars import _int, _state
from .c21_fars import FILTERS, BASE as C21_BASE, MANUAL, MANUAL_URL
from .c35_core import BASE, REPO, save_panel, source_record
from .wonder import write_once

HIST = REPO / 'data/l2_pipeline/connectors_v2/fars_hist/raw'
POP = REPO / 'data/l2_pipeline/connectors_v2/census_popest_hist/raw'
SOURCE_URL = 'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars'
RACE_SEX_1970 = {'White male', 'White female', 'Black male', 'Black female',
                 'Other races male', 'Other races female'}


def historical_rows(archive, name, required):
    """Preserve selected ASCII fields; make ancillary NUL bytes explicit.

    Python 3.9 csv rejects NUL even in unused VIN truck-series labels (1979,
    1980). The visible U+2400 sentinel keeps the byte anomaly observable and
    causes fail-closed ASCII validation if it ever appears in numeric fields.
    No source bytes are changed and no selected value is substituted.
    """
    members = [n for n in archive.namelist() if Path(n).name.casefold() == name.casefold()]
    if len(members) != 1:
        raise ConnectorError('Historical FARS requires exactly one ' + name)
    with archive.open(members[0]) as raw, io.TextIOWrapper(raw, encoding='latin1', newline='') as handle:
        reader = csv.DictReader(line.replace('\x00', '\u2400') for line in handle)
        clean = lambda s: s.strip().removeprefix('\xef\xbb\xbf').upper()
        fields = [clean(k) for k in reader.fieldnames or []]
        if len(fields) != len(set(fields)) or not required <= set(fields):
            raise ConnectorError('Historical FARS required schema mismatch')
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ConnectorError('Malformed historical FARS row')
            cell = {clean(k): v.strip() for k, v in row.items()}
            numeric = required | ({'N_MOT_NO'} if 'N_MOT_NO' in cell else set())
            if any(not cell[k].isascii() for k in numeric):
                raise ConnectorError('Non-ASCII historical FARS numeric contract')
            yield cell


def parse_archive(path, year):
    path = Path(path)
    if not 1975 <= year <= 1998 or path.name != f'FARS{year}NationalCSV.zip':
        raise ConnectorError('C35 historical coding verified only for 1975–1998')
    occupant = {1, 2, 9} if year <= 1981 else {1, 2, 3, 9}
    cyclist = {4} if year <= 1981 else {6, 7}
    counts = {u: dict(occupant=0, pedalcyclist_age5_14=0, all_road=0,
        pedalcyclist_age_unknown=0, occupant_not_in_transport=0,
        pedalcyclist_other_cycle=0, crashes=0, unknown_year_present=False,
        zero_fatals_crash_present=False, optional_nonmotor_id_missing_present=False) for u in STATES.values()}
    crashes, persons = {}, set()
    with zipfile.ZipFile(path) as archive:
        for cell in historical_rows(archive, 'accident.csv', {'STATE', 'ST_CASE', 'YEAR', 'FATALS'}):
            unit = _state(cell['STATE']); key = (unit, _int(cell['ST_CASE'], 'ST_CASE'))
            declared = _int(cell['YEAR'], 'YEAR')
            # YEAR=99 is the published unknown code, observed in 1975 and 1977.
            # Calendar membership comes from the verified annual archive; retain
            # this provenance flag rather than reinterpret 99 as year 1999.
            unknown_year = declared == 99
            if key in crashes or (declared not in {year, year % 100} and not unknown_year):
                raise ConnectorError('Historical FARS duplicate crash or wrong year')
            deaths = _int(cell['FATALS'], 'FATALS')
            # Legacy national archives retain a few zero-FATALS crash rows.
            # They may contribute zero only after person-level reproduction.
            if not 0 <= deaths <= 99:
                raise ConnectorError('Invalid historical FARS fatal-crash count')
            crashes[key] = deaths
            counts[unit]['all_road'] += deaths; counts[unit]['crashes'] += 1
            counts[unit]['unknown_year_present'] |= unknown_year
            counts[unit]['zero_fatals_crash_present'] |= deaths == 0
        if not crashes or any(v['crashes'] == 0 for v in counts.values()):
            raise ConnectorError('Historical national FARS missing jurisdiction; no zero fill')
        reproduced = {k: 0 for k in crashes}
        for cell in historical_rows(archive, 'person.csv', {'STATE', 'ST_CASE', 'VEH_NO', 'PER_NO', 'INJ_SEV', 'PER_TYP', 'AGE'}):
            unit = _state(cell['STATE']); key = (unit, _int(cell['ST_CASE'], 'ST_CASE'))
            # N_MOT_NO is an auxiliary identifier, introduced1982, not a
            # measurement. One1985 pedestrian has a published blank auxiliary
            # ID; retain None in its linkage key and flag it, never infer a0.
            auxiliary = cell.get('N_MOT_NO', '0')
            auxiliary = None if auxiliary == '' else _int(auxiliary, 'N_MOT_NO')
            pk = (*key, _int(cell['VEH_NO'], 'VEH_NO'), _int(cell['PER_NO'], 'PER_NO'), auxiliary)
            if key not in crashes or pk in persons:
                raise ConnectorError('Historical FARS absent/duplicate person linkage')
            persons.add(pk)
            counts[unit]['optional_nonmotor_id_missing_present'] |= auxiliary is None
            if _int(cell['INJ_SEV'], 'INJ_SEV') != 4:
                continue
            reproduced[key] += 1
            role = _int(cell['PER_TYP'], 'PER_TYP')
            if role in occupant:
                counts[unit]['occupant'] += 1
                counts[unit]['occupant_not_in_transport'] += int(year >= 1982 and role == 3)
            if role in cyclist:
                age = _int(cell['AGE'], 'AGE'); unknown = age == 99
                if not unknown and not 0 <= age <= 97:
                    raise ConnectorError('Historical FARS unsupported age code')
                counts[unit]['pedalcyclist_age_unknown'] += int(unknown)
                if not unknown and 5 <= age <= 14:
                    counts[unit]['pedalcyclist_age5_14'] += 1
                    counts[unit]['pedalcyclist_other_cycle'] += int(year >= 1982 and role == 7)
        if reproduced != crashes:
            raise ConnectorError('Historical fatal persons do not reproduce every crash FATALS')
    return counts


def parse_pe19(path, units=tuple(STATES.values())):
    """1970s published exclusive race/sex strata and exact five-year bands."""
    total, age, seen = {}, {}, set()
    with Path(path).open(newline='', encoding='utf-8-sig') as handle:
        rows = csv.reader(handle)
        for row in rows:
            if not row or not row[0].isdigit():
                continue
            if len(row) != 22 or row[3] not in RACE_SEX_1970:
                raise ConnectorError('PE19 unexpected schema or race-sex indicator')
            year = int(row[0]); fips = row[1].zfill(2)
            if fips not in STATES or not 1970 <= year <= 1979:
                raise ConnectorError('PE19 unknown year/state')
            unit = STATES[fips]; key = (unit, year, row[3])
            if unit not in units or key in seen or row[2] not in NAMES or NAMES[row[2]] != unit:
                raise ConnectorError('PE19 duplicate or inconsistent state')
            seen.add(key); vals = [_int(v.replace(',', ''), 'population') for v in row[4:]]
            stateyear = (unit, year)
            total[stateyear] = total.get(stateyear, 0) + sum(vals)
            age[stateyear] = age.get(stateyear, 0) + vals[1] + vals[2]
    expected = {(u, y, s) for u in units for y in range(1970, 1980) for s in RACE_SEX_1970}
    if seen != expected or any(v <= 0 for v in total.values()) or any(v <= 0 for v in age.values()):
        raise ConnectorError('PE19 incomplete exact denominator census')
    return total, age


def parse_s5yr(path, units=tuple(STATES.values())):
    """First (both-sex) block, July1980–89; April1980/90 excluded."""
    total, age = {}, {}; unit = None; selected = {}; totals = {}
    for line in Path(path).read_text().splitlines():
        label = line.strip()
        if label in NAMES:
            unit = NAMES[label]
            if unit not in units or unit in selected:
                raise ConnectorError('1980s duplicate/unknown Census state')
            selected[unit] = {}
        if unit is None:
            continue
        match = re.match(r'^\s*(Total|5\s+to\s+9 years|10\s+to\s+14 years)\s+((?:\d+\s*)+)$', line)
        if not match:
            continue
        kind = re.sub(r'\s+', ' ', match[1]); vals = list(map(int, match[2].split()))
        if len(vals) != 12:
            raise ConnectorError('1980s Census date columns differ')
        if kind == 'Total':
            if unit in totals:
                raise ConnectorError('1980s Census duplicate total block')
            totals[unit] = vals[1:11]
        else:
            # First occurrence is both sexes, followed by male/female duplicates.
            selected[unit].setdefault(kind, vals[1:11])
    if set(selected) != set(units) or set(totals) != set(units) or any(len(v) != 2 for v in selected.values()):
        raise ConnectorError('1980s Census exact-band state census incomplete')
    for unit in units:
        for i, year in enumerate(range(1980, 1990)):
            total[unit, year] = totals[unit][i]
            age[unit, year] = sum(v[i] for v in selected[unit].values())
    if any(v <= 0 for v in total.values()) or any(v <= 0 for v in age.values()):
        raise ConnectorError('1980s Census nonpositive population')
    return total, age


def parse_counties(path, year, units=tuple(STATES.values())):
    """1990s state totals from exclusive county-age-race-sex-origin cells."""
    if not 1990 <= year <= 1999:
        raise ConnectorError('County population year outside 1990s')
    total, age, seen = {}, {}, set(); counties = set()
    with Path(path).open() as handle:
        for line in handle:
            fields = line.split()
            if len(fields) != 6:
                raise ConnectorError('1990s county population row schema')
            yy, geography, band, race_sex, origin, value = fields
            if len(geography) != 5 or geography[:2] not in STATES or geography[2:] == '000':
                raise ConnectorError('1990s expected county, not state/national total')
            unit = STATES[geography[:2]]
            codes = (int(band), int(race_sex), int(origin))
            if int(yy) != year % 100 or unit not in units or not (0 <= codes[0] <= 18 and 1 <= codes[1] <= 8 and codes[2] in {1, 2}):
                raise ConnectorError('1990s county population unsupported codes')
            key = (geography, *codes)
            if key in seen:
                raise ConnectorError('Duplicate 1990s county/age/race-sex/origin')
            seen.add(key); counties.add(geography); pop = _int(value, 'population')
            stateyear = (unit, year); total[stateyear] = total.get(stateyear, 0) + pop
            if codes[0] in {2, 3}:
                age[stateyear] = age.get(stateyear, 0) + pop
    # Exactly 19*8*2 disjoint cells per observed county, zero cells retained.
    if len(seen) != len(counties) * 304 or {g[:2] for g in counties} != {f for f, u in STATES.items() if u in units}:
        raise ConnectorError('1990s county population incomplete strata/state census')
    if any(v <= 0 for v in total.values()) or set(age) != set(total) or any(v <= 0 for v in age.values()):
        raise ConnectorError('1990s county population nonpositive denominator')
    return total, age, dict(year=year, counties=len(counties), exclusive_cells=len(seen),
                           county_geography_sha256=hashlib.sha256('\n'.join(sorted(counties)).encode()).hexdigest(),
                           age_codes=[2, 3], race_sex_codes=list(range(1, 9)), origin_codes=[1, 2])


def historical_population(raw=POP):
    raw = Path(raw); total, age, inventory, checks = {}, {}, [], []
    for parser, name in [(parse_pe19, 'pe-19.csv'), (parse_s5yr, 's5yr8090.txt')]:
        path = raw / name; inventory.append(source_record(path)); t, a = parser(path)
        total.update(t); age.update(a)
    for year in range(1990, 2000):
        path = raw / f'stch-icen{year}.txt'; inventory.append(source_record(path))
        t, a, check = parse_counties(path, year); total.update(t); age.update(a); checks.append(check)
    expected = {(u, y) for u in STATES.values() for y in range(1970, 2000)}
    if set(total) != expected or set(age) != expected:
        raise ConnectorError('Historical Census exact 1970–99 support differs')
    if any(c['counties'] != 3141 for c in checks) or len({c['county_geography_sha256'] for c in checks}) != 1:
        raise ConnectorError('Historical county geography census changed/incomplete')
    inventory.extend(source_record(raw / n) for n in ['stch-intercensal_layout.txt', 'co-asr-7079-layout.txt'])
    return total, age, inventory, checks


def build(latest=2024):
    if latest != 2024:
        raise ConnectorNotReady('C35 latest verified FARS annual file is 2024')
    years = list(range(1975, latest + 1)); total, age, pop_inventory, county_checks = historical_population()
    paths = [HIST / f'FARS{y}NationalCSV.zip' for y in range(1975, 1999)]
    historical_inventory = [source_record(p) for p in paths]
    with ProcessPoolExecutor(max_workers=4) as pool:
        counts = dict(zip(range(1975, 1999), pool.map(parse_archive, paths, range(1975, 1999))))
    fragments = {}; overlap = {}; boundary = {}
    for sid, filters in FILTERS.items():
        c21_path = C21_BASE / 'parsed' / ('c21_' + sid + '.json')
        c21 = json.loads(c21_path.read_text())
        if c21['series_id'] != sid or c21['population_filters'] != filters or c21['years'] != list(range(1999, 2025)):
            raise ConnectorError('C35 C21 overlap identity/year mismatch')
        cyclist = 'pedalcyclist' in sid
        field = 'pedalcyclist_age5_14' if cyclist else ('all_road' if 'all_road' in sid else 'occupant')
        by_key = {(r['unit'], r['year']): r for r in c21['rows']}
        meta_by_key = {(r['unit'], r['year']): r for r in c21['metadata']}
        if len(by_key) != 51 * 26 or set(by_key) != set(meta_by_key):
            raise ConnectorError('C21 overlap grid duplicate/incomplete')
        rows, meta = [], []
        boundary_different = 0
        for unit in sorted(STATES.values()):
            oldden = by_key[unit, 1999]['population_denominator']
            boundary_different += oldden != (age if cyclist else total)[unit, 1999]
            for year in years:
                if year >= 1999:
                    row = dict(by_key[unit, year]); m = dict(meta_by_key[unit, year])
                    # save_panel strips all value/count fields from support.
                    m['unknown_age_present'] = bool(cyclist and m.get('pedalcyclist_age_unknown'))
                    m['unknown_year_present'] = False
                else:
                    count = counts[year][unit]; denominator = (age if cyclist else total)[unit, year]
                    row = dict(unit=unit, year=year, period=str(year), frequency='annual',
                        outcome_value=count[field] / denominator * 100000,
                        outcome_unit='deaths per 100,000 residents aged 5–14' if cyclist else 'deaths per 100,000 residents',
                        outcome_scale='rate', fatality_count=count[field], population_denominator=denominator)
                    m = dict(unit=unit, state=unit, year=year, period=str(year), frequency='annual',
                        available=1, annual_usable=1, linkage_certified=int(not cyclist or count['pedalcyclist_age_unknown'] == 0),
                        denominator_available=1, unknown_age_present=bool(cyclist and count['pedalcyclist_age_unknown']),
                        unknown_year_present=bool(count['unknown_year_present']))
                    issues = []
                    if count['zero_fatals_crash_present']:
                        issues.append('zero_FATALS_crash_retained_and_reconciled')
                    if count['optional_nonmotor_id_missing_present']:
                        issues.append('published_blank_optional_N_MOT_NO')
                    m['source_issue'] = '; '.join(issues)
                m['series_break'] = ('FARS person-role recoding; pre-1982 not-in-transport occupant subtype unavailable' if year == 1982 else
                    'Census denominator boundary: existing C21 1999+ published vintage retained' if year == 1999 else '')
                rows.append(row); meta.append(m)
        new_overlap = [r for r in rows if r['year'] >= 1999]
        old_overlap = sorted(c21['rows'], key=lambda r: (r['unit'], r['year']))
        if new_overlap != old_overlap:
            raise ConnectorError('C35 altered C21 post-1999 row values/codes')
        # Record a byte proof of the canonical row arrays without exposing values.
        encode = lambda obj: json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        overlap[sid] = dict(c21_artifact_sha256=sha(c21_path), rows=51 * 26,
            c21_canonical_row_sha256=hashlib.sha256(encode(old_overlap)).hexdigest(),
            c35_canonical_row_sha256=hashlib.sha256(encode(new_overlap)).hexdigest(), equal=True)
        boundary[sid] = dict(year=1999, final_series_denominator='Existing C21 published denominator; unchanged',
            alternative='1999 county intercensal summed to states', states_with_vintage_difference=boundary_different,
            alternative_used=False, rescaled=False)
        inv = historical_inventory + pop_inventory + [source_record(c21_path)] + c21['inventory']
        unique = {i['path']: i for i in inv}
        limitations = list(c21['limitations']) + [
            '1975–1981 motor vehicle occupants PER_TYP1/2/9; pedalcyclists PER_TYP4. Code3 is pedestrian before1982. From1982 C21 roles1/2/3/9 and6/7 apply.',
            'Pre1982 occupant-not-in-transport subtype not separately identifiable; 1982 role recoding is flagged as a series break.',
            '1970s PE19 sums six exclusive race-sex strata and published5–9+10–14 bands; 1980s use published July both-sex5-year bands; 1990s sum every county,19ages,8race-sex,2origin strata.',
            'Historical denominator coverage1970–1999; final FARS series keeps all existing C211999+ denominators. County1999 alternative is compared only in metadata and never spliced into C21.',
            'Published unknown crash YEAR99 is assigned by verified annual archive membership and flagged; numeric unknown ages remain uncertified for exact-age linkage.',
            'NUL in historical ancillary VIN truck-series labels is mapped to an explicit sentinel only during parsing; selected numeric cells reject any such anomaly. Raw bytes remain unchanged.',
            'Legacy zero-FATALS crash rows contribute zero only when person fatality counts exactly reproduce them; published blank optional N_MOT_NO retains a None identifier and is flagged rather than imputed.',
        ]
        details = dict(c21['details'], person_code_vintages={'1975–1981': {'occupants': [1, 2, 9], 'pedalcyclists': [4]},
            '1982–2024': {'occupants': [1, 2, 3, 9], 'pedalcyclists': [6, 7]}},
            historical_crash_FATALS_reconciled=True, c21_overlap=overlap[sid], denominator_1999_boundary=boundary[sid],
            denominator_historical_years=list(range(1970, 2000)), suppression_applied=False)
        fragments[sid] = save_panel(sid, filters, rows, meta, list(unique.values()), limitations, details,
            'Pedalcyclists aged exactly5–14; crash state' if cyclist else
                ('All road users; crash state' if field == 'all_road' else 'All-age motor vehicle occupants; crash state'),
            SOURCE_URL, requested_year_range=[1975, 2024], legal_certification_granted=False)
    write_once(BASE / 'checks/FARS_OVERLAP.json', dict(series=overlap, outcome_association_calls=0))
    write_once(BASE / 'checks/FARS_DENOMINATORS.json', dict(historical_years=list(range(1970, 2000)),
        state_years=51 * 30, exact_age_bands=[['5–9', '10–14']], county_checks=county_checks,
        boundary_1999=boundary, back_calculation=False, outcome_association_calls=0))
    write_once(BASE / 'checks/FARS_RECONCILIATION.json', dict(years=years, every_historical_crash_FATALS_reconciled=True,
        post1999='Unchanged C21 artifacts, whose every-crash reconciliation was verified at C21',
        metadata_only_support=True, outcome_association_calls=0))
    return fragments


if __name__ == '__main__':
    build()
