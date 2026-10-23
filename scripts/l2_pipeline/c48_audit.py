"""V3.1 outcome-blind, evidence-bound unresolved-state sensitivity overlay."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from .storage import ensure_once,sha,public_input

RULE='docs/plans/l2-pipeline/HUB_RULINGS_C48.md'
NAME='drop_unresolved_audit_dates'
FROZEN=('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','TERMINAL.json')
PARK_PREFIX='QUEUE_PARK: INSUFFICIENT_WORK (engineering event, reopenable): '
PARK_RULE=('V3.1 2026-10-19: a stored V3.1 value that differs from its own re-derivation is an engineering fault, never a verdict on the '
           'design; before the freeze it is a reopenable PARK (release_recovery, card-bound requeue), not a STOP')


def drift(ctx,reason):
    """The error for a stored V3.1 value that no longer equals its re-derivation (a record older than the code or the order that
    wrote it). A frozen/sealed design keeps its ValueError (its code snapshot is the authority); an earlier one PARKs, reopenably."""
    if any((ctx.design_dir/n).exists() for n in FROZEN):return ValueError(reason)
    from .c42 import Park
    return Park(PARK_PREFIX+reason,dict(kind='QUEUE_PARK',reopen='release_recovery',outcome_values_read=0,rule=PARK_RULE))


def bound_comparison(evidence):
    """Reproduce the overlay from the immutable fresh audit and reconciliation."""
    import json
    from .intake.official_history import corrected_comparison
    from .intake.history_routing import select_fresh_sample
    paths={Path(item['path']).name:Path(item['path']) for item in evidence}
    if not {'RECONCILIATION.json','OFFICIAL_AUDIT.json'} <= set(paths):
        raise ValueError('V3.1 requires bound fresh audit and reconciliation')
    report=json.loads(public_input(paths['RECONCILIATION.json']).read_text())
    answer=json.loads(public_input(paths['OFFICIAL_AUDIT.json']).read_text())
    sample=select_fresh_sample(report['audit_eligible_records'],report['fresh_policy'])
    # Owner D8 (ruling C85): a 1-9 state remainder audited as a census is a valid fresh V3
    # only with its bound census receipt; the ten-state rule is unchanged otherwise.
    from .c85 import census_bound
    census=census_bound(paths['RECONCILIATION.json'],sample)
    comparison,audit,_=corrected_comparison(report['historical_comparison'],answer['checks'],sample,remainder_census=census,
        conflict_units=report.get('audit_conflict_units'),conflict_checks=answer.get('conflict_checks'))
    if not audit['passed']:
        from .c42 import Park
        raise Park('V3.1 fresh audit did not pass',audit)
    return comparison


def declaration(payload,comparison):
    unresolved=sorted(r['unit'] for r in comparison['accepted'] if r.get('audit_unresolved'))
    if not unresolved:return None
    # corrected_comparison only retains the independently reconciled accepted
    # tuples. C42 has already excluded every A/B disagreement/unknown history.
    excluded=sorted(set(payload['treatment'].get('excluded_units',[]))|set(unresolved))
    sensitivities=deepcopy(payload['sensitivities'])
    existing=next((s for s in sensitivities if s['name']==NAME),None)
    expected=dict(name=NAME,changes=dict(treatment=dict(excluded_units=excluded)))
    if existing:
        changes=existing.get('changes',{});treatment=changes.get('treatment',{})
        if set(changes)!={'treatment'} or set(treatment)!={'excluded_units'}:
            raise ValueError('V3.1 sensitivity has unrelated scientific changes')
        expected['changes']['treatment']['excluded_units']=sorted(set(treatment['excluded_units'])|set(excluded))
        sensitivities[sensitivities.index(existing)]=expected
    else:sensitivities.append(expected)
    return dict(unresolved_units=unresolved,sensitivities=sensitivities)


def register(ctx,comparison,evidence):
    value=declaration(ctx.card.payload,comparison)
    if value is None:return ctx
    if any((ctx.design_dir/n).exists() for n in FROZEN):
        raise ValueError('V3.1 cannot revise a frozen/sealed/finished design')
    ruling=ctx.repo/RULE
    if (ctx.design_dir/'C48_AUDIT_PREPARATION.json').exists():
        import json
        prior=json.loads((ctx.design_dir/'C48_AUDIT_PREPARATION.json').read_text())
        if prior['comparison']!=comparison or prior['evidence']!=list(evidence)+[dict(path=str(ruling),sha256=sha(ruling))]:
            raise drift(ctx,'V3.1 registered audit changed')
        return effective_context(ctx)
    ensure_once(ctx.design_dir/'C48_AUDIT_PREPARATION.json',dict(schema='l2_v31_audit_sensitivity_v1',
        design_id=ctx.card.design_id,version=ctx.card.version,original_card_sha256=sha(ctx.card.path),
        base_sensitivities=deepcopy(ctx.card.payload['sensitivities']),comparison=deepcopy(comparison),
        evidence=list(evidence)+[dict(path=str(ruling),sha256=sha(ruling))],
        outcome_values_read=0,execution_authorized=False,**value))
    return effective_context(ctx)


def effective_context(ctx):
    import json
    path=ctx.design_dir/'C48_AUDIT_PREPARATION.json'
    if not path.exists():return ctx
    value=json.loads(path.read_text())
    if (value.get('schema')!='l2_v31_audit_sensitivity_v1' or value['design_id']!=ctx.card.design_id
        or value['version']!=ctx.card.version or value['original_card_sha256']!=sha(ctx.card.path)
        or value.get('outcome_values_read')!=0 or value.get('execution_authorized') is not False):
        raise ValueError('V3.1 card binding changed')
    for item in value['evidence']:
        if sha(public_input(Path(item['path'])))!=item['sha256']:raise ValueError('V3.1 evidence/ruling changed')
    if value['comparison']!=bound_comparison(value['evidence']):
        raise drift(ctx,'V3.1 comparison differs from bound fresh audit')
    payload=deepcopy(dict(ctx.card.payload))
    current=payload['sensitivities']
    base=deepcopy(payload);base['sensitivities']=value['base_sensitivities']
    expected=declaration(base,value['comparison'])
    stored=dict(unresolved_units=value['unresolved_units'],sensitivities=value['sensitivities'])
    effective=value['sensitivities']
    if expected!=stored:
        # A file written at the S1 entry, before the fresh audit's dated conflict states left the conflict exclusions (AUDIT_RESOLUTIONS.json,
        # written when S1 accepts the audit), equals the derivation from the card as it stood then. Only that older derivation is accepted
        # besides the current one, and the card as it stands now (the rule, applied to the dated card) supplies the sensitivity.
        earlier=restored(base,ctx)
        if expected is None or earlier is None or declaration(earlier,value['comparison'])!=stored:
            raise drift(ctx,'V3.1 mechanical sensitivity differs')
        effective=expected['sensitivities']
    if current not in (value['base_sensitivities'],value['sensitivities'],effective):
        raise ValueError('V3.1 sensitivity binding changed')
    payload['sensitivities']=deepcopy(effective)
    return replace(ctx,card=replace(ctx.card,payload=payload))


def restored(payload,ctx):
    """``payload`` with the states the fresh audit dated put back among the exclusions (the card before AUDIT_RESOLUTIONS.json); None if there are none."""
    from .intake.official_history import load_audit_resolutions
    audited=load_audit_resolutions(ctx.design_dir)
    resolved={r['unit'] for r in audited['resolved']} if audited else set()
    if not resolved:return None
    earlier=deepcopy(payload)
    earlier['treatment']['excluded_units']=sorted(set(earlier['treatment'].get('excluded_units',[]))|resolved)
    return earlier


def prepare_s1(ctx,job_path):
    """C49 §4: register V3.1 on the S1 entry path before history certification."""
    from .agents.jobs import load_job
    from .autopilot_history import validate_job,read,bind
    job=load_job(Path(job_path));validate_job(job)
    collection=read(Path(job.output_dir)/'COLLECTION.json')
    if (collection.get('validated') is not True or collection['original_sha256']!=sha(job.output_path)
        or collection['promoted_sha256']!=sha(job.promote_path)):
        raise ValueError('Fresh audit collector bindings changed')
    evidence=[bind(job.output_path),bind(job.promote_path),bind(Path(job.output_dir)/'COLLECTION.json'),bind(job.contract['reconciliation_path'])]
    comparison=bound_comparison(evidence)
    return register(date_conflict_states(ctx,job),comparison,evidence)


def date_conflict_states(ctx,job):
    """The sensitivity must come from the card as it will stand once the states the fresh audit dated leave the conflict exclusions, so
    those resolutions are written first (the same bytes accept() writes a moment later; the audit has passed: bound_comparison checked)."""
    from .autopilot_history import bind,read
    from .c42 import effective_context as c42_context
    from .intake.official_history import audit_resolutions,RESOLUTIONS
    audited=audit_resolutions(read(job.contract['reconciliation_path']),read(job.promote_path),
        dict(reconciliation=bind(job.contract['reconciliation_path']),audit_answer=bind(job.promote_path),audit_original=bind(job.output_path)))
    if not audited:return ctx
    ensure_once(ctx.design_dir/RESOLUTIONS,audited)
    return c42_context(ctx)
