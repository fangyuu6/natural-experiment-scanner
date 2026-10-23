#!/usr/bin/env python3
"""Production literature-adoption intake, isolated stage progression and audit repair.

Preparation and job emission never dispatch agents or fit outcome associations.
Original imports, jobs and successful S1 provenance remain immutable on resume.
"""
import argparse, json, sys, time, traceback
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from datetime import datetime, timezone
sys.dont_write_bytecode=True
REPO=Path(__file__).resolve().parents[2]
ADAPTER=Path(__file__).resolve()
sys.path.insert(0,str(REPO))
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext, StageResult, Disposition
from scripts.l2_pipeline.storage import artifact, sha, ensure_once, write_once
from scripts.l2_pipeline.cli import run_stage, preflight
from scripts.l2_pipeline.agents.jobs import AgentJob, load_job, _packet, _write_text_once
from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.stages.s1_treatment import audit_sample, evaluate_v3
from scripts.l3_pooled.dta_reader import rows as dta_rows
RULING='HUB_RULING_V5_LITERATURE_TABLES'
RULING_PATH=REPO/'docs/plans/l2-scale/HUB_RULING_V5_LITERATURE_TABLES.md'
# The original metadata is a locked input, not a sibling of the promoted code.
META_ROOT=REPO/'data/l2_pipeline/cards/run2/_meta'
MAP=META_ROOT/'DK_ADOPTION_WINDOW_MAP.json'
MAP_RECEIPT=META_ROOT/'DK_ADOPTION_WINDOW_MAP_RECEIPT.json'
SCOPE_DECISION='A correction falling outside the source coverage window drops that state to a sensitivity (primary excludes it)'
ATTESTATION_ERROR='Missing/contaminated blindness attestation'

def build_map():
 source=REPO/'data/l3_pooled/downloads/DVKim_SPID_iqt.dta'
 pol={}
 for r in dta_rows(source,('policy_num','policy','state','year','adopt_year','adopted')):
  p=pol.setdefault(r['policy'],{'dk_policy_num':r['policy_num'],'policy_id':r['policy'],'states':{},'source_window':{'start_year':r['year'],'end_year':r['year'],'basis':'actual supplied DK state-year rows; absence means convention_zero only within these rows'}})
  if p['dk_policy_num']!=r['policy_num']:raise ValueError('Repeated SPID code with different DK ID')
  y=r['year'];p['source_window']['start_year']=min(p['source_window']['start_year'],y);p['source_window']['end_year']=max(p['source_window']['end_year'],y)
  a=p['states'].setdefault(r['state'],{'start_year':y,'end_year':y,'adopt_year':r['adopt_year'],'row_count':0,'seen_years':set()})
  if a['adopt_year']!=r['adopt_year']:raise ValueError('Published adoption clock changes')
  if r['adopted'] not in (0,1) or r['adopted']!=int(r['adopt_year'] is not None and y>=r['adopt_year']):raise ValueError('Published stock conflicts with absorption')
  if y in a['seen_years']:raise ValueError('Duplicate supplied state-year row')
  a['seen_years'].add(y)
  a['start_year']=min(a['start_year'],y);a['end_year']=max(a['end_year'],y);a['row_count']+=1
 for p in pol.values():
  for a in p['states'].values():
   if a['row_count']!=a['end_year']-a['start_year']+1 or len(a.pop('seen_years'))!=a['row_count']:raise ValueError('Internal source grid gap or duplicate')
  p['universe']=sorted(p['states']);p['adopters_1995_2019']=sum(a['adopt_year'] is not None and 1995<=a['adopt_year']<=2019 for a in p['states'].values());p['treated_count']=sum(a['adopt_year'] is not None for a in p['states'].values())
 if MAP.exists():
  if json.loads(MAP.read_text())!=pol:raise ValueError('Existing DK map differs from published rows')
 else:ensure_once(MAP,pol)
 ensure_once(MAP_RECEIPT,{'source_path':str(source),'source_sha256':sha(source),'map_sha256':sha(MAP),'reader_path':str(REPO/'scripts/l3_pooled/dta_reader.py'),'reader_sha256':sha(REPO/'scripts/l3_pooled/dta_reader.py'),'stock_absorption_checked':True,'state_year_grid_contiguity_checked':True,'outcome_values_read':0,'model_calls':0})
 return pol

