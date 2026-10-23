"""Preparation/collection bridge; process launch belongs to the host dispatcher."""
from dataclasses import asdict
import json
from pathlib import Path
from ..agents import emit_stage_jobs, collect_job, emit_purpose_verification, accept_purpose_verification
from ..storage import ensure_once


def prepare(ctx, stage, *, roles=None, allow_draft=False):
    # Disposable fixture workspaces keep their packets alongside their own files.
    jobs_root = None if ctx.workspace.resolve().is_relative_to(ctx.repo.resolve()) else ctx.workspace / "jobs"
    options={}
    pointer=ctx.design_dir/"SOURCE_PACK.json"
    if stage=="S1" and pointer.is_file():
        value=json.loads(pointer.read_text());jobs_root=Path(value["jobs_root"])
        options={"source_pack_path":Path(value["manifest_path"])}
        if roles!=["auditor"]: options["attempt_id"]=value["attempt_id"]
    jobs = emit_stage_jobs(ctx, stage, jobs_root=jobs_root, roles=roles, allow_draft=allow_draft,**options)
    suffix = "_" + "_".join(roles) if roles else ""
    manifest = ensure_once(ctx.design_dir / (stage + suffix + "_AGENT_JOBS.json"),
                           {"jobs": [asdict(job) for job in jobs], "execution": "external_host_only", "retries": 0})
    for job in jobs:
        # The collector revalidates prompt, input bindings and the stage contract.
        if Path(job.output_path).is_file():
            collect_job(Path(job.job_path))
    return manifest, jobs
