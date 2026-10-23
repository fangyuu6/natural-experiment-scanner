"""Tick-applied hub data closure; never edit a seal or consume execution slots."""
from pathlib import Path
from .storage import ensure_once, sha


def accept(p,row,jobs):
    from .autopilot import read
    from .registration import hub_signer
    from .stages.s6_execute import sealed_review_evidence
    root=p.backend.root(row)
    if any((root/n).exists() for n in ('EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json')):
        raise ValueError('Consumed execution cannot acquire a closure')
    if (p.case_dir(row)/'EXECUTE_INTENT.json').exists():
        raise ValueError('Execution intent cannot acquire a closure')
    if len(jobs)!=1: raise ValueError('One sealed review job required')
    job=Path(jobs[0]);meta=read(job.with_suffix('.job.json'))
    if (meta.get('design_id')!=row['design_id'] or meta.get('job_sha256')!=sha(job)
            or Path(meta.get('promote_path','')).resolve()!=(root/'EXECUTION_REVIEW.json').resolve()
            or meta.get('contract',{}).get('candidate_sha256')!=sha(root/'FREEZE_FINAL.json')):
        raise ValueError('Sealed job identity/candidate binding differs')
    evidence=sealed_review_evidence(root,sha(root/'FREEZE_FINAL.json'),row['design_id'],hub_signer(p.backend.ctx(row)))
    if 'review_resolution_sha256' not in evidence: raise ValueError('Hub closure required')
    # Accept ordinary JSON formatting; the write-once receipt binds exact bytes.
    ensure_once(p.case_dir(row)/'SEALED_CLOSURE_ACCEPTED.json',dict(
        schema='l2_sealed_closure_acceptance_v1',design_id=row['design_id'],
        attempt_id=row['attempt_id'],candidate_sha256=sha(root/'FREEZE_FINAL.json'),
        **evidence,execution_authorized=False))
    return root/'EXECUTION_REVIEW_RESOLUTION.json'


def resume(p,row):
    from .autopilot import read,atomic,utc
    from .population_resolution import bind
    slot=p.case_dir(row); marker=slot/'STOP_SUPERSEDED_C70.json'
    stop=p.stop_record(row)
    if marker.exists() and stop is None:
        _complete(p,row,marker)
        return False
    reason=stop.get('reason','') if stop else ''
    # 'review SHOULD' (C70) or the class-table NOVEL text (HUB_RULING_20261019_NOVEL_GATE Amendment 1); accept() still needs a valid closure.
    if not stop or stop.get('action')!='sealed' or not ('review SHOULD' in reason or 'novel/science finding requires explicit validated hub closure' in reason.lower()):
        return False
    inventory=read(slot/'jobs/sealed.json')
    if not inventory: return False
    try: closure=accept(p,row,inventory.get('jobs',[]))
    except (OSError,ValueError,KeyError,TypeError,AttributeError): return False
    matches=[f for f in slot.glob('STOP*.json') if not f.name.startswith('STOP_SUPERSEDED') and read(f)==stop]
    if len(matches)!=1: raise ValueError('Unique retained sealed stop required')
    # Checkpoint first: crash before marker is safely replayable using ensure_once.
    ensure_once(p.checkpoint(row,'sealed'),inventory)
    root=p.backend.root(row)
    ensure_once(marker,dict(stop=bind(matches[0]),evidence=[bind(closure),
        bind(root/'EXECUTION_REVIEW.json'),bind(root/'FREEZE_FINAL.json')],execution_authorized=False))
    _complete(p,row,marker)
    return True


def _complete(p,row,marker):
    from .autopilot import read,atomic,utc
    from .population_resolution import bind
    queue=read(p.registry/'HUB_QUEUE.json',dict(entries=[]))
    changed=False
    for entry in queue['entries']:
        if (entry.get('design_id'),entry.get('attempt_id'),entry.get('status'))==(row['design_id'],row['attempt_id'],'NEEDS_RULING'):
            entry.update(status='SUPERSEDED_C70',superseded_at=utc(),resolution=bind(marker))
            changed=True
    if changed:atomic(p.registry/'HUB_QUEUE.json',queue)
    p.ledger(row,'resume','SUPERSEDED_C70','record',resolution=bind(marker))
