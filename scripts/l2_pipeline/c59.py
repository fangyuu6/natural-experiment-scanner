"""C59 stopped-scheduler manual supplement application; no agent dispatch."""
from pathlib import Path
from .autopilot import Autopilot,read,atomic,utc,scheduler_lock
from .storage import ensure_once,sha
from .manual_evidence import binding,load


def resume_manual():
    p=Autopilot();out=p.repo/'data/l2_pipeline/autopilot/c59/manual'
    if (out/'APPLICATION.json').exists():return read(out/'APPLICATION.json')
    with scheduler_lock(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C59 requires stopped scheduler')
        row=next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']=='r1011_good_samaritan_911')
        root=p.backend.root(row)
        if any((root/n).exists() for n in ('CODING_LOCK.json','FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_FINAL.json','TERMINAL.json','EXECUTION_ONCE.json')):
            raise ValueError('Manual supplement cannot rewrite reconciled/frozen design')
        packet=load(p.repo,row['design_id'],read(row['card'])['treatment']['predicate'])
        roles=[]
        from .agents.jobs import load_job
        from .agents.collect import collect_job
        for original in read(p.case_dir(row)/'jobs/history.json')['jobs']:
            path=p.effective_job(row,original);job=load_job(Path(path))
            # Completed original only; never edit running jobs or transport packets.
            running=(Path(job.output_dir)/'DISPATCH_ONCE.json').exists() and not (Path(job.output_dir)/'DISPATCH_RESULT.json').exists()
            from .agents.sonnet_queue import state as queue_state
            running=running or queue_state(path,p.repo)=='running'
            if Path(job.output_path).exists() and not running:
                collection=collect_job(path)
                roles.append(dict(role=job.role,collection=binding(Path(job.output_dir)/'COLLECTION.json'),validated=collection['validated']))
            else:roles.append(dict(role=job.role,job=binding(path),action='await_independent_submission'))
        receipt=ensure_once(out/'INTEGRATION.json',dict(design_id=row['design_id'],roles=roles,
            manual_evidence=packet['bindings'],predicate_review=packet['review'],
            independent_ab_required=True,fresh_v3_required=True,dispatches=0,execution_authorized=False))
        stop=p.stop_record(row)
        paths=[x for x in p.case_dir(row).glob('STOP*.json') if read(x)==stop]
        if len(paths)!=1:raise ValueError('Expected unique retained stop')
        ensure_once(p.case_dir(row)/'STOP_SUPERSEDED_C59.json',dict(stop=binding(paths[0]),evidence=[binding(receipt)],execution_authorized=False))
        queue=read(p.registry/'HUB_QUEUE.json')
        for e in queue['entries']:
            if e['design_id']==row['design_id'] and e['attempt_id']==row['attempt_id'] and not e['status'].startswith('SUPERSEDED_'):
                e.update(prior_status=e['status'],status='SUPERSEDED_C59',superseded_at=utc(),evidence=binding(receipt))
        atomic(p.registry/'HUB_QUEUE.json',queue)
        p.ledger(row,'resume','SUPERSEDED_C59','record',evidence=binding(receipt),independent_ab_required=True,fresh_v3_required=True)
        application=dict(design_id=row['design_id'],receipt=binding(receipt),next_action=p.next_action(row),dispatches=0,execution_authorized=False)
        ensure_once(out/'APPLICATION.json',application);return application

if __name__=='__main__':print(resume_manual())
