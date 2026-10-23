"""Prospective N13–N16 field contracts. No outcomes or adoption associations read."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from .storage import encoded, ensure_once, public_input, sha

SPEC_NAME = 'SPEC_ADDENDUM_C70.json'
SCHEMA = 'l2_l3_amendment_c70_v1'
AMENDMENT_PATH = 'docs/plans/l3-meta/PREREG_ADDENDUM_V2_AMEND_20261011.md'
AMENDMENT_SHA256 = '6d28a7fb3149a079ae996d372d523519c82d0b9ee512fc7663f784bf02308acf'
TARGETS_PATH = 'data/l3_meta/replication_targets/TARGETS.json'
TARGETS_SHA256 = 'f70df1d4770eab9ad75b56f677dbe24e2e852dfa7b2baecd629f12b9ec1dca62'
FIELDS = {'F11':'placebo_group', 'F12':'loo_control_att', 'F13':'statute_variant',
          'F14':'interruption_ledger', 'F15':'pub_ledger', 'F16':'group_time'}


def protected(root):
    root = Path(root)
    return any((root/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json','RESULT.json','L3_EXPORT_DATA.json')) or any(root.glob('SEALED_*.json'))


def _text(value):
    return isinstance(value,str) and bool(value.strip())


def _blind(record, extra=()):
    attestation = record.get('blind_attestation', {})
    if any(attestation.get(k) is not False for k in ('estimate_read','forecast_read',*extra)):
        raise ValueError('C70 blind coding attestation missing')
    coders = record.get('coders')
    if not isinstance(coders,list) or len(coders)!=2 or not all(_text(c) for c in coders) or len(set(coders))!=2:
        raise ValueError('Two independent C70 coders required')


def validate_statute_variant(value, universe):
    if not isinstance(value,list): raise ValueError('statute_variant must be per-state rows')
    seen=set()
    for row in value:
        unit=row.get('unit'); kind=row.get('variant')
        if unit not in universe or unit in seen or kind not in {'same','stronger','weaker','other_scope','unknown'}:
            raise ValueError('Invalid/duplicate statute variant')
        seen.add(unit); _blind(row, ('literature_sign_read',))
        if not _text(row.get('locator')) or not _text(row.get('first_cohort_locator')):
            raise ValueError('Statute and first-cohort locators required')
        votes=row.get('coder_variants')
        if not isinstance(votes,list) or len(votes)!=2 or any(v not in {'same','stronger','weaker','other_scope','unknown'} for v in votes):
            raise ValueError('Two blind variant votes required')
        if votes[0]!=votes[1]:
            if not _text(row.get('adjudicator')) or row['adjudicator'] in row['coders'] or not _text(row.get('adjudication_locator')):
                raise ValueError('Variant disagreement requires independent third coder')
        elif kind!=votes[0]: raise ValueError('Variant differs from agreed coding')
    return copy.deepcopy(value)


def validate_interruption_ledger(value, universe, treatment=None):
    if not isinstance(value,dict): raise ValueError('Interruption ledger must be an object')
    _blind(value)
    try: asof=date.fromisoformat(value['as_of_date'])
    except (KeyError,ValueError,TypeError): raise ValueError('Interruption as-of date required') from None
    certified=value.get('interruption_coverage_certified')
    if type(certified) is not bool: raise ValueError('Interruption certification must be explicit')
    searches=value.get('searches',[]); units=[r.get('unit') for r in searches]
    if len(set(units))!=len(units) or not set(units)<=set(universe): raise ValueError('Invalid interruption search universe')
    if certified and (set(units)!=set(universe) or any(r.get('searched_through')!=asof.isoformat() or not _text(r.get('evidence_ref')) for r in searches)):
        raise ValueError('Certified absence requires every state searched through as-of date')
    rows=value.get('rows'); seen=set()
    if not isinstance(rows,list): raise ValueError('Interruption state-year rows required')
    onsets=value.get('onset_years',{})
    if not isinstance(onsets,dict) or not set(onsets)<=set(universe) or any(type(y) is not int or y>asof.year for y in onsets.values()):
        raise ValueError('Invalid interruption onsets')
    for row in rows:
        key=(row.get('unit'),row.get('year'))
        if key[0] not in universe or type(key[1]) is not int or key in seen or key[1]>asof.year or row.get('status') not in {'in_force','interrupted','conflict','unknown'}:
            raise ValueError('Invalid interruption state-year cell')
        seen.add(key)
        if row['status']=='interrupted':
            event=row.get('interruption',{})
            if event.get('type') not in {'repeal','rollback','sunset_expiry','court_block','other'} or event.get('both_coders_agree') is not True or not _text(event.get('evidence_ref')):
                raise ValueError('Agreed interruption type and evidence required')
            try: start=date.fromisoformat(event['start_date'])
            except (KeyError,ValueError,TypeError): raise ValueError('Interruption start date required') from None
            if start>asof or start.year>key[1]: raise ValueError('Interruption starts after cell/as-of')
    never=value.get('never_adopted_units',[])
    if certified and (not isinstance(never,list) or len(set(never))!=len(never) or set(never)&set(onsets) or set(never)|set(onsets)!=set(universe)):
        raise ValueError('Certified interruption universe needs onset or explicit never-adopted status')
    if certified and treatment is not None:
        from .prewindow import forbid
        forbid(treatment.get('records',[]), 'interruption onset code (T192)')
        records={r['unit']:r for r in treatment.get('records',[])}
        if set(universe)-set(records): raise ValueError('Interruption universe absent from bound treatment')
        for unit in universe:
            record=records[unit]
            if unit in never:
                if record.get('status')!='never_treated': raise ValueError('Never-adopted status contradicts bound treatment')
            else:
                onset=record.get('operative_period',record.get('effective_year',record.get('start_period')))
                if type(onset) is not int or onset!=onsets[unit] or record.get('status') not in {'treated','repealed','reverted','not_yet_treated'}:
                    raise ValueError('Interruption onset differs from bound treatment')
    expected={(u,y) for u,g in onsets.items() for y in range(g,asof.year+1)}
    if certified and (seen!=expected or any(r['status']=='unknown' for r in rows)):
        raise ValueError('Certified interruption coverage requires every post-onset cell')
    return copy.deepcopy(value)


def publication_class(candidate, direction, reported):
    match=candidate.get('match',{})
    if any(match.get(k) not in {'match','arith_convertible','partial'} for k in ('policy_scope_dose','outcome')):
        return 'UNCLASSIFIED'
    estimate=candidate.get('estimate',{}); ci=estimate.get('ci')
    if ci is not None:
        if not isinstance(ci,list) or len(ci)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in ci) or ci[0]>ci[1]:
            raise ValueError('Invalid published confidence interval')
        significant=ci[0]>0 or ci[1]<0
    elif estimate.get('point') is not None and estimate.get('se') is not None:
        point,se=estimate['point'],estimate['se']
        if type(point) not in (int,float) or type(se) not in (int,float) or not math.isfinite(point) or not math.isfinite(se) or se<=0: raise ValueError('Invalid published estimate/SE')
        significant=abs(point/se)>=1.96
    elif reported in {'sig','notsig'}: significant=reported=='sig'
    else: return 'UNCLASSIFIED'
    sign=candidate.get('direction_a_i')
    if not significant or sign==0: return 'NULL'
    if type(sign) is not int or sign not in (-1,1) or type(direction) is not int or direction not in (-1,1): return 'UNCLASSIFIED'
    return 'POS' if sign*direction==1 else 'WRONG'


def validate_pub_ledger(value, direction, expected_sha, targets_document=None, targets_sha256=None, design_id=None):
    if not isinstance(value,dict): raise ValueError('Publication ledger must be an object')
    _blind(value, ('adoption_dates_read',))
    if hashlib.sha256(encoded(value)).hexdigest()!=expected_sha:
        raise ValueError('Publication ledger canonical SHA-256 differs')
    targets=value.get('targets_candidates'); rows=value.get('rows')
    if not isinstance(targets,list) or not isinstance(rows,list) or len(rows)!=len(targets):
        raise ValueError('Ledger must cover every locked TARGETS candidate')
    if value.get('targets_candidates_sha256')!=hashlib.sha256(encoded(targets)).hexdigest():
        raise ValueError('Locked TARGETS candidate inventory hash differs')
    if targets_document is None or targets_sha256 is None:
        raise ValueError('Original locked TARGETS file required for publication inventory validation')
    if value.get('targets_file_sha256')!=targets_sha256:
        raise ValueError('Original TARGETS file SHA-256 differs')
    designs=targets_document.get('designs',[])
    matched=[r for r in designs if r.get('design_id')==design_id]
    if len(matched)!=1: raise ValueError('TARGETS design identity absent or duplicated')
    original=matched[0].get('candidates',[])
    if len(targets)<len(original) or targets[:len(original)]!=original:
        raise ValueError('Publication ledger omits or changes an original TARGETS candidate')
    if any(not candidate.get('new_candidate') for candidate in targets[len(original):]):
        raise ValueError('Additional publication candidate needs declared logged search')
    indices=[r.get('candidate_index') for r in rows]
    if any(type(i) is not int for i in indices) or sorted(indices)!=list(range(len(targets))): raise ValueError('Publication candidate inventory differs')
    for row in rows:
        candidate=targets[row['candidate_index']]
        year=row.get('first_public_year')
        if year is None:
            if type(candidate.get('year')) is not int or row.get('year_is_upper_bound') is not True:
                raise ValueError('Missing first public year requires flagged TARGETS upper bound')
        elif type(year) is not int or not _text(row.get('first_public_source')):
            raise ValueError('First public year and source required')
        reported=row.get('reported_significance')
        if reported not in {'sig','notsig',None}: raise ValueError('Invalid reported significance')
        if reported is not None and not _text(row.get('reported_significance_locator')):
            raise ValueError('Authors wording locator required')
        if candidate.get('new_candidate') and len(set(candidate.get('search_queries',[])))<3:
            raise ValueError('New publication candidate requires three distinct search queries')
        if row.get('class')!=publication_class(candidate,direction,reported): raise ValueError('Publication class differs from preregistered rule')
    return copy.deepcopy(value)


def contract(card, treatment=None, targets_document=None, targets_sha256=None):
    """Missing blind coding remains null; never derive it from empirical results."""
    source=card.get('l3_amendment_v2',{})
    universe=card.get('treatment',{}).get('universe',[])
    first=card.get('first_order',{}).get('purpose_direction')
    value=dict(schema=SCHEMA, amendment_path=AMENDMENT_PATH, amendment_sha256=AMENDMENT_SHA256,
        outcome_compute_stage='S6_after_final_freeze', no_l3_hypothesis_tests=True,
        field_status={f:'REGISTERED_S6' if f in {'F11','F12','F16'} else 'FIELD_MISSING' for f in FIELDS},
        placebo_group=None, loo_control_att=None, statute_variant=None, interruption_ledger=None,
        pub_ledger=None, pub_ledger_sha256=None, group_time=None,
        definitions=dict(F11='Every fixed RI draw; mean of all supported post cells per cohort, identical to group[].att; retain infeasible indices without redraw',
            F12='Rebuild frozen estimator without each control in certified risk set; same pooled-post cohort mean; record unsupported omissions',
            F13='Two statute-only blind coders, third on disagreement; compare each treated state to first-cohort predicate; locator required',
            F14='Every post-onset state-year with searched absence; two coders agree on interruption; explicit full-universe coverage/as-of certification',
            F15='Two paper-only blind coders; complete hashed candidate ledger before first outcome calculation; earliest public version and rule-derived class',
            F16='Calendar year for every primary cell, including subannual outcome clocks; no inferred subannual g+event_time'))
    for field,validator in [('statute_variant',lambda v:validate_statute_variant(v,universe)),
                            ('interruption_ledger',lambda v:validate_interruption_ledger(v,universe,treatment)),
                            ('pub_ledger',lambda v:validate_pub_ledger(v,first,source.get('pub_ledger_sha256'),targets_document,targets_sha256,card.get('design_id')))]:
        if source.get(field) is not None:
            value[field]=validator(source[field])
            field_id=next(f for f,n in FIELDS.items() if n==field)
            value['field_status'][field_id]='AVAILABLE'
    if value['statute_variant'] is not None:
        treated={r['unit'] for r in (treatment or {}).get('records',[]) if r.get('status') in {'treated','repealed','reverted','not_yet_treated'}}
        coded={r['unit'] for r in value['statute_variant']}
        if treatment is None or not treated or coded!=treated or any(r['variant']=='unknown' for r in value['statute_variant']):
            value['field_status']['F13']='FIELD_MISSING'
    if value['interruption_ledger'] is not None and (treatment is None or not value['interruption_ledger']['interruption_coverage_certified']):
        value['field_status']['F14']='NO_EVENTS_FIELD'
    if value['pub_ledger'] is not None: value['pub_ledger_sha256']=source['pub_ledger_sha256']
    return value


def context_contract(card, repo, treatment=None):
    source=card.get('l3_amendment_v2',{})
    targets=None; digest=None
    if source.get('pub_ledger') is not None:
        relative=source.get('targets_file',TARGETS_PATH)
        if relative!=TARGETS_PATH: raise ValueError('Publication ledger must use the amendment-locked TARGETS path')
        path=public_input(Path(repo)/relative)
        if path.name!='TARGETS.json' or not path.resolve().is_relative_to(Path(repo).resolve()):
            raise ValueError('C70 publication source must be a repository TARGETS.json')
        digest=sha(path)
        if digest!=TARGETS_SHA256: raise ValueError('Amendment-locked TARGETS bytes changed')
        targets=json.loads(path.read_text())
    return contract(card,treatment,targets,digest)


def retain(ctx):
    if protected(ctx.design_dir): return None
    card=copy.deepcopy(dict(ctx.card.payload))
    first=card.setdefault('first_order',{})
    for ruling in sorted(ctx.design_dir.glob('PURPOSE_HUB_RULING*.json')):
        patch=json.loads(ruling.read_text()).get('card_patch',{}).get('first_order',{})
        if patch.get('target_outcome')!=first.get('target_outcome'): continue
        for key in ('purpose_direction','purpose_direction_evidence'):
            if key in patch: first[key]=patch[key]
    if first.get('purpose_direction') is not None:
        from .agents.purpose import validate_purpose_direction
        validate_purpose_direction(first)
    treatment_path=ctx.design_dir/'TREATMENT.json'
    treatment=json.loads(treatment_path.read_text()) if treatment_path.exists() else None
    return ensure_once(ctx.design_dir/SPEC_NAME,context_contract(card,getattr(ctx,'repo',ctx.design_dir),treatment))


def merge_registration(registration, amendment):
    value=copy.deepcopy(registration or {})
    if amendment is None or amendment.get('status')=='fields unavailable':
        value['amendment_v2']=dict(schema=SCHEMA,status='fields unavailable',field_status={f:'FIELD_MISSING' for f in FIELDS},reason='No frozen C70 supplemental specification')
    else:
        if amendment.get('schema')!=SCHEMA or amendment.get('amendment_sha256')!=AMENDMENT_SHA256:
            raise ValueError('C70 amendment identity differs')
        value['amendment_v2']=copy.deepcopy(amendment)
    return value


def frozen_contract(root):
    """S7 can export only the amendment already bound by the design's freeze."""
    root=Path(root)
    for name in ('FREEZE_FINAL.json','FREEZE_PREP.json'):
        manifest_path=root/name
        if not manifest_path.is_file(): continue
        manifest=json.loads(manifest_path.read_text())
        binding=manifest.get('files',{}).get(SPEC_NAME)
        if binding is None: return None
        path=root/SPEC_NAME
        if not path.is_file() or sha(path)!=binding.get('sha256'):
            raise ValueError('Frozen C70 supplemental specification absent or changed')
        return json.loads(path.read_text())
    return None
