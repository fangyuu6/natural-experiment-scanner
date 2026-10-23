"""Batch4 archival acceptance: validated S1 import and S2/S3 reproduction.

Archival material is evidence only. No agent is dispatched, numeric outcome tail
is parsed, or frozen input/output modified. Reports separate semantic equality
from intentional layout/schema/path differences. Original specifications are
verified read-only; this replay earns no prospective S0/S4 readiness.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
from .cards import load_card, validate_card
from .contracts import StageContext, StageResult, Disposition
from .storage import sha, ensure_once, artifact, public_input, output_root, encoded
from .opaque import opaque_rows

ROOT=Path(__file__).resolve().parents[2]
CASES=('pay_secrecy','pregnancy_accommodation')


def read(path: Path): return json.loads(public_input(path).read_text())


def archival_support(declaration, variant, checks):
    """Replay historical metadata with its unchanged batch4 constructor.

    The live constructor enforces C51; this isolated acceptance path grants no
    prospective readiness and never fits or decodes historical outcome values.
    """
    from scripts.l2_batch4.specs.structural_support import support
    from .full_year import effective_full_year_declaration
    if 'min_post_periods' in declaration:
        raise ValueError('Registered prospective post gates cannot use historical replay')
    d,routed=effective_full_year_declaration(declaration,variant)
    summary,required=support(d,routed,{})
    summary['variant']=variant;summary['checks'].update(checks)
    if variant=='include_scope_ambiguous' and d['scope_ambiguous_starts']:
        summary['checks']['mechanism_scope_certified']=False
    summary['support_pass']=all(summary['checks'].values());summary['source_ready']=d['source_ready']
    return summary,required


def reproduce_archival_support(ctx):
    """Bind the historical topology to freshly copied opaque archival panels."""
    import csv
    from .variants import registered_cards
    summaries=[];cards=registered_cards(dict(ctx.card.payload))
    for panel in ctx.read('PANELS.json')['variants']:
        name=panel['variant'];card=cards[name]
        summary,required=archival_support(card['treatment']['full_year_declaration'],
                                          card['analysis']['full_year_variant'],ctx.read('TREATMENT.json')['source_checks'])
        directory=ctx.design_dir/'panels'/name
        with (directory/'metadata.csv').open(newline='') as handle:
            keys={(r['unit'],int(r['year'])) for r in csv.DictReader(handle)}
        if keys!=required:raise ValueError('Archival exact mask differs')
        summary.update(panel_path=str(directory/'panel.csv'),panel_sha256=sha(directory/'panel.csv'),
            metadata_path=str(directory/'metadata.csv'),metadata_sha256=sha(directory/'metadata.csv'),
            inventory_test_pass=True,state='SUPPORTED' if summary['support_pass'] else 'UNSUPPORTED',
            passed=summary['support_pass'],disposition='ready' if summary['support_pass'] else 'stopped')
        summaries.append(summary)
    primary=next(r for r in summaries if r['variant']=='primary')
    ensure_once(ctx.design_dir/'SUPPORT.json',dict(variants=summaries,primary_passed=primary['support_pass'],
        adapter='archival_batch4_read_only',outcome_values_parsed=0,prospective_readiness_granted=False))


def validate_archival_s1(repo: Path, key: str, declaration: dict) -> tuple[dict,dict]:
    """Validate original coding locks, canonical rulings and prelocked V3 evidence.

    This is an import attestation, not a fabricated fresh coder/auditor receipt.
    Blindness is historical self-attestation; hashes independently verify bytes.
    """
    base=repo/'scratchpad/l2_batch4';reconciled=base/'reconciled'/key
    evidence=[]
    def bind(p):
        p=public_input(p);evidence.append({'path':str(p.relative_to(repo)),'sha256':sha(p),'size_bytes':p.stat().st_size});return read(p)
    declared=bind(base/'DECLARATIONS.json')[key]
    convention=bind(reconciled/'COMMON_CONVENTION.json')
    # The archived never pool is explicitly a convention inventory. Enrich the
    # import adapter only, after checking the original ruling and boundary;
    # generic declarations cannot infer that every never control was filled.
    convention_controls = []
    if convention.get('absence_is_convention_not_statute_negative_proof') is True:
        if convention.get('absence_boundary') != f"{declared['window'][1]}-12-31":
            raise ValueError('Archival convention boundary differs from registered window')
        convention_controls = list(declared['never_controls'])
    imported = dict(declaration)
    if 'convention_filled_controls' not in declared and 'convention_filled_controls' in imported:
        if imported.pop('convention_filled_controls') != convention_controls:
            raise ValueError('Declaration differs from verified archival convention inventory')
    if declared!=imported: raise ValueError('Declaration differs from archival source')
    verification=bind(base/'BLIND_LOCK_VERIFICATION.json')
    if verification.get('pass') is not True: raise ValueError('Historical lock verification failed')
    locked=[]
    for role in ('A','B'):
        directory=base/'blind'/key/role;lock=bind(directory/'LOCK.json')
        hashes=lock.get('files',lock.get('sha256',{}))
        if not hashes: raise ValueError('Empty historical coder lock')
        for name,digest in hashes.items():
            if Path(name).name!=name: raise ValueError('Historical lock name escapes role directory')
            p=public_input(directory/name)
            if sha(p)!=digest: raise ValueError('Historical locked coder file changed')
            proof=[r for r in verification['files'] if r['file']==str(p.relative_to(repo))]
            if len(proof)!=1 or proof[0].get('pass') is not True or proof[0]['sha256']!=digest: raise ValueError('Historical lock index mismatch')
            item={'path':str(p.relative_to(repo)),'sha256':digest,'role':role};locked.append(item);evidence.append(item)
    canonical=bind(reconciled/'CANONICAL.json');comparison=bind(reconciled/'COMPARISON.json')
    if canonical['operative']!=declaration['operative'] or canonical['never_controls']!=declaration['never_controls'] or canonical['exclusions']!=declaration['exclusions']: raise ValueError('Canonical declaration mismatch')
    if canonical.get('source_audit_pending') is not False or convention.get('original_minimal_tuples_unchanged') is not True: raise ValueError('Archival reconciliation incomplete')
    if not (comparison.get('blind_originals_retained') is True or comparison.get('originals_preserved') is True): raise ValueError('Archival original preservation not attested')
    audit=bind(reconciled/'AUDIT_SUMMARY.json');selection=bind(reconciled/'AUDIT_SAMPLE.json')
    bound=audit.get('sample_sha256',audit.get('sample_frozen_sha256'))
    observations=audit.get('observations',[]);sample=[r['unit'] for r in selection.get('samples',[])]
    if bound!=sha(reconciled/'AUDIT_SAMPLE.json') or len(sample)!=10 or len(set(sample))!=10 or {r['unit'] for r in observations}!=set(sample) or len(observations)!=10: raise ValueError('Historical seeded V3 sample binding failed')
    errors=sum(r.get('known_error',r.get('error',False)) is True for r in observations)
    unresolved=sum(r.get('unresolved') is True for r in observations)
    if errors!=audit['known_errors'] or unresolved!=audit['unresolved']: raise ValueError('Historical V3 counts differ from observations')
    if any(not (r.get('official_locator') or r.get('exact_locator')) for r in observations): raise ValueError('Official locator missing from V3 evidence')
    with (base/'DOWNLOADS.csv').open(newline='') as handle: downloads=list(csv.DictReader(handle))
    names={'iwpr2021.pdf','harris2018.pdf'} if key=='pay_secrecy' else {'pregnancy2019.pdf','pregnancy2022.pdf'}
    compiled=[r for r in downloads if Path(r['path']).name in names]
    if len(compiled)!=len(names): raise ValueError('Compiled historical sources missing')
    for item in compiled:
        p=public_input(repo/item['path'])
        if sha(p)!=item['sha256'] or p.stat().st_size!=int(item['size_bytes']): raise ValueError('Archived compiled source binding changed')
        evidence.append({'path':item['path'],'sha256':item['sha256'],'size_bytes':int(item['size_bytes']),'url':item['url'],'license':item['license'],'read_mode':'Hash only; archived source content not ingested.'})
    bind(base/'review/SOURCE_SCAN_REVIEW.json')
    source=declaration['source'];end=declaration['window'][1]
    checks={'source_audit_pass':declaration['source_ready'] is True and audit.get('pass') is True and audit.get('n')==10 and audit.get('sample_locked_before_lookup') is True and selection.get('sample_locked_before_lookup') is True and errors<=2 and unresolved<=5,
        'complete_list_history_and_boundary':source.get('complete_list') is True and bool(source.get('historical_meaning')) and bool(source.get('absence_rule')) and bool(source.get('url')) and source.get('coverage_end','')>=f'{end}-12-31',
        'mechanism_scope_certified':declaration.get('mechanism_scope_certified') is True,'absorbing_exposure_certified':declaration.get('absorbing_exposure_certified') is True}
    for field in ('independent_coding_verified','outcome_linkage_verified','free_public_data_verified','target_population_verified','confounding_reviewed'): checks[field]=declaration.get(field) is True
    checks['legal_identity_verified']=declaration.get('representative_legal_identity',{}).get('verified') is True
    if not all(checks.values()): raise ValueError('Archival S1 contract fails: '+str([k for k,v in checks.items() if not v]))
    # All evidence paths are hashed imports. Sources are not promoted to authority.
    return checks,{'schema':'l2_archival_s1_import_v1','source_batch':'batch4','design':key,'locked_originals':locked,'evidence':evidence,
        'original_agreement_unchanged':True,'reconciliation_after_lock':True,'blindness':'Historical original role attestations only; no claim of a fresh blind rerun.',
        'audit_seed':selection['seed'],'audit_sample':sample,'known_errors':errors,'unresolved':unresolved,
        'convention_filled_controls':convention_controls,
        'adapter_enrichment':'Explicit convention inventory from the hash-bound historical common ruling; original declaration bytes retained.',
        'source_checks':checks,'outcome_values_parsed':0,'agents_dispatched':0,'hub_signatures_created':0}


def create_cards(repo: Path, cards_dir: Path) -> list[Path]:
    ds=read(repo/'scratchpad/l2_batch4/DECLARATIONS.json');manifest=read(repo/'scripts/l2_batch4/specs/SENSITIVITY_MANIFEST.json');lineage=read(repo/'scripts/l2_batch4/specs/LINEAGE.json')
    with (repo/'scratchpad/l2_batch4/DOWNLOADS.csv').open(newline='') as handle: downloads=list(csv.DictReader(handle))
    paths=[]
    for key in CASES:
        d=ds[key];checks,proof=validate_archival_s1(repo,key,d)
        d=dict(d, convention_filled_controls=proof['convention_filled_controls'])
        source_meta=public_input(repo/d['metadata_path'])
        if sha(source_meta)!=d['metadata_sha256'] or sha(public_input(repo/d['outcome_path']))!=d['outcome_sha256']: raise ValueError('Archival outcome/metadata input changed')
        with source_meta.open(newline='') as handle: metadata=list(csv.DictReader(handle))
        if len({(r['unit'],int(r['year'])) for r in metadata})!=len(metadata): raise ValueError('Duplicate annual source metadata')
        draft=read(repo/f'scripts/l2_batch4/specs/{key}_v1.json')
        if sha(repo/f'scripts/l2_batch4/specs/{key}_v1.json')!=lineage[key]['draft_json_sha256']: raise ValueError('Archival registration binding changed')
        variants=[r['variant'] for r in manifest['panels'] if r['key']==key]
        if len(set(variants))!=len(variants) or 'primary' not in variants: raise ValueError('Archival variant inventory invalid')
        masks={}
        for variant in variants:
            _,required=archival_support(d,variant,checks)
            p=ensure_once(cards_dir/'masks'/key/(variant+'.json'),[[u,y] for u,y in sorted(required)])
            masks[variant]={'mask_path':str(p.resolve()),'mask_sha256':sha(p)}
        names={'iwpr2021.pdf','harris2018.pdf'} if key=='pay_secrecy' else {'pregnancy2019.pdf','pregnancy2022.pdf'}
        compiled=[r for r in downloads if Path(r['path']).name in names]
        if len(compiled)!=len(names): raise ValueError('Compiled historical source inventory incomplete')
        sources=[]
        for item in compiled:
            source=public_input(repo/item['path'])
            if sha(source)!=item['sha256'] or source.stat().st_size!=int(item['size_bytes']): raise ValueError('Compiled historical source bytes changed')
            primary=item['url']==d['source']['url']
            sources.append({'source_id':'archival_complete_list' if primary else Path(item['path']).stem,'url':item['url'],'locator':d['source']['locator'],'license':item['license'],'coverage_end':d['source']['coverage_end'],'complete_list':primary,'path':str(source.resolve()),'sha256':item['sha256']})
        filters={'employment_ownership' :'private','industry':'10'} if key=='pay_secrecy' else {'age_min':20,'age_max':44,'sex':'female','civilian_noninstitutional':True}
        universe=sorted({r['unit'] for r in metadata})
        card={'schema_version':'1.0','version':'v1','design_id':key,'policy_family':d['policy_family']['policy_family_id'],'mechanism':d['predicate'],'track':'DiD','unit':'state','outcome_family':'wage' if key=='pay_secrecy' else 'employment',
            'sources':sources,
            'lineage':{'queue_version':'l2_batch4_V3_v1','screen_version':'new_l1_documented_labor:V3','source_text_ids':[lineage[key]['verified_representative_source']],'legal_instrument_ids':[lineage[key]['representative_legal_id']]},
            'population':{'description':d['population_alignment'],'filters':filters},
            'outcome':{'connector':'opaque_state_year','paths':[str((repo/d['outcome_path']).resolve())],'source_sha256':d['outcome_sha256'],'years':d['window'],'units':universe,'frequency':'annual','population_filters':filters,'outcome_unit':d['outcome_unit'],**masks['primary']},
            'treatment':{'universe':universe,'seed':proof['audit_seed'],'complete_list_source_id':'archival_complete_list','predicate':d['predicate'],'coverage_end':d['source']['coverage_end'],'historical_convention':d['source']['historical_meaning'],'full_year_declaration':d},
            'analysis':{'support_adapter':'annual_full_year_v1','support_metadata_path':str(source_meta.resolve()),'support_metadata_sha256':d['metadata_sha256'],'full_year_variant':'primary','min_pre_periods':3,'min_controls':5,'min_cohorts':2,'min_treated':6,'estimand':draft['specification']['estimand'],'clock':'first_wholly_exposed_year','controls':'never_treated','weights':draft['specification']['weights'],'inference':draft['specification']['inference'],'diagnostics':['pretrend_joint','pretrend_economic','placebo'],'stop_rules':['support','registered_calibration','fail_closed_integrity'],'interval_path':['Webb','CT_complete_control_path','point_plus_RI_if_uncertified'],'registration':draft['specification']},
            'sensitivities':[{'name':v,'changes':{'outcome':masks[v],'analysis':{'full_year_variant':v}}} for v in variants if v!='primary'],
            'automation':{'archival_s1_import':True,'registration_identity':{k:v for k,v in draft.items() if k!='specification'},'prediction_templates':{persona:f'scripts/l2_batch4/specs/predictions/{key}/{persona}.template.json' for persona in ('Card','Finkelstein','Roth','Liebman','Boehmke')},'methods_path':'scripts/l2_batch4/specs/METHODS_V3.md'}}
        errors=validate_card(card)
        if errors: raise ValueError('Acceptance card invalid: '+str(errors))
        paths.append(ensure_once(cards_dir/(key+'.json'),card))
    return paths


def import_s1(ctx: StageContext) -> None:
    checks,proof=validate_archival_s1(ctx.repo,ctx.card.design_id,ctx.card.payload['treatment']['full_year_declaration'])
    proof_path=ensure_once(ctx.design_dir/'S1_IMPORT_PROVENANCE.json',proof)
    declaration=ctx.card.payload['treatment']['full_year_declaration']
    records=[{'unit':u,'status':'never_treated' if u in declaration['never_controls'] else 'treated' if u in declaration['operative'] else 'unknown','start_period':declaration['operative'].get(u),'mechanism':ctx.card.mechanism,'locator':'archival canonical/rulings; preserved original comparison separately'} for u in ctx.card.payload['treatment']['universe']]
    p=ensure_once(ctx.design_dir/'TREATMENT.json',{'records':records,'full_year_declaration':declaration,'source_checks':checks,'import_provenance_sha256':sha(proof_path),'archival_import':True})
    result=StageResult('S1',ctx.card.design_id,Disposition.READY,'Validated archival source-ready import; no fresh agent claim',artifacts=(artifact(proof_path,'archival_import_provenance'),artifact(p,'treatment')),metrics={'fresh_coders_run':0,'outcome_values_parsed':0})
    ensure_once(ctx.design_dir/'receipts/S1/0000000000000000000.json',{'card_sha256':sha(ctx.card.path),'result':result.as_dict()})



def compare_batch4(ctx: StageContext) -> dict:
    baseline=read(ctx.repo/'scripts/l2_batch4/specs/SUPPORT.json')['cases'][ctx.card.design_id]['variants']
    current={r['variant']:r for r in ctx.read('SUPPORT.json')['variants']};diffs=[];panels=[]
    if set(current)!=set(baseline): diffs.append({'type':'variant_names','baseline':sorted(baseline),'pipeline':sorted(current)})
    for name in sorted(set(baseline)&set(current)):
        old=baseline[name];new=current[name]
        row={'variant':name,'panel_bytes_equal':sha(public_input(ctx.repo/old['panel_path']))==new['panel_sha256'],'metadata_bytes_equal':sha(public_input(ctx.repo/old['metadata_path']))==new['metadata_sha256'],'state_equal':old['state']==new['state']}
        panels.append(row)
        for field,value in old.items():
            if field in {'panel_path','metadata_path'}: continue
            if new.get(field)!=value: diffs.append({'variant':name,'field':field,'baseline':value,'pipeline':new.get(field)})
        if not all(v for k,v in row.items() if k!='variant'): diffs.append({'type':'panel','details':row})
    spec_path=ctx.repo/f'scripts/l2_batch4/specs/{ctx.card.design_id}_v1.json'
    old_spec=read(spec_path)
    lineage=read(ctx.repo/'scripts/l2_batch4/specs/LINEAGE.json')[ctx.card.design_id]
    spec_hash=sha(spec_path)
    bound=spec_hash==lineage['draft_json_sha256']
    if not bound: raise ValueError('Archival registration binding changed')
    # Verify the imported historical declaration against the original bytes.
    # This is not a generated S4 specification or a new registration draft.
    imported_registration=dict(ctx.card.payload['automation']['registration_identity'],
                               specification=ctx.card.payload['analysis']['registration'])
    spec_equal=old_spec==imported_registration
    if not spec_equal: diffs.append({'type':'archival_spec_import','baseline':old_spec,'imported':imported_registration})
    spec_verification={'path':str(spec_path.relative_to(ctx.repo)), 'sha256':spec_hash,
                       'lineage_bound':bound, 'imported_registration_semantically_equal':spec_equal,
                       'read_only':True, 'prospective_spec_generated':False}
    return {'design':ctx.card.design_id,'equivalent':not diffs,'variants':len(current),'unsupported_variants':[k for k,v in current.items() if not v['support_pass']],
        'primary_counts':{'treated':len(current['primary']['retained_treated']),'controls':len(current['primary']['never_controls']),'cohorts':sum(not r['dropped'] for r in current['primary']['cohort_checks'])},
        'panels':panels,'scientific_diffs':diffs,'archival_spec_verification':spec_verification,
        'prospective_readiness_granted':False,
        'intentional_format_differences':['Paths rebind to task-owned acceptance tree.','Pipeline SUPPORT uses a stage inventory envelope and adds complete metadata cell inventories.','Original frozen specification semantics are verified read-only against the archival import; no new S4 specification is generated.','S1 records an explicit archival validated import, preserving historical original agreements and scope rulings.'],
        'outcome_values_parsed':0,'outcome_association_calls':0,'estimations':0,'agents_dispatched':0}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo',type=Path,default=ROOT);parser.add_argument('--cards-dir',type=Path,default=ROOT/'data/l2_pipeline/cards/batch4');parser.add_argument('--work-dir',type=Path,default=ROOT/'data/l2_pipeline/acceptance/batch4');args=parser.parse_args(argv)
    repo=args.repo.resolve();cards=output_root(args.cards_dir,repo);workspace=output_root(args.work_dir,repo)
    from .cli import run_stage
    reports=[]
    for card_path in create_cards(repo,cards):
        ctx=StageContext(load_card(card_path),workspace,repo);import_s1(ctx)
        for stage in ('S2',):
            result=run_stage(ctx,stage)
            if result.disposition!=Disposition.READY: raise ValueError(stage+' failed: '+result.reason)
        reproduce_archival_support(ctx)
        report=compare_batch4(ctx);ensure_once(ctx.design_dir/'ACCEPTANCE_DIFF.json',report);reports.append(report)
    summary={'schema':'l2_batch4_archival_acceptance_v2','equivalent':all(r['equivalent'] for r in reports),'cases':reports,'panel_count':sum(r['variants'] for r in reports),'unsupported_count':sum(len(r['unsupported_variants']) for r in reports),'new_downloads':[],'outcome_values_parsed':0,'agents_dispatched':0,'prospective_readiness_granted':False,'prospective_specs_generated':0,'stage_stop':'S3 archival panel/support reproduction; original specifications verified read-only; prospective S0/S4 require first-order registration'}
    ensure_once(workspace/'ACCEPTANCE_REPORT.json',summary)
    print(encoded({'equivalent':summary['equivalent'],'panel_count':summary['panel_count'],'unsupported_count':summary['unsupported_count'],'report':str(workspace/'ACCEPTANCE_REPORT.json')}).decode())
    return 0 if summary['equivalent'] else 1

if __name__=='__main__': raise SystemExit(main())
