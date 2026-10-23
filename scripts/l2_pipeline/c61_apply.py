"""Stopped-host C61 application through the existing checked mechanical rebind."""
from pathlib import Path
from scripts.l2_pipeline.autopilot import Autopilot,read,atomic
from scripts.l2_pipeline.c57 import migrate
from scripts.l2_pipeline.storage import ensure_once,sha
BASE=Path('data/l2_pipeline/autopilot/c61')
DESIGNS=['b1010_handheld_ban_all','b1010_permitless_carry','b1010_primary_seatbelt',
         'b1010b_zero_tolerance_under21','b1010b_child_restraint_booster_age5','b1010b_pdmp_mandatory_query']
def main():
    p=Autopilot()
    authority=ensure_once(BASE/'MECHANICAL_AUTHORIZATION.json',dict(schema='l2_c57_mechanical_authorization_v1',
        task='C61',designs=DESIGNS,authority='Explicit C61 user request; HUB_RULING_C54_CLOSURES.md §2',
        reason='Schema-valid independent SPEC/card checks, named anchor columns, suppression eligibility, RI-count alignment and reporting fixes; prior packages preserved; fresh draft review required',
        execution_authorized=False))
    records=[]
    for did in DESIGNS:
        prior=read(BASE/'resolutions'/did/'APPLICATION.json')
        if prior:
            record=prior
        else:
            old=next(r for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs'] if r['design_id']==did)
            record=migrate(p,old,BASE/'resolutions'/did,authority)
        row=record['new_row'];root=p.backend.root(row)
        completed=read(BASE/'resolutions'/did/'DRAFT_REFRESH_C61.json')
        if completed:
            records.append(dict(record,c61_draft_jobs=completed['review_jobs']));continue
        notes=read(BASE/'REVIEW_FINDINGS.json')
        notes=dict(notes,design_id=did,spec_sha256=sha(root/'SPEC.json'),audit_evidence={})
        for name in ('S03_IMPACT.json','ZERO_TOLERANCE_TIMING.json'):
            evidence=read(BASE/name)
            if name=='S03_IMPACT.json':evidence=dict(evidence,studies=[r for r in evidence['studies'] if r['design_id']==did])
            if name=='ZERO_TOLERANCE_TIMING.json' and 'zero_tolerance' not in did:continue
            notes['audit_evidence'][name]=evidence
        ensure_once(root/'FINDING_RESOLUTIONS.json',notes)
        from scripts.l2_pipeline.agents import emit_stage_jobs
        jobs=emit_stage_jobs(p.backend.ctx(row,strict=True),'S6',jobs_root=BASE/'draft_jobs',allow_draft=True,attempt_id=row['attempt_id']+'_c61_current')
        previous=read(p.case_dir(row)/'jobs/draft.json')
        refresh=ensure_once(BASE/'resolutions'/did/'DRAFT_REFRESH_C61.json',dict(previous_jobs=previous,review_jobs=[j.job_path for j in jobs],review_evidence_sha256=sha(root/'FINDING_RESOLUTIONS.json'),dispatches=0,execution_authorized=False))
        atomic(p.case_dir(row)/'jobs/draft.json',dict(jobs=[j.job_path for j in jobs]))
        p.ledger(row,'draft','C61_REEMITTED','record',receipt=str(refresh),dispatches=0)
        records.append(dict(record,c61_draft_jobs=[j.job_path for j in jobs]));print(did,row['attempt_id'],p.next_action(row),flush=True)
    ensure_once(BASE/'APPLICATION.json',dict(designs=records,dispatches=0,actual_data_estimation_calls=0,execution_authorized=False))

if __name__=="__main__": main()


def refresh_verified_reviews():
    """Re-emit only C61's undispatched draft pointers against the final code."""
    from scripts.l2_pipeline.agents import emit_stage_jobs
    from scripts.l2_pipeline.registration import _check_evidence
    p=Autopilot();_check_evidence(BASE/'TEST_REPORT.json')
    if not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('Stopped scheduler required')
    rows=read(p.registry/'ACTIVE_DESIGNS.json')['designs'];records=[]
    for did in DESIGNS:
        row=next(r for r in rows if r['design_id']==did)
        previous=read(p.case_dir(row)/'jobs/draft.json')
        jobs=emit_stage_jobs(p.backend.ctx(row,strict=True),'S6',jobs_root=BASE/'final_draft_jobs',allow_draft=True,attempt_id=row['attempt_id']+'_c61_verified')
        path=ensure_once(BASE/'resolutions'/did/'DRAFT_FINAL_C61.json',dict(previous_jobs=previous,review_jobs=[j.job_path for j in jobs],test_report_sha256=sha(BASE/'TEST_REPORT.json'),code_snapshot=jobs[0].contract['code_snapshot'],dispatches=0,execution_authorized=False))
        atomic(p.case_dir(row)/'jobs/draft.json',dict(jobs=[j.job_path for j in jobs]))
        p.ledger(row,'draft','C61_VERIFIED_REEMITTED','record',receipt=str(path),dispatches=0)
        records.append(dict(design_id=did,jobs=[j.job_path for j in jobs],receipt=str(path)))
    ensure_once(BASE/'FINAL_DRAFTS.json',dict(records=records,execution_authorized=False,dispatches=0))
    return records
