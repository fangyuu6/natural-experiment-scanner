"""C21 prospective outcome catalog. Old V4 names and bytes remain intact."""
from __future__ import annotations
import json
from pathlib import Path
from dataclasses import replace

from .common import ConnectorError, ConnectorNotReady
from .catalog import _entry, v2_supported
from .wonder_registry import REQUIRED, _verified_wonder, _construct as construct_v4

CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V5.json')


def catalog(catalog_path=CATALOG_PATH, version=5, *, verify_sources=True):
    data = json.loads(catalog_path.read_text())
    if data.get('schema_version') != version or data.get('catalog_id') != 'first_order_outcomes_v'+str(version):
        raise ConnectorError('Expected C21 catalog version '+str(version))
    for sid, spec in data['series'].items():
        if not REQUIRED <= spec.keys() or spec['series_id'] != sid:
            raise ConnectorError('Incomplete V5 series: ' + sid)
        years = spec['years']
        if sorted(set(years)) != years or any(type(y) is not int for y in years):
            raise ConnectorError('Invalid V5 coverage: ' + sid)
        if spec['public_available'] and not years:
            raise ConnectorError('Available V5 series has no coverage')
        if spec['adapter'] == 'wonder' and spec['public_available'] and verify_sources:
            _verified_wonder(spec)
    for target in data['targets'].values():
        if not set(target.get('series', [])) <= data['series'].keys():
            raise ConnectorError('Unknown V5 target series')
    return data


def entries(data):
    result = {}
    for sid, spec in data['series'].items():
        years = spec['years']; window = years or spec.get('requested_year_range', [])
        if not window:
            raise ConnectorError('V5 series needs observed or requested window')
        bindings = {sid: {sid}}
        bindings.update({t: {sid} for t, definition in data['targets'].items()
                         if sid in definition['series'] and definition.get('selection_authorized', True)})
        e = _entry(min(window), max(window), bindings, [], [spec['population']],
                   spec['source_url'], access='public_no_key' if spec['public_available'] else 'pending_public_exports',
                   excluded_years=set(range(min(window), max(window)+1))-set(years))
        e.update(series_id=sid, series_years=tuple(years),
                 fixed_population_filters=spec.get('population_filters', {}),
                 required_filters=tuple(spec.get('required_filters', [])),
                 allowed_causes=tuple(spec.get('causes', [])),
                 allowed_age_groups=tuple(spec.get('age_groups', [])),
                 frequencies=tuple(spec['frequency']), outcome_unit=spec['units'],
                 catalog_version=data['schema_version'], source_validation_required=True,
                 legal_certification_granted=False, status=spec['status'],
                 public_available=spec['public_available'])
        from .rates import scale_metadata
        e.update(scale_metadata(sid, spec['units']))
        from .series_windows import definition_metadata
        e.update(definition_metadata(sid))
        result['outcome_v'+str(data['schema_version'])+'_'+sid] = e
    return result


def _construct(request, data):
    sid = request.options.get('series_id'); spec = data['series'].get(sid)
    if spec is None or request.options.get('connector') != 'outcome_v'+str(data['schema_version'])+'_'+str(sid):
        raise ConnectorError('V5 requires exact connector/series binding')
    if not spec['public_available']:
        raise ConnectorNotReady('Pending verified public exports: '+sid)
    if any(k in request.options for k in ('treatment', 'adoption', 'party', 'estimator')):
        raise ConnectorError('Outcome adapters accept outcome-only inputs')
    if not v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V5 population/measure binding differs')
    if dict(request.filters) != request.options.get('population_filters', {}) or request.frequency != request.options.get('frequency', 'annual'):
        raise ConnectorError('V5 request population/frequency differs')
    if not set(request.years) <= set(spec['years']):
        raise ConnectorNotReady('V5 request exceeds observed source coverage')
    if spec['adapter'] == 'wonder':
        _verified_wonder(spec)
        from .wonder import build
        return build(request, catalog_data=data)
    if spec['adapter'] == 'c21_fars':
        from .c21_fars import construct
        return construct(request, catalog_data=data)
    # Inherited contracts delegate to the original adapter, retaining source IDs.
    options = dict(request.options, connector='outcome_v4_'+sid)
    return construct_v4(replace(request, options=options), data)


def register(data=None):
    from scripts.l2_pipeline import connectors
    data = catalog(verify_sources=False) if data is None else data; registry = entries(data)
    for name, e in registry.items():
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name] != e:
            raise ConnectorError('Conflicting V5 registration: '+name)
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = e
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {e['series_id']}
    return tuple(sorted(registry))


def construct(request):
    register()
    from .rates import apply_scale
    data = catalog()
    return apply_scale(request, _construct(request, data), catalog_data=data)
