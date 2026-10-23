"""Hospital nurse hours from declared local CPS files; no treatment inputs.

PEHRACTT is actual hours at all jobs among people at work (PEMLR=1).
Job-one occupation/industry identify the hospital nurse population. The RN code
before 2011 includes advanced-practice nurses; the break is disclosed rather
than claiming a retrospectively constant population.
"""
from __future__ import annotations

import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData, STATES, inventory, metadata, reject_filters, row, sha
from .cps import LOC, MONTHS

# Official public layout epochs, independently checked for the selected fields.
EPOCHS = (
    (199401, '1994_jan94_mar94_dd.txt'), (199404, '1995_apr94_may95_dd.txt'),
    (199506, '1995_jun95_aug95_dd.txt'), (199509, '1995_sep95_dec97_dd.txt'),
    (199801, '2002_jan98dd.asc'), (200301, '2003_jan03dd.txt'),
    (200405, '2004_may04dd.txt'), (200508, '2005_augnov05dd.txt'),
    (200701, '2007_jan07dd.txt'), (200901, '2009_jan09dd.txt'),
    (201001, '2010_jan10dd.txt'), (201205, '2012_may12dd.txt'),
    (201301, '2013_January_2013_Record_Layout.txt'),
    (201401, '2014_January_2014_Record_Layout.txt'),
    (201501, '2015_January_2015_Record_Layout.txt'),
    (201701, '2017_January_2017_Record_Layout.txt'),
    (202001, '2020_2020_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
    (202101, '2021_2021_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
    (202201, '2022_2022_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
    (202301, '2023_2023_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
    (202401, '2024_2024_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
    (202405, '2024_May_2024_Basic_CPS_Public_Use_Record_Layout.txt'),
    (202501, '2025_2025_Basic_CPS_Public_Use_Record_Layout_plus_IO_Code_list.txt'),
)
CODE_SOURCES = (
    'https://www2.census.gov/programs-surveys/nychvs/technical-documentation/code-lists/occupation/1990occ.pdf',
    'https://www2.census.gov/programs-surveys/demo/guidance/industry-occupation/1990-census-sic-codes.pdf',
    'https://www.bls.gov/tus/iocodes/census02iocodes.pdf',
    'https://www.bls.gov/cps/cenocc2010.htm',
    'https://www.bls.gov/cps/cpsoccind.htm',
    'https://www.bls.gov/news.release/archives/empsit_04032020.htm',
)


def nurse_codes(year: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not 1994 <= year <= 2025:
        raise ConnectorNotReady('Nurse CPS codes/layout not audited for this year')
    return ((95,) if year < 2003 else (3130,) if year < 2011 else (3255,),
            (831,) if year < 2003 else (8190,) if year < 2020 else (8191, 8192))


def nurse_locations(year: int) -> dict[str, tuple[int, int]]:
    nurse_codes(year)
    return dict(LOC, hours=(247, 249), industry=(436, 438) if year < 2003 else (856, 859),
                occupation=(439, 441) if year < 2003 else (860, 863))


def population(request: ConnectorRequest) -> None:
    reject_filters(request, {'age_min', 'age_max', 'sex', 'civilian_noninstitutional', 'occupation', 'industry'})
    if (request.filters.get('occupation') != 'registered_nurses'
            or request.filters.get('industry') != 'hospitals'
            or request.filters.get('civilian_noninstitutional') is not True):
        raise ConnectorNotReady('Hours target requires hospital registered_nurses and civilian_noninstitutional=true')
    lo, hi = request.filters.get('age_min'), request.filters.get('age_max')
    if type(lo) is not int or type(hi) is not int or not 16 <= lo <= hi <= 79:
        raise ConnectorNotReady('Nurse hours requires fixed ages 16–79')
    if request.filters.get('sex', 'all') not in ('all', 'male', 'female'):
        raise ConnectorNotReady('Unknown nurse sex target')
    if request.options.get('value_field') not in ('weekly_hours', 'long_hours_percent'):
        raise ConnectorNotReady('Nurse adapter supports weekly_hours or long_hours_percent')
    if request.frequency not in ('annual', 'monthly'):
        raise ConnectorNotReady('Nurse adapter supports annual or monthly frequency')
    threshold = request.options.get('long_hours_threshold', 40)
    if type(threshold) is not int or not 1 <= threshold <= 197:
        raise ConnectorError('Long-hours threshold must be an integer from 1 to 197')


def audit_dictionaries(request: ConnectorRequest) -> list[dict[str, Any]]:
    """Validate selected offsets at every used epoch, retaining source hashes."""
    from ..source_packs import scan_text
    root = request.options.get('dictionary_root')
    if not root:
        raise ConnectorNotReady('Nurse CPS requires declared dictionary_root for audited public record layouts')
    root = request.base_dir / root
    selected = set()
    for year in request.years:
        nurse_codes(year)
        for month in range(1, 13):
            selected.add(max((e for e in EPOCHS if e[0] <= year * 100 + month), key=lambda e: e[0]))
    audits = []
    for start, name in sorted(selected):
        path = root / name
        # Apply the same public-source/credential path guard used for outcomes.
        options = dict(request.options, paths=[str(path)])
        guarded = ConnectorRequest(options, request.filters, request.years, request.units, request.frequency, request.base_dir)
        path = guarded.paths()[0]
        text = path.read_text(errors='replace').replace('\ufffd', '-')
        initial = scan_text(text)
        if initial['suspicious']:
            raise ConnectorNotReady('Dictionary initial check found suspicious assistant instructions')
        locations = nurse_locations(start // 100)
        found = {}
        for key, field in [('hours', 'PEHRACTT'), ('industry', 'PEIO1ICD'), ('occupation', '(?:PEIO1OCD|PTIO1OCD)')]:
            match = re.search(r'(?:^|\n|\f)\s*' + field + r'\s+\d+[^\n]*?\(?\s*(\d+)(?:\s*[-–]\s*|\s{2,})(\d+)\s*\)?\s*(?:\n|$)', text)
            if match:
                bounds = tuple(map(int, match.groups()))
            else:
                match = re.search(r'^D\s+' + field + r'\s+(\d+)\s+(\d+)\s*$', text, re.M)
                bounds = (int(match[2]), int(match[2]) + int(match[1]) - 1) if match else None
            if bounds != locations[key]:
                raise ConnectorError('Nurse field layout mismatch: ' + name + ':' + key)
            found[key] = bounds
        audits.append(dict(path=str(path), sha256=sha(path), start_year_month=start,
                           selected_locations=found, initial_check=initial))
    return audits


def parse_month(path: Path, year: int, month: int, request: ConnectorRequest) -> list[dict[str, Any]]:
    import numpy as np
    occupation, industry = nurse_codes(year)
    locations = nurse_locations(year)
    fields = ['state', 'age', 'sex', 'person_type', 'employment', 'weight', 'hours', 'industry', 'occupation']
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith('/')]
        if len(members) != 1:
            raise ConnectorError('CPS nurse ZIP requires one data member')
        raw = archive.read(members[0])
    size = raw.find(b'\n') + 1
    if size <= max(locations[k][1] for k in fields) or len(raw) % size:
        raise ConnectorError('Nonrectangular or short CPS nurse records')
    dtype = np.dtype({'names': fields, 'formats': ['S' + str(locations[k][1] - locations[k][0] + 1) for k in fields],
                      'offsets': [locations[k][0] - 1 for k in fields], 'itemsize': size})
    data = np.frombuffer(raw, dtype=dtype)
    try:
        values = {k: data[k].astype(np.int64) for k in fields}
    except ValueError as exc:
        raise ConnectorError('Invalid CPS nurse integer field') from exc
    target = ((values['person_type'] == 2) & (values['age'] >= request.filters['age_min'])
              & (values['age'] <= request.filters['age_max']) & np.isin(values['occupation'], occupation)
              & np.isin(values['industry'], industry) & (values['employment'] == 1))
    sex = request.filters.get('sex', 'all')
    if sex != 'all':
        target &= values['sex'] == (1 if sex == 'male' else 2)
    output = []
    for fips, unit in STATES.items():
        if unit not in request.units:
            continue
        sample = target & (values['state'] == int(fips))
        valid = sample & (values['weight'] > 0) & (values['hours'] >= 0) & (values['hours'] <= 198)
        weights = values['weight'][valid].astype(float) / 10000
        hours = values['hours'][valid]
        output.append(dict(unit=unit, year=year, month=month, n_target=int(sample.sum()), n_valid=int(valid.sum()),
                           n_missing=int(sample.sum() - valid.sum()), weight_sum=float(weights.sum()),
                           hours_weight_sum=float((weights * hours).sum()),
                           long_hours_weight_sum=float(weights[hours > request.options.get('long_hours_threshold', 40)].sum()),
                           linkage_certified=1))
    return output


def annualize(monthly: list[dict[str, Any]], request: ConnectorRequest) -> PanelData:
    groups = defaultdict(list)
    seen = set()
    for record in monthly:
        key = record['unit'], int(record['year']), int(record['month'])
        if key in seen:
            raise ConnectorError('Duplicate nurse CPS state/month')
        seen.add(key)
        if key[0] in request.units and key[1] in request.years:
            groups[key[:2]].append(record)
    output, support = [], []
    strict = request.options.get('missing_person_rule', 'exclude_and_count')
    if strict not in ('exclude_and_count', 'require_all_valid'):
        raise ConnectorError('Unknown CPS missing_person_rule')
    for unit in sorted(request.units):
        for year in request.years:
            group = groups[unit, year]
            periods = [[r for r in group if int(r['month']) == m] for m in range(1, 13)] if request.frequency == 'monthly' else [group]
            for i, period in enumerate(periods, 1):
                total = sum(r['weight_sum'] for r in period)
                valid = sum(r['n_valid'] for r in period)
                missing = sum(r['n_missing'] for r in period)
                complete = len(period) == (1 if request.frequency == 'monthly' else 12)
                usable = complete and total > 0 and all(r['n_valid'] > 0 for r in period)
                if strict == 'require_all_valid':
                    usable &= missing == 0
                measure = request.options['value_field']
                numerator = sum(r['hours_weight_sum' if measure == 'weekly_hours' else 'long_hours_weight_sum'] for r in period)
                value = (numerator / total * (1 if measure == 'weekly_hours' else 100)) if usable else ''
                record = row(unit, year, value, 'actual hours at all jobs per hospital nurse at work' if measure == 'weekly_hours' else 'percent hospital nurses at work above declared actual-hour threshold',
                             request.frequency, i if request.frequency == 'monthly' else None, weight_sum=total, n_person_months=valid)
                output.append(record)
                support.append(metadata(record, usable, months_present=len(period), complete_period=int(complete),
                                        n_target=sum(r['n_target'] for r in period), n_valid=valid, n_missing=missing, weight_sum=total))
    return PanelData(tuple(output), tuple(support), limitations=(
        'Before January 2011 the RN occupation includes advanced-practice nurses; later RN codes exclude separately coded advanced-practice nurses. If separately registered, a 2011+ sensitivity would use the narrower definition.',
        'Actual hours include all jobs; first-job occupation and industry identify hospital RNs at work. Mandatory overtime is not separately observed.',
    ), details=dict(aggregation='sum monthly weighted actual hours / sum valid person-month weights',
                    at_work_rule='PEMLR=1; valid PEHRACTT 0–198; absence is never imputed zero',
                    weight_divisor=10000, long_hours_threshold=request.options.get('long_hours_threshold', 40),
                    code_sources=CODE_SOURCES, code_epochs=[dict(start=1994, occupation=[95], industry=[831]),
                    dict(start=2003, occupation=[3130], industry=[8190]), dict(start=2011, occupation=[3255], industry=[8190]),
                    dict(start=2020, occupation=[3255], industry=[8191, 8192])]))


def cps_nurse_hours(request: ConnectorRequest) -> PanelData:
    population(request)
    audits = audit_dictionaries(request)
    monthly, dates = [], set()
    paths = request.paths()
    for path in paths:
        match = re.fullmatch(r'(\d{4})_([a-z]{3})\.zip', path.name)
        if not match or match[2] not in MONTHS:
            raise ConnectorError('CPS file names must be YYYY_mon.zip')
        year, month = int(match[1]), MONTHS.index(match[2]) + 1
        if (year, month) in dates:
            raise ConnectorError('Duplicate CPS source month')
        dates.add((year, month))
        if year in request.years:
            monthly.extend(parse_month(path, year, month, request))
    panel = annualize(monthly, request)
    return PanelData(panel.rows, panel.metadata, tuple(inventory(p, request) for p in paths),
                     panel.limitations, dict(panel.details, dictionary_audit=audits))
