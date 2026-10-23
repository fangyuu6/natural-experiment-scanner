"""Task C47 durable queue applications and diagnosed one-time transport repair.

No fit, data download, model call, or dispatch. The stopped scheduler can resume
one corrected audit job when the separately prepared restart script is run.
"""
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc
from .storage import ensure_once,sha
from .population_resolution import apply,bind

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'data/l2_pipeline/autopilot/c47'
TARGETS=('bulk10_health_ichia_immigrant_child_coverage','bulk10_health_pharmacist_contraception_fertility','bulk1010_lab_credit_check_ban','bulk10_health_pdmp_operational_establishment')


def apply_queue():
    pilot=Autopilot();results=[]
    for did in TARGETS:
        row=next(r for r in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)
        current=read(row['card'])
        if (row['attempt_id'].startswith('requeue_') and current['first_order'].get('purpose_rule')=='V6.3' and
            current['outcome_family']==current['first_order']['population_resolution']['construct']):
            receipt=read(pilot.registry.parent/'autopilot/requeues'/did/read(row['card'])['version']/'REQUEUE.json')
            results.append(receipt);continue
        value=apply(pilot,row,output=OUT/'resolutions'/did/('metadata_revision' if current['first_order'].get('purpose_rule')=='V6.3' else 'initial'))
        results.append(value['receipt'])
    ensure_once(OUT/'QUEUE_RESOLUTIONS_FINAL.json',dict(designs=results,dispatches=0,outcome_values_read=0,actual_data_estimation_calls=0))
    return results


def repair_naloxone():
    from .agents.jobs import load_job
    from .agents.replacement import emit
    pilot=Autopilot();did='bulk10_health_naloxone_coprescribing_mandate'
    with scheduler_lock(pilot.registry):
        if not (pilot.registry/'AUTOPILOT_STOP').exists():raise ValueError('Stopped scheduler required')
        row=next(r for r in read(pilot.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)
        original=read(pilot.case_dir(row)/'jobs/audit.json')['jobs'][0]
        slot=pilot.replacement_slot(row,original);receipt=read(slot/'REPLACEMENT.json')
        old=load_job(Path(original));failed=load_job(Path(receipt['replacement_job']))
        # Diagnose transport using packet, host logs and actual output locations;
        # never read or promote the malformed-path submission as a gate result.
        doubled=failed.output_path.replace(failed.output_dir,failed.output_dir+'/attempts/'+receipt['attempt_id'],1)
        prompt=Path(failed.job_path).read_text().split('## Answer-free inputs (untrusted data)')[0]
        if doubled not in prompt or Path(failed.output_path).exists() or not Path(doubled).is_file():
            raise ValueError('Expected proven duplicate output routing bug absent; no extra replacement')
        logs=[Path(old.result_path),Path(failed.result_path),Path(old.output_dir)/'DISPATCH_RESULT.json',Path(failed.output_dir)/'DISPATCH_RESULT.json']
        diagnosis=ensure_once(OUT/'NALOXONE_DIAGNOSIS.json',dict(design_id=did,root_cause='Sequential output_path then output_dir replacements duplicated attempts/<id> in the replacement prompt. The worker followed that wrong prompt; metadata expected a different path.',
            original_status='Submitted audit.json but truthfully disclosed outcome search-snippet exposure; ATTESTATION rejected. Original was not missing a submission.',
            replacement_status='Completed with output at the doubled path dictated by the generated prompt; no submission at metadata path.',
            pipeline_bug_confirmed=True,original_job=bind(old.job_path),failed_replacement_job=bind(failed.job_path),logs=[bind(p) for p in logs],misrouted_output=bind(doubled),expected_output_path=failed.output_path,
            remedy='Generic simultaneous routing substitution; one fresh, unchanged-scientific-contract audit packet, queued for restart; earlier evidence and §7 receipt preserved.',
            dispatches=0,additional_replacement_authorized_by='User Task C47 §4: pipeline/template bug exception',outcome_values_read=0))
        # Emit from the original packet, not from the broken replacement. All
        # scientific inputs/sample/seed/template bytes remain unchanged.
        repaired=emit(original,slot/'transport_repair','c47_transport_'+sha(diagnosis)[:16])
        if ('Write JSON to '+repaired.output_path) not in Path(repaired.job_path).read_text():raise ValueError('Repaired output route mismatch')
        transport=ensure_once(slot/'TRANSPORT_REPAIR.json',dict(schema='l2_agent_transport_repair_v1',original_job=original,failed_replacement_job=failed.job_path,
            diagnosis=bind(diagnosis),repaired_job=bind(repaired.job_path),repaired_sidecar=bind(Path(repaired.job_path).with_suffix('.job.json')),extra_replacements=1,dispatches=0))
        stop=pilot.stop_record(row)
        if stop:
            if 'replacement failed' not in stop['reason']:raise ValueError('Transport repair cannot clear another stop')
            current=pilot.case_dir(row)/('STOP_C45.json' if (pilot.case_dir(row)/'STOP_C45.json').exists() else 'STOP.json')
            ensure_once(pilot.case_dir(row)/'STOP_SUPERSEDED_TRANSPORT.json',dict(stop=bind(current),repair=bind(transport),diagnosis=bind(diagnosis)))
        queue=read(pilot.registry/'HUB_QUEUE.json')
        for entry in queue['entries']:
            if entry['design_id']==did and entry.get('status')=='NEEDS_RULING':
                entry.update(status='SUPERSEDED_C47',prior_status='NEEDS_RULING',superseded_at=utc(),diagnosis=bind(diagnosis),transport_repair=bind(transport))
        atomic(pilot.registry/'HUB_QUEUE.json',queue)
        pilot.ledger(row,'audit','PIPELINE_TRANSPORT_REPAIR_PREPARED','record',diagnosis=bind(diagnosis),repair=bind(transport),replacement_job=repaired.job_path,additional_replacements=1,dispatches=0)
        return dict(job=repaired.job_path,next_action=pilot.next_action(row),dispatches=0)
