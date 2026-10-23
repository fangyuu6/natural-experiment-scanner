"""C44 one-time declaration transport and intake-bug recovery; no dispatch."""
from copy import deepcopy
from pathlib import Path
from .autopilot import Autopilot, REGISTRY, read, atomic, scheduler_lock
from .storage import ensure_once, sha
from .c42 import completeness, register_exclusions


def bind(path): return dict(path=str(Path(path).resolve()),sha256=sha(path))


def upgrade(pilot=None):
    pilot=pilot or Autopilot();root=pilot.registry.parent/'autopilot/c44'
    if (root/'UPGRADE_RECEIPT.json').exists(): raise ValueError('C44 already transported; no repeated recovery')
    with scheduler_lock(pilot.registry):
        if not (pilot.registry/'AUTOPILOT_STOP').exists(): raise ValueError('Clean stop required before C44')
        manifest=read(pilot.registry/'ACTIVE_DESIGNS.json');queue=read(pilot.registry/'HUB_QUEUE.json')
        ensure_once(root/'ACTIVE_DESIGNS_BEFORE.json',manifest);ensure_once(root/'HUB_QUEUE_BEFORE.json',queue)
        declarations={r['design_id']:r for r in read(root/'SEEDED_CARD_RECEIPT.json')['rows']}
        health={p.stem for p in (pilot.registry.parent/'intake/bulk_20261010/health/cards').glob('*.json')}
        if len(health)!=11 or len(declarations)!=7: raise ValueError('C44 expected 11 health and 7 seeded cards')
        updated=[];reports=[];superseded=[]
        for old in manifest['designs']:
            did=old['design_id']
            if did not in health and did not in declarations:
                updated.append(old);continue
            row=dict(old,attempt_id='c44')
            if did in health:
                if pilot.done(old,'history'): raise ValueError('Health recovery must precede successful history')
                reports.append(dict(design_id=did,status='HISTORY_RECOVERED',old_attempt=old['attempt_id']))
            else:
                declaration=declarations[did];card=read(declaration['card']['path'])
                if completeness(card):raise ValueError('C44 declaration incomplete: '+did)
                row.update(card=declaration['card']['path'],card_sha256=declaration['card']['sha256'],
                           purpose_ruling=declaration['purpose_ruling'])
                history=pilot.done(old,'history')
                if history:ensure_once(pilot.checkpoint(row,'history'),history)
                guidance=declaration['disposition_guidance']
                if guidance=='PARKED_C43':
                    evidence=bind(root.parent/'c43/recheck'/did/'RECHECK.json')
                    ensure_once(pilot.case_dir(row)/'PARKED.json',dict(design_id=did,attempt_id='c44',
                        action='reconcile',status='PARKED',reason='C43 metadata/support/fresh gates remain binding',
                        evidence=evidence,rule='C43_STATUS.md; C44 unchanged gates'))
                elif guidance=='NEEDS_SOURCE_REPAIR_C42':
                    stop=dict(read(pilot.case_dir(old)/'STOP.json'),attempt_id='c44')
                    ensure_once(pilot.case_dir(row)/'STOP.json',stop)
                elif guidance=='WAIT_EXISTING_AB_OR_AUDIT_NO_REDISPATCH':
                    # Preserve the existing legal packet/sample; declaration-only
                    # changes do not authorize another agent or redraw.
                    rec=pilot.done(old,'reconcile')
                    if rec:
                        ensure_once(pilot.checkpoint(row,'reconcile'),rec)
                        inventory=read(pilot.case_dir(old)/'jobs/audit.json')
                        if inventory:ensure_once(pilot.case_dir(row)/'jobs/audit.json',inventory)
                        ctx=pilot.backend.ctx(row)
                        previous=read(pilot.backend.root(old)/'C42_PREPARATION.json')
                        register_exclusions(ctx,previous['prepared_payload']['treatment'],previous['evidence'])
                        for name in ('RECONCILIATION.json','CODING_LOCK.json','AUDIT_SELECTION.json'):
                            ensure_once(ctx.design_dir/name,read(pilot.backend.root(old)/name))
                elif guidance!='FRESH_V3_PENDING_C43':raise ValueError('Unknown C44 disposition')
                reports.append(dict(design_id=did,status=guidance,card=declaration['card']))
            updated.append(row)
        retained=[]
        for entry in queue['entries']:
            did=entry['design_id']
            if did in health or (did in declarations and entry['status']=='NEEDS_CARD_DECLARATION'):
                superseded.append(entry)
                pilot.ledger(entry,entry['action'],'SUPERSEDED_C44','record',prior_entry=entry,new_attempt='c44')
            elif did in declarations:
                retained.append(dict(entry,attempt_id='c44'))
            else:retained.append(entry)
        atomic(pilot.registry/'ACTIVE_DESIGNS.json',dict(manifest,designs=updated))
        atomic(pilot.registry/'HUB_QUEUE.json',dict(queue,entries=retained))
        # Prepare only the authorized seatbelt fresh audit, with original A/B.
        seat=next(r for r in updated if r['design_id']=='c35_cspp_regulation_mandatory_seatbelts')
        result=pilot.backend.perform(seat,'reconcile',pilot)
        ensure_once(pilot.checkpoint(seat,'reconcile'),result)
        pilot.ledger(seat,'reconcile','FRESH_V3_PREPARED','accept',**result)
        value=dict(schema='l2_c44_upgrade_v1',designs=reports,superseded=superseded,
            health_recovered=11,seeded_cards=7,seatbelt_audit=result,new_downloads=[],
            dispatches=0,actual_data_estimation_calls=0,outcome_values_read=0)
        ensure_once(root/'UPGRADE_RECEIPT.json',value)
        return value

if __name__=='__main__':
    result=upgrade();print('C44 recovered health:',result['health_recovered'],'seeded:',result['seeded_cards'])
