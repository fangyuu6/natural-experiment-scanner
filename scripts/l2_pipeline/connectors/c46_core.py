"""C46 offline artifacts: separate outcome bytes from metadata-only intake support."""
from __future__ import annotations
import json
from dataclasses import replace
from pathlib import Path
from .common import ConnectorError, ConnectorNotReady, PanelData, sha
from .wonder import scan_source, write_once

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / 'data/l2_pipeline/connectors_v2/c46_20261010'
from .support_schema import SUPPORT_FIELDS,C70_SUPPORT_FIELDS


def source_record(path):
    path = Path(path).resolve()
    if not path.is_relative_to(REPO / 'data'):
        raise ConnectorError('C46 sources must be repository data artifacts')
    receipt = path.with_name(path.name + '.receipt.json')
    record = json.loads(receipt.read_text()) if receipt.is_file() else {}
    digest = sha(path)
    if receipt.is_file() and (record.get('sha256') != digest or record.get('size_bytes') != path.stat().st_size):
        raise ConnectorError('C46 source receipt byte binding changed: ' + str(path))
    return dict(path=str(path.relative_to(REPO)), sha256=digest,
        size_bytes=path.stat().st_size, url=record.get('url',''),
        license=record.get('license','Inherited repository artifact; see original source receipts'),
        **({'receipt_path':str(receipt.relative_to(REPO)), 'receipt_sha256':sha(receipt)} if receipt.is_file() else {}))


def coverage(meta):
    years = sorted({m['year'] for m in meta})
    return dict(years=years, states=sorted({m['unit'] for m in meta}), cells=len(meta),
        available_cells=sum(bool(m['available']) for m in meta),
        usable_cells=sum(bool(m['annual_usable']) and bool(m['linkage_certified']) for m in meta),
        suppressed_cells=sum(bool(m.get('suppressed')) for m in meta),
        provisional_cells=sum(bool(m.get('provisional')) for m in meta),
        by_year={str(y):dict(cells=sum(m['year']==y for m in meta),
            available=sum(m['year']==y and bool(m['available']) for m in meta),
            usable=sum(m['year']==y and bool(m['annual_usable']) and bool(m['linkage_certified']) for m in meta)) for y in years})


def save_panel(sid, filters, rows, meta, inventory, limitations, details, population,
               source_url, frequency='annual', **spec_extra):
    """Support sidecar contains flags only, never outcome/count/denominator values."""
    rows, meta = list(rows), list(meta)
    keys = [(r['unit'],r['year']) for r in rows]
    if len(keys)!=len(set(keys)) or keys != [(m['unit'],m['year']) for m in meta]:
        raise ConnectorError('C46 duplicate or mismatched row/support keys')
    safe = [{k:v for k,v in m.items() if k in SUPPORT_FIELDS} for m in meta]
    for m in safe:
        if not {'unit','year','available','annual_usable','linkage_certified'} <= m.keys():
            raise ConnectorError('C46 incomplete support contract')
    inv=[]
    for item in inventory:
        item=dict(item)
        p=Path(item['path']);p=p if p.is_absolute() else REPO/p
        item['path']=str(p.relative_to(REPO))
        if sha(p)!=item['sha256']:raise ConnectorError('C46 inventory source changed')
        inv.append(item)
    years=sorted({r['year'] for r in rows})
    # Retain the pre-review bytes; corrected unsealed V8 series build into a
    # versioned directory so repeating the offline build is write-once safe.
    destination=BASE/'review1' if sid in {'fars_motorcyclist_fatality_rate','fbi_hate_crime_incident_rate'} else BASE
    artifact=destination/'parsed'/(sid+'.json');support=destination/'support'/(sid+'.json')
    payload=dict(series_id=sid,population_filters=filters,years=years,rows=rows,metadata=safe,
        inventory=inv,limitations=list(limitations),details=dict(details,outcome_association_calls=0))
    write_once(artifact,payload)
    from .support_repair import project
    write_once(support,project(dict(series_id=sid,population_filters=filters,years=years,metadata=safe,
        artifact_path=str(artifact.relative_to(REPO)),artifact_sha256=sha(artifact),
        coverage=coverage(safe),outcome_values_in_sidecar=False,outcome_association_calls=0)))
    return dict(series_id=sid,adapter='c46_artifact',adapter_version='c46_offline_v1',years=years,
        geography='US states and DC',population=population,population_filters=filters,
        units=rows[0]['outcome_unit'],frequency=[frequency],source_url=source_url,
        release_lag='See pinned source vintage and receipts',public_available=True,
        status='receipt_verified_local_artifact',license='; '.join(sorted({i['license'] for i in inv})),
        registered_connector='outcome_v8_'+sid,
        artifact=dict(path=str(artifact.relative_to(REPO)),sha256=sha(artifact)),
        support_artifact=dict(path=str(support.relative_to(REPO)),sha256=sha(support)),
        paths=[str(artifact.relative_to(REPO))],source_sha256={str(artifact.relative_to(REPO)):sha(artifact)},
        observed_coverage=coverage(safe),**spec_extra)


def construct(request, spec):
    if any(k in request.options for k in ('party','adoption','treatment','estimator')):
        raise ConnectorError('C46 accepts outcome-only inputs')
    if dict(request.filters)!=spec['population_filters'] or request.frequency not in spec['frequency']:
        raise ConnectorError('C46 exact population and frequency required')
    if not set(request.years)<=set(spec['years']):raise ConnectorNotReady('C46 year outside pinned source')
    artifact=spec['artifact'];path=REPO/artifact['path']
    # Catalog paths are repo-relative, independent of the design card directory.
    # Accept only this exact catalog source before resolving against REPO.
    # Compact secondary declarations bind to this adapter's pinned catalog,
    # never to CURRENT or to the primary's sources. Partial bindings stay fatal.
    if 'paths' not in request.options and 'source_sha256' not in request.options:
        request=replace(request,options=dict(request.options,paths=[artifact['path']],
            source_sha256={artifact['path']:artifact['sha256']}))
    declared=request.options.get('paths',[])
    declared=[declared] if isinstance(declared,str) else list(declared)
    if declared not in ([artifact['path']],[str(path)]):
        raise ConnectorError('C46 source path differs from catalog')
    if replace(request,base_dir=REPO).paths()!=(path,) or sha(path)!=artifact['sha256']:
        raise ConnectorError('C46 exact catalog artifact/hash binding required')
    if request.options.get('source_sha256',{}).get(declared[0])!=artifact['sha256']:
        raise ConnectorError('C46 card must declare source hash')
    data=json.loads(path.read_text())
    if data['series_id']!=spec['series_id'] or data['population_filters']!=dict(request.filters):
        raise ConnectorError('C46 artifact identity changed')
    for item in data['inventory']:
        if sha(REPO/item['path'])!=item['sha256']:raise ConnectorError('C46 upstream changed: '+item['path'])
        if 'receipt_path' in item and sha(REPO/item['receipt_path'])!=item['receipt_sha256']:
            raise ConnectorError('C46 upstream receipt changed')
    def subset(records):
        selected=tuple(r for r in records if r['unit'] in request.units and r['year'] in request.years)
        if len(selected)!=len(request.units)*len(request.years):raise ConnectorError('C46 incomplete requested support grid')
        return selected
    return PanelData(subset(data['rows']),subset(data['metadata']),tuple(data['inventory']),tuple(data['limitations']),data['details'])
