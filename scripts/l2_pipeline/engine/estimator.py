"""Frozen staggered DiD arithmetic copied without batch imports or file I/O.

Ordering, summation and RNG remain unchanged for archival byte equivalence.
"""
from __future__ import annotations
import math, copy, random, statistics
from collections import defaultdict
from datetime import date
from .calendar import date_bounds, clock_date_bounds, period_of, time_basis, period_index
from .exposure import clean_period, full_period, included_unit, excluded_units as view_excluded_units, source_date_row, include_source_dates, treatment_cohort
from .types import Design, Outcomes, PanelRow
from typing import Sequence
SEED=20261001
RI_SEED=20261002
CHECKS=("pretrend_joint", "pretrend_economic", "placebo")

def excluded_units(case, *, include_date_unverified=False):
    if include_date_unverified:
        verification = case.get('comparison', {}).get('date_verification', {})
        if not verification.get('sensitivity') and not any(
                v.get('include_date_unverified') for v in case.get('variants', [])):
            raise ValueError('Date reinclusion requires a registered sensitivity')
        view=copy.deepcopy(case)
        view['comparison']['date_verification']['exclusions']=[]
        return view_excluded_units(view)
    return view_excluded_units(case)

def normalize_rows(case, rows, *, include_date_unverified=False):
    """Convert declared labels to consecutive indices before any arithmetic."""
    basis = time_basis(case)
    excluded = excluded_units(case, include_date_unverified=include_date_unverified)
    return [(u, period_index(t, basis), y, None if g is None else period_index(g, basis))
            for u, t, y, g in rows if u not in excluded
            and ('include_units' not in case.get('sample_mask',{}) or u in case['sample_mask']['include_units'])]

def normalize_window(case, window):
    basis = time_basis(case)
    return dict(window, start=period_index(window['start'], basis),
                end=period_index(window['end'], basis),
                excluded_periods=[period_index(t, basis) for t in window['excluded_periods']])

def operative_periods(case, treatment):
    """Canonical treatment rows, keyed by the panel's unit identifier."""
    result = {}
    for u, row in treatment.items():
        row = source_date_row(case, row, u)
        if row['policy_status'] == 'treated':
            low, high = clock_date_bounds(row[case['exposure_clock']['date_column']], row['date_precision'], case['exposure_clock'])
            a, b = period_of(low, time_basis(case)), period_of(high, time_basis(case))
            if a != b:
                raise ValueError('Operative date precision does not identify a unique period: ' + u)
            history = case.get('exposure_history',{}).get(u,[])
            result[u] = min([a] + [period_of(date.fromisoformat(x['start']),time_basis(case)) for x in history])
        else:
            result[u] = None
    return result

