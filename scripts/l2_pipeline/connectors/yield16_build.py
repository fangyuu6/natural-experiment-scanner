"""Build catalog V16 (and its population metadata) from V15 plus the 2026-10-16 public downloads.

Run once, in the YIELD_FIXES code window, BEFORE the connector package can register V16:
    python -B -I scripts/l2_pipeline/connectors/yield16_build.py
It is self-contained on purpose (the package __init__ needs the file this script creates; STATES is a
literal copy checked against connectors.common by the tests) and is repeatable: a second run reproduces the same bytes. Reads receipts and presence/period keys only:
no outcome value is parsed, summed or compared.
Writes (write-once, byte-checked on repeat): OUTCOME_CATALOG_V16.json, OUTCOME_POPULATIONS_V16.json,
data/l2_pipeline/connectors_v2/yield_fixes_20261016/{SOURCE_MANIFEST,BUILD_RECEIPT}.json.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


STATES = dict(zip(
    '01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split(),
    'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))


class ConnectorError(ValueError):
    """Invalid source construction (same meaning as connectors.common.ConnectorError)."""


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_once(path, obj):
    """Same bytes and refusal rule as connectors.wonder.write_once."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if path.exists():
        if path.read_text() != content:
            raise ConnectorError('Refuse overwrite of audited artifact: ' + str(path))
    else:
        with path.open('x') as stream:
            stream.write(content)


V15, V16 = HERE / 'OUTCOME_CATALOG_V15.json', HERE / 'OUTCOME_CATALOG_V16.json'
P15, P16 = HERE / 'OUTCOME_POPULATIONS_V15.json', HERE / 'OUTCOME_POPULATIONS_V16.json'
BASE = REPO / 'data/l2_pipeline/connectors_v2'
OUT = BASE / 'yield_fixes_20261016'
AUTHORITY = REPO / 'docs/plans/l2-pipeline/YIELD_DIAGNOSTIC_20261016.md'
UNITS = set(STATES.values())
BLOCKED = [
    dict(url='https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU', result='HTTP 403 from the connector client; no bypass attempted'),
    dict(url='https://download.bls.gov/pub/time.series/sm/sm.data.56.TotalPrivate.Current', result='HTTP 403 from the connector client; no bypass attempted'),
]


def relative(path):
    return str(Path(path).resolve().relative_to(REPO))


def receipt_row(path):
    receipt = json.loads(Path(str(path) + '.receipt.json').read_text())
    if receipt.get('sha256') != sha(path) or receipt.get('size_bytes') != Path(path).stat().st_size:
        raise ConnectorError('Receipt does not bind the file: ' + str(path))
    keep = ('url', 'final_url', 'size_bytes', 'sha256', 'license', 'downloaded_at_utc', 'request_method', 'request_body_sha256',
            'artifact_role', 'derived_from_urls')
    return dict({'path': relative(path)}, **{k: receipt[k] for k in keep if k in receipt})


def bls_files(series):
    return sorted(p for p in (BASE / 'bls_api_v1/raw').glob('bls_v1_' + series + '_*.json')
                  if not p.name.endswith('.receipt.json'))


def bls_coverage(files):
    """Presence of (unit, year, month) keys only; no value is read."""
    seen, years = {}, set()
    for path in files:
        value = json.loads(path.read_text())
        if value.get('status') != 'REQUEST_SUCCEEDED' or not value.get('Results', {}).get('series'):
            raise ConnectorError('BLS API response did not succeed: ' + path.name)
        for series in value['Results']['series']:
            match = re.fullmatch(r'(?:LAUST|SMU)(\d{2})\d+', series['seriesID'])
            unit = STATES[match[1]]
            for record in series['data']:
                seen.setdefault((unit, int(record['year'])), set()).add(record['period'])
                years.add(int(record['year']))
    units = {u for u, _ in seen}
    if units != UNITS:
        raise ConnectorError('BLS download does not cover all 50 states and DC')
    complete = sorted(y for y in years if all(len(seen.get((u, y), ())) == 12 for u in UNITS))
    return sorted(years), complete


def acs_files():
    files, years = [], []
    for year in (2021, 2022, 2023, 2024):
        path = BASE / 'census_acs1/raw/tablebased' / f'acsdt1y{year}-b22003.dat'
        if path.is_file():
            files.append(path)
            years.append(year)
    for year in range(2010, 2020):
        derived = sorted((BASE / 'census_acs1/raw/seq_b22003' / str(year)).glob(f'acsdt1y{year}-b22003-*-derived.dat'))
        # A year enters only with all 50 states and DC; a partial year is never bound.
        if {re.fullmatch(rf'acsdt1y{year}-b22003-([a-z]{{2}})-derived\.dat', p.name)[1].upper() for p in derived} == UNITS:
            files.extend(derived)
            years.append(year)
    return files, sorted(years)