def table_map():
 if not MAP_RECEIPT.is_file():return build_map()
 proof=json.loads(MAP_RECEIPT.read_text())
 for k in ('source','reader'):
  if sha(Path(proof[k+'_path']))!=proof[k+'_sha256']:raise ValueError('Cached DK map '+k+' changed')
 if sha(MAP)!=proof['map_sha256']:raise ValueError('Cached DK map changed')
 return json.loads(MAP.read_text())

def source_for(card):
 cfg=card.payload['treatment']
 if cfg.get('source_type')!='literature_adoption_table' or cfg.get('source_format')!='dk_state_year_dta':raise ValueError('This adapter requires literature_adoption_table / dk_state_year_dta; raw SPID event extrema cannot certify a zero window')
 source=next(s for s in card.payload['sources'] if s['source_id']==cfg['complete_list_source_id'])
 path=(card.path.parent/source['path']).resolve()
 if path!=REPO/'data/l3_pooled/downloads/DVKim_SPID_iqt.dta' or sha(path)!=source['sha256']:raise ValueError('DK published table identity/hash changed')
 return cfg,source,path

def import_one(ctx,pol):
 cfg,source,path=source_for(ctx.card);pid=cfg['policy_id'];p=pol[pid]
 if sorted(cfg['universe'])!=p['universe'] or len(set(cfg['universe']))!=len(cfg['universe']):raise ValueError('Universe differs from supplied DK geography')
 if type(cfg['seed']) is not int:raise ValueError('Audit seed must be integer')
 if cfg['source_window']['start_year']!=p['source_window']['start_year'] or cfg['source_window']['end_year']!=p['source_window']['end_year']:raise ValueError('Policy source window differs from actual supplied rows')
 if cfg.get('dk_policy_num',p['dk_policy_num'])!=p['dk_policy_num']:raise ValueError('DK policy identity changed')
 years=ctx.card.payload['outcome']['years']
 if not years or any(type(y)is not int for y in years):raise ValueError('Annual integer outcome window required')
 records=[]
 for state,a in sorted(p['states'].items()):
  if min(years)<a['start_year'] or max(years)>a['end_year']:raise ValueError('Outcome window extends outside supplied state-year coverage')
  ay=a['adopt_year'];status='never_treated' if ay is None else 'treated' if ay<=max(years) else 'not_yet_treated'
  records.append({'unit':state,'status':status,'start_period':ay,'mechanism':ctx.card.mechanism,'locator':f'DVKim_SPID_iqt.dta policy_num={p["dk_policy_num"]}; policy={pid}; state={state}; actual rows {a["start_year"]}-{a["end_year"]}', 'source_status':'convention_zero' if ay is None else 'published_adoption_start','source_window':{'start_year':a['start_year'],'end_year':a['end_year']},'outside_source_window':'unknown'})
 projection=ensure_once(ctx.design_dir/'LITERATURE_SOURCE_PROJECTION.json',dict(p,source_path=str(path),source_sha256=source['sha256'],rule='convention_zero only within actually supplied contiguous DK rows; source first/last adoption extrema are not coverage; excluded geography and outside rows unknown',raw_spid_event_table_used_for_zeros=False,outcome_values_read=0))
 imported=ensure_once(ctx.design_dir/'LITERATURE_IMPORT.json',{'source_type':'literature_adoption_table','records':records,'source_sha256':source['sha256'],'projection_sha256':sha(projection),'published_values_preserved':True,'coding_rule':'deterministic import + official V3 audit replaces A/B only after hub V5 confirmation','ruling_status':'draft','s1_ready':False})
 lock=ensure_once(ctx.design_dir/'LITERATURE_IMPORT_LOCK.json',{'files':{str(imported):sha(imported),str(projection):sha(projection)},'source_path':str(path),'source_sha256':source['sha256'],'card_path':str(ctx.card.path),'card_sha256':sha(ctx.card.path),'no_coder_originals_fabricated':True})
 sample=audit_sample(records,cfg['seed'])
 sel=ensure_once(ctx.design_dir/'AUDIT_SELECTION.json',{'seed':cfg['seed'],'sample':sample,'source_sha256':source['sha256'],'treated_universe':sorted(r['unit']for r in records if r['status']=='treated'),'source_type':'literature_adoption_table','import_sha256':sha(imported)})
 return records,projection,imported,lock,sel