def structural_design(case: dict, rows: Sequence[PanelRow], treatment: dict, *, window=None, control_group='never_treated', operative=None, normalized=False, include_date_unverified=False, exposure_override=None) -> Design:
    """Only unit, period, adoption and observed-cell mask are selected from rows."""
    if include_date_unverified: case = include_source_dates(case)
    window = normalize_window(case, window or case['windows']['primary'])
    if not normalized:
        rows = normalize_rows(case, rows, include_date_unverified=include_date_unverified)
    else:
        excluded = excluded_units(case, include_date_unverified=include_date_unverified)
        rows = [r for r in rows if r[0] not in excluded
                and ('include_units' not in case.get('sample_mask',{}) or r[0] in case['sample_mask']['include_units'])]
    observed_periods=sorted(set(t for u,t,y,g in rows if window['start'] <= t <= window['end']))
    periods=[t for t in observed_periods if t not in window['excluded_periods']]
    adoption = {u:g for u,t,y,g in rows}
    for u, g in adoption.items():
        row = source_date_row(case, treatment[u], u)
        if exposure_override is None and 'rule' in case['exposure_clock'] and row['policy_status'] == 'treated':
            if u in case.get('_source_date_units', []) and row.get('source1_effective_date'):
                adoption[u] = period_index(treatment_cohort(case, row, u), time_basis(case))
            elif g is None:
                future = period_index(treatment_cohort(case, row, u), time_basis(case))
                if future > window['end']: adoption[u] = future
    units = sorted(adoption)
    mask = {(u,t) for u,t,y,g in rows if t in observed_periods}
    operative = operative if operative is not None else operative_periods(case, {u:treatment[u] for u in units})
    exposure = exposure_override or {
        u: {t: dict(clean=clean_period(case, treatment[u], u, t) and
                               (operative[u] is None or t<operative[u]),
                    full=full_period(case, treatment[u], u, t)) for t in observed_periods}
        for u in units}
    def clean(u, t):
        return exposure[u].get(t, {}).get('clean', False)
    def full(u, t):
        return exposure[u].get(t, {}).get('full', False)
    # A future adopter is a primary control only if every observed window
    # period is wholly clean, including any earlier interrupted episode.
    whole_window_clean = {u: all(clean(u,t) for t in observed_periods) for u in units}
    groups = sorted({g for g in adoption.values() if g is not None and window['start']<=g<=window['end']})
    cells = []
    bases = {}
    for u,g in adoption.items():
        if g is not None:
            clean_bases = [t for t in observed_periods if (u,t) in mask and t < g and clean(u,t)]
            if clean_bases:
                bases[u] = max(clean_bases)
    for g in groups:
        members = [u for u in units if adoption[u] == g]
        for t in periods:
            post = t >= g
            parts = defaultdict(list)
            for u in members:
                base = bases.get(u) if post else t-1
                if base is None or base not in (observed_periods if post else periods) or (u,t) not in mask or (u,base) not in mask:
                    continue
                if not clean(u,base) or (post and not full(u,t)) or (not post and not clean(u,t)):
                    continue
                parts[base].append(u)
            for base, treated in sorted(parts.items()):
                controls = [u for u in units if adoption[u] != g
                            and (u,t) in mask and (u,base) in mask
                            and clean(u,t) and clean(u,base)
                            and ((control_group=='never_treated' and whole_window_clean[u]) or
                                 (control_group=='not_yet_treated' and
                                  (adoption[u] is None or max(t,base)<adoption[u])))]
                if not controls or (control_group=='not_yet_treated' and 'v11_min_controls' in case and len(controls)<case.get('v11_min_controls',5)):
                    continue
                cells.append(dict(g=g,t=t,event_time=t-g,base_period=base,post=post,
                                  n_treated=len(treated),n_control=len(controls),
                                  treated=treated,controls=controls))
    if control_group=='never_treated' and 'min_post_periods' in case:
        from ..support import minimum_post_periods
        minimum = minimum_post_periods(case)
        cells = [c for c in cells if c['n_control'] >= case.get('min_controls',1)]
        dropped = {g for g in groups if len({c['t'] for c in cells
                   if c['g']==g and c['post']}) < minimum}
        rejected = {u for u in units if adoption[u] in dropped}
        if rejected:
            return structural_design(case, [r for r in rows if r[0] not in rejected], treatment,
                window=window, control_group=control_group, operative=operative, normalized=True,
                exposure_override=exposure)
    if control_group=='not_yet_treated' and 'v11_min_controls' in case:
        # Keep late adopters in the control pool; only supported cohorts are
        # estimated. RI applies this same fixed rule to every label bundle.
        retained={g for g in groups if len({c['t'] for c in cells if c['g']==g and c['post']})>=case.get('v11_min_post_periods',2)
                  and all(sum((u,t) in mask and clean(u,t) for t in periods if t<g)>=case.get('v11_min_pre_periods',3)
                          for u in units if adoption[u]==g)}
        cells=[c for c in cells if c['g'] in retained]
        groups=[g for g in groups if g in retained]
    if not any(c['post'] for c in cells):
        raise ValueError('No supported post cells')
    result = dict(units=units,periods=periods,observed_periods=observed_periods,baseline_only_periods=sorted(set(observed_periods)-set(periods)),adoption=adoption,operative=operative,
                mask=mask,groups=groups,cells=cells,cohort_bases=bases,
                exposure=exposure,whole_window_clean=whole_window_clean,
                control_group=control_group,window=window,time_basis=time_basis(case))
    if 'v11_min_controls' in case: result['v11_comparison']=True
    return result

def contrast_weights(cell: dict) -> dict:
    weights = {}
    for role, sign in (('treated',1),('controls',-1)):
        for u in cell[role]:
            weights[u,cell['t']] = sign/len(cell[role])
            weights[u,cell['base_period']] = -sign/len(cell[role])
    return weights

def event_operators(design: Design) -> dict:
    result = {}
    for e in sorted({c['event_time'] for c in design['cells']}):
        selected = [c for c in design['cells'] if c['event_time']==e]
        mass = sum(c['n_treated'] for c in selected)
        weights = defaultdict(float)
        for c in selected:
            for key, value in contrast_weights(c).items():
                weights[key] += c['n_treated']/mass*value
        result[e] = dict(weights=dict(weights), n_treated=mass)
    return result

