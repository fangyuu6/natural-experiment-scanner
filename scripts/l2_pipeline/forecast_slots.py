"""Prospective C76 information-separated forecasts; no estimation or dispatch."""
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
from .storage import ensure_once, sha, encoded

ROSTER = ('Card', 'Finkelstein', 'Roth')
SCHEMA = 'l2_forecast_slots_c76_v1'

def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default

def roster(ctx):
    root=ctx.design_dir
    old=read(root/'PREDICTIONS_LOCK.json') or read(root/'FORECAST_SLOTS.json')
    if old:return list(old['personas'])
    imported=read(root/'FORECAST_IMPORT.json')
    if imported:return imported['personas']
    # In-flight sets retain their already issued roster. New defaults are three.
    existing=[p.name for p in (root/'predictions').iterdir()] if (root/'predictions').is_dir() else []
    if existing:
        from .agents.jobs import PERSONAS
        return [p for p in PERSONAS if p in existing]
    spec=read(root/'SPEC.json',{})
    if spec.get('c79_hygiene'):return list(ROSTER)
    return list(ctx.card.payload.get('automation',{}).get('personas',ROSTER))

def _purpose(ctx):
    value=read(ctx.design_dir/'PURPOSE_VERIFICATION.json',{})
    if not value:value=read(ctx.design_dir/'agents/purpose_verifier/submission.json',{})
    return {k:value[k] for k in ('target_outcome','target_population','confirmation','purpose_direction') if k in value}

def _literature(ctx):
    first=ctx.card.payload.get('first_order',{})
    pack=dict(summary=first.get('literature_summary'),citations=first.get('literature_refs',[]),verified_record=None)
    pointer=ctx.card.payload.get('automation',{}).get('literature_record',{})
    if pointer.get('collector_verified') is True and pointer.get('record_ref'):
        from .storage import public_input
        p=Path(pointer['record_ref']);p=public_input(p if p.is_absolute() else ctx.repo/p)
        record=read(p,{})
        if record.get('status')=='VERIFIED':
            fields=('title','authors','year','venue','doi','source_version','policy_definition','primary_outcome','population','study_window','published_results','citation_evidence')
            pack['verified_record']={k:record[k] for k in fields if k in record}
            pack['record_sha256']=sha(p)
    pack['summary_status']='AVAILABLE' if pack['summary'] or pack['verified_record'] else 'FIELD_MISSING: only located citations available'
    return pack

def _historical(ctx):
    """Select prior other-policy records using metadata BEFORE opening exports.

    Exact policy family and outcome units define similarity. Same policy identity,
    this design (all versions), and declared related/lineage designs are excluded.
    Only terminal estimates already admitted to the registry are read.
    """
    current=ctx.card.payload;first=current.get('first_order',{})
    family=current.get('policy_family');units=current.get('outcome',{}).get('outcome_unit')
    forbidden={ctx.card.design_id,*first.get('related_design_ids',[])}
    policy=current.get('policy_id') or current.get('mechanism')
    lineage=set(current.get('lineage',{}).get('source_text_ids',[]))
    rows=[]
    if not family:return rows
    registry=read(ctx.repo/'data/l2_pipeline/registry/TERMINAL_RUNS.json',{})
    for record in registry.get('runs',[]):
        if record.get('design_id') in forbidden or record.get('study_estimated') is not True:continue
        root=Path(record.get('package_path',''))
        if not root.is_absolute() or not root.is_relative_to(ctx.repo/'data/l2_pipeline'):continue
        card=read(root/'CARD.json',{})
        if (card.get('design_id') in forbidden or card.get('policy_family')!=family
            or card.get('outcome',{}).get('outcome_unit')!=units
            or (card.get('policy_id') or card.get('mechanism'))==policy
            or forbidden.intersection(card.get('first_order',{}).get('related_design_ids',[]))
            or lineage.intersection(card.get('lineage',{}).get('source_text_ids',[]))):continue
        # Read the prior published pipeline export, never raw outcomes or a fit.
        p=root/'L3_EXPORT_DATA.json'
        expected=record.get('s7_exports',{}).get(p.name)
        if not expected or not p.is_file() or sha(p)!=expected:continue
        export=read(p,{})
        from .c64 import resolve_references
        export['result']=resolve_references(export.get('result'),p.parent)
        primary=(export.get('result') or {}).get('registered_results',{}).get('primary',{})
        estimate=(primary.get('result') or {}).get('overall',{}).get('att')
        if type(estimate) not in {int,float} or not math.isfinite(estimate):continue
        rows.append(dict(design_id=card['design_id'],policy_family=family,outcome_unit=units,
            estimate=estimate,source_sha256=expected,source_path=str(p)))
    return rows

