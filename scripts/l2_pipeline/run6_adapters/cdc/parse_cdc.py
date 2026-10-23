"""Run6 CDC adapter: provision-specific stocks; no outcome values are read."""
from pathlib import Path
import sys,json,csv,hashlib,collections,datetime
from scripts.l2_pipeline.policy_tracker_sources import STATES,CDC,CDC_LICENSE
from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT / 'cdc'

SMOKE_ZERO={'No Provision','None','No','Designated Areas','Designated Smoking Area','Designated Smoking Areas','Separate Ventilated Areas'}
def selection(tid,r):
 desc=r['provisiondesc'];pid=r['provisionid']
 if tid.endswith('smokefree') and (desc.startswith('Type of Restriction') or r['measuredesc'].endswith('Other Sites')):
  return 'smokefree_air','ban'
 if tid.endswith('campus') and desc=='Smokefree Campus': return 'smokefree_air','yes'
 if tid=='cdc_tobacco_youth_access' and pid=='203': return 'tobacco_sales_age','age21'
 if tid=='cdc_ecig_youth_access' and pid=='509':return 'tobacco_sales_age','age21'
 if tid.endswith('youth_access') and desc in {'Restriction on Access','Banned from Location','Limited Placement','Locking Device','Supervision'}:
  return 'tobacco_vending_access','yes'
 if tid.endswith('licensure') and desc=='License Required':return 'tobacco_retail_licensing','yes'
 if tid=='cdc_ecig_tax' and pid=='450':return 'e_cigarette_tax','yes'
 if tid.endswith('preemption') and (desc in {'Distribution','Sales to Youth','Vending Machines','Over-the-Counter'} or desc.endswith('Summary Preemption')):
  return 'tobacco_preemption','yes'
 return None

def code(raw,kind):
 if kind=='yes':return 1 if raw=='Yes' else 0 if raw in {'No','No Provision','None'} else None
 if kind=='ban':return 1 if raw=='Banned' else 0 if raw in SMOKE_ZERO else None
 if kind=='age21':return int(float(raw)>=21) if raw.replace('.','',1).isdigit() else 0 if raw=='No Provision' else None

