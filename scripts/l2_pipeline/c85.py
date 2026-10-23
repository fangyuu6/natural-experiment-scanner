"""Owner D8 fresh-audit remainder census; prospective, metadata-only records."""
import json
from pathlib import Path
from .storage import sha, ensure_once, public_input

REPO = Path(__file__).resolve().parents[2]
RULE = 'docs/plans/l2-pipeline/HUB_RULING_C85_D8_REMAINDER_CENSUS.md'
APPROVED = '2026-10-12'
PARK_REASON = 'Fresh V3 ten-treated audit unavailable after prior-sample exclusions'


def census_receipt(repo, sample):
    return dict(rule=RULE, owner_approval_date=APPROVED, mode='census_of_remainder',
        sample_size=len(sample), remaining_treated=sample,
        authority=dict(path=str(Path(repo)/RULE), sha256=sha(Path(repo)/RULE)))


def validate_census(job, selection, sample):
    expected = census_receipt(job.repo, sample)
    if selection.get('remainder_census') != expected:
        raise ValueError('D8 remainder census authority/roster changed')


def census_bound(reconciliation_path, sample, repo=REPO):
    """True only when the AUDIT_SELECTION beside this reconciliation binds the exact D8 receipt.

    V3.1 recomputes the corrected comparison from bound artifacts alone. A 1-9 state
    remainder is a valid audit only under the owner D8 census receipt (ruling C85);
    without that receipt the ordinary ten-state rule applies and the audit does not pass.
    """
    selection_path = Path(reconciliation_path).with_name('AUDIT_SELECTION.json')
    if not 0 < len(sample) < 10 or not selection_path.is_file():
        return False
    selection = json.loads(public_input(selection_path).read_text())
    return (selection.get('sample') == sample
            and selection.get('remainder_census') == census_receipt(repo, sample))