def prepare(ctx, personas=None, bundle=False):
    """Freeze distinct projections once before emitting any new-slot job.

    ``bundle`` (REDESIGN R2): one author writes all three forecasts, so no slot may sit with a Codex model; the file records the mode."""
    root=ctx.design_dir;path=root/'FORECAST_SLOTS.json'
    if path.exists():return read(path)
    if (root/'PREDICTIONS_LOCK.json').exists() or (root/'FORECAST_IMPORT.json').exists():return None
    personas=list(personas or roster(ctx))
    if personas!=list(ROSTER):return None # explicitly retained legacy roster
    from .contract_card import registered_contract_card
    contract=registered_contract_card(root)
    if contract['registered_metadata_incomplete']:return None # incomplete test/draft cannot be registered
    card=ctx.card.payload;first=card.get('first_order',{})
    statute=list(first.get('statute_refs',[]))
    for source in card.get('sources',[]):
        if source.get('source_type') not in {'statute','official_statute','legislation','official_law'} and not source.get('source_id','').startswith('statute_'):continue
        if not source.get('path'):
            statute.append({k:source[k] for k in ('source_id','url','locator') if k in source});continue
        p=Path(source['path']);p=p if p.is_absolute() else ctx.card.path.parent/p
        if sha(p)!=source['sha256']:raise ValueError('Forecast statute source hash mismatch')
        if p.suffix.lower() not in {'.txt','.md','.json','.html'}:continue
        statute.append(dict(source_id=source.get('source_id'),sha256=sha(p),text=p.read_text()))
    packs=[dict(kind='statute_and_purpose',statutes=statute,purpose=_purpose(ctx),
                statute_purpose=first.get('statute_purpose'),policy_mechanism=card.get('mechanism')),
           dict(kind='published_literature_summary',**_literature(ctx)),
           dict(kind='other_policy_historical_base_rates',similarity_rule='same policy_family and original unit; different policy identity',
                excluded_design_ids=[ctx.card.design_id,*first.get('related_design_ids',[])],records=_historical(ctx))]
    slots={}
    for i,(persona,pack) in enumerate(zip(personas,packs),1):
        p=ensure_once(root/'forecast_packs'/('slot'+str(i)+'.json'),pack)
        slots[persona]=dict(slot=i,kind=pack['kind'],pack_path=str(p.resolve()),pack_sha256=sha(p),
            model_id='gpt-6.1-sol' if i==3 else 'claude-sonnet-5.5',executor='codex' if i==3 else 'sonnet_queue')
    from .agents.sonnet_queue import claude_only
    if claude_only() or bundle:  # no Codex slot
        for s in slots.values():s.update(model_id='claude-sonnet-5.5',executor='sonnet_queue')
    if len({s['pack_sha256'] for s in slots.values()})!=3:raise ValueError('Forecast information packs must differ pairwise')
    return read(ensure_once(path,dict(schema=SCHEMA,personas=personas,slots=slots,**({'mode':'bundle'} if bundle else {}))))