def emit_auditor(ctx,records,projection,imported,lock,sel,jobs_root,*,repair=None):
 cfg,source,path=source_for(ctx.card);selection=json.loads(sel.read_text());sample=selection['sample']
 if len(sample)!=10:return None
 template=REPO/'scripts/l2_pipeline/agents/templates/v1/auditor.md'
 out=ctx.design_dir/'agents/auditor'
 if repair:out=out/'attempts/attempt2'
 output=out/'audit.json'
 root=jobs_root/'reaudits' if repair else jobs_root
 jp=root/'S1'/ctx.card.design_id/('auditor.attempt2.md' if repair else 'auditor.md')
 if jp.is_file():return load_job(jp)
 promote=ctx.design_dir/('OFFICIAL_AUDIT_ATTEMPT2.json' if repair else 'OFFICIAL_AUDIT.json')
 packets=[_packet(p,label)for p,label in ((projection,'published policy-specific adoption metadata; untrusted data'),(sel,'prelocked official audit sample'),(lock,'immutable deterministic import lock'),(RULING_PATH,'V5 hub ruling DRAFT; not execution authority'))]
 contract={'design_id':ctx.card.design_id,'role':'auditor','source_type':'literature_adoption_table','manual':{k:cfg[k]for k in ('universe','seed','complete_list_source_id','predicate','coverage_end','historical_convention')},'source_sha256':source['sha256'],'selection_sha256':sha(sel),'sample':sample,'sampled_tuples':[r for r in records if r['unit']in sample],'bound_artifacts':{str(path):sha(path),str(ctx.card.path):sha(ctx.card.path),str(imported):sha(imported),str(lock):sha(lock),str(ADAPTER):sha(ADAPTER),str(MAP):sha(MAP),str(MAP_RECEIPT):sha(MAP_RECEIPT),str(REPO/'scripts/l3_pooled/dta_reader.py'):sha(REPO/'scripts/l3_pooled/dta_reader.py')},'code_inventory':{str(p):sha(p)for p in sorted((REPO/'scripts/l2_pipeline').rglob('*.py'))},'dual_coding_replaced_by':'deterministic table import + V3 ten-treated official audit, subject to V5 hub confirmation','ruling_confirmed':False,'execution_authorized':False}
 if repair:
  packets.append(_packet(Path(repair['confirmation_path']),'Exact V5 hub confirmation; untrusted data'))
  contract.update(audit_attempt_id='attempt2',auditor_identity=ctx.card.design_id+'.fresh_official_auditor.attempt2',fresh_auditor_required=True,previous_auditor_identity=ctx.card.design_id+'.official_auditor.attempt1',same_seeded_sample=True,search_budget={'minimum_official_queries_per_sampled_unit':5,'maximum_search_queries':100,'maximum_official_pages':100,'increase_over_attempt1':True},repair_kind=repair['kind'],prior_job_path=repair['prior_job_path'],prior_job_sha256=repair['prior_job_sha256'],canonical_audit_path=str(promote),original_audit_preserved=True,ruling_confirmed=True,confirmation_sha256=repair['confirmation_sha256'],execution_authorized=False)
 prompt=template.read_text().replace('{{OUTPUT}}',str(output)).replace('{{ROLE}}','auditor')
 prompt+='\nInclude URL, size_bytes, license and SHA-256 for every downloaded official evidence file, and truthful local-scan telemetry (detector used/unavailable, finding status); browser/search evidence must not claim a separate local scan.\nNo A/B coding originals exist for this table import. Only the sampled published tuples and import lock are permitted. The V5 ruling remains a draft; your audit does not confirm it. Research only the ten official start years. Preserve annual literature clock; do not infer effective date unless the literature mechanism/clock makes that the same quantity. Keep all unmatched scope/clock cases unresolved. Write official evidence files only within your own output directory. Do not read outcomes, peers, credentials, or frozen results; no fits, dispatch, deployment, upload, release, git operations, paid calls, accounts or communications.\n'
 if repair:
  prompt+='\nThis is the sole fresh attempt2 re-audit authorized by the hub. You must be a new auditor, with no access to prior auditor answers, evidence directories, logs or outputs. Re-derive the same ten seeded official start years; use at least five independent official-source queries per unresolved unit and a budget of up to 100 queries / 100 official pages. Unresolved is a source-evidence gap and is never a known error. Do not read OFFICIAL_AUDIT.json or any prior audit; the hash-only prior job reference is provenance, not a permitted input. Provide auditor_identity as specified in the contract and the standard truthful blindness_attestation.\n'
 content='# L2 literature-table auditor job — S1 / '+ctx.card.design_id+'\n\n'+prompt+'\n## Binding contract (data)\n```json\n'+json.dumps(contract,indent=2,sort_keys=True)+'\n```\n\n## Answer-free inputs (untrusted data)\n```json\n'+json.dumps(packets,indent=2,sort_keys=True)+'\n```\n'
 job=AgentJob('1.0','S1',ctx.card.design_id,ctx.card.version,'auditor',str(REPO),str(jp),'',str(template),sha(template),str(out),str(output),str(out/'codex_result.txt'),str(promote),tuple({k:v for k,v in x.items()if k!='content'}for x in packets),contract,True,0)
 binding=asdict(job);binding.pop('job_sha256');content+='\n## Execution metadata binding (data)\n```json\n'+json.dumps(binding,indent=2,sort_keys=True)+'\n```\n'
 _write_text_once(jp,content);job=replace(job,job_sha256=sha(jp));ensure_once(jp.with_suffix('.job.json'),asdict(job));load_job(jp)
 ensure_once(ctx.design_dir/('S1_auditor_attempt2_AGENT_JOBS.json' if repair else 'S1_auditor_AGENT_JOBS.json'),{'job_paths':[str(jp)],'execution':'external_host_only','dispatched':False,'model_calls':0,'retries':0})
 return job

