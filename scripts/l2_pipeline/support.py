"""Metadata-only preflight: outcomes and associations are never decoded here."""
from __future__ import annotations
from collections import Counter
from typing import Iterable, Mapping
from .engine.panels import transition_year_drop_enabled, transition_year_keys

V7_SUPPORT_GAP_RULE = "drop_affected_state_v7"
ENGINE_MINIMUMS = dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3)


def minimum_post_periods(analysis):
    value = analysis.get('min_post_periods', 2)
    if type(value) is not int or value < 1:
        raise ValueError('Invalid min_post_periods support threshold')
    return max(2, value)


def operation_years(treatment, enabled):
    from .prewindow import forbid
    forbid(treatment, 'cohort/operation-year code (T192)')
    # Future adopters still have an operation year to exclude from RI/noise
    # geometry. They remain outside treated cohorts and never-treated controls.
    return {r['unit']: r.get('operative_period',r['start_period']) for r in treatment
            if r['status']=='treated' or (enabled and r['status']=='not_yet_treated'
                and r.get('engine_clock')=='operation_year_drop_then_shift_once')}


def registered_support_gap_rule(analysis: Mapping) -> str | None:
    rule = analysis.get("support_gap_rule")
    if rule not in (None, V7_SUPPORT_GAP_RULE):
        raise ValueError("Unknown registered support gap rule")
    return rule

def declared_annual_periods(outcome: Mapping) -> list[int]:
    """The registered calendar exists independently of observed metadata keys."""
    if outcome.get("period_years") is not None:
        years = list(outcome["period_years"])
    else:
        first, last = outcome["years"]
        years = list(range(first, last + 1))
    if not years or any(type(y) is not int for y in years) or years != sorted(set(years)):
        raise ValueError("Invalid declared annual calendar")
    return years


def standard_annual_declaration(treatment: list[dict], analysis: Mapping,
                                period_years: list[int], *, key="metadata", variant="primary",
                                summary=None, transition_year_drop=False) -> dict:
    """Shared metadata and numerical declaration for annual adoption labels."""
    rule = registered_support_gap_rule(analysis)
    if summary and summary.get("support_gap_rule") != rule:
        raise ValueError("Registered support gap rule differs from metadata summary")
    excluded = set(summary.get("support_gap_excluded_units", [])) if summary else set()
    if excluded and rule != V7_SUPPORT_GAP_RULE:
        raise ValueError("Support exclusions require the registered V7 drop rule")
    excluded |= set(summary.get('post_support_excluded_units', [])) if summary else set()
    treatment = [r for r in treatment if r["unit"] not in excluded]
    enabled = transition_year_drop_enabled(dict(transition_year_drop=transition_year_drop))
    if summary and transition_year_drop_enabled(summary) != enabled:
        raise ValueError('Registered transition-year drop differs from metadata summary')
    units = sorted(r["unit"] for r in treatment if r["status"] != "unknown")
    effective = operation_years(treatment, enabled)
    cohorts = {r['unit']: r['start_period'] + int(enabled) for r in treatment if r['status']=='treated'}
    result = dict(key=key, variant=variant, cohorts=cohorts, operative_years=effective,
        never_controls=sorted(r["unit"] for r in treatment if r["status"] == "never_treated"),
        case=dict(min_post_periods=minimum_post_periods(analysis), min_controls=analysis.get("min_controls",1),
            panel=dict(frequency="annual", time_basis=dict(kind="calendar_year")),
            windows=dict(primary=dict(start=period_years[0], end=period_years[-1],
                excluded_periods=analysis.get("excluded_periods", []))),
            sample_mask=dict(include_units=units), comparison=dict(exclusions=[]),
            exposure_clock=dict(date_column="operative_date")), summary=summary or {})
    from .v11_v12 import declared_group
    group=declared_group(analysis,summary)
    if group=='not_yet_treated':
        result['operative_years'].update({r['unit']:r.get('operative_period',r['start_period']) for r in treatment if r['status']=='not_yet_treated'})
        result['control_group']=group
        result['cohorts'].update({r['unit']:r['start_period']+int(enabled) for r in treatment if r['status']=='not_yet_treated'})
        result['case'].update(v11_min_controls=max(5,analysis.get('min_controls',5)),
                            v11_min_post_periods=max(2,analysis.get('min_post_periods',2)),
                            v11_min_pre_periods=max(3,analysis.get('min_pre_periods',3)))
    ends={r['unit']:r['censor_after_period'] for r in treatment if r.get('censor_after_period') is not None}
    if ends: result['censor_after_periods']=ends
    if enabled:
        result.update(transition_year_drop=True, transition_effective_years=effective)
    return result


