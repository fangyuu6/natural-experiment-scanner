"""Independent S0 direction coding, no numerical or literature-sign inputs."""
from pathlib import Path
from dataclasses import asdict,replace
import copy,json
from ..storage import ensure_once,sha,output_root
from .jobs import AgentJob,_write_text_once,load_job
from .purpose import C64_SCHEMA,validate_purpose_direction
KIND='s0_purpose_direction_c83'
ROLES=('purpose_direction_A','purpose_direction_B','purpose_direction_third')
TEMPLATE=Path(__file__).parent/'templates/v6/purpose_direction_c83.md'

def refs(ctx):
    source=ctx.design_dir/'PURPOSE_VERIFICATION.json'
    first=ctx.card.payload.get('first_order',{})
    records=json.loads(source.read_text()).get('citations',[]) if source.exists() else []
    records += [dict(r,source_type='statute') for r in first.get('statute_refs',[])]
    # Only locator metadata, never confirmation prose or paper estimates.
    found={}
    for r in records:
        if r.get('source_ref') and r.get('locator') and ('target_outcome' in r.get('supports',[]) or r.get('source_type')=='statute'):
            found[r['source_ref'],r['locator']]={k:r[k] for k in ('source_ref','locator','source_type') if k in r}
    return list(found.values())

def emit(ctx,role,root):
    if role not in ROLES:raise ValueError('Invalid direction role')
    from ..f1_coding import f1_protected as protected
    if protected(ctx.design_dir):raise ValueError('No new direction coding for protected designs')
    root=output_root(Path(root),ctx.repo);path=root/'S0'/ctx.card.design_id/(role+'.md')
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

def validate_job(job):
    c=job.contract
    if job.stage!='S0' or job.role not in ROLES or c.get('jobs_kind')!=KIND or set(c)!={'design_id','role','jobs_kind','target_outcome','citations','permitted_source_paths','execution_authorized'}:
        raise ValueError('Direction packet contains non-blind fields')
    if job.inputs or c['permitted_source_paths'] or c['execution_authorized'] is not False:raise ValueError('Direction packet read scope differs')
    if any(set(r)-{'source_ref','locator','source_type'} for r in c['citations']):raise ValueError('Direction citation contains extra content')

def validate_output(job,value):
    from .sonnet_queue import locate,root_for,identity,read
    if any(value.get(k)!=v for k,v in [('design_id',job.design_id),('role',job.role),('target_outcome',job.contract['target_outcome'])]):raise ValueError('Direction identity/target differs')
    packet=locate(root_for(job.repo),identity(job.job_path))
    if packet and (packet/'LEASE.json').exists() and value.get('coder_id')!=read(packet/'LEASE.json')['worker']:raise ValueError('Direction coder differs from queue lease')
    if value.get('blindness_attestation',{}).get('other_codings_read') is not False:raise ValueError('Every direction coder must remain peer blind')
    record={k:copy.deepcopy(value.get(k)) for k in ('coder_id','recorded_at','purpose_direction','citations','blindness_attestation')}
    record.update(role='A',original_sha256=sha(job.output_path))
    peer=copy.deepcopy(record);peer.update(role='B',coder_id=str(record['coder_id'])+'-validation-peer',original_sha256='f'*64 if record['original_sha256']!='f'*64 else 'e'*64)
    sign=value.get('purpose_direction');target=job.contract['target_outcome']
    evidence=dict(schema=C64_SCHEMA,coded_at_stage='S0',target_outcome=target,status='verified',codings=[record,peer])
    citations=[dict(r,supports=['target_outcome']) for r in job.contract['citations']]
    validate_purpose_direction(dict(target_outcome=target,purpose_direction=sign,purpose_direction_evidence=evidence),citations)
    record['role']=job.role.removeprefix('purpose_direction_')
    return record

def resolve(ctx,paths):
    records=[]
    for p in paths:
        job=load_job(Path(p));v=json.loads(Path(job.promote_path).read_text())
        # Revalidate exact output and lease every time; promote cannot substitute authors.
        if validate_output(job,json.loads(Path(job.output_path).read_text()))!=v:raise ValueError('Direction promotion changed')
        records.append(v)
    if len(records)==2 and records[0]['purpose_direction']!=records[1]['purpose_direction']:return None
    target=ctx.card.payload['first_order']['target_outcome']
    value=dict(target_outcome=target,purpose_direction=records[-1]['purpose_direction'],purpose_direction_evidence=dict(
        schema=C64_SCHEMA,coded_at_stage='S0',target_outcome=target,status='verified',codings=records))
    validate_purpose_direction(value)
    ensure_once(ctx.design_dir/'PURPOSE_DIRECTION_C83.json',value)
    from ..f1_coding import stamp
    stamp(ctx.design_dir)
    return value