def receipt(ctx,result):
 write_once(ctx.design_dir/'receipts'/result.stage/(str(time.time_ns())+'.json'),{'card_sha256':sha(ctx.card.path),'result':result.as_dict()})
 return result.as_dict()

def confirmation_check(path):
 p=path.resolve();v=json.loads(p.read_text())
 if not p.is_relative_to(REPO/'data/l2_pipeline/jobs/run2'):raise ValueError('Confirmation must be task-owned run2 artifact')
 if v.get('ruling_id')!=RULING or v.get('status')!='CONFIRMED' or v.get('ruling_sha256')!=sha(RULING_PATH) or not all(isinstance(v.get(k),str)and v[k].strip()for k in ('signer','signed_at')):raise ValueError('Missing or invalid hub confirmation of exact V5 draft bytes')
 try:stamp=datetime.fromisoformat(v['signed_at'].replace('Z','+00:00'))
 except ValueError as exc:raise ValueError('Hub confirmation signed_at must be ISO UTC')from exc
 if stamp.tzinfo is None or stamp.utcoffset()!=timezone.utc.utcoffset(stamp):raise ValueError('Hub confirmation signed_at must carry UTC offset')
 return p

def corrected_records(originals, checks, years):
 """Keep out-of-coverage corrections as explicit sensitivity tuples only."""
 corrections={r['unit']:r['corrected_start_period'] for r in checks if r['status']=='known_error'}
 records=[];sensitivities=[]
 for original in originals:
  row=dict(original)
  if row['unit'] in corrections:
   corrected=corrections[row['unit']]
   if type(corrected) is not int or not 1<=corrected<=9999:
    raise ValueError('Invalid annual audit correction; integer year 1..9999 required')
   window=row['source_window']
   row.update(start_period=corrected,status='treated' if corrected<=max(years) else 'not_yet_treated',audit_corrected=True,published_start_period=original['start_period'],correction_source='V3 official audit',known_treated_prewindow=corrected<window['start_year'])
   if not window['start_year']<=corrected<=window['end_year']:
    row.update(primary_excluded=True,sensitivity_only=True,exclusion_reason='Audited correction outside supplied source coverage window',hub_scope_decision=SCOPE_DECISION)
    sensitivities.append(row)
    continue
  records.append(row)
 return records,sensitivities,corrections