def bind_contract(ctx, role, contract, inputs):
    value=read(ctx.design_dir/'FORECAST_SLOTS.json')
    if not value:return False
    slot=value['slots'][role]
    from .agents.jobs import _packet
    packet=_packet(Path(slot['pack_path']),'exclusive forecast information pack')
    if packet['sha256']!=slot['pack_sha256']:raise ValueError('Forecast information pack changed')
    # The full SPEC contains literature and source inventories. Share only the
    # registered target card, otherwise statute-only slots would see the literature.
    inputs[:]=[p for p in inputs if not p['label'].startswith('registered ')]
    inputs.append(packet)
    contract['forecast_slot']=slot
    contract['role_backend_overrides']={role:slot['executor']}
    return True

def same_model(registered, reported):
    """Model ids match after case and "."/"-" normalisation: slots say claude-sonnet-5.5, workers report claude-sonnet-5-5."""
    norm=lambda v:(v.strip().lower().replace('.','-') or None) if isinstance(v,str) else None
    return norm(registered) is not None and norm(registered)==norm(reported)

def provenance(job, value):
    slot=job.contract['forecast_slot'];author=value.get('author_provenance')
    if not isinstance(author,dict) or any(not isinstance(author.get(k),str) or not author[k].strip() for k in ('worker_id','model_id')):
        raise ValueError('Forecast author provenance must be non-null')
    if not same_model(slot['model_id'],author['model_id']):raise ValueError('Forecast model differs from registered slot')
    return dict(slot=slot['slot'],kind=slot['kind'],model_id=author['model_id'],
        author_provenance=author,pack_sha256=slot['pack_sha256'],prompt_sha256=job.job_sha256)

FORWARD_RULE_DROPPED='start_minus_one'   # hygiene.SMO_RULE (HUB_RULING_20261022_DRAFT_STOP Q7): the only SUPPORT row a rebind may remove

def _forward_rule_dropped_row(root,imported):
    """T284: the name of the single SUPPORT variant that the rebuilt SPEC lost against the SOURCE SPEC of an unchanged-forecast import, if and only if it is
    the forward-rule row (start_minus_one) and nothing else is missing; otherwise None. The source SPEC is read from the import proof and must match its
    recorded hash. Reads two SPEC files, writes nothing."""
    try:
        source=Path(imported.get('source_spec_path',''))
        if not source.is_file() or sha(source)!=imported.get('source_spec_sha256'):return None
        old=[r.get('variant') for r in read(source).get('support',{}).get('variants',[])]
        new=[r.get('variant') for r in read(Path(root)/'SPEC.json').get('support',{}).get('variants',[])]
    except (OSError,ValueError,AttributeError,TypeError):return None
    gone=[n for n in old if n not in new]
    if gone==[FORWARD_RULE_DROPPED] and old.count(FORWARD_RULE_DROPPED)==1:return FORWARD_RULE_DROPPED
    return None

def validate_contract(root, personas):
    """Compare independently saved job cards, not a card copied from SPEC now."""
    from .contract_card import registered_contract_card
    expected=registered_contract_card(root)
    # Forecasts rebound unchanged to a rebuilt design (C57 rebind, FORECAST_IMPORT.json written by predictions.import_unchanged_predictions
    # for this very SPEC) keep the card of the old attempt: `version` is the one field c57.science_equal ignores; nothing else is relaxed.
    imported=read(Path(root)/'FORECAST_IMPORT.json',{})
    relax=(isinstance(imported,dict) and imported.get('schema')=='l2_unchanged_forecast_import_v1' and imported.get('primary_target_unchanged') is True
        and imported.get('target_spec_sha256')==sha(Path(root)/'SPEC.json'))
    added=imported.get('added_sensitivity') if relax else None
    removed=_forward_rule_dropped_row(root,imported) if relax else None
    def same(card):
        if not (relax and isinstance(card,dict)):return card
        card={k:v for k,v in card.items() if k!='version'}
        skip={x for x in (added,removed) if x is not None}
        if skip and isinstance(card.get('support'),dict):
            # one report-only variant added by an authorised C57 rebind (FORECAST_IMPORT.added_sensitivity), or the one variant the T3 forward rule
            # removed from the rebuilt SPEC (T284, rebind-ui-20261023): its SUPPORT row is the only difference
            card['support']=dict(card['support'],variants=[v for v in card['support'].get('variants',[]) if v.get('variant') not in skip])
        return card
    for persona in personas:
        receipt=read(Path(root)/'predictions'/persona/'COLLECTION.json',{})
        if not receipt.get('job_path'):raise ValueError('Forecast contract card provenance missing')
        jobpath=Path(receipt['job_path']);meta=read(jobpath.with_suffix('.job.json'),{})
        marker='## Execution metadata binding (data)\n```json\n'
        prompt=jobpath.read_text()
        try:bound=json.loads(prompt.split(marker,1)[1].split('\n```',1)[0])
        except (IndexError,ValueError):bound={}
        if (sha(jobpath)!=receipt.get('job_sha256') or same(meta.get('contract',{}).get('card'))!=same(expected)
            or same(bound.get('contract',{}).get('card'))!=same(expected)):
            raise ValueError('Forecast contract card differs from registered SPEC/SUPPORT: '+persona)

