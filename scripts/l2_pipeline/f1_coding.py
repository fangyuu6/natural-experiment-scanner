"""F1 purpose_direction coding gate, time stamp and export binding (hub ruling HUB_RULING_20261017_F1C).

The gate is narrower than ``c70_l3.protected``: a direction may be coded until an estimate artifact exists. The pipeline writes its own
immutable time stamp when it accepts a coding; the export carries coding time, file hash and epoch. The registration hash does not cover these.
"""
import copy,json
from datetime import datetime,timezone
from pathlib import Path
from .storage import sha,write_once

CODING='PURPOSE_DIRECTION_C83.json'
STAMP='PURPOSE_DIRECTION_STAMP_C83.json'
SCHEMA='l2_f1_stamp_c83_v1'
ESTIMATE_FILES=('EXECUTION_ONCE.json','TERMINAL.json','FAILURE.json','RESULT.json','L3_EXPORT_DATA.json')


def f1_protected(root):
    """True once an estimate artifact exists for the design (freeze and approval files do not count)."""
    root=Path(root)
    return any((root/n).exists() for n in ESTIMATE_FILES) or any(root.glob('SEALED_*.json'))


def stamp(root):
    """Write the pipeline's own accepted-at stamp for an accepted coding; None when the gate is closed or nothing is coded."""
    root=Path(root);path=root/STAMP;coding=root/CODING
    if path.exists():return json.loads(path.read_text())
    if not coding.exists() or f1_protected(root):return None
    if json.loads(coding.read_text()).get('purpose_direction') is None:return None
    value=dict(schema=SCHEMA,accepted_at=datetime.now(timezone.utc).isoformat(),coding_file_sha256=sha(coding),f1_gate='no_estimate_artifact')
    write_once(path,value);return value


def bind(registration,root):
    """Add coding time, file hash and epoch to first_order of the export; fill the direction when the manifest did not bind it."""
    if registration is None:return registration
    root=Path(root);coding=root/CODING
    if not coding.exists():return registration
    value=copy.deepcopy(registration);first=value.setdefault('first_order',{})
    file_sha=sha(coding);bound=first.get('purpose_direction_evidence') is not None
    if not bound:
        data=json.loads(coding.read_text())
        from .agents.purpose import validate_purpose_direction
        validate_purpose_direction(data)
        first['purpose_direction']=data['purpose_direction'];first['purpose_direction_evidence']=copy.deepcopy(data['purpose_direction_evidence'])
        value['purpose_direction_status']=('FIELD_MISSING' if data['purpose_direction'] is None else
            'DIRECTION_UNDETERMINED' if data['purpose_direction']==0 else 'EVAL')
    first['coding_file_sha256']=file_sha
    path=root/STAMP
    if path.exists():
        stamped=json.loads(path.read_text())
        if stamped.get('coding_file_sha256')!=file_sha:raise ValueError('F1 coding file changed after its stamp')
        first['purpose_direction_coded_at']=stamped['accepted_at'];first['coding_epoch']='prospective'
    elif bound:
        first['coding_epoch']='prospective'   # coded before the freeze, hence before any estimate
    return value
