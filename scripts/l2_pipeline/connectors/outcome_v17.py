"""NAMED_PANEL Q2 (2026-10-16) prospective catalog: V16 plus the LAUS employment-population ratio.

Hub ruling HUB_RULING_20261016_NAMED_PANEL.md, Q2: the four LAUS employment-count designs (ban_14c,
wd_implied_contract_exception, wd_public_policy_exception, credit_check_ban) get a rate outcome,
LAUS employment divided by the LAUS civilian noninstitutional population age 16+ (same release, same
state-year). V17 adds two series and nothing else (tests/test_named_panel_20261016.py compares every other
series with V16 byte for byte):

* ``bls_laus_civilian_noninstitutional_population``: BLS LAUS measure 09, persons, annual value = mean of the
  twelve months (the same construction as the V16 employment series).
* ``bls_laus_employment_population_ratio``: 100 x employment / population, percent. Where LAUS lacks the
  population for a state-year, the Census population age 16+ already on disk may stand in, flagged on the row
  (``population_source``); no LAUS value is ever replaced when it exists.

V17 is dormant until its files exist: ``register_if_built`` does nothing without OUTCOME_CATALOG_V17.json, and
the current-catalog pointer (population_v63.CURRENT_PATH) is a separate, explicit step.
"""
from dataclasses import replace
import csv
from pathlib import Path

from . import outcome_v5, outcome_v16, c46_core, c70_build
from . import outcome_v2_labor as labor
from .common import ConnectorError, ConnectorNotReady, PanelData, inventory, metadata, row

# Built name, not a literal: the dependency closure binds literal resource files and V17 does not exist until its build step.
CATALOG_PATH = Path(__file__).with_name('OUTCOME_CATALOG_V%d.json' % 17)
POPULATION_ID = 'bls_laus_civilian_noninstitutional_population'
RATIO_ID = 'bls_laus_employment_population_ratio'
NUMERATOR_ID = 'bls_laus_employment'
NEW_SERIES = (POPULATION_ID, RATIO_ID)
LOCAL_SERIES = outcome_v16.LOCAL_SERIES + NEW_SERIES
# BLS LAUS measure 09, civilian noninstitutional population age 16+ (state series LAUST<fips>0000000000009).
POPULATION_SPEC = dict(series_id=POPULATION_ID, bls_measure='09', units='persons',
                       population='civilian noninstitutional residents age16+', source_url=labor.LAUS_URL)
FALLBACK_COLUMNS = {'unit', 'year', 'population', 'source'}
RATIO_POPULATION = 'civilian noninstitutional residents age16+; employed share'


def catalog(*, verify_sources=True):
    data = outcome_v5.catalog(CATALOG_PATH, 17, verify_sources=verify_sources)
    for spec in data['series'].values():
        if spec['adapter'] == 'c70_artifact':
            for key in ('artifact', 'support_artifact'):
                r = spec[key]
                p = c46_core.REPO / r['path']
                if not p.resolve().is_relative_to(c70_build.BASE.resolve()):
                    raise ConnectorError('V17 artifact outside version root')
                if verify_sources and c46_core.sha(p) != r['sha256']:
                    raise ConnectorError('V17 artifact changed')
    if verify_sources:
        for sid in LOCAL_SERIES:
            spec = data['series'][sid]
            if not spec.get('paths') or set(spec['paths']) != set(spec.get('source_sha256', {})):
                raise ConnectorError('V17 local source bindings incomplete: ' + sid)
            for relative in spec['paths']:
                if c46_core.sha(c46_core.REPO / relative) != spec['source_sha256'][relative]:
                    raise ConnectorError('V17 local source changed: ' + relative)
    return data


def _subrequest(request, names):
    """The same request, restricted to the named bound files (their hashes stay checked by the request)."""
    declared = request.options.get('source_sha256', {})
    # The catalog spells its files relative to the repository. A request built through bind_outcome carries them
    # absolute (and hashes them under the absolute spelling), and its base_dir is the card directory, so a bare
    # catalog name would resolve under the card directory and drop the hash check. Hand each named file back under
    # the spelling the request already declared (T190, 2026-10-20).
    spelled = {}
    for key in declared:
        spelled.setdefault(_relative(request, Path(key)), key)
    chosen = [spelled.get(n, n) for n in names]
    return replace(request, options=dict(request.options, paths=chosen,
                                         source_sha256={n: declared[n] for n in chosen if n in declared}))


def _relative(request, path):
    for root in (request.base_dir, c46_core.REPO):
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


def _fallback_population(request, names):
    """unit/year -> (population, source label) from the declared Census population age 16+ CSV."""
    result = {}
    for path in request.paths():
        if _relative(request, path) not in names:
            continue
        with path.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if not FALLBACK_COLUMNS <= set(reader.fieldnames or []):
                raise ConnectorError('Census 16+ fallback requires unit/year/population/source')
            for cell in reader:
                key = cell['unit'], int(cell['year'])
                if key in result:
                    raise ConnectorError('Duplicate Census 16+ fallback state-year')
                value = float(cell['population'])
                if value <= 0 or value != value:
                    raise ConnectorError('Census 16+ fallback population must be positive')
                result[key] = (value, cell['source'])
    return result


def _population(request):
    spec = dict(POPULATION_SPEC, frequency=['annual'], years=[1976, 2025])
    return labor._published(request, spec)