def support_check(keys: Iterable[tuple[str,int]], treatment: list[dict], analysis: Mapping,
                  *, period_years: list[int] | None = None, transition_year_drop=False, omitted_cells=()) -> dict:
    minimum_post = minimum_post_periods(analysis)
    enabled = transition_year_drop_enabled(dict(transition_year_drop=transition_year_drop))
    effective = operation_years(treatment, enabled)   # also refuses pre_window_bound rows (T192)
    transition_keys = transition_year_keys(effective, enabled) | set(omitted_cells)
    original_treatment = treatment
    if enabled:
        treatment = [dict(r, start_period=r['start_period']+1) if r['status'] in {'treated','not_yet_treated'} else dict(r)
                     for r in treatment]
    rule = registered_support_gap_rule(analysis)
    if rule:
        analysis = dict(analysis)
        for field, minimum in ENGINE_MINIMUMS.items():
            value = analysis.get(field, minimum)
            if type(value) is not int or value < 1:
                raise ValueError("Invalid support thresholds")
            analysis[field] = max(minimum, value)
    keys=set(keys); counts=Counter(); cells=[]; units={r["unit"]:r for r in treatment}
    if len(units)!=len(treatment): raise ValueError("Duplicate treatment unit")
    years=list(period_years) if period_years is not None else sorted({t for _,t in keys})
    unknown=[u for u in sorted(units) if units[u]["status"]=="unknown"]
    if unknown: return {"passed":False,"disposition":"parked","reason":"Unknown treatment units","units":unknown}
    if not years: return {"passed":False,"disposition":"parked","reason":"No outcome coverage"}
    omitted=set(analysis.get("excluded_periods", []));years=[y for y in years if y not in omitted]
    if not years: return {"passed":False,"disposition":"parked","reason":"No retained calendar periods"}
    keys={(u,t) for u,t in keys if u in units and t in years and (u,t) not in transition_keys
          and (units[u].get('censor_after_period') is None or t<=units[u]['censor_after_period'])}
    support_gap_drops = []
    if rule:
        for u in sorted(units):
            missing = [t for t in years if (u,t) not in keys and (u,t) not in transition_keys
                       and (units[u].get('censor_after_period') is None or t<=units[u]['censor_after_period'])]
            row = units[u]
            pre = [t for t in years if (u,t) in keys and t < min(row["start_period"],effective.get(u,row["start_period"]))] if row["status"]=="treated" else []
            insufficient_pre = row["status"]=="treated" and len(pre)<analysis["min_pre_periods"]
            no_post = row["status"]=="treated" and not any(t>=row["start_period"] for t in years)
            if missing or insufficient_pre or no_post:
                support_gap_drops.append(dict(unit=u, status=units[u]["status"],
                    missing_periods=missing, clean_pre_periods=pre,
                    reason="Registered outcome metadata lacks support" if missing else
                    "Insufficient registered clean pre-periods" if insufficient_pre else
                    "No registered post-period support"))
        excluded = {r["unit"] for r in support_gap_drops}
        treatment = [r for r in treatment if r["unit"] not in excluded]
        units = {r["unit"]: r for r in treatment}
        keys = {(u,t) for u,t in keys if u not in excluded}
    missing_years=[y for y in years if not any(t==y for _,t in keys)]
    if missing_years: counts["missing_declared_calendar_periods"]+=len(missing_years)
    minimum_pre=analysis["min_pre_periods"]; minimum_controls=analysis["min_controls"]
    never=sorted(u for u,r in units.items() if r["status"]=="never_treated")
    from .v11_v12 import comparison_group
    group=analysis.get('comparison_group') or ('not_yet_treated' if analysis.get('controls')=='not_yet_treated' else comparison_group(treatment) if rule or analysis.get('min_controls',5)>=5 else 'never_treated')
    cohorts=sorted({r["start_period"] for r in treatment if r["status"]=="treated"})
    post_drops = []
    censor_block = None
    if 'cohort_censor_rule' in analysis:
        # R-S3C1 (cohort_censor_rule.py): only a card carrying the block changes the per-cell test below.
        from . import cohort_censor_rule
        censor_block = cohort_censor_rule.block_from_analysis(analysis)
        if group != 'never_treated': raise ValueError('R-S3C1 applies to never-treated comparison only')
    censor_cohorts = []
    for g in cohorts:
        treated=sorted(u for u,r in units.items() if r["status"]=="treated" and r["start_period"]==g)
        pre=[t for t in years if t<g and all((u,t) in keys and t<effective.get(u,g) for u in treated)]
        base=max(pre, default=g-1) if enabled or any("operative_period" in units[u] for u in treated) else g-1
        if len(pre)<minimum_pre: counts["insufficient_pre"]+=1; continue
        cohort_cells = []; unsupported = 0; censor_cells = []
        for t in years:
            if t<g: continue
            pool=never if group=='never_treated' else sorted(u for u,r in units.items() if r['status']=='never_treated' or min(r['start_period'],r.get('operative_period',r['start_period']))>max(t,base))
            controls=[u for u in pool if (u,t) in keys and (u,base) in keys]
            complete=[u for u in treated if (u,t) in keys and (u,base) in keys]
            if len(controls)<minimum_controls: unsupported += 1; continue
            if len(complete)!=len(treated):
                decision = cohort_censor_rule.cell_decision(censor_block, treated, complete, units, t) if censor_block else 'unsupported'
                if decision == 'skip': continue
                if decision != 'relaxed': unsupported += 1; continue
                censor_cells.append(cohort_censor_rule.cell_record(g, t, treated, complete))
            cohort_cells.append({"cohort":g,"period":t,"treated":complete,"controls":controls,"base_period":base})
        if group == 'never_treated' and len({c['period'] for c in cohort_cells}) < minimum_post:
            post_drops.append(dict(cohort=g, members=treated,
                retained_post_periods=sorted({c['period'] for c in cohort_cells}),
                min_post_periods=minimum_post, reason='Insufficient registered retained post-periods'))
        else:
            cells.extend(cohort_cells)
            counts['unsupported_post_cells'] += unsupported
            if censor_cells: censor_cohorts.append(dict(cohort=g, roster=treated, cells=censor_cells))
    post_excluded = {u for r in post_drops for u in r['members']}
    treatment = [r for r in treatment if r['unit'] not in post_excluded]
    units = {r['unit']:r for r in treatment}
    keys = {(u,t) for u,t in keys if u not in post_excluded}
    cohorts = sorted(set(cohorts)-{r['cohort'] for r in post_drops})
    if not counts['unsupported_post_cells']: counts.pop('unsupported_post_cells',None)
    treated_count=sum(r["status"]=="treated" for r in treatment)
    if treated_count<analysis.get("min_treated",1): counts["insufficient_treated"]+=1
    supported_cohorts={c["cohort"] for c in cells}
    if rule:
        if len(supported_cohorts) < analysis["min_cohorts"]: counts["insufficient_cohorts"] += 1
        if group=='never_treated' and len(never) < analysis["min_controls"]: counts["insufficient_controls"] += 1
    if set(cohorts)-supported_cohorts: counts["cohort_without_post_support"]+=len(set(cohorts)-supported_cohorts)
    # Build the actual pre and post geometry with zero placeholders only.
    from .engine.design import build_design
    declaration=standard_annual_declaration(original_treatment,analysis,years,
        summary={'support_gap_rule':rule,'support_gap_excluded_units':[r['unit'] for r in support_gap_drops],
                 'comparison_group':group,'post_support_excluded_units':sorted(post_excluded),**({'transition_year_drop':True} if enabled else {})},
        transition_year_drop=enabled)
    engine_error=None
    try:
        design=build_design(declaration,[(u,t,0.,None) for u,t in sorted(keys)])
        inventory=design["cells"];anchor_units=design["units"]
        anchor_years=[t for t in years if all((u,t) in keys
            and design["exposure"][u].get(t,{}).get("clean",False) for u in anchor_units)]
        engine_posts=[dict(cohort=c["g"],period=c["t"],treated=c["treated"],
            controls=c["controls"],base_period=c["base_period"]) for c in inventory if c["post"]]
        if group=='not_yet_treated':
            cells=engine_posts
            supported_cohorts={c['cohort'] for c in cells}
            counts.pop('unsupported_post_cells',None)
            counts.pop('cohort_without_post_support',None)
            counts.pop('insufficient_pre',None)
            counts.pop('insufficient_cohorts',None)
            if len(supported_cohorts)<analysis['min_cohorts']: counts['insufficient_cohorts']+=1
            if len({u for c in cells for u in c['treated']})<max(6,analysis.get('min_treated',6)): counts['insufficient_treated']+=1
        if engine_posts!=cells: counts["numerical_post_inventory_differs"]+=1
        if len(anchor_years)<2 or len(anchor_units)<2: counts["insufficient_common_clean_anchor"]+=1
    except ValueError as exc:
        engine_error=str(exc);inventory=[];anchor_units=[];anchor_years=[]
        counts["engine_metadata_adapter"]+=1
    passing=len(cells)>0 and len(supported_cohorts)>=analysis["min_cohorts"] and not counts
    result = {"passed":passing,"support_pass":passing,"disposition":"ready" if passing else "stopped",
        "reason":"Supported metadata topology" if passing else "Registered support insufficient",
        "cohorts":len(supported_cohorts),"post_cells":len(cells),"issues":dict(counts),"cells":cells,
        "cell_inventory":inventory,"anchor_years":anchor_years,"anchor_unit_n":len(anchor_units),
        "anchor_units":anchor_units,"anchor_value_computed":False,"engine_error":engine_error,
        "analysis_mask":[list(k) for k in sorted(keys)],"period_years":years,
        "missing_declared_calendar_periods":missing_years,"outcome_values_parsed":0,
        "min_post_periods":minimum_post,"post_support_drops":post_drops,
        "post_support_excluded_units":sorted(post_excluded),
        "support_gap_rule":rule,"support_gap_drops":support_gap_drops,
        "support_gap_excluded_units":[r["unit"] for r in support_gap_drops],
        "retained_treated":sorted({u for c in cells for u in c["treated"]}) if group=="not_yet_treated" else sorted(u for u,r in units.items() if r["status"]=="treated"),
        "never_controls":never,"comparison_group":group,"comparison_controls":sorted({u for c in cells for u in c["controls"]}),"effective_support_minimums":{
            k:analysis.get(k,1) for k in ENGINE_MINIMUMS},
        "metadata_only":True,"estimator_calls":0}
    if censor_block:
        result["cohort_censor"] = dict(rule=cohort_censor_rule.RULE, mode=cohort_censor_rule.validate_block(censor_block),
            cohorts=[dict(cohort=c["cohort"], roster=c["roster"], n_roster=len(c["roster"])) for c in censor_cohorts],
            cells=[x for c in censor_cohorts for x in c["cells"]])
    if enabled:
        result.update(transition_year_drop=True,
                      transition_years_dropped=[list(k) for k in sorted(transition_keys)
                                               if k[0] in units and k[1] in years])
    return result


