"""Explicit hub ruling reopens an unfrozen stopped design under a new card.

No dispatch, fitting, sample redraw or mutation of old design artifacts.
"""
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from .autopilot import Autopilot,read,atomic,scheduler_lock,utc,durable
from .storage import ensure_once,sha
from .cards import load_card


KEPT_PURPOSE_RULINGS=('PURPOSE_MECHANICAL_RULING_V63.json','PURPOSE_HUB_RULING_V62.json')
FRESH_HISTORY_RULE='R5'   # ruling key `fresh_history_rule`: one fresh history attempt for a RECONCILE_FLOOR park (HUB_RULING_20261020_PARK_TRIAGE, question C)
FLOOR_REASON='V7 retained treated below minimum'
PURPOSE_UNCHANGED=('first_order','population','outcome')  # the card parts an accepted purpose is about


def lineage(pilot,row,limit=12):
    """`row`, then the rows it was requeued from, newest first, following the retained REQUEUE receipts."""
    seen={row['attempt_id']};current=row
    for _ in range(limit):
        yield current
        parent=None
        for path in sorted((pilot.registry.parent/'autopilot/requeues'/row['design_id']).glob('*/REQUEUE.json')):
            receipt=read(path)
            if receipt and receipt['new_row']['attempt_id']==current['attempt_id'] and receipt['prior_row']['attempt_id'] not in seen:
                parent=receipt['prior_row'];break
        if parent is None:return
        seen.add(parent['attempt_id']);current=parent


def retained_reason(pilot,old):
    """The stop/park text of a design's current attempt."""
    return (pilot.stop_record(old) or read(pilot.case_dir(old)/'PARKED.json',{}) or {}).get('reason','')


def fresh_history(pilot,old,stop,decision):
    """True when the ruling asks for one fresh history attempt (opt-in key `fresh_history_rule`); refuses anything but a floor PARK."""
    if 'fresh_history_rule' not in decision:return False
    if decision['fresh_history_rule']!=FRESH_HISTORY_RULE:raise ValueError('Invalid fresh_history_rule')
    parked=read(pilot.case_dir(old)/'PARKED.json')
    if stop or not parked or parked.get('action')!='reconcile' or not retained_reason(pilot,old).startswith(FLOOR_REASON):
        raise ValueError('Fresh history requires a RECONCILE_FLOOR park of the current attempt')
    if decision.get('decision')=='accept':raise ValueError('Fresh history cannot be combined with purpose acceptance')
    return True


def curated_retry(pilot,old,decision,new_payload,card):
    """True when the card-bound ruling asks for the Q2 extra curated-audit round (opt-in key `curated_retry_rule`, HUB_RULING_20261023_PARK_REOPEN s.4);
    curated_route.validate_retry refuses anything but the one round for one of the four named designs."""
    if 'curated_retry_rule' not in decision:return False
    if 'fresh_history_rule' in decision:raise ValueError('Curated retry cannot be combined with a fresh history rule')
    from .curated_route import validate_retry
    validate_retry(pilot,old,decision,new_payload,card);return True


def kept_purpose(pilot,old,new_payload):
    """(row, ruling path, ruling) of the nearest attempt of this design that has an accepted purpose, if it still applies.

    YIELD_DIAGNOSTIC_20261017 class B: `resume` dropped an accepted purpose, so the re-run purpose text (a prose line, never
    the card's machine id) could not be accepted again. The ruling is kept only if the card parts it is about are unchanged."""
    for row in lineage(pilot,old):
        if pilot.done(row,'purpose') is None:continue
        previous=read(row['card'])
        if not previous or any(previous.get(k)!=new_payload.get(k) for k in PURPOSE_UNCHANGED):return None
        pointer=row.get('purpose_ruling');root=pilot.backend.root(row)
        for path in ([Path(pointer['path'])] if pointer else [root/n for n in KEPT_PURPOSE_RULINGS]):
            if not path.is_file() or (pointer and sha(path)!=pointer['sha256']):continue
            ruling=read(path)
            if (ruling.get('decision')=='accept' and ruling.get('purpose_rule') in {'V6.2','V6.3'}
                    and ruling.get('card_sha256')==sha(row['card'])):return row,path,ruling
        return None
    return None