def _employment(request):
    return labor._published(request, labor.SERIES[NUMERATOR_ID])


def _ratio(request, data):
    spec = data['series'][RATIO_ID]
    if request.filters:
        raise ConnectorNotReady('LAUS ratio has a fixed population; custom population filters need a separate series')
    if request.frequency != 'annual':
        raise ConnectorNotReady('LAUS ratio is annual')
    if not set(request.years) <= set(spec['years']):
        raise ConnectorNotReady('V17 request exceeds the LAUS ratio coverage (numerator and denominator both bound)')
    paths = request.paths()
    names = {_relative(request, p) for p in paths}
    numerator, denominator = set(spec['numerator_paths']), set(spec['denominator_paths'])
    fallback = set(spec.get('fallback_paths', []))
    if names != numerator | denominator | fallback:
        raise ConnectorError('LAUS ratio card must bind exactly the catalog numerator, denominator and fallback files')
    emp = _employment(_subrequest(request, sorted(numerator)))
    pop = _population(_subrequest(request, sorted(denominator)))
    backup = _fallback_population(request, fallback) if fallback else {}
    pop_by_key = {(r['unit'], r['year']): (r, m) for r, m in zip(pop.rows, pop.metadata)}
    rows, metas, used = [], [], []
    for erow, emeta in zip(emp.rows, emp.metadata):
        key = erow['unit'], erow['year']
        prow, pmeta = pop_by_key.get(key, ({'outcome_value': ''}, {'available': 0, 'months_present': 0}))
        employed = erow['outcome_value'] if emeta['available'] else None
        laus = prow['outcome_value'] if pmeta['available'] and prow['outcome_value'] != '' else None
        if laus is not None:
            denominator_value, source = float(laus), 'laus'
        elif key in backup:
            denominator_value, source = backup[key][0], 'census_population_age16plus_fallback'
            used.append(dict(unit=key[0], year=key[1], population=denominator_value, source=backup[key][1]))
        else:
            denominator_value, source = None, 'unavailable'
        available = employed is not None and denominator_value is not None
        value = 100.0 * float(employed) / denominator_value if available else ''
        record = row(key[0], key[1], value, 'percent', 'annual', None, series_id=RATIO_ID, population=RATIO_POPULATION,
                     population_source=source, population_persons='' if denominator_value is None else denominator_value)
        rows.append(record)
        metas.append(metadata(record, available, months_present=min(emeta.get('months_present', 0), pmeta.get('months_present', 0)),
                              preliminary=int(bool(emeta.get('preliminary', 0) or pmeta.get('preliminary', 0))),
                              population_fallback=int(source == 'census_population_age16plus_fallback'),
                              missing_reason='' if available else
                              'LAUS employment incomplete' if employed is None else 'No LAUS or Census population for the state-year'))
    extra = tuple(inventory(p, request) | {'role': 'Census population age 16+ fallback (flagged per row)'}
                  for p in paths if _relative(request, p) in fallback)
    return PanelData(tuple(rows), tuple(metas), emp.inventory + pop.inventory + extra,
        emp.limitations + ('Employment-population ratio: LAUS employment divided by LAUS civilian noninstitutional population age 16+, '
                           'annual means of twelve months each, in percentage points; LAUS state series are model based.',),
        dict(series=spec, aggregation='100 x annual mean employment / annual mean civilian noninstitutional population age16+',
             numerator=NUMERATOR_ID, denominator=POPULATION_ID, fallback_rows=used, fallback_row_count=len(used),
             denominator_back_calculation=False, count_fallback=False))


def _construct(request, data):
    sid = request.options.get('series_id')
    spec = data['series'].get(sid)
    if spec is None or request.options.get('connector') != 'outcome_v17_' + str(sid):
        raise ConnectorError('V17 exact series identity required')
    if not outcome_v5.v2_supported(request.options, request.options.get('measure_id')):
        raise ConnectorError('V17 exact population/measure contract required')
    if any(k in request.options for k in ('party', 'adoption', 'treatment', 'estimator')):
        raise ConnectorError('V17 is outcome-only')
    if sid == RATIO_ID:
        return _ratio(request, data)
    if sid == POPULATION_ID:
        if request.filters or request.frequency != 'annual' or not set(request.years) <= set(spec['years']):
            raise ConnectorNotReady('LAUS population is annual, unfiltered and limited to its bound years')
        return _population(request)
    # Everything else is V16's, unchanged; only the registered connector name differs.
    return outcome_v16._construct(replace(request, options=dict(request.options, connector='outcome_v16_' + sid)),
                                  outcome_v16.catalog(verify_sources=False))


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
            raise ConnectorError('Conflicting V17 registration')
        if name not in connectors.CONNECTOR_CATALOG:
            connectors.CONNECTOR_CATALOG[name] = entry
            connectors.ADAPTERS[name] = lambda request, data=data: _construct(request, data)
            connectors.FIRST_ORDER_MEASURES[name] = {entry['series_id']}
    return tuple(sorted(outcome_v5.entries(data)))


def register_if_built():
    """The connector package imports this unconditionally; V17 exists only after its build step."""
    return register() if CATALOG_PATH.is_file() else ()


def construct(request):
    register()
    from .rates import apply_scale
    data = catalog()
    return apply_scale(request, _construct(request, data), catalog_data=data)
