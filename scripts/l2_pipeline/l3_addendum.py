"""S6-only numerical exports for the prospectively registered L3 addendum.

No fitting, input loading, cross-design tabulation, or inferential statistics.
Inputs use the structural engine's indexed periods; exported g/year/event_time
are calendar years when declaration.period_years is present.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from .engine.estimator import contrast_weights


def _year(declaration, period):
    calendar = declaration.get('period_years')
    if calendar is None:
        return period
    if not isinstance(period, int) or not 0 <= period < len(calendar):
        return None
    return calendar[period]


def _direction(value):
    if isinstance(value, bool) or value not in (-1, 0, 1, None):
        raise ValueError('purpose_direction must be a blind-coded -1, 0, or +1')
    return value


def _clean_keys(design):
    """Registered usable periods only; no post/partial/exposed observations."""
    periods = set(design['periods'])
    return sorted((u, t) for u, t in design['mask']
                  if u in design['units'] and t in periods
                  and (design['adoption'][u] is None or t < design['adoption'][u])
                  and design['exposure'][u].get(t, {}).get('clean', False))


def _value(outcomes, key):
    value = float(outcomes[key])
    if not math.isfinite(value):
        raise ValueError('Nonfinite registered outcome in L3 export')
    return value


def _profile(declaration, design, outcomes=None):
    """Keep cells separate when the estimator splits a cohort by base period."""
    rows = []
    for cell in design['cells']:
        g = _year(declaration, cell['g'])
        year = _year(declaration, cell['t'])
        if g is None or year is None:
            continue
        e = year - g
        if not cell['post'] or e not in range(6):
            continue
        row = dict(g=g, event_time=e, year=year, n_treated=cell['n_treated'],
                   g_period=cell['g'], period=cell['t'],
                   base_period=cell['base_period'], treated=list(cell['treated']))
        if outcomes is not None:
            row['att'] = sum(w * _value(outcomes, key)
                             for key, w in contrast_weights(cell).items())
        rows.append(row)
    return rows


def _balanced_profile(declaration, design):
    profile = _profile(declaration, design)
    events = defaultdict(set)
    for row in profile:
        events[row['g']].add(row['event_time'])
    cohorts = sorted(g for g, es in events.items() if {0, 1, 3} <= es)
    common = sorted(set.intersection(*(events[g] for g in cohorts))) if cohorts else []
    balanced = []
    for e in range(6):
        if e not in common:
            break
        balanced.append(e)
    return dict(H=len(balanced) if balanced else None, cohorts=cohorts,
                event_times=balanced, common_event_times=common,
                E=max(common) if common else None,
                definition='N2 C: cohorts supporting calendar e=0,1,3; H counts '
                           'contiguous common calendar post years e=0..H-1, capped at 5',
                status='AVAILABLE' if balanced else 'SUPPORT')


def _trough(declaration, design, outcomes, purpose_direction, original_design=None):
    clean = set(_clean_keys(design))
    if original_design is not None:
        clean &= set(_clean_keys(original_design))
    units = sorted(design['units'])
    universe = sorted(declaration.get('l3_universe', units))
    periods = sorted(design['periods'])
    by_year = {_year(declaration, t): t for t in periods}
    rows = []
    # The addendum requires full U_i demeaning. Refuse a year if any U_i
    # observation is exposed/post or missing; replacing U_i by a time-varying
    # subset would silently change that frozen definition.
    full_u_pre = {t for t in periods if all((u, t) in clean for u in universe)}
    means = {}
    for u in units:
        cohort = design['adoption'][u]
        if cohort is None or cohort not in design['groups']:
            continue
        g = _year(declaration, cohort)
        row = dict(unit=u, g=g, g_period=cohort, recent_years=[], base_years=[],
                   recent_mean=None, base_mean=None, depth=None, status='SUPPORT', reason=None)
        rows.append(row)
        if not universe:
            row['reason'] = 'Registered U_i unavailable'
            continue
        if g is None:
            row['reason'] = 'Cohort calendar year unavailable'
            continue
        recent_years = list(range(g - 3, g))
        base = [t for t in periods if _year(declaration, t) <= g - 4 and (u, t) in clean]
        row.update(recent_years=recent_years, base_years=[_year(declaration, t) for t in base])
        if len(base) < 3:
            row['reason'] = 'Fewer than three clean baseline calendar years'
            continue
        recent = [by_year[y] for y in recent_years if y in by_year]
        if len(recent) != 3 or any((u, t) not in clean for t in recent):
            row['reason'] = 'Incomplete clean g-3 through g-1 calendar-year window'
            continue
        if any(t not in full_u_pre for t in base + recent):
            row['reason'] = 'Full U_i year mean requires missing or post/exposed data'
            continue
        for t in base + recent:
            if t not in means:
                means[t] = statistics.mean(_value(outcomes, (v, t)) for v in universe)
        baseline = statistics.mean(_value(outcomes, (u, t)) - means[t] for t in base)
        recent_mean = statistics.mean(_value(outcomes, (u, t)) - means[t] for t in recent)
        row.update(base_mean=baseline, recent_mean=recent_mean,
                   depth=-purpose_direction * (recent_mean - baseline) if purpose_direction else None,
                   status=('FIELD_MISSING' if purpose_direction is None else
                           'AVAILABLE' if purpose_direction else 'DIRECTION_UNDETERMINED'), reason=None)
    return rows


def observed_fields(declaration, design, outcomes, purpose_direction):
    """Compute only inside an addendum-enabled design's registered S6 run."""
    purpose_direction = _direction(purpose_direction)
    clean = _clean_keys(design)
    values = [_value(outcomes, k) for k in clean]
    support = _balanced_profile(declaration, design)
    return dict(s_pre=statistics.stdev(values) if len(values) >= 2 else None,
                s_pre_n=len(values), s_pre_ddof=1,
                s_pre_definition='Pooled sample SD of registered clean pre unit-years; never controls contribute all clean years',
                H=support['H'], balanced_profile=support,
                trough=_trough(declaration, design, outcomes, purpose_direction),
                trough_definition='Full U_i year-demeaned outcome; all contributing unit-years must be clean pre; no post values read',
                computed_stage='S6')


