#!/usr/bin/env python3
"""Deterministic design-specific screen; no network, LLM, or estimation calls."""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

csv.field_size_limit(2**31 - 1)
ROOT = Path(__file__).resolve().parents[3]
# Import only the deterministic registry file; package __init__ imports API clients.
_spec=importlib.util.spec_from_file_location('queue_dataset_registry',ROOT/'src/extraction/dataset_registry.py')
_registry=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_registry)
classify_datasets=_registry.classify_datasets
STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())
STATE_NAMES = dict(zip('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'.lower().split('|'), 'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))
NUMERIC_RV = re.compile(r'age|income|earnings|wage|salary|revenue|sales|receipts|assets|loan|amount|employees|employee_count|workers|firm.?size|employment|population|enrollment|students|score|rank|percent|ratio|poverty|hours|months|years|tenure|days|weight|capacity|acre|distance|emissions|production|volume|height|size|cost|value|balance|credit|debt|gpa|mileage|birth|date|duration', re.I)
RANDOM = re.compile(r'lotter(?:y|ies)|random(?:ly)?\s+(?:assign\w*|allocat\w*|select\w*|draw\w*)|randomiz(?:ed|ation)|randomis(?:ed|ation)', re.I)
GAMBLING = re.compile(r'(?:state|gaming|gambling|lottery)\s+lottery|lottery\s+(?:tickets?|proceeds|commission|corporation|retailers?)|lottery.*(?:gambling|jackpot)|gaming.*lottery',re.I)

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(8*1024*1024),b''): h.update(block)
    return h.hexdigest()

def stable_id(track, key): return f'{track.lower()}-{digest([track,key])[:20]}'

def ranking_key(d):
    public=any(classify_datasets(s.get('suggested_datasets',''))['public'] for s in d.get('source_records',[]))
    return (-int(d['domain'] in ('labor_market','taxation_fiscal','appropriations_budget')),-int(public),-d.get('evidence_strength',0),-len(d.get('states',[])),d['design_id'])

def safe_url(value):
    """Exports public location only; legacy URLs contain embedded query secrets."""
    try:
        u=urlsplit(str(value or ''))
        if u.scheme not in ('http','https') or u.username or u.password: return ''
        allowed={'id','i','b','s','n','h','l','sn','pn','year','bill','billid','bill_id','billnumber','billtype','bill_number','bill_type','number','type','doc','docname','docnum','leg','session','sessionyear','sessionname','sa','sln','nav','file','filename','bs','ses','page'}
        query=urlencode([(k,v) for k,v in parse_qsl(u.query) if k.lower() in allowed and len(v)<160])
        return urlunsplit((u.scheme,u.netloc,u.path,query,''))
    except ValueError: return ''

def sanitize(value):
    if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items()}
    if isinstance(value,list):return [sanitize(v) for v in value]
    if isinstance(value,str):return re.sub(r'https?://[^\s<>"\]\)]+',lambda m:safe_url(m[0]),value,flags=re.I)
    return value

def flag(value):return value in ('1',1,True,'true')

def list_of(value):
    if isinstance(value,list): return value
    if isinstance(value,str):
        try:
            out=json.loads(value)
            return out if isinstance(out,list) else []
        except (ValueError,TypeError): return []
    return []

def family_of(value):
    return re.sub(r'[^a-z0-9_]+','_',str(value or '').lower().replace(' ','_')).strip('_').replace('marijuana','cannabis').replace('childcare','child_care')

def state_of(jurisdiction):
    x=str(jurisdiction or '').removesuffix(' State').strip()
    return x if x in STATES else STATE_NAMES.get(x.lower(),'')

def year_of(row):
    for val in (row.get('year'),row.get('date')):
        m=re.match(r'^(19\d{2}|20\d{2})',str(val or ''))
        if m:return int(m[1])
    return None

