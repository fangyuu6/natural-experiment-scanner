"""T192 (HUB_RULING_20261020_T192): history rows marked ``pre_window_bound=true`` (template v9_t191).

Such a row says the registered policy was already in force in year Y, earlier than the first outcome year W, so the state is a
pre-window adopter. The row serves only the exclusion of the state from both arms (treated cohorts and every control pool).
Its ``start_period`` (= ``best_evidence_start_year`` = Y) is an upper bound of the true start and must never feed timing:
no cohort, event time, S3 or RI count, reconcile year comparison, audit sample or L3 export reads it.

Outcome-blind: this module reads rows only.
"""
from __future__ import annotations

from copy import deepcopy

FLAG = 'pre_window_bound'
REASON = 'pre_window_bound'
INVALID_KIND = 'pre_window_bound_invalid'


def is_bound(row) -> bool:
    """True for a treated row carrying the flag. Any other status (a downgrade, an unknown) never counts."""
    return isinstance(row, dict) and row.get(FLAG) is True and row.get('status') == 'treated'


def bound_units(rows) -> list:
    return sorted(r['unit'] for r in rows if is_bound(r))


def validate_row(row) -> None:
    """Schema acceptance: the flag is a boolean; a bound row is a treated row whose start equals its in-force year."""
    flag = row.get(FLAG)
    if flag is None or flag is False:
        return
    if flag is not True:
        raise ValueError('pre_window_bound must be true or absent')
    if row.get('status') != 'treated':
        return   # a downgraded or unknown row keeps no bound meaning (is_bound is False)
    year, start = row.get('best_evidence_start_year'), row.get('start_period')
    if type(year) is not int or type(start) is not int or year != start:
        raise ValueError('pre_window_bound row needs best_evidence_start_year equal to start_period (the sourced in-force year)')


def validate_window(row, window_start) -> None:
    """A bound year must lie before the first outcome year; otherwise the exact-year rule applies."""
    if is_bound(row) and type(window_start) is int and row['start_period'] >= window_start:
        raise ValueError('pre_window_bound year %s is not before the window start %s: %s' % (row['start_period'], window_start, row.get('unit')))


def forbid(rows, where: str = 'timing code') -> None:
    """Fail closed where a row could set a cohort, an event time or a control status."""
    bad = [r.get('unit') for r in rows if is_bound(r)]
    if bad:
        raise ValueError('pre_window_bound rows must not reach %s: %s' % (where, ', '.join(map(str, sorted(bad)))))


def _clean(row) -> bool:
    return not row.get('observed_interruptions') and not row.get('history_uncertain')


def pair(x, y):
    """Reconcile two known rows of one state when at least one is bound (panel T192, 5-0).

    Both bound: match whatever the two years (Q1). Bound Y against an exact treated row with operation year X: match only if
    X <= Y (Q2). Everything else (exact year after Y, never_treated, a year at or after W, unknown, interruptions) returns None and
    follows the ordinary rules, i.e. stays a conflict or a one-sided gap (Q3). The accepted row is the bound row, never an exact one,
    so no year of the pair can be mistaken for a start.
    """
    bx, by = is_bound(x), is_bound(y)
    if not (bx or by) or not (_clean(x) and _clean(y)):
        return None
    if x.get('mechanism') != y.get('mechanism'):
        return None
    if bx and by:
        row = deepcopy(x)
        row['pre_window_bound_years'] = sorted({x['start_period'], y['start_period']})
        row['pre_window_pair'] = 'both_bound'
        return row
    bound, exact = (x, y) if bx else (y, x)
    if exact.get('status') != 'treated':
        return None
    x_op = exact.get('best_evidence_start_year')
    x_op = x_op if type(x_op) is int else exact.get('start_period')
    if type(x_op) is not int or x_op > bound['start_period']:
        return None
    row = deepcopy(bound)
    row['pre_window_exact_operation_year'] = x_op
    row['pre_window_pair'] = 'bound_and_exact'
    return row


def enforce_window(comparison, window_start):
    """Move accepted bound rows whose year is not before the window into a gap (kind pre_window_bound_invalid). In place."""
    if type(window_start) is not int:
        return comparison
    keep = []
    for row in comparison['accepted']:
        if is_bound(row) and row['start_period'] >= window_start:
            comparison['gaps'].append(dict(unit=row['unit'], kind=INVALID_KIND))
            counts = comparison.get('counts', {})
            counts['known_match'] = counts.get('known_match', 1) - 1
            counts[INVALID_KIND] = counts.get(INVALID_KIND, 0) + 1
            continue
        keep.append(row)
    comparison['accepted'] = keep
    return comparison
