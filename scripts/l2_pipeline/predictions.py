"""Blind forecast locking retains original bytes and declared persona roster."""
from __future__ import annotations
import json, math
from pathlib import Path
from .storage import local, sha, write_once, ensure_once


def primary_forecast_target(spec: dict) -> dict:
    """Scientific forecast identity, excluding implementation/version hashes.

    The supported cohort-time inventory binds the target population and ATT
    weights. A changed primary sample therefore requires fresh forecasts even
    when the prose estimand has not changed.
    """
    primary=next(r for r in spec['support']['variants'] if r['variant']=='primary')
    cells=primary.get('cell_inventory',primary.get('cells',[]))
    post=[dict(cohort=c.get('g',c.get('cohort')),period=c.get('t',c.get('period')),
               base_period=c.get('base_period'),treated=sorted(c['treated']),controls=sorted(c['controls']))
          for c in cells if c.get('post',True)]
    post.sort(key=lambda c:(c['cohort'],c['period']))
    return dict(design_id=spec['design_id'],track=spec['track'],population=spec['population'],
        outcome={k:spec['outcome'].get(k) for k in ('outcome_unit','outcome_id','value_field','population_filters','years','frequency')},
        analysis={k:spec['analysis'].get(k) for k in ('estimand','weights','controls','clock')},
        cells=post)


def import_unchanged_predictions(source: Path, target: Path, personas: list[str], added_sensitivity: str | None = None) -> Path:
    """Rebind collected forecasts only after exact primary-target equality.

    Originals stay byte-identical. New normalized aliases and collection
    receipts explicitly record the old/new specification and source receipts.
    """
    source,target=Path(source).resolve(),Path(target).resolve()
    old=json.loads((source/'SPEC.json').read_text());new=json.loads((target/'SPEC.json').read_text())
    if primary_forecast_target(old)!=primary_forecast_target(new):
        raise ValueError('Primary forecast estimand/sample changed; supersede forecasts and emit new jobs')
    proof={'schema':'l2_unchanged_forecast_import_v1','source_spec_path':str(source/'SPEC.json'),
        'source_spec_sha256':sha(source/'SPEC.json'),'target_spec_sha256':sha(target/'SPEC.json'),
        'primary_target_unchanged':True,'scientific_values_changed':False,'personas':personas,'files':{}}
    if added_sensitivity is not None:
        # C57 authorised added sensitivity (c57.apply_addition): report-only, never forecast; the contract-card comparison ignores this one SUPPORT row.
        names=[r['variant'] for r in new['support']['variants']]
        if names.count(added_sensitivity)!=1 or added_sensitivity in [r['variant'] for r in old['support']['variants']]:
            raise ValueError('Added sensitivity must be exactly one new SUPPORT row')
        proof['added_sensitivity']=added_sensitivity
    for persona in personas:
        old_dir=local(source,'predictions/'+persona);new_dir=local(target,'predictions/'+persona)
        value=json.loads((old_dir/'submission.json').read_text())
        receipt=json.loads((old_dir/'COLLECTION.json').read_text())
        from .forecast_snapshot import locked_relative
        lock_path=source/'PREDICTIONS_LOCK.json'
        original=(local(source,locked_relative(json.loads(lock_path.read_text()),persona,receipt))
                  if lock_path.is_file() else Path(receipt['original_path']).resolve())
        if (not original.is_relative_to(source) or receipt.get('validated') is not True
                or receipt.get('role')!=persona or receipt.get('design_id')!=new['design_id']
                or receipt.get('stage')!='S5' or receipt.get('original_sha256')!=sha(original)
                or receipt.get('promoted_sha256')!=sha(old_dir/'submission.json')
                or value.get('spec_sha256')!=sha(source/'SPEC.json')):
            raise ValueError('Collected source forecast binding changed')
        new_dir.mkdir(parents=True,exist_ok=True);copy=new_dir/original.name
        if copy.exists():
            if copy.read_bytes()!=original.read_bytes(): raise ValueError('Imported original changed')
        else: copy.write_bytes(original.read_bytes())
        normalization=dict(value.get('normalization',{}),pipeline_spec_sha256=sha(target/'SPEC.json'),
            source_pipeline_spec_sha256=sha(source/'SPEC.json'),primary_target_unchanged=True,
            source_collection_path=str(old_dir/'COLLECTION.json'),source_collection_sha256=sha(old_dir/'COLLECTION.json'))
        promoted=ensure_once(new_dir/'submission.json',dict(value,spec_sha256=sha(target/'SPEC.json'),normalization=normalization))
        ensure_once(new_dir/'NORMALIZATION.json',normalization)
        ensure_once(new_dir/'COLLECTION.json',dict(receipt,original_path=str(copy),original_sha256=sha(copy),
            promoted_path=str(promoted),promoted_sha256=sha(promoted),forecast_import=normalization))
        proof['files'][str(original)]=sha(original)
        proof['files'][str(old_dir/'COLLECTION.json')]=sha(old_dir/'COLLECTION.json')
    # Q3 ruling 3a (HUB_RULING_20261023_PARK_REOPEN.md): carry the source lock's independence record into the write-once proof. An import proof written
    # before this fix is never rewritten (resume stays idempotent), so it simply carries nothing.
    existing=target/'FORECAST_IMPORT.json'
    if not existing.is_file() or 'independence_carried' in json.loads(existing.read_text()):
        from .forecast_slots import carried_independence
        carried=carried_independence(source/'PREDICTIONS_LOCK.json')
        if carried is not None: proof['independence_carried']=carried
    return ensure_once(existing,proof)