def placebo_operator(design: Design) -> dict:
    """Fixed three pre-adoption placebo posts on a common clean cohort clock.

    Each unit's own first_treated defines pre-exposure eligibility. The final
    common wholly clean period b anchors the fixed b-3/base, b-2,b-1,b posts.
    Missing support is an explicit cohort record, never an alternate shift.
    """
    out = defaultdict(float)
    mass = 0
    rows, cohorts = [], []
    for g in design['groups']:
        members = [u for u in design['units'] if design['adoption'][u]==g]
        common = [t for t in design['periods']
                  if all(t < design['adoption'][u] and (u,t) in design['mask']
                         and design['exposure'][u][t]['clean'] for u in members)]
        b = max(common) if common else None
        endpoints = [] if b is None else [b-3,b-2,b-1,b]
        reason = None
        if b is None:
            reason = 'No common wholly clean pre-adoption period'
        elif any(t not in design['periods'] for t in endpoints) or any(
                (u,t) not in design['mask'] or not design['exposure'][u].get(t,{}).get('clean',False)
                or t >= design['adoption'][u] for u in members for t in endpoints):
            reason = 'Fixed four-period placebo endpoints are absent or exposed'
        from ..v11_v12 import placebo_controls
        controls = [] if reason else placebo_controls(design,members,endpoints)
        if not reason and (not controls or (design.get('v11_comparison') and design['control_group']=='not_yet_treated' and len(controls)<5)):
            reason = 'No wholly clean never-treated support'
        cohorts.append(dict(g=g,status='placebo_unsupported' if reason else 'supported',
                            reason=reason,last_common_clean_period=b,
                            base_period=None if reason else b-3,units=members))
        if reason:
            continue
        for t in endpoints[1:]:
            cell = dict(g=g,t=t,base_period=b-3,treated=members,controls=controls,
                        n_treated=len(members),n_control=len(controls),event_time=t-g)
            rows.append(cell)
            for key,w in contrast_weights(cell).items():
                out[key] += len(members)*w
            mass += len(members)
    return dict(weights={k:v/mass for k,v in out.items()} if mass else {},
                cells=rows,mass=mass,cohorts=cohorts,
                supported_cohorts=[r['g'] for r in cohorts if r['status']=='supported'],
                unsupported_cohorts=[r for r in cohorts if r['status']=='placebo_unsupported'],
                assessment_status='COMPLETED' if mass else 'NOT_ASSESSABLE')

