"""C50 additive, outcome-blind rulings and versioned mechanical windows."""
from copy import deepcopy
from pathlib import Path
import json,re
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .population_resolution import bind
from .c42 import Park

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c50'
AUTH=ROOT/'docs/plans/l2-pipeline/HUB_RULINGS_C50.md'
from .connectors.population_v63 import C50_TABLE
TABLE=C50_TABLE


def normalized(value):
    return re.sub(r'[^a-z0-9]+','_',value.lower()).strip('_') if isinstance(value,str) else ''


def equivalence_target(target,*,construct=None,meta,family=None):
    if meta.get('catalog_id') not in {'first_order_outcomes_v10','first_order_outcomes_v11','first_order_outcomes_v12','first_order_outcomes_v13','first_order_outcomes_v14','first_order_outcomes_v15','first_order_outcomes_v16','first_order_outcomes_v17','first_order_outcomes_v18','first_order_outcomes_v19'}:return None
    table=read(TABLE)
    authority=table['authority']
    if sha(authority['path'])!=authority['sha256']:raise ValueError('C50 equivalence authority changed')
    entries=table['entries']
    match=next((e for e in entries if normalized(target) in [normalized(a) for a in e['aliases']]),None)
    if match is None:
        # Reuse the reviewed C49 table by semantic identity. No fuzzy word\n        # similarity, new construct, or downstream outcome is inferred.
        prior=meta.get('construct_equivalences')
        if prior:
            if sha(ROOT/prior['path'])!=prior['sha256']:raise ValueError('C49 construct-equivalence table changed')
            old=read(ROOT/prior['path'])['entries']
            found=[e for e in old if normalized(e['card_construct'])==normalized(target)]
            found=found or [e for e in old if e['construct']==(construct or target)]
            if found:
                e=min(found,key=lambda e:len(e['secondary_series']))
                old_authority=ROOT/e['citation'].split(' §')[0]
                if sha(old_authority)!=e['authority_sha256']:raise ValueError('C49 equivalence authority changed')
                match=dict(construct=e['construct'],primary_series=e['primary_series'],secondary_series=e['secondary_series'],citation=e['citation'])
        match=match or next((e for e in entries if e['construct']==(construct or target)),None)
    if match is None:return None
    sid=match['primary_series']
    if meta['series'].get(sid,{}).get('construct')!=match['construct']:raise ValueError('C50 series construct changed')
    return dict(construct=match['construct'],ages=[0,120],restrictions={},preferred_series=sid,
        aliases=[target],rule=match.get('citation','docs/plans/l2-pipeline/HUB_RULINGS_C50.md §1'),
        equivalence_citation=match.get('citation','docs/plans/l2-pipeline/HUB_RULINGS_C50.md §1'),secondary_series=match.get('secondary_series',[]),
        equivalence_evidence=[bind(TABLE),dict(authority)])


def window_card(card,*,catalog=None):
    """Intersect declared calendars only; preserve legal and scientific rules."""
    from .coverage import coverage_bounded_card
    new=deepcopy(card);new.setdefault('automation',{}).pop('coverage_window',None)
    new=coverage_bounded_card(new,catalog=catalog)
    report=new['automation']['coverage_window']
    if report['parked']:raise Park(report['reason'],dict(coverage=report))
    new['version']='v'+str(int(card['version'][1:])+1)
    new['automation']['c50_window_revision']=dict(authority=bind(AUTH),prior_version=card['version'],
        outcome_values_read=0,outcome_association_calls=0,execution_authorized=False)
    return new


def validate_window_ruling(pilot,row,new,decision):
    from .population_resolution import rebase_sources
    expected=rebase_sources(window_card(read(row['card'])),row['card'])
    if new!=expected or decision['authority']!=bind(AUTH):raise ValueError('C50 mechanical window/card differs')


def window_precheck(pilot,row,*,scheduler_owned=False,force=False):
    from .coverage import coverage_bounded_card
    from .variants import registered_cards
    from .requeue import requeue
    from .population_resolution import rebase_sources
    card=read(row['card'])
    if not card.get('automation',{}).get('coverage_window'):return None
    changed=force
    for variant in registered_cards(card).values():
        declared=deepcopy(variant);declared['automation'].pop('coverage_window',None)
        bounded=coverage_bounded_card(declared)
        if bounded['automation']['coverage_window']['parked']:
            if variant.get('_secondary_unsupported_reason') or variant['outcome']['series_id']!=card['outcome']['series_id']:continue
            raise Park(bounded['automation']['coverage_window']['reason'],dict(coverage=bounded['automation']['coverage_window']))
        changed=changed or bounded['outcome']!=variant['outcome']
    if not changed:return None
    new=rebase_sources(window_card(card),row['card']);slot=OUT/'resolutions'/row['design_id']/new['version']
    path=ensure_once(slot/'CARD.json',new)
    ruling=ensure_once(slot/'RULING.json',dict(schema='l2_requeue_ruling_v1',design_id=row['design_id'],
        prior_card_sha256=sha(row['card']),card_sha256=sha(path),decision='accept',reason='C50 §2 mechanical calendar intersection; retained legal gates; numerical readiness must be rebuilt',
        authority=bind(AUTH),purpose_rule='V6.2',mechanical_window_rule='C50 §2',superseded_status='SUPERSEDED_C50',execution_authorized=False))
    ensure_once(pilot.case_dir(row)/'MECHANICAL_REQUEUE.json',dict(card=bind(path),ruling=bind(ruling)))
    receipt=requeue(row['design_id'],path,ruling,pilot,scheduler_owned=scheduler_owned)
    return dict(requeued=True,receipt=receipt,outcome_values_read=0,actual_data_estimation_calls=0)