def worker_ids(slots):
    """Worker id of every slot (list in slot order), or None when any slot lacks a non-empty string worker id.

    Missing or null worker data is never read as "different workers": callers treat None as not independent (HUB_RULING_20261023_PARK_REOPEN section 4, Q3).
    """
    if not isinstance(slots,dict) or not slots:return None
    ids=[]
    for slot in slots.values():
        author=slot.get('author_provenance') if isinstance(slot,dict) else None
        worker=author.get('worker_id') if isinstance(author,dict) else None
        if not isinstance(worker,str) or not worker.strip():return None
        ids.append(worker)
    return ids

def n10_rule(single_consensus, n_personas, n_workers):
    """THE N10 eligibility rule (Q3 ruling 3b, panel 5-0): not SINGLE_CONSENSUS, at least 3 personas, at least 2 distinct worker ids.

    The lock writer, `n10_eligible_record` (the L3 reader), the carry sidecar and the L3 ledger all call this one function.
    Anything that is not exactly a bool False / an int meeting the bound (None, missing, a bool) is ineligible.
    """
    def count(v,least):return isinstance(v,int) and not isinstance(v,bool) and v>=least
    return single_consensus is False and count(n_personas,3) and count(n_workers,2)

def n10_eligible_record(independence):
    """Read-time eligibility of an `independence` record (lock keys, export `forecast_independence` or sidecar block).

    Requires the stored flag AND the rule recomputed from the record's own slots (a stored n_workers that disagrees with the slots is ineligible), n_eff > 1.
    A record without forecast_slots, with a null worker id, or from before the worker rule (one author) is not eligible: no file is edited, the reading changes.
    """
    if not isinstance(independence,dict):return False
    ids=worker_ids(independence.get('forecast_slots'))
    if ids is None:return False
    n_workers=len(set(ids))
    stored=independence.get('n_workers')
    if stored is not None and stored!=n_workers:return False
    n_eff=independence.get('n_eff')
    if isinstance(n_eff,bool) or not isinstance(n_eff,(int,float)) or not n_eff>1:return False
    return independence.get('n10_eligible') is True and n10_rule(independence.get('SINGLE_CONSENSUS'),len(ids),n_workers)

INDEPENDENCE_KEYS = ('n_eff', 'SINGLE_CONSENSUS', 'n10_eligible', 'forecast_slots')
CARRY_NAME = 'INDEPENDENCE_CARRY.json'
CARRY_SCHEMA = 'l2_independence_carry_v1'
CARRY_AUTHORITY = 'docs/plans/l2-pipeline/HUB_RULING_20261023_PARK_REOPEN.md section 4 (Q3 ruling 3a/3b)'

