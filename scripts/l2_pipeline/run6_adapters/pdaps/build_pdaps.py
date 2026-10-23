"""Run6 selective public-viewer adapter; extracts JSON data without JS execution.
Only named family predicates are coded. No missing cell or prior year is zero.
"""
from pathlib import Path
import json, hashlib
from collections import Counter
from scripts.l2_pipeline.policy_tracker_sources import extract_pdaps_viewer, _pdaps_date, _binary, annualize_intervals, PDAPS_LICENSE, PDAPS_TERMS, STATES
from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT / 'pdaps'
CONFIG=[
 ('pdmp-mandates','pdmp_use_mandate','PDMP_check',[2014,2019],'run1_pdmp_mandatory_query'),
 ('pain-management-clinic-laws','pain_clinic_regulation','PMC_Law',[2006,2017],None),
 ('opioid-analgesics-prescribing-limits','opioid_prescribing_limits','opio_',[2014,2019],'run1_opioid_acute_cap'),
 ('medical-marijuana-patient-related-laws-1501600783','medical_cannabis','mmlaw',[2014,2016],'legacy_medical_cannabis_fo'),
 ('recreational-marijuana-laws','recreational_cannabis','rm-rmlaw',[2015,2016],None),
 ('informed-consent-for-opioid-prescribing-laws','opioid_informed_consent','summary_requirement',[2014,2019],None),
 ('opioid-prescribing-guidelines-for-acute-and-emergency-care','opioid_prescribing_guidelines','OPG_Law',[2006,2016],None),
 ('medication-assisted-treatment-coverage-1580241551','moud_insurance_coverage','com_MAT_req',[2018,2019],None),
 ('policies-regulating-prior-authorization-requirements-for-medications-for-op','moud_prior_authorization_restrictions','PAMOUD_law',[2017,2022],None),
 ('syringe-services-programs-laws','syringe_services_authorization','ssp_does state allow ',[2020,2020],'run2_syringe_exchange_authorization'),
]

def code(v,mode):
 if mode=='summary_requirement':
  if v=='No': return 0
  if isinstance(v,str) and v.strip() in {'Yes, for adults only','Yes, for minors only','Yes, for both adults and minors'}:return 1
  return None
 return _binary(v)

