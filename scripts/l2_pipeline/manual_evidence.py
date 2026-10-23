"""Hash-bound hub supplements; never supply an independent coder or V3 audit."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit
from .storage import public_input, sha


def binding(path):
    path=public_input(Path(path))
    return dict(path=str(path.resolve()),sha256=sha(path))


def load(repo, design_id, predicate):
    base=Path(repo)/'data/l2_pipeline/autopilot'/design_id/'manual_evidence'
    handoff=base/'HANDOFF.json'
    if not handoff.exists():return None
    packet=json.loads(handoff.read_text()); bindings=[binding(handoff)];entries={}
    for filename in packet.get('authoritative_packet_files',[]):
        path=(base/filename).resolve()
        if not path.is_relative_to(base.resolve()):raise ValueError('Supplement path outside packet')
        bindings.append(binding(path))
    for filename in packet.get('state_records',[]):
        path=(base/filename).resolve()
        if not path.is_relative_to(base.resolve()):raise ValueError('Supplement path outside packet')
        entry=json.loads(public_input(path).read_text());bindings.append(binding(path))
        state=entry['state']
        if state in entries:raise ValueError('Duplicate manual evidence state')
        date.fromisoformat(entry['effective_date'])
        if not entry.get('quote') or not entry.get('effective_date_quote') or not entry.get('statute'):
            raise ValueError('Supplement requires quoted official legal/date locators')
        parsed=urlsplit(entry.get('url',''))
        if parsed.scheme not in {'http','https'} or not parsed.hostname:raise ValueError('Supplement URL missing')
        sources=entry.get('sources',[])
        for source in sources:
            local=(base/source['file']).resolve()
            if not local.is_relative_to(base.resolve()) or sha(public_input(local))!=source['sha256']:
                raise ValueError('Supplement source bytes changed')
            bindings.append(binding(local))
        entry['official_complete']=(state in packet.get('completed_states',[]) and state not in packet.get('partial_states',[])
            and any(s.get('official_host') is True for s in sources)
            and 'gap' not in entry.get('status',''))
        entries[state]=entry
    review_path=base/'PREDICATE_REVIEW_C59.json';review={}
    if review_path.exists():
        review=json.loads(review_path.read_text());bindings.append(binding(review_path))
        if review.get('predicate')!=predicate:raise ValueError('Manual predicate review differs from LOCKED predicate')
    from .hub_adjudication import load as adjudication
    ruling=adjudication(repo,design_id,predicate)
    if ruling:
        bindings.append(ruling['binding'])
        bindings.append(binding(Path(repo)/ruling['authority']['path']))
        review=deepcopy(review)
        review.setdefault('states',{}).update({u:dict(d,hub_adjudicated=True,authority=ruling['authority']) for u,d in ruling['states'].items()})
    return dict(entries=entries,review=review,bindings=list({b['path']:b for b in bindings}.values()),
        predicate=predicate,s1_ready=False,execution_authorized=False)


def located(row, sources):
    return bool(row.get('locator')) and any(isinstance(r,dict) and r.get('locator') and r.get('quote')
        and sources.get(r.get('source_id'),{}).get('source_type')=='official' for r in row.get('evidence_refs',[]))


def supplement(value, packet):
    if packet is None:return value,None
    result=deepcopy(value);events=[]
    indexed={s['source_id']:s for s in result['sources']}
    for row in result['records']:
        entry=packet['entries'].get(row['unit'])
        if not entry:continue
        unit=row['unit']; decision=packet['review'].get('states',{}).get(unit,{})
        event=dict(unit=unit,effective_date=entry['effective_date'],qualifications=entry.get('conditions',[]),
            legal_protection=entry.get('legal_protection'),open_issues=entry.get('notes',[]),
            predicate_review=decision,original_status=row['status'],original_start_period=row.get('start_period'))
        if decision.get('hub_adjudicated') is True:
            if row.get('hub_adjudication')==decision['authority'] and row.get('effective_date')==decision['effective_date'] and located(row,indexed):
                event['action']='retained_hub_adjudicated_inclusion';events.append(event);continue
            if not entry['official_complete'] or decision['effective_date']!=entry['effective_date']:
                raise ValueError('Adjudicated date requires complete matching official evidence')
            year=date.fromisoformat(decision['effective_date']).year
            row['hub_adjudication_original']=deepcopy(row)
            row.update(status='treated',start_period=year+1,best_evidence_start_year=year,
                effective_date=decision['effective_date'],operative_date=decision['effective_date'],
                hub_adjudication=decision['authority'],evidence_status='known_start',
                disposition='RESOLVED_HUB_RULING')
            row.pop('pre_window_bound',None)   # T192: an adjudicated exact date replaces a bound
            for field in ('manual_evidence_conflict','named_conflict','reason'):
                row.pop(field,None)  # Exact prior fields remain in the original above.
        # An unknown remains unknown. Supplements cannot invent a coder's answer.
        if row['status']=='unknown':
            event['action']='retained_unknown';events.append(event);continue
        official=located(row,indexed)
        adoption=row['status'] in {'treated','not_yet_treated'}
        year=date.fromisoformat(entry['effective_date']).year
        conflict=None
        if adoption and (row.get('best_evidence_start_year')!=year or row.get('start_period')!=year+1):
            conflict='manual_evidence_clock_conflict'
        for key in ('effective_date','operative_date'):
            if adoption and row.get(key) and row[key]!=entry['effective_date']:conflict='manual_evidence_clock_conflict'
        disposition=decision.get('decision')
        if row['status']=='never_treated' and entry['official_complete'] and disposition!='exclude':
            conflict='manual_evidence_status_conflict'
        if disposition=='NEEDS_RULING' or (disposition=='exclude' and adoption):
            conflict='predicate_excluded' if disposition=='exclude' else 'predicate_NEEDS_RULING'
        if not entry['official_complete'] and not official:conflict='official_evidence_gap'
        sid='manual_evidence_'+unit
        if conflict:
            # The gap is named and retained; C42 excludes it from both arms.
            if sid in indexed:raise ValueError('Manual supplement source id collision')
            source=dict(source_id=sid,url=entry['url'],source_type='official' if entry['official_complete'] else 'supplement_gap')
            result['sources'].append(source);indexed[sid]=source
            row['manual_evidence_original']=deepcopy(row)
            row.update(status='unknown',start_period=None,reason=conflict,disposition='NEEDS_RULING',
                manual_evidence_conflict=conflict,named_conflict=dict(kind='policy_definition_ambiguous',
                    detail=conflict+': '+json.dumps(event,sort_keys=True),binary_relevant_fact='Whether the submitted start meets the unchanged predicate and C11 operation clock',
                    source_ids=[row['source_id'],sid],question='Retain excluded under C42 until independent official evidence or a predicate ruling resolves this state.'))
            event['action']=conflict
        elif (not official or decision.get('hub_adjudicated') is True) and adoption and entry['official_complete']:
            if sid in indexed:raise ValueError('Manual supplement source id collision')
            source=dict(source_id=sid,url=entry['url'],source_type='official')
            result['sources'].append(source);indexed[sid]=source
            row.setdefault('evidence_refs',[]).append(dict(source_id=sid,locator=entry['statute']+'; '+entry.get('effective_date_basis',''),
                quote=entry['quote']+' '+entry['effective_date_quote'],supports='start_year',year=year,
                effective_date=entry['effective_date'],hub_manual_supplement=True))
            row['locator']=row.get('locator') or entry['statute']
            event['action']='supported_existing_coder_start'
        else:event['action']='retained_coder_official_evidence'
        if decision.get('hub_adjudicated') is True:event['action']='hub_adjudicated_inclusion'
        events.append(event)
    report=dict(schema='l2_manual_evidence_integration_v1',bindings=packet['bindings'],states=events,
        predicate=packet['predicate'],independent_ab_required=True,fresh_v3_required=True,execution_authorized=False)
    return result,report


def for_job(job,value):
    return supplement(value,load(job.repo,job.design_id,job.contract['mechanism']))


def collection_directory(job):
    """A changed adjudication produces additive receipts, preserving old bytes."""
    original=Path(job.output_path).parent
    if job.contract.get('jobs_kind')!='official_history_collection':return original
    packet=load(job.repo,job.design_id,job.contract['mechanism'])
    if not packet:return original
    prior=original/'MANUAL_EVIDENCE.json'
    if not prior.exists():return original
    old=json.loads(prior.read_text())
    if old.get('bindings')==packet['bindings']:return original
    import hashlib
    from .storage import encoded
    digest=hashlib.sha256(encoded(packet['bindings'])).hexdigest()
    return original/'manual_revisions'/digest


def collection_path(job):
    return collection_directory(job)/'COLLECTION.json'
