"""C46 explicit hub rulings and metadata geometry; no outcomes or dispatch."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot,read
from .storage import ensure_once,sha
from .cards import validate_card
from .contracts import StageContext
from .connectors import outcome_v8
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c46'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULINGS_C46.md'
TARGETS={'bulk10_health_gestational_limit_infant_mortality':'park','bulk10_health_medicaid_sud_imd_waiver':'accept','bulk10_health_child_access_prevention_storage':'accept'}


def bind(p):return dict(path=str(Path(p).resolve()),sha256=sha(p))


def support_sidecar(sid,spec,revision=None):
    observed=spec['observed_coverage'];numeric=observed['numeric_years_by_state'];published=observed['observed_years_by_state']
    meta=[]
    for u in sorted(observed['states']):
        for y in range(1999,2025):
            ok=y in numeric.get(u,[]);present=y in published.get(u,[])
            meta.append(dict(unit=u,state=u,year=y,period=str(y),frequency='annual',available=int(ok),annual_usable=int(ok),linkage_certified=int(ok),published_cell_present=int(present),missing_reason='' if ok else 'nonnumeric_or_missing_published_cell'))
    p=OUT/'metadata'/((revision+'/') if revision else '')/(sid+'.json');ensure_once(p,dict(series_id=sid,metadata=meta,catalog=bind(outcome_v8.CATALOG_PATH),outcome_values_in_sidecar=False,outcome_values_read=0))
    return p


def prepare():
    pilot=Autopilot();manifest=read(pilot.registry/'ACTIVE_DESIGNS.json');data=outcome_v8.catalog(verify_sources=False);outcome_v8.register();prepared=[]
    for old in manifest['designs']:
        did=old['design_id']
        if did not in TARGETS:continue
        card=deepcopy(read(old['card']));card['version']='v2';oldroot=pilot.backend.root(old);purpose=read(oldroot/'PURPOSE_VERIFICATION.json')
        decision=TARGETS[did];reason='C46 §1: statutory target is late abortions; infant mortality is downstream. Reopen only with a late-abortion series.'
        if decision=='accept':
            sid='wonder_opioid_overdose_deaths' if 'imd' in did else 'wonder_firearm_deaths';spec=data['series'][sid];support=support_sidecar(sid,spec)
            outcome=card['outcome'];outcome.update(connector='outcome_v8_'+sid,series_id=sid,measure_id=sid,years=[1999,2024],period_years=list(range(1999,2025)),population_filters=spec['population_filters'],source_population_filters=spec['population_filters'],outcome_unit='deaths per 100,000 residents in declared age/sex population',source_outcome_unit='deaths',outcome_scale='rate',license=spec['license'],
                catalog_path=str(outcome_v8.CATALOG_PATH.relative_to(ROOT)),catalog_sha256=sha(outcome_v8.CATALOG_PATH),source_validation_required=True,
                support_path=str(support.relative_to(ROOT)),support_sha256=sha(support),paths=[str((ROOT/e['raw_path']).resolve()) for e in spec['exports']],source_sha256={str((ROOT/e['raw_path']).resolve()):e['sha256'] for e in spec['exports']})
            card['outcome_scale']='rate';card['population'].update(description=spec['population']+'; mortality rate per100,000 residents; legal target population separately verified',filters=spec['population_filters'])
            first=card['first_order'];first.update(target_outcome=purpose['target_outcome'],target_population=purpose['target_population'],primary_outcome=sid,relation='closest_proxy',evidence_status='verified_by_hub',purpose_rule='V6.2',
                purpose_route='statute' if 'imd' in did else 'established_literature',purpose_basis='statute_purpose' if 'imd' in did else 'established_literature',
                proxy_justification='C46 '+('§2 accepts the administrative-purpose route: opioid deaths are a CMS SMD17-003 demonstration goal. ' if 'imd' in did else '§3 accepts all-intent firearm mortality, not homicide-only mortality. ')+ 'Same construct measured over a broader all-resident population; dilution is expected because only the legally targeted subgroup is directly exposed.',
                justification='Hub C46 independently accepts the verified target and population; all-resident rate uses the same construct over a broader population.',
                statute_refs=[{k:r[k] for k in ('source_ref','locator','supports')} for r in purpose['citations'] if r['source_type']=='statute'],
                literature_refs=[{k:r[k] for k in ('source_ref','title','locator','supports')} for r in purpose['citations'] if r['source_type']=='paper'])
            for s in card['sources']:
                if s['source_id']=='outcome':s.update(url=spec['source_url'],locator=sid+'; all ages, rate scale, final1999–2024',license=spec['license'])
            card.setdefault('automation',{}).pop('coverage_window',None)
            from .coverage import coverage_bounded_card
            card=coverage_bounded_card(card)
            reason='C46 §2: CMS SMD17-003 administrative goal; rate scale; same construct broader population.' if 'imd' in did else 'C46 §3: all-intent firearm deaths1999–2024, rate scale; geometry/support must pass or PARK.'
        errors=validate_card(card)
        if errors:raise ValueError(str(errors))
        path=ensure_once(OUT/'cards'/(did+'.json'),card)
        ruling=ensure_once(OUT/'rulings'/(did+'.json'),dict(schema='l2_requeue_ruling_v1',design_id=did,prior_card_sha256=sha(old['card']),card_sha256=sha(path),decision=decision,reason=reason,authority=bind(AUTH),purpose_evidence=bind(oldroot/'PURPOSE_VERIFICATION.json'),
            reviewed_citations=purpose['citations'],purpose_rule='V6.2',execution_authorized=False))
        prepared.append(dict(design_id=did,card=str(path),ruling=str(ruling),decision=decision))
    ensure_once(OUT/'PREPARED_RULINGS.json',dict(designs=prepared,outcome_values_read=0,dispatches=0))
    return prepared


def check_cap():
    """After new-card S0, certify history and check metadata support/RI only."""
    from .support import support_check,standard_annual_declaration,placebo_support
    from .engine.design import build_design
    from .hub import ri_geometry_feasibility
    from .c42 import post_support_gate,Park
    pilot=Autopilot();row=next(r for r in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs'] if 'child_access_prevention_storage' in r['design_id'])
    ctx=pilot.backend.ctx(row,strict=True);history_error=None
    for action in ('S0','S1'):
        if pilot.done(row,action) is None:
            try:result=pilot.backend.perform(row,action,pilot)
            except ValueError as exc:
                if action!='S1' or 'unresolved V3 date' not in str(exc):raise
                history_error=str(exc);break
            ensure_once(pilot.checkpoint(row,action),result)
    ctx=pilot.backend.ctx(row,strict=True);sidecar=ROOT/ctx.card.payload['outcome']['support_path']
    if sha(sidecar)!=ctx.card.payload['outcome']['support_sha256']:raise ValueError('CAP support metadata changed')
    keys={(m['unit'],m['year']) for m in read(sidecar)['metadata'] if m['annual_usable'] and m['linkage_certified']}
    if history_error:
        from .agents.jobs import load_job
        from .intake.official_history import corrected_comparison,engine_records
        job=load_job(Path(pilot.done(row,'audit')['jobs'][0]));audit=read(job.promote_path)
        comparison,_,_=corrected_comparison(ctx.read('RECONCILIATION.json')['historical_comparison'],audit['checks'],job.contract['sample'])
        # Audit uncertainty is retained, and forbids readiness regardless of
        # the descriptive planning geometry. No new exclusion or date is used.
        treatment=engine_records(comparison['accepted'],set(ctx.card.payload['treatment'].get('excluded_units',[])))
    else:treatment=ctx.read('TREATMENT.json')['records']
    analysis=ctx.card.payload['analysis'];years=ctx.card.payload['outcome']['period_years']
    support=support_check(keys,treatment,analysis,period_years=years,transition_year_drop=True)
    error=None;design=None;placebo=[]
    if support['passed']:
        declaration=standard_annual_declaration(treatment,analysis,years,summary=support,transition_year_drop=True)
        try:design=build_design(declaration,[(u,t,0.,None) for u,t in support['analysis_mask']])
        except ValueError as exc:error=str(exc)
        if design:placebo=placebo_support(design)
        ri=ri_geometry_feasibility(declaration,design) if design else dict(passed=False,reason=error,outcome_values_read=0)
    else:ri=dict(passed=False,status='NOT_RUN_SUPPORT_FAILED',outcome_values_read=0)
    post_counts={str(g):len({c['period'] for c in support['cells'] if c['cohort']==g}) for g in sorted({c['cohort'] for c in support['cells']})}
    post_pass=bool(post_counts) and all(n>=2 for n in post_counts.values())
    passed=history_error is None and support['passed'] and post_pass and design is not None and ri.get('passed') is True and any(p['status']=='supported' for p in placebo)
    result=dict(design_id=row['design_id'],card=bind(ctx.card.path),window=[1999,2024],support=support,ri=ri,placebo=placebo,post_periods_by_cohort=post_counts,passed=passed,
        status='READY_METADATA_ONLY' if passed else 'PARKED',history_error=history_error,planning_geometry_only=history_error is not None,geometry_error=error,outcome_values_read=0,actual_data_estimation_calls=0)
    path=ensure_once(OUT/('CAP_GEOMETRY.json' if ctx.card.payload['version']=='v2' else 'CAP_GEOMETRY_'+ctx.card.payload['version']+'.json'),result)
    if not passed:
        ensure_once(pilot.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],action='S2',status='PARKED',reason='C46 §3:1999–2024 metadata geometry/support/RI/placebo gates failed',evidence=bind(path)))
        pilot.ledger(row,'geometry','PARK','record',evidence=bind(path))
    return result
