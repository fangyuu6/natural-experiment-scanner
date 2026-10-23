"""Run1 prospective preparation. Emits jobs; never dispatches or estimates.

Read original source/card bindings for S1, then create a separate prospective
annual package from metadata alone. The 2024 endpoint is selected before reading
outcome values. Agent submissions are immutable evidence, not instructions.
"""
from __future__ import annotations
import argparse, copy, csv, json, time
from dataclasses import asdict
from pathlib import Path
from .agents.jobs import load_job, emit_stage_jobs, PERSONAS
from .cards import load_card
from .cli import run_stage
from .contracts import StageContext, StageResult, Disposition
from .full_year import construct_full_year
from .storage import ensure_once, sha, artifact
from .stages.s1_reconciliation import emit_followup

REPO = Path(__file__).resolve().parents[2]
OUTCOMES = REPO / 'data/l2_pipeline/jobs/run1/_v4/outcomes'
ROOT = REPO / 'data/l2_pipeline/jobs/run1/_v4/reviewed'
OLD = REPO / 'data/l2_pipeline/jobs/run1/_s1_reconciliation'


def read(p): return json.loads(Path(p).read_text())
def bind(p): return {'path': str(Path(p).resolve().relative_to(REPO)), 'sha256': sha(Path(p))}

def timed(ctx, stage, times):
    start=time.perf_counter(); r=run_stage(ctx,stage);times[stage]=time.perf_counter()-start
    if r.disposition==Disposition.FAILED: raise ValueError(stage+': '+r.reason)
    return r


def make_ruling():
    original=read(OLD/'HUB_RULING_V2_LIST.json');configs=copy.deepcopy(original['cards'])
    evidence={
        'run1_pt_compact': {'administrator_publishes_withdrawals':True,
            'basis':'Commission Policy1.4 requires administrator notification of withdrawal effective date to ALL states/member and nonmember and affected privilege holders. V4 administrator-notification inference; known contrary histories remain untouched.',
            'locator':'PT_WITHDRAWAL_POLICY.txt, Policy1.4 procedure2',
            'evidence':bind(OUTCOMES/'PT_WITHDRAWAL_POLICY.pdf')},
        'run1_ems_compact': {'administrator_publishes_withdrawals':True,
            'basis':'Administrator publishes governing status rules and state authority notices: Rule13.1(G,H) requires public website/regulatory announcement of membership termination. V4 administrator-publication inference; original known contrary histories remain untouched.',
            'locator':'EMS_RULES_2025.txt, Rule13.1(G,H); rule2024 archived separately',
            'evidence':bind(OUTCOMES/'EMS_RULES_2025.html')},
    }
    for key,decls in configs.items():
        for d in decls:
            d['withdrawal_history']=evidence.get(key,{'possible':True,
                'basis':'No administrator history publication certification is available for this mechanism. Primary stock absence is an explicit V4 assumption; all uncertified state histories are registered for drop_uncertain_history_states. Known dates and contrary histories are retained.',
                'locator':d['locator'], 'source_pack_sha256':d['source_pack_sha256']})
    p=ROOT/'HUB_RULING_V4.json'
    ensure_once(p,{'ruling_id':'HUB_RULING_V4_STOCK_HISTORY','binding_document':bind(REPO/'docs/plans/l2-scale/HUB_RULING_V4_STOCK_HISTORY.md'),
        'cards':configs,'assumptions_before_outcomes':True,'outcome_associations_read':False,
        'absence_scope':'V4 stock absence with withdrawal declaration; preserve known paths; unknown dated/scope tuples excluded, never converted to dates'})
    return p,configs


