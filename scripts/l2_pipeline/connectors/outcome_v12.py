"""C59 additive catalog; all inherited contracts keep their original identities."""
from pathlib import Path
from dataclasses import replace
from . import outcome_v5, outcome_v11, c46_core, c59_core
from .common import ConnectorError
CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V12.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,12,verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter']=='c59_artifact':
            for key in ('artifact','support_artifact'):
                r=spec[key];p=c46_core.REPO/r['path']
                if not p.resolve().is_relative_to(c59_core.BASE.resolve()):raise ConnectorError('V12 artifact outside version root')
                if verify_sources and c46_core.sha(p)!=r['sha256']:raise ConnectorError('V12 artifact changed')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v12_'+str(sid):raise ConnectorError('V12 exact series identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):raise ConnectorError('V12 exact population/measure contract required')
    if any(k in request.options for k in ('party','adoption','treatment','estimator')):raise ConnectorError('V12 is outcome-only')
    if spec['adapter']=='c59_artifact':return c46_core.construct(request,spec)
    return outcome_v11._construct(replace(request,options=dict(request.options,connector='outcome_v11_'+sid)),outcome_v11.catalog(verify_sources=False))


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False)
    for name,entry in outcome_v5.entries(data).items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:raise ConnectorError('Conflicting V12 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog();return apply_scale(request,_construct(request,data),catalog_data=data)