def placebo_fields(declaration, fake_design, outcomes, purpose_direction, *, original_design=None):
    """Archive one already fixed RI relabeling without another estimator fit.

    Caller preserves its original draw_index and stores label/generator binding.
    Trough reads only unit-years clean under both original and placebo clocks.
    The registered caller must pass original_design; standalone synthetic calls
    can omit it when fake and observed clocks are the same.
    """
    purpose_direction = _direction(purpose_direction)
    return dict(group_time=_profile(declaration, fake_design, outcomes),
                trough=_trough(declaration, fake_design, outcomes, purpose_direction, original_design))


def cohort_points(declaration, design, outcomes):
    """Exact estimator group mean: equal weights across supported post cells.

    Includes every post cell (no e<=5 cap); n_treated counts unique members,
    rather than summing the same states repeatedly across post periods.
    """
    rows=[]
    for g in design['groups']:
        cells=[c for c in design['cells'] if c['g']==g and c['post']]
        if not cells: continue
        points=[sum(w*_value(outcomes,k) for k,w in contrast_weights(c).items()) for c in cells]
        rows.append(dict(g=_year(declaration,g),g_period=g,att=sum(points)/len(points),
            n_treated=len({u for c in cells for u in c['treated']}),n_periods=len(cells)))
    return rows


def calendar_year(declaration, period):
    """Resolve the engine's normalized clock, including monthly and quarterly."""
    if 'period_years' in declaration: return _year(declaration,period)
    mapping=declaration.get('period_calendar_years')
    if mapping is not None:
        return mapping.get(period,mapping.get(str(period))) if isinstance(mapping,dict) else None
    from .engine.calendar import time_basis
    basis=time_basis(declaration['case'])['kind']
    if basis=='calendar_year': return period
    if basis in {'quarter','legacy_quarterly'}: return period//4
    if basis=='monthly': return period//12
    # Fiscal labels straddle two calendar years. Require an explicit mapping.
    return None


def amendment_fields(declaration, design, outcomes, registration, *, on_estimate=None):
    """S6-only prospective F12/F16. No cross-design or adoption associations."""
    import copy
    from .engine.design import build_design
    amendment=registration.get('amendment_v2',{})
    if amendment.get('schema')!='l2_l3_amendment_c70_v1' or amendment.get('status')=='fields unavailable':
        return {}
    points=[]
    for c in design['cells']:
        points.append(dict(g=_year(declaration,c['g']),g_period=c['g'],period=c['t'],
            event_time=(_year(declaration,c['t'])-_year(declaration,c['g']) if 'period_years' in declaration else c['event_time']),calendar_year=calendar_year(declaration,c['t']),
            att=sum(w*_value(outcomes,k) for k,w in contrast_weights(c).items()),
            n_treated=c['n_treated'],base_period=c['base_period'],post=c['post']))
    status=copy.deepcopy(amendment['field_status'])
    status['F16']='AVAILABLE' if points and all(type(r['calendar_year']) is int for r in points) else 'FIELD_MISSING'
    risk=registration.get('geography',{}).get('R')
    omissions=None
    if risk is None: status['F12']='FIELD_MISSING'
    else:
        omissions=[]
        controls=sorted({u for c in design['cells'] for u in c['controls']} & set(risk))
        for unit in controls:
            # Delete the control from the frozen declaration and rebuild its
            # metadata support. No mask/estimator selection uses outcomes.
            reduced=copy.deepcopy(declaration)
            reduced['case']['sample_mask']['include_units']=[u for u in reduced['case']['sample_mask']['include_units'] if u!=unit]
            reduced['never_controls']=[u for u in reduced.get('never_controls',[]) if u!=unit]
            raw=[(u,declaration['period_years'][t] if 'period_years' in declaration else t,y,None)
                 for (u,t),y in sorted(outcomes.items()) if u!=unit]
            try:
                rebuilt=build_design(reduced,raw)
                if not any(c['post'] for c in rebuilt['cells']): raise ValueError('No supported post cells')
            except ValueError as exc:
                omissions.append(dict(unit=unit,status='SUPPORT',reason=str(exc),group=None)); continue
            if on_estimate is not None: on_estimate()
            omissions.append(dict(unit=unit,status='AVAILABLE',group=cohort_points(declaration,rebuilt,outcomes)))
        status['F12']='AVAILABLE' if all(r['status']=='AVAILABLE' for r in omissions) else 'SUPPORT'
    return dict(group_time=points,group=cohort_points(declaration,design,outcomes),
        loo_control_att=omissions,amendment_field_status=status,
        statute_variant=copy.deepcopy(amendment.get('statute_variant')),
        interruption_ledger=copy.deepcopy(amendment.get('interruption_ledger')),
        pub_ledger=copy.deepcopy(amendment.get('pub_ledger')),
        pub_ledger_sha256=amendment.get('pub_ledger_sha256'))