def reconcile_all():
    ruling,configs=make_ruling();results=[];originals={}
    for key in sorted(configs):
        old=OLD/'work'/key/'v1';inputs=read(old/'S1_RECONCILIATION_INPUTS.json')
        a=load_job(Path(inputs['coder_jobs']['A']));pack=read(Path(a.contract['source_pack']['manifest_path']))
        card=load_card(Path(pack['card_path']));ctx=StageContext(card,ROOT/'reconciliation',REPO)
        follows={}
        for role in ('auditor','third_reader'):
            p=OLD/'jobs/S1'/key/(role+'.md')
            if p.exists():
                job=load_job(p);originals[job.output_path]=sha(Path(job.output_path))
                if (Path(job.promote_path).is_file()): follows[role]=str(p)
        for role,p in inputs['coder_jobs'].items():
            job=load_job(Path(p));originals[job.output_path]=sha(Path(job.output_path))
            if Path(job.promote_path).is_file():originals[job.promote_path]=sha(Path(job.promote_path))
        window=[2014,2024] if card.payload['outcome']['connector']=='qcew' else [2000,2024]
        ensure_once(ctx.design_dir/'S1_RECONCILIATION_INPUTS.json',dict(inputs,ruling_path=str(ruling),ruling_sha256=sha(ruling),
            followup_jobs=follows,emit_missing_followups=False,followup_jobs_root=str(ROOT/'jobs'),outcome_inventory_window=window))
        times={};timed(ctx,'S0',times);stage=timed(ctx,'S1',times);report=ctx.read('RECONCILIATION.json')
        results.append({'design_id':key,'s1_status':report['status'],'status':report['status'],'reason':stage.reason,
            'window':report['window'],'agreement':report['historical_comparison']['agreement'],
            'official_audit':report['official_audit'],'stage_timings_seconds':times,'reconciliation_path':str(ctx.design_dir/'RECONCILIATION.json')})
        if key=='run1_refundable_care_credit':
            # Retain rejected read byte-for-byte; new job binds the new same-unit conflict.
            retry=emit_followup(ctx,'third_reader',ROOT/'jobs',ctx.design_dir/'RECONCILIATION.json',ctx.design_dir/'CODING_LOCK.json',
                Path(a.contract['source_pack']['manifest_path']),attempt_id='attempt2')
            results[-1]['third_read_attempt2_job']=retry.job_path
    ensure_once(ROOT/'ORIGINAL_HASHES.json',originals)
    ensure_once(ROOT/'S1_REPORT.json',{'cards':results,'originals_unchanged':all(sha(Path(p))==h for p,h in originals.items()),'dispatched':False})
    return results


def raw_outcome(ctx, times):
    original=dict(ctx.card.payload);card=copy.deepcopy(original);key=ctx.card.design_id
    card['version']='v42' if key=='run1_ems_compact' else 'v40';card['analysis']['controls']='never_treated'
    card['outcome']['years']=ctx.read('RECONCILIATION.json')['window']['inventory_window']
    for s in card['sources']:
        if s.get('path'):s['path']=str((ctx.card.path.parent/s['path']).resolve())
    o=card['outcome']
    if o['connector']=='qcew':
        inventory=read(OUTCOMES/'QCEW_DOWNLOADS.json');items=inventory.get('downloads',inventory) if isinstance(inventory,dict) else inventory
        files=[r for r in items if str(o['population_filters']['industry']) in Path(r['path']).name]
        o['paths']=[r['path'] for r in files];o['source_urls']={r['path']:r['url'] for r in files};o['source_sha256']={r['path']:r['sha256'] for r in files}
        o['outcome_unit']='annual average covered employment; jobs'
        if key=='run1_ems_compact':
            o['ownership_aggregation']='certified_sum'
            o['ownership_completeness_certificate']=str(OUTCOMES/'EMS_OWNERSHIP_CERTIFICATE.json')
    else:
        years=range(o['years'][0],o['years'][1]+1)
        files=sorted(REPO.joinpath('scripts/l2_batch1/raw/monthly').glob('*.zip'))
        files=[p for p in files if int(p.name[:4]) in years]
        o['paths']=[str(p) for p in files];o['source_urls']={str(p):f'https://www2.census.gov/programs-surveys/cps/datasets/{p.name[:4]}/basic/{p.name[5:8]}{p.name[2:4]}pub.zip' for p in files}
        if key=='run1_nurse_overtime':o['dictionary_root']=str(REPO/'scripts/l2_batch1/raw/metadata')
        o['outcome_unit']='actual total weekly hours; employed at-work hospital registered nurses' if key=='run1_nurse_overtime' else 'employment percentage; resident women25–54 with own children'
    o['license']='US federal government public domain; cite BLS/Census; existing local originals unchanged'
    path=ensure_once(ROOT/'cards'/('source_'+card['version'])/ (key+'.json'),card)
    source=StageContext(load_card(path),ROOT/'source_work',REPO);run_stage(source,'S0')
    # This is a hash-verified import of the fresh validated V4 treatment, not a fresh coder run.
    proof=ensure_once(source.design_dir/'S1_IMPORT_PROVENANCE.json',{'evidence':[bind(ctx.design_dir/'RECONCILIATION.json'),bind(ctx.design_dir/'TREATMENT.json')], 'v4_import':True})
    p=ensure_once(source.design_dir/'TREATMENT.json',ctx.read('TREATMENT.json'))
    r=StageResult('S1',key,Disposition.READY,'Validated V4 source-ready import',artifacts=(artifact(proof,'provenance'),artifact(p,'treatment')))
    ensure_once(source.design_dir/'receipts/S1/0000000000000000000.json',{'card_sha256':sha(path),'result':r.as_dict()})
    source_times={};r=timed(source,'S2',source_times)
    times['S2_outcome_construction']=source_times['S2']
    return source,r


