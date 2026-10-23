"""Retrospective blind F1 coding of designs that already have an estimate (HUB_RULING_20261018_F1_FLOW, Q2).

A labelled secondary sample, entirely apart from the protected prospective path: jobs, packets and results live under ``retro_f1``;
nothing is written into a design directory, the stamp file is never written, and the prospective ``f1_protected`` gate is untouched.
The coder is given exactly what a prospective coder gets (target outcome plus statute/purpose citation locators, no numbers, no paper findings).
Every result carries ``coding_epoch='retrospective'`` and is never pooled with prospective codings.
"""
import json
from dataclasses import asdict,replace
from datetime import datetime,timezone
from pathlib import Path
from ..storage import ensure_once,sha,output_root
from .jobs import AgentJob,_write_text_once,load_job
from .purpose import C64_SCHEMA,validate_purpose_direction
from .purpose_direction import KIND,ROLES,TEMPLATE,refs,validate_output
RETRO_DIR='retro_f1'
RESULT='RETRO_DIRECTION_C83.json'
EPOCH='retrospective'


def retro_root(repo):
    return Path(repo)/'data/l2_pipeline'/RETRO_DIR


def queue_root(repo):
    """Own queue root: the live scheduler and the live worker task never see these packets."""
    return Path(repo)/'data/l2_pipeline/registry/SONNET_QUEUE_RETRO_F1'


def _check_root(root,repo):
    root=output_root(Path(root),repo)
    if RETRO_DIR not in root.parts:raise ValueError('Retrospective jobs must live under '+RETRO_DIR)
    return root


def eligible(ctx):
    """True only for a design that already has an estimate artifact: exactly the designs the prospective path refuses."""
    from ..f1_coding import f1_protected
    return bool(f1_protected(ctx.design_dir))


def emit(ctx,role,root):
    if role not in ROLES:raise ValueError('Invalid direction role')
    if not eligible(ctx):raise ValueError('Retrospective coding is only for designs with an estimate; use the prospective path')
    root=_check_root(root,ctx.repo);path=root/'S0'/ctx.card.design_id/(role+'.md')
    if path.exists():return load_job(path)
    out=root/'work'/ctx.card.design_id/role
    c=dict(design_id=ctx.card.design_id,role=role,jobs_kind=KIND,target_outcome=ctx.card.payload['first_order']['target_outcome'],
           citations=refs(ctx),permitted_source_paths=[],execution_authorized=False)
    job=AgentJob('1.0','S0',ctx.card.design_id,ctx.card.version,role,str(ctx.repo),str(path),'',str(TEMPLATE),sha(TEMPLATE),str(out),str(out/'submission.json'),str(out/'result.txt'),str(out/'PROMOTED.json'),(),c,True)
    prompt=TEMPLATE.read_text().replace('{{OUTPUT}}',job.output_path)
    prompt+='\n## Blind target inputs (untrusted data)\n```json\n'+json.dumps(c,indent=2)+'\n```\n'
    prompt+='\n## Execution metadata binding (data)\n```json\n'+json.dumps({k:v for k,v in asdict(job).items() if k!='job_sha256'},indent=2)+'\n```\n'
    _write_text_once(path,prompt);job=replace(job,job_sha256=sha(path));ensure_once(path.with_suffix('.job.json'),asdict(job))
    return load_job(path)


def resolve(ctx,paths,root,now=None):
    """Two (or three) accepted codings -> one labelled result under retro_f1/results/<design>/; None while the first two disagree.

    Revalidates every output and its queue lease exactly like the prospective resolve; writes nothing into the design directory."""
    root=_check_root(root,ctx.repo);records=[]
    for p in paths:
        job=load_job(Path(p));v=json.loads(Path(job.promote_path).read_text())
        if validate_output(job,json.loads(Path(job.output_path).read_text()))!=v:raise ValueError('Direction promotion changed')
        records.append(v)
    if len(records)==2 and records[0]['purpose_direction']!=records[1]['purpose_direction']:return None
    if len(records)==3 and records[2]['purpose_direction'] not in (records[0]['purpose_direction'],records[1]['purpose_direction']):
        sign=0  # three-way split: undetermined, never a majority invented
    else:
        sign=records[-1]['purpose_direction']
    target=ctx.card.payload['first_order']['target_outcome']
    value=dict(target_outcome=target,purpose_direction=sign,purpose_direction_evidence=dict(
        schema=C64_SCHEMA,coded_at_stage='S0',target_outcome=target,status='verified',codings=records))
    validate_purpose_direction(value)
    when=(datetime.fromtimestamp(now,timezone.utc) if now is not None else datetime.now(timezone.utc)).isoformat()
    out=dict(value,coding_epoch=EPOCH,design_id=ctx.card.design_id,coded_at=when,
        agreement=dict(coders=len(records),first_two_agree=records[0]['purpose_direction']==records[1]['purpose_direction']),
        note='Secondary sample: coded after an estimate existed; never pooled with prospective codings.')
    ensure_once(root/'results'/ctx.card.design_id/RESULT,out)
    return out
