"""Generalized batch1/2/3 CPS construction from immutable local monthly ZIPs.

These byte locations copy the batch1 audited CPS dictionary crosswalk. Unknown
filters fail closed. Ratios pool monthly weights, never annual percentages.
"""
from __future__ import annotations

import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import (ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData,
                     STATES, exact_filters, inventory, metadata, records, reject_filters, row)

MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()
LOC = {'state': (93, 94), 'age': (122, 123), 'sex': (129, 130),
       'person_type': (161, 162), 'employment': (180, 181),
       'weight': (613, 622), 'prchld': (633, 634)}


def population(request: ConnectorRequest) -> None:
    reject_filters(request, {'age_min', 'age_max', 'sex', 'civilian_noninstitutional', 'own_children'})
    if request.filters.get('civilian_noninstitutional') is not True:
        raise ConnectorNotReady('CPS target must declare civilian_noninstitutional=true')
    lo, hi = request.filters.get('age_min'), request.filters.get('age_max')
    if type(lo) is not int or type(hi) is not int or not 15 <= lo <= hi <= 79:
        raise ConnectorNotReady('CPS employment requires fixed age_min/max between 15 and 79')
    if request.filters.get('sex', 'all') not in ('all', 'male', 'female'):
        raise ConnectorNotReady('CPS sex filter must be all, male, or female')
    if 'own_children' in request.filters and request.filters['own_children'] is not True:
        raise ConnectorNotReady('Only own_children=true is implemented; missing links are never childless')
    if request.options.get('value_field', 'employment_percent') != 'employment_percent':
        raise ConnectorNotReady('CPS raw adapter currently constructs employment_percent only')


def parse_month(path: Path, year: int, month: int, request: ConnectorRequest) -> list[dict[str, Any]]:
    import numpy as np
    if not 1994 <= year <= 2025:
        raise ConnectorNotReady('CPS layout not audited for this year')
    if request.filters.get('own_children') and year < 2000:
        raise ConnectorNotReady('Official own-child linkage primary path requires year >= 2000')
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith('/')]
        if len(members) != 1:
            raise ConnectorError('CPS ZIP requires one data member')
        raw = archive.read(members[0])
    size = raw.find(b'\n') + 1
    if size < 635 or len(raw) % size:
        raise ConnectorError('Nonrectangular CPS fixed-width records')
    fields = ['state', 'age', 'sex', 'person_type', 'employment', 'weight']
    if request.filters.get('own_children'):
        fields.append('prchld')
    dtype = np.dtype({'names': fields,
                      'formats': ['S' + str(LOC[k][1] - LOC[k][0] + 1) for k in fields],
                      'offsets': [LOC[k][0] - 1 for k in fields], 'itemsize': size})
    data = np.frombuffer(raw, dtype=dtype)
    try:
        values = {k: data[k].astype(np.int64) for k in fields}
    except ValueError as exc:
        raise ConnectorError('Invalid CPS integer field') from exc
    target = ((values['person_type'] == 2) & (values['age'] >= request.filters['age_min'])
              & (values['age'] <= request.filters['age_max']))
    sex = request.filters.get('sex', 'all')
    if sex != 'all':
        target &= values['sex'] == (1 if sex == 'male' else 2)
    # Preserve linkage missing counts among the age/sex target before selecting parents.
    child_base = target.copy()
    child_missing = ((values['prchld'] < -1) | (values['prchld'] > 15)) if 'prchld' in values else np.zeros(len(data), bool)
    if 'prchld' in values:
        target &= (values['prchld'] >= 1) & (values['prchld'] <= 15)
    result = []
    for fips, unit in STATES.items():
        if unit not in request.units:
            continue
        state = values['state'] == int(fips)
        sample = target & state
        valid = sample & (values['weight'] > 0) & np.isin(values['employment'], range(1, 8))
        weights = values['weight'][valid].astype(float) / 10000
        missing_children = int((child_base & state & child_missing).sum())
        result.append(dict(unit=unit, year=year, month=month, n_target=int(sample.sum()),
                           n_valid=int(valid.sum()), n_missing=int(sample.sum() - valid.sum()),
                           n_child_missing=missing_children, linkage_certified=int(missing_children == 0),
                           weight_sum=float(weights.sum()),
                           employed_weight_sum=float(weights[np.isin(values['employment'][valid], [1, 2])].sum())))
    return result


