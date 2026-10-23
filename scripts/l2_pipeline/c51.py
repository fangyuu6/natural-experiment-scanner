"""C51 stopped-scheduler, metadata-only support migration and review emission."""
from copy import deepcopy
from pathlib import Path
import csv
from .autopilot import Autopilot,read,scheduler_lock
from .storage import ensure_once,sha
from .population_resolution import bind,rebase_sources
from .support import support_check,declared_annual_periods,minimum_post_periods
from .stages.s3_support import variant_treatment
from .variants import registered_cards
from .engine.panels import metadata_eligible_keys
from .requeue import requeue
from .c42 import Park

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c51'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULING_C51_MIN_POST.md'
FROZEN=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','TERMINAL.json')


def short_posts(summary,minimum):
    posts={}
    for c in summary.get('cell_inventory',summary.get('cells',[])):
        if c.get('post',True):
            g=c.get('g',c.get('cohort'));t=c.get('t',c.get('period'))
            posts.setdefault(g,set()).add(t)
    return [dict(cohort=g,post_periods=sorted(ts),post_periods_n=len(ts))
            for g,ts in sorted(posts.items()) if len(ts)<minimum]


def scan(p):
    manifest=read(p.registry/'ACTIVE_DESIGNS.json');rows=[]
    for row in manifest['designs']:
        root=p.backend.root(row);ctx=p.backend.ctx(row);path=root/'SUPPORT.json'
        record=dict(design_id=row['design_id'],prior_row=row,root=str(root),
            freezes=[bind(root/n) for n in FROZEN if (root/n).exists()],variants=[],has_support=path.exists())
        if path.exists():
            record['support']=bind(path)
            cards=registered_cards(dict(ctx.card.payload))
            for summary in read(path)['variants']:
                name=summary['variant'];cfg=cards[name]['analysis']
                record['variants'].append(dict(variant=name,old_support_pass=summary.get('support_pass',summary.get('passed')),
                    min_post_periods=minimum_post_periods(cfg),short_cohorts=short_posts(summary,minimum_post_periods(cfg))))
        record['affected']=any(v['short_cohorts'] for v in record['variants'])
        record['action']='STOP_FROZEN_C51' if record['affected'] and record['freezes'] else 'REBUILD' if path.exists() and not record['freezes'] else 'NO_SUPPORT_YET' if not path.exists() else 'FROZEN_REPORT_ONLY'
        rows.append(record)
    return dict(schema='l2_c51_active_scan_v1',active_registry=bind(p.registry/'ACTIVE_DESIGNS.json'),
        authority=bind(AUTH),designs=rows,design_count=len(rows),support_count=sum(r['has_support'] for r in rows),
        affected_designs=[r['design_id'] for r in rows if r['affected']],
        frozen_affected=[r['design_id'] for r in rows if r['affected'] and r['freezes']],
        outcome_association_calls=0,estimator_calls=0,new_downloads=[])


def migrate(p,record):
    old=record['prior_row'];oldroot=p.backend.root(old)
    card=rebase_sources(read(old['card']),old['card']);card['version']='v'+str(int(card['version'][1:])+1)
    slot=OUT/'resolutions'/old['design_id']/card['version']
    prior_application=read(slot/'APPLICATION.json')
    if prior_application:return prior_application
    card.setdefault('automation',{})['c51_support_revision']=dict(authority=bind(AUTH),
        prior_card=bind(old['card']),prior_support=bind(oldroot/'SUPPORT.json'),
        registered_threshold_unchanged=True,execution_authorized=False)
    path=ensure_once(slot/'CARD.json',card)
    ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=old['design_id'],
        card_sha256=sha(path),prior_card_sha256=sha(old['card']),decision='accept',
        reason='C51 existing post support threshold enforced; unchanged audited assignment and purpose; new draft required',
        authority=bind(AUTH),purpose_rule='V6.2',superseded_status='SUPERSEDED_C51',execution_authorized=False))
    receipt=requeue(old['design_id'],path,ruling,p,scheduler_owned=False);row=receipt['new_row']
    stages=[];failed=None
    for action in ['S0','S1','S2','S3','S4']:
        try:
            result=p.backend.perform(row,action,p)
            ensure_once(p.checkpoint(row,action),result);stages.append(dict(action=action,result=result))
        except Park as exc:
            failed=dict(action=action,reason=str(exc),metrics=exc.metrics);break
    # Purpose was retained by the card-bound ruling. No forecasts are copied
    # across changed SPEC hashes, and no new agent or numerical run is launched.
    root=p.backend.root(row);new_support=read(root/'SUPPORT.json')
    if new_support:
        for v in new_support['variants']:
            if short_posts(v,minimum_post_periods(card['analysis'])):
                raise ValueError('C51 constructor retained a short post cohort')
    prior_park=read(p.case_dir(old)/'PARKED.json')
    jobs=[]
    if failed:
        ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],
            status='PARKED',**failed,authority=bind(AUTH)))
    elif row['design_id']=='b1010b_epcs_mandate':
        # Emit the reviewer now. The stopped loop's draft inventory dispatches
        # this exact packet after the new SPEC's forecast/lock steps complete.
        jobs=p.backend.jobs(row,'draft',p)
        ensure_once(p.case_dir(row)/'jobs/draft.json',dict(jobs=jobs))
    elif prior_park and prior_park['reason']!='V7 supported post periods below minimum':
        ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],
            status='PARKED',reason=prior_park['reason'],prior_park=bind(p.case_dir(old)/'PARKED.json'),
            support_rebuilt_under_C51=True,authority=bind(AUTH)))
    value=dict(schema='l2_c51_support_revision_v1',design_id=row['design_id'],prior_support=bind(oldroot/'SUPPORT.json'),
        new_row=row,receipt=receipt,stages=stages,failure=failed,new_support=bind(root/'SUPPORT.json') if new_support else None,
        new_primary_passed=new_support.get('primary_passed') if new_support else None,
        review_jobs=jobs,next_action=p.next_action(row),dispatches=0,actual_data_estimation_calls=0,new_downloads=[])
    ensure_once(slot/'APPLICATION.json',value)
    return value


def apply():
    p=Autopilot()
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C51 requires stopped scheduler')
        report=read(OUT/'ACTIVE_SCAN.json') or scan(p)
        ensure_once(OUT/'ACTIVE_SCAN.json',report)
    if report['frozen_affected']:
        ensure_once(OUT/'STOP_FROZEN_C51.json',dict(designs=report['frozen_affected'],authority=bind(AUTH)))
        return report
    results=[]
    for record in report['designs']:
        if record['action']=='REBUILD':results.append(migrate(p,record))
    ensure_once(OUT/'APPLICATIONS.json',dict(results=results,dispatches=0,actual_data_estimation_calls=0,new_downloads=[]))
    return dict(scanned=report['design_count'],rebuilt=len(results),affected=report['affected_designs'],
        frozen_affected=report['frozen_affected'],applications=[dict(design_id=r['design_id'],passed=r['new_primary_passed'],
            failure=r['failure']['reason'] if r['failure'] else None,next_action=r['next_action'],review_jobs=r['review_jobs']) for r in results])

if __name__=='__main__':
    import json
    print(json.dumps(apply(),indent=2))
