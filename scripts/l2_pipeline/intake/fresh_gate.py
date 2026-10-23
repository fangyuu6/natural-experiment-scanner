"""Fresh audit launches require confirmed calibration and V6.2 purpose acceptance.

All receipts are data; only their validated hash bindings satisfy this gate.
"""
from pathlib import Path
import json
from scripts.l2_pipeline.storage import sha

def fresh_gate(root, designs=None, *, validate_audit_clock):
    ROOT = Path(root).resolve()
    from scripts.l2_pipeline.agents.jobs import load_job
    manifest=json.loads((ROOT/'JOBS_MANIFEST.json').read_text())
    available=set()
    for entry in manifest['jobs']:
        p=Path(entry['path'])
        if 'fresh_audit_jobs' not in p.parts:continue
        if not p.resolve().is_relative_to(ROOT/'fresh_audit_jobs') or sha(p)!=entry['sha256']:
            raise ValueError('Fresh manifest path or bytes changed')
        job=load_job(p);c=job.contract;did=job.design_id
        available.add(did)
        if sha(Path(c['card_path'])) != c['card_sha256']:
            raise ValueError('Fresh card binding differs: '+did)
        if designs and did not in designs:continue
        cal=ROOT/'calibration_jobs'/'S1'/did/('auditor.'+ROOT.name+'.md')
        cal_job=load_job(cal)
        if cal_job.contract['card_sha256'] != c['card_sha256']:
            raise ValueError('Calibration and fresh audit cards differ: '+did)
        output=Path(cal_job.output_path)
        acceptance=Path(cal_job.output_dir)/'CALIBRATION_HUB_ACCEPTANCE.json'
        a=json.loads(acceptance.read_text());o=json.loads(output.read_text())
        if (a.get('confirmed') is not True or a.get('calibration_job_sha256')!=sha(cal)
            or a.get('calibration_output_sha256')!=sha(output)):
            raise ValueError('Fresh audit requires hash-bound hub-confirmed calibration: '+did)
        validate_audit_clock(cal_job,o)
        checks=o['checks'];resolved=[r for r in checks if r.get('class_a_offset') is True]
        if (len(checks)!=8 or {r['unit'] for r in checks}!=set(cal_job.contract['sample']) or not resolved
            or any(r.get('status')=='unresolved' or r.get('direction_match') is not True
                   or r.get('panel_year')!=r.get('official_operational_calendar_year') for r in resolved)):
            raise ValueError('Calibration direction evidence is inconclusive or contradicts: '+did)
        purpose=Path(a['purpose_ruling_path']).resolve()
        if not purpose.is_relative_to(ROOT) or sha(purpose)!=a['purpose_ruling_sha256']:
            raise ValueError('Purpose acceptance binding differs: '+did)
        ruling=json.loads(purpose.read_text())
        if (ruling.get('decision')!='accept' or ruling.get('design_id')!=did
            or ruling.get('card_sha256')!=c['card_sha256'] or ruling.get('purpose_rule')!='V6.2'):
            raise ValueError('Fresh audit requires V6.2 purpose acceptance: '+did)
        if c['direction_gate']=='UNAUDITED_PENDING_DIRECTION_CALIBRATION':
            direction_path=ROOT/'direction_work'/did/'DIRECTION_HUB_ACCEPTANCE.json'
            direction=json.loads(direction_path.read_text())
            evidence=Path(direction['evidence_path']).resolve()
            if (direction.get('confirmed') is not True or direction.get('design_id')!=did
                or direction.get('card_sha256')!=c['card_sha256']
                or direction.get('basis') not in {'codebook','independent_direction_screen'}
                or not evidence.is_relative_to(ROOT) or sha(evidence)!=direction['evidence_sha256']):
                raise ValueError('Unaudited variable requires separately bound direction screen: '+did)
            if direction['basis']=='independent_direction_screen' and (
                not direction.get('sample') or set(direction['sample'])&set(cal_job.contract['sample'])
                or set(direction['sample'])&set(c['sample'])):
                raise ValueError('Direction screen must be disjoint from calibration and fresh audit: '+did)
    if not available:
        raise ValueError('No manifest-bound fresh audit jobs')
    if designs and not set(designs)<=available:
        raise ValueError('Unknown intake design selector')
