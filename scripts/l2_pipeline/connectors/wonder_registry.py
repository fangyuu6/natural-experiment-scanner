"""Scoped V4 registration; no network, estimates, or shared-source edits.

Call register() in a prospective pipeline process before using outcome_v4_*
connectors. This leaves every pre-existing connector entry and builder intact.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

from scripts.l2_pipeline import connectors
from scripts.l2_pipeline.connectors.catalog import _entry, v2_supported
from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady
from scripts.l2_pipeline.connectors.outcome_v2 import construct as construct_v2

ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V4.json')
REQUIRED = {'series_id', 'adapter', 'years', 'geography', 'population', 'units',
            'frequency', 'source_url', 'release_lag', 'public_available'}


def _verified_artifact(record):
    """Only explicitly hashed task-directory artifacts may activate WONDER."""
    if not isinstance(record, dict) or not isinstance(record.get('path'), str):
        raise ConnectorError('WONDER activation requires artifact path/hash records')
    path = (ROOT / record['path']).resolve()
    allowed = (ROOT / 'data/l2_pipeline/connectors_v2/wonder').resolve()
    if not path.is_relative_to(allowed) or path.name.startswith('.env') or path.name in {'auth.json', 'credentials'}:
        raise ConnectorError('WONDER receipt outside authorized artifact directory')
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get('sha256'):
        raise ConnectorError('WONDER activation artifact missing or hash changed')
    return path


def _verified_wonder(spec):
    if spec.get('browser_receipts_verified') is not True or not spec['years']:
        raise ConnectorError('WONDER availability requires explicitly verified browser receipts')
    from .wonder import verify_receipt
    receipts = spec.get('query_receipts')
    if not isinstance(receipts,list) or not receipts or not spec.get('exports'):
        raise ConnectorError('WONDER activation requires verified query receipts and exact export bindings')
    if len(receipts)!=len(spec['exports']):
        raise ConnectorError('Receipt/export cardinality differs')
    for record, export in zip(receipts,spec['exports']):
        _verified_artifact(record)
        raw = _verified_artifact(dict(path=export['raw_path'],sha256=export['sha256']))
        receipt,_=verify_receipt(raw,export)
        expected_sid='wonder_births_by_maternal_age' if spec['series_id']=='wonder_fertility_rate_by_maternal_age' else spec['series_id']
        if receipt['series_id']!=expected_sid or receipt['population_filters']!=export['population_filters']:
            raise ConnectorError('WONDER query population differs from registered receipt binding')


def catalog(*, verify_sources=True):
    data = json.loads(CATALOG_PATH.read_text())
    if data.get('schema_version') != 4 or data.get('catalog_id') != 'first_order_outcomes_v4':
        raise ConnectorError('Expected first_order_outcomes_v4 schema_version=3')
    for sid, spec in data['series'].items():
        if not REQUIRED <= spec.keys() or spec['series_id'] != sid:
            raise ConnectorError('Incomplete V4 series: ' + sid)
        years = spec['years']
        if any(type(y) is not int for y in years) or sorted(set(years)) != years:
            raise ConnectorError('V4 years must be increasing unique integers: ' + sid)
        if spec['public_available'] and not years:
            raise ConnectorError('Available V4 series has no observed coverage: ' + sid)
        if spec['adapter'] == 'wonder':
            if spec['status'] == 'blocked_access_denied' and (spec['public_available'] or years):
                raise ConnectorError('Blocked WONDER plan cannot confer source availability')
            if spec['public_available'] and verify_sources:
                _verified_wonder(spec)
    for target in data['targets'].values():
        if not set(target.get('series', [])) <= data['series'].keys():
            raise ConnectorError('V4 target references unknown series')
    return data


def _entries(data):
    entries = {}
    for sid, spec in data['series'].items():
        observed = spec['years']
        window = observed or spec.get('requested_year_range', [])
        if not window:
            raise ConnectorError('No observed or requested V4 window: ' + sid)
        bindings = {target: {sid} for target, definition in data['targets'].items()
                    if sid in definition['series'] and definition.get('selection_authorized', True)}
        available = spec['public_available']
        entry = _entry(min(window), max(window), bindings, [], [spec['population']],
                       spec['source_url'], access='public_no_key' if available else 'blocked_access_denied',
                       excluded_years=set(range(min(window), max(window) + 1)) - set(observed))
        entry.update(series_id=sid, series_years=tuple(observed),
                     age_group_years=spec.get('age_group_years', {}),
                     fixed_population_filters=spec.get('population_filters', {}),
                     required_filters=tuple(spec.get('required_filters', [])),
                     allowed_causes=tuple(spec.get('causes', [])),
                     allowed_age_groups=tuple(spec.get('age_groups', [])),
                     frequencies=tuple(spec['frequency']), outcome_unit=spec['units'],
                     source_validation_required=True, legal_certification_granted=False,
                     catalog_version=4, status=spec['status'],
                     public_available=available, public_download_years=tuple(spec.get('public_download_years', observed)))
        from .rates import scale_metadata
        entry.update(scale_metadata(sid, spec['units']))
        entries['outcome_v4_' + sid] = entry
    return entries


def _construct(request, data):
    sid = request.options.get('series_id')
    spec = data['series'].get(sid)
    if spec is None:
        raise ConnectorNotReady('Unknown V4 outcome series: ' + str(sid))
    name = 'outcome_v4_' + sid
    if request.options.get('connector') != name:
        raise ConnectorError('V4 connector must bind its exact series_id')
    if not spec['public_available']:
        raise ConnectorNotReady('WONDER access blocked; browser collection required: ' + sid)
    if any(k in request.options for k in ('treatment', 'adoption', 'party', 'estimator')):
        raise ConnectorError('Outcome adapters accept outcome-only inputs')
    if not v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V4 requires exact series, measure and published population binding')
    if dict(request.filters) != request.options.get('population_filters', {}) or request.frequency != request.options.get('frequency', 'annual'):
        raise ConnectorError('V4 request population/frequency differs from bound outcome')
    if not set(request.years) <= set(spec['years']):
        raise ConnectorError('V4 request years exceed observed source coverage')
    age=request.filters.get('age_group')
    if age and spec.get('age_group_years') and not set(request.years)<=set(spec['age_group_years'].get(age, [])):
        raise ConnectorError('V4 request years exceed exact published age-bin coverage')
    if spec['adapter_version'] == 'v2_inherited':
        return construct_v2(request)
    if spec['adapter'] == 'wonder':
        _verified_wonder(spec)
        from . import wonder
        return wonder.build(request)
    from . import historical
    if spec['adapter'] != 'historical_alias':
        return historical.construct(request)
    source = spec['alias_of']
    options = dict(request.options, series_id=source, measure_id=source,
                   connector='outcome_v4_' + source)
    panel = historical.construct(replace(request, options=options))
    return replace(panel, rows=tuple(dict(row, series_id=sid) for row in panel.rows),
                   metadata=tuple(dict(row, series_id=sid) for row in panel.metadata),
                   details=dict(panel.details, series_id=sid, alias_of=source))


def register():
    """Register only V4 names after validation; repeat calls are idempotent."""
    # Registration exposes declarations only. Source activation is checked at
    # construction; frozen-panel workers do not need the construction sources.
    data = catalog(verify_sources=False)
    entries = _entries(data)
    for name, entry in entries.items():
        existing = connectors.CONNECTOR_CATALOG.get(name)
        if existing is not None and existing != entry:
            raise ConnectorError('Conflicting V4 registration: ' + name)
        if existing is None and name in connectors.ADAPTERS:
            raise ConnectorError('Existing builder lacks matching V4 contract: ' + name)
    for name, entry in entries.items():
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = entry
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {entry['series_id']}
    return tuple(sorted(entries))


def construct(request):
    register()
    from .rates import apply_scale
    return apply_scale(request, _construct(request, catalog()))
