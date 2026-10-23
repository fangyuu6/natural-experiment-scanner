"""Stopped C55 metadata-only versioning; gates decide, no agent/fit dispatch."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock
from .storage import ensure_once,sha
from .population_resolution import bind,rebase_sources
from .requeue import requeue
from .c42 import Park
from .coverage import coverage_bounded_card
from .biennial import ADAPTER

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c55'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULING_C55.md'
EPCS='b1010b_epcs_mandate'
TURNOUT=('c35_cspp_earlvot','b1010_online_voter_registration','b1010_preregistration_youth','b1010_same_day_registration')


def migrate(p,old):
    slot=OUT/'resolutions'/old['design_id'];prior=read(slot/'APPLICATION.json')
    if prior:return prior
    root=p.backend.root(old)
    if any((root/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','TERMINAL.json')):
        raise ValueError('C55 refuses frozen or finished design')
    card=rebase_sources(read(old['card']),old['card'])
    card['version']='v'+str(int(card['version'][1:])+1)
    if old['design_id']==EPCS:
        card.setdefault('automation',{}).pop('coverage_window',None)
        card=coverage_bounded_card(card)
        if card['outcome']['years']!=[2006,2018]:raise ValueError('EPCS must bind 2006–2018')
    else:
        if card['outcome'].get('frequency')!='biennial':raise ValueError('C55 turnout frequency differs')
        card['analysis']['support_adapter']=ADAPTER
    card.setdefault('automation',{})['c55_revision']=dict(authority=bind(AUTH),prior_card=bind(old['card']),
        rule='C55 §1' if old['design_id']==EPCS else 'C55 §3',execution_authorized=False,
        outcome_values_read=0,outcome_association_calls=0)
    path=ensure_once(slot/'CARD.json',card)
    accepted=all(p.done(old,a) for a in ('history','reconcile','audit'))
    ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=old['design_id'],
        decision='accept' if accepted else 'resume',card_sha256=sha(path),prior_card_sha256=sha(old['card']),
        purpose_rule='V6.2',superseded_status='SUPERSEDED_C55',authority=bind(AUTH),
        reason='C55 consistent-definition window / even-election-grid adapter; all independent gates retained',execution_authorized=False))
    receipt=requeue(old['design_id'],path,ruling,p);row=receipt['new_row'];stages=[];failure=read(p.case_dir(row)/'PARKED.json')
    for action in ('reconcile','S0','S1','S2','S3','S4'):
        if p.done(row,action):continue
        if p.next_action(row)!=action:continue
        try:
            value=p.backend.perform(row,action,p)
            if value.get('requeued'):raise ValueError('Unexpected secondary window requeue')
            ensure_once(p.checkpoint(row,action),value);stages.append(action)
        except Park as exc:
            failure=dict(action=action,reason=str(exc),metrics=exc.metrics)
            ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='PARKED',**failure))
            p.ledger(row,action,'PARK','stop',reason=str(exc),metrics=exc.metrics)
            break
    # RI is topology-only and uses no numeric outcomes; require the normal gate.
    if p.next_action(row)=='ri':
        try:
            value=p.backend.perform(row,'ri',p);ensure_once(p.checkpoint(row,'ri'),value);stages.append('ri')
        except Park as exc:
            failure=dict(action='ri',reason=str(exc),metrics=exc.metrics)
            ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='PARKED',**failure))
            p.ledger(row,'ri','PARK','stop',reason=str(exc),metrics=exc.metrics)
    result=dict(design_id=row['design_id'],requeue=receipt,new_row=row,stages=stages,failure=failure,
        next_action=p.next_action(row),outcome_values_read=0,actual_data_estimation_calls=0,
        outcome_association_calls=0,dispatches=0)
    ensure_once(slot/'APPLICATION.json',result);return result


def apply():
    p=Autopilot()
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C55 requires retained stop flag')
        rows=read(OUT/'ACTIVE_BASELINE.json',read(p.registry/'ACTIVE_DESIGNS.json'))['designs']
        ensure_once(OUT/'ACTIVE_BASELINE.json',dict(designs=rows))
        if not (OUT/'HUB_QUEUE_BASELINE.json').exists():ensure_once(OUT/'HUB_QUEUE_BASELINE.json',read(p.registry/'HUB_QUEUE.json'))
    # requeue owns its own exclusive scheduler transaction; never start a tick.
    applications=[migrate(p,row) for row in rows if row['design_id'] in {EPCS,*TURNOUT}]
    if len(applications)!=5:raise ValueError('C55 requires EPCS and four turnout designs')
    result=dict(applications=applications,downloads=[],dispatches=0,loop_restarted=False,
        outcome_values_read=0,actual_data_estimation_calls=0,outcome_association_calls=0)
    ensure_once(OUT/'APPLICATIONS.json',result)
    return result

if __name__=='__main__':
    value=apply();print([(r['design_id'],r['next_action']) for r in value['applications']])
