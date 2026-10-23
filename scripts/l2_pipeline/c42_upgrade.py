"""One-time outcome-blind transport of the seven C41 seeds into C42."""
from copy import deepcopy
import json
from pathlib import Path

from .autopilot import Autopilot, REGISTRY, atomic, read, scheduler_lock
from .c42 import Park, completeness
from .storage import ensure_once, sha


def binding(path):
    return dict(path=str(Path(path).resolve()), sha256=sha(path))


def main():
    pilot=Autopilot(REGISTRY)
    root=REGISTRY.parent/'autopilot/c42'
    with scheduler_lock(REGISTRY):
        if (root/'UPGRADE_RECEIPT.json').exists():
            raise ValueError('C42 upgrade already completed; no repeated transport')
        manifest=read(REGISTRY/'ACTIVE_DESIGNS.json')
        queue=read(REGISTRY/'HUB_QUEUE.json')
        ensure_once(root/'ACTIVE_DESIGNS_BEFORE.json',manifest)
        ensure_once(root/'HUB_QUEUE_BEFORE.json',queue)
        template=Path(__file__).parent/'examples/design.json'
        defaults=deepcopy(read(template)['analysis'])
        defaults['min_post_periods']=2
        defaults['support_gap_rule']='drop_affected_state_v7'
        sources=[]; reports=[]; updated=[]
        for old in manifest['designs']:
            original=read(old['card']); value=deepcopy(original)
            catalog_path=Path(value['treatment']['catalog_path'])
            catalog=read(catalog_path)
            source_rows=[f for f in catalog['files'] if f['source_id']=='cspp']
            value['sources']=[dict(source_id='cspp_'+str(n),url=f['url'],license=f['license'],
                locator=value['treatment']['policy_id']+'; '+value['treatment']['predicate'],
                path=f['path'],sha256=f['sha256']) for n,f in enumerate(source_rows)]
            value['schema_version']='1.0';value['version']='v1';value['supersedes']=old['card']
            value['population']=dict(description=original['population']['target'],
                                      filters=original['population']['filters'])
            value['lineage']={}
            value['analysis']=deepcopy(defaults)
            value['sensitivities']=[]
            intake_path=(pilot.repo/'data/l2_pipeline/sources/research_panel'/
                         ('c35_20261010' if old['design_id'].startswith('c35_') else 'c36_20261010')/'INTAKE.json')
            intake=read(intake_path)
            intake_row=next(r for r in intake['rows'] if r['design_id']==old['design_id'])
            purpose_path=Path(old['purpose_ruling']['path']) if old.get('purpose_ruling') else (
                Path(old['card']).parent.parent/'purpose_work'/old['design_id']/original['version']/'PURPOSE_HUB_RULING_V62.json')
            purpose=read(purpose_path)
            if purpose['decision']!='accept' or purpose['card_sha256']!=sha(old['card']):
                raise ValueError('Existing purpose acceptance/card binding differs')
            value['first_order'].update(purpose['card_patch']['first_order'])
            value['first_order']['primary_outcome']=value['outcome']['measure_id']
            # Connector availability is an existing intake declaration, not
            # evidence of source/population support or permission to estimate.
            mapping=intake_row.get('mapping',{})
            if mapping.get('connector_exists') is True:
                value['first_order']['availability']='available'
            value['first_order']['secondary_outcomes']=[]
            allowed=set(read(Path(__file__).with_name('design_card.schema.json'))['properties'])
            intake_only={k:value.pop(k) for k in list(value) if k not in allowed}
            value.setdefault('automation',{})['intake']=dict(original_card=binding(old['card']),
                original_intake=binding(intake_path),original_fields=intake_only,
                purpose_acceptance=binding(purpose_path),analysis_default=binding(template),
                support_defaults=binding(Path(__file__).with_name('support.py')),
                post_period_rule='C35/C36 intake geometry: at least two post periods',
                provenance_catalog=binding(catalog_path),readiness_granted=False)
            card_path=ensure_once(root/'cards'/(old['design_id']+'.json'),value)
            errors=completeness(value)
            # Legal purpose transport does not certify the still missing
            # target-to-measure declaration or the downstream measurement gate.
            errors.append('$.first_order.target_outcome -> $.outcome.measure_id: exact registered same-construct mapping missing')
            row=dict(old,card=str(card_path),card_sha256=sha(card_path),attempt_id='c42')
            transport=dict(purpose,card_sha256=sha(card_path),transported_from=binding(purpose_path),
                original_card=binding(old['card']),scope='Existing purpose only; measurement and S1 remain uncertified')
            pp=ensure_once(root/'purpose_rulings'/(old['design_id']+'.json'),transport)
            row['purpose_ruling']=binding(pp)
            # Reuse only the completed history checkpoint; it points to the
            # unchanged A/B pair. No audit sample or numerical stage is copied.
            history=pilot.done(old,'history')
            if history:ensure_once(pilot.checkpoint(row,'history'),history)
            updated.append(row)
            report=dict(design_id=row['design_id'],card=binding(card_path),complete=False,
                        missing_or_invalid=errors,original_card=binding(old['card']))
            reports.append(report)
            sources.append(dict(design_id=row['design_id'],intake=binding(intake_path),purpose=binding(purpose_path)))
        stale={(r['design_id'],r['attempt_id']) for r in queue['entries'] if r['attempt_id']=='c41'
               and r['design_id'] in {r['design_id'] for r in updated}}
        retained=[r for r in queue['entries'] if (r['design_id'],r['attempt_id']) not in stale]
        for entry in queue['entries']:
            if (entry['design_id'],entry['attempt_id']) in stale:
                pilot.ledger(entry,entry['action'],'SUPERSEDED_C42','exclude',prior_entry=entry,new_attempt='c42')
        for report in reports:
            retained.append(dict(design_id=report['design_id'],attempt_id='c42',action='S0',
                status='NEEDS_CARD_DECLARATION',reason='; '.join(report['missing_or_invalid']),
                missing_fields=report['missing_or_invalid'],rule='C42 card completeness; no invented science'))
        atomic(REGISTRY/'HUB_QUEUE.json',dict(queue,entries=retained))
        atomic(REGISTRY/'ACTIVE_DESIGNS.json',dict(manifest,designs=updated))
        ensure_once(root/'CARD_COMPLETENESS.json',dict(cards=reports,sources=sources))
        dispositions=[]
        for row in updated:
            if pilot.next_action(row)!='reconcile':continue
            try:
                result=pilot.backend.perform(row,'reconcile',pilot)
                ensure_once(pilot.checkpoint(row,'reconcile'),result)
                pilot.ledger(row,'reconcile','EXCLUDE','exclude',**result)
                dispositions.append(dict(design_id=row['design_id'],next='audit',**result))
            except Park as exc:
                value=dict(design_id=row['design_id'],attempt_id='c42',action='reconcile',status='PARKED',
                    reason=str(exc),metrics=exc.metrics,rule='HUB_RULING_C42_UNKNOWN_STATES.md §2')
                ensure_once(pilot.case_dir(row)/'PARKED.json',value)
                pilot.ledger(row,'reconcile','PARK','exclude',reason=str(exc),metrics=exc.metrics)
                dispositions.append(value)
        receipt=dict(superseded_entries=len(stale),cards=reports,reconciliation=dispositions,
            new_downloads=[],model_calls=0,actual_data_estimation_calls=0,loop_restarted=False)
        ensure_once(root/'UPGRADE_RECEIPT.json',receipt)
        print(json.dumps(dict(superseded_entries=len(stale),
            cards=len(reports),reconciliation=dispositions),indent=2))


if __name__=='__main__':main()