def apply(pilot=None):
    """Resume exact D8 parks using the existing card-bound requeue path."""
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .c70_l3 import protected
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.registry.parent/'autopilot/c85'
    with guard(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists() or read(p.registry/'CODE_TASK.lock',{}).get('task') != 'C85':
            raise ValueError('C85 stopped code window required')
        clean_stop(p.repo)
        application = out/'APPLICATION.json'
        if application.exists(): return read(application)
        results=[]
        for row in read(p.registry/'ACTIVE_DESIGNS.json')['designs']:
            # Replay a committed requeue if application recording was interrupted.
            version=read(row['card'])['version']
            receipt=p.registry.parent/'autopilot/requeues'/row['design_id']/version/'REQUEUE.json'
            if receipt.exists() and read(read(receipt)['ruling']).get('superseded_status')=='SUPERSEDED_C85':
                results.append(requeue(row['design_id'],Path(row['card']),Path(read(receipt)['ruling']),p))
                continue
            park=read(p.case_dir(row)/'PARKED.json',{})
            if park.get('reason') != PARK_REASON or p.next_action(row) != 'PARK' or protected(p.backend.root(row)):
                continue
            report=read(p.backend.root(row)/'RECONCILIATION.json',{})
            from .intake.history_routing import select_fresh_sample
            records=report.get('audit_eligible_records',[])
            if not records: continue
            try: sample=select_fresh_sample(records,row['fresh_policy'])
            except ValueError: continue
            if not 0 < len(sample) < 10: continue
            card=read(row['card']);card['version'] = 'v'+str(int(card['version'][1:])+1)
            # Keep relative source paths anchored at the same directory.
            path=Path(row['card']).with_name(Path(row['card']).stem+'_c85.json')
            ensure_once(path,card)
            ruling=ensure_once(out/'rulings'/(row['design_id']+'.json'),dict(
                schema='l2_requeue_ruling_v1',design_id=row['design_id'],
                prior_card_sha256=sha(row['card']),card_sha256=sha(path),decision='resume',
                reason='Owner D8: audit all remaining treated states; approval '+APPROVED,
                superseded_status='SUPERSEDED_C85',authority=census_receipt(p.repo,sample)['authority'],
                owner_approval_date=APPROVED,prior_park=dict(path=str(p.case_dir(row)/'PARKED.json'),
                    sha256=sha(p.case_dir(row)/'PARKED.json'))))
            # Requeue acquires the scheduler lock and checks the stopped path.
            results.append(requeue(row['design_id'],path,ruling,p))
        value=dict(records=results,actual_data_estimation_calls=0,dispatches=0)
        ensure_once(application,value)
        return value


V31_PARK_REASON = 'V3.1 fresh audit did not pass'


def audit_passes_now(pilot, row):
    """Replay the V3.1 recomputation on the design's bound audit (read-only; no write)."""
    from .agents.jobs import load_job
    from .autopilot_history import bind
    from .c42 import Park
    from .c48_audit import bound_comparison
    audit = pilot.done(row, 'audit')
    if not audit or len(audit.get('jobs', [])) != 1:
        return False
    try:
        job = load_job(Path(audit['jobs'][0]))
        evidence = [bind(job.output_path), bind(job.promote_path), bind(Path(job.output_dir)/'COLLECTION.json'),
                    bind(job.contract['reconciliation_path'])]
        bound_comparison(evidence)
    except (Park, ValueError, OSError, KeyError, TypeError):
        return False  # changed inputs or a real failure: never reopen on doubt
    return True


def reopen_v31(pilot=None):
    """YIELD_FIXES bug (a): reopen census designs parked only by the missing remainder_census flag.

    A design qualifies when it is PARKED at S1 with the exact V3.1 reason, is not frozen, and its own
    bound audit passes under the fixed code. Real failures stay parked. The reopen is the C85 route:
    a new card version through the existing card-bound requeue (decision=resume), citing ruling C85.
    """
    from .autopilot import Autopilot, read
    from .ops.watchdog import clean_stop, guard
    from .c70_l3 import protected
    from .requeue import requeue
    p = pilot or Autopilot()
    out = p.repo/'data/l2_pipeline/autopilot/yield_fixes/d8_v31'
    with guard(p.registry):
        if not (p.registry/'AUTOPILOT_STOP').exists() or read(p.registry/'CODE_TASK.lock', {}).get('task') != 'YIELD_FIXES':
            raise ValueError('YIELD_FIXES stopped code window required')
        clean_stop(p.repo)
        application = out/'APPLICATION.json'
        if application.exists():
            return read(application)
        results, kept = [], []
        for row in read(p.registry/'ACTIVE_DESIGNS.json')['designs']:
            park = read(p.case_dir(row)/'PARKED.json', {})
            if park.get('reason') != V31_PARK_REASON or park.get('action') != 'S1' or p.next_action(row) != 'PARK':
                continue
            if protected(p.backend.root(row)) or not audit_passes_now(p, row):
                kept.append(row['design_id'])
                continue
            card = read(row['card'])
            card['version'] = 'v'+str(int(card['version'][1:])+1)
            path = ensure_once(Path(row['card']).with_name(Path(row['card']).stem+'_yf.json'), card)
            ruling = ensure_once(out/'rulings'/(row['design_id']+'.json'), dict(
                schema='l2_requeue_ruling_v1', design_id=row['design_id'], prior_card_sha256=sha(row['card']),
                card_sha256=sha(path), decision='resume', superseded_status='SUPERSEDED_YIELD_FIXES',
                reason='Owner D8 census (C85): the V3.1 recomputation omitted remainder_census; fixed, audit passes; approval '+APPROVED,
                authority=dict(path=str(p.repo/RULE), sha256=sha(p.repo/RULE)), owner_approval_date=APPROVED,
                prior_park=dict(path=str(p.case_dir(row)/'PARKED.json'), sha256=sha(p.case_dir(row)/'PARKED.json')),
                execution_authorized=False))
            results.append(requeue(row['design_id'], path, ruling, p))
        ensure_once(application, dict(records=results, kept_parked=kept, dispatches=0, actual_data_estimation_calls=0))
        return read(application)


if __name__=='__main__':
    import json, sys
    print(json.dumps(reopen_v31() if sys.argv[1:] == ['reopen_v31'] else apply(), indent=2))