def annualize(monthly: list[dict[str, Any]], request: ConnectorRequest) -> PanelData:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for record in monthly:
        key = record['unit'], int(record['year']), int(record['month'])
        if key in seen:
            raise ConnectorError('Duplicate CPS state/month')
        seen.add(key)
        if key[0] in request.units and key[1] in request.years:
            groups[key[:2]].append(record)
    output, support = [], []
    outcome_unit = 'employment percentage; civilian noninstitutional resident person-months'
    strict = request.options.get('missing_person_rule', 'exclude_and_count')
    if strict not in ('exclude_and_count', 'require_all_valid'):
        raise ConnectorError('Unknown CPS missing_person_rule')
    def construct(unit: str, year: int, group: list[dict[str, Any]], month: int | None = None) -> None:
        total = sum(float(r['weight_sum']) for r in group)
        employed = sum(float(r['employed_weight_sum']) for r in group)
        valid = sum(int(r['n_valid']) for r in group)
        missing = sum(int(r['n_missing']) for r in group)
        complete = len(group) == (1 if month is not None else 12)
        if month is None:
            complete &= {int(r['month']) for r in group} == set(range(1, 13))
        linked = bool(group) and all(int(r.get('linkage_certified', 1)) and not int(r.get('n_child_missing', 0)) for r in group)
        usable = complete and linked and total > 0 and all(int(r['n_valid']) > 0 for r in group)
        if strict == 'require_all_valid':
            usable &= missing == 0
        share = employed / total if usable else None
        if share is not None and not 0 <= share <= 1:
            raise ConnectorError('Invalid CPS weighted employment share')
        record = row(unit, year, 100 * share if share is not None else '', outcome_unit,
                     request.frequency, month, employment_share=share if share is not None else '',
                     employment_percent=100 * share if share is not None else '',
                     weight_sum=total, n_person_months=valid)
        output.append(record)
        support.append(metadata(record, usable, linked, months_present=len(group),
                                complete_period=int(complete), n_target=sum(int(r['n_target']) for r in group),
                                n_valid=valid, n_missing=missing,
                                n_child_missing=sum(int(r.get('n_child_missing', 0)) for r in group),
                                weight_sum=total))
    if request.frequency not in ('annual', 'monthly'):
        raise ConnectorNotReady('CPS raw/aggregate adapter requires annual or monthly frequency')
    for unit in sorted(request.units):
        for year in request.years:
            group = groups[unit, year]
            if request.frequency == 'monthly':
                for month in range(1, 13):
                    construct(unit, year, [r for r in group if int(r['month']) == month], month)
            else:
                construct(unit, year, group)
    return PanelData(tuple(output), tuple(support), details={
        'aggregation': 'sum monthly employed weights / sum monthly valid labor-status weights',
        'weight_divisor': 10000, 'missing_person_rule': strict,
        'person_months_are_repeated_cross_sections': True})


def cps_monthly(request: ConnectorRequest) -> PanelData:
    if request.filters.get('occupation') == 'registered_nurses':
        from .cps_nurse import cps_nurse_hours
        return cps_nurse_hours(request)
    population(request)
    paths = request.paths()
    monthly = []
    dates = set()
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
                     panel.limitations, panel.details)


def cps_monthly_aggregates(request: ConnectorRequest) -> PanelData:
    """Reuse frozen construction sums only when the source population is identical."""
    population(request)
    if 'source_population_filters' not in request.options:
        raise ConnectorNotReady('Declare source_population_filters for pre-aggregated CPS files')
    exact_filters(request, request.options['source_population_filters'])
    paths = request.paths()
    panel = annualize([r for path in paths for r in records(path)], request)
    return PanelData(panel.rows, panel.metadata, tuple(inventory(p, request) for p in paths),
                     panel.limitations, panel.details)
