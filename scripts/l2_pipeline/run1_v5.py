"""Immutable run1 review repair; metadata-only construction, no dispatch or fit."""
from __future__ import annotations
import copy
import csv
import json
import time
from pathlib import Path
from .cards import load_card
from .cli import run_stage
from .contracts import StageContext, StageResult, Disposition
from .full_year import construct_full_year
from .predictions import import_unchanged_predictions, primary_forecast_target
from .storage import ensure_once, sha, artifact
from .agents.jobs import PERSONAS, emit_stage_jobs

REPO=Path(__file__).resolve().parents[2]
OLD=REPO/'data/l2_pipeline/jobs/run1/_v4/reviewed'
ROOT=REPO/'data/l2_pipeline/jobs/run1/_v5'
VERSIONS={'run1_pt_compact':'v51','run1_ems_compact':'v53','run1_nurse_overtime':'v51'}


def read(path):return json.loads(Path(path).read_text())
def bind(path):return dict(path=str(Path(path).resolve().relative_to(REPO)),sha256=sha(path))
def text_once(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_text()!=text:raise ValueError('Existing text changed: '+str(path))
    else:path.write_text(text)


def prepare():
    results=[];originals={}
    methods=ROOT/'METHODS_V5.md'
    text_once(methods,'# Run1 V5 prospective review repair\n\n'
        'Equal-state group-time policy availability ATT; primary excludes states with documented cessation before the end of2024. '
        'The registered retain_ended_exposure sensitivity retains original adoption and prior exposure, censors the partial cessation year and all subsequent uncertified years, and never returns a formerly treated state to the control pool. '
        'Missouri: half-open exposure ends2019-11-30; fully exposed annual cells end2018. No replacement coverage is inferred. '
        'The primary sample change supersedes all five nurse forecasts; fresh forecast jobs precede its S5 lock/prep/calibration/final seal. '
        'PT and EMS primary cell inventories and forecast targets are unchanged; original forecast bytes are retained with explicit new-spec normalization provenance.\n\n'
        'Convention-control sensitivity removes only final never-treatment records with a convention object. '
        'analysis.multiplier_count is the numeric registered source for4999 state multipliers, including omission standard errors and placebo diagnostics;999 predrawn RI labels remain separate. '
        'Calibration uses fixed1000 invented worlds and93% coverage, with the registered width gates. '
        'Evaluate certified Webb and CT independently on the current panel; report the first feasible certified candidate in Webb then CT order; otherwise point plus RI p. '
        'RI feasibility must be at least90%, with no redraw. No sensitivity rescues the primary. '
        'Final approval remains unsigned until independent SEALED review PASS. Preparation reads only keys/support/legal metadata and copies opaque outcome bytes.\n')
    for key,version in VERSIONS.items():
        times={};old_version='v43' if key=='run1_ems_compact' else 'v41';old=OLD/'work'/key/old_version
        card=copy.deepcopy(read(old/'CARD.json') if (old/'CARD.json').exists() else read(OLD/'cards'/('draft_'+old_version)/(key+'.json')))
        treatment=read(old/'TREATMENT.json');d=card['treatment']['full_year_declaration'];records=treatment['records']
        d['convention_filled_controls']=sorted(r['unit'] for r in records if r.get('convention') and r['unit'] in d['never_controls'])
        d['exposure_end_dates']={r['unit']:min(e['date'] for e in r.get('history_events',[]) if e.get('type')=='documented_rule_rescission')
            for r in records if any(e.get('type')=='documented_rule_rescission' for e in r.get('history_events',[]))}
        d['reversal_handling']='drop_states'
        metadata_path=Path(card['analysis']['support_metadata_path'])
        if sha(metadata_path)!=card['analysis']['support_metadata_sha256']:raise ValueError('Source metadata changed')
        with metadata_path.open(newline='') as f:metadata=list(csv.DictReader(f))
        names=['primary']+[s['name'] for s in card['sensitivities']]
        if d['exposure_end_dates']:names+=['retain_ended_exposure']
        begin=time.perf_counter();masks={}
        for name in names:
            _,required=construct_full_year(d,name,metadata,treatment['source_checks'])
            mask=ensure_once(ROOT/'masks'/key/(name+'.json'),[[u,y] for u,y in sorted(required)])
            masks[name]=dict(mask_path=str(mask),mask_sha256=sha(mask))
        times['masks']=time.perf_counter()-begin
        card['version']=version;card['outcome'].update(masks['primary']);card['analysis']['multiplier_count']=4999
        card['analysis']['registration']['inference']='Fixed Webb then separately certified feasible CT then point+RI p; analysis.multiplier_count=4999 for every reported fit/omission and placebo diagnostic;999 predrawn labels without redraw;1000-world coverage>=93% plus width certification; two approvals required.'
        card['analysis']['inference']=card['analysis']['registration']['inference']
        card['sensitivities']=[dict(name=name,changes=dict(outcome=masks[name],analysis=dict(full_year_variant=name))) for name in names if name!='primary']
        card['automation']['registration_identity'].update(schema='l2_run1_v5_draft',outcome_spec_id=key+'_v5')
        card['automation']['methods_path']=str(methods)
        templates={role:str(ROOT/'templates'/key/(role+'.json')) for role in PERSONAS}
        card['automation']['prediction_templates']=templates
        path=ensure_once(ROOT/'cards'/(key+'.json'),card)
        ctx=StageContext(load_card(path),ROOT/'work',REPO)
        begin=time.perf_counter();r=run_stage(ctx,'S0');times['S0']=time.perf_counter()-begin
        if r.disposition!=Disposition.READY:raise ValueError(r.reason)
        proof=ensure_once(ctx.design_dir/'S1_IMPORT_PROVENANCE.json',dict(evidence=[bind(old/'TREATMENT.json'),bind(old/'S1_IMPORT_PROVENANCE.json'),bind(old/'RECONCILIATION.json')],originals_read_only=True))
        t=ensure_once(ctx.design_dir/'TREATMENT.json',dict(treatment,full_year_declaration=d))
        for name in ('RECONCILIATION.json','CODING_LOCK.json','HUB_RULING.json','OFFICIAL_AUDIT.json','THIRD_READ.json','SOURCES.json'):
            if (old/name).exists():ensure_once(ctx.design_dir/name,read(old/name))
        r=StageResult('S1',key,Disposition.READY,'Validated prior legal evidence and prospective explicit cessation/convention declaration',artifacts=(artifact(proof,'provenance'),artifact(t,'treatment')))
        ensure_once(ctx.design_dir/'receipts/S1/0000000000000000000.json',dict(card_sha256=sha(path),result=r.as_dict()))
        for stage in ('S2','S3','S4'):
            begin=time.perf_counter();r=run_stage(ctx,stage);times[stage]=time.perf_counter()-begin
            if r.disposition!=Disposition.READY:raise ValueError(stage+': '+r.reason)
        primary=ctx.read('SUPPORT.json')['variants'][0]
        text_once(ctx.design_dir/'SPEC.md','# '+key+' V5 prospective repair\n\n'+json.dumps(card['analysis']['registration'],indent=2)+'\n\nReversal handling: '+json.dumps(d['exposure_end_dates'])+'; primary drops reversed states, retain_ended_exposure censors partial and later years.\nConvention-filled controls: '+json.dumps(d['convention_filled_controls'])+'\nPrimary treated/never: '+str(len(primary['retained_treated']))+'/'+str(len(primary['never_controls']))+'\n')
        unchanged=primary_forecast_target(read(old/'SPEC.json'))==primary_forecast_target(ctx.read('SPEC.json'))
        for p in old.glob('predictions/*/*'):
            if p.is_file():originals[str(p)]=sha(p)
        if unchanged:
            import_unchanged_predictions(old,ctx.design_dir,list(PERSONAS))
            ensure_once(ROOT/'freeze_cards'/(key+'.json'),card)
            forecast_status='UNCHANGED_IMPORTED_5'
        else:
            ensure_once(ctx.design_dir/'FORECASTS_SUPERSEDED.json',dict(status='SUPERSEDED',reason='Primary target cell inventory changed: Missouri removed; treated10 ->9',
                old_spec=bind(old/'SPEC.json'),new_spec=bind(ctx.design_dir/'SPEC.json'),original_files={str(p):sha(p) for p in old.glob('predictions/*/*') if p.is_file()},forecasts=5))
            for role,p in templates.items():
                template=copy.deepcopy(read(OLD/'templates'/key/(role+'.json')))
                template.update(outcome_spec_id=key+'_v5',spec_path=str(ctx.design_dir/'SPEC.md'),spec_sha256=sha(ctx.design_dir/'SPEC.md'),
                    spec_json_path=str(ctx.design_dir/'SPEC_DRAFT.json'),spec_json_sha256=sha(ctx.design_dir/'SPEC_DRAFT.json'),methods_path=str(methods),methods_sha256=sha(methods))
                ensure_once(Path(p),template)
            jobs=emit_stage_jobs(ctx,'S5',jobs_root=ROOT/'forecast_jobs')
            forecast_status='SUPERSEDED_5_NEW_JOBS'
            ensure_once(ctx.design_dir/'FORECAST_JOBS.json',dict(jobs=[j.job_path for j in jobs],dispatched=False))
        emit_stage_jobs(ctx,'S6',allow_draft=True,jobs_root=ROOT/'draft_jobs',attempt_id='v5-readiness')
        results.append(dict(design_id=key,version=version,forecasts=forecast_status,
            primary_treated=len(primary['retained_treated']),primary_never=len(primary['never_controls']),primary_support=primary['support_pass'],
            stage_timings_seconds=times,spec_sha256=sha(ctx.design_dir/'SPEC.json'),spec_path=str(ctx.design_dir/'SPEC.json')))
    ensure_once(ROOT/'ORIGINAL_HASHES.json',originals)
    care=OLD/'reconciliation/run1_refundable_care_credit/v1/THIRD_READ.json'
    ensure_once(ROOT/'PREPARATION.json',dict(cards=results,care_credit=dict(status='PARKED',adequate_treated=4,minimum=6,third_read=bind(care)),
        real_estimation_calls=0,outcome_association_calls=0,new_downloads=[],dispatched=False,model_calls=0))
    print(json.dumps(results,indent=2))


if __name__=='__main__':prepare()
