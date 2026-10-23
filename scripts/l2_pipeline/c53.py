"""C53 stopped-host metadata migration; no dispatch, calibration or real fits."""
from pathlib import Path
from copy import deepcopy
from .autopilot import Autopilot,read,scheduler_lock
from .storage import ensure_once,sha
from .population_resolution import bind,rebase_sources
from .variants import registered_cards,secondary_entries
from .requeue import requeue
from .c42 import Park

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c53'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULING_C53_MUST_FIXES.md'
FROZEN=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','TERMINAL.json')
WINDOW_FALSE_PARKS={'b1010b_child_restraint_booster_age5','b1010b_pdmp_mandatory_query'}


def risks(card,support):
    variants=registered_cards(card)
    timing=[n for n,c in variants.items() if c['analysis'].get('support_adapter')!='annual_full_year_v1'
            and c.get('treatment',{}).get('start_shift',0)]
    primary=next((s for s in (support or {}).get('variants',[]) if s['variant']=='primary'),{})
    group=primary.get('comparison_group',card.get('comparison_group',card['analysis'].get('comparison_group',card['analysis'].get('controls','never_treated'))))
    secondary=[s['variant'] for s in (support or {}).get('variants',[])
               if s.get('reason')=='No certified secondary future-control support adapter']
    return dict(timing_variants=timing,calibration_future_group=group=='not_yet_treated',
                rejected_secondary_variants=secondary,registered_secondary_future=bool(secondary_entries(card)) and group=='not_yet_treated')


def scan(p):
    records=[]
    for row in read(OUT/'ACTIVE_BASELINE.json')['designs']:
        root=p.backend.root(row);support=read(root/'SUPPORT.json');card=dict(p.backend.ctx(row).card.payload)
        risk=risks(card,support);frozen=[bind(root/n) for n in FROZEN if (root/n).exists()]
        affected=bool(support and (risk['timing_variants'] or risk['calibration_future_group'] or risk['rejected_secondary_variants']))
        terminal=(root/'TERMINAL.json').exists() or row.get('status')=='COMPLETED'
        action='FINISHED_REPORT_ONLY' if terminal else 'FROZEN_HUB_REPORT' if frozen and affected else 'REBUILD' if affected else 'CURRENT_CODE_ON_FIRST_PREPARATION' if not support else 'UNAFFECTED'
        records.append(dict(design_id=row['design_id'],prior_row=row,root=str(root),prior_next=p.next_action(row),
            has_support=bool(support),support=bind(root/'SUPPORT.json') if support else None,risks=risk,
            affected=affected,freezes=frozen,action=action))
    return dict(schema='l2_c53_active_scan_v1',design_count=len(records),designs=records,
                frozen_affected=[r['design_id'] for r in records if r['action']=='FROZEN_HUB_REPORT'],
                authority=bind(AUTH),actual_data_estimation_calls=0,outcome_association_calls=0)


def migrate(p,record):
    old=record['prior_row'];slot=OUT/'resolutions'/old['design_id'];prior=read(slot/'APPLICATION.json')
    if prior:return prior
    oldroot=p.backend.root(old)
    if any((oldroot/n).exists() for n in FROZEN):raise ValueError('C53 cannot revise frozen or terminal bytes')
    card=rebase_sources(read(old['card']),old['card']);card['version']='v'+str(int(card['version'][1:])+1)
    card.setdefault('automation',{})['c53_implementation_revision']=dict(authority=bind(AUTH),prior_card=bind(old['card']),
        prior_support=bind(oldroot/'SUPPORT.json'),scientific_fields_unchanged=True,execution_authorized=False)
    path=ensure_once(slot/'CARD.json',card)
    ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=old['design_id'],
        card_sha256=sha(path),prior_card_sha256=sha(old['card']),decision='accept',purpose_rule='V6.2',
        reason='C53 implementation repairs; unchanged legal assignment and registered science; current-code draft required',
        authority=bind(AUTH),superseded_status='SUPERSEDED_C53',execution_authorized=False))
    inputs=read(slot/'INPUTS.json')
    if not inputs:
        ensure_once(slot/'INPUTS.json',dict(prior_row=old,card=bind(path),ruling=bind(ruling)))
    if not p.stop_record(old) and not (p.case_dir(old)/'PARKED.json').exists():
        with scheduler_lock(p.registry):p.stop(old,'C53','C53 metadata/code migration requires new version and draft')
    receipt=requeue(old['design_id'],path,ruling,p);row=receipt['new_row'];stages=[];failure=None
    for action in ('S0','S1','S2','S3','S4'):
        if p.done(row,action):continue
        try:
            result=p.backend.perform(row,action,p);ensure_once(p.checkpoint(row,action),result)
            stages.append(dict(action=action,result=result))
        except Park as exc:
            failure=dict(action=action,reason=str(exc),metrics=exc.metrics)
            ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='PARKED',**failure))
            break
    prior_park=read(p.case_dir(old)/'PARKED.json');jobs=[]
    # Only keep a genuine independent blocking gate. All S3 failures have just
    # been independently rebuilt, and the two V6 false PARKs are now resolved.
    if not failure and prior_park and prior_park.get('action') not in {'S3','S4'}:
        ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='PARKED',
            reason=prior_park['reason'],action=prior_park.get('action'),prior_park=bind(p.case_dir(old)/'PARKED.json'),support_rebuilt_under_C53=True))
    if p.next_action(row)!='PARK':
        jobs=p.backend.jobs(row,'draft',p);ensure_once(p.case_dir(row)/'jobs/draft.json',dict(jobs=jobs))
    root=p.backend.root(row)
    value=dict(design_id=row['design_id'],new_row=row,requeue=receipt,stages=stages,failure=failure,
        new_support=bind(root/'SUPPORT.json') if (root/'SUPPORT.json').exists() else None,
        draft_jobs=[bind(j) for j in jobs],next_action=p.next_action(row),
        retained_park=bind(p.case_dir(row)/'PARKED.json') if (p.case_dir(row)/'PARKED.json').exists() else None,
        science_changed=False,dispatches=0,actual_data_estimation_calls=0)
    ensure_once(slot/'APPLICATION.json',value)
    return value


def apply():
    p=Autopilot()
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C53 requires stopped scheduler')
        report=read(OUT/'ACTIVE_SCAN.json') or scan(p)
        ensure_once(OUT/'ACTIVE_SCAN.json',report)
    applications=[migrate(p,r) for r in report['designs'] if r['action']=='REBUILD']
    value=dict(applications=applications,frozen_affected=report['frozen_affected'],downloads=[],dispatches=0,actual_data_estimation_calls=0)
    ensure_once(OUT/'APPLICATIONS.json',value)
    return value

if __name__=='__main__':
    value=apply();print(dict(rebuilt=len(value['applications']),frozen_affected=value['frozen_affected']))