def parse():
 policies=[];catalog=[];future=[]
 receipts=json.loads((ROOT/'FETCH_REPORT.json').read_text())
 for receipt in receipts:
  try:
   tid=receipt['tracker_id'];out=ROOT/'raw'/tid
   if receipt['status']!='downloaded':catalog.append(receipt);continue
   meta=json.loads((out/'metadata.json').read_text());source=out/'history.csv';sha=hashlib.sha256(source.read_bytes()).hexdigest()
   inventories={};rows=collections.defaultdict(list);selected={};periods=set();all_units=set()
   with source.open(newline='') as f:
    for r in csv.DictReader(f):
     year=int(r['year']);quarter=int(r['quarter']);key=r['measureid']+'_'+r['provisionid']
     inv=inventories.setdefault(key,dict(measure=r['measuredesc'],provision=r['provisiondesc'],values=collections.Counter(),periods=set(),units=set(),cells=set(),duplicate_cells=0))
     inv['values'][r['provisionvalue']]+=1;inv['periods'].add((year,quarter));inv['units'].add(r['locationabbr']);cell=(r['locationabbr'],year,quarter)
     if cell in inv['cells']:inv['duplicate_cells']+=1
     inv['cells'].add(cell)
     # Future scheduled step is retained in raw and quarantined from observed stock history.
     if year>2025:
      future.append(dict(tracker_id=tid,record=r,reason='Future scheduled row outside declared 1995-2025 history'));continue
     periods.add((year,quarter));all_units.add(r['locationabbr'])
     chosen=selection(tid,r)
     if not chosen or r['locationabbr'] not in STATES:continue
     family,kind=chosen;selected[key]=(family,kind,r['measuredesc'],r['provisiondesc'])
     rows[key].append(dict(unit=r['locationabbr'],year=year,quarter=quarter,active=code(r['provisionvalue'],kind),raw_value=r['provisionvalue'],numeric_value=r['provisionaltvalue'],measure=r['measuredesc'],provision=r['provisiondesc'],effective_date=r['effective_date'],enacted_date=r['enacted_date'],citation=r['citation'],comments=r['comments'],locator=f"{r['measureid']}/{r['provisionid']}/{r['locationabbr']}/{year}Q{quarter}"))
   inventory_out=[]
   for key,inv in sorted(inventories.items()):
    inv.pop('cells');inv['values']=dict(inv['values']);inv['periods']=[f'{y}Q{q}' for y,q in sorted(inv['periods'])];inv['units']=sorted(inv['units']);inventory_out.append(dict(policy_key=key,**inv))
   (out/'provision_inventory.json').write_text(json.dumps(inventory_out,indent=2)+'\n')
   provision_summaries=[]
   for key,rs in sorted(rows.items()):
    family,kind,measure,desc=selected[key];states=sorted(STATES);cells=collections.Counter((r['unit'],r['year'],r['quarter']) for r in rs)
    complete_years=[y for y in range(1995,2026) if all(cells[(s,y,q)]==1 for s in states for q in range(1,5))]
    start=min(complete_years) if complete_years else min(r['year'] for r in rs);end=max(complete_years) if complete_years else max(r['year'] for r in rs)
    missing=[dict(unit=s,year=y,quarter=q) for s in states for y in range(start,end+1) for q in range(1,5) if cells[(s,y,q)]!=1]
    unknown=[r['locator'] for r in rs if start<=r['year']<=end and r['active'] is None]
    source_complete=not missing and not unknown and bool(complete_years)
    definition=measure+': '+desc+(' = Banned (complete prohibition)' if kind=='ban' else ' >= 21 years (state law only)' if kind=='age21' else ' = Yes')
    ecig=tid.startswith('cdc_ecig');preempt=family=='tobacco_preemption';campus=tid.endswith('campus');youth=family in {'tobacco_sales_age','tobacco_vending_access'}
    target='local_tobacco_policy_adoption' if preempt else 'current_vaping_pct' if ecig else 'current_smoking_pct'
    outcome=dict(connector='brfss_state_prevalence',measure_id=target,target_outcome=target,years=[1995,2025],relation='direct',population_description='Published adult BRFSS smoking population; policy-specific target match requires verification',measurement_url='https://www.cdc.gov/brfss/',evidence_locator='STATE policy construct; policy-specific purpose review pending')
    if ecig:outcome['target_gap']='Current connector catalog has no current vaping prevalence measure; cigarette smoking is not substituted.'
    if preempt:outcome['target_gap']='Immediate state-local legal-authority outcome is unavailable in existing health connectors; smoking is downstream.'
    if campus or youth:outcome.update(target_population_gap=True,target_population_description='Students on regulated campuses' if campus else 'Youth / ages18-20 for state Tobacco-21',population_description='Published adult BRFSS population; exact legal target subgroup not certified')
    policy=dict(existing_design_source_supplement_only=(family=='smokefree_air' and not ecig),duplicate_existing_first_order=(family=='smokefree_air' and not ecig),existing_design_reference='data/did_samples/smoke_free_air/case.json' if family=='smokefree_air' and not ecig else None,tracker_id=tid,policy_id='cdc_'+key+'_'+kind,policy_family=family,shared_legal_family=family,domain='Health',title=measure+': '+desc,definition=definition,source_url='https://data.cdc.gov/resource/'+CDC[tid]+'.csv',source_path=str(source.resolve()),sha256=sha,size_bytes=source.stat().st_size,license=CDC_LICENSE,terms_url='https://data.cdc.gov/',as_of='2025-Q2',metadata_rows_updated_at=datetime.datetime.fromtimestamp(meta['rowsUpdatedAt'],datetime.timezone.utc).isoformat(),source_description=meta['description'],coverage=dict(start_year=start,end_year=end),raw_source_coverage=dict(start_year=1995,end_year=2025,observed_periods=[f'{y}Q{q}' for y,q in sorted(periods)]),history_complete=source_complete,history_completeness_rule='All 51 states/DC observed exactly once in each of four quarters for every declared year, with explicit provision-specific binary coding; daily legal exposure requires official audit',quarterly_stock_complete=source_complete,daily_full_year_certified=False,repeals_included=True,repeal_scope='All observed stock reversals retained; original effective/enacted dates and citations retained; dates and exceptions need V3 official audit',states=states,quarterly_rows=rs,events=[],numeric_coding_requires_definition=bool(unknown),coding_definition=dict(kind=kind,negative_labels=sorted(SMOKE_ZERO) if kind=='ban' else ['No','No Provision','None'],unknown_handling='Retained null; never interpolated'),outcome=outcome,source_check=dict(authority='untrusted_data',separate_local_scan_performed=True,ml_scanner='not run',method='NFKC/zero-width/Base64/ROT13 normalized heuristic scan before parsing; full receipts include results',clean_check_not_proof_of_safety=True),raw_source_paths=[d['path'] for d in receipt['downloads']],missing_quarter_cells=missing,unknown_coded_locators=unknown)
    policies.append(policy);provision_summaries.append(dict(policy_id=policy['policy_id'],family=family,coverage=policy['coverage'],history_complete=source_complete,missing_cells=len(missing),unknown_cells=len(unknown),rows=len(rs)))
   catalog.append(dict(tracker_id=tid,status='downloaded',source_url='https://data.cdc.gov/resource/'+CDC[tid]+'.csv',metadata_url='https://data.cdc.gov/api/views/'+CDC[tid]+'.json',license=CDC_LICENSE,metadata_license_id=meta.get('licenseId'),source_description=meta['description'],metadata_rows_updated_at=datetime.datetime.fromtimestamp(meta['rowsUpdatedAt'],datetime.timezone.utc).isoformat(),latest_observed_stock='2025-Q2',verified_download_rows=receipt['verified_rows'],paged_downloads=receipt['paged_downloads'],all_jurisdictions=sorted(all_units),analysis_universe=sorted(STATES),period_inventory=[f'{y}Q{q}' for y,q in sorted(periods)],pre_2006_rule='Only Q4 stocks observed; Q1-Q3 remain unknown, no carried-forward interpolation',partial_2025_rule='Only Q1/Q2 stocks observed; exclude incomplete full year',source_completeness_verdict='Row count exactly matched counts before and after downloads; provision coverage assessed separately; legal completeness ungranted',repeal_handling='Every decrease or binary reversal is retained; missing cells never interpreted as nonadoption',provisions=provision_summaries,source_path=str(source.resolve()),source_sha256=sha,source_size_bytes=source.stat().st_size,download_count=len(receipt['downloads'])))
  except Exception as exc:
   catalog.append(dict(tracker_id=receipt.get("tracker_id"),status="stopped",error=str(exc)))
 (ROOT/'POLICIES.json').write_text(json.dumps(policies,indent=2)+'\n')
 (ROOT/'SOURCE_CATALOG.json').write_text(json.dumps(catalog,indent=2)+'\n')
 (ROOT/'SCHEDULED_FUTURE_ROWS.json').write_text(json.dumps(future,indent=2)+'\n')
 failures=[r for r in catalog if r.get('status')=='stopped']
 print('POLICIES',len(policies),'FAMILIES',dict(collections.Counter(p['policy_family'] for p in policies)),flush=True)
 return failures
def main(argv=None):
 import argparse
 global ROOT
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--data-root',type=Path,default=ROOT)
 args=parser.parse_args(argv)
 ROOT=args.data_root.resolve()
 failures=parse()
 return int(bool(failures))

if __name__=='__main__':
 raise SystemExit(main())
