"""Calendar/exposure contracts copied from frozen case validation; pure metadata."""
from __future__ import annotations
import calendar, re
from datetime import date, timedelta

class CaseValidationError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise CaseValidationError(message)

def obj(value, fields, label, optional=()):
    require(isinstance(value, dict), label + ' must be an object')
    require(set(fields) <= set(value) <= set(fields) | set(optional),
            label + ' has missing or unknown fields')

def integer(value):
    return type(value) is int

def date_bounds(raw, precision):
    """Coarse dates are intervals; never invent a particular day."""
    try:
        if precision == 'year' and re.fullmatch(r'\d{4}', raw):
            return date(int(raw), 1, 1), date(int(raw), 12, 31)
        if precision == 'month' and re.fullmatch(r'\d{4}-\d{2}', raw):
            year, month = map(int, raw.split('-'))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        require(precision == 'day' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw),
                'Date format does not match precision')
        return date.fromisoformat(raw), date.fromisoformat(raw)
    except ValueError as exc:
        raise CaseValidationError('Invalid operative date: ' + raw) from exc

def clock_date_bounds(raw, row_precision, clock):
    """An explicitly year-precision clock uses July 1 solely for exposure."""
    precision = clock.get('precision', row_precision)
    low, high = date_bounds(raw, precision)
    if clock.get('precision') == 'year':
        low = high = date(low.year, 7, 1)
    return low, high

def time_basis(case):
    """Explicit outcome calendar; preserve legacy subannual encodings if omitted."""
    panel = case['panel']
    basis = panel.get('time_basis')
    if basis is None:
        return dict(kind={'annual': 'calendar_year', 'quarterly': 'legacy_quarterly',
                          'monthly': 'monthly'}[panel.get('frequency', 'annual')])
    obj(basis, ('kind',), 'time_basis', ('fiscal_start_month',))
    kind = basis['kind']
    require(kind in ('calendar_year', 'fiscal_year', 'quarter'), 'Invalid time basis')
    require(panel['frequency'] == ('quarterly' if kind == 'quarter' else 'annual'),
            'Time basis disagrees with panel frequency')
    if kind == 'fiscal_year':
        month = basis.get('fiscal_start_month', 7)
        require(integer(month) and 1 <= month <= 12, 'Invalid fiscal start month')
        return dict(kind=kind, fiscal_start_month=month)
    require('fiscal_start_month' not in basis, 'Fiscal start month requires fiscal_year')
    return dict(kind=kind)

def _basis(value):
    if isinstance(value, str):
        return dict(kind={'annual': 'calendar_year', 'quarterly': 'legacy_quarterly',
                          'monthly': 'monthly'}.get(value, value))
    return value

def period_index(period, basis):
    """Convert a stored period code into its consecutive calendar index."""
    require(integer(period), 'Period must be an integer')
    kind = _basis(basis)['kind']
    if kind == 'quarter':
        year, quarter = divmod(period, 10)
        require(1 <= year <= 9999 and 1 <= quarter <= 4, 'Invalid quarter period code')
        return year * 4 + quarter - 1
    return period

def period_code(index, basis):
    if _basis(basis)['kind'] == 'quarter':
        year, offset = divmod(index, 4)
        return year * 10 + offset + 1
    return index

def period_of(day, frequency):
    basis = _basis(frequency)
    kind = basis['kind']
    if kind == 'calendar_year':
        return day.year
    if kind == 'fiscal_year':
        month = basis.get('fiscal_start_month', 7)
        return day.year + (month != 1 and day.month >= month)
    return day.year * (4 if kind in ('quarter', 'legacy_quarterly') else 12) + (
        (day.month - 1) // 3 if kind in ('quarter', 'legacy_quarterly') else day.month - 1)

def period_start(period, frequency):
    basis = _basis(frequency)
    kind = basis['kind']
    if kind == 'calendar_year':
        return date(period, 1, 1)
    if kind == 'fiscal_year':
        month = basis.get('fiscal_start_month', 7)
        return date(period - (month != 1), month, 1)
    count = 4 if kind in ('quarter', 'legacy_quarterly') else 12
    year, offset = divmod(period, count)
    return date(year, offset * 3 + 1 if count == 4 else offset + 1, 1)

def exposure_period(day, frequency, clock, full_exposure_min_days=None, history=None):
    """Resolve a source onset against actual half-open exposure intervals.

    A source date clips earlier history, so differing later onsets can still
    change the cohort. Gaps never count as exposed days. With no history the
    original date-only clock is retained.
    """
    period = period_of(day, frequency)
    if history and clock['rule'] != 'effective_period':
        intervals = [(max(day, date.fromisoformat(x['start'])),
                      date.fromisoformat(x['end']) if x['end'] else None) for x in history]
        last = max(period_of(end or start, frequency) for start, end in intervals) + 1
        for candidate in range(period, last + 1):
            start, end = period_start(candidate, frequency), period_start(candidate + 1, frequency)
            days = sum(max(0, (min(end, right or end) - max(start, left)).days)
                       for left, right in intervals)
            length = (end - start).days
            if clock['rule'] == 'first_full_period':
                required = length if full_exposure_min_days is None else full_exposure_min_days
            else:
                required = length * clock['threshold']
            if days >= required:
                return candidate
        raise CaseValidationError('Exposure history has no qualifying exposed period')
    if clock['rule'] == 'effective_period':
        return period
    if clock['rule'] == 'first_full_period':
        end = period_start(period + 1, frequency)
        required = (end - period_start(period, frequency)).days if full_exposure_min_days is None else full_exposure_min_days
        return period + ((end - day).days < required)
    end = period_start(period + 1, frequency)
    share = (end - day).days / (end - period_start(period, frequency)).days
    return period + (share < clock['threshold'])
