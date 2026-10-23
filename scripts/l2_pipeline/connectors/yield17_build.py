"""NAMED_PANEL Q2: download the LAUS population series and build catalog V17 (V16 plus the ratio).

    python -B -m scripts.l2_pipeline.connectors.yield17_build plan       # what is on disk, what is missing, queries needed
    python -B -m scripts.l2_pipeline.connectors.yield17_build download   # the missing keyless BLS API v1 queries, within the daily 25
    python -B -m scripts.l2_pipeline.connectors.yield17_build build      # V17 files, only when every state-year is bound

Source: BLS LAUS measure 09 (civilian noninstitutional population age 16+), series LAUST<fips>0000000000009, through
the connector's documented keyless API v1 route (the flat file answers HTTP 403 to the connector client and is not
retried or bypassed). Every response is saved with a byte receipt by the connector; this script reads series
identifiers, years and months only (it computes nothing from the values).
The build is repeatable and write-once: a second run reproduces the same bytes. If LAUS lacks the population for a
state-year the build refuses, unless the Census population age 16+ fallback CSV (unit, year, population, source,
with its receipt) covers exactly those state-years.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

from . import yield16_build as y16
from .common import ConnectorError, ConnectorNotReady

HERE = y16.HERE
REPO = y16.REPO
V16, V17 = y16.V16, HERE / ('OUTCOME_CATALOG_V%d.json' % 17)  # built names: no literal resource before the build step
P16, P17 = y16.P16, HERE / ('OUTCOME_POPULATIONS_V%d.json' % 17)
BASE = y16.BASE
RAW = BASE / 'bls_api_v1/raw'
OUT = BASE / 'named_panel_20261016'
AUTHORITY = REPO / 'docs/plans/l2-pipeline/HUB_RULING_20261016_NAMED_PANEL.md'
FALLBACK = OUT / 'census_population_age16plus_fallback.csv'
POPULATION_ID = 'bls_laus_civilian_noninstitutional_population'
RATIO_ID = 'bls_laus_employment_population_ratio'
NUMERATOR_ID = 'bls_laus_employment'
WINDOWS = [(1976, 1985), (1986, 1995), (1996, 2005), (2006, 2015), (2016, 2025)]
UNITS = y16.STATES
ALL_UNITS = list(UNITS.values())
IDENT = re.compile(r'LAUST(\d{2})0{11}09')
BLOCKED = y16.BLOCKED + [
    dict(url='https://www.bls.gov/lau/staadata.txt', result='HTTP 404 through the connector client; not pursued, the API route was used')]
RATIO_POPULATION = 'civilian noninstitutional residents age16+; employed share'


def population_files():
    return sorted(p for p in RAW.glob('bls_v1_bls_laus_population_*.json') if not p.name.endswith('.receipt.json'))


def coverage(files):
    """(unit, year) -> months present, from the series identifiers and periods only."""
    seen = {}
    for path in files:
        value = json.loads(path.read_text())
        if value.get('status') != 'REQUEST_SUCCEEDED' or not value.get('Results', {}).get('series'):
            raise ConnectorError('BLS API response did not succeed: ' + path.name)
        for series in value['Results']['series']:
            match = IDENT.fullmatch(series['seriesID'])
            if not match:
                raise ConnectorError('Unexpected LAUS population series identifier: ' + series['seriesID'])
            unit = UNITS[match[1]]
            for record in series['data']:
                if re.fullmatch(r'M(0[1-9]|1[0-2])', record['period']):
                    seen.setdefault((unit, int(record['year'])), set()).add(record['period'])
    return seen


def gaps(seen, windows=WINDOWS):
    """Per window, the units that lack any of the twelve months of any year."""
    result = {}
    for first, last in windows:
        units = [u for u in ALL_UNITS if any(len(seen.get((u, y), ())) != 12 for y in range(first, last + 1))]
        if units:
            result[(first, last)] = units
    return result


def batches(units):
    return [units[i:i + 25] for i in range(0, len(units), 25)]


def plan():
    seen = coverage(population_files())
    missing = gaps(seen)
    quota = RAW / '.bls_v1_quota.json'
    ledger = json.loads(quota.read_text()) if quota.exists() else {}
    queries = sum(len(batches(u)) for u in missing.values())
    return dict(files=len(population_files()), complete_windows=[w for w in WINDOWS if w not in missing],
                missing={f'{a}-{b}': u for (a, b), u in missing.items()}, queries_needed=queries,
                quota_ledger=ledger)


def download(client=None):
    """Run the missing queries in window order. Stops without error when the daily limit is reached."""
    from . import outcome_v2_labor as labor
    from .outcome_v2_core import PublicClient
    client = client or PublicClient(max_bytes=100_000_000, retries=0)  # no retries: the quota reservation stays exact
    done, stopped = [], None
    for (first, last), units in gaps(coverage(population_files())).items():
        for batch in batches(units):
            try:
                labor._acquire_api('bls_laus_population', tuple(range(first, last + 1)), tuple(batch), RAW, client, 0,
                                   spec=dict(bls_measure='09'))
            except ConnectorNotReady as exc:
                stopped = str(exc)
                break
            done.append(dict(window=[first, last], units=len(batch)))
        if stopped:
            break
    return dict(downloaded=done, stopped_by_quota=stopped, remaining=plan())


def population_years(seen):
    return sorted(y for y in {y for _, y in seen} if all(len(seen.get((u, y), ())) == 12 for u in ALL_UNITS))


def fallback_rows():
    import csv
    if not FALLBACK.exists():
        return {}
    with FALLBACK.open(newline='', encoding='utf-8-sig') as stream:
        return {(r['unit'], int(r['year'])): r for r in csv.DictReader(stream)}


def derive_catalog(v16, p16, *, pop_rel, pop_hash, pop_years, ratio_years, fallback_rel, fallback_hash, pointer, v16_ref, p16_ref, lineage):
    """Pure: V16 catalog and population metadata in, V17 out. Only the two LAUS series and one target field are new."""
    data = deepcopy(v16)
    data.update(catalog_id='first_order_outcomes_v17', schema_version=17, inherited_catalog=v16_ref,
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v17.construct',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v17.register', version_lineage=lineage)
    for sid, spec in data['series'].items():
        spec['registered_connector'] = 'outcome_v17_' + sid
    emp = data['series'][NUMERATOR_ID]
    note = ('Local files: keyless BLS API v1 responses saved by the connector (LAUS state series LAUST<fips>0000000000009, '
            'civilian noninstitutional population age 16+, not seasonally adjusted; months M01-M12; annual value = 12-month mean, no M13). '
            'The flat file returned HTTP 403 and was not retried or bypassed.')
    population = deepcopy(emp)
    population.update(series_id=POPULATION_ID, bls_measure='09', units='persons',
        population='civilian noninstitutional residents age16+', paths=list(pop_rel), source_sha256=dict(pop_hash),
        locally_downloaded_years=list(pop_years), years=list(pop_years), source_year_range=[min(pop_years), max(pop_years)],
        publisher_support_years=list(pop_years), local_source_manifest=pointer, local_source_note=note,
        registered_connector='outcome_v17_' + POPULATION_ID)
    ratio = deepcopy(emp)
    ratio.update(series_id=RATIO_ID, units='percent', population=RATIO_POPULATION, frequency=['annual'],
        numerator_series=NUMERATOR_ID, denominator_series=POPULATION_ID, numerator_paths=list(emp['paths']),
        denominator_paths=list(pop_rel), fallback_paths=list(fallback_rel), paths=list(emp['paths']) + list(pop_rel) + list(fallback_rel),
        source_sha256={**emp['source_sha256'], **pop_hash, **fallback_hash},
        locally_downloaded_years=list(ratio_years), years=list(ratio_years), source_year_range=[min(ratio_years), max(ratio_years)],
        publisher_support_years=list(ratio_years), local_source_manifest=pointer,
        local_source_note=('Ratio = 100 x annual mean LAUS employment / annual mean LAUS civilian noninstitutional population age 16+; '
                           'both are LAUS state series from the keyless API v1 route (twelve months each). Where LAUS lacks the population '
                           'for a state-year the Census population age 16+ fallback is used and flagged on the row; none is used when LAUS has the value.'),
        registered_connector='outcome_v17_' + RATIO_ID, construction='laus_ratio_v17')
    data['series'][POPULATION_ID] = population
    data['series'][RATIO_ID] = ratio
    for target in data['targets'].values():
        if NUMERATOR_ID in target['series'] and RATIO_ID not in target['series']:
            target['series'] = list(target['series']) + [RATIO_ID]
    populations = deepcopy(p16)
    populations.update(catalog_id='first_order_outcomes_v17', inherited_population_metadata=p16_ref)
    populations['series'][RATIO_ID] = dict(ages=[16, 120], construct='employment_status', filters={},
        metadata_basis='NAMED_PANEL Q2: LAUS employment divided by the LAUS civilian noninstitutional population age 16+; no outcome values',
        population_basis=RATIO_POPULATION, restrictions={})
    for target in populations['targets']:
        if target['construct'] == 'employment_status' and not target.get('family'):
            target['preferred_series'] = RATIO_ID
    return data, populations


def build():
    v16 = json.loads(V16.read_text())
    emp_files = y16.bls_files(NUMERATOR_ID)
    emp_years, emp_complete = y16.bls_coverage(emp_files)
    pop_files = population_files()
    seen = coverage(pop_files)
    complete = population_years(seen)
    uncovered = sorted((u, y) for u in ALL_UNITS for y in emp_complete if y not in complete and len(seen.get((u, y), ())) != 12)
    backup = fallback_rows()
    if uncovered and set(backup) != set(uncovered):
        raise ConnectorError('LAUS population missing for %d state-years (first %s); download the rest, or supply a '
                             'Census age16+ fallback CSV with exactly those rows' % (len(uncovered), uncovered[:3]))
    if backup and not uncovered:
        raise ConnectorError('A fallback CSV exists although LAUS covers every state-year; remove it from the inputs')
    ratio_years = sorted(y for y in emp_complete if all(len(seen.get((u, y), ())) == 12 or (u, y) in backup for u in ALL_UNITS))
    if not ratio_years:
        raise ConnectorError('No year has both employment and population for every state')
    sources = {
        'bls_laus_population': [y16.receipt_row(p) for p in pop_files],
    }
    manifest_value = dict(schema='l2_named_panel_source_manifest_v1', downloaded_utc_date='2026-10-16/17',
        authority=dict(path=y16.relative(AUTHORITY), sha256=y16.sha(AUTHORITY)), sources=sources,
        fallback=(dict(path=y16.relative(FALLBACK), sha256=y16.sha(FALLBACK), rows=len(backup)) if backup else None),
        blocked_urls=BLOCKED, outcome_values_read=0, outcome_association_calls=0,
        access='public_no_account_no_key; the BLS flat file answers HTTP 403 to the connector client; the connector\'s documented keyless API v1 route supplied measure 09',
        laus_population_years_bound=complete)
    manifest = OUT / 'SOURCE_MANIFEST.json'
    y16.write_once(manifest, manifest_value)
    pointer = dict(path=y16.relative(manifest), sha256=y16.sha(manifest))
    data, populations = derive_catalog(v16, json.loads(P16.read_text()), pop_rel=[y16.relative(p) for p in pop_files],
        pop_hash={y16.relative(p): y16.sha(p) for p in pop_files}, pop_years=complete, ratio_years=ratio_years,
        fallback_rel=[y16.relative(FALLBACK)] if backup else [], fallback_hash={y16.relative(FALLBACK): y16.sha(FALLBACK)} if backup else {},
        pointer=pointer, v16_ref=dict(path=y16.relative(V16), sha256=y16.sha(V16)), p16_ref=dict(path=y16.relative(P16), sha256=y16.sha(P16)),
        lineage=dict(previous=dict(path=str(V16), sha256=y16.sha(V16)), ruling=dict(path=str(AUTHORITY), sha256=y16.sha(AUTHORITY))))
    y16.write_once(V17, data)
    y16.write_once(P17, populations)
    receipt = OUT / 'BUILD_RECEIPT.json'
    y16.write_once(receipt, dict(schema='l2_named_panel_v17_build_v1',
        catalog_v16=dict(path=y16.relative(V16), sha256=y16.sha(V16)), catalog_v17=dict(path=y16.relative(V17), sha256=y16.sha(V17)),
        populations_v16=dict(path=y16.relative(P16), sha256=y16.sha(P16)), populations_v17=dict(path=y16.relative(P17), sha256=y16.sha(P17)),
        manifest=pointer, population_files=len(pop_files), population_years=complete, ratio_years=ratio_years,
        fallback_state_years=len(backup), outcome_values_read=0, outcome_association_calls=0, actual_data_estimation_calls=0))
    return json.loads(receipt.read_text())


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0] if argv else 'plan'
    result = {'plan': plan, 'download': download, 'build': build}[action]()
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
