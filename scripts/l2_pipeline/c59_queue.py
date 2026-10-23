"""C59 outcome-blind queue diagnosis; incomplete research is not absence."""
from pathlib import Path

from .c42 import Park


def require_complete_history(job, value):
    """Route unfinished checkpoints to PARK before strict status validation.

    Do not invent unknown/absent classifications, promote the checkpoint, or
    spend a contract-replacement slot on an acknowledged research shortfall.
    """
    if job.contract.get('jobs_kind') != 'official_history_collection':
        return
    rows = value.get('records', [])
    classified = {r.get('unit') for r in rows if isinstance(r, dict) and r.get('status') is not None}
    unclassified = [u for u in job.contract.get('manual',{}).get('universe',[r.get('unit') for r in rows if isinstance(r,dict)]) if u not in classified]
    if value.get('collection_complete') is False or unclassified:
        raise Park('INSUFFICIENT_WORK: official history is an incomplete research checkpoint',
                   dict(role=job.role, unclassified_units=unclassified,
                        unclassified_count=len(unclassified), original_preserved=True,
                        extra_replacements=0, s1_ready=False))


def apply_queue():
    """Run under a stopped scheduler; zero model calls, fits, or downloads."""
    from .autopilot import Autopilot, read, atomic, scheduler_lock, utc
    from .population_resolution import apply, bind
    from .storage import ensure_once
    from .agents.jobs import load_job
    pilot = Autopilot()
    output = pilot.registry.parent / 'autopilot/c59/queue'
    results = []
    with scheduler_lock(pilot.registry):
        if not (pilot.registry / 'AUTOPILOT_STOP').exists():
            raise ValueError('C59 requires stopped scheduler')
        for did in ('r1011_naloxone_access_law', 'r1011_social_host_liability_minors'):
            dest = output / did / 'APPLICATION.json'
            if dest.exists():
                results.append(read(dest)); continue
            row = next(r for r in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs'] if r['design_id'] == did)
            value = apply(pilot, row, scheduler_owned=True, output=output / did,
                          superseded_status='SUPERSEDED_C59')
            value['diagnosis'] = ('Intake population_resolution had catalog hashes but purpose remained '
                                  'pending_S0_verification/V6.2; precheck incorrectly skipped fresh verified purpose. '
                                  'Existing structured construct is retained; no new construct interpretation.')
            ensure_once(dest, value); results.append(value)
        for did in ('b1011eh_complete_streets_statute', 'b1011tl_snap_call_center'):
            dest = output / did / 'APPLICATION.json'
            if dest.exists():
                results.append(read(dest)); continue
            row = next(r for r in read(pilot.registry / 'ACTIVE_DESIGNS.json')['designs'] if r['design_id'] == did)
            if any((pilot.backend.root(row) / n).exists() for n in ('FREEZE_PREP.json', 'FREEZE_FINAL.json', 'TERMINAL.json')):
                raise ValueError('C59 cannot modify frozen or finished design')
            jobs = [load_job(Path(pilot.effective_job(row, p))) for p in read(pilot.case_dir(row) / 'jobs/history.json')['jobs']]
            evidence, gaps, transport = [], [], None
            for job in jobs:
                from .agents.sonnet_queue import state as queue_state
                state=queue_state(job.job_path,pilot.repo)
                completion=read(Path(job.output_dir)/'DISPATCH_RESULT.json',{})
                if state=='running' or ((Path(job.output_dir)/'DISPATCH_ONCE.json').exists() and completion.get('status')!='COMPLETED'):
                    raise ValueError('Running agent must remain untouched')
                for path in (job.job_path, Path(job.job_path).with_suffix('.job.json'), job.output_path,
                             Path(job.output_dir) / 'DISPATCH_RESULT.json', job.result_path):
                    if Path(path).exists(): evidence.append(bind(path))
                try: require_complete_history(job, read(job.output_path, {}))
                except Park as exc: gaps.append(dict(reason=str(exc), **exc.metrics))
                replacement=pilot.replacement_slot(row,job.job_path)/'REPLACEMENT.json'
                if replacement.exists():
                    receipt=read(replacement);old=load_job(Path(receipt['original_job']))
                    prior_completion=Path(old.output_dir)/'DISPATCH_RESULT.json'
                    for path in (replacement,old.job_path,prior_completion,old.result_path):
                        if Path(path).exists():evidence.append(bind(path))
                    transport=dict(original_job=old.job_path,replacement_job=job.job_path,
                        original_completion=read(prior_completion,{}),replacement_completion=completion,
                        original_output_present=Path(old.output_path).exists(),replacement_output_present=Path(job.output_path).exists(),
                        prior_stop=pilot.stop_record(row))
            if not gaps:
                raise ValueError('Expected diagnosed incomplete research checkpoint absent')
            snap = did == 'b1011tl_snap_call_center'
            diagnosis = dict(design_id=did, decision='PARK', pipeline_bug_confirmed=snap,
                cause=('C58 clean predecessor was selected after replacement lookup, so the scheduler re-read '
                       'the predecessor with no submission while its replacement was still running. The completed '
                       'replacement is at the correct path and explicitly incomplete; both roles lack research.' if snap else
                       'Coder B explicitly reports incomplete collection with 31 unclassified null statuses. '
                       'Strict status validation surfaced a generic exception instead of INSUFFICIENT_WORK.'),
                remedy=('Resolve C58-clean replacement identity before worker-state inspection; preserve completed '
                        'replacement, prepare one fresh B transport-repair packet authorized by C59; '
                        'PARK design for independent coder A incomplete evidence; no model dispatch.' if snap else
                        'Route explicit incomplete official-history checkpoints to INSUFFICIENT_WORK PARK; '
                        'never recode unfinished rows as unknown or absent.'),
                gaps=gaps, evidence=evidence, transport_timeline=transport, extra_replacements=1 if snap else 0, dispatches=0,
                outcome_values_read=0, actual_data_estimation_calls=0)
            proof = ensure_once(output / did / 'DIAGNOSIS.json', diagnosis)
            repair=None
            if snap:
                from .agents.replacement import emit
                from .storage import sha
                if not transport or transport['prior_stop']['time'] >= transport['replacement_completion']['completed_at']:
                    raise ValueError('Expected premature replacement failure is not established')
                fresh=emit(transport['original_job'],output/did/'fresh_replacement','c59_transport_'+sha(proof)[:16])
                slot=pilot.replacement_slot(row,transport['original_job'])
                receipt=ensure_once(slot/'TRANSPORT_REPAIR.json',dict(schema='l2_agent_transport_repair_v1',
                    original_job=transport['original_job'],failed_replacement_job=transport['replacement_job'],
                    diagnosis=bind(proof),repaired_job=bind(fresh.job_path),
                    repaired_sidecar=bind(Path(fresh.job_path).with_suffix('.job.json')),
                    extra_replacements=1,dispatches=0,authorization='User Task C59 §3: one fresh replacement for pipeline-caused failure',
                    dispatch_blocker='Independent coder A incomplete research; design remains PARKED'))
                repair=bind(receipt)
            stop = pilot.stop_record(row)
            originals = [p for p in pilot.case_dir(row).glob('STOP*.json') if read(p) == stop]
            if len(originals) != 1: raise ValueError('Unique retained stop required')
            ensure_once(pilot.case_dir(row) / 'STOP_SUPERSEDED_C59.json',
                        dict(stop=bind(originals[0]), evidence=[bind(proof)]))
            ensure_once(pilot.case_dir(row) / 'PARKED.json',
                        dict(design_id=did, attempt_id=row['attempt_id'], action='history', status='PARKED',
                             reason='INSUFFICIENT_WORK: complete independent state history required', diagnosis=bind(proof)))
            queue = read(pilot.registry / 'HUB_QUEUE.json')
            for entry in queue['entries']:
                if (entry['design_id'], entry['attempt_id'], entry.get('status')) == (did, row['attempt_id'], 'NEEDS_RULING'):
                    entry.update(status='SUPERSEDED_C59', prior_status='NEEDS_RULING', superseded_at=utc(), diagnosis=bind(proof))
            atomic(pilot.registry / 'HUB_QUEUE.json', queue)
            value = dict(design_id=did, next_action=pilot.next_action(row), diagnosis=bind(proof),transport_repair=repair)
            ensure_once(dest, value); results.append(value)
            pilot.ledger(row, 'history', 'SUPERSEDED_C59', 'record', diagnosis=bind(proof), disposition='PARKED')
    return results