def _action(pilot,row):
    ctx=pilot.backend.ctx(row);root=pilot.case_dir(row)/'purpose_direction_jobs'
    from ..f1_coding import f1_protected as protected
    if protected(ctx.design_dir):return dict(status='fields unavailable',reason='Protected design remains unchanged')
    if not refs(ctx):return missing(ctx,'no_public_citation')
    jobs=[emit(ctx,r,root).job_path for r in ROLES[:2]]
    if pilot.collect_submissions(row,'purpose-direction',jobs) is None:return None
    value=resolve(ctx,jobs)
    if value is None:
        from .. import rules as rules_module
        rules=rules_module.of_payload(ctx.card.payload,ctx.repo)
        if rules_module.at_least(rules,2) and rules['direction']['third_coder'] is False:
            return missing(ctx,'coders_disagree')   # REDESIGN R2: no third coder; the direction stays an explicit gap
        jobs.append(emit(ctx,ROLES[2],root).job_path)
        if pilot.collect_submissions(row,'purpose-direction',jobs[-1:]) is None:return None
        value=resolve(ctx,jobs)
    return dict(jobs=jobs,purpose_direction=value['purpose_direction'])


def missing(ctx,reason,detail=None):
    """Exploratory F1 absence is explicit; never infer a zero direction."""
    target=ctx.card.payload['first_order']['target_outcome']
    evidence=dict(schema=C64_SCHEMA,coded_at_stage='S0',target_outcome=target,
        status='pending_blind_coding',coverage_code='FIELD_MISSING',reason=reason)
    if detail:evidence['detail']=detail
    value=dict(target_outcome=target,purpose_direction=None,purpose_direction_evidence=evidence)
    ensure_once(ctx.design_dir/'PURPOSE_DIRECTION_C83.json',value)
    return dict(field_status={'F1':'FIELD_MISSING'},reason=reason,purpose_direction=None)


def action(pilot,row):
    ctx=pilot.backend.ctx(row)
    from ..f1_coding import f1_protected as protected
    if protected(ctx.design_dir):return dict(status='fields unavailable',reason='Protected design remains unchanged')
    path=ctx.design_dir/'PURPOSE_DIRECTION_C83.json'
    if path.exists():
        value=json.loads(path.read_text());evidence=validate_purpose_direction(value)
        if evidence.get('coverage_code')=='FIELD_MISSING':
            return dict(field_status={'F1':'FIELD_MISSING'},reason=evidence['reason'],purpose_direction=None)
        from ..f1_coding import stamp
        stamp(ctx.design_dir)
        return dict(purpose_direction=value['purpose_direction'])
    try:return _action(pilot,row)
    except Exception as exc:
        # EX-1: failed exploratory coding is retained as missing, never an L2 STOP.
        # Drop only this design's unsent F1 work; retained packets stay immutable.
        pilot.pending[:]=[(r,p) for r,p in pilot.pending
            if not (r==row and Path(p).is_relative_to(pilot.case_dir(row)/'purpose_direction_jobs'))]
        return missing(ctx,'blind_coding_unavailable',type(exc).__name__+': '+str(exc))


def is_direction(path):
    """Identify retained transport packets without reading another coder's answer."""
    meta=json.loads(Path(path).with_suffix('.job.json').read_text())
    return meta.get('contract',{}).get('jobs_kind')==KIND


def transport_failure(pilot,row,path,reason):
    if not is_direction(path):
        from ..redesign import label_transport_failure
        return label_transport_failure(pilot,row,path,reason)
    from ..f1_coding import f1_protected as protected
    ctx=pilot.backend.ctx(row)
    if not protected(ctx.design_dir) and pilot.done(row,'purpose-direction') is None:
        value=(action(pilot,row) if (ctx.design_dir/'PURPOSE_DIRECTION_C83.json').exists()
            else missing(ctx,'blind_coding_unavailable',reason))
        ensure_once(pilot.checkpoint(row,'purpose-direction'),value)
        pilot.ledger(row,'purpose-direction','FIELD_MISSING','record',reason=reason)
    return True
