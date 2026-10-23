"""Offline historical NVSS panels and explicit keyless CDC acquisition.

No policy/treatment inputs or outcome associations enter this module. National
demographic rows are never substituted for state populations. Published missing
or suppressed observations remain null, with the source token retained.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import urllib.parse

from scripts.l2_pipeline.connectors.common import (
    ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES, STATES,
    PanelData, metadata, row,
)
from scripts.l2_pipeline.connectors import outcome_v2_cdc as cdc
from scripts.l2_pipeline.connectors.outcome_v2_core import (
    PublicClient, PUBLIC_DOMAIN, file_receipt,
)
from scripts.l2_pipeline.policy_tracker_sources import scan_untrusted

ROOT = Path(__file__).resolve().parents[3] / 'data/l2_pipeline/connectors_v2/wonder'
LICENSE_URL = cdc.LICENSE_URL
DRUG_FILTERS = {'sex': 'Both Sexes', 'age_group': 'All Ages',
                'race_ethnicity': 'All Races-All Origins'}
DRUG_DEFINITION = 'Underlying cause ICD-10 X40-X44, X60-X64, X85, Y10-Y14; all intents'
SERIES = {
    sid: dict(info, connector='wonder_historical', module=__name__)
    for sid, info in cdc.SERIES.items()
    if info['dataset_id'] in {'bi63-dtpu', 'y268-sna3'}
}
for suffix, field, units in (
    ('deaths', 'deaths', 'deaths'),
    ('crude_rate', 'rate', 'deaths per 100,000 population, crude'),
    ('age_adjusted_rate', 'ageadjrate',
     'deaths per 100,000, age adjusted to 2000 US standard population'),
):
    SERIES['nchs_historical_drug_overdose_' + suffix] = dict(
        connector='wonder_historical', module=__name__, dataset_id='44rk-q6r2',
        years=[1999, 2018], units=units, frequency='annual',
        geography='state_of_residence', population='All ages, both sexes, all races and origins',
        value_field=field, required_filters=list(DRUG_FILTERS),
        population_filters=DRUG_FILTERS, cause_definition=DRUG_DEFINITION,
        source_url='https://data.cdc.gov/resource/44rk-q6r2.csv',
        definition_url='https://data.cdc.gov/api/views/44rk-q6r2.json',
        release_lag='Historical final NVSS; actual source observations end 2018',
        license=PUBLIC_DOMAIN, license_url=LICENSE_URL,
        access='public_no_account_no_key', status='ready',
        source_vintage_note='Metadata denominator text ends 2017; exported state-year records include 2018.',
    )

SUPPRESSED = {'*', '**', 'suppressed', '1-9', '-999'}
UNRELIABLE = {'unreliable'}
MISSING = {'', '---', '--', '-', '–', '—', '...', 'na', 'n/a', 'null', 'not available'}


def _number(raw, *, count=False):
    token = str(raw or '').strip()
    if token.lower() in SUPPRESSED | UNRELIABLE | MISSING:
        return None
    try:
        value = float(token.replace(',', ''))
    except ValueError as exc:
        raise ConnectorError('Invalid historical published numeric cell') from exc
    if not math.isfinite(value) or value < 0 or (count and not value.is_integer()):
        raise ConnectorError('Historical counts require nonnegative integers; rates finite nonnegative values')
    return int(value) if count else value


def _flags(token, present):
    text = str(token or '').strip().lower()
    return dict(suppressed=bool(present and text in SUPPRESSED),
                unreliable=bool(present and text in UNRELIABLE),
                published_cell_present=int(present), published_value_token=token if present else None,
                missing_reason=('absent_or_outside_published_coverage' if not present else
                                'suppressed' if text in SUPPRESSED else
                                'unreliable' if text in UNRELIABLE else
                                'missing_published_value' if text in MISSING else None))


def _source_rows(paths, required):
    for path in paths:
        with path.open(newline='', encoding='utf-8-sig') as stream:
            reader = csv.DictReader(stream)
            if not required <= set(reader.fieldnames or []):
                raise ConnectorError('Historical source schema differs from declared dataset')
            yield from reader


def _inventory(path, request, info):
    return cdc._inventory(path, request, info)


def _build(request: ConnectorRequest) -> PanelData:
    sid = request.options.get('series_id')
    if sid not in SERIES:
        raise ConnectorNotReady('Unknown historical NVSS series')
    info = SERIES[sid]
    if request.frequency != 'annual':
        raise ConnectorNotReady('Historical sources publish annual state panels')
    if request.options.get('value_field', info['value_field']) != info['value_field']:
        raise ConnectorError('Historical series and published value field disagree')
    paths = request.paths()
    cells = {}
    if info['dataset_id'] == '44rk-q6r2':
        if dict(request.filters) != DRUG_FILTERS:
            raise ConnectorNotReady('Drug historical state series require exact all-age/sex/race filters')
        required = {'state', 'year', 'sex', 'age', 'race', 'deaths', 'popul', 'rate', 'ageadjrate', 'unit'}
        for source in _source_rows(paths, required):
            if source['state'] == 'United States':
                continue
            if source['state'] not in NAMES:
                raise ConnectorError('Unknown historical residence state')
            if (source['sex'], source['age'], source['race']) != (
                    DRUG_FILTERS['sex'], DRUG_FILTERS['age_group'], DRUG_FILTERS['race_ethnicity']):
                raise ConnectorError('State drug population strata differ from declared source population')
            if source['unit'] != 'per 100,000 population':
                raise ConnectorError('Drug rate denominator unit changed')
            try:
                year = int(source['year'])
            except ValueError as exc:
                raise ConnectorError('Invalid historical year') from exc
            if not info['years'][0] <= year <= info['years'][1]:
                raise ConnectorError('Historical observation exceeds verified source vintage')
            unit = NAMES[source['state']]
            if unit not in request.units or year not in request.years:
                continue
            key = unit, year
            if key in cells:
                raise ConnectorError('Duplicate historical state/year/population cell')
            token = source[info['value_field']]
            cells[key] = dict(value=_number(token, count=info['value_field'] == 'deaths'),
                             token=token, source_population=_number(source['popul'], count=True),
                             sex=source['sex'], age_group=source['age'], race_ethnicity=source['race'],
                             cause_definition=DRUG_DEFINITION)
        rows, metas = [], []
        for unit in request.units:
            for year in request.years:
                cell = cells.get((unit, year), {})
                flags = _flags(cell.get('token'), bool(cell))
                record = row(unit, year, cell.get('value'), info['units'], series_id=sid,
                             geography=info['geography'],
                             **{k: v for k, v in cell.items() if k not in {'value', 'token'}}, **flags)
                rows.append(record)
                metas.append(metadata(record, record['outcome_value'] is not None, **flags))
        return PanelData(tuple(rows), tuple(metas),
                         tuple(_inventory(p, request, info) for p in paths),
                         ('No interpolation, suppressed-cell imputation, or population back-calculation.',
                          'Pending cause classification may understate NJ 2009 and WV 2005/2009, per NCHS metadata.'),
                         dict(info, series_id=sid, population_filters=dict(request.filters),
                              requested_years=list(request.years), outcome_association_calls=0))

    # Reuse established CDC population/age/cause contracts and byte-receipt checks.
    base = cdc.build(request)
    dataset = info['dataset_id']
    required = ({'state', 'year', 'cause_name', info['value_field']} if dataset == 'bi63-dtpu'
                else {'state', 'year', 'age_years', info['value_field']})
    for source in _source_rows(paths, required):
        unit = NAMES.get(source['state'])
        if unit not in request.units or int(source['year']) not in request.years:
            continue
        if dataset == 'bi63-dtpu' and source['cause_name'] != request.filters['cause_name']:
            continue
        if dataset == 'y268-sna3' and source['age_years'] != request.filters['age_group'] + ' years':
            continue
        key = unit, int(source['year'])
        if key in cells:
            raise ConnectorError('Duplicate historical source cell')
        cells[key] = source[info['value_field']]
    rows = tuple(dict(r, **_flags(cells.get((r['unit'], r['year'])), (r['unit'], r['year']) in cells))
                 for r in base.rows)
    metas = tuple(dict(m, **_flags(cells.get((m['unit'], m['year'])), (m['unit'], m['year']) in cells))
                  for m in base.metadata)
    return replace(base, rows=rows, metadata=metas,
                   details=dict(base.details, connector='wonder_historical', module=__name__))


def build(request: ConnectorRequest) -> PanelData:
    from .rates import apply_scale
    return apply_scale(request, _build(request))


construct = build


def acquire(destination=ROOT, client=None):
    """Three complete historical resources; one full CSV request per dataset.

    Official count(*) exports must match the downloaded CSV row counts exactly.
    Metadata and every byte export have URL/time/size/hash/license receipts.
    """
    destination = Path(destination)
    client = client or PublicClient()
    downloads, summaries = [], []
    for dataset in ('44rk-q6r2', 'bi63-dtpu', 'y268-sna3'):
        jobs = [
            (f'https://data.cdc.gov/api/views/{dataset}.json', f'historical_{dataset}_metadata.json'),
            (f'https://data.cdc.gov/resource/{dataset}.csv?' + urllib.parse.urlencode({'$limit': 1000000}),
             f'historical_{dataset}.csv'),
            (f'https://data.cdc.gov/resource/{dataset}.json?' + urllib.parse.urlencode({'$select': 'count(*) as n'}),
             f'historical_{dataset}_count.json'),
        ]
        for url, name in jobs:
            receipt = client.fetch(url, destination / 'raw' / name, PUBLIC_DOMAIN, LICENSE_URL)
            path = Path(receipt['path'])
            scan = scan_untrusted(path.read_bytes(), url)
            path.with_name(path.name + '.scan.json').write_text(json.dumps(scan, indent=2) + '\n')
            if scan['heuristic_hit_count']:
                raise ConnectorNotReady('Historical source local heuristic scan flagged suspicious assistant instructions')
            downloads.append(dict(receipt, source_check=scan))
        raw = destination / 'raw' / f'historical_{dataset}.csv'
        rows = list(_source_rows([raw], {'state', 'year'}))
        expected = int(json.loads((destination / 'raw' / f'historical_{dataset}_count.json').read_text())[0]['n'])
        if len(rows) != expected or len(rows) >= 1000000:
            raise ConnectorError('Historical CSV incomplete against official row count')
        meta = json.loads((destination / 'raw' / f'historical_{dataset}_metadata.json').read_text())
        if meta.get('licenseId') != 'USGOV_WORKS':
            raise ConnectorNotReady('Historical source license requires review')
        summaries.append(dict(dataset_id=dataset, name=meta['name'], description=meta['description'],
                              license_id=meta['licenseId'], downloaded_rows=len(rows), official_rows=expected,
                              complete_export=True, observed_years=sorted({int(r['year']) for r in rows}),
                              observed_states=sorted({r['state'] for r in rows})))
    return dict(downloads=downloads, datasets=summaries, outcome_association_calls=0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquire', action='store_true')
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args(argv)
    if args.acquire:
        manifest = acquire()
        # Preserve legacy discovery downloads as distinct provenance, not source substitution.
        for path in sorted((ROOT / 'raw').glob('historical_jx6g-fdh6*.receipt.json')):
            receipt = json.loads(path.read_text())
            scan_path = Path(receipt['path'] + '.scan.json')
            if not scan_path.exists():
                scan_path.write_text(json.dumps(scan_untrusted(Path(receipt['path']).read_bytes(), receipt['url']), indent=2) + '\n')
            manifest['downloads'].append(dict(receipt, discovery_only=True,
                                              source_check=json.loads(scan_path.read_text())))
        (ROOT / 'historical_downloads.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if args.build:
        summaries = []
        for sid, info in SERIES.items():
            filters_list = ([DRUG_FILTERS] if info['dataset_id'] == '44rk-q6r2' else
                            [{'cause_name': x} for x in cdc.CAUSES] if info['dataset_id'] == 'bi63-dtpu' else
                            [{'age_group': x} for x in info['age_groups']])
            for filters in filters_list:
                request = ConnectorRequest(
                    {'series_id': sid, 'paths': ['raw/historical_' + info['dataset_id'] + '.csv']},
                    filters, tuple(range(info['years'][0], info['years'][1] + 1)),
                    tuple(sorted(STATES.values())), 'annual', ROOT)
                panel = build(request)
                suffix = filters.get('cause_name', filters.get('age_group', 'all')).lower().replace(' ', '_').replace("'", '')
                name = f'historical_{sid}_{suffix}.json'
                payload = asdict(panel)
                (ROOT / 'parsed').mkdir(parents=True, exist_ok=True)
                (ROOT / 'parsed' / name).write_text(json.dumps(payload, indent=2) + '\n')
                summaries.append(dict(series_id=sid, filters=filters, years=info['years'],
                                      states=len(request.units), requested_cells=len(panel.rows),
                                      available_cells=sum(m['available'] for m in panel.metadata),
                                      suppressed_cells=sum(r['suppressed'] for r in panel.rows),
                                      unreliable_cells=sum(r['unreliable'] for r in panel.rows),
                                      panel_path=str(ROOT / 'parsed' / name)))
        (ROOT / 'parsed' / 'historical_coverage.json').write_text(json.dumps(
            dict(panels=summaries, outcome_association_calls=0), indent=2) + '\n')
        print(json.dumps(dict(panel_count=len(summaries), outcome_association_calls=0)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