def thresholds(detail):
    value=detail.get('all_rd_thresholds')
    if isinstance(value,str):
        try:value=json.loads(value)
        except ValueError:value=[]
    result=[x for x in ([value] if isinstance(value,dict) else value or []) if isinstance(x,dict)]
    info=detail.get('rd_info','')
    if info:
        fields={k.strip().lower():v.strip() for k,v in (part.split(':',1) for part in str(info).split('|') if ':' in part)}
        result.append({'running_variable':fields.get('variable',''),'cutoff':fields.get('cutoff',''),'quote':detail.get('rd_quote',''),'rd_type':detail.get('rd_type',''),'threshold_structure':detail.get('threshold_structure',''),'direction':fields.get('direction','')})
    unique={}
    for x in result:
        key=(str(x.get('running_variable','')).lower().strip(),str(x.get('cutoff','')).strip())
        if key not in unique or len(str(x.get('quote','')))>len(str(unique[key].get('quote',''))):unique[key]=x
    return list(unique.values())

def measurable_threshold(x):
    rv=str(x.get('running_variable','')).strip()
    cutoff=str(x.get('cutoff','')).strip()
    missing={'none','null','unknown','n/a','na','unclear','not specified','not available'}
    return bool(rv.lower() not in missing and cutoff.lower() not in missing and rv and NUMERIC_RV.search(rv) and re.search(r'\d',cutoff))

def evidence(detail):
    arr=[x for x in list_of(detail.get('all_did_evidences')) if isinstance(x,dict)]
    old=detail.get('design_evidence_did')
    if isinstance(old,str):
        try:old=json.loads(old)
        except ValueError:old=None
    if isinstance(old,dict):arr.append(old)
    if not arr:
        arr=[{'quote':detail.get('did_quote',''),'treatment_group':detail.get('treatment',detail.get('treatment_description','')),'control_group':detail.get('control_group',''),'timing':detail.get('treatment_timing','')}]
    return arr

def lexical_evidence(row,detail):
    """Quote/title lane plus explicitly weaker extraction-narrative lane."""
    fields=[('title',row.get('title','')),('did_quote',detail.get('did_quote','')),('rd_quote',detail.get('rd_quote','')),('quote_snippet',detail.get('quote_snippet',''))]
    fields += [('all_did_evidences.quote',x.get('quote','')) for x in evidence(detail)]
    fields += [('all_rd_thresholds.quote',x.get('quote','')) for x in thresholds(detail)]
    narrative=('intuition_pitch','treatment_description','treatment','control_group','treatment_timing','did_reasoning','rd_reasoning')
    fields += [(k,detail.get(k,'')) for k in narrative]
    fields += [('intuition_pitch',row.get('intuition_pitch',''))]
    hits=[]
    for field,value in fields:
        value=str(value or '')
        m=RANDOM.search(value)
        if m:
            snippet=value[max(0,m.start()-180):min(len(value),m.end()+400)]
            item={'field':field,'origin':'extraction_narrative' if field in narrative else ('source_title' if field=='title' else 'extracted_source_quote'),'snippet':sanitize(snippet),'gambling_context':bool(GAMBLING.search(value))}
            if not any(h['snippet']==snippet for h in hits):hits.append(item)
    return hits

def wave_windows(sources):
    by_year=collections.defaultdict(set)
    for r in sources:
        if r['state'] and r['year'] is not None:by_year[r['year']].add(r['state'])
    centers=sorted({c for y in by_year for c in (y-1,y,y+1)})
    return [{'center_year':y,'states':sorted(by_year[y-1]|by_year[y]|by_year[y+1])} for y in centers if len(by_year[y-1]|by_year[y]|by_year[y+1])>=3]