def reopen(p,row,evidence):
    stop=p.stop_record(row)
    if not stop:return
    paths=[q for q in p.case_dir(row).glob('STOP*.json') if read(q)==stop]
    if len(paths)!=1:raise ValueError('Unique retained stop required')
    ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C50.json',dict(stop=bind(paths[0]),evidence=list(evidence)+[bind(AUTH)],authority=bind(AUTH),execution_authorized=False))


def park(p,row,reason,metrics,evidence):
    ensure_once(p.case_dir(row)/'PARKED.json',dict(design_id=row['design_id'],attempt_id=row['attempt_id'],status='PARKED',reason=reason,metrics=metrics,evidence=evidence,authority=bind(AUTH)))
    reopen(p,row,evidence)


def apply():
    from .population_resolution import apply as resolve
    from .c49 import row_for
    from .agents.jobs import load_job
    from .agents.replacement import ContractRejection
    p=Autopilot();entries=[e for e in read(p.registry/'HUB_QUEUE.json')['entries'] if not e['status'].startswith('SUPERSEDED_')]
    prior=read(OUT/'OPEN_QUEUE_BASELINE.json')
    if prior:entries=prior['entries']
    else:ensure_once(OUT/'OPEN_QUEUE_BASELINE.json',dict(entries=entries))
    resolutions=[];handled=[]
    for e in entries:
        row=row_for(p,e['design_id']);slot=OUT/'resolutions'/row['design_id'];action=e['action']
        if action=='draft':continue
        if (slot/'APPLICATION.json').exists():
            resolutions.append(bind(slot/'APPLICATION.json'));handled.append((e['design_id'],e['attempt_id']));continue
        if action=='purpose':
            value=resolve(p,row,output=slot/'population')
            # C50 retains old rows and immutable evidence; change only the\n            # explicit supersession label for the queue covered by this ruling.
            value=dict(action='C50 §1 approved construct',**value)
        else:
            with scheduler_lock(p.registry):
                if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('Stopped scheduler required')
                if action=='S2' and 'source path differs' in e['reason']:
                    receipt=ensure_once(slot/'SOURCE_BINDING.json',dict(reason=e['reason'],root_cause='Compact secondary declaration omits paths/source_sha256; adapter resolves its own pinned catalog, not CURRENT or primary',code=[bind(ROOT/'scripts/l2_pipeline/connectors/c35_core.py'),bind(ROOT/'scripts/l2_pipeline/connectors/c46_core.py')],authority=bind(AUTH),outcome_values_read=0))
                    reopen(p,row,[bind(receipt)]);value=dict(action='S2 compact source binding repaired',receipt=bind(receipt),next_action=p.next_action(row))
                elif action=='S1' and 'V3.1 fresh audit' in e['reason']:
                    inv=read(p.case_dir(row)/'jobs/audit.json');job=load_job(Path(inv['jobs'][0]));answer=read(job.promote_path)
                    from .stages.s1_treatment import evaluate_v3
                    metrics=evaluate_v3(job.contract['sample'],answer['checks'])
                    if metrics['passed']:raise ValueError('Expected failed V3')
                    park(p,row,e['reason'],metrics,[bind(job.output_path),bind(job.promote_path)])
                    value=dict(action='C50 §3 substantive PARK',metrics=metrics,next_action=p.next_action(row))
                elif action=='history' and 'V2_LIST' in e['reason']:
                    jobs=read(p.case_dir(row)/'jobs/history.json')['jobs'];roles=[]
                    for original in jobs:
                        path=p.effective_job(row,original)
                        if p.backend.job_state(path)!='done':
                            roles.append(dict(job=path,action='running retained; no collection or redispatch'));continue
                        try:p.backend.collect(path);roles.append(dict(job=path,action='validated'))
                        except ContractRejection as exc:
                            if str(exc)!='V9 absence requires all three V2_LIST checks':raise
                            try:replacement=p.replace_submission(row,'history',exc);roles.append(dict(action='§7 replacement prepared',job=bind(replacement)))
                            except Park as failure:park(p,row,str(failure),failure.metrics,[bind(path)]);roles.append(dict(action='PARK',reason=str(failure)))
                    receipt=ensure_once(slot/'CONTRACT.json',dict(roles=roles,authority=bind(AUTH),dispatches=0));reopen(p,row,[bind(receipt)])
                    value=dict(action='C50 §4 contract routing',receipt=bind(receipt),next_action=p.next_action(row))
                elif action=='S2' and 'window changed' in e['reason']:
                    value=window_precheck(p,row,scheduler_owned=True,force=True)
                else:continue
        receipt=ensure_once(slot/'APPLICATION.json',value);resolutions.append(bind(receipt));handled.append((e['design_id'],e['attempt_id']))
    with scheduler_lock(p.registry):
        queue=read(p.registry/'HUB_QUEUE.json')
        for e in queue['entries']:
            if (e['design_id'],e['attempt_id']) in handled:
                e.update(status='SUPERSEDED_C50',prior_status=e.get('prior_status',e['status']),superseded_at=utc(),authority=bind(AUTH))
        atomic(p.registry/'HUB_QUEUE.json',queue)
    remaining=[e for e in queue['entries'] if not e['status'].startswith('SUPERSEDED_')]
    return ensure_once(OUT/'QUEUE_APPLICATION.json',dict(resolutions=resolutions,superseded_entries=len(handled),remaining_open=remaining,downloads=[],dispatches=0,actual_data_estimation_calls=0,outcome_association_calls=0))

if __name__=='__main__':print(apply())
