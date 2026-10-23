"""C70 prospective catalog; exact outcome-only contracts and source bindings."""
from pathlib import Path
from dataclasses import replace
from . import outcome_v5,outcome_v14,c46_core,c70_build
from .common import ConnectorError
CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V15.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,15,verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter']=='c70_artifact':
            for key in ('artifact','support_artifact'):
                r=spec[key];p=c46_core.REPO/r['path']
                if not p.resolve().is_relative_to(c70_build.BASE.resolve()):raise ConnectorError('V15 artifact outside version root')
                if verify_sources and c46_core.sha(p)!=r['sha256']:raise ConnectorError('V15 artifact changed')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v15_'+str(sid):raise ConnectorError('V15 exact series identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):raise ConnectorError('V15 exact population/measure contract required')
    if any(k in request.options for k in ('party','adoption','treatment','estimator')):raise ConnectorError('V15 is outcome-only')
    threshold=request.options.get('institution_coverage_min',.95)
    ipeds=sid in {'ipeds_enrollment_per_population','ipeds_completions_per_population'}
    if 'institution_coverage_min' in request.options and (not ipeds or type(threshold) not in (int,float) or threshold not in (.95,.99)):
        raise ConnectorError('V15 institution coverage option requires IPEDS and .95 or .99')
    if spec['adapter']=='c70_artifact':
        panel=c46_core.construct(request,spec)
        if ipeds:
            rows=[];metadata=[]
            for row,meta in zip(panel.rows,panel.metadata):
                row=dict(row);meta=dict(meta);share=meta.get('institution_coverage')
                if share is None or share<threshold:
                    row['outcome_value']=None;meta.update(annual_usable=0,linkage_certified=0,missing_reason='IPEDS institution coverage below registered threshold')
                rows.append(row);metadata.append(meta)
            return replace(panel,rows=tuple(rows),metadata=tuple(metadata),details=dict(panel.details,institution_coverage_min=threshold))
        return panel
    return outcome_v14._construct(replace(request,options=dict(request.options,connector='outcome_v14_'+sid)),outcome_v14.catalog(verify_sources=False))


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False)
    for name,entry in outcome_v5.entries(data).items():
        spec=data['series'][entry['series_id']]
        if spec.get('series_breaks'):entry['series_breaks']=spec['series_breaks']
        if spec.get('required_sensitivities'):entry['required_sensitivities']=spec['required_sensitivities']
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:raise ConnectorError('Conflicting V15 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog();return apply_scale(request,_construct(request,data),catalog_data=data)
