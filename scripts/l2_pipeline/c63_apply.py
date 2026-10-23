"""C63 stopped-host metadata/card migrations. No model calls or estimation."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock
from .storage import ensure_once,sha
from .population_resolution import bind,rebase_sources
BASE=Path('data/l2_pipeline/autopilot/c63')
AUTH=Path('docs/plans/l2-pipeline/HUB_RULINGS_C63.md')


def validate_yrbs():
    from .cards import validate_card
    from .population_resolution import choose
    from .connectors import outcome_v14
    from .connectors.population_v63 import CURRENT_PATH
    from .prescreen import bind_outcome,evaluate
    from .coverage import coverage_bounded_card
    source=Path('data/l2_pipeline/intake/bulk_20261011c/health_energy/cards_yrbs_pending');reports=[]
    catalog=outcome_v14.catalog()
    for path in sorted(source.glob('*.json')):
        c=rebase_sources(read(path),path);old=deepcopy(c);sid=c['outcome']['series_id'];spec=catalog['series'][sid]
        c['version']='v2';out=c['outcome'];out.update(connector='outcome_v14_'+sid,catalog_path=str(outcome_v14.CATALOG_PATH.resolve()),
            catalog_sha256=sha(outcome_v14.CATALOG_PATH),population_filters=spec['population_filters'],source_population_filters=spec['population_filters'],
            paths=spec['paths'],source_sha256=spec['source_sha256'],support_path=spec['support_artifact']['path'],support_sha256=spec['support_artifact']['sha256'],status='receipt_verified_local_artifact')
        for k in ['planned_source','planned_indicator']:out.pop(k,None)
        # Bind sources but preserve the card's declared study window.
        c['outcome']=bind_outcome(out);c['analysis']['support_adapter']='biennial_odd_year_v1'
        first=c['first_order'];first['availability']='available';first.pop('availability_note',None)
        first['justification']='C63 registered weighted YRBS prevalence; purpose/literature verification remains a future S0 gate.'
        resolution=choose(c,dict(status='VERIFIED',target_outcome=first['target_outcome'],target_construct=first['population_resolution']['construct']))
        resolution.update(catalog=bind(outcome_v14.CATALOG_PATH),population_metadata=bind(CURRENT_PATH),authority=bind(AUTH),
            purpose_verification='conditional metadata validation only; original pending_S0_verification retained')
        first['population_resolution']=resolution
        c['population']['filters']=spec['population_filters']
        for row in c['sources']:
            if row['source_id']=='outcome':row.update(locator=sid+'; C63 microdata weights, n<100 suppression, no NYA splice',license=spec['license'])
        c.setdefault('automation',{}).pop('coverage_window',None);c=coverage_bounded_card(c)
        dest=ensure_once(BASE/'cards_yrbs_validated'/path.name,c)
        errors=validate_card(c)
        screen=evaluate(c,dest.parent)
        report=dict(design_id=c['design_id'],original=bind(path),validated_card=bind(dest),schema_errors=errors,
            connector_registered=True,selected_series=resolution['selected']['series_id'],frequency='biennial',adapter='biennial_odd_year_v1',
            prescreen=screen,ingested=False,outcome_association_calls=0)
        ensure_once(BASE/'yrbs_validation'/(c['design_id']+'.json'),report);reports.append(report)
    ensure_once(BASE/'YRBS_VALIDATION.json',dict(cards=reports,ingested=0));return reports


def ui_supersede(p):
    from .c62_apply import inactive
    from .requeue import requeue
    from .coverage import coverage_bounded_card
    from .prescreen import bind_outcome
    did='b1011tl_ui_duration_cap_below_26_weeks';dest=BASE/'ui_duration_cap/APPLICATION.json'
    if dest.exists():return read(dest)
    row=next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)
    root=p.backend.root(row);newpath=Path('data/l2_pipeline/intake/bulk_20261011c/fiscal_labor/cards/b1011cfl_ui_duration_cap_weeks_compensated.json')
    if p.done(row,'S2') is not None or (root/'PANELS.json').exists():
        value=dict(decision='KEEP_PRIMARY_NEW_CARD_SECONDARY',existing=row,new_card=bind(newpath),s2_reached=True,ingested=False)
        ensure_once(dest,value);return value
    inactive(p,row)
    # Inspect every emitted job, not only history, before switching active row.
    from .agents.jobs import load_job
    from .agents.sonnet_queue import state
    for inventory in (p.case_dir(row)/'jobs').glob('*.json'):
        for path in read(inventory).get('jobs',[]):
            job=load_job(Path(path));out=Path(job.output_dir)
            if state(path,p.repo)=='running' or ((out/'DISPATCH_ONCE.json').exists() and not (out/'DISPATCH_RESULT.json').exists()):raise ValueError('UI has running/claimed jobs')
    old=rebase_sources(read(row['card']),row['card']);new=read(newpath)
    for k in ['outcome','population','first_order','outcome_family','outcome_scale']:old[k]=deepcopy(new[k])
    old['outcome']=bind_outcome(old['outcome']);old['version']='v'+str(int(old['version'][1:])+1)
    old['sources']=[r for r in old['sources'] if r['source_id']!='outcome']+[r for r in new['sources'] if r['source_id']=='outcome']
    old.setdefault('automation',{}).pop('coverage_window',None);old=coverage_bounded_card(old)
    card=ensure_once(BASE/'ui_duration_cap/CARD.json',old)
    ruling=ensure_once(BASE/'ui_duration_cap/RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=did,card_sha256=sha(card),
        prior_card_sha256=row['card_sha256'],decision='resume',purpose_rule='V6.2',superseded_status='SUPERSEDED_C63',authority=bind(AUTH),
        reason='C63 §7 owner outcome supersession before S2: DOL 5159 compensated weeks is more direct than LAUS unemployment. Legal history and its unresolved gaps retained; no outcome read.',execution_authorized=False))
    result=requeue(did,card,ruling,p,scheduler_owned=False)
    # This is an outcome correction, not permission to rerun a failed legal gate.
    prior_park=read(p.case_dir(row)/'PARKED.json')
    if prior_park:ensure_once(p.case_dir(result['new_row'])/'PARKED.json',dict(prior_park,attempt_id=result['new_row']['attempt_id'],c63_outcome_superseded=True))
    value=dict(decision='SUPERSEDED_PRE_S2',s2_reached=False,new_card=bind(card),requeue=result,legal_park_retained=bool(prior_park),outcome_values_read=0)
    ensure_once(dest,value);return value


def apply():
    p=Autopilot()
    if not (p.registry/'CODE_TASK.lock').exists() or not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C63 maintenance lock and STOP required')
    ui=ui_supersede(p)
    with scheduler_lock(p.registry):
        atomic(p.registry/'INGEST_GATE.json',dict(schema='l2_c63_ingest_gate_v1',phase='PAUSED_FIRST_AUTOPILOT_ESTIMATE',canary_designs=[],authority=bind(AUTH)))
        from .ops.forecast_authors import audit
        sets=audit(p.repo,BASE/'forecast_authors')
        can=Path('data/l2_pipeline/intake/bulk_20261011c/health_energy/cards/b1011c_contraceptive_coverage_mandate.json')
        from .population_resolution import choose
        c=read(can);choice=choose(c,dict(status='VERIFIED',target_outcome=c['first_order']['target_outcome']))
        ensure_once(BASE/'CONTRACEPTIVE_DECISION.json',dict(card=bind(can),resolution=choice,ingested=False,authority=bind(AUTH)))
        value=dict(ui=ui,forecast_sets=len(sets),shared_author_sets=sum(s['shared_author_personas'] for s in sets),ingest_paused=True)
        ensure_once(BASE/'APPLICATION.json',value);return value

if __name__=='__main__':
    print(apply());print('YRBS cards',len(validate_yrbs()))
