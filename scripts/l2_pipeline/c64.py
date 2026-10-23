"""Prospective L3 export registration; never reads outcomes or rewrites a seal."""
import copy
import json
from pathlib import Path
from .storage import ensure_once, sha

SPEC_NAME = 'SPEC_ADDENDUM_C64.json'
SCHEMA = 'l2_addendum_v2_c64'


def contract(card, repo=None, root=None):
    from .c64_metadata import export_contract
    value = export_contract(card, repo=repo)
    first = copy.deepcopy(card.get('first_order', {}))
    if root is not None:
        for ruling in sorted(Path(root).glob('PURPOSE_HUB_RULING*.json')):
            patch = json.loads(ruling.read_text()).get('card_patch', {}).get('first_order', {})
            if patch.get('target_outcome') != first.get('target_outcome'): continue
            for key in ('purpose_direction', 'purpose_direction_evidence'):
                if key in patch: first[key] = patch[key]
    if (first.get('purpose_direction') is not None and first.get('purpose_direction_evidence') is None
            and str(first.get('purpose_direction_status', '')).startswith('researcher_proposal_pending')):
        # NAMED_PANEL (2026-10-16): a legacy intake placeholder that no blind coder ever confirmed is a missing
        # coding, not a direction (never encoded as zero); the S0 direction file or the C83 addendum supplies the real one.
        first['purpose_direction'] = None
    if first.get('purpose_direction') is not None:
        from .agents.purpose import validate_purpose_direction
        validate_purpose_direction(first)
    value['first_order'] = {k: first.get(k) for k in ('purpose_direction', 'purpose_direction_evidence')}
    value['purpose_direction_status'] = 'FIELD_MISSING' if first.get('purpose_direction') is None else ('DIRECTION_UNDETERMINED' if first['purpose_direction'] == 0 else 'EVAL')
    from .c64_metadata import geography
    treatment_path = Path(root)/'TREATMENT.json' if root is not None else None
    treatment = json.loads(treatment_path.read_text()) if treatment_path is not None and treatment_path.exists() else None
    value['geography'].update(geography(card, treatment))
    if root is not None and (Path(root)/'SPEC_ADDENDUM_C83.json').exists():
        from .c83_l3 import overlay
        value=overlay(value,json.loads((Path(root)/'SPEC_ADDENDUM_C83.json').read_text()))
    # REDESIGN R1: the purpose label travels with the export so L3 can stratify; an R0 design has none.
    from .rules import pinned
    label_path = Path(root)/'PURPOSE_LABEL.json' if root is not None else None
    if label_path is not None and label_path.is_file():
        value['purpose_label'] = json.loads(label_path.read_text())['label']
    else:
        value['purpose_label'] = 'not_applicable_r0' if pinned(card) is None else 'unavailable'
    value['computation_stage'] = 'S6_after_final_freeze'
    value['no_l3_hypothesis_tests'] = True
    value['outcome_values_read'] = 0
    return value


def retain(ctx):
    root = ctx.design_dir
    from .c70_l3 import protected, retain as retain_amendment
    if protected(root):
        return None
    retain_amendment(ctx)
    path = root/SPEC_NAME
    if path.exists(): return path
    return ensure_once(path, contract(dict(ctx.card.payload), repo=ctx.repo, root=root))


def export_block(card, result, registration=None, numerical_reference=None):
    if registration is None:
        from .c70_l3 import merge_registration
        return merge_registration(dict(schema=SCHEMA, status='addendum fields unavailable', reason='Design has no frozen C64 supplemental specification'),None)
    from .c70_l3 import merge_registration
    block = merge_registration(registration, registration.get('amendment_v2'))
    numerical = (result or {}).get('l3_export_v2', {})
    block['field_definitions'] = {k:block.pop(k) for k in ('s_pre','H','trough','placebo_profiles') if k in block}
    block.update(numerical)
    if numerical_reference is not None:
        for key in numerical:
            pointer=numerical_reference['pointer']+'/'+key.replace('~','~0').replace('/','~1')
            block[key]={'$ref':dict(numerical_reference,pointer=pointer)}
    amendment=block['amendment_v2']
    for key in ('placebo_group','loo_control_att','statute_variant','interruption_ledger','pub_ledger','pub_ledger_sha256','group_time'):
        block.setdefault(key,copy.deepcopy(amendment.get(key)))
    block.setdefault('amendment_field_status',copy.deepcopy(amendment.get('field_status',{})))
    for key in ('s_pre', 'H', 'trough', 'placebo_profiles', 'placebo_labels'):
        block.setdefault(key, None)
    block.setdefault('status', 'AVAILABLE_FIELDS' if numerical else 'STOP_PRE_RUN' if result is None else 'FIELD_MISSING')
    return block


def reference(path, pointer=''):
    path=Path(path)
    return dict(path=path.name,sha256=sha(path),pointer=pointer)


def resolve_references(value, root, *, _cache=None, _seen=()):
    """Read-only, hash-checked L3 reader; legacy inline exports remain valid."""
    cache={} if _cache is None else _cache
    if isinstance(value,list):return [resolve_references(v,root,_cache=cache,_seen=_seen) for v in value]
    if not isinstance(value,dict):return value
    if set(value)=={'$ref'}:
        ref=value['$ref'];root=Path(root).resolve();path=(root/ref['path']).resolve()
        if path.parent!=root or Path(ref['path']).name!=ref['path'] or (root/ref['path']).is_symlink():
            raise ValueError('L3 reference escapes its package')
        key=(str(path),ref['sha256'],ref['pointer'])
        if key in _seen:raise ValueError('Cyclic L3 reference')
        if str(path) not in cache:
            if sha(path)!=ref['sha256']:raise ValueError('L3 reference hash mismatch')
            cache[str(path)]=(ref['sha256'],json.loads(path.read_text()))
        digest,target=cache[str(path)]
        if digest!=ref['sha256']:raise ValueError('Conflicting L3 reference hashes')
        pointer=ref['pointer']
        if pointer and not pointer.startswith('/'):raise ValueError('Invalid L3 JSON pointer')
        for token in pointer.split('/')[1:]:
            token=token.replace('~1','/').replace('~0','~')
            target=target[int(token)] if isinstance(target,list) else target[token]
        return resolve_references(target,root,_cache=cache,_seen=(*_seen,key))
    return {k:resolve_references(v,root,_cache=cache,_seen=_seen) for k,v in value.items()}


def read_l3_export(path):
    path=Path(path)
    return resolve_references(json.loads(path.read_text()),path.parent)