def requeue(design,card,ruling,pilot=None,*,scheduler_owned=False):
    pilot=pilot or Autopilot();card=durable(Path(card).resolve(),pilot.repo);ruling=Path(ruling).resolve()
    with (nullcontext() if scheduler_owned else scheduler_lock(pilot.registry)):
        decision=read(ruling)
        mechanical=decision.get('purpose_rule')=='V6.3'
        window=decision.get('mechanical_window_rule')=='C50 §2'
        superseded_status=decision.get('superseded_status','SUPERSEDED_C46')
        if superseded_status not in {'SUPERSEDED_C46','SUPERSEDED_C47','SUPERSEDED_C48','SUPERSEDED_C49','SUPERSEDED_C50','SUPERSEDED_C51','SUPERSEDED_C52','SUPERSEDED_C53','SUPERSEDED_C54','SUPERSEDED_C55','SUPERSEDED_C57','SUPERSEDED_C59','SUPERSEDED_C62','SUPERSEDED_C63','SUPERSEDED_C85','SUPERSEDED_GATE0_CATALOG','SUPERSEDED_YIELD_FIXES','SUPERSEDED_HISTORY_REUSE','SUPERSEDED_CURATED_ROUTE','SUPERSEDED_NAMED_PANEL_Q1','SUPERSEDED_FORECAST_MODEL_ID','SUPERSEDED_INTAKE_PANEL_Q4','SUPERSEDED_PURPOSE_KEPT','SUPERSEDED_REDESIGN_R1','SUPERSEDED_RELEASE_RECOVERY','SUPERSEDED_GATE_RELAX_R31','SUPERSEDED_S3_COHORT_RS3C1'}:raise ValueError('Invalid superseded status')
        c57=decision.get('implementation_rule')=='C57'
        c54=decision.get('implementation_rule')=='C54 §2'
        q1=decision.get('implementation_rule')=='NAMED_PANEL_Q1'  # 2026-10-16 structural exclusions on S3-stopped cards
        redesign=decision.get('implementation_rule')=='REDESIGN_R1'  # 2026-10-17 rules-file reopen: new card = old card + automation.rules
        gate_relax=decision.get('implementation_rule')=='GATE_RELAX_R31'  # 2026-10-22 R3.1 RI floor 0.80 for five named designs
        s3_cohort=decision.get('implementation_rule')=='S3_COHORT_RS3C1'  # 2026-10-22 R-S3C1 cohort-interruption rule for five named designs
        if scheduler_owned and not (mechanical or window or c54 or redesign):raise ValueError('Live requeue requires mechanical V6.3 or verified C54 implementation ruling')
        if not scheduler_owned and not (pilot.registry/'AUTOPILOT_STOP').exists():raise ValueError('Requeue requires stopped scheduler')
        records=[read_line for read_line in (pilot.registry/'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()] if (pilot.registry/'AUTOPILOT_LEDGER.jsonl').exists() else []
        import json
        if not scheduler_owned and not any(json.loads(s).get('decision')=='CLEAN_STOP' for s in records):raise ValueError('Observed CLEAN_STOP required')
        decision=read(ruling);new_card=load_card(card);manifest=read(pilot.registry/'ACTIVE_DESIGNS.json')
        matches=[r for r in manifest['designs'] if r['design_id']==design]
        if len(matches)!=1:raise ValueError('Requeue requires unique active design')
        old=matches[0];receipt_dir=pilot.registry.parent/'autopilot/requeues'/design/new_card.version
        existing=read(receipt_dir/'REQUEUE.json')
        if existing:
            if existing['new_card_sha256']!=sha(card) or existing['ruling_sha256']!=sha(ruling):raise ValueError('Requeue inputs changed')
            if old['card_sha256']==existing['prior_row']['card_sha256']:
                if old!=existing['prior_row']:raise ValueError('Pending requeue prior row changed')
                manifest['designs']=[existing['new_row'] if r['design_id']==design else r for r in manifest['designs']]
                atomic(pilot.registry/'ACTIVE_DESIGNS.json',manifest)
            elif old['card_sha256']!=sha(card):raise ValueError('Requeued manifest changed')
            queue=read(pilot.registry/'HUB_QUEUE.json',dict(entries=[]))
            for entry in queue['entries']:
                if any((entry['design_id'],entry['attempt_id'])==(v['design_id'],v['attempt_id']) for v in existing['superseded']) and entry.get('status')!=superseded_status:
                    entry.update(status=superseded_status,prior_status=entry['status'],new_attempt=existing['new_row']['attempt_id'],ruling_sha256=sha(ruling),superseded_at=utc())
            atomic(pilot.registry/'HUB_QUEUE.json',queue)
            return existing
        if c54:
            from .c54 import validate_implementation_ruling
            validate_implementation_ruling(pilot,decision,card)
        if c57:
            from .c57 import validate_rebind
            validate_rebind(pilot,old,decision,card)
        if old.get('status')=='COMPLETED' or pilot.next_action(old)=='FINISHED':raise ValueError('Finished design cannot requeue')
        oldroot=pilot.backend.root(old)
        if not c57 and any((oldroot/n).exists() for n in ('FREEZE_PREP.json','FREEZE_FINAL.json','APPROVAL_PREP.json','APPROVAL_FINAL.json','TERMINAL.json')):
            raise ValueError('Frozen/sealed design cannot requeue')
        if sha(old['card'])!=old['card_sha256']:raise ValueError('Prior card bytes changed')
        stop=pilot.stop_record(old)
        if not stop and not (pilot.case_dir(old)/'PARKED.json').exists() and not ((mechanical or window) and (pilot.case_dir(old)/'MECHANICAL_REQUEUE.json').exists()):raise ValueError('Design is not stopped/parked')
        fresh=fresh_history(pilot,old,stop,decision)
        retry=curated_retry(pilot,old,decision,new_card.payload,card)  # Q2: one extra blind-audit round as a new attempt, restart at history
        if ('impact_tags' in decision or decision.get('implementation_rule')) and not redesign and not gate_relax and not s3_cohort:  # gate_relax checks its own registered reason in validate_ruling
            from .maintenance import require_match
            retained_reason=(stop or read(pilot.case_dir(old)/'PARKED.json',{})).get('reason','')
            require_match(retained_reason,decision.get('impact_tags'))
        previous=read(old['card'])
        if new_card.design_id!=design or new_card.version==previous['version']:raise ValueError('New card version for same design required')
        if (decision.get('schema')!='l2_requeue_ruling_v1' or decision.get('design_id')!=design or
            decision.get('card_sha256')!=sha(card) or decision.get('prior_card_sha256')!=sha(old['card']) or
            decision.get('decision') not in {'accept','park','resume'} or not decision.get('reason')):raise ValueError('Invalid card-bound hub ruling')
        for key in ('purpose_evidence',):
            if key in decision and sha(decision[key]['path'])!=decision[key]['sha256']:raise ValueError('Ruling evidence bytes changed')
        authority=decision['authority']
        if sha(authority['path'])!=authority['sha256']:raise ValueError('Hub authority bytes changed')
        if redesign:
            from .rules import validate_requeue
            validate_requeue(pilot,old,new_card.payload,decision)
        if q1:
            from .named_panel import validate_q1_ruling
            validate_q1_ruling(pilot,old,new_card.payload,decision,card)
        if gate_relax:
            from .gate_relax import validate_ruling as validate_gate_relax
            validate_gate_relax(pilot,old,new_card.payload,decision,card)
        if s3_cohort:
            from .cohort_censor_rule import validate_ruling as validate_s3_cohort
            validate_s3_cohort(pilot,old,new_card.payload,decision,card)
        if window:
            from .c50 import validate_window_ruling
            validate_window_ruling(pilot,old,new_card.payload,decision)
        if mechanical:
            from .population_resolution import validate_ruling
            validate_ruling(pilot,old,new_card.payload,decision)
        changed={k for k in set(previous)|set(new_card.payload) if previous.get(k)!=new_card.payload.get(k)}-{'version'}
        if q1:changed.discard('treatment')  # validate_q1_ruling proved that only excluded_units differs there; no new legal intake
        legal={'treatment','mechanism','policy_family','unit','track'}
        def canonical_sources(value,parent):
            records=deepcopy([r for r in value['sources'] if r['source_id']!='outcome'])
            for record in records:
                if record.get('path'):record['path']=str((parent/record['path']).resolve())
            return records
        if canonical_sources(previous,Path(old['card']).parent)!=canonical_sources(new_card.payload,new_card.path.parent):legal.add('sources')
        # An unchanged assignment permits retaining independently verified A/B,
        # reconciliation and fresh V3 bytes. Never preserve numerical readiness.
        restart='history' if changed&legal else 'purpose'
        if changed-legal-{'outcome','population','first_order','outcome_scale','outcome_family','analysis','automation','sensitivities','sources'}:
            raise ValueError('Unsupported card change requires explicit new intake')
        if restart=='history' and decision['decision']=='accept':raise ValueError('Legal changes require new independent intake, not purpose acceptance')
        row=deepcopy(old);row.update(card=str(card),card_sha256=sha(card),attempt_id='requeue_'+new_card.version)
        row.pop('purpose_ruling',None);row.pop('status',None)
        root=pilot.backend.root(row)
        if root.exists():raise ValueError('New design version directory already exists')
        retained=[]
        if decision['decision']=='accept' and any(pilot.done(old,a) is None for a in ('history','reconcile','audit')):raise ValueError('Purpose acceptance requires complete retained legal gates')
        if fresh:
            # Nothing of the old attempt's legal gates is kept; its pinned history jobs must not feed the new attempt either.
            restart='history';row.pop('history_jobs',None)
        elif retry:
            restart='history'   # nothing retained: the retry attempt builds and audits afresh (curated_route.same_audit pins it to the first audit)
        elif restart=='purpose':
            for action in ('history','reconcile','audit'):
                result=pilot.done(old,action)
                if result is None:restart=action;break
                ensure_once(pilot.checkpoint(row,action),result);retained.append(action)
                inventory=read(pilot.case_dir(old)/'jobs'/(action+'.json'))
                if inventory:ensure_once(pilot.case_dir(row)/'jobs'/(action+'.json'),inventory)
            if 'reconcile' in retained:
                for name in ('RECONCILIATION.json','CODING_LOCK.json','AUDIT_SELECTION.json'):
                    ensure_once(root/name,read(oldroot/name))
                overlay=read(oldroot/'C42_PREPARATION.json')
                if overlay:
                    from .c42 import register_exclusions
                    register_exclusions(pilot.backend.ctx(row),overlay['prepared_payload']['treatment'],overlay['evidence'])
        kept=None
        if decision['decision']=='resume' and restart=='purpose':
            kept=kept_purpose(pilot,old,new_card.payload)
            if kept:
                # Same state `accept` reaches: the accepted purpose, bound to the new card, and the direction step next.
                source,path,prior=kept
                rebound=ensure_once(root/path.name,dict(prior,card_sha256=sha(card),rebound_from=dict(path=str(path),sha256=sha(path)),
                    rebound_attempt=source['attempt_id'],execution_authorized=False))
                row['purpose_ruling']=dict(path=str(rebound),sha256=sha(rebound))
                ensure_once(pilot.checkpoint(row,'purpose'),read(rebound));retained.append('purpose');restart='purpose-direction'
        if redesign:
            if kept:
                # An earlier accepted purpose is the label 'target_matched'; under the new chain the design restarts at its first undone action.
                from . import rules as rules_module,redesign as redesign_module
                redesign_module._write_label(pilot.backend.ctx(row),rules_module.of_payload(new_card.payload,pilot.repo),'target_matched',
                    'ACCEPTED_EARLIER_RULING',True,True,[],None)
            restart=pilot.chain_next(row)
        if decision['decision']=='park':
            ensure_once(pilot.case_dir(row)/'PARKED.json',dict(design_id=design,attempt_id=row['attempt_id'],action=restart,status='PARKED',reason=decision['reason'],ruling=str(ruling),ruling_sha256=sha(ruling)))
        elif decision['decision']=='accept':
            if restart!='purpose':raise ValueError('Purpose acceptance requires complete retained legal gates')
            purpose=dict(decision,purpose_rule='V6.3' if mechanical else 'V6.2',execution_authorized=False)
            p=ensure_once(root/('PURPOSE_MECHANICAL_RULING_V63.json' if mechanical else 'PURPOSE_HUB_RULING_V62.json'),purpose)
            row['purpose_ruling']=dict(path=str(p),sha256=sha(p))
            ensure_once(pilot.checkpoint(row,'purpose'),purpose);restart='purpose-direction'
        queue=read(pilot.registry/'HUB_QUEUE.json',dict(entries=[]));superseded=[]
        for entry in queue['entries']:
            if entry['design_id']==design and not entry.get('status','').startswith('SUPERSEDED_'):
                superseded.append(deepcopy(entry));entry.update(status=superseded_status,prior_status=entry['status'],superseded_at=utc(),new_attempt=row['attempt_id'],ruling_sha256=sha(ruling))
        value=dict(schema='l2_requeue_receipt_v1',design_id=design,prior_row=old,new_row=row,
            new_card_sha256=sha(card),ruling=str(ruling),ruling_sha256=sha(ruling),retained_actions=retained,
            restart_action='PARK' if decision['decision']=='park' else restart,superseded=superseded,dispatches=0,actual_data_estimation_calls=0,
            **({'purpose_kept_from':kept[0]['attempt_id']} if kept else {}),**({'fresh_history':True} if fresh else {}),**({'curated_retry':True} if retry else {}))
        ensure_once(receipt_dir/'REQUEUE.json',value)
        manifest['designs']=[row if r['design_id']==design else r for r in manifest['designs']]
        atomic(pilot.registry/'ACTIVE_DESIGNS.json',manifest);atomic(pilot.registry/'HUB_QUEUE.json',queue)
        pilot.ledger(row,'requeue',superseded_status,'record',receipt=str(receipt_dir/'REQUEUE.json'),restart=value['restart_action'])
        return value