# Annual full-year support adapter v1: batch4 metadata geometry, independently
# copied engine. Frozen batches are not imported or edited.
import copy
from .engine.structure import primary_structural_design
from .engine.estimator import structural_design
from .engine.exposure import annual_before_cessation, annual_exposure_profile

def placebo_support(design):
    """Mirror frozen placebo_operator's eligibility only, selecting no weights."""
    records = []
    for g in design["groups"]:
        members = [u for u in design["units"] if design["adoption"][u] == g]
        common = [t for t in design["periods"] if all(
            t < design["adoption"][u] and (u,t) in design["mask"]
            and design["exposure"][u][t]["clean"] for u in members)]
        b = max(common) if common else None
        endpoints = [] if b is None else [b-3,b-2,b-1,b]
        reason = "No common wholly clean pre-adoption period" if b is None else None
        if not reason and (any(t not in design["periods"] for t in endpoints) or any(
            (u,t) not in design["mask"] or not design["exposure"][u].get(t,{}).get("clean",False)
            or t >= design["adoption"][u] for u in members for t in endpoints)):
            reason = "Fixed four-period placebo endpoints are absent or exposed"
        from .v11_v12 import placebo_controls
        controls = [] if reason else placebo_controls(design,members,endpoints)
        if not reason and (not controls or (design.get("v11_comparison") and design["control_group"]=="not_yet_treated" and len(controls)<5)): reason = "No wholly clean never-treated support"
        records.append({"cohort":g,"status":"placebo_unsupported" if reason else "supported",
                        "reason":reason,"last_common_clean_period":b,"endpoints":endpoints,
                        "units":members,"n_control":len(controls)})
    return records


