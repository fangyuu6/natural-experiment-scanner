"""Metadata-only sidecar projection. Never opens the linked outcome artifact."""
import copy,json
from pathlib import Path
from ..storage import ensure_once,sha
from .support_schema import SUPPORT_FIELDS

def project(value):
    if value.get('outcome_values_in_sidecar') is not False:raise ValueError('Sidecar value boundary not declared')
    allowed={"series_id","population_filters","years","metadata","artifact_path","artifact_sha256","coverage","outcome_values_in_sidecar","outcome_association_calls"}
    if set(value)-allowed:raise ValueError("Non-metadata top-level sidecar fields")
    result=copy.deepcopy(value)
    result['metadata']=[{k:v for k,v in r.items() if k in SUPPORT_FIELDS} for r in value['metadata']]
    for r in result['metadata']:
        if not {'unit','year','available','annual_usable','linkage_certified'}<=r.keys():raise ValueError('Required support metadata missing')
    return result

def rebuild(source,destination):
    source=Path(source);destination=Path(destination)
    if source.resolve()==destination.resolve():raise ValueError('Rebuild must preserve original sidecar')
    raw=source.read_bytes();old=json.loads(raw);new=project(old)
    data=raw if new==old else (json.dumps(new,indent=2,sort_keys=True)+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if destination.read_bytes()!=data:raise ValueError('Rebuild destination changed')
    else:
        with destination.open('xb') as stream:stream.write(data)
    return dict(source=dict(path=str(source),sha256=sha(source)),rebuilt=dict(path=str(destination),sha256=sha(destination)),
        byte_identical=raw==data,removed_fields=sorted({k for r in old['metadata'] for k in r if k not in SUPPORT_FIELDS}),
        outcome_artifacts_opened=0,outcome_values_read=0)
