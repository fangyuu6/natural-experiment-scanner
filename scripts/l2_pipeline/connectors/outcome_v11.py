"""C56 additive catalog; all inherited contracts keep their original identities."""
from pathlib import Path
from dataclasses import replace
from . import outcome_v5, outcome_v10, c46_core, c56_core, c56_qcew
from .common import ConnectorError
CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V11.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,11,verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter']=='c56_artifact':
            for key in ('artifact','support_artifact'):
                r=spec[key];p=c46_core.REPO/r['path']
                if not p.resolve().is_relative_to(c56_core.BASE.resolve()):raise ConnectorError('V11 artifact outside version root')
                if verify_sources and c46_core.sha(p)!=r['sha256']:raise ConnectorError('V11 artifact changed')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v11_'+str(sid):raise ConnectorError('V11 exact series identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):raise ConnectorError('V11 exact population/measure contract required')
    if any(k in request.options for k in ('party','adoption','treatment','estimator')):raise ConnectorError('V11 is outcome-only')
    if spec['adapter']=='c56_artifact':return c46_core.construct(request,spec)
    if spec['adapter']=='c56_qcew':return c56_qcew.construct(request)
    return outcome_v10._construct(replace(request,options=dict(request.options,connector='outcome_v10_'+sid)),outcome_v10.catalog(verify_sources=False))


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False)
    for name,entry in outcome_v5.entries(data).items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:raise ConnectorError('Conflicting V11 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog();return apply_scale(request,_construct(request,data),catalog_data=data)
