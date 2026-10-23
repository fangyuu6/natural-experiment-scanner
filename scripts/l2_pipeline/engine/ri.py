"""Fixed relabeling, prospective feasibility and diagnostics; no redraw."""
from __future__ import annotations
import copy, hashlib, json, math, random, statistics
from . import estimator as engine
from .design import treatment_rows
from .. import gate_relax
SEED=engine.SEED
RI_SEED=engine.RI_SEED
CHECKS=engine.CHECKS
DEFAULT_FLOOR=.9
FAILURES=("Permutation has no post support", "No supported post cells", "NOT_ASSESSABLE: empty supported pre family", "RI lead inventory changed under relabeling", "RI supported family changes under relabeling; no redraw permitted")
def encoded(value): return json.dumps(value,sort_keys=True,separators=(",", ":"),allow_nan=False).encode()
def support_failure(exc):
    return isinstance(exc, ValueError) and any(str(exc).startswith(s) for s in FAILURES)

def inventory(records, floor=None):
    n = len(records)
    bad = [dict(draw_index=i, reason=r['reason']) for i,r in enumerate(records) if r.get('infeasible')]
    feasible = n-len(bad)
    if floor is not None:
        # R3.1 (gate_relax): exact integer floor; every infeasible draw must carry an allowed reason code.
        floor = gate_relax.checked_floor(floor)
        codes = {code: 0 for code in gate_relax.REASON_CODES}
        for b in bad:
            code = gate_relax.reason_code(b['reason'])
            if code is not None: codes[code] += 1
        uncoded = len(bad)-sum(codes.values())
        return dict(requested_draws=n, feasible_draws=feasible, infeasible_count=len(bad),
                    infeasible_draws=bad, feasible_fraction=feasible/n if n else None,
                    minimum_feasible_fraction=floor, support_pass=bool(n and feasible>=gate_relax.required_draws(n,floor) and uncoded==0),
                    redrawn=0, rule='Fixed draws; condition on feasible assignments; exclude without redraw',
                    ri_floor_version=gate_relax.RULE, reason_codes=codes)
    return dict(requested_draws=n, feasible_draws=feasible, infeasible_count=len(bad),
                infeasible_draws=bad, feasible_fraction=feasible/n if n else None,
                minimum_feasible_fraction=.9, support_pass=bool(n and 10*feasible>=9*n),
                redrawn=0, rule='Fixed draws; condition on feasible assignments; exclude without redraw')

def require_feasible(record):
    if not record['support_pass']:
        pct = round(record.get('minimum_feasible_fraction', DEFAULT_FLOOR)*100)
        raise ValueError(f'NOT_ASSESSABLE: RI feasible draws below {pct}%: '+str(record))

def pre_family(design):
    return sorted(e for e,r in engine.event_operators(design).items() if e<0 and r['n_treated']>=5)

def predraw_labels(design, count=999):
    rng=random.Random(RI_SEED)
    labels=[(design['adoption'][u],None if design['adoption'][u] is None else
             design['operative'][u]-design['adoption'][u],design['exposure'][u],
             design['whole_window_clean'][u]) for u in design['units']]
    draws=[]
    for _ in range(count):
        shuffled=list(labels); rng.shuffle(shuffled); draws.append(shuffled)
    return draws

def relabeled_design(declaration, design, labels):
    units=design['units']; adoption={u:x[0] for u,x in zip(units,labels)}
    operative={u:None if x[0] is None else x[0]+x[1] for u,x in zip(units,labels)}
    case=copy.deepcopy(declaration['case'])
    # R6 and all sample masks have already fixed the eligible units/cells.
    case.pop('cohort_preperiod_rule',None)
    rows=[(u,t,0.,adoption[u]) for u,t in sorted(design['mask'])]
    exposure={u:x[2] for u,x in zip(units,labels)}
    if design['control_group']=='never_treated':
        from .structure import primary_structural_design
        allowed=sorted(u for u,x in zip(units,labels) if x[3])
        result=primary_structural_design(case,rows,treatment_rows(declaration),
            never_controls=allowed,operative=operative,normalized=True,exposure_override=exposure,
            control_group=design['control_group'])
    else:
        result=engine.structural_design(case,rows,treatment_rows(declaration),operative=operative,
            normalized=True,exposure_override=exposure,control_group=design['control_group'])
    if not any(c['post'] for c in result['cells']): raise ValueError('Permutation has no post support')
    return result

def one_reference(labels, context=None):
    if context is None:
        raise ValueError('Explicit reference context required')
    declaration,design,outcomes,family=context[:4]
    export_options=context[4] if len(context)>4 else None
    try:
        fake=relabeled_design(declaration,design,labels)
        if pre_family(fake)!=family: raise ValueError('RI lead inventory changed under relabeling')
    except ValueError as exc:
        if not support_failure(exc): raise
        return dict(infeasible=True,reason=str(exc))
    ops=engine.event_operators(fake)
    value=lambda w: sum(weight*outcomes[key] for key,weight in w.items())
    pre=[value(ops[e]['weights']) for e in family]
    post=[c for c in fake['cells'] if c['post']]; mass=sum(c['n_treated'] for c in post)
    record = dict(overall=sum(c['n_treated']*value(engine.contrast_weights(c)) for c in post)/mass,
                rms=math.sqrt(statistics.mean(v*v for v in pre)))
    if export_options is not None:
        from ..l3_addendum import placebo_fields
        record['l3_export_v2'] = placebo_fields(declaration, fake, outcomes, export_options.get('purpose_direction'), original_design=design)
        amendment=export_options.get('amendment_v2') or {}
        if amendment.get('schema')=='l2_l3_amendment_c70_v1' and amendment.get('status')!='fields unavailable':
            from ..l3_addendum import cohort_points
            record['l3_export_v2']['group']=cohort_points(declaration,fake,outcomes)
    return record


