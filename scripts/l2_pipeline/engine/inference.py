"""Fixed weighted ATT intervals with design-bound synthetic certification."""
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
import numpy as np
from scipy.stats import t as student_t
METHODS=("webb_wcu_t", "control_path_ct")
INTERVAL_COUNTS=dict(repetitions=1000, B=4999, heldout=100)
COVERAGE_MIN=.93


def _canonical(value):
    if isinstance(value, dict):
        return {json.dumps(_canonical(k), sort_keys=True): _canonical(v)
                for k,v in sorted(value.items(), key=lambda x: repr(x[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, np.ndarray):
        return _canonical(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def object_sha256(value):
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def design_binding(design):
    """Bind exact mask/exposure/topology and fixed ATT/CT donor operators."""
    g=geometry(design)
    operator={k:g[k] for k in ('H','A','Q','signs','units','periods','donors')}
    return dict(design_sha256=object_sha256(design), operator_sha256=object_sha256(operator))


def select_method(cases: dict, *, expected_designs, order=METHODS) -> str | None:
    """Select Webb then CT across the entire registered design inventory."""
    expected=list(expected_designs)
    if not isinstance(cases,dict):
        raise ValueError('Interval evaluation cases must be an object')
    if not expected or len(set(expected)) != len(expected) or set(cases) != set(expected):
        raise ValueError('Interval evaluation design inventory incomplete')
    if tuple(order) != METHODS:
        raise ValueError('Unregistered interval candidate order')
    for c in cases.values():
        if (not isinstance(c,dict) or not isinstance(c.get('summaries'),dict)
                or any(not isinstance(c['summaries'].get(name),dict) for name in METHODS)):
            raise ValueError('Interval candidate summaries missing/invalid')
        if any(type(c['summaries'][name].get('passed')) is not bool for name in METHODS):
            raise ValueError('Interval candidate passed flags must be literal booleans')
    return next((name for name in METHODS if all(c['summaries'][name]['passed'] is True
                                               for c in cases.values())), None)


def interval_summary(records, name):
    """Failures count against coverage and cannot certify a candidate."""
    from .simulation import wilson
    if (not isinstance(records,list) or not records
            or any(not isinstance(r,dict) or not isinstance(r.get(name),dict) for r in records)):
        raise ValueError('Interval world evidence missing/invalid')
    rows=[r[name] for r in records]
    valid=[r for r in rows if r.get('status') == 'ok']
    for row in valid:
        ci=row.get('ci95')
        if (not isinstance(ci,list) or len(ci)!=2 or any(type(x) not in (float,int)
                or not math.isfinite(x) for x in ci) or ci[0]>ci[1]
                or row.get('covered') is not (ci[0]<=0<=ci[1])
                or type(row.get('width')) not in (float,int)
                or row['width'] != ci[1]-ci[0]):
            raise ValueError('Interval world evidence invalid')
    if any(r.get('status') not in ('ok','failed') for r in rows):
        raise ValueError('Interval world status invalid')
    if any(not isinstance(r.get('reason'),str) or not r['reason'] for r in rows if r['status']=='failed'):
        raise ValueError('Interval failure reason missing')
    covered=sum(r['covered'] for r in valid)
    widths=[r['width'] for r in valid]
    n=len(records)
    return dict(covered=covered,repetitions=n,coverage=covered/n,
        monte_carlo_ci95=wilson(covered,n),failures=n-len(valid),
        median_width=float(np.median(widths)) if widths else None,
        p95_width=float(np.quantile(widths,.95)) if widths else None)


def interval_summaries(records, coverage_min=COVERAGE_MIN):
    summaries={name:interval_summary(records,name) for name in ('baseline',)+METHODS}
    baseline=summaries['baseline']
    for name in METHODS:
        s=summaries[name]
        ratios={}
        for metric in ('median_width','p95_width'):
            ratios[metric+'_ratio']=(s[metric]/baseline[metric]
                if s[metric] is not None and baseline[metric] is not None and baseline[metric]>0 else None)
        s.update(ratios)
        s['acceptable_width']=(all(v is not None for v in ratios.values())
            and ratios['median_width_ratio']<=2 and ratios['p95_width_ratio']<=3)
        s['passed']=(s['failures']==0 and baseline['failures']==0
                     and s['coverage']>=coverage_min and s['acceptable_width'])
    return summaries


def _digest_valid(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def validate_certification(evaluation, *, expected_designs, design=None, design_id=None):
    """Validate production evidence; a selected_method flag is never sufficient."""
    if not isinstance(evaluation,dict) or evaluation.get('schema')!='l2_interval_certification_v1':
        raise ValueError('Interval certification schema missing/invalid')
    if evaluation.get('candidate_order')!=list(METHODS):
        raise ValueError('Interval certification candidate order invalid')
    expected=list(expected_designs)
    if evaluation.get('expected_designs')!=expected:
        raise ValueError('Interval certification registered design inventory differs')
    cases=evaluation.get('cases',{})
    method=select_method(cases,expected_designs=expected)
    for key,c in cases.items():
        if (c.get('case_id')!=key or c.get('counts')!=INTERVAL_COUNTS
                or any(type(v) is not int for v in c.get('counts',{}).values())
                or c.get('coverage_min')!=COVERAGE_MIN):
            raise ValueError('Interval certification registered counts/target differ')
        if c.get('schema')!='l2_interval_evaluation_v1' or c.get('production_ready') is not True or c.get('actual_data_estimation_calls')!=0 or c.get('treated_post_outcome_fields_parsed')!=0:
            raise ValueError('Interval certification is not production synthetic evidence')
        if c.get('seed')!=20261001+4000000 or c.get('world_stage')!='evaluation':
            raise ValueError('Interval certification world/draw seed differs')
        if any(not _digest_valid(c.get(k)) for k in ('design_sha256','operator_sha256','world_sha256','world_records_sha256','heldout_world_records_sha256')):
            raise ValueError('Interval certification design/world hash missing')
        records=c.get('world_records',[])
        if (not isinstance(records,list) or len(records)!=1000
                or any(not isinstance(r,dict) or type(r.get('repetition')) is not int for r in records)
                or [r.get('repetition') for r in records]!=list(range(1000))):
            raise ValueError('Interval certification clean world inventory incomplete')
        if object_sha256(records)!=c['world_records_sha256']:
            raise ValueError('Interval certification world evidence hash mismatch')
        heldout=c.get('heldout_world_records')
        if (heldout!=records[:100] or object_sha256(heldout)!=c['heldout_world_records_sha256']
                or c.get('heldout_summaries')!=interval_summaries(heldout)):
            raise ValueError('Interval certification held-out evidence incomplete')
        if c.get('summaries')!=interval_summaries(records):
            raise ValueError('Interval certification coverage/width evidence differs')
    if evaluation.get('selected_method')!=method:
        raise ValueError('Interval certification selection differs from registered order')
    if design is not None:
        if design_id not in cases:
            raise ValueError('Current design missing from interval certification')
        if any(cases[design_id].get(k)!=v for k,v in design_binding(design).items()):
            raise ValueError('Interval certification current design/operator differs')
    return method


def apply_reporting(result: dict, design: dict, outcomes: dict, *,
                    evaluation_path: str | Path | None = None,
                    primary: bool = False, B: int = INTERVAL_COUNTS['B'],
                    seed: int = 20261001 + 4000000,
                    design_id=None, expected_designs=None) -> dict:
    """Report primary overall CI only after complete frozen certification."""
    for row in [*result['group_time'], *result['event_study'], *result['group'],
                *result['calendar'], result['overall']]:
        for field in list(row):
            if field in {'ci95', 'se'} or 'simultaneous95' in field:
                row[field] = None
        row['interval'] = dict(method=None, reported=False, reason='No coverage calibration for this row')
    method=None
    certified=[]
    valid=False
    reason='No interval certification; point and diagnostic RI p only'
    if evaluation_path is not None:
        try:
            evaluation=json.loads(Path(evaluation_path).read_text())
            if expected_designs is None or design_id is None:
                raise ValueError('Authoritative interval inventory/current design identity required')
            method=validate_certification(evaluation,expected_designs=expected_designs,
                design=design,design_id=design_id)
            certified=[name for name in METHODS if all(
                case['summaries'][name]['passed'] is True for case in evaluation['cases'].values())]
            valid=True
            reason='No registered candidate passed all designs; point and diagnostic RI p only'
        except (ValueError, KeyError, TypeError) as exc:
            reason='Invalid interval certification: '+str(exc)
    result['inference']['interval_reporting'] = dict(selected_method=method,
        certified_methods=certified, calibrated_method=method, current_panel_failures={},
        primary_only=True, certification_valid=valid,
        evaluation_sha256=sha(evaluation_path) if evaluation_path is not None else None)
    if primary and method:
        if B!=INTERVAL_COUNTS['B'] or seed!=20261001+4000000:
            raise ValueError('Reported interval draws/seed differ from certification')
        g=geometry(design)
        # Every certified candidate gets its own algebraic evaluation. A Webb
        # failure cannot veto a CT interval that independently passed the same
        # frozen design inventory's coverage and width gates.
        rec=candidates(g,outcome_matrix(g,outcomes),draws(g,B,seed),methods=tuple(certified))
        reported=next((name for name in certified if rec[name] is not None),None)
        result['inference']['interval_reporting'].update(selected_method=reported,
            current_panel_failures=rec['failures'])
        if reported is not None:
            result['overall']['ci95']=rec[reported]
            result['overall']['interval']=dict(method=reported,reported=True,nominal_level=.95,
                assumptions='Independent exchangeable untreated state residual paths; fixed weighted ATT',approximate=True)
        else:
            result['overall']['interval']['reason']='Certified candidates failed on current panel: '+ '; '.join(
                name+': '+rec['failures'][name] for name in certified)
    elif primary:
        result['overall']['interval']['reason']=reason
    return result


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def geometry(design, donor_units=None):
    units = design['units']; periods = design['periods']
    ui = {u:i for i,u in enumerate(units)}; ti = {t:i for i,t in enumerate(periods)}
    cells = [c for c in design['cells'] if c['post']]
    mass = sum(c['n_treated'] for c in cells)
    H = np.zeros((len(units), len(periods)))
    A = np.zeros((len(cells), len(units))); Q = A.copy(); signs = A.copy()
    for j,c in enumerate(cells):
        a = c['n_treated']/mass
        for role,sign in (('treated',1),('controls',-1)):
            for u in c[role]:
                i = ui[u]; w = a*sign/len(c[role])
                H[i,ti[c['t']]] += w; H[i,ti[c['base_period']]] -= w
                A[j,i] = w; Q[j,i] = .5/len(c[role]); signs[j,i] = sign
    if not np.allclose(H.sum(axis=0),0,atol=1e-12) or not np.allclose(H.sum(axis=1),0,atol=1e-12):
        raise ValueError('Fixed ATT operator does not cancel unit/calendar levels')
    if donor_units is None: donor_units=design.get('CT_complete_path_reference_controls')
    donors = [ui[u] for u in units if design['whole_window_clean'][u] and (donor_units is None or u in donor_units)]
    return dict(H=H,A=A,Q=Q,signs=signs,cells=cells,units=units,periods=periods,ui=ui,ti=ti,donors=donors)

def draws(g,B,seed):
    rng = np.random.default_rng(seed)
    webb = np.array([-np.sqrt(1.5),-1.,-np.sqrt(.5),np.sqrt(.5),1.,np.sqrt(1.5)])
    return dict(webb=webb[rng.integers(0,6,(B,len(g['units'])))],
                donors=(rng.integers(0,len(g['donors']),(B,len(g['units'])))
                        if g['donors'] else None))

def outcome_matrix(g,outcomes):
    # Unused cells stay zero. Missing complete-path donor cells invalidate CT
    # independently; they do not enter the fixed Webb ATT operator.
    Y = np.zeros_like(g['H'])
    g['incomplete_donors']=[]
    for i,u in enumerate(g['units']):
        for j,t in enumerate(g['periods']):
            if (u,t) in outcomes: Y[i,j] = outcomes[u,t]
            elif g['H'][i,j]!=0:
                raise ValueError('Missing fixed-operator cell')
            elif i in g['donors']:
                g['incomplete_donors'].append((u,t))
    if not np.isfinite(Y).all(): raise ValueError('Nonfinite interval input')
    return Y

def candidates(g,Y,d, *, methods=METHODS):
    """Compute each candidate independently, retaining algebraic failures."""
    if any(name not in METHODS for name in methods):
        raise ValueError('Unknown interval candidate')
    H,A,Q,S = (g[k] for k in ('H','A','Q','signs'))
    n = len(g['units']); att = float(np.sum(H*Y))
    delta = np.zeros_like(A); centered = np.zeros_like(A)
    for j,c in enumerate(g['cells']):
        for role in ('treated','controls'):
            ix = [g['ui'][u] for u in c[role]]
            delta[j,ix] = Y[ix,g['ti'][c['t']]]-Y[ix,g['ti'][c['base_period']]]
            centered[j,ix] = delta[j,ix]-delta[j,ix].mean()
    tau = float(np.sum(A*delta))
    # Same identity, rounding-aware: the tolerance never falls below the old absolute 1e-9
    # and grows with the summed magnitude of the terms, so count-scale outcomes (1e6+)
    # do not trip on float rounding while a real weight mismatch still does.
    scale = max(1.0, float(np.sum(np.abs(H*Y))), float(np.sum(np.abs(A*delta))))
    if abs(tau-att)>1e-9*scale: raise ValueError('Stacked WLS differs from registered weighted ATT')
    rec=dict(att=att,failures={},**{name:None for name in METHODS})
    for name in methods:
        try:
            if name=='webb_wcu_t':
                alpha = np.sum(Q*delta,axis=1)
                E = (delta-alpha[:,None]-S*att/2)*(S!=0)
                h = np.sum(A*E,axis=0)
                K = np.diag(h)-A.T@(Q*E)-np.outer(np.sum(A*S/2,axis=0),h)
                cr1 = n/(n-1); se = float(np.sqrt(cr1*np.sum(h*h)))
                boot_error = d['webb']@h
                boot_se = np.sqrt(cr1*np.sum((d['webb']@K.T)**2,axis=1))
                if se<=0 or np.any(boot_se<=0) or not np.isfinite(boot_se).all():
                    raise ValueError('Degenerate Webb studentization')
                critical = max(float(student_t.ppf(.975,n-1)),float(np.quantile(np.abs(boot_error)/boot_se,.95)))
                ci = [att-critical*se,att+critical*se]
                rec.update(webb_se=se,webb_critical=critical)
            else:
                if len(g['donors'])<5:
                    raise ValueError('CT needs at least five certified never-control paths')
                if g.get('incomplete_donors'):
                    raise ValueError('Missing complete CT donor cell')
                R = Y[g['donors']]; R = R-R.mean(axis=0)
                projected = R@H.T
                errors = projected[d['donors'],np.arange(n)].sum(axis=1)*np.sqrt(len(R)/(len(R)-1))
                ci = [att-float(np.quantile(errors,.975)),att-float(np.quantile(errors,.025))]
            if not all(math.isfinite(x) for x in ci) or ci[0]>ci[1]:
                raise ValueError('Nonfinite or reversed candidate interval')
            rec[name]=ci
        except (ValueError,FloatingPointError,ZeroDivisionError) as exc:
            rec['failures'][name]=str(exc)
    old = np.sum(A*centered,axis=0)
    mass=sum(x['n_treated'] for x in g['cells'])
    for j,c in enumerate(g['cells']):
        cellatt = np.sum(A[j]*delta[j])/(c['n_treated']/mass)
        for u in c['treated']:
            old[g['ui'][u]] += (cellatt-att)/mass
    old_se = float(np.linalg.norm(old))
    rec['baseline']=[att-1.96*old_se,att+1.96*old_se]
    return rec
