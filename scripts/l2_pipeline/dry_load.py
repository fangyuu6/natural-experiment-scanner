"""Full registered input loading with no estimator, scoring or result writing."""
import json
from pathlib import Path
from .storage import ensure_once, sha


def inventory(root, files):
    """Frozen structural inventory only; no numeric outcome fields are read."""
    root=Path(root)
    if not {'PANELS.json','SUPPORT.json'} <= set(files): return []
    from .hygiene import inventory_names
    support_name,panel_name=inventory_names(root)
    panels={r['variant']:r for r in json.loads((root/panel_name).read_text())['variants']}
    support={r['variant']:r for r in json.loads((root/support_name).read_text())['variants']}
    if 'SECONDARY_PANELS.json' in files:
        from .variants import secondary_name
        for row in json.loads((root/'SECONDARY_PANELS.json').read_text())['secondaries']:
            name=secondary_name(dict(outcome=row['outcome']))
            if name not in panels:
                panels[name]=row['construction']
                support[name]=json.loads((root/'secondary_panels'/row['outcome']/'SUPPORT.json').read_text())
    return [dict(variant=n,panel_sha256=panels[n]['panel_sha256'],metadata_sha256=panels[n]['metadata_sha256'])
            for n in sorted(support) if support[n].get('support_pass',support[n].get('passed')) is True]


def inputs(root):
    from .registered import load_registration
    from .engine.panels import load_bound_panel
    root = Path(root)
    manifest = json.loads((root/'FREEZE_FINAL.json').read_text())
    from .contract_card import registered_execution_card
    card = registered_execution_card(root, manifest)
    bundle = load_registration(root, card)
    loaded = []
    for name in bundle['expected']:
        support = bundle['support'][name]
        if support.get('support_pass', support.get('passed')) is not True:
            continue  # Same registered unsupported path as run_registered.
        rows, identity = load_bound_panel(bundle['bindings'][name])
        loaded.append(dict(variant=name, rows=len(rows), panel_sha256=identity['panel_sha256'],
                           metadata_sha256=identity['metadata_sha256']))
        del rows
    loaded.sort(key=lambda r:r['variant'])
    projection=[{k:r[k] for k in ('variant','panel_sha256','metadata_sha256')} for r in loaded]
    if projection != manifest['dry_load_inventory']:
        raise ValueError('DRY-LOAD loaded inventory differs from final candidate')
    return dict(schema='l2_dry_load_v1', passed=True, fitting_disabled=True,
        design_id=card['design_id'], candidate_sha256=sha(root/'FREEZE_FINAL.json'),
        code_snapshot_digest=manifest['code_snapshot']['digest'], loaded=loaded,
        actual_data_estimation_calls=0, execution_slot_consumed=False,
        outcome_values_printed=False, outcome_associations_computed=False)


def run(root, signer):
    from . import snapshots
    from .freeze import verify
    root = Path(root)
    manifest = snapshots.manifest_for(root)
    if manifest is None: raise ValueError('DRY-LOAD requires an execution snapshot')
    if snapshots.active() is None:
        return snapshots.call(root, manifest, 'dry_load', dict(signer=signer))
    check = verify(root, 'final', signer, require_approval=False)
    if not check['ok']: raise ValueError('DRY-LOAD freeze refused: ' + str(check['issues']))
    value = inputs(root)
    path = ensure_once(root/'DRY_LOAD.json', value)
    return dict(value, path=str(path), sha256=sha(path))


def require_pass(root, candidate_sha256, design_id):
    root = Path(root); path = root/'DRY_LOAD.json'
    if not path.is_file(): raise ValueError('Mandatory DRY-LOAD PASS required before final signature')
    value = json.loads(path.read_text())
    manifest = json.loads((root/'FREEZE_FINAL.json').read_text())
    if (value.get('schema') != 'l2_dry_load_v1' or value.get('passed') is not True
            or value.get('fitting_disabled') is not True or value.get('design_id') != design_id
            or value.get('candidate_sha256') != candidate_sha256
            or value.get('code_snapshot_digest') != manifest['code_snapshot']['digest']
            or type(value.get('actual_data_estimation_calls')) is not int or value['actual_data_estimation_calls'] != 0
            or value.get('execution_slot_consumed') is not False
            or value.get('outcome_values_printed') is not False
            or value.get('outcome_associations_computed') is not False):
        raise ValueError('Mandatory DRY-LOAD PASS is stale/invalid')
    loaded=value.get('loaded')
    if not isinstance(loaded,list) or any(not isinstance(r,dict) or set(r) !=
            {'variant','rows','panel_sha256','metadata_sha256'} or type(r['rows']) is not int or r['rows']<0 for r in loaded):
        raise ValueError('DRY-LOAD loaded inventory is invalid')
    projected=[{k:r[k] for k in ('variant','panel_sha256','metadata_sha256')} for r in loaded]
    if projected!=manifest['dry_load_inventory'] or inventory(root,manifest['files'])!=manifest['dry_load_inventory']:
        raise ValueError('DRY-LOAD loaded inventory differs from final candidate')
    return sha(path)


def draft(ctx):
    """Use the execution loader before review, with no calibration/approval fiction.

    The ephemeral manifest binds current preparation bytes. This check grants no
    seal, signature, or execution authority; final DRY-LOAD remains mandatory.
    """
    from .registered import load_registration
    from .contract_card import registered_execution_card
    from .registration import _retain_cards
    from .engine.panels import load_bound_panel
    root=ctx.design_dir
    if (root/'FREEZE_PREP.json').exists():raise ValueError('Pre-review DRY-LOAD cannot mutate frozen preparation')
    _retain_cards(ctx)
    files={}
    for name in ('CARD.json','SCIENTIFIC_CARD.json','SPEC.json','SUPPORT.json','TREATMENT.json','PANELS.json','SUPPORT_C79.json','PANELS_C79.json','SECONDARY_PANELS.json'):
        if (root/name).is_file():files[name]=dict(sha256=sha(root/name),role='card' if name=='CARD.json' else 'verification')
    for folder in ('panels','secondary_panels'):
        for path in (root/folder).glob('*/*'):
            if path.is_file():files[str(path.relative_to(root))]=dict(sha256=sha(path),role='verification')
    manifest=dict(files=files)
    card=registered_execution_card(root,manifest)
    # SPEC binds intake bytes; the retained CARD can be an audited overlay.
    card['_card_path']=str(ctx.card.path)
    bundle=load_registration(root,card,_draft_manifest=manifest)
    loaded=[]
    for name in bundle['expected']:
        s=bundle['support'][name]
        if s.get('support_pass',s.get('passed')) is not True:continue
        rows,identity=load_bound_panel(bundle['bindings'][name])
        loaded.append(dict(variant=name,rows=len(rows),panel_sha256=identity['panel_sha256'],metadata_sha256=identity['metadata_sha256']))
        del rows
    value=dict(schema='l2_draft_dry_load_v1',design_id=ctx.card.design_id,passed=True,files=files,
        loaded=loaded,fitting_disabled=True,actual_data_estimation_calls=0,outcome_associations_computed=False,
        outcome_values_printed=False,execution_authorized=False)
    ensure_once(root/'DRY_LOAD_DRAFT.json',value)
    return value
