"""C56 write-once outcome-only artifacts, with separate value-free support."""
from pathlib import Path
from .c46_core import REPO, SUPPORT_FIELDS, coverage, source_record
from .common import row, metadata
from .wonder import write_once
from ..storage import sha

BASE = REPO/'data/l2_pipeline/connectors_v2/c56_20261011'
UNITS = tuple(sorted(__import__('scripts.l2_pipeline.connectors.common', fromlist=['STATES']).STATES.values()))


def save(sid, years, values, sources, population, url, units, *, filters=None,
         flags=None, details=None, limitations=()):
    filters=filters or {}; flags=flags or {}; rows=[]; support=[]
    for u in UNITS:
        for y in years:
            v=values.get((u,y)); f=flags.get((u,y),{})
            r=row(u,y,v,units); rows.append(r)
            m=metadata(r,v is not None,missing_reason=f.get('missing_reason','' if v is not None else 'published_cell_absent'),
                       **{k:v for k,v in f.items() if k!='missing_reason'})
            support.append({k:v for k,v in m.items() if k in SUPPORT_FIELDS})
    inventory=[source_record(p) for p in dict.fromkeys(sources)]
    artifact=BASE/'parsed'/(sid+'.json'); sidecar=BASE/'support'/(sid+'.json')
    write_once(artifact,dict(series_id=sid,population_filters=filters,years=list(years),rows=rows,
        metadata=support,inventory=inventory,limitations=list(limitations),
        details=dict(details or {},outcome_association_calls=0,interpolation=False)))
    observed=coverage(support)
    write_once(sidecar,dict(series_id=sid,population_filters=filters,metadata=support,coverage=observed,
        artifact_path=str(artifact.relative_to(REPO)),artifact_sha256=sha(artifact),
        outcome_values_in_sidecar=False,outcome_association_calls=0))
    return dict(series_id=sid,adapter='c56_artifact',adapter_version='c56_offline_v1',years=list(years),
        geography='US states and DC',population=population,population_filters=filters,units=units,
        frequency=['annual'],source_url=url,release_lag='Pinned final source vintage; see receipts',
        public_available=True,status='receipt_verified_local_artifact',
        license='; '.join(sorted({s['license'] for s in inventory})),registered_connector='outcome_v11_'+sid,
        artifact=dict(path=str(artifact.relative_to(REPO)),sha256=sha(artifact)),
        support_artifact=dict(path=str(sidecar.relative_to(REPO)),sha256=sha(sidecar)),
        paths=[str(artifact.relative_to(REPO))],source_sha256={str(artifact.relative_to(REPO)):sha(artifact)},
        observed_coverage=observed,legal_certification_granted=False)
