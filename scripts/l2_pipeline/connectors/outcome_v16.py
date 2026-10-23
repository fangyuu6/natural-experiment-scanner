"""YIELD_FIXES 2026-10-16 prospective catalog: V15 plus local public sources.

The diagnostic of 2026-10-16 found 14 designs parked for "no local public files" or re-opened
by a Gate 0 catalog check that could not run. V16 changes only the source bindings of the
series that were public but not on disk (LAUS employment, CES private weekly earnings, ACS1
SNAP receipt rate). Every other series, population semantic and construction rule is V15's,
byte for byte (tests/test_yield_fixes_20261016.py compares them series by series).
"""
from pathlib import Path
from dataclasses import replace
from . import outcome_v5, outcome_v15, c46_core, c70_build
from .common import ConnectorError
CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V16.json')
# Series whose bindings V16 adds; the build script and the identity test use this list.
LOCAL_SERIES = ('bls_laus_employment', 'bls_ces_private_weekly_earnings', 'acs1_snap_household_receipt_rate')


def catalog(*, verify_sources=True):
    data = outcome_v5.catalog(CATALOG_PATH, 16, verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter'] == 'c70_artifact':
            for key in ('artifact', 'support_artifact'):
                r = spec[key]
                p = c46_core.REPO / r['path']
                if not p.resolve().is_relative_to(c70_build.BASE.resolve()):
                    raise ConnectorError('V16 artifact outside version root')
                if verify_sources and c46_core.sha(p) != r['sha256']:
                    raise ConnectorError('V16 artifact changed')
    if verify_sources:
        for sid in LOCAL_SERIES:
            spec = data['series'][sid]
            if not spec.get('paths') or set(spec['paths']) != set(spec.get('source_sha256', {})):
                raise ConnectorError('V16 local source bindings incomplete: ' + sid)
            for relative in spec['paths']:
                if c46_core.sha(c46_core.REPO / relative) != spec['source_sha256'][relative]:
                    raise ConnectorError('V16 local source changed: ' + relative)
    return data


def _construct(request, data):
    sid = request.options.get('series_id')
    spec = data['series'].get(sid)
    if spec is None or request.options.get('connector') != 'outcome_v16_' + str(sid):
        raise ConnectorError('V16 exact series identity required')
    if not outcome_v5.v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V16 exact population/measure contract required')
    if any(k in request.options for k in ('party', 'adoption', 'treatment', 'estimator')):
        raise ConnectorError('V16 is outcome-only')
    # Construction is V15's, unchanged; only the card's bound file list differs.
    return outcome_v15._construct(replace(request, options=dict(request.options, connector='outcome_v15_' + sid)),
                                  outcome_v15.catalog(verify_sources=False))


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
            raise ConnectorError('Conflicting V16 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = entry
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def construct(request):
    register()
    from .rates import apply_scale
    data = catalog()
    return apply_scale(request, _construct(request, data), catalog_data=data)
