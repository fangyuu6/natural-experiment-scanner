"""C63 prospective catalog with receipt-bound YRBS state waves."""
from pathlib import Path
from dataclasses import replace
from . import outcome_v5,outcome_v13,c46_core,c63_yrbs
from .common import ConnectorError
CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V14.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,14,verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter']=='c63_yrbs_artifact':
            for key in ('artifact','support_artifact'):
                r=spec[key];p=c46_core.REPO/r['path']
                if not p.resolve().is_relative_to(c63_yrbs.BASE.resolve()):raise ConnectorError('V14 artifact outside version root')
                if verify_sources and c46_core.sha(p)!=r['sha256']:raise ConnectorError('V14 artifact changed')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v14_'+str(sid):raise ConnectorError('V14 exact series identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):raise ConnectorError('V14 exact population/measure contract required')
    if any(k in request.options for k in ('party','adoption','treatment','estimator')):raise ConnectorError('V14 is outcome-only')
    if spec['adapter']=='c63_yrbs_artifact':return c46_core.construct(request,spec)
    return outcome_v13._construct(replace(request,options=dict(request.options,connector='outcome_v13_'+sid)),outcome_v13.catalog(verify_sources=False))


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False)
    for name,entry in outcome_v5.entries(data).items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:raise ConnectorError('Conflicting V14 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog();return apply_scale(request,_construct(request,data),catalog_data=data)