def annual_full_year_support(declaration, variant, metadata_rows):
    d=copy.deepcopy(declaration);key=d['key'];start,end=d['window'];years=d['period_years']
    minimum_post=minimum_post_periods(d)
    transition_drop=transition_year_drop_enabled(d)
    rule=registered_support_gap_rule(d)
    axis={y:i for i,y in enumerate(years)};omit=set(d['omitted_years'])
    op=d['operative'];excluded=set(d['exclusions']);controls=set(d['never_controls'])
    if variant=='exclude_convention_controls':controls-=set(d.get('convention_filled_controls', []))
    if variant=='exclude_2020_2021':omit|={2020,2021}
    if variant=='include_2020_2021':omit-= {2020,2021}
    if variant.startswith('omit_state_'):excluded.add(variant[len('omit_state_'):])
    if variant.startswith('omit_cohort_'):
        year=int(variant[len('omit_cohort_'):])
        excluded|={u for u,g in op.items() if next((y for y in years if y>=g+1),end+2)==year}
    group='not_yet_treated' if variant=='not_yet_controls' else d.get('comparison_group','never_treated')
    v11='comparison_group' in d
    scope_supported=True
    delta={'start_plus_one':1,'start_minus_one':-1}.get(variant,0)
    labeled={u:g+delta for u,g in op.items()}
    # Actual exposure always bounds clean comparisons, including late labels.
    clean_onset={u:min(op[u],labeled[u],d.get('actual_clean_onset',{}).get(u,op[u])) for u in op}
    cohort={u:next((axis[y] for y in years if y>=g+1),len(years)) for u,g in labeled.items()}
    eligible={u:g for u,g in cohort.items() if u not in excluded and start<=op[u]<=end and g<len(years)}
    future={u:g for u,g in cohort.items() if op[u]>end or g==len(years)}
    metadata=metadata_rows
    declared_grid={(r['unit'],int(r['year'])) for r in metadata}
    available={(r['unit'],int(r['year'])) for r in metadata if r['annual_usable']=='1' and r.get('linkage_certified','1')=='1' and r.get('suppression_flag','0')=='0'}
    support_gap_drops=[]
    if rule:
        candidates=set(eligible)|controls
        if group=='not_yet_treated' or v11:candidates|=set(future)-excluded
        for u in sorted(candidates):
            missing=[y for y in years if y not in omit and y!=op.get(u) and y!=labeled.get(u)
                     and annual_before_cessation(u,y,d.get('exposure_end_dates',{})) and (u,y) not in available]
            if missing:support_gap_drops.append(dict(unit=u,missing_periods=missing,
                status='treated' if u in cohort else 'never_treated',
                reason='Registered outcome metadata lacks support'))
        excluded|={r['unit'] for r in support_gap_drops}
        eligible={u:g for u,g in eligible.items() if u not in excluded};controls-=excluded
    if v11 and variant!='not_yet_controls': group='never_treated' if len(controls)>=5 else 'not_yet_treated'
    cohort_checks=[];dropped=[]
    for g in sorted(set(eligible.values())):
        members=sorted(u for u in eligible if eligible[u]==g)
        pre=[y for y in years if y not in omit and all((u,y) in available and y<clean_onset[u] for u in members)]
        post=[y for y in years if axis[y]>=g and y not in omit and all(
            (u,y) in available and annual_before_cessation(u,y,d.get('exposure_end_dates',{}))
            and (not transition_drop or y not in {op.get(u),labeled.get(u)})
            for u in members)]
        failed=len(pre)<3 or len(post)<minimum_post
        if v11 and group=='not_yet_treated' and not post:
            future.update({u:g for u in members})
        cohort_checks.append({'cohort_index':g,'cohort_year':years[g],'members':members,'clean_pre_years':pre,'observed_post_years':post,'dropped':failed})
        if failed:
            dropped+=members
            if rule:
                support_gap_drops += [dict(unit=u,status='treated',missing_periods=[],clean_pre_periods=pre,
                    reason='Insufficient registered clean pre-periods' if len(pre)<3 else
                    'Insufficient registered retained post-periods') for u in members]
    excluded|=(set(dropped)-set(future) if group=='not_yet_treated' else set(dropped));eligible={u:g for u,g in eligible.items() if u not in excluded};controls-=excluded
    included=set(eligible)|controls
    if group=='not_yet_treated':included|={u for u in future if u not in excluded}
    required={(u,y) for u in included for y in years if y not in omit and y!=op.get(u) and y!=labeled.get(u)
              and annual_before_cessation(u,y,d.get('exposure_end_dates',{}))}
    missing=sorted(required-available)
    rows=[(u,axis[y],0.,eligible.get(u,future.get(u))) for u,y in sorted(required&available)]
    profiles=annual_exposure_profile(years,op,clean_onset,cohort,d.get('exposure_end_dates',{}),included)
    case={'min_post_periods':minimum_post,'min_controls':5,'panel':{'frequency':'annual','time_basis':{'kind':'calendar_year'}},
          'windows':{'primary':{'start':0,'end':len(years)-1,'excluded_periods':[axis[y] for y in omit if y in axis]}},
          'comparison':{'control_group':'never_treated','exclusions':[]},'sample_mask':{'include_units':sorted(included)},'exposure_clock':{'date_column':'operative_date'}}
    error=None
    try:
        if group=='not_yet_treated':
            case.update(v11_min_controls=5,v11_min_post_periods=minimum_post,v11_min_pre_periods=3)
        adapter=structural_design if group=='not_yet_treated' else primary_structural_design
        options={} if group=='not_yet_treated' else {'never_controls':sorted(controls)}
        design=adapter(case,rows,{u:{'policy_status':'treated' if u in cohort else 'never-treated'} for u in included},
             operative={u:next((axis[y] for y in years if y>=op[u]),len(years)) if u in op else None for u in included},
             exposure_override=profiles,normalized=True,control_group=group,**options)
    except ValueError as e:
        error=str(e);design={'cells':[],'groups':[],'units':[], 'periods':[], 'adoption':{},'mask':set(),'exposure':{},'whole_window_clean':{}}
    mass=Counter()
    for c in design['cells']:mass[c['event_time']]+=c['n_treated']
    leads=sorted(e for e,n in mass.items() if e<0 and n>=5)
    posts=[c for c in design['cells'] if c['post']];post_units={u for c in posts for u in c['treated']}
    placebos=placebo_support(design) if not error else []
    first_onset=min((clean_onset[u] for u in eligible),default=start)
    anchor_years=[y for y in years if y not in omit and y<first_onset and all((u,y) in available and (u,y) in required for u in included)]
    ct_reference_controls=sorted(u for u in controls if all((u,y) in available for y in years if y not in omit))
    checks={'six_treated':len(post_units)>=6,'two_cohorts':len({c['g'] for c in posts})>=2,
        'post_each_retained_cohort_member':bool(post_units) if group=='not_yet_treated' else post_units==set(eligible),
        'five_controls_each_post':bool(posts) and all(c['n_control']>=5 for c in posts),
        'min_post_periods_each_retained_cohort':bool(posts) and all(len({c['t'] for c in posts if c['g']==g})>=minimum_post for g in design['groups']),
        'three_clean_pre_each_retained_cohort':all(len(c['clean_pre_years'])>=3 for c in cohort_checks if not c['dropped']),
        'nonempty_supported_pre_leads':bool(leads),'supported_placebo':any(r['status']=='supported' for r in placebos),
        'complete_required_panel':not missing,
        'engine_metadata_adapter':error is None,'mechanism_scope_certified':scope_supported,
        'two_common_clean_anchor_years':len(anchor_years)>=2 and len(included)>=2,
        'five_complete_CT_reference_controls':len(ct_reference_controls)>=5}
    if v11 and group=='not_yet_treated': checks.pop('five_complete_CT_reference_controls')
    return {'min_post_periods':minimum_post,'comparison_group':group,'comparison_controls':sorted({u for c in posts for u in c['controls']}),'key':key,'variant':variant,'window':d['window'],'period_years':years,'period_index_to_year':{str(i):y for y,i in axis.items()},
            'calendar_omissions':sorted(omit),'retained_treated':sorted(post_units),'never_controls':sorted(controls),'future_adopters':future,
            'exposure_end_dates':dict(d.get('exposure_end_dates',{})),
            'convention_filled_controls':list(d.get('convention_filled_controls',[])),
            'anchor_years':anchor_years,'anchor_unit_n':len(included),'anchor_value_computed':False,
            'CT_complete_path_reference_controls':ct_reference_controls,
            'anchor_availability_exclusions':d['anchor_availability_exclusions'],
            'non_never_comparison_units_used':sorted({u for c in design['cells'] for u in c['controls']}-controls),
            'cohort_checks':cohort_checks,'metadata_excluded_units':sorted(excluded),'required_cells':len(required),
            'support_gap_rule':rule,'support_gap_drops':support_gap_drops,
            'support_gap_excluded_units':sorted(r['unit'] for r in support_gap_drops),
            'available_cells':len(required&available),'missing_cells':missing,
            'availability_rule':rule or 'Complete declared state-period panel',
            'supported_pre_leads':leads,
            'placebo_cohorts':placebos,'checks':checks,'support_pass':all(checks.values()),'engine_error':error,
            'metadata_only':True,'outcome_values_read':False,'estimator_calls':0,'cell_inventory':design['cells']},required&available