def existing_s1(ctx,confirmation):
 """Reuse the valid immutable receipt without rebinding old S1 code provenance."""
 receipts=sorted((ctx.design_dir/'receipts/S1').glob('*.json'))
 if not receipts:return None
 value=json.loads(receipts[-1].read_text())
 if value['result']['disposition']!=Disposition.READY:return None
 preflight(ctx,'S2')
 cp=confirmation_check(confirmation)
 treatment=json.loads((ctx.design_dir/'TREATMENT.json').read_text())
 if treatment.get('confirmation_sha256')!=sha(cp):raise ValueError('Existing S1 confirmation changed')
 return value['result']


def finish(ctx,job,confirmation):
 existing=existing_s1(ctx,confirmation)
 if existing is not None:return existing
 if job is None:
  return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'Ten treated states unavailable; scoped small-universe ruling required'))
 if not Path(job.output_path).is_file():
  return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'Official audit output pending','Dispatch the emitted auditor job externally'))
 # The generic collector enforces output identity, blindness and exact seeded sample.
 if job.contract.get('audit_attempt_id')=='attempt2':
  value=json.loads(Path(job.output_path).read_text())
  if value.get('auditor_identity')!=job.contract['auditor_identity']:raise ValueError('Fresh re-auditor identity missing or changed')
 try:
  collected=collect_job(job.job_path)
 except ValueError as exc:
  if str(exc)==ATTESTATION_ERROR:
   return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,str(exc),'Emit the one fresh attempt2 auditor job'))
  raise
 if not collected['validated']:raise ValueError('Official audit not collected')
 auditfile=Path(job.promote_path);aud=json.loads(auditfile.read_text());audit=evaluate_v3(job.contract['sample'],aud['checks'])
 if not audit['passed']:
  return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'V3 audit unresolved evidence exceeds readiness allowance; unresolved is not error' if audit['known_start_errors']<=2 else 'V3 known start errors exceed allowance','One fresh re-audit with same seeded sample if known errors <=2',metrics=audit))
 cp=confirmation_check(confirmation)
 imported=ctx.design_dir/'LITERATURE_IMPORT.json';source=Path(json.loads((ctx.design_dir/'LITERATURE_IMPORT_LOCK.json').read_text())['source_path'])
 originals=json.loads(imported.read_text())['records']
 analysis_records,sensitivity_records,cor=corrected_records(originals,aud['checks'],ctx.card.payload['outcome']['years'])
 # Published values stay immutable in LITERATURE_IMPORT; V3 corrections are explicitly separated for the L2 analysis tuple.
 treatment=ensure_once(ctx.design_dir/'TREATMENT.json',{'records':analysis_records,'published_import_sha256':sha(imported),'audit':audit,'audit_corrections':cor,**({'scope_sensitivity_records':sensitivity_records,'primary_excluded_units':[r['unit'] for r in sensitivity_records],'hub_scope_decision':SCOPE_DECISION} if sensitivity_records else {}),'analysis_clock_rule':'published annual adoption with V3 known audited errors corrected; published originals retained separately','source_sha256':sha(source),'source_type':'literature_adoption_table','sample':job.contract['sample'],'ruling_sha256':sha(RULING_PATH),'confirmation_sha256':sha(cp)})
 proof=ensure_once(ctx.design_dir/'S1_IMPORT_PROVENANCE.json',{'evidence':[{'path':str(p.relative_to(REPO)),'sha256':sha(p)}for p in (source,ctx.card.path,RULING_PATH,cp,Path(job.output_path),Path(job.job_path),Path(job.job_path).with_suffix('.job.json'),ADAPTER,MAP,MAP_RECEIPT,REPO/'scripts/l3_pooled/dta_reader.py',*[Path(p)for p in job.contract.get('code_inventory',{})])],'source_type':'literature_adoption_table','no_dual_coding_originals':True,'v3_passed':True})
 local=[treatment,proof,imported,ctx.design_dir/'LITERATURE_IMPORT_LOCK.json',ctx.design_dir/'LITERATURE_SOURCE_PROJECTION.json',ctx.design_dir/'AUDIT_SELECTION.json',auditfile]
 return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.READY,'Literature table imported; V3 audit passed; exact V5 draft confirmed',artifacts=tuple(artifact(p,p.stem.lower())for p in local),metrics=audit))

