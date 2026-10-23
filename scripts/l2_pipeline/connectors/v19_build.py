"""Catalog V19: six BLS QCEW private-ownership industry series, built write-once from pinned local bytes.

    python -B -m scripts.l2_pipeline.connectors.v19_build plan     # series, coverage, suppression counts, step check; writes nothing
    python -B -m scripts.l2_pipeline.connectors.v19_build build    # artifacts + OUTCOME_CATALOG_V19.json + OUTCOME_POPULATIONS_V19.json

Pure outcome-series work: no policy, treatment or outcome-association input is read. Every artifact binds the raw
file hashes (and their receipts); the build refuses to overwrite different bytes (write_once). V19 is V18 plus the
six new series and six new targets only (V18 series, targets and population entries stay byte for byte, except
``registered_connector``). The V19 populations table is the V18 table plus the one V17C field
(``preferred_series: fbi_ucr_rape_legacy_rate`` on construct ``rape``) plus the six series and six targets.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

from . import c46_core as core, v18_build, v19_sources
from .common import ConnectorError, STATES
from .v18_build import build_series as _build_series, settings
from .wonder import write_once
from ..storage import sha

HERE = Path(__file__).parent
REPO = core.REPO
BASE = REPO / 'data/l2_pipeline/connectors_v2/v19_20261023'
UNITS = tuple(sorted(STATES.values()))
V18 = HERE / ('OUTCOME_CATALOG_V%d.json' % 18)
P18 = HERE / ('OUTCOME_POPULATIONS_V%d.json' % 18)
P17B = HERE / ('OUTCOME_POPULATIONS_V%d%s.json' % (17, 'B'))
P17C = HERE / ('OUTCOME_POPULATIONS_V%d%s.json' % (17, 'C'))
V19 = HERE / ('OUTCOME_CATALOG_V%d.json' % 19)
P19 = HERE / ('OUTCOME_POPULATIONS_V%d.json' % 19)
AUTHORITY = 'docs/plans/l2-pipeline/CONNECTORS_V19_STATUS_20261023.md (T273E/T277 combined window, hub brief 2026-10-23; owner allows free public downloads)'
RULE = 'V19 exact published construct; outcome-only'
# Hub decision B1 (2026-10-23): the V18 populations table gave construct snap_participation a second candidate (the FNS series). Designs already
# in the pipeline keep the 10-20 resolution through one family-specific target per design family (same aliases, same rule text, preferred_series
# pinned); every other family resolves by the normal V6.3 rule.
FAMILY_PINS = (
    ('snap_participation', 'snap_broad_based_categorical_eligibility', 'acs1_snap_household_receipt_rate'),
    ('snap_participation', 'snap_call_center_option', 'acs1_snap_household_receipt_rate'),
    ('snap_participation', 'snap_online_application_option', 'acs1_snap_household_receipt_rate'),
    ('snap_participation', 'snap_interview_waiver_option', 'acs1_snap_household_receipt_rate'),
)
# Series-level definition breaks the cell flags cannot carry, keyed by series-id prefix (none needed so far).
EXTRA_BREAKS = {}
# Cell-level breaks written as `series_break` flags on every unit of the named year: (sid, year, note).
# Set from the plan's step check (T273E section 9): the national common-state sums of NAICS 4853 step up in 2023 and 2024, and each step
# is one state (employment: WA 602 -> 11,717 in 2023, NY 7,517 -> 88,653 in 2024; establishments: NY 1,546 -> 1,844 in 2023); the
# sum without that state moves 3-5 percent. Cause unverified (label only). A [year, note] series break carries no
# `definition_change` flag, so it is recorded in the catalog but is not a window edge.
CELL_BREAKS = [
    ('bls_qcew_private_taxi_limousine_employment', 2023, 'qcew_4853_step_2022_23_national_sum_driven_by_WA_cause_unverified'),
    ('bls_qcew_private_taxi_limousine_employment', 2024, 'qcew_4853_step_2023_24_national_sum_driven_by_NY_cause_unverified'),
    ('bls_qcew_private_taxi_limousine_establishments', 2023, 'qcew_4853_step_2022_23_national_sum_driven_by_NY_cause_unverified'),
]


def _with_cell_breaks(series):
    for sid, year, note in CELL_BREAKS:
        if series.sid == sid and year in series.years:
            for u in UNITS:
                series.flags.setdefault((u, year), {})['series_break'] = note
    return series


def all_series():
    return [_with_cell_breaks(s) for s in v19_sources.all_series()]


def build_series(series):
    with settings(v18_build, BASE=BASE, EXTRA_BREAKS=EXTRA_BREAKS):
        spec = _build_series(series)
    spec.update(adapter='v18_artifact', adapter_version='v18_offline_v1', registered_connector='outcome_v19_' + series.sid)
    return spec


def rape_field(p17b, p17c):
    """The single V17C change as {construct: {field: value}}; refuses anything else so V19 cannot silently carry more."""
    if {k: v for k, v in p17b.items() if k != 'targets'} != {k: v for k, v in p17c.items() if k != 'targets'} or len(p17b['targets']) != len(p17c['targets']):
        raise ConnectorError('V17C differs from V17B beyond the targets list')
    change = {}
    for b, c in zip(p17b['targets'], p17c['targets']):
        if b != c:
            extra = {k: c[k] for k in c if k not in b or b[k] != c[k]}
            if set(b) - set(c) or b['construct'] != c['construct']:
                raise ConnectorError('V17C removes or renames a V17B target field')
            change[c['construct']] = extra
    if change != {'rape': {'preferred_series': 'fbi_ucr_rape_legacy_rate'}}:
        raise ConnectorError('Unexpected V17C change: %r' % (change,))
    return change


def family_pin_targets(meta, pins=FAMILY_PINS):
    """Pure: family-specific copies of an existing generic target with preferred_series set; refuses anything ambiguous."""
    out = []
    for construct, family, series in pins:
        base = [t for t in meta['targets'] if t['construct'] == construct and not t.get('family')]
        if len(base) != 1 or 'preferred_series' in base[0]:
            raise ConnectorError('Family pin needs exactly one generic unpinned target: ' + construct)
        if meta['series'].get(series, {}).get('construct') != construct:
            raise ConnectorError('Family pin series is not of the construct: ' + series)
        if any(t.get('family') == family and t['construct'] == construct for t in meta['targets']):
            raise ConnectorError('Family target already exists: ' + family)
        out.append(dict(deepcopy(base[0]), family=family, preferred_series=series))
    return out


def derive(v18, p18, specs, series_by_id, *, v18_ref, p18_ref, v17c_change, family_pins=FAMILY_PINS):
    """Pure: V18 catalog and populations in, V19 out. New series and brand-new targets only, plus the V17C field."""
    data = deepcopy(v18)
    data.update(catalog_id='first_order_outcomes_v19', schema_version=19, inherited_catalog=v18_ref,
                registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v19.construct',
                registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v19.register',
                acquisition_entrypoint='scripts.l2_pipeline.connectors.v19_build.build')
    for spec in data['series'].values():
        spec['registered_connector'] = 'outcome_v19_' + spec['series_id']
    meta = deepcopy(p18)
    meta.update(catalog_id='first_order_outcomes_v19', inherited_population_metadata=p18_ref)
    for construct, fields in v17c_change.items():
        hit = [t for t in meta['targets'] if t['construct'] == construct]
        if len(hit) != 1 or any(k in hit[0] for k in fields):
            raise ConnectorError('V17C field already present or target ambiguous in V18 populations: ' + construct)
        hit[0].update(fields)
    meta['targets'].extend(family_pin_targets(meta, family_pins))
    existing_targets = set(data['targets'])
    taken = {a for t in meta['targets'] for a in t['aliases']} | {t['construct'] for t in meta['targets']} | existing_targets
    held_back = {}
    for sid, spec in specs.items():
        if sid in data['series']:
            raise ConnectorError('V19 series id already in V18: ' + sid)
        series = series_by_id[sid]
        if series.construct in taken:
            raise ConnectorError('V19 construct already a V18 target or alias: ' + series.construct)
        data['series'][sid] = spec
        meta['series'][sid] = dict(construct=series.construct, ages=list(series.ages), restrictions={}, filters=series.filters,
                                   population_basis=series.population, population_unit=series.population_unit,
                                   metadata_basis='V19 published source population and construct; no policy/outcome associations')
        data['targets'][series.construct] = dict(series=[sid], selection_authorized=True, citation=AUTHORITY)
        aliases = [series.construct]
        phrase = v19_sources.PHRASE_ALIASES.get(series.construct)
        if phrase:
            if phrase in taken:
                raise ConnectorError('V19 phrase alias already used: ' + phrase)
            aliases.append(phrase)
        # construct = the series' own construct (V18's loop wrote the alias here; V19 keeps aliases separate).
        meta['targets'].append(dict(ages=list(series.ages), aliases=aliases, construct=series.construct, restrictions={}, rule=RULE))
    return data, meta, held_back


def diagnostics(series):
    """Per-series suppression, absence and zero counts, per-year usable counts (value-free except zeros/step totals)."""
    out = []
    for s in series:
        cells = [(u, y) for u in UNITS for y in s.years]
        suppressed = [k for k in cells if s.flags.get(k, {}).get('suppressed')]
        absent = [k for k in cells if k not in s.values and not s.flags.get(k, {}).get('suppressed')]
        zeros = [k for k in cells if s.values.get(k) == 0.0]
        out.append(dict(series_id=s.sid, cells=len(cells), available=len(s.values), n_disclosure_cells=len(suppressed),
                        absent_rows=len(absent), zero_values=len(zeros), zero_cells=sorted(zeros)[:10],
                        usable_by_year={str(y): sum((u, y) in s.values and not s.flags.get((u, y), {}).get('suppressed') for u in UNITS) for y in s.years},
                        absent_by_year={str(y): sum(1 for u in UNITS if (u, y) in absent) for y in s.years if any((u, y) in absent for u in UNITS)},
                        n_by_year={str(y): sum(1 for u in UNITS if (u, y) in suppressed) for y in s.years if any((u, y) in suppressed for u in UNITS)}))
    return out


def step_check(series, naics='4853', years=(2021, 2022, 2023, 2024)):
    """Totals over the states present in all of the compared years, and year-to-year log changes (T273E section 9)."""
    import math
    result = {}
    for s in series:
        if s.filters.get('industry') != naics:
            continue
        common = [u for u in UNITS if all((u, y) in s.values and s.values[u, y] > 0 for y in years)]
        totals = {y: sum(s.values[u, y] for u in common) for y in years}
        # Reference: the same statistic for every adjacent pair of years over the states present in both.
        history = {}
        for y in s.years[1:]:
            both = [u for u in UNITS if (u, y - 1) in s.values and (u, y) in s.values and s.values[u, y - 1] > 0 and s.values[u, y] > 0]
            if len(both) >= 30:
                history[y] = round(math.log(sum(s.values[u, y] for u in both) / sum(s.values[u, y - 1] for u in both)), 4)
        largest = {}
        for a, b in zip(years, years[1:]):
            top = max(common, key=lambda u: abs(s.values[u, b] - s.values[u, a]))
            rest = [u for u in common if u != top]
            largest['%d_%d' % (a, b)] = dict(state=top, from_value=s.values[top, a], to_value=s.values[top, b],
                                             log_change_of_sum_without_that_state=round(math.log(sum(s.values[u, b] for u in rest) / sum(s.values[u, a] for u in rest)), 4))
        result[s.sid] = dict(states_in_all_compared_years=len(common), totals={str(y): totals[y] for y in years}, largest_state_change=largest,
                             log_change_by_year={str(k): v for k, v in history.items()})
    return result


def plan():
    series = all_series()
    out = []
    for s in series:
        out.append(dict(series_id=s.sid, units=s.units, years=[min(s.years), max(s.years)], cells=len(s.years) * len(UNITS),
                        available=len(s.values), construct=s.construct, sources=[Path(p).name for p in s.paths]))
    return dict(series=out, diagnostics=diagnostics(series), step_check_naics_4853=step_check(series))


def build():
    series = all_series()
    by_id = {s.sid: s for s in series}
    v18 = json.loads(V18.read_text())
    p18 = json.loads(P18.read_text())
    change = rape_field(json.loads(P17B.read_text()), json.loads(P17C.read_text()))
    specs = {s.sid: build_series(s) for s in series}
    data, meta, held_back = derive(v18, p18, specs, by_id,
                                   v18_ref=dict(path=str(V18.relative_to(REPO)), sha256=sha(V18)),
                                   p18_ref=dict(path=str(P18.relative_to(REPO)), sha256=sha(P18)), v17c_change=change)
    write_once(V19, data)
    write_once(P19, meta)
    receipts = {}
    for sp in specs.values():
        artifact = json.loads((REPO / sp['artifact']['path']).read_text())
        for r in artifact['inventory']:
            receipts[r['path']] = r
    write_once(BASE / 'BUILD_RECEIPT.json', dict(
        schema='l2_v19_build_v1', authority=AUTHORITY,
        catalog_v18=dict(path=str(V18.relative_to(REPO)), sha256=sha(V18)), catalog_v19=dict(path=str(V19.relative_to(REPO)), sha256=sha(V19)),
        populations_v18=dict(path=str(P18.relative_to(REPO)), sha256=sha(P18)), populations_v19=dict(path=str(P19.relative_to(REPO)), sha256=sha(P19)),
        populations_v17c_field_source=dict(path=str(P17C.relative_to(REPO)), sha256=sha(P17C), change=change),
        family_pins=[dict(construct=c, family=f, preferred_series=sid) for c, f, sid in FAMILY_PINS],
        series={sid: dict(artifact=sp['artifact'], support_artifact=sp['support_artifact'], coverage=sp['observed_coverage']) for sid, sp in specs.items()},
        sources=sorted(receipts.values(), key=lambda r: r['path']), targets_held_back_existing_in_v18=held_back,
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
