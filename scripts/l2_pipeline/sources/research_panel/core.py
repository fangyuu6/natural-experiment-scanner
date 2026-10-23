"""Outcome-free canonicalization and conservative exposure histories."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

STATES = tuple('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split())
REPO = Path(__file__).resolve().parents[4]
DATA = REPO / 'data/l2_pipeline/sources/research_panel'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def owned(path):
    """Restrict outputs to the two task trees and disposable scratch locations."""
    path = Path(path).resolve()
    roots = (DATA, REPO / 'data/l2_pipeline/runs/run7', Path(__file__).resolve().parent,
             Path('/private/tmp'), Path('/tmp').resolve())
    import tempfile
    if not any(path.is_relative_to(r.resolve()) for r in (*roots, Path(tempfile.gettempdir()))):
        raise ValueError('Output outside research_panel task trees/scratchpad')
    return path


def write_json(path, value, *, immutable=False):
    path = owned(path)
    content = json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'
    if immutable and path.exists():
        if path.read_text() != content:
            raise ValueError('Immutable research-panel artifact changed: ' + str(path))
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x' if immutable else 'w') as f:
        f.write(content)
    return path


def canonicalize(rows, variables):
    """Use explicit 50-state rectangles; an absent source cell stays unknown."""
    definitions = {v['policy_id']: dict(v) for v in variables}
    if len(definitions) != len(variables):
        raise ValueError('Duplicate variable definition')
    cells = defaultdict(dict)
    excluded = defaultdict(int)
    for row in rows:
        policy = row['policy_id']
        if policy not in definitions:
            raise ValueError('Undefined policy')
        state, year, value = row['state'], row['year'], row['value']
        if state == 'DC':
            excluded[policy] += 1
            continue
        if state not in STATES or type(year) is not int:
            raise ValueError('Invalid state or annual period')
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('Nonfinite/nonnumeric policy value')
        if definitions[policy]['kind'] == 'binary_adoption' and value not in (None, 0, 1):
            raise ValueError('Binary definition has nonbinary value')
        if (state, year) in cells[policy]:
            raise ValueError('Duplicate state-year-policy cell')
        cells[policy][state, year] = dict(row)
    canonical, catalog = [], []
    for policy, definition in sorted(definitions.items()):
        observed = cells[policy]
        if not observed:
            raise ValueError('Variable has no state rows')
        years = sorted({y for _, y in observed})
        start, end = years[0], years[-1]
        complete_years, counts = [], defaultdict(int)
        per_state = {}
        for year in range(start, end + 1):
            complete = True
            for state in STATES:
                row = observed.get((state, year))
                value = row['value'] if row else None
                status = 'missing_source_cell' if row is None else 'source_missing' if value is None else 'observed'
                counts[status] += 1
                if value is None:
                    complete = False
                canonical.append(dict(state=state, year=year, policy_id=policy, value=value,
                    status=status, source_id=definition['source_id'],
                    year_status=(row or {}).get('year_status', 'source_coded_annual_value'),
                    locator=(row or {}).get('locator', (row or {}).get('source_locator', definition.get('locator', policy)))))
            if complete:
                complete_years.append(year)
        windows = []
        for year in complete_years:
            if windows and year == windows[-1][1] + 1:
                windows[-1][1] = year
            else:
                windows.append([year, year])
        for state in STATES:
            known = [y for s, y in observed if s == state and observed[s, y]['value'] is not None]
            per_state[state] = dict(first_observed_year=min(known) if known else None,
                last_observed_year=max(known) if known else None, observed_years=len(known))
        historical_windows = []
        for left, right in windows:
            left = max(left, definition.get('historical_year_start', start))
            right = min(right, definition.get('historical_year_end', end))
            if left <= right:
                historical_windows.append([left, right])
        catalog.append(dict(definition, coverage=dict(start_year=start, end_year=end,
            expected_cells=50 * (end - start + 1), **counts,
            complete_50_state_windows=windows, complete_50_state_years=len(complete_years),
            historical_complete_50_state_windows=historical_windows,
            state_coverage=per_state, dc_rows_excluded=excluded[policy]),
            candidate_status='official_audit_pending' if definition['kind'] == 'binary_adoption'
                else 'parked_continuous_requires_predeclared_threshold',
            outside_source_window='unknown', legal_certification_granted=False,
            first_order_outcome_candidates=definition.get('outcomes', [])))
    return canonical, catalog


def threshold_rule(path, expected_sha256, policy):
    """Bind an externally predeclared level; never select a cutpoint from outcomes."""
    path = Path(path)
    if digest(path) != expected_sha256:
        raise ValueError('Threshold preregistration hash mismatch')
    rule = json.loads(path.read_text())
    if (rule.get('policy_id') != policy['policy_id'] or rule.get('unit') != policy['unit']
            or rule.get('operator') not in ('>=', '<=')
            or type(rule.get('threshold')) not in (int, float)
            or not math.isfinite(rule['threshold'])
            or not rule.get('rationale') or not rule.get('declared_at')
            or rule.get('outcome_associations_examined') is not False):
        raise ValueError('Incomplete prospective threshold declaration')
    return dict(rule, declaration_path=str(path.resolve()), declaration_sha256=expected_sha256)


def history(rows, policy, window, *, threshold=None):
    start, end = window
    if type(start) is not int or type(end) is not int or start > end:
        raise ValueError('Invalid history window')
    coverage = policy['coverage']
    if start < coverage['start_year'] or end > coverage['end_year']:
        raise ValueError('Requested history outside source coverage')
    binary = policy['kind'] == 'binary_adoption'
    if not binary and threshold is None:
        raise ValueError('Continuous policy parked pending predeclared threshold')
    cells = {(r['state'], r['year']): r for r in rows if r['policy_id'] == policy['policy_id']}
    annual, records, events = [], [], []
    for state in STATES:
        path = []
        for year in range(start, end + 1):
            row = cells.get((state, year), {})
            if row.get('year_status') in ('future_snapshot_unverified', 'current_year_partial', 'partial_start_year'):
                raise ValueError('Partial/future source period cannot certify annual exposure')
            value = row.get('value')
            active = value if binary or value is None else int(value >= threshold['threshold']
                if threshold['operator'] == '>=' else value <= threshold['threshold'])
            path.append(dict(unit=state, year=year, active=active, value=value,
                locator=row.get('locator', policy['policy_id'])))
        annual.extend(path)
        missing = [r['year'] for r in path if r['active'] is None]
        baseline = path[0]['active'] == 1
        first = next((r['year'] for r in path if r['active'] == 1), None)
        reversals, spells, spell = [], [], None
        for i, row in enumerate(path):
            if i and row['active'] is not None and path[i - 1]['active'] is not None and row['active'] != path[i - 1]['active']:
                events.append(dict(unit=state, year=row['year'], event='activation' if row['active'] else 'reversal',
                    prior_active=path[i - 1]['active'], active=row['active'], locator=row['locator']))
            if (row['active'] == 0 and first is not None and row['year'] > first
                    and (i == 0 or path[i - 1]['active'] != 0)):
                reversals.append(row['year'])
            if row['active'] == 1:
                if spell is None:
                    spell = dict(start_period=row['year'], end_period=row['year'])
                else:
                    spell['end_period'] = row['year']
            elif spell:
                spells.append(spell); spell = None
        if spell:
            spells.append(spell)
        records.append(dict(unit=state, status='unknown' if missing or baseline else 'treated' if first else 'never_treated',
            start_period=None if baseline else first, mechanism=policy['definition'], locator=path[0]['locator'],
            unknown_years=missing, left_censored=baseline, reversal_years=reversals, intervals=spells,
            primary_excluded=bool(missing or baseline or reversals),
            never_control_eligible=not missing and first is None,
            daily_full_year_certified=False, outside_source_window='unknown'))
    return dict(schema_version='research_panel_history_v1', source_type='research_panel',
        policy_id=policy['policy_id'], definition=policy['definition'], clock=policy['clock'],
        coverage=dict(start_year=start, end_year=end), units=list(STATES), records=records,
        annual_rows=annual, events=events, threshold=threshold,
        history_complete=not any(r['unknown_years'] for r in records),
        reversal_handling='Preserve all spells; exclude ever-reversed states from absorbing primary/control pool',
        annual_stock_is_full_year_exposure=False, legal_certification_granted=False,
        no_dual_coding_originals=True, outcome_association_calls=0)


def write_panel(path, rows):
    path = owned(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=('state', 'year', 'policy_id', 'value', 'status', 'source_id', 'year_status', 'locator'))
        writer.writeheader()
        writer.writerows(rows)


def read_panel(path):
    with Path(path).open(newline='') as f:
        return [dict(r, year=int(r['year']), value=float(r['value']) if r['value'] else None)
                for r in csv.DictReader(f)]