def load_derived(path):
    out={};duplicates=conflicts=0
    with path.open() as f:
        for r in csv.DictReader(f):
            pid=r['policy_id']
            if pid in out:
                duplicates+=1
                if out[pid]!=r:
                    conflicts+=1;out[pid]['layer_conflict']='1';out[pid]['strict_enacted']='0'
            else:out[pid]=r
    return out,{'duplicates':duplicates,'conflicts':conflicts}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--csv',type=Path,default=ROOT/'data/output/policy_db_20260415_483k.csv')
    ap.add_argument('--derived',type=Path,default=ROOT/'data/output/derived/derived_views.csv')
    ap.add_argument('--db',type=Path,default=ROOT/'src/output/llm_results.db')
    ap.add_argument('--output',type=Path,default=ROOT/'data/pipeline/queue_v2')
    args=ap.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    derived,derived_audit=load_derived(args.derived)
    counts=collections.defaultdict(collections.Counter)
    seen={};meta={};pools=collections.defaultdict(list);research=[];rdd_best={};rct_best={};source_count=0
    paths=['sources.jsonl','rdd_candidates.jsonl','rct_mentions.jsonl','dispositions.csv','duplicate_conflicts.jsonl']
    fs={p:(args.output/p).open('w',encoding='utf-8',newline='') for p in paths}
    dw=csv.writer(fs['dispositions.csv']);dw.writerow(['source_text_id','chapter_id','domain','track','disposition','reason','reopen_condition'])
    def bump(track,stage,domain):counts[(track,stage)][domain]+=1
    def dispose(s,track,reason,reopen):
        bump(track,'disposition:'+reason,s['domain']);dw.writerow([s['source_text_id'],s['chapter_id'],s['domain'],track,'queued' if reason.endswith('verification') else 'awaiting_evidence',reason,reopen])
    def process(row,detail,chapter='',source='legacy_csv',rownum=None):
        nonlocal source_count
        pid=row['policy_id'];domain=row.get('policy_topic') or detail.get('policy_topic') or 'other';family=family_of(row.get('policy_family') or detail.get('policy_family'))
        dv=derived.get(pid,{})
        s={'source_text_id':pid,'chapter_id':chapter,'source_record_id':f'{pid}::{chapter or "whole"}', 'source':source,'cohort':'legacy' if pid in seen else 'db_additional','source_row_number':rownum,'domain':domain,'policy_family':family,'policy_family_id':stable_id('family',family) if family else None,'legal_instrument_id':stable_id('legal',pid),'legal_instrument_mapping':'provisional: same parent policy ID; bill versions not collapsed to enacted law','title':row.get('title',''),'state':state_of(row.get('jurisdiction')),'jurisdiction':row.get('jurisdiction',''),'year':year_of(row),'database_date':row.get('date',''),'database_status':row.get('status',''),'strict_enacted':dv.get('strict_enacted')=='1','enactment_layer_available':bool(dv),'legacy_viable_strict':dv.get('viable_strict')=='1','confound_flags':dv.get('confound_flags',''),'legacy_wave_flag':dv.get('wave_flag',''),'data_alias_verdict':dv.get('data_verdict','unknown'),'suggested_datasets':detail.get('datasets',detail.get('recommended_datasets','')),'official_data_verified':False,'source_url':safe_url(row.get('url') or detail.get('document_url')),'extraction_evidence':evidence(detail),'threshold_evidence':thresholds(detail),'confounding_policies':detail.get('confounding_policies',''),'outcome_suggestions':detail.get('outcomes',detail.get('potential_outcomes','')),'suspect_extraction_artifact':any('score-consistency injection' in str(detail.get(k,'')) for k in ('did_reasoning','rd_reasoning')),'payload_sha256':digest(detail)}
        if source=='whole_db':s['source_record_id']=f'{pid}::db_whole'
        s['extraction_version']=source
        if source!='legacy_csv':
            access=classify_datasets(s['suggested_datasets'])
            s['data_alias_verdict']=access['verdict'];s['data_alias_matches']=access
        s=sanitize(s)
        s['derived_layer_conflict']=dv.get('layer_conflict')=='1'
        s['evidence_strength']=max([sum(bool(str(x.get(k,'')).strip()) for k in ('quote','treatment_group','control_group','timing')) for x in s['extraction_evidence']] or [0])
        source_count+=1;bump('source','all_records',domain)
        fs['sources.jsonl'].write(json.dumps(s,ensure_ascii=False)+'\n')
        did=flag(row.get('did')) or bool(list_of(detail.get('all_did_evidences'))) or flag(detail.get('did_candidate'))
        if did:bump('DiD','descriptive_extracted_signal',domain)
        bump('DiD','01_all_source_records',domain)
        if not family or family.startswith('unique_'):dispose(s,'DiD','family_missing_or_unique','Verify common legal intervention and assign family.')
        elif not s['state']:dispose(s,'DiD','non_state_track','Verify another DiD unit or federal rollout; no universal exclusion.')
        else:
            bump('DiD','02_state_family',domain)
            if not s['strict_enacted']:dispose(s,'DiD','enactment_unverified','Find official enactment/status evidence; missing legacy derived layer held.')
            elif s['year'] is None:dispose(s,'DiD','timing_missing','Recover official adoption dates; database dates are unverified.')
            else:bump('DiD','03_strict_enacted_and_year',domain);pools[family].append(s)
        ts=s['threshold_evidence'];rd=flag(row.get('rd')) or bool(ts)
        if rd:
            bump('RDD','01_signal',domain)
            if not ts:dispose(s,'RDD','threshold_fields_missing','Supply quoted threshold and measurable running variable.')
            else:
                bump('RDD','02_threshold_fields',domain);good=[]
                for x in ts:
                    if not measurable_threshold(x):continue
                    bump('RDD','03_measurable_numeric_threshold_records',domain)
                    if str(x.get('quote','')).strip().lower() in ('','none','null','unknown','n/a','unclear','not specified','not available'):continue
                    bump('RDD','04_quoted_threshold_records',domain)
                    key=[pid,chapter,str(x.get('running_variable','')).lower().strip(),str(x.get('cutoff','')).strip()]
                    if source=='whole_db':key.append('db_whole')
                    d={'design_id':stable_id('RDD',key),'track':'RDD','domain':domain,'policy_family':family,'status':'queued','disposition':'lexical_threshold_pending_verification','evidence_status':'unverified extraction, statutory applicability and measurements pending','source_records':[s],'threshold':x,'evidence_strength':sum(bool(x.get(k)) for k in ('running_variable','cutoff','quote','rd_type')),'data_alias_verdict':s['data_alias_verdict'],'official_data_verified':False}
                    fs['rdd_candidates.jsonl'].write(json.dumps(d,ensure_ascii=False)+'\n');good.append(d)
                    rank=ranking_key(d)
                    if family not in rdd_best or rank<rdd_best[family][0]:rdd_best[family]=(rank,d)
                if good:bump('RDD','05_queued_source_records',domain);dispose(s,'RDD','threshold_verification','Verify official cutoff, running-variable microdata, manipulation, common thresholds.')
                else:dispose(s,'RDD','threshold_not_measurable_or_unquoted','Recover numeric cutoff/running variable and exact legal quote.')
        else:dispose(s,'RDD','no_extracted_signal','Re-screen when legal threshold evidence is available.')
        hits=lexical_evidence(row,detail)
        if hits:
            bump('RCT','01_random_or_lottery_mention',domain)
            gambling=all(x['gambling_context'] for x in hits)
            quote_lane=any(x['origin']!='extraction_narrative' for x in hits)
            if quote_lane:bump('RCT','descriptive_title_or_quoted_mention',domain)
            rct_key=[pid,chapter]+(['db_whole'] if source=='whole_db' else [])
            d={'design_id':stable_id('RCT',rct_key),'track':'RCT','domain':domain,'policy_family':family,'status':'queued','disposition':'government_gambling_lottery' if gambling else ('allocation_mention_pending_verification' if quote_lane else 'weak_narrative_mention_missing_source_quote'),'evidence_status':'lexical mention only','title_or_quoted_evidence_available':quote_lane,'source_quote_available':any(x['origin']=='extracted_source_quote' for x in hits),'source_records':[s],'mentions':hits,'assignment_mechanism':None,'assignment_unit':None,'assignment_probability':None,'assignment_records_url':None,'eligible_population':None,'strata_or_blocks':None,'compliance':None,'attrition':None,'outcome_linkage':None,'official_data_verified':False}
            fs['rct_mentions.jsonl'].write(json.dumps(d,ensure_ascii=False)+'\n')
            if gambling:dispose(s,'RCT','government_gambling_lottery','Supply evidence of treatment allocation, rather than gambling operations.')
            else:
                bump('RCT','02_non_gambling_allocation_mentions',domain);dispose(s,'RCT','allocation_verification','Verify lottery/randomization roster, probabilities, assignment unit and free outcome linkage.')
                if family not in rct_best or quote_lane>rct_best[family]['title_or_quoted_evidence_available']:rct_best[family]=d
        else:dispose(s,'RCT','no_quoted_random_allocation_mention','Supply source quote or assignment records; absence in extraction is not absence in full text.')
    with args.csv.open(encoding='utf-8',errors='replace',newline='') as f:
        for lineno,row in enumerate(csv.DictReader(f),2):
            pid=row['policy_id']
            try:
                detail=json.loads(row.get('detail_json') or '{}')
                if not isinstance(detail,dict):raise ValueError('nonobject detail')
            except (ValueError,TypeError):detail={};bump('source','malformed_detail_json',row.get('policy_topic') or 'other')
            h=digest(row)
            if pid in seen:
                bump('source','duplicate_rows',row.get('policy_topic') or 'other')
                if seen[pid]!=h:
                    bump('source','conflicting_duplicate_rows',row.get('policy_topic') or 'other');fs['duplicate_conflicts.jsonl'].write(json.dumps({'source_text_id':pid,'kept_sha256':seen[pid],'other_sha256':h,'other_row_number':lineno})+'\n')
                continue
            seen[pid]=h;meta[pid]={k:row.get(k,'') for k in ('policy_id','title','jurisdiction','year','date','status','url','source_family')};process(row,detail,rownum=lineno)
    conn=sqlite3.connect(f'file:{args.db}?mode=ro',uri=True)
    db_metadata=conn.execute("select count(*),count(distinct policy_id),sum(chapter_id!=''),sum(error is not null and error!=''),min(analyzed_at),max(analyzed_at) from results").fetchone()
    chapter_audit=collections.Counter();db_scope=collections.Counter()
    for pid,raw,topic,fam,error in conn.execute("select policy_id,result_json,policy_topic,policy_family,error from results where chapter_id='' order by policy_id"):
        within=pid in seen
        prefix='within_legacy' if within else 'additional'
        db_scope[prefix+'_whole_policy_available']+=1
        if error:db_scope[prefix+'_whole_policy_errored']+=1;continue
        try:
            detail=json.loads(raw or '{}')
            if not isinstance(detail,dict):raise ValueError('nonobject')
        except ValueError:db_scope[prefix+'_whole_policy_malformed']+=1;continue
        row=dict(meta[pid]) if within else {'policy_id':pid,'title':detail.get('title',''),'jurisdiction':detail.get('jurisdiction',''),'status':detail.get('policy_status',''),'date':detail.get('introduced_date',''),'url':detail.get('document_url','')}
        row.update(policy_topic=topic or detail.get('policy_topic','other'),policy_family=fam or detail.get('policy_family',''),did=detail.get('did_candidate'),rd=detail.get('rd_candidate'))
        if not within:meta[pid]=row
        process(row,detail,source='whole_db' if within else 'additional_db');db_scope[prefix+'_whole_policy_included']+=1
    for pid,cid,raw,topic,fam,error in conn.execute("select policy_id,chapter_id,result_json,policy_topic,policy_family,error from results where chapter_id!='' order by policy_id,chapter_id"):
        chapter_audit['available']+=1
        if error:chapter_audit['errored']+=1;continue
        try:
            detail=json.loads(raw or '{}')
            if not isinstance(detail,dict):raise ValueError('nonobject detail')
        except ValueError:chapter_audit['malformed']+=1;continue
        if pid not in meta:
            chapter_audit['parent_metadata_from_chapter_only']+=1
            meta[pid]={'policy_id':pid,'title':detail.get('title',''),'jurisdiction':detail.get('jurisdiction',''),'status':detail.get('policy_status',''),'date':detail.get('introduced_date',''),'url':detail.get('document_url','')}
        row=dict(meta[pid]);row.update(policy_topic=topic or detail.get('policy_topic','other'),policy_family=fam or detail.get('policy_family',''),did=detail.get('did_candidate'),rd=detail.get('rd_candidate'))
        process(row,detail,chapter=cid,source='chapter_db');chapter_audit['included']+=1
    conn.close()
    did_designs=[]
    with (args.output/'did_designs.jsonl').open('w',encoding='utf-8') as f:
        for fam,ss in sorted(pools.items()):
            windows=wave_windows(ss)
            if not windows:
                for s in ss:dispose(s,'DiD','no_three_state_wave','Verify missing states or use another DiD design unit; sampling is incomplete.')
                continue
            years={y for w in windows for y in range(w['center_year']-1,w['center_year']+2)}
            included=[s for s in ss if s['year'] in years]
            for s in ss:
                if s not in included:dispose(s,'DiD','outside_detected_wave','Verify complete legal timeline or another design window.')
                else:bump('DiD','04_wave_eligible_source_records',s['domain']);dispose(s,'DiD','wave_verification','Blindly verify legal intervention, full clocks, controls, confounds and data coverage.')
            domain=collections.Counter(s['domain'] for s in included).most_common(1)[0][0]
            best=sorted(included,key=lambda s:(-s['evidence_strength'],s['data_alias_verdict'] not in ('public_ok','mixed'),s['source_record_id']))
            d={'design_id':stable_id('DiD',fam),'track':'DiD','domain':domain,'policy_family':fam,'policy_family_id':stable_id('family',fam),'status':'queued','disposition':'same_family_wave_pending_verification','evidence_status':'database text years, not verified adoption dates','windows':windows,'source_record_count':len(included),'distinct_source_texts':len({s['source_text_id'] for s in included}),'states':sorted({s['state'] for s in included}),'source_records':best,'evidence_strength':best[0]['evidence_strength'],'data_alias_verdict':'public_ok' if any(s['data_alias_verdict']=='public_ok' for s in included) else best[0]['data_alias_verdict'],'official_data_verified':False,'family_scope':'provisional; expansions, repeals and distinct instruments must be split during verification'}
            bump('DiD','05_family_designs',domain);f.write(json.dumps(d,ensure_ascii=False)+'\n');did_designs.append(d)
    rank=ranking_key
    did_designs.sort(key=rank)
    compact=[]
    for n,d in enumerate(did_designs,1):
        compact.append({**{k:v for k,v in d.items() if k!='source_records'},'rank':n,'representative_sources':d['source_records'][:8],'all_source_record_ids':[s['source_record_id'] for s in d['source_records']]})
    research={'did_ranked':compact,'rdd_family_exemplars':[d for _,d in sorted(rdd_best.values(),key=lambda item:rank(item[1]))],'rct_family_exemplars':list(rct_best.values())}
    (args.output/'research_candidates.json').write_text(json.dumps(research,ensure_ascii=False,indent=2)+'\n')
    for f in fs.values():f.close()
    summary={'version':'queue_v2.0','source_scope':'Unique legacy cohort, first row kept; all successful DB whole-policy and chapter outputs supplement it as separate extraction versions without replacing baseline. DB extras lack legacy strict-enactment layers and are held explicitly for verification. Data aliases for DB extracts are recomputed from the existing free-data registry, still unverified availability.','unique_legacy_source_texts':len(seen),'unique_source_texts_all_cohorts':len(meta),'source_records_including_chapters':source_count,'did_family_designs':len(did_designs),'db_metadata':dict(zip(['rows','unique_policy_ids','chapter_rows','error_rows','earliest_analyzed_at','latest_analyzed_at'],db_metadata)),'db_scope':dict(db_scope),'chapter_scope':dict(chapter_audit),'derived_audit':derived_audit,'counts':{track:{stage:{'total':sum(dom.values()),'by_domain':dict(sorted(dom.items()))} for (tr,stage),dom in counts.items() if tr==track} for track in ('source','DiD','RDD','RCT')},'ranking':'domain priority, public data alias match, quoted evidence completeness, state coverage, stable ID; no sign, significance, policy_significance, composite or min-gate','counts_unit':'source records except explicitly threshold_records or family_designs; track counts overlap; extraction versions can repeat source text IDs','downloads':[],'inputs':[{'path':str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),'bytes':p.stat().st_size,'sha256':file_hash(p)} for p in (args.csv,args.derived,args.db,ROOT/'src/extraction/dataset_registry.py')]}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'source_texts':len(seen),'did_designs':len(did_designs),'counts':{k:{stage:v['total'] for stage,v in stages.items() if not stage.startswith('disposition:')} for k,stages in summary['counts'].items()},'chapter_scope':dict(chapter_audit)},indent=2))

if __name__=='__main__':main()