def _imported_independence(imported: dict, personas: list[str], slots: dict, values: list) -> dict:
    """Lock keys for an unchanged-forecast import: recompute independence from the imported (byte-identical) submissions and require the source's numbers."""
    from .forecast_slots import independence,carried_independence,worker_ids
    carried=imported['independence_carried']
    if imported.get('schema')!='l2_unchanged_forecast_import_v1' or list(carried.get('personas',[]))!=list(personas):
        raise ValueError('Carried independence record does not match the imported roster')
    lock_path=Path(carried['source_lock_path'])
    if not lock_path.is_file() or sha(lock_path)!=carried['source_lock_sha256']: raise ValueError('Source forecast lock changed after import')
    if carried_independence(lock_path,carried.get('source_attempt'))!=carried: raise ValueError('Carried independence record differs from the source lock')
    source=json.loads(lock_path.read_text())
    if set(slots)!=set(personas) or slots!=carried['forecast_slots']: raise ValueError('Imported forecast slot provenance differs from the source lock')
    if worker_ids(slots) is None: raise ValueError('Forecast author provenance must be non-null')
    if len({s['pack_sha256'] for s in slots.values()})!=len(personas): raise ValueError('Forecast pack hashes must differ pairwise')
    fresh=independence(values,slots)
    if (fresh['n_eff']!=source.get('n_eff') or fresh['SINGLE_CONSENSUS']!=source.get('SINGLE_CONSENSUS') or fresh['n_workers']!=carried['n_workers']
            or (source.get('n10_eligible') is False and fresh['n10_eligible'])):
        raise ValueError('Recomputed independence differs from the source lock')
    return dict(forecast_slots=slots,**fresh,independence_source=dict(source_attempt=carried['source_attempt'],source_lock_path=carried['source_lock_path'],
        source_lock_sha256=carried['source_lock_sha256'],forecast_import='FORECAST_IMPORT.json'))

