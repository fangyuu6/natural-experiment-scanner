"""Stopped-scheduler §7/8 queue migration. Prepare jobs; never dispatch."""
from pathlib import Path
from .autopilot import Autopilot, RULES, read, atomic, scheduler_lock, utc
from .storage import ensure_once, sha

TARGETS = {'bulk1010_lab_credit_check_ban', 'bulk10_health_ichia_immigrant_child_coverage'}


def upgrade(pilot=None):
    pilot=pilot or Autopilot()
    root=pilot.registry.parent/'autopilot/c45'
    with scheduler_lock(pilot.registry):
        if not (pilot.registry/'AUTOPILOT_STOP').exists(): raise ValueError('C45 requires clean stop')
        stop=read(root/'STOP_RECEIPT.json')
        if not stop or not stop['lock_released'] or stop['clean_stop']['decision']!='CLEAN_STOP':
            raise ValueError('C45 requires observed CLEAN_STOP and released lock')
        prior=read(root/'UPGRADE_RECEIPT.json')
        if prior: return prior
        manifest=read(pilot.registry/'ACTIVE_DESIGNS.json');queue=read(pilot.registry/'HUB_QUEUE.json')
        ensure_once(root/'ACTIVE_DESIGNS_BEFORE.json',manifest)
        ensure_once(root/'HUB_QUEUE_BEFORE.json',queue)
        recovered=[]
        for row in manifest['designs']:
            if row['design_id'] not in TARGETS: continue
            record=read(pilot.case_dir(row)/'STOP.json')
            if record['action']!='history' or pilot.done(row,'history'):
                raise ValueError('C45 recovery must precede successful history')
            inventory=read(pilot.case_dir(row)/'jobs/history.json')
            pilot.collect_submissions(row,'history',inventory['jobs'])
            replacements=[]
            for path in inventory['jobs']:
                effective=pilot.effective_job(row,path)
                if effective!=path:replacements.append(read(pilot.replacement_slot(row,path)/'REPLACEMENT.json'))
            if len(replacements)!=1:raise ValueError('C45 expects exactly one rejected role per queued design')
            value=dict(stop_sha256=sha(pilot.case_dir(row)/'STOP.json'),replacement=replacements[0],
                rule=RULES['replacement'])
            ensure_once(pilot.case_dir(row)/'STOP_SUPERSEDED_C45.json',value)
            pilot.ledger(row,'history','SUPERSEDED_C45','replacement',stop_sha256=value['stop_sha256'],replacement=value['replacement'])
            recovered.append(dict(design_id=row['design_id'],replacement=replacements[0]))
        if {v['design_id'] for v in recovered}!=TARGETS:raise ValueError('Missing C45 queue targets')
        superseded=[]
        for entry in queue['entries']:
            orphan=entry['design_id'].startswith('orphan_worker_')
            target=entry['design_id'] in TARGETS
            if not (orphan or target): continue
            if entry.get('status')=='SUPERSEDED_C45':continue
            original=dict(entry)
            entry.update(status='SUPERSEDED_C45',superseded_at=utc(),prior_status=original['status'])
            if orphan:
                entry.update(reconciled_status='DEAD_NO_RESULT',automatic_redispatch=False)
            superseded.append(original)
            pilot.ledger(entry,entry['action'],'SUPERSEDED_C45','orphan' if orphan else 'replacement',
                prior_entry=original,automatic_redispatch=False if orphan else None)
        atomic(pilot.registry/'HUB_QUEUE.json',queue)
        value=dict(schema='l2_c45_upgrade_v1',recovered=recovered,superseded=superseded,
            orphan_count=sum(v['design_id'].startswith('orphan_worker_') for v in superseded),
            dispatches=0,actual_data_estimation_calls=0,new_downloads=[])
        ensure_once(root/'UPGRADE_RECEIPT.json',value)
        return value


if __name__=='__main__':
    import json
    print(json.dumps(upgrade(),indent=2))