def estimate_design(design: Design, outcomes: Outcomes, *, B: int=4999, seed: int=SEED, export_influence: bool=False) -> dict:
    units = design['units']; n=len(units); index={u:i for i,u in enumerate(units)}
    records=[]; influences=[]
    for cell in design['cells']:
        changes={u:outcomes[u,cell['t']]-outcomes[u,cell['base_period']]
                 for u in cell['treated']+cell['controls']}
        mg=statistics.mean(changes[u] for u in cell['treated'])
        mc=statistics.mean(changes[u] for u in cell['controls'])
        inf=[0.]*n
        for role,mean,sign in (('treated',mg,1),('controls',mc,-1)):
            for u in cell[role]:
                inf[index[u]]=sign*n/len(cell[role])*(changes[u]-mean)
        records.append(dict(cell,att=mg-mc)); influences.append(inf)
    cell_count=len(records)
    def aggregate(selected, fields, composition=True):
        masses=[records[j]['n_treated'] if composition else 1 for j in selected]
        total=sum(masses)
        att=sum(w*records[j]['att'] for w,j in zip(masses,selected))/total
        inf=[sum(w*influences[j][i] for w,j in zip(masses,selected))/total for i in range(n)]
        if composition:
            for j in selected:
                for u in records[j]['treated']:
                    inf[index[u]] += n/total*(records[j]['att']-att)
        records.append(dict(fields,att=att)); influences.append(inf)
        return records[-1]
    events=[]
    for e in sorted({r['event_time'] for r in records[:cell_count]}):
        sel=[j for j in range(cell_count) if records[j]['event_time']==e]
        events.append(aggregate(sel,dict(event_time=e,n_treated=sum(records[j]['n_treated'] for j in sel),
                      n_cohorts=len({records[j]['g'] for j in sel}),cohorts=sorted({records[j]['g'] for j in sel}))))
    groups=[]; calendars=[]
    for g in design['groups']:
        sel=[j for j in range(cell_count) if records[j]['g']==g and records[j]['post']]
        if sel: groups.append(aggregate(sel,dict(g=g,n_periods=len(sel)),False))
    for t in design['periods']:
        sel=[j for j in range(cell_count) if records[j]['t']==t and records[j]['post']]
        if sel: calendars.append(aggregate(sel,dict(t=t)))
    overall=aggregate([j for j in range(cell_count) if records[j]['post']],dict(n_cells=sum(r['post'] for r in records[:cell_count])))
    rng=random.Random(seed); draws=[[] for _ in records]
    for _ in range(B):
        multipliers=[rng.gauss(0,1) for _ in units]
        for column,inf in zip(draws,influences):
            column.append(sum(a*b for a,b in zip(multipliers,inf))/n)
    for r,column in zip(records,draws):
        r['se']=statistics.stdev(column); r['ci95']=[r['att']-1.96*r['se'],r['att']+1.96*r['se']]
        r['interval']=dict(method='Gaussian unit multiplier point interval',nominal_level=.95,approximate=True)
    positions={id(r):j for j,r in enumerate(records)}
    families={name:[r for r in events if r['n_treated']>=5 and predicate(r)] for name,predicate in
              (('pre',lambda r:r['event_time']<0),('post',lambda r:r['event_time']>=0),('all',lambda r:True))}
    band_info={}
    for name,family in families.items():
        maxima=[max((abs(draws[positions[id(r)]][b])/r['se'] for r in family if r['se']>0),default=0) for b in range(B)]
        critical=_quantile(maxima,.95) if family else None
        band_info[name]=dict(event_times=[r['event_time'] for r in family],critical_value=critical,descriptive=name!='pre')
        for r in family:
            r[name+'_simultaneous95']=[r['att']-critical*r['se'],r['att']+critical*r['se']]
    for r in events:
        r['thin_support']=r['n_treated']<5
        r['simultaneous95']=None if r['thin_support'] else r.get('all_simultaneous95')
        if r['thin_support']: r['point_interval_role']='descriptive only; public inferential band suppressed'
    for r in records[:cell_count]:
        r.pop('treated');r.pop('controls')
    influence_export=None
    if export_influence:
        # Staged T175: per-unit influence scores of the overall ATT and of every event-study row; lead covariance is
        # sum_u psi_u(e) psi_u(e') / n^2 (exact analytic multiplier covariance). Opt-in; default result is unchanged.
        pos={id(r):j for j,r in enumerate(records)}
        ev_psi={r['event_time']:influences[pos[id(r)]] for r in events}
        pre=[e for e in sorted(ev_psi) if e<0]
        lead_cov=[[sum(a*b for a,b in zip(ev_psi[e],ev_psi[f]))/(n*n) for f in pre] for e in pre]
        influence_export=dict(units=list(units),overall_psi=influences[pos[id(overall)]],
                              overall_variance=sum(x*x for x in influences[pos[id(overall)]])/(n*n),
                              event_psi={str(e):ev_psi[e] for e in sorted(ev_psi)},
                              lead_event_times=pre,lead_cov=lead_cov,
                              definition='psi_u = unit influence score; Var = sum psi^2 / n^2; cov(e,e\') = sum psi(e) psi(e\') / n^2')
    out=dict(group_time=records[:cell_count],event_study=events,group=groups,calendar=calendars,overall=overall,
                inference=dict(B=B,seed=seed,method='unit Gaussian multiplier',pre_min_treated=5,
                               tested_pre_event_times=band_info['pre']['event_times'],bands=band_info),
                settings=dict(control_group=design['control_group'],aggregation='cohort size weighted supported e>=0 cells',
                              baseline='adjacent clean pre; last wholly untreated post',equal_jurisdiction_weights=True))
    if influence_export is not None: out['influence_export']=influence_export
    return out

def estimate_case(case, rows, treatment, *, window=None, control_group='never_treated',B=4999,seed=SEED,normalized=False,include_date_unverified=False):
    rows = rows if normalized else normalize_rows(case, rows, include_date_unverified=include_date_unverified)
    excluded = excluded_units(case, include_date_unverified=include_date_unverified)
    rows = [r for r in rows if r[0] not in excluded
                and ('include_units' not in case.get('sample_mask',{}) or r[0] in case['sample_mask']['include_units'])]
    design=structural_design(case,rows,treatment,window=window,control_group=control_group,normalized=True,include_date_unverified=include_date_unverified)
    outcomes={(u,t):float(y) for u,t,y,g in rows}
    return estimate_design(design,outcomes,B=B,seed=seed)

def _quantile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (position - lo) * (values[hi] - values[lo])