def annual_package(source, s1, times):
    card=copy.deepcopy(dict(source.card.payload));key=card['design_id'];card['version']='v43' if key=='run1_ems_compact' else 'v41'
    folder=source.design_dir/'panels/primary'
    with (folder/'metadata.csv').open(newline='') as f: metadata=list(csv.DictReader(f))
    start,end=card['outcome']['years'];years=list(range(start,end+1));records=s1.read('TREATMENT.json')['all_dated_records']
    op={r['unit']:r['start_period']-1 for r in records}
    never=s1.read('RECONCILIATION.json')['window']['never_treated_units']
    units=card['treatment']['universe']; available={(r['unit'],int(r['year'])) for r in metadata if r['annual_usable']=='1' and r.get('linkage_certified','1')=='1' and r.get('suppression_flag','0')=='0'}
    # Prespecified completeness selection uses only support metadata and never outcome signs.
    incomplete=sorted(u for u in units if not all((u,y) in available for y in years if y!=op.get(u)))
    unknown=s1.read('RECONCILIATION.json')['window']['excluded_undated_units']
    d={'key':key,'window':[start,end],'period_years':years,'operative':op,'actual_clean_onset':op,
       'never_controls':never,'exclusions':sorted(set(unknown)|set(incomplete)),'omitted_years':[],
       'anchor_availability_exclusions':incomplete,'source_ready':True,
       'disputed_audit_error_units':[],'unresolved_audit_units':[],'competing_mechanism_units':[],
       'narrow_coverage_units':[],'scope_ambiguous_starts':{},'initial_step_starts':{},
       'uncertain_history_units':s1.read('TREATMENT.json').get('required_sensitivities',{}).get('drop_uncertain_history_states',[]),
       'clock_mapping':'Original starts are first fully exposed year; operative=start-1 is a conservative annual transition-year boundary, not an inferred legal date.',
       'v4_stock_history_sensitivity':s1.read('TREATMENT.json').get('required_sensitivities',{})}
    checks={'source_audit_pass':True,'mechanism_scope_certified':True,'v4_stock_convention_applied':True,'independent_coding_verified':True}
    variants=['primary','start_plus_one','start_minus_one','exclude_2020_2021','not_yet_controls','exclude_convention_controls']
    variants+=['omit_state_'+u for u in sorted(set(op)-set(d['exclusions'])) if start<=op[u]<end]
    variants+=['omit_cohort_'+str(y) for y in sorted({g+1 for g in op.values() if start<=g<end})]
    if d['uncertain_history_units']:variants+=['drop_uncertain_history_states']
    if key=='run1_nurse_overtime':
        variants+=['constant_definition_window'];d['constant_definition_window']=[2011,end]
    masks={};summaries=[]
    for name in variants:
        summary,required=construct_full_year(d,name,metadata,checks)
        mask_key=key+'_v43' if key=='run1_ems_compact' else key
        mask=ensure_once(ROOT/'masks'/mask_key/(name+'.json'),[[u,y] for u,y in sorted(required)])
        masks[name]={'mask_path':str(mask),'mask_sha256':sha(mask)};summaries.append({'variant':name,**summary})
    # A new explicit declaration-only variant routes to primary with a different calendar axis.
    card['outcome'].update(connector='opaque_state_year',paths=[str(folder/'panel.csv')],source_sha256=sha(folder/'panel.csv'),**masks['primary'])
    registration={'estimand':'Equal-state group-time policy availability ATT in original outcome units; no outcome association inspected during preparation.',
        'clock':d['clock_mapping'],'controls':'V4 dated stock-list never-treated pool; retain known future/prior treatment outside the never pool; unknown scope excluded.',
        'weights':'Equal states within cohort-time; treated-state mass across supported post cells. No covariates.',
        'inference':'Fixed Webb then certified CT complete control path then point+RI fallback; 4999 state multipliers,999 predrawn labels without redraw;1000-world coverage/width certification and two approvals pending.',
        'diagnostics':'three clean pre, six treated, two cohorts, five controls per post; supported pre leads/placebo; two common clean anchors and five complete CT controls. No anchor computed.',
        'stop_rules':'Primary metadata failure parks. Unsupported sensitivity retained, cannot rescue primary. No fit until forecasts, calibration, final freeze, review and distinct approvals.'}
    card['analysis'].update(support_adapter='annual_full_year_v1',support_metadata_path=str(folder/'metadata.csv'),support_metadata_sha256=sha(folder/'metadata.csv'),
        full_year_variant='primary',min_pre_periods=3,min_controls=5,min_cohorts=2,min_treated=6,clock='first_wholly_exposed_year',controls='never_treated',
        estimand=registration['estimand'],weights=registration['weights'],inference=registration['inference'],diagnostics=['pretrend_joint','pretrend_economic','placebo'],
        stop_rules=['support','registered_calibration','fail_closed_integrity'],interval_path=['Webb','CT_complete_control_path','point_plus_RI_if_uncertified'],registration=registration)
    card['treatment']['full_year_declaration']=d
    card['sensitivities']=[{'name':name,'changes':{'outcome':masks[name],'analysis':{'full_year_variant':name}}} for name in variants if name!='primary']
    card['automation']={'registration_identity':{'schema':'l2_run1_v4_draft','design_id':key,'outcome_spec_id':key+'_v4','status':'DRAFT_UNSIGNED'},'methods_path':str(ROOT/'METHODS_V4.md'),
        'prediction_templates':{role:str(ROOT/'templates'/key/(role+'.json')) for role in PERSONAS}}
    path=ensure_once(ROOT/'cards'/('draft_'+card['version'])/ (key+'.json'),card);ctx=StageContext(load_card(path),ROOT/'work',REPO);run_stage(ctx,'S0')
    proof=ensure_once(ctx.design_dir/'S1_IMPORT_PROVENANCE.json',{'evidence':[bind(s1.design_dir/'RECONCILIATION.json'),bind(s1.design_dir/'TREATMENT.json')], 'v4_import':True,'originals_read_only':True})
    t=ensure_once(ctx.design_dir/'TREATMENT.json',{'records':s1.read('TREATMENT.json')['records'],'full_year_declaration':d,'source_checks':checks})
    for name,value in [('RECONCILIATION.json',s1.read('RECONCILIATION.json')),
                       ('CODING_LOCK.json',s1.read('CODING_LOCK.json')),
                       ('HUB_RULING.json',read(ROOT/'HUB_RULING_V4.json'))]:
        ensure_once(ctx.design_dir/name,value)
    source_report=s1.read('RECONCILIATION.json')
    official=source_report['applied_followups']['auditor']['promoted_path']
    ensure_once(ctx.design_dir/'OFFICIAL_AUDIT.json',read(official))
    if 'third_reader' in source_report['applied_followups']:
        ensure_once(ctx.design_dir/'THIRD_READ.json',read(source_report['applied_followups']['third_reader']['promoted_path']))
    ensure_once(ctx.design_dir/'SOURCES.json',{'review_scope':'Legal timing/audit/withdrawal metadata; no outcomes',
        'reconciliation_sha256':sha(ctx.design_dir/'RECONCILIATION.json'),
        'hub_ruling_sha256':sha(ctx.design_dir/'HUB_RULING.json'),
        'stock_history_assumptions':read(ROOT/'HUB_RULING_V4.json')['cards'][key],
        'official_start_audit':read(official),'source_pack_sha256':source_report['source_pack_sha256']})
    r=StageResult('S1',key,Disposition.READY,'Prospective V4 annual source-ready import',artifacts=(artifact(proof,'provenance'),artifact(t,'treatment')))
    ensure_once(ctx.design_dir/'receipts/S1/0000000000000000000.json',{'card_sha256':sha(path),'result':r.as_dict()})
    for stage in ('S2','S3','S4'):
        r=timed(ctx,stage,times)
        if stage=='S2':times['S2_opaque_masks']=times.pop('S2')
        if r.disposition!=Disposition.READY:return ctx,r
    # Readable spec and methods match batch3/4 draft forecast conventions.
    spec_md=ctx.design_dir/'SPEC.md';ensure_text(spec_md,'# '+key+' V4 draft\n\n'+ '\n\n'.join(k+': '+v for k,v in registration.items())+'\n\nSource availability exclusions: '+json.dumps(d['exclusions'])+'\n')
    return ctx,r


