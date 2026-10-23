"""C49 additive outcome catalog; prior V9 contracts and bytes remain intact."""
from pathlib import Path
from . import outcome_v5,c46_core,c35_core,c48_fars,c49_ucr
from .common import ConnectorError

CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V10_REVIEW1.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,10,verify_sources=verify_sources)
    roots={'c46_artifact':c46_core.BASE,'c35_artifact':c35_core.BASE,'c48_artifact':c48_fars.BASE,'c49_artifact':c49_ucr.BASE.parent}
    for sid,spec in data['series'].items():
        if spec['adapter'] in roots:
            for key in ('artifact','support_artifact'):
                r=spec[key];p=c46_core.REPO/r['path']
                if not p.resolve().is_relative_to(roots[spec['adapter']].resolve()):raise ConnectorError('V10 artifact outside registered version root')
                if verify_sources and c46_core.sha(p)!=r['sha256']:raise ConnectorError('V10 catalog artifact changed')
        if sid.startswith('turnout_') and not (spec.get('analysis_only') is True and spec.get('republication_prohibited') is True):
            raise ConnectorError('Turnout license ambiguity requires analysis-only non-republication')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v10_'+sid:raise ConnectorError('V10 exact series/connector identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):raise ConnectorError('V10 exact measure/population contract required')
    if spec['adapter'] in {'c46_artifact','c48_artifact','c49_artifact'}:return c46_core.construct(request,spec)
    if spec['adapter']=='c35_artifact':return c35_core.construct(request,spec)
    return outcome_v5._construct(request,data)


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False);entries=outcome_v5.entries(data)
    for name,entry in entries.items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:raise ConnectorError('Conflicting V10 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(entries))


def construct(request):
    register()
    from .rates import apply_scale
    data=catalog();return apply_scale(request,_construct(request,data),catalog_data=data)
