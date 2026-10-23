"""C84 exact-defect recovery through code_defect_fixed and §7; no fits."""
from pathlib import Path
from .storage import ensure_once


def resume(p,row,stop,tags,fix):
    from .autopilot import read
    from .c70_l3 import protected
    from .agents.replacement import ContractRejection
    from .c62_apply import supersede
    case=p.case_dir(row);root=p.backend.root(row)
    if protected(root) or (case/'STOP_SUPERSEDED_C84.json').exists():return False
    expected={'purpose-direction':'ValueError: Purpose direction requires public source locators',
        'purpose':'ValueError: Purpose citation must identify supported fields'}
    if stop.get('reason')!=expected[fix['action']]:return False
    inventory=read(case/('jobs/'+fix['action']+'.json'),dict(jobs=[]))
    if fix['action']=='purpose-direction':
        from .agents.purpose_direction import refs
        if refs(p.backend.ctx(row)):return False
        jobs=[str(x) for x in (case/'purpose_direction_jobs').glob('S0/*/*.md')]
    else:
        jobs=inventory['jobs']
        if len(jobs)!=1:return False
        path=p.effective_job(row,jobs[0])
        if path!=jobs[0] or (p.replacement_slot(row,path)/'REPLACEMENT.json').exists():return False
        try:p.backend.collect(path)
        except ContractRejection as exc:
            if exc.kind!='SCHEMA_FORMAT' or str(exc)!=expected['purpose'].removeprefix('ValueError: '):return False
        else:return False
    evidence=ensure_once(case/'CODE_FIX_C84.json',dict(schema='l2_code_fix_supersession_v1',
        code_task='C84',fix=fix['fix'],impact_tags=tags,stop=stop,reused_jobs=jobs,
        dispatches=0,execution_authorized=False,outcome_values_read=0))
    supersede(p,row,evidence,tags,suffix='C84')
    return True


def apply():
    from .autopilot import Autopilot,read,scheduler_lock
    from .maintenance import resume_code_fix
    from .ops.watchdog import clean_stop
    from .agents.sonnet_queue import enqueue
    p=Autopilot();results=[]
    with scheduler_lock(p.registry):
        application=p.registry.parent/'autopilot/c84/APPLICATION.json'
        if application.exists():return read(application)['records']
        lock=read(p.registry/'CODE_TASK.lock',{})
        if lock.get('task')!='C84' or not (p.registry/'AUTOPILOT_STOP').exists():raise ValueError('C84 stopped code window required')
        clean_stop(p.repo)
        for row in read(p.registry/'ACTIVE_DESIGNS.json')['designs']:
            stop=p.stop_record(row)
            recovered=read(p.case_dir(row)/'CODE_FIX_C84.json')
            if not stop and recovered and (p.case_dir(row)/'STOP_SUPERSEDED_C84.json').exists():
                stop=recovered['stop'];resumed=True
            else:resumed=False
            if not stop or stop.get('reason') not in {'ValueError: Purpose direction requires public source locators','ValueError: Purpose citation must identify supported fields'}:continue
            if not resumed and not resume_code_fix(p,row):continue
            packets=[]
            if stop['action']=='purpose':
                jobs=read(p.case_dir(row)/'jobs/purpose.json')['jobs']
                p.collect_submissions(row,'purpose',jobs)
                for pending_row,path in p.pending:
                    if pending_row==row:packets.append(enqueue(path,row))
                p.pending.clear()
            else:
                from .agents.purpose_direction import action
                ensure_once(p.checkpoint(row,'purpose-direction'),action(p,row))
            results.append(dict(design_id=row['design_id'],action=stop['action'],packets=packets,next_action=p.next_action(row)))
        ensure_once(p.registry.parent/'autopilot/c84/APPLICATION.json',dict(records=results,actual_data_estimation_calls=0))
    return results

if __name__=='__main__':
    import json
    print(json.dumps(apply(),indent=2))