def build_policy(config):
 slug,family,note,scope,duplicate=config
 path=ROOT/'raw'/(slug+'.html'); raw=path.read_bytes(); data=extract_pdaps_viewer(raw.decode()); snap=data['snapshot']; qs=snap['questions_answers']
 if note=='PDMP_check':q=next(q for q in qs if q.get('note')=='PDMP_any')
 else:q=next(q for q in qs if q.get('note')==note)
 qid=q['id']; rootid=qs[0]['id']; parents={child:p['id'] for p in qs for child in (p.get('children')or[])}
 for r in snap['mappable_data']:
  if r.get('state_code')=='District of Columbia':r['state_code']='DC'
 rs=[r for r in snap['mappable_data'] if r.get('state_code') in STATES and not r.get('_deleted') and not r.get('_hidden')]
 intervals=[]
 for r in rs:
  ans=r.get('answers',r); v=ans.get(qid); active=code(v,note); basis='explicit binary/categorical root answer'
  if active is None and note!='summary_requirement' and _binary(ans.get(rootid))==0:active=0; basis='explicit no parent law'
  if active is None and qid in parents and _binary(ans.get(parents[qid]))==0:active=0;basis='explicit no parent provision'
  intervals.append(dict(unit=r['state_code'],start_date=_pdaps_date(r['effective']).isoformat(),end_date=_pdaps_date(r['through']).isoformat(),end_inclusive=True,active=active,raw_value=v,coding_basis=basis,locator='snapshot.mappable_data/'+str(r.get('record_id',r['id']))+'/answers/'+qid,citation=[{k:c[k] for k in ('id','path','url','title') if k in c} for c in r.get('citations',{}).get(qid,[])],law_ids=r.get('laws',[]),caution=r.get('cautions',{}).get(qid,'')))
 annual=annualize_intervals(intervals,*scope)
 # Explicitly add missing jurisdiction years as unknown; never create legal zero.
 keys={(r['unit'],r['year']) for r in annual}
 for u in sorted(STATES):
  for y in range(scope[0],scope[1]+1):
   if (u,y) not in keys:annual.append(dict(unit=u,year=y,active=None,transition_year=False,missing_days=366 if y%4==0 else 365,unknown_days=0,conflict_days=0,locator='snapshot.mappable_data/no_observed_record'))
 for r in annual:
  if not r.get('locator'):r['locator']='snapshot.mappable_data/no_observed_interval/'+r['unit']+'/'+str(r['year'])
 annual.sort(key=lambda r:(r['unit'],r['year']))
 complete=all(not(r['missing_days']or r['unknown_days']or r['conflict_days']) for r in annual)
 bounds=dict(start_date=min(r['start_date'] for r in intervals),end_date=max(r['end_date'] for r in intervals))
 declared=_pdaps_date(data['valid_from']).isoformat() if data.get('valid_from') else None
 issues=[]
 if declared and declared>_pdaps_date(data['updated_through']).isoformat():issues.append('declared_valid_from_after_as_of; conservative descriptor window used, requires official/source clarification')
 if not declared:issues.append('viewer lacks declared_valid_from; whole-year scope uses observed intervals only')
 if slug=='opioid-prescribing-guidelines-for-acute-and-emergency-care':issues.append('51 single-state records with variable start dates; no certified no-law histories before those dates')
 if duplicate:issues.append('existing L2 family/design; use as source upgrade only and verify exact predicate equivalence')
 targets={
  'pdmp_use_mandate':('PDMP query compliance / opioid dispensing','pdmp_query_compliance'),
  'pain_clinic_regulation':('Pain-clinic prescribing / operation compliance','pain_clinic_prescribing'),
  'opioid_prescribing_limits':('Opioid prescription duration / quantity','opioid_prescription_quantity'),
  'medical_cannabis':('Medically authorized patient access / clinical symptoms','medical_cannabis_patient_access'),
  'recreational_cannabis':('Legal cannabis access / cannabis consumption','cannabis_consumption'),
  'opioid_informed_consent':('Informed consent before opioid prescribing','opioid_informed_consent_compliance'),
  'opioid_prescribing_guidelines':('Opioid prescribing practice / guideline adherence','opioid_prescribing_practice'),
  'moud_insurance_coverage':('Coverage and utilization of MOUD by insured patients','moud_covered_treatment_utilization'),
  'moud_prior_authorization_restrictions':('MOUD prior authorization barriers / treatment access','moud_treatment_access'),
  'syringe_services_authorization':('Sterile syringe access / HIV or HCV infection','sterile_syringe_access'),
 }
 target,measure=targets[family]
 outcome=dict(connector='unavailable',measure_id=measure,years=scope,population_description='Policy-specific target population requires statutory verification',target_outcome=target,relation='direct',target_gap='No current connector measures the specified first-order target; source lead parked',target_population_gap=True,measurement_url='https://pdaps.org/datasets/'+slug)
 outcome_map=dict(first_order_target=target,target_purpose_evidence='tracker question defines exposure; statutory purpose evidence pending',available_connectors_for_actual_target=[],connector_unavailable=True,readiness_granted=False,
  downstream_candidate=dict(connector='nchs',measure_id='drug_overdose_death_rate',coverage=[1999,2017],purpose='Potential downstream overdose mortality; not established first-order target',may_substitute_primary=False) if 'cannabis' not in family and family!='syringe_services_authorization' else None,
  cannabis_drinking_primary_permitted=False if 'cannabis' in family else None)
 policy=dict(tracker_id='pdaps_'+family,policy_id='run6_'+family,policy_family=family,shared_legal_family=family,domain='health_drug_policy',title=q['question'].strip(),definition=q['question'].strip(),question_id=qid,source_url='https://pdaps.org/datasets/'+slug,source_path=str(path),sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw),license=PDAPS_LICENSE,terms_url=PDAPS_TERMS,as_of=_pdaps_date(data['updated_through']).isoformat(),declared_valid_from=declared,coverage=dict(start_year=scope[0],end_year=scope[1],**bounds),states=sorted(STATES),interval_rows=intervals,annual_rows=annual,events=[],history_complete=complete,history_completeness_rule='Every calendar day covered by explicit nonconflicting root predicate; complete only inside declared conservative scope',repeals_included=True,repeal_scope='Every observed 1-to-0 reversal retained; no repeal claim outside window',source_attribution=data.get('created_by'),source_check=json.loads(path.with_suffix('.html.scan.json').read_text()),outcome=outcome,outcome_map=outcome_map,existing_design_id=duplicate,count_as_new_family=not bool(duplicate),issues=issues,daily_full_year_certified=True)
 return policy, {k:policy[k] for k in ('tracker_id','policy_id','policy_family','source_url','source_path','sha256','size_bytes','license','as_of','declared_valid_from','coverage','definition','question_id','states','history_complete','history_completeness_rule','repeals_included','repeal_scope','source_attribution','existing_design_id','count_as_new_family','issues','outcome_map') } | dict(completeness_verdict='complete_scoped_tracker_history_pending_official_V3' if complete else 'incomplete_scoped_history; gaps remain unknown',annual_cells=len(annual),unknown_days=sum(r['unknown_days'] for r in annual),missing_days=sum(r['missing_days'] for r in annual),conflict_days=sum(r['conflict_days'] for r in annual),transition_years=sum(r['transition_year'] for r in annual))


def replay():
 all_policies=[]; catalog=[]; failures=[]
 for config in CONFIG:
  try:
   policy, source=build_policy(config)
   all_policies.append(policy); catalog.append(source)
  except Exception as exc:
   failures.append(dict(policy_family=config[1],status="stopped",error=str(exc)))
 (ROOT/'POLICIES.json').write_text(json.dumps({'policies':all_policies},indent=2)+'\n')
 (ROOT/'SOURCE_CATALOG.json').write_text(json.dumps({'sources':catalog,'ESAS':{'status':'unresolved_dataset_identifier','searched_terms':['LawAtlas ESAS','LawAtlas opioid longitudinal','LawAtlas emerging opioid ESAS'],'verdict':'No exact public dataset identified by this acronym; opioid prescribing limits separately acquired. No fabricated mapping.'},'outcome_values_read':0,'outcome_association_calls':0,'failures':failures},indent=2)+'\n')
 print(json.dumps([{k:r[k] for k in ('policy_family','history_complete','coverage','completeness_verdict','existing_design_id','missing_days','unknown_days','conflict_days')} for r in catalog],indent=2))

 return failures

def main(argv=None):
 import argparse
 global ROOT
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--data-root",type=Path,default=ROOT)
 args=parser.parse_args(argv)
 ROOT=args.data_root.resolve()
 failures=replay()
 return int(bool(failures))


if __name__=="__main__":
 raise SystemExit(main())