def carried_independence(source_lock_path, source_attempt=None):
    """Immutable record of a SOURCE lock's independence facts (3a): lock path and sha256, attempt, slots, worker ids, n_workers.

    None when the lock carries none of the independence keys (legacy five-persona sets and rebounds written before this fix). When it carries
    any key, slots with non-null worker ids for every persona are required, otherwise ValueError (fail closed). Reads no forecast value.
    """
    path=Path(source_lock_path)
    lock=read(path)
    if not isinstance(lock,dict) or not set(INDEPENDENCE_KEYS)&set(lock):return None
    personas=lock.get('personas')
    slots=lock.get('forecast_slots')
    ids=worker_ids(slots)
    if ids is None or not isinstance(personas,list) or set(slots)!=set(personas):
        raise ValueError('Source lock independence record lacks worker ids for every persona (fail closed)')
    return dict(source_lock_path=str(path.resolve()),source_lock_sha256=sha(path),source_attempt=source_attempt or path.parent.name,
        personas=list(personas),forecast_slots=slots,worker_ids={k:v['author_provenance']['worker_id'] for k,v in slots.items()},
        n_workers=len(set(ids)),source_n_eff=lock.get('n_eff'),source_SINGLE_CONSENSUS=lock.get('SINGLE_CONSENSUS'))

def _carry_independence_block(carried):
    return dict(n_eff=carried['source_n_eff'],SINGLE_CONSENSUS=carried['source_SINGLE_CONSENSUS'],forecast_slots=carried['forecast_slots'],
        n_workers=carried['n_workers'],
        n10_eligible=n10_rule(carried['source_SINGLE_CONSENSUS'],len(carried['personas']),carried['n_workers']),
        independence_source=dict(source_attempt=carried['source_attempt'],source_lock_path=carried['source_lock_path'],
            source_lock_sha256=carried['source_lock_sha256'],forecast_import='FORECAST_IMPORT.json',via=CARRY_NAME))

def _import_problems(imported, target):
    """Value-free check that a design's FORECAST_IMPORT.json proof still holds: schema, target spec and every carried original/collection hash."""
    problems=[]
    if not isinstance(imported,dict) or imported.get('schema')!='l2_unchanged_forecast_import_v1' or imported.get('primary_target_unchanged') is not True:
        return ['FORECAST_IMPORT.json is not an unchanged-forecast import proof']
    if imported.get('target_spec_sha256')!=(sha(target/'SPEC.json') if (target/'SPEC.json').is_file() else None):problems.append('target SPEC.json differs from the import proof')
    for file,digest in (imported.get('files') or {}).items():
        p=Path(file)
        if not p.is_file() or sha(p)!=digest:problems.append('imported source file changed: '+file)
    return problems

def build_carry(target_root):
    """Content of the additive sidecar for ONE rebound design (apply script); raises ValueError if the facts do not support it.

    Reads only lock keys, worker ids and file hashes. The target lock is never edited; the sidecar hashes it and the source lock.
    """
    target=Path(target_root).resolve()
    lock_path=target/'PREDICTIONS_LOCK.json'
    lock=read(lock_path)
    imported=read(target/'FORECAST_IMPORT.json')
    if not isinstance(lock,dict):raise ValueError('target has no PREDICTIONS_LOCK.json')
    if set(INDEPENDENCE_KEYS)&set(lock):raise ValueError('target lock already carries independence keys; no sidecar is needed')
    if isinstance(imported,dict) and 'independence_carried' in imported:raise ValueError('import proof already carries independence; no sidecar is needed')
    problems=_import_problems(imported,target)
    if problems:raise ValueError('; '.join(problems))
    if list(imported.get('personas',[]))!=list(lock.get('personas',[])):raise ValueError('import personas differ from the target lock')
    source_lock=Path(imported['source_spec_path']).parent/'PREDICTIONS_LOCK.json'
    carried=carried_independence(source_lock)
    if carried is None:raise ValueError('source lock carries no independence record')
    if carried['personas']!=list(lock['personas']):raise ValueError('source personas differ from the target lock')
    return dict(schema=CARRY_SCHEMA,design_id=lock.get('design_id'),authority=CARRY_AUTHORITY,
        target_lock=dict(path=str(lock_path),sha256=sha(lock_path)),
        forecast_import=dict(path=str(target/'FORECAST_IMPORT.json'),sha256=sha(target/'FORECAST_IMPORT.json')),
        carried=carried,independence=_carry_independence_block(carried))

