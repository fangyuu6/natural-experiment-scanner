"""Prepared-only full-chain sandbox replay; default proposed fixture is SYG.

This CLI does nothing without --run AND a fixture approval JSON. It never
registers a study or dispatches reviewers. Retained independent fixture reviews
are test inputs, not a review of current code. Output is isolated under c62.
"""
import argparse,json,shutil
from pathlib import Path
from types import SimpleNamespace
from .storage import ensure_once,sha,public_input

BASE=Path(__file__).resolve().parents[2]/'data/l2_pipeline/autopilot/c62'


def plan(fixture='SYG'):
    return dict(schema='l2_c62_rehearsal_plan_v1',proposed_fixture=fixture,fixture_confirmed=False,
        stages=['prep','approve-prep','calibrate','seal','DRY-LOAD','registered sandbox execution'],
        command='.venv_did/bin/python -B -m scripts.l2_pipeline.c62_rehearsal --run --approval APPROVED_FIXTURE.json --test-report data/l2_pipeline/autopilot/c62/TEST_REPORT.json',
        approval_schema=dict(schema='l2_c62_fixture_approval_v1',fixture=fixture,source_root='OWNER_CONFIRMED_FINISHED_ROOT',
            final_manifest_sha256='OWNER_CONFIRMED_SHA256',approved=True),
        output_root=str(BASE/'rehearsal'),run_count=0,dispatches=0,production_registry_writes=False)


def replay(approval,test_report):
    from . import registration as reg
    from .autopilot import read
    from .cards import load_card
    from .contracts import StageContext
    from dataclasses import replace
    from .dry_load import run as dry_load
    from .contract_card import registered_execution_card
    from .freeze import verify
    from .snapshots import call
    auth=read(public_input(Path(approval)));source=public_input(Path(auth['source_root']))
    if (auth.get('schema')!='l2_c62_fixture_approval_v1' or auth.get('approved') is not True
        or auth.get('final_manifest_sha256')!=sha(source/'FREEZE_FINAL.json')):
        raise ValueError('Owner-confirmed hash-bound fixture approval required')
    if not any((source/n).is_file() for n in ('TERMINAL.json','RESULT.json','results.json')):
        raise ValueError('Only a finished design may be rehearsed')
    report=public_input(Path(test_report));reg._check_evidence(report)
    manifest=read(source/'FREEZE_FINAL.json');registration=read(source/'REGISTRATION.json')
    card=load_card(Path(registration['card_path']))
    if sha(card.path)!=registration['card_sha256']:raise ValueError('Fixture source card changed')
    run=BASE/'rehearsal'/auth['final_manifest_sha256']
    if run.exists():raise FileExistsError('Rehearsal is write-once; no retry')
    root=run/'work'/card.design_id/card.version
    # Source reads only. Preserve an inventory before creating the isolated copy.
    before={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    shutil.copytree(source,root,ignore=shutil.ignore_patterns('code_snapshots','results','runs','receipts','agents'))
    # Delete only generated artifacts in the newly copied sandbox; originals
    # remain immutable. Historical review/forecast inputs stay as fixture bytes.
    generated=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json',
        'APPROVAL_FINAL.template.json','CODE.json','ENVIRONMENT.json','REGISTRATION.json',
        'REVIEW.json','TEST_EVIDENCE.json','TEST_EVIDENCE_SELECTION.json','INTERVAL_PLAN.json',
        'ANCHOR.json','RI_FEASIBILITY.json','DIAGNOSTIC_CALIBRATION.json','INTERVAL_EVALUATION.json',
        'INTERVAL_CERTIFICATION.json','CALIBRATION_GATES.json','FINAL_READINESS.json','FINAL_READINESS_EVIDENCE.json',
        'DRY_LOAD.json','EXECUTION_ONCE.json','EXECUTION_REVIEW.json','TERMINAL.json','FAILURE.json','RESULT.json')
    for name in generated:
        (root/name).unlink(missing_ok=True)
    payload=read(source/'SCIENTIFIC_CARD.json') or dict(card.payload)
    card=replace(card,payload=payload)
    ctx=StageContext(card,run/'work',Path(__file__).resolve().parents[2])
    signer='C62_SANDBOX_ONLY'
    args=SimpleNamespace(signer=signer,signed_at='2026-10-11T00:00:00Z',test_report=report,review_attempt=registration['readiness_review_attempt'])
    receipts=[]
    receipts.append(dict(stage='prep',result=reg.prep(ctx,args,[card.design_id])))
    receipts.append(dict(stage='approve-prep',result=reg.approve(ctx,args,'prep')))
    receipts.append(dict(stage='calibrate',result=reg.calibration(ctx)))
    receipts.append(dict(stage='seal',result=reg.seal([ctx])))
    receipts.append(dict(stage='DRY-LOAD',result=dry_load(root,signer)))
    # Sandbox-only worker executes the same registered runner under copied code.
    frozen=read(root/'FREEZE_FINAL.json')
    result=call(root,frozen,'c62_sandbox_replay',dict(approval=str(Path(approval).resolve())))
    receipts.append(dict(stage='registered sandbox execution',result=result))
    after={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file()}
    if before!=after:raise ValueError('Source fixture changed during sandbox replay')
    return ensure_once(run/'REHEARSAL_RECEIPT.json',dict(stages=receipts,source_unchanged=True,production_registry_writes=False,fixture_authority=auth))


def execute_copy(root,approval):
    """Snapshot entry, inaccessible to the production scheduler."""
    from .autopilot import read
    from .contract_card import registered_execution_card
    from .registered import load_registration,run_registered
    root=Path(root).resolve();auth=read(approval)
    marker='/data/l2_pipeline/autopilot/c62/rehearsal/'
    if marker not in str(root) or auth.get('approved') is not True:
        raise ValueError('Sandbox replay outside approved copy')
    # Exclusive reservation; copying or rerunning a finished production path is forbidden.
    with (root/'SANDBOX_EXECUTION_ONCE').open('x') as f:f.write('C62 sandbox only\n')
    manifest=read(root/'FREEZE_FINAL.json')
    if auth['final_manifest_sha256']!=root.parents[2].name:
        raise ValueError('Sandbox approval path binding changed')
    bundle=load_registration(root,registered_execution_card(root,manifest))
    value=run_registered(bundle)
    path=ensure_once(root/'SANDBOX_RESULT.json',value)
    return dict(path=str(path),sha256=sha(path),status=value['status'],production_registry_writes=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',default='SYG');parser.add_argument('--run',action='store_true')
    parser.add_argument('--approval',type=Path);parser.add_argument('--test-report',type=Path)
    args=parser.parse_args()
    if not args.run:print(json.dumps(plan(args.fixture),indent=2));return
    if not args.approval or not args.test_report:parser.error('--run requires owner fixture approval and current test evidence')
    print(replay(args.approval,args.test_report))

if __name__=='__main__':main()