def current_auditor(ctx,jobs_root):
 """Attempt2 is a separate canonical audit; never overwrite an earlier answer."""
 directory=jobs_root/'S1'/ctx.card.design_id
 replacement=jobs_root/'reaudits/S1'/ctx.card.design_id/'auditor.attempt2.md'
 return load_job(replacement if replacement.is_file() else directory/'auditor.md')


def emit_reaudit(ctx,jobs_root,confirmation):
 existing=existing_s1(ctx,confirmation)
 if existing is not None:return existing,0
 prior=load_job(jobs_root/'S1'/ctx.card.design_id/'auditor.md')
 if not Path(prior.output_path).is_file():
  return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'Initial audit output missing; a re-audit is not eligible')),0
 try:
  collected=collect_job(prior.job_path)
 except ValueError as exc:
  if str(exc)!=ATTESTATION_ERROR:raise
  kind='blindness_attestation_repair'
 else:
  if not collected['validated']:raise ValueError('Initial audit not validated')
  value=json.loads(Path(prior.promote_path).read_text())
  audit=evaluate_v3(prior.contract['sample'],value['checks'])
  if audit['passed']:
   return finish(ctx,prior,confirmation),0
  if audit['known_start_errors']>2:
   return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'Known errors exceed two; the single re-audit authorization does not apply',metrics=audit)),0
  kind='unresolved_evidence_reaudit'
 selection=ctx.design_dir/'AUDIT_SELECTION.json'
 if json.loads(selection.read_text())['sample']!=prior.contract['sample'] or sha(selection)!=prior.contract['selection_sha256']:
  raise ValueError('Re-audit must preserve the same seeded sample')
 imported=ctx.design_dir/'LITERATURE_IMPORT.json'
 paths=(ctx.design_dir/'LITERATURE_SOURCE_PROJECTION.json',imported,ctx.design_dir/'LITERATURE_IMPORT_LOCK.json',selection)
 cp=confirmation_check(confirmation)
 repair={'kind':kind,'prior_job_path':prior.job_path,'prior_job_sha256':prior.job_sha256,'confirmation_path':str(cp),'confirmation_sha256':sha(cp)}
 job=emit_auditor(ctx,json.loads(imported.read_text())['records'],*paths,jobs_root,repair=repair)
 return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,'One fresh attempt2 auditor job emitted; prior audit preserved','External hub dispatch then resume the attempt2 audit',artifacts=(artifact(Path(job.job_path),'auditor_attempt2_job'),),metrics={'auditor_jobs':1,'attempt':'attempt2','same_seeded_sample':True,'repair_kind':kind})),1


def process_design(ctx,args,pol=None):
 """Each design has its own disposition; no failed audit aborts its neighbors."""
 jobs=args.jobs_root.resolve()
 if args.action=='prepare':
  s0=run_stage(ctx,'S0')
  if s0.disposition!=Disposition.READY:return s0.as_dict(),0
  records,projection,imported,lock,sel=import_one(ctx,pol)
  job=emit_auditor(ctx,records,projection,imported,lock,sel,jobs)
  reason='Literature import locked; V3 official auditor emitted; hub confirmation pending' if job else 'Literature import locked; fewer than ten treated states; scoped disposition needed'
  return receipt(ctx,StageResult('S1',ctx.card.design_id,Disposition.PARKED,reason,'Collect seeded official audit and confirm exact V5 ruling',tuple(artifact(p,p.stem.lower()) for p in (projection,imported,lock,sel)),{'auditor_jobs':int(job is not None),'s2_s4_permitted':False})),int(job is not None)
 if args.action=='reaudits':return emit_reaudit(ctx,jobs,args.confirmation)
 finished=existing_s1(ctx,args.confirmation)
 if finished is None:finished=finish(ctx,current_auditor(ctx,jobs),args.confirmation)
 if args.action=='resume':return finished,0
 if finished['disposition']!=Disposition.READY:
  return receipt(ctx,StageResult(args.stage,ctx.card.design_id,Disposition.PARKED,'S1 not ready: '+finished['reason'],finished.get('reopen_condition'),metrics=finished.get('metrics',{}))),0
 # New prospective data/analysis declarations retain the immutable source card.
 from scripts.l2_pipeline.run2_preparation import prepare_context
 ctx=prepare_context(ctx)
 result=run_stage(ctx,args.stage)
 binding=ctx.design_dir/'RUN2_PREPARATION.json'
 if binding.is_file():
  result=replace(result,artifacts=(*result.artifacts,artifact(binding,'prospective_preparation')))
  return receipt(ctx,result),0
 return result.as_dict(),0


