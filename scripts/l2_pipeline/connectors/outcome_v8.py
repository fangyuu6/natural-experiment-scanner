"""C46 prospective historical catalog; V2–V7 names/bytes remain untouched."""
from pathlib import Path
from . import outcome_v5
from .common import ConnectorError
from . import c46_core

CATALOG_PATH=Path(__file__).with_name('OUTCOME_CATALOG_V8.json')


def catalog(*,verify_sources=True):
    data=outcome_v5.catalog(CATALOG_PATH,8,verify_sources=verify_sources)
    for sid,spec in data['series'].items():
        if spec['adapter']=='c46_artifact':
            for key in ('artifact','support_artifact'):
                record=spec[key];p=c46_core.REPO/record['path']
                if not p.resolve().is_relative_to(c46_core.BASE.resolve()):raise ConnectorError('C46 artifact outside version directory')
                if verify_sources and c46_core.sha(p)!=record['sha256']:raise ConnectorError('C46 catalog artifact changed')
            if sid.startswith('turnout_') and not (spec.get('analysis_only') is True and spec.get('republication_prohibited') is True):
                raise ConnectorError('Turnout license ambiguity requires analysis-only non-republication')
    return data


def _construct(request,data):
    sid=request.options.get('series_id');spec=data['series'].get(sid)
    if spec is None or request.options.get('connector')!='outcome_v8_'+sid:
        raise ConnectorError('V8 exact series/connector identity required')
    if not outcome_v5.v2_supported(request.options,request.options.get('measure_id')):
        raise ConnectorError('V8 exact measure/population contract required')
    if spec['adapter']=='c46_artifact':return c46_core.construct(request,spec)
    
    if spec['adapter']=='c35_artifact':
        from .c35_core import construct
        return construct(request,spec)
    return outcome_v5._construct(request,data)


def register():
    from scripts.l2_pipeline import connectors
    data=catalog(verify_sources=False);entries=outcome_v5.entries(data)
    for name,entry in entries.items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name]!=entry:
            raise ConnectorError('Conflicting V8 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name]=entry
            connectors.ADAPTERS[name]=lambda request,data=data:_construct(request,data)
            connectors.FIRST_ORDER_MEASURES[name]={entry['series_id']}
    return tuple(sorted(entries))


def construct(request):
    register();data=catalog()
    from .rates import apply_scale
    return apply_scale(request,_construct(request,data),catalog_data=data)
