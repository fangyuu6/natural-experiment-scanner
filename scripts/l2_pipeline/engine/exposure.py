"""Half-open exposure histories and sample masks; no file access."""
from __future__ import annotations
import copy
from datetime import date
from . import calendar
def _v(): return calendar

def effective_case(case, variant=None):
    result = copy.deepcopy(case)
    if variant is None: return result
    if isinstance(variant, str):
        variant = next(x for x in case.get('variants', []) if x['id'] == variant)
    for key in ('sample_mask', 'exposure_clock', 'full_exposure_min_days', 'outcome', 'benchmark', 'outcome_transform'):
        if key in variant: result[key] = copy.deepcopy(variant[key])
    if variant.get('include_date_unverified'):
        result = include_source_dates(result)
    result['_variant_id'] = variant['id']
    result['_clock_variant'] = variant['kind'] == 'clock'
    return result

def include_source_dates(case):
    result = copy.deepcopy(case)
    verification = result['comparison'].get('date_verification', {})
    result['_source_date_units'] = result.get('_source_date_units',
        [item['unit'] for item in verification.get('exclusions', [])])
    verification['exclusions'] = []
    return result

def source_date_row(case, row, unit):
    if unit not in case.get('_source_date_units', []): return row
    result = dict(row, policy_status='treated')
    if case.get('_clock_variant'): return result
    clock = case['exposure_clock']['date_column']
    result[clock] = row.get('source1_effective_date') or row[clock]
    result['date_precision'] = {10: 'day', 7: 'month', 4: 'year'}.get(len(result[clock]), row['date_precision'])
    return result

def treatment_cohort(case, row, unit):
    v = _v(); basis = v.time_basis(case)
    low, high = v.clock_date_bounds(row[case['exposure_clock']['date_column']], row['date_precision'], case['exposure_clock'])
    args = (basis, case['exposure_clock'], case.get('full_exposure_min_days'),
            case.get('exposure_history', {}).get(unit))
    cohort = v.exposure_period(low, *args)
    v.require(cohort == v.exposure_period(high, *args), 'Date precision leaves exposure unresolved')
    return v.period_code(cohort, basis)

def excluded_units(case, variant=None):
    case = effective_case(case, variant) if variant is not None else case
    comparison = case.get('comparison', {})
    return ({x['unit'] if isinstance(x, dict) else x for x in comparison.get('exclusions', [])}
            | {x['unit'] for x in comparison.get('date_verification', {}).get('exclusions', [])}
            | set(case.get('sample_mask', {}).get('exclude_units', [])))

def included_unit(case, unit):
    mask = case.get('sample_mask', {})
    return unit not in excluded_units(case) and ('include_units' not in mask or unit in mask['include_units'])

def period_day_count(case, period):
    v = _v(); basis = v.time_basis(case)
    return (v.period_start(period+1, basis) - v.period_start(period, basis)).days

def exposure_days(case, row, unit, period):
    """Number of exposed days in a consecutive-index period; conservative coarse dates."""
    row = source_date_row(case, row, unit)
    v = _v(); basis = v.time_basis(case)
    start, end = v.period_start(period, basis), v.period_start(period+1, basis)
    intervals = case.get('exposure_history', {}).get(unit)
    if intervals is None:
        raw = row.get(case['exposure_clock']['date_column'], '')
        if not raw or row.get('policy_status') == 'never_treated': return 0
        low, high = v.clock_date_bounds(raw, row['date_precision'], case['exposure_clock'])
        intervals = [{'start': low.isoformat(), 'end': None}]
    count = 0
    for interval in intervals:
        left = max(start, date.fromisoformat(interval['start']))
        right = min(end, date.fromisoformat(interval['end']) if interval['end'] else end)
        count += max(0, (right-left).days)
    return count

def clean_period(case, row, unit, period):
    return exposure_days(case, row, unit, period) == 0

def full_period(case, row, unit, period):
    count = period_day_count(case, period)
    minimum = case.get('full_exposure_min_days', count)
    # A fixed annual minimum scales to each actual year length (leap days
    # are exposed normally); impossible minima are rejected at validation.
    return exposure_days(case, row, unit, period) >= minimum


def annual_exposure_profile(years, operative, clean_onset, cohorts, end_dates, units):
    """Full annual policy availability, bounded by a half-open cessation date.

    Previously exposed states never become clean controls after cessation.
    Partial cessation years and uncertified later exposure are not full years.
    This same metadata-only profile binds preparation and numerical replay.
    """
    ends = {u: date.fromisoformat(end) for u, end in end_dates.items()}
    return {u: {i: {'clean': u not in operative or y < clean_onset[u],
                    'full': u in cohorts and i >= cohorts[u]
                            and (u not in ends or date(y + 1, 1, 1) <= ends[u])}
                for i, y in enumerate(years)} for u in units}


def annual_before_cessation(unit, year, end_dates):
    """Whether a full calendar year remains inside the certified exposure path."""
    return (unit not in end_dates
            or date(year + 1, 1, 1) <= date.fromisoformat(end_dates[unit]))
