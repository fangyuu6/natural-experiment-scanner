"""Catalog V18 (T184, 2026-10-20): V17 plus public state-year series for alcohol, business dynamics, school vaccination,
graduation, state finance 1996-2011, BEA income and GDP, FHFA house prices, SNAP and QCEW industry slices.

V18 adds series and brand-new targets only. Every V17 series, target and population entry is byte for byte V17's
(tests compare them). New series are local artifacts built write-once from hash-bound public files by
``v18_build``; they are read through the same exact-binding rules as the C46/C70 artifacts.

V18 is dormant until its files exist: ``register_if_built`` does nothing without OUTCOME_CATALOG_V18.json, and the
current-catalog pointer (population_v63.CURRENT_PATH) is a separate, explicit step.
"""
from dataclasses import replace
from pathlib import Path

from . import outcome_v5, outcome_v17, c46_core
from .common import ConnectorError

# Built name, not a literal: the dependency closure binds literal resource files and V18 does not exist until its build step.
CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V%d.json' % 18)
BASE = c46_core.REPO / 'data/l2_pipeline/connectors_v2/v18_20261020'


def catalog(*, verify_sources=True):
    data = outcome_v5.catalog(CATALOG_PATH, 18, verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter'] == 'v18_artifact':
            for key in ('artifact', 'support_artifact'):
                r = spec[key]
                p = c46_core.REPO / r['path']
                if not p.resolve().is_relative_to(BASE.resolve()):
                    raise ConnectorError('V18 artifact outside version root')
                if verify_sources and c46_core.sha(p) != r['sha256']:
                    raise ConnectorError('V18 artifact changed')
    if verify_sources:
        # The inherited part is checked by V17 itself (its artifacts, local sources and hashes).
        outcome_v17.catalog(verify_sources=True)
    return data


def _construct(request, data):
    sid = request.options.get('series_id')
    spec = data['series'].get(sid)
    if spec is None or request.options.get('connector') != 'outcome_v18_' + str(sid):
        raise ConnectorError('V18 exact series identity required')
    if not outcome_v5.v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V18 exact population/measure contract required')
    if any(k in request.options for k in ('party', 'adoption', 'treatment', 'estimator')):
        raise ConnectorError('V18 is outcome-only')
    if spec['adapter'] == 'v18_artifact':
        return c46_core.construct(request, spec)
    # Everything else is V17's, unchanged; only the registered connector name differs.
    return outcome_v17._construct(replace(request, options=dict(request.options, connector='outcome_v17_' + sid)),
                                  outcome_v17.catalog(verify_sources=False))


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
            raise ConnectorError('Conflicting V18 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = entry
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def register_if_built():
    """The connector package imports this unconditionally; V18 exists only after its build step."""
    return register() if CATALOG_PATH.is_file() else ()


def construct(request):
    register()
    from .rates import apply_scale
    data = catalog()
    return apply_scale(request, _construct(request, data), catalog_data=data)
