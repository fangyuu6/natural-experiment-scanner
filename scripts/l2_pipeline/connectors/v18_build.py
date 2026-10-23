"""Catalog V18: ten new public state-year sources, built write-once from pinned local bytes.

    python -B -m scripts.l2_pipeline.connectors.v18_build plan     # series, coverage, targets; writes nothing
    python -B -m scripts.l2_pipeline.connectors.v18_build build    # artifacts + OUTCOME_CATALOG_V18.json + OUTCOME_POPULATIONS_V18.json

Pure outcome-series work: no policy, treatment or outcome-association input is read. Every artifact binds the raw
file hashes (and their receipts); the build refuses to overwrite different bytes (write_once). V18 is V17 plus
the new series and new targets only: every V17 series, target and population entry stays byte for byte.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import c46_core as core, v18_sources
from .common import ConnectorError, STATES, metadata, row
from .support_schema import C70_SUPPORT_FIELDS
from .wonder import write_once
from ..storage import sha

HERE = Path(__file__).parent
REPO = core.REPO
BASE = REPO / 'data/l2_pipeline/connectors_v2/v18_20261020'
UNITS = tuple(sorted(STATES.values()))
V17 = HERE / ('OUTCOME_CATALOG_V%d.json' % 17)
P17 = HERE / ('OUTCOME_POPULATIONS_V%d%s.json' % (17, 'B'))
V18 = HERE / ('OUTCOME_CATALOG_V%d.json' % 18)
P18 = HERE / ('OUTCOME_POPULATIONS_V%d.json' % 18)
AUTHORITY = 'docs/plans/l2-pipeline/CONNECTORS_V18_STATUS_20261020.md (T184, hub brief 2026-10-20; owner allows free public downloads)'
# Series-level definition breaks the cell flags cannot carry (window edges for the design windows).
EXTRA_BREAKS = {
    'census_state_finance_hist_': [(2005, 'census_2005_item_code_redefinitions')],
}
# Units with these exact strings are counts for the rate machinery; everything else here is a rate, share, index or level.
COUNT_UNITS = {'jobs'}


@contextmanager
def settings(module, **changes):
    old = {name: getattr(module, name) for name in changes}
    try:
        for name, value in changes.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in old.items():
            setattr(module, name, value)


def _artifact(series):
    rows, meta = [], []
    bad = set()
    for u in UNITS:
        for y in series.years:
            v = series.values.get((u, y))
            f = dict(series.flags.get((u, y), {}))
            extra = set(f) - core.SUPPORT_FIELDS
            if extra:
                raise ConnectorError('Non-metadata flags in V18 support: %s' % sorted(extra))
            usable = v is not None and not any(f.get(k) for k in ('unreliable', 'suppressed', 'limited_data', 'partial_year', 'provisional'))
            kwargs = {} if series.units in COUNT_UNITS else dict(outcome_scale='rate')
            kwargs.update({k: x for k, x in f.items() if k in C70_SUPPORT_FIELDS})
            rows.append(row(u, y, v, series.units, **kwargs))
            meta.append(metadata(rows[-1], usable, **f))
    return rows, meta


def build_series(series):
    rows, meta = _artifact(series)
    paths = list(dict.fromkeys(Path(p) for p in series.paths))
    inventory = [core.source_record(p) for p in paths]
    with settings(core, BASE=BASE):
        spec = core.save_panel(series.sid, series.filters, rows, meta, inventory, series.limitations,
                               dict(series.details, units=series.units, ages=list(series.ages)), series.population, series.url,
                               requested_year_range=[min(series.years), max(series.years)], legal_certification_granted=False)
    breaks = sorted({(y, f['series_break']) for (u, y), f in series.flags.items() if f.get('series_break')})
    for prefix, extra in EXTRA_BREAKS.items():
        if series.sid.startswith(prefix):
            breaks = sorted(set(breaks) | set(extra))
    spec.update(adapter='v18_artifact', adapter_version='v18_offline_v1', registered_connector='outcome_v18_' + series.sid,
                construct=series.construct, ages=list(series.ages), series_breaks=[list(b) for b in breaks])
    return spec


def derive(v17, p17, specs, series_by_id, *, v17_ref, p17_ref):
    """Pure: V17 catalog and populations in, V18 out. New series and brand-new targets only."""
    data = deepcopy(v17)
    data.update(catalog_id='first_order_outcomes_v18', schema_version=18, inherited_catalog=v17_ref,
                registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v18.construct',
                registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v18.register',
                acquisition_entrypoint='scripts.l2_pipeline.connectors.v18_build.build')
    for spec in data['series'].values():
        spec['registered_connector'] = 'outcome_v18_' + spec['series_id']
    meta = deepcopy(p17)
    meta.update(catalog_id='first_order_outcomes_v18', inherited_population_metadata=p17_ref)
    existing_targets = set(data['targets'])
    held_back = {}
    for sid, spec in specs.items():
        if sid in data['series']:
            raise ConnectorError('V18 series id already in V17: ' + sid)
        series = series_by_id[sid]
        data['series'][sid] = spec
        meta['series'][sid] = dict(construct=series.construct, ages=list(series.ages), restrictions={}, filters=series.filters,
                                   population_basis=series.population, population_unit=series.population_unit,
                                   metadata_basis='V18 published source population and construct; no policy/outcome associations')
        for alias in dict.fromkeys([series.construct] + list(series.aliases)):
            if alias in existing_targets:
                # A V17 target is never changed: its selectable series are part of frozen card behaviour. Reviewed later.
                held_back.setdefault(alias, []).append(sid)
                continue
            target = data['targets'].setdefault(alias, dict(series=[], selection_authorized=True, citation=AUTHORITY))
            if sid not in target['series']:
                target['series'].append(sid)
    for alias in [t for t in data['targets'] if t not in existing_targets]:
        construct_ids = data['targets'][alias]['series']
        first = series_by_id[construct_ids[0]]
        if not any(t['construct'] == alias for t in meta['targets']):
            meta['targets'].append(dict(construct=alias, aliases=[alias], ages=list(first.ages), restrictions={},
                                        rule='V18 exact published construct; outcome-only'))
    return data, meta, held_back


def plan():
    out = []
    for s in v18_sources.all_series():
        out.append(dict(series_id=s.sid, units=s.units, years=[min(s.years), max(s.years)], cells=len(s.years) * len(UNITS),
                        available=len(s.values), construct=s.construct, sources=[Path(p).name for p in s.paths]))
    return out


def build():
    series = v18_sources.all_series()
    by_id = {s.sid: s for s in series}
    v17 = json.loads(V17.read_text())
    p17 = json.loads(P17.read_text())
    specs = {}
    for s in series:
        specs[s.sid] = build_series(s)
    data, meta, held_back = derive(v17, p17, specs, by_id,
                                   v17_ref=dict(path=str(V17.relative_to(REPO)), sha256=sha(V17)),
                                   p17_ref=dict(path=str(P17.relative_to(REPO)), sha256=sha(P17)))
    write_once(V18, data)
    write_once(P18, meta)
    receipts = {}
    for sp in specs.values():
        artifact = json.loads((REPO / sp['artifact']['path']).read_text())
        for r in artifact['inventory']:
            receipts[r['path']] = r
    write_once(BASE / 'BUILD_RECEIPT.json', dict(
        schema='l2_v18_build_v1', authority=AUTHORITY,
        catalog_v17=dict(path=str(V17.relative_to(REPO)), sha256=sha(V17)), catalog_v18=dict(path=str(V18.relative_to(REPO)), sha256=sha(V18)),
        populations_v17b=dict(path=str(P17.relative_to(REPO)), sha256=sha(P17)), populations_v18=dict(path=str(P18.relative_to(REPO)), sha256=sha(P18)),
        series={sid: dict(artifact=sp['artifact'], support_artifact=sp['support_artifact'], coverage=sp['observed_coverage']) for sid, sp in specs.items()},
        sources=sorted(receipts.values(), key=lambda r: r['path']), targets_held_back_existing_in_v17=held_back,
        held_out=dict(vera_incarceration='Vera Limited License (research use only, non-dissemination, indemnity); downloaded and not parsed pending an owner decision'),
        outcome_association_calls=0, actual_data_estimation_calls=0))
    return json.loads((BASE / 'BUILD_RECEIPT.json').read_text())


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else 'plan'
    result = {'plan': plan, 'build': build}[action]()
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
