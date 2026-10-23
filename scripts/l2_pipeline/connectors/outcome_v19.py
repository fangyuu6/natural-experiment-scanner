"""Catalog V19 (T273E/T277, 2026-10-23): V18 plus six BLS QCEW private-ownership industry slices (NAICS 6213, 4853, 23).

V19 adds series and brand-new targets only. Every V18 series, target and population entry is byte for byte V18's
(tests compare them; only ``registered_connector`` is renamed). New series are local artifacts built write-once from
hash-bound public files by ``v19_build``; they are read through the same exact-binding rules as the C46/C70 artifacts.

V19 is dormant until its files exist: ``register_if_built`` does nothing without OUTCOME_CATALOG_V19.json.
"""
from dataclasses import replace
from pathlib import Path

from . import outcome_v5, outcome_v18, c46_core
from .common import ConnectorError

# Built name, not a literal: the dependency closure binds literal resource files and V19 does not exist until its build step.
CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V%d.json' % 19)
BASE = c46_core.REPO / 'data/l2_pipeline/connectors_v2/v19_20261023'


def catalog(*, verify_sources=True):
    data = outcome_v5.catalog(CATALOG_PATH, 19, verify_sources=verify_sources)
    roots = (outcome_v18.BASE.resolve(), BASE.resolve())
    for spec in data['series'].values():
        if spec['adapter'] == 'v18_artifact':
            for key in ('artifact', 'support_artifact'):
                r = spec[key]
                p = c46_core.REPO / r['path']
                if not any(p.resolve().is_relative_to(root) for root in roots):
                    raise ConnectorError('V19 artifact outside version root')
                if verify_sources and c46_core.sha(p) != r['sha256']:
                    raise ConnectorError('V19 artifact changed')
    if verify_sources:
        # The inherited part is checked by V18 itself (its artifacts, local sources and hashes).
        outcome_v18.catalog(verify_sources=True)
    return data


def _construct(request, data):
    sid = request.options.get('series_id')
    spec = data['series'].get(sid)
    if spec is None or request.options.get('connector') != 'outcome_v19_' + str(sid):
        raise ConnectorError('V19 exact series identity required')
    if not outcome_v5.v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V19 exact population/measure contract required')
    if any(k in request.options for k in ('party', 'adoption', 'treatment', 'estimator')):
        raise ConnectorError('V19 is outcome-only')
    if spec['adapter'] == 'v18_artifact':
        return c46_core.construct(request, spec)
    # Everything else is V18's, unchanged; only the registered connector name differs.
    return outcome_v18._construct(replace(request, options=dict(request.options, connector='outcome_v18_' + sid)),
                                  outcome_v18.catalog(verify_sources=False))


def register():
    from scripts.l2_pipeline import connectors
    data = catalog(verify_sources=False)
    for name, entry in outcome_v5.entries(data).items():
        spec = data['series'][entry['series_id']]
        if spec.get('series_breaks'):
            entry['series_breaks'] = spec['series_breaks']
        if spec.get('required_sensitivities'):
            entry['required_sensitivities'] = spec['required_sensitivities']
        if name in connectors.CONNECTOR_CATALOG and connectors.CONNECTOR_CATALOG[name] != entry:
            raise ConnectorError('Conflicting V19 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = entry
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def register_if_built():
    """The connector package imports this unconditionally; V19 exists only after its build step."""
    return register() if CATALOG_PATH.is_file() else ()


def construct(request):
    register()
    from .rates import apply_scale
    data = catalog()
    return apply_scale(request, _construct(request, data), catalog_data=data)