def lock_predictions(root: Path, personas: list[str], design_id: str, spec_sha256: str) -> Path:
    if not personas or len(set(personas))!=len(personas): raise ValueError("Invalid persona roster")
    files={}; original_files={}; values=[]; slots={}
    for persona in personas:
        relative="predictions/"+persona+"/submission.json"; p=local(root,relative)
        value=json.loads(p.read_text())
        if value.get("persona")!=persona or value.get("design_id")!=design_id or value.get("spec_sha256")!=spec_sha256: raise ValueError("Prediction identity/binding mismatch")
        if value.get("source_type")=="published_did_study":
            known=value.get("published_estimate_known")
            if type(known) is not bool or value.get("blindness_scope")!="our_outcomes_and_estimates_v7":
                raise ValueError("Literature forecast needs V7 knowledge/scope disclosure")
            proof=value.get("v7_blindness_validation",{})
            if (proof.get("published_results_read") is not known or any(proof.get(k) is not False
                    for k in ("other_submissions_read","our_outcome_data_read","our_estimates_read"))):
                raise ValueError("Literature forecast V7 blindness unconfirmed")
        required={"point","interval","interval_level","rationale","disclosures"}
        if not required<=set(value) or not isinstance(value["disclosures"],list): raise ValueError("Incomplete prediction ledger")
        numbers=[value["point"],value["interval_level"],*value["interval"]]
        if len(value["interval"])!=2 or any(type(n) not in {int,float} or not math.isfinite(n) for n in numbers): raise ValueError("Invalid prediction values")
        if value["interval"][0]>value["interval"][1] or not 0<value["interval_level"]<1: raise ValueError("Invalid prediction interval")
        values.append(value)
        if value.get("forecast_slot"): slots[persona]=value["forecast_slot"]
        files[relative]=sha(p)
        collection_path=local(root,"predictions/"+persona+"/COLLECTION.json")
        if collection_path.is_file():
            collection=json.loads(collection_path.read_text())
            original=Path(collection["original_path"]).resolve()
            if not original.is_relative_to(root.resolve()): raise ValueError("Original forecast escapes study root")
            if collection.get("validated") is not True or collection.get("stage")!="S5" or collection.get("role")!=persona or collection.get("design_id")!=design_id or collection.get("promoted_sha256")!=sha(p) or collection.get("original_sha256")!=sha(original): raise ValueError("Original forecast collection binding mismatch")
            original_files[str(original.relative_to(root.resolve()))]=sha(original)
            original_files[str(collection_path.resolve().relative_to(root.resolve()))]=sha(collection_path)
            normalization=local(root,"predictions/"+persona+"/NORMALIZATION.json")
            if normalization.is_file(): original_files[str(normalization.resolve().relative_to(root.resolve()))]=sha(normalization)
    bundle_collection=local(root,"predictions/forecast_bundle/COLLECTION.json")
    if bundle_collection.is_file():
        # REDESIGN R2: the bundle original and its receipt are frozen with the members it produced.
        receipt=json.loads(bundle_collection.read_text())
        original=Path(receipt["original_path"]).resolve()
        if (not original.is_relative_to(root.resolve()) or receipt.get("validated") is not True or receipt.get("role")!="forecast_bundle"
                or receipt.get("design_id")!=design_id or receipt.get("original_sha256")!=sha(original)
                or set(receipt.get("members",{}))!=set(personas)):
            raise ValueError("Forecast bundle collection binding mismatch")
        original_files[str(original.relative_to(root.resolve()))]=sha(original)
        original_files[str(bundle_collection.resolve().relative_to(root.resolve()))]=sha(bundle_collection)
    extra={}
    if (root/'FORECAST_SLOTS.json').exists():
        from .forecast_slots import independence,same_model,validate_contract
        validate_contract(root,personas)
        registered=json.loads((root/'FORECAST_SLOTS.json').read_text())
        if personas!=registered['personas'] or set(slots)!=set(personas):raise ValueError('Incomplete forecast slot provenance')
        for role,slot in slots.items():
            expected=registered['slots'][role]
            if any(slot.get(k)!=expected[k] for k in ('slot','pack_sha256')) or not same_model(expected['model_id'],slot.get('model_id')) or not slot.get('prompt_sha256'):
                raise ValueError('Forecast slot identity/hash mismatch')
            author=slot.get('author_provenance')
            if not isinstance(author,dict) or not author.get('worker_id') or not same_model(slot['model_id'],author.get('model_id')):
                raise ValueError('Forecast author provenance must be non-null')
        if len({s['pack_sha256'] for s in slots.values()})!=len(personas):raise ValueError('Forecast pack hashes must differ pairwise')
        workers={s['author_provenance']['worker_id'] for s in slots.values()}
        if all(v.get('single_author_bundle') is True for v in values):
            if len(workers)!=1:raise ValueError('Bundled forecast slots require one author')   # REDESIGN R2
        elif len(workers)!=len(personas):raise ValueError('Forecast slots require distinct authors')
        extra=dict(forecast_slots=slots,**independence(values,slots))
    elif (root/'FORECAST_IMPORT.json').is_file():
        imported=json.loads((root/'FORECAST_IMPORT.json').read_text())
        if isinstance(imported,dict) and 'independence_carried' in imported: extra=_imported_independence(imported,personas,slots,values)
    return write_once(root/"PREDICTIONS_LOCK.json",{"personas":personas,"files":files,"original_files":original_files,"design_id":design_id,"spec_sha256":spec_sha256,**extra})