def ensure_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_text()!=text:raise ValueError('Immutable text changed: '+str(path))
    else:path.write_text(text)


def emit_forecasts(ctx):
    templates={}
    for role in PERSONAS:
        p=ROOT/'templates'/ctx.card.design_id/(role+'.json')
        template={'status':'BLANK_PRE_ESTIMATION_TEMPLATE','design_id':ctx.card.design_id,'outcome_spec_id':ctx.card.design_id+'_v4',
            'spec_path':str(ctx.design_dir/'SPEC.md'),'spec_sha256':sha(ctx.design_dir/'SPEC.md'),
            'spec_json_path':str(ctx.design_dir/'SPEC_DRAFT.json'),'spec_json_sha256':sha(ctx.design_dir/'SPEC_DRAFT.json'),
            'methods_path':str(ROOT/'METHODS_V4.md'),'methods_sha256':sha(ROOT/'METHODS_V4.md'),
            'reviewer_perspective':role,'reviewer_identity':'Internal AI perspective; not the named scholar',
            'unit':ctx.card.payload['outcome']['outcome_unit'],'entered_at':None,'central_ATT':None,'interval_80':None,
            'prob_positive':None,'prob_negative':None,'prob_near_zero':None,'near_zero_threshold':None,
            'prob_diagnostics_pass':None,'direction':None,'mechanism':None,'assumptions':[],
            'outcome_associations_seen_attestation':None,'signature':None,'freeze_manifest_sha256':None}
        ensure_once(p,template);templates[role]=str(p)
    jobs=emit_stage_jobs(ctx,'S5',jobs_root=ROOT/'jobs_final')
    jobs+=emit_stage_jobs(ctx,'S6',jobs_root=ROOT/'jobs_final',allow_draft=True)
    ensure_once(ctx.design_dir/'EMITTED_JOBS.final.json',{'jobs':[asdict(j) for j in jobs],'dispatched':False})
    return jobs


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=('reconcile','prepare'),required=True);args=p.parse_args(argv)
    ROOT.mkdir(parents=True,exist_ok=True)
    if args.phase=='reconcile':
        results=reconcile_all();print(json.dumps({'cards':[(r['design_id'],r['status']) for r in results]},indent=2));return 0
    ensure_text(ROOT/'METHODS_V4.md','# Run1 annual V4 draft methods\n\n'+
        'Reuse pipeline annual_full_year_v1 batch3/4 metadata geometry and copied engine. Coder start is first fully exposed year; omit prior transition year. V4 whole-window stock-list absence subject to documented withdrawal assumptions; known prior/future histories retained, unknown scope dropped. Metadata availability selects complete units before outcomes are inspected. Primary omits no COVID years; fixed exclusion sensitivity retained. All sensitivity masks are declared before opaque outcome copies. Forecasts and draft review precede calibration/approvals/freeze; no estimates or association summaries are run here.\n\n'+
        'QCEW therapy offices include PT/OT/speech/audiology; ambulance establishments exclude fire-department EMS. CPS hospital-RN weekly hours includes all jobs and current occupation selection; 2011 classification removes advanced-practice nurses. Neither observes mandatory overtime or all eligible persons; record occupation definition break. The fixed2011+ support appendix is metadata only and is not a rescue.\n')
    results=copy.deepcopy(read(ROOT/'S1_REPORT.json')['cards']);jobs=[]
    for item in results:
        if item['s1_status']!='ready':continue
        key=item['design_id'];ctxroot=ROOT/'reconciliation'/key/'v1';inputs=read(ctxroot/'S1_RECONCILIATION_INPUTS.json');a=load_job(Path(inputs['coder_jobs']['A']));pack=read(a.contract['source_pack']['manifest_path']);ctx=StageContext(load_card(Path(pack['card_path'])),ROOT/'reconciliation',REPO)
        source,r=raw_outcome(ctx,item['stage_timings_seconds'])
        construction=source.read('PANELS.json')['variants'][0]
        item['outcome_coverage']={k:construction[k] for k in ('cells','available_cells','years','units','limitations')}
        if r.disposition!=Disposition.READY:item.update(status='parked_S2',reason=r.reason);continue
        final,r=annual_package(source,ctx,item['stage_timings_seconds'])
        item['support_path']=str(final.design_dir/'SUPPORT.json')
        if r.disposition!=Disposition.READY:item.update(status='parked_'+r.stage,reason=r.reason);continue
        start=time.perf_counter();emitted=emit_forecasts(final);item['stage_timings_seconds']['jobs']=time.perf_counter()-start;jobs+=emitted
        item.update(status='S4_READY_S5_S6_JOBS',reason='Annual primary metadata support passed; draft review/forecast jobs emitted',
            forecast_jobs=5,reviewer_jobs=1,spec_path=str(final.design_dir/'SPEC.json'),panel_count=len(final.read('PANELS.json')['variants']))
        primary=next(r for r in final.read('SUPPORT.json')['variants'] if r['variant']=='primary')
        item['primary_counts']={'treated':len(primary['retained_treated']),'never':len(primary['never_controls']),'cohorts':sum(not r['dropped'] for r in primary['cohort_checks']),'post_cells':sum(r['post'] for r in primary['cell_inventory'])}
    originals=read(ROOT/'ORIGINAL_HASHES.json')
    if not all(sha(Path(p))==h for p,h in originals.items()):raise ValueError('Agent originals changed')
    ensure_once(ROOT/'REPORT.final.json',{'cards':results,'job_count':len(jobs)+1,'forecast_jobs':sum(j.stage=='S5' for j in jobs),
        'reviewer_jobs':sum(j.stage=='S6' for j in jobs),'third_reader_attempt2_jobs':1,'originals_unchanged':True,
        'dispatched':False,'model_calls':0,'real_estimation_calls':0,'outcome_association_calls':0,
        's3_outcome_values_parsed':0,'outcome_values_constructed_only_in_s2':True,
        'downloads_inventory':[str(p) for p in OUTCOMES.glob('*DOWNLOAD*.json')]})
    print(json.dumps({'cards':[(r['design_id'],r['status']) for r in results],'jobs':len(jobs)+1},indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