def permutation_reference(declaration: dict, design: dict, outcomes: dict, *,
                          count: int = 999, seed: int = RI_SEED, l3_export=None) -> dict:
    """Pre-draw ordered complete label bundles and retain every failed index.

    The skeleton executes sequentially; external task pools can map
    one_reference over the same predrawn labels without changing RNG order.
    """
    if seed != RI_SEED:
        raise ValueError('Alternative RI seeds require a separately versioned protocol')
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError('Positive fixed RI draw count required')
    family = pre_family(design)
    if not family:
        raise ValueError('NOT_ASSESSABLE: empty supported pre family')
    floor = gate_relax.floor_from_declaration(declaration)
    context = declaration, design, outcomes, family
    labels = predraw_labels(design, count)
    if l3_export is not None: context = (*context, l3_export)
    records = [one_reference(d, context) for d in labels]
    feasibility = inventory(records, floor=floor)
    require_feasible(feasibility)
    feasible = [r for r in records if not r.get('infeasible')]
    result = dict(overall=[r['overall'] for r in feasible], rms=[r['rms'] for r in feasible],
                feasibility=feasibility, event_times=family, draws=count, seed=seed,
                draw_sha256=hashlib.sha256(encoded([{k:v for k,v in r.items() if k!='l3_export_v2'} for r in feasible])).hexdigest())
    if floor is not None:
        # The observed assignment is the built design passed in (a design that did not build stopped earlier).
        result['observed_assignment_feasible'] = True
        result['feasibility_199'] = inventory(records[:199], floor=floor)
    if l3_export is not None:
        result['placebo_profiles'] = [dict(draw_index=i, **r['l3_export_v2']) for i,r in enumerate(records) if not r.get('infeasible')]
        if any('group' in r.get('l3_export_v2',{}) for r in records):
            result['placebo_group']=[dict(draw_index=i,status='SUPPORT' if r.get('infeasible') else 'AVAILABLE',reason=r.get('reason'),group=r.get('l3_export_v2',{}).get('group')) for i,r in enumerate(records)]
        result['placebo_labels'] = dict(units=design['units'], label_fields=['adoption','operative_offset','exposure','whole_window_clean'], draws=labels, seed=seed, generator_sha256=hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest())
    return result

def diagnostics(design,outcomes,result,reference,*,B=4999,on_estimate=None):
    ops=engine.event_operators(design); family=pre_family(design)
    value=lambda w: sum(weight*outcomes[key] for key,weight in w.items())
    pre=[value(ops[e]['weights']) for e in family]
    if not pre: raise ValueError('NOT_ASSESSABLE: empty supported pre family')
    rms=math.sqrt(statistics.mean(v*v for v in pre))
    p=(1+sum(v>=rms for v in reference['rms']))/(len(reference['rms'])+1)
    placebo=engine.placebo_operator(design)
    record={k:v for k,v in placebo.items() if k not in ('weights','cells')}
    if not placebo['mass']: raise ValueError('NOT_ASSESSABLE: no supported placebo cohort')
    placebo_att=value(placebo['weights'])
    if on_estimate is not None:on_estimate()
    fit=engine.estimate_design(dict(design,cells=[dict(c,post=True,event_time=c['t']-(c['base_period']+1))
                        for c in placebo['cells']]),outcomes,B=B,seed=SEED)
    record.update(fit['overall'],att=placebo_att)
    record['ci95']=None
    record['se']=None
    record['interval']=dict(method=None,reported=False,reason='Placebo interval has no coverage calibration')
    return dict(statistics=dict(pretrend_joint=p,pretrend_economic=max(abs(v) for v in pre),placebo=abs(placebo_att)),
                pretrend_ri=dict(p_value=p,statistic=rms,event_times=family,null_statistics=reference['rms'],
                                draws=len(reference['rms']),seed=RI_SEED,feasibility=reference['feasibility']),placebo=record)

def gate_checks(stats,critical):
    if set(stats)!=set(CHECKS) or set(critical)!=set(CHECKS): raise ValueError('Incomplete calibrated gate inventory')
    if any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in [*stats.values(),*critical.values()]):
        raise ValueError('Nonfinite diagnostic/gate')
    return [dict(gate=k,check_class='B',blocking=True,statistic=stats[k],critical_value=critical[k],
                 rule='strictly below' if k=='pretrend_joint' else 'strictly above',
                 passed=not (stats[k]<critical[k] if k=='pretrend_joint' else stats[k]>critical[k])) for k in CHECKS]
