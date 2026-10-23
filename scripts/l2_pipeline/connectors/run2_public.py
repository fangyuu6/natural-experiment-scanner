"""Run2 public sources, with explicit acquisition separate from offline construction.

The original card and S1 binding are never rewritten. Plans bind downloaded bytes
and override only source paths and connector names, preserving declared outcomes.
No treatment labels, outcome associations or estimators enter acquisition.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import re
import struct
import urllib.request
import urllib.error
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, PanelData, sha
from .published import _finalize, _numeric, published_sheet

LICENSE = 'US federal government public domain; cite originating Census/BLS/CDC/EIA agency'
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'data/l2_pipeline/jobs/run2/outcomes'
ALLOWED_HOSTS = {'data.bls.gov', 'www2.census.gov', 'www.eia.gov', 'data.cdc.gov', 'www.cdc.gov'}
MONTHS = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()


def _save(path: Path, payload: Any) -> None:
    raw = (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ConnectorError('Immutable acquisition record differs: ' + str(path))
    else:
        path.write_bytes(raw)


def _safe_url(url: str) -> None:
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ConnectorError('Credential-free official HTTPS endpoint required')
    if any(re.search('key|token|password|secret|auth', k, re.I) for k in parse_qs(parsed.query)):
        raise ConnectorError('Credential-bearing download query refused')


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def acquire(url: str, path: Path, *, byte_range: tuple[int, int] | None = None,
            max_bytes: int = 200_000_000) -> dict[str, Any]:
    """Single public request, no retries, bypasses, secrets or implicit fallbacks."""
    _safe_url(url)
    path = path.resolve()
    if not path.is_relative_to(OUT.resolve()):
        raise ConnectorError('Acquisition outside owned run2 outcomes root')
    receipt = path.with_name(path.name + '.download.json')
    if path.exists():
        if not receipt.exists():
            raise ConnectorError('Existing downloaded file lacks provenance: ' + str(path))
        record = json.loads(receipt.read_text())
        if record['url'] != url or record['sha256'] != sha(path):
            raise ConnectorError('Existing source identity changed')
        return record
    headers = {'User-Agent': 'L2-public-source/1.0'}
    if byte_range:
        headers['Range'] = 'bytes=%d-%d' % byte_range
    try:
        with urllib.request.build_opener(_OfficialRedirect()).open(
                urllib.request.Request(url, headers=headers), timeout=45) as response:
            _safe_url(response.geturl())
            if 'html' in response.headers.get('Content-Type', '').lower():
                raise ConnectorNotReady('Public file endpoint returned HTML; no account/bot-check bypass attempted')
            if byte_range and response.status != 206:
                raise ConnectorNotReady('Official archive server did not support requested public byte range')
            raw = response.read(max_bytes + 1)
            final_url = response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ConnectorNotReady(f'Public file unavailable ({type(exc).__name__}: {exc}); zero retries') from exc
    if not raw or len(raw) > max_bytes or raw.lstrip().lower().startswith((b'<html', b'<!doctype')):
        raise ConnectorNotReady('Public response is empty, oversized or HTML')
    if byte_range and len(raw) != byte_range[1] - byte_range[0] + 1:
        raise ConnectorError('Public range byte count mismatch')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    record = dict(path=str(path), url=url, final_url=final_url, size_bytes=len(raw),
                  sha256=sha(path), license=LICENSE, retries=0,
                  http_range=None if byte_range is None else 'bytes=%d-%d' % byte_range)
    _save(receipt, record)
    return record


def eia_state_sales(request: ConnectorRequest) -> PanelData:
    """EIA annual total-industry MWh / customers; residential and all-sector cells."""
    field = request.options.get('value_field')
    targets = {'residential_electricity_kwh_per_customer': ('buildings', 'Residential'),
               'electricity_sales_kwh_per_customer': ('electricity', 'Total')}
    if field not in targets:
        raise ConnectorNotReady('EIA adapter preserves declared sales/customer only; solar is a separate source')
    target, sector = targets[field]
    if dict(request.filters) != {'published_target': target}:
        raise ConnectorNotReady('EIA declared population differs from selected sector')
    paths = request.paths()
    if len(paths) != 2 or {p.name for p in paths} != {'sales_annual.xlsx', 'customers_annual.xlsx'}:
        raise ConnectorNotReady('Declare the paired official EIA sales_annual.xlsx and customers_annual.xlsx files')
    cells: dict[str, dict[tuple[str, int], Any]] = {}
    for path in paths:
        _, table = published_sheet(path)
        headers = table[1]
        if headers[:3] != ['Year', 'State', 'Industry Sector Category'] or sector not in headers:
            raise ConnectorError('EIA published column layout changed')
        col = headers.index(sector)
        by = {}
        for source in table[2:]:
            year, unit, category = source[:3]
            if category != 'Total Electric Industry' or unit not in request.units or year not in request.years:
                continue
            key = unit, int(year)
            if key in by:
                raise ConnectorError('Duplicate total-industry EIA state/year')
            token = source[col]
            by[key] = '' if token is None or str(token).strip() in ('', 'NA', 'NM', '--') else _numeric(token)
        cells[path.name] = by
    values = {}
    for key in {(u, y) for u in request.units for y in request.years}:
        sales = cells['sales_annual.xlsx'].get(key, '')
        customers = cells['customers_annual.xlsx'].get(key, '')
        usable = sales != '' and customers != '' and customers > 0
        values[key] = dict(value=1000 * sales / customers if usable else '', source=';'.join(map(str, paths)),
                           counts={'denominator_positive': int(customers != '' and customers > 0)})
    panel = _finalize(request, values, paths, 'kWh per annual electricity customer',
                      ('Source sales are MWh; multiply by 1000 before dividing by customers.',
                       'All-sector classifications changed in 2003; residential sector is unchanged.',
                       'Customers are service accounts rather than distinct residents/households.'))
    return replace(panel, details={'sector': sector, 'provider_category': 'Total Electric Industry',
                   'sales_source_unit': 'MWh', 'denominator': 'annual customers',
                   'construction': 'sales_MWh * 1000 / customers'})


def parked(request: ConnectorRequest) -> PanelData:
    raise ConnectorNotReady(request.options.get('run2_park_reason', 'No verified public outcome plan'))


def apply_outcome_plan(ctx):
    """Offline StageContext -> StageContext; preserve original card.path and hash.

    A parked plan becomes a registered connector that returns the S2 PARKED
    contract. Acquisition is never triggered by S2, S3 or S4.
    """
    from .. import connectors
    connectors.ADAPTERS['run2_eia_state_sales'] = eia_state_sales
    connectors.ADAPTERS['run2_parked'] = parked
    plan_dir = ctx.repo / 'data/l2_pipeline/jobs/run2/outcomes' / ctx.card.design_id
    plan_path = plan_dir / ('OUTCOME_PLAN.v2.json' if (plan_dir/'OUTCOME_PLAN.v2.json').exists() else 'OUTCOME_PLAN.json')
    if not plan_path.exists():
        changes = {'connector': 'run2_parked', 'run2_park_reason': 'Public outcome acquisition plan not prepared for ' + ctx.card.design_id}
    else:
        plan = json.loads(plan_path.read_text())
        if plan['card_sha256'] != sha(ctx.card.path) or plan['design_id'] != ctx.card.design_id:
            raise ConnectorError('Run2 outcome plan original card binding changed')
        for source in plan.get('source_files', []):
            if sha(Path(source['path'])) != source['sha256']:
                raise ConnectorError('Run2 planned public source bytes changed')
        changes = plan['outcome_overrides']
    payload = copy.deepcopy(dict(ctx.card.payload))
    payload['outcome'].update(changes)
    payload['outcome']['run2_plan_path'] = str(plan_path)
    if plan_path.exists():
        payload['outcome']['run2_plan_sha256'] = sha(plan_path)
    return replace(ctx, card=replace(ctx.card, payload=payload))


def qcew_year(year: int, industries: tuple[str, ...]) -> tuple[list[dict], dict[str, Path]]:
    """Ordinary official HTTP ranges, complete ZIP member CRC/length validation."""
    url = f'https://data.bls.gov/cew/data/files/{year}/csv/{year}_annual_by_industry.zip'
    _safe_url(url)
    with urllib.request.build_opener(_OfficialRedirect()).open(urllib.request.Request(url, method='HEAD'), timeout=25) as response:
        total = int(response.headers['Content-Length'])
    base = OUT / 'qcew' / str(year)
    records = [acquire(url, base / 'tail.bin', byte_range=(max(0, total-65536), total-1))]
    tail = (base / 'tail.bin').read_bytes()
    end = tail.rfind(b'PK\x05\x06')
    if end < 0:
        raise ConnectorError('ZIP end-of-central-directory absent')
    entry = struct.unpack_from('<4s4H2IH', tail, end)
    records.append(acquire(url, base / 'central.bin', byte_range=(entry[6], entry[6]+entry[5]-1)))
    central = (base / 'central.bin').read_bytes()
    pos, found = 0, {}
    while pos < len(central):
        info = struct.unpack_from('<4s6H3I5H2I', central, pos)
        if info[0] != b'PK\x01\x02':
            raise ConnectorError('ZIP central-directory signature mismatch')
        name = central[pos+46:pos+46+info[10]].decode()
        for industry in industries:
            if f'annual {industry} ' in name or f'annual_{industry}_' in name:
                if industry in found:
                    raise ConnectorError('Ambiguous industry member in QCEW annual archive')
                found[industry] = name, info
        pos += 46 + info[10] + info[11] + info[12]
    if set(found) != set(industries):
        raise ConnectorNotReady('Historical QCEW industry member absent: ' + ','.join(sorted(set(industries)-set(found))))
    paths = {}
    for industry, (name, info) in found.items():
        offset = info[-1]
        records.append(acquire(url, base / f'{industry}_header.bin', byte_range=(offset, offset+29)))
        header = struct.unpack('<4s5H3I2H', (base/f'{industry}_header.bin').read_bytes())
        if header[0] != b'PK\x03\x04':
            raise ConnectorError('ZIP member header signature mismatch')
        start = offset+30+header[-2]+header[-1]
        records.append(acquire(url, base/f'{industry}_member.bin', byte_range=(start, start+info[8]-1)))
        compressed = (base/f'{industry}_member.bin').read_bytes()
        raw = compressed if info[4] == 0 else zlib.decompress(compressed, -15) if info[4] == 8 else None
        if raw is None or len(raw) != info[9] or zlib.crc32(raw)&0xffffffff != info[7]:
            raise ConnectorError('ZIP member length/CRC validation failed')
        path = base/f'{industry}.csv'
        if path.exists() and path.read_bytes() != raw:
            raise ConnectorError('Derived historical QCEW source changed')
        path.write_bytes(raw)
        _save(base/f'{industry}.extraction.json', {'archive_url':url, 'archive_size_bytes':total,
              'member':name, 'crc32':info[7], 'derived_sha256':sha(path), 'derived_size_bytes':len(raw),
              'whole_archive_downloaded':False, 'downloads':[r['path'] for r in records]})
        paths[industry] = path
    return records, paths


def _plan(card_path: Path, overrides: dict, sources: list[dict], status='READY', reason='') -> dict:
    card = json.loads(card_path.read_text())
    payload = {'design_id':card['design_id'], 'card_path':str(card_path.resolve()),
               'card_sha256':sha(card_path), 'status':status, 'reason':reason,
               'outcome_overrides':overrides, 'source_files':sources,
               'declared_outcome_preserved':True, 'outcome_association_calls':0,
               'population_filters_preserved':True}
    _save(OUT/card['design_id']/'OUTCOME_PLAN.json', payload)
    return payload


def prepare(design_id: str | None = None, family: str | None = None) -> dict:
    """Explicit CLI acquisition: prepare only existing S1-passing cards by default."""
    cards = []
    for path in sorted((ROOT/'data/l2_pipeline/cards/run2').glob('*.json')):
        card = json.loads(path.read_text())
        if design_id and card['design_id'] != design_id:
            continue
        if not (ROOT/'data/l2_pipeline/jobs/run2/work'/card['design_id']/'v1/TREATMENT.json').is_file():
            continue
        if family and card['outcome']['connector'] != family:
            continue
        cards.append((path, card))
    plans, downloads, failures = [], [], {}
    groups = {name:[(p,c) for p,c in cards if c['outcome']['connector'] == name]
              for name in {'qcew','cps','eia','cdc','cdc_public_files','fbi'}}
    if groups['eia']:
        try:
            sources = [acquire('https://www.eia.gov/electricity/data/state/'+name, OUT/name)
                       for name in ('sales_annual.xlsx','customers_annual.xlsx')]
            downloads += sources
            for path,card in groups['eia']:
                plans.append(_plan(path, {'connector':'run2_eia_state_sales', 'paths':[s['path'] for s in sources],
                    'license':LICENSE, 'source_sha256':{s['path']:s['sha256'] for s in sources},
                    'source_urls':{s['path']:s['url'] for s in sources}}, sources))
        except Exception as exc:
            failures['eia'] = str(exc)
    if groups['qcew']:
        industries = tuple(sorted({c['outcome']['population_filters']['industry'] for _,c in groups['qcew']}))
        years = sorted({y for _,c in groups['qcew'] for y in range(*[c['outcome']['years'][0], c['outcome']['years'][1]+1])})
        all_paths = {}
        def year_work(year):
            needed=tuple(i for i in industries if any(c['outcome']['population_filters']['industry']==i and c['outcome']['years'][0]<=year<=c['outcome']['years'][1] for _,c in groups['qcew']))
            if year < 2014:
                return year, qcew_year(year,needed)
            sources=[];paths={}
            for i in needed:
                p=OUT/'qcew'/str(year)/(i+'.csv')
                sources.append(acquire(f'https://data.bls.gov/cew/data/api/{year}/a/industry/{i}.csv',p));paths[i]=p
            return year,(sources,paths)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures={y:pool.submit(year_work,y) for y in years}
            for year,future in futures.items():
                try:
                    _,(items,paths)=future.result();downloads+=items
                    for industry,path in paths.items():all_paths[year,industry]=path
                except Exception as exc:failures['qcew_'+str(year)]=str(exc)
        for path,card in groups['qcew']:
            lo,hi=card['outcome']['years'];industry=card['outcome']['population_filters']['industry']
            missing=[y for y in range(lo,hi+1) if (y,industry) not in all_paths]
            if missing:
                reason='Declared QCEW source years unavailable: '+','.join(map(str,missing))+'; '+ '; '.join(failures.get('qcew_'+str(y),'') for y in missing)
                plans.append(_plan(path,{'connector':'run2_parked','run2_park_reason':reason},[], 'PARKED',reason));continue
            sources=[dict(path=str(all_paths[y,industry]),sha256=sha(all_paths[y,industry]),size_bytes=all_paths[y,industry].stat().st_size,
                    url=f'https://data.bls.gov/cew/data/files/{y}/csv/{y}_annual_by_industry.zip' if y<2014 else f'https://data.bls.gov/cew/data/api/{y}/a/industry/{industry}.csv',license=LICENSE) for y in range(lo,hi+1)]
            plans.append(_plan(path,{'paths':[s['path'] for s in sources],'license':LICENSE,
                'source_sha256':{s['path']:s['sha256'] for s in sources},'source_urls':{s['path']:s['url'] for s in sources}},sources))
    if groups['cps']:
        from .cps import parse_month, population
        dates=sorted({(y,m) for _,c in groups['cps'] for y in range(c['outcome']['years'][0],c['outcome']['years'][1]+1) for m in range(1,13)})
        paths={}
        def month_work(date):
            y,m=date;month=MONTHS[m-1];p=OUT/'cps'/f'{y}_{month}.zip'
            return date,acquire(f'https://www2.census.gov/programs-surveys/cps/datasets/{y}/basic/{month}{str(y)[2:]}pub.zip',p)
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures={d:pool.submit(month_work,d) for d in dates}
            for date,future in futures.items():
                try:
                    _,record=future.result();downloads.append(record);paths[date]=Path(record['path'])
                except Exception as exc:failures[f'cps_{date[0]}_{date[1]}']=str(exc)
        for path,card in groups['cps']:
            lo,hi=card['outcome']['years'];needed=[d for d in dates if lo<=d[0]<=hi];missing=[d for d in needed if d not in paths]
            if missing:
                reason='CPS annual employment requires all 12 declared months; unavailable '+repr(missing)+'; '+ '; '.join(failures[f'cps_{d[0]}_{d[1]}'] for d in missing)
                plans.append(_plan(path,{'connector':'run2_parked','run2_park_reason':reason},[],'PARKED',reason));continue
            sources=[next(r for r in downloads if r['path']==str(paths[d])) for d in needed]
            plans.append(_plan(path,{'paths':[s['path'] for s in sources],'license':LICENSE,
                'source_sha256':{s['path']:s['sha256'] for s in sources},'source_urls':{s['path']:s['url'] for s in sources}},sources))
    for path,card in groups['cdc']:
        source=ROOT/'data/l2_batch2/raw/cdc/drug_poisoning_1999_2016.csv'
        # Existing immutable official all-drug final public table. Do not splice
        # newer provisional series or change outcome/time window to gain readiness.
        if card['outcome']['years'][1]>2016:
            reason='Declared final all-drug age-adjusted state panel requires 1999-2021; archived official NCHS table ends 2016. Newer public dashboard begins 2019 and mixes trailing-month/provisional rows; no verified same-definition final 2017-2021 extension has been built. Supply certified final annual state rates before reopening.'
            plans.append(_plan(path,{'connector':'run2_parked','run2_park_reason':reason},[], 'PARKED',reason))
        else:
            s=dict(path=str(source),url='https://data.cdc.gov/resource/xbxb-epbu.csv?$limit=50000',sha256=sha(source),size_bytes=source.stat().st_size,license=LICENSE)
            plans.append(_plan(path,{'paths':[str(source)],'license':LICENSE,'source_sha256':{str(source):sha(source)},'source_urls':{str(source):s['url']}},[s]))
    for path,card in groups['cdc_public_files']:
        reason='Declared state bicycle-injury death rate spans 1989-2007; public NVSS microdata remove state identifiers from 2005 onward and ICD-9 to ICD-10 changes in 1999 require an explicit bicycle-code bridge and same age/denominator definition. WISQARS query-derived rates need a reproducible public export and suppression rules; no verified free state-year file for the exact declared endpoint has been supplied. Do not replace bicycle deaths with all-injury deaths.'
        plans.append(_plan(path,{'connector':'run2_parked','run2_park_reason':reason},[], 'PARKED',reason))
    for path,card in groups['fbi']:
        reason='Exact violent-crime-rate endpoint is potentially available in free FBI UCR archived state tables, but no certified parser, fixed reporting-coverage denominator, offense-definition bridge and source vintage have been supplied for declared 2000-2004. Do not treat missing agency reports as zero offenses; supply verified public state annual files and coverage rules before reopening.'
        plans.append(_plan(path,{'connector':'run2_parked','run2_park_reason':reason},[], 'PARKED',reason))
    # Every HTTP file/range is independently inventoried, including downloads
    # retained from a partly failed acquisition. Derived CSVs are labeled above.
    manifest=[]
    for receipt in sorted(OUT.rglob('*.download.json')):
        manifest.append(json.loads(receipt.read_text()))
    (OUT/'DOWNLOADS.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    all_plans=[json.loads((p.with_name('OUTCOME_PLAN.v2.json') if p.with_name('OUTCOME_PLAN.v2.json').exists() else p).read_text()) for p in sorted(OUT.glob('*/OUTCOME_PLAN.json'))]
    report={'designs':all_plans,'downloads':len(manifest),'downloaded_bytes':sum(r['size_bytes'] for r in manifest),
            'failures':failures,'outcome_association_calls':0,'download_manifest':str(OUT/'DOWNLOADS.json')}
    (OUT/'PREPARATION_REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--design-id');parser.add_argument('--family',choices=('eia','qcew','cps','cdc','cdc_public_files','fbi'))
    args=parser.parse_args();report=prepare(args.design_id,args.family)
    print(json.dumps({'designs':len(report['designs']),'downloads':report['downloads'],'bytes':report['downloaded_bytes'],'failures':report['failures']}))

if __name__=='__main__':main()