def process_batch(args):
 """Return a durable batch report, including input and preparation failures."""
 started=time.perf_counter();results=[];jobs_emitted=0
 pol=table_map() if args.action=='prepare' else None
 paths=sorted(args.batch.glob('*.json'))
 for card_path in paths:
  ctx=None;design_id=card_path.stem
  design_started=time.perf_counter()
  try:
   if args.design_id and design_id not in args.design_id:
    # A card filename need not be identical to its design ID.
    raw=json.loads(card_path.read_text())
    if raw.get('design_id') not in args.design_id:continue
   card=load_card(card_path);design_id=card.design_id
   if args.design_id and design_id not in args.design_id:continue
   ctx=StageContext(card,args.work_dir.resolve(),REPO)
   result,count=process_design(ctx,args,pol);jobs_emitted+=count
  except Exception as exc:
   failure=StageResult(args.stage if args.action=='downstream' else 'S1',design_id,Disposition.FAILED,str(exc),'Repair invalid preparation; do not consume official runs or retry fits',metrics={'exception_type':type(exc).__name__,'traceback':traceback.format_exc()})
   result=receipt(ctx,failure) if ctx is not None else failure.as_dict()
  # Timings belong to the batch report, not immutable prior stage receipts.
  result=dict(result,wall_seconds=time.perf_counter()-design_started)
  results.append(result)
 selected={r['design_id'] for r in results}
 missing=set(args.design_id or ())-selected
 for design_id in sorted(missing):
  results.append(StageResult(args.stage if args.action=='downstream' else 'S1',design_id,Disposition.FAILED,'Requested design ID has no card in batch').as_dict())
 return {'source_type':'literature_adoption_table','action':args.action,'stage':args.stage,'cards':len(results),'auditor_jobs':jobs_emitted,'dispositions':dict(Counter(r['disposition'] for r in results)),'dispatched':False,'model_calls':0,'wall_seconds':time.perf_counter()-started,'outcome_values_read':0 if args.action!='downstream' else 'stage dependent after audit and hub confirmation','results':results,'new_downloads':[]}


def main(argv=None):
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--action',choices=('prepare','resume','downstream','reaudits'),default='prepare')
 ap.add_argument('--batch',type=Path,default=REPO/'data/l2_pipeline/cards/run2')
 ap.add_argument('--work-dir',type=Path,default=REPO/'data/l2_pipeline/jobs/run2/work')
 ap.add_argument('--jobs-root',type=Path,default=REPO/'data/l2_pipeline/jobs/run2')
 ap.add_argument('--confirmation',type=Path)
 ap.add_argument('--stage',choices=('S2','S3','S4'))
 ap.add_argument('--design-id',action='append',help='Process only this design; repeat for multiple designs')
 args=ap.parse_args(argv)
 work=args.work_dir.resolve();jobs=args.jobs_root.resolve()
 if not work.is_relative_to(REPO/'data/l2_pipeline/jobs/run2') or not jobs.is_relative_to(REPO/'data/l2_pipeline/jobs/run2'):
  ap.error('Run2 outputs must remain under authorized jobs/run2')
 if args.action!='prepare' and args.confirmation is None:ap.error('Resume/downstream/reaudits require --confirmation')
 if args.action=='downstream' and args.stage is None:ap.error('Downstream requires --stage S2/S3/S4')
 if args.confirmation is not None:confirmation_check(args.confirmation)
 report=process_batch(args)
 target=jobs/(args.action.upper()+'_REPORT_'+str(time.time_ns())+'.json')
 ensure_once(target,report)
 print(json.dumps({k:report[k] for k in ('cards','auditor_jobs','dispositions','wall_seconds','dispatched','model_calls')}|{'report':str(target)},indent=2))
 return 1 if report['dispositions'].get(Disposition.FAILED,0) else 0

if __name__=='__main__':raise SystemExit(main())