def read_carry(root):
    """(independence block or None, problems). The block is returned only when every hash and the recomputation hold; absent sidecar = (None, []).

    The block goes through `n10_eligible_record` like any other independence record; this function decides nothing about eligibility.
    A target lock that carries its own independence keys always wins: the sidecar is then ignored (and reported).
    """
    root=Path(root)
    path=root/CARRY_NAME
    if not path.is_file():return None,[]
    value=read(path)
    problems=[]
    try:
        if not isinstance(value,dict) or value.get('schema')!=CARRY_SCHEMA:return None,['unknown sidecar schema']
        lock_path=root/'PREDICTIONS_LOCK.json'
        lock=read(lock_path)
        if not isinstance(lock,dict):return None,['target lock missing']
        if set(INDEPENDENCE_KEYS)&set(lock):return None,['target lock carries its own independence keys; sidecar ignored']
        if value['target_lock'].get('sha256')!=sha(lock_path):problems.append('target lock changed since the sidecar')
        imp=root/'FORECAST_IMPORT.json'
        if not imp.is_file() or value['forecast_import'].get('sha256')!=sha(imp):problems.append('FORECAST_IMPORT.json changed since the sidecar')
        else:problems+=_import_problems(read(imp),root)
        carried=value.get('carried')
        if not isinstance(carried,dict):return None,problems+['sidecar has no carried record']
        fresh=carried_independence(carried['source_lock_path'],carried.get('source_attempt'))
        if fresh!=carried:problems.append('source lock or carried record differs from the sidecar')
        elif list(carried['personas'])!=list(lock.get('personas',[])):problems.append('personas differ from the target lock')
        elif value.get('independence')!=_carry_independence_block(carried):problems.append('sidecar independence block differs from its carried record')
    except (KeyError,TypeError,ValueError) as exc:
        problems.append('sidecar unreadable: %s'%exc)
    return (None if problems else value['independence']),problems

def independence(values, slots=None):
    """Report-only equivalence groups, tolerance fixed at 1% of interval width.

    Effective count is inverse Simpson concentration over forecast-equivalence
    groups. This is a descriptive effective count, not a statistical independence
    estimate; near-agreement never triggers forecast regeneration.

    `slots` (persona -> forecast_slot with author_provenance) adds the authorship fact: `n_workers` is the number of distinct worker ids
    (None when any is missing) and `n10_eligible` follows `n10_rule`. Without slots the worker count is unknown, so n10_eligible is False.
    """
    groups=[]
    def near(a,b):
        scale=max(abs(a['interval'][1]-a['interval'][0]),abs(b['interval'][1]-b['interval'][0]),1e-12)
        return abs(a['interval_level']-b['interval_level'])<=1e-8 and all(abs(x-y)<=.01*scale for x,y in zip([a['point'],*a['interval']],[b['point'],*b['interval']]))
    for value in values:
        group=next((g for g in groups if all(near(value,old) for old in g)),None)
        if group is None:groups.append([value])
        else:group.append(value)
    n=len(values);single=len(groups)==1
    ids=worker_ids(slots) if slots is not None else None
    workers=len(set(ids)) if ids is not None else None
    return dict(n_eff=n*n/sum(len(g)**2 for g in groups),SINGLE_CONSENSUS=single,n_workers=workers,
        n10_eligible=n10_rule(single,len(slots) if isinstance(slots,dict) else None,workers),
        independence_rule='complete-link forecast equivalence; 1% of pairwise max interval width; inverse Simpson group count; n10_eligible = not SINGLE_CONSENSUS and >= 3 personas and >= 2 distinct worker ids')