def bind(spec, files, downloaded_years, manifest, note, years=None):
    rel = [relative(p) for p in files]
    spec.update(paths=rel, source_sha256={r: sha(REPO / r) for r in rel},
                locally_downloaded_years=sorted(downloaded_years),
                local_source_manifest=manifest, local_source_note=note)
    if years is not None:
        spec['years'] = sorted(years)


def build():
    v15 = json.loads(V15.read_text())
    laus, ces = bls_files('bls_laus_employment'), bls_files('bls_ces_private_weekly_earnings')
    laus_years, laus_complete = bls_coverage(laus)
    ces_years, ces_complete = bls_coverage(ces)
    acs, acs_years = acs_files()
    if not laus or not ces or not acs:
        raise ConnectorError('Downloads missing; nothing to bind')
    sources = {
        'bls_laus_employment': [receipt_row(p) for p in laus],
        'bls_ces_private_weekly_earnings': [receipt_row(p) for p in ces],
        'acs1_snap_household_receipt_rate': [receipt_row(p) for p in acs],
    }
    manifest_value = dict(schema='l2_yield_fixes_source_manifest_v1', downloaded_utc_date='2026-10-16',
        authority=dict(path=relative(AUTHORITY), sha256=sha(AUTHORITY)),
        sources=sources, blocked_urls=BLOCKED, outcome_values_read=0, outcome_association_calls=0,
        acs_years_bound=acs_years,
        acs_years_refused=dict((str(y), 'connector refused: split or ambiguous table lookup; not worked around')
                               for y in range(2010, 2020) if y not in acs_years),
        bls_complete_years=dict(laus=laus_complete, ces=ces_complete),
        access='public_no_account_no_key; BLS flat files returned HTTP 403, the connector\'s documented keyless API v1 route supplied the series')
    manifest = OUT / 'SOURCE_MANIFEST.json'
    write_once(manifest, manifest_value)
    pointer = dict(path=relative(manifest), sha256=sha(manifest))
    data = deepcopy(v15)
    data.update(catalog_id='first_order_outcomes_v16', schema_version=16,
        inherited_catalog=dict(path=relative(V15), sha256=sha(V15)),
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v16.construct',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v16.register',
        version_lineage=dict(previous=dict(path=str(V15), sha256=sha(V15)), ruling=dict(path=str(AUTHORITY), sha256=sha(AUTHORITY))))
    for sid, spec in data['series'].items():
        spec['registered_connector'] = 'outcome_v16_' + sid
    api_note = ('Local files: keyless BLS API v1 responses saved by the connector (series ID prefix, state, measure %s; months M01-M12; '
                'annual value = 12-month mean, no M13). The flat file returned HTTP 403 and was not retried or bypassed.')
    bind(data['series']['bls_laus_employment'], laus, laus_years, pointer, api_note % 'LAUST..05')
    bind(data['series']['bls_ces_private_weekly_earnings'], ces, ces_years, pointer, api_note % 'SMU..11')
    bind(data['series']['acs1_snap_household_receipt_rate'], acs, acs_years, pointer,
         'Local files: Census table-based B22003 DAT (2021-2024) and per-state derived files joined from the public sequence ZIPs '
         '(connector acquire); years the connector refused are absent, 2020 has no standard ACS1; years are the locally bound years.',
         years=acs_years)
    populations = json.loads(P15.read_text())
    populations.update(catalog_id='first_order_outcomes_v16',
                       inherited_population_metadata=dict(path=relative(P15), sha256=sha(P15)))
    write_once(V16, data)
    write_once(P16, populations)
    receipt = OUT / 'BUILD_RECEIPT.json'
    write_once(receipt, dict(schema='l2_yield_fixes_v16_build_v1',
        catalog_v15=dict(path=relative(V15), sha256=sha(V15)), catalog_v16=dict(path=relative(V16), sha256=sha(V16)),
        populations_v15=dict(path=relative(P15), sha256=sha(P15)), populations_v16=dict(path=relative(P16), sha256=sha(P16)),
        manifest=pointer, bound_series={'bls_laus_employment': len(laus), 'bls_ces_private_weekly_earnings': len(ces),
                                       'acs1_snap_household_receipt_rate': len(acs)},
        acs_years=acs_years, outcome_values_read=0, outcome_association_calls=0, actual_data_estimation_calls=0))
    return json.loads(receipt.read_text())


if __name__ == '__main__':
    print(json.dumps(build(), indent=2))
