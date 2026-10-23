"""Masked DERIVED CMF copies and unscaled, disjoint ICD-9/ICD-10 rate splice."""
from __future__ import annotations
import csv,io,json,re
from pathlib import Path
from .common import ConnectorError, NAMES, STATES
from .wonder import verify_receipt, parse_export, _number, scan_source, write_once
from .c35_core import BASE,REPO,source_record,save_panel

RAW=REPO/'data/l2_pipeline/connectors_v2/wonder/raw'
BREAK='ICD-9_to_ICD-10_1999'
COMPARABILITY=dict(cause='Homicide (all mechanisms)',ratio=1.0020,
    direction='ICD-10 / ICD-9',source_url='https://www.cdc.gov/nchs/hus/sources-definitions/comparability-ratio.htm',
    use='metadata_only',applied=False,scope='National cause comparability; not a state, demographic or firearm-homicide adjustment')


def mask_cmf_text(text):
    """Never retain a suppressed cell's count or any associated rate in derived bytes.

    The owner permission includes all sub-ten cells, including zeros. Raw bytes
    and query receipts are never rewritten. No count/rate is inferred.
    """
    lines=text.splitlines();header=next((i for i,l in enumerate(lines) if '"State"' in l and 'Deaths' in l),None)
    if header is None:raise ConnectorError('CMF state/year header missing')
    fields=next(csv.reader([lines[header]],delimiter='\t'));masked=0;out=lines[:header+1]
    for line in lines[header+1:]:
        cells=next(csv.reader([line],delimiter='\t'))
        if len(cells)==len(fields) and cells[fields.index('State')] in NAMES:
            d=cells[fields.index('Deaths')].strip();value=_number(d,count=False)
            if value is not None and (value<0 or value!=int(value)):raise ConnectorError('Invalid CMF death count')
            small=value is not None and 0<=value<10
            suppressed=small or d.lower() in {'suppressed','*','**','1-9'}
            if suppressed:
                cells[fields.index('Deaths')]='Suppressed'
                for i,f in enumerate(fields):
                    if 'rate' in f.lower() or 'percent' in f.lower():cells[i]='Suppressed'
                masked+=int(small)
            buf=io.StringIO();csv.writer(buf,delimiter='\t',lineterminator='').writerow(cells);line=buf.getvalue()
        out.append(line)
    return '\n'.join(out)+'\n',masked


def validate_semantics(receipt, sid, first, last):
    if receipt['series_id']!=sid or not re.fullmatch(re.escape(f'{first}-{last}')+r'(?: \(All Years\))?',receipt['years_requested']):
        raise ConnectorError('WONDER source identity/window differs')
    p=receipt['population_filters']
    if p.get('sex')!='all' or p.get('age')!='all ages' or not p.get('injury_intent','').startswith('Homicide'):
        raise ConnectorError('WONDER homicide/age/sex population differs')
    firearm='firearm' in sid
    if firearm != p.get('injury_mechanism','').startswith('Firearm'):
        raise ConnectorError('WONDER homicide mechanism differs')
    if first<1999 and (receipt.get('database_code')!='D16' or receipt.get('legal_intervention_excluded') is not True):
        raise ConnectorError('CMF requires ICD-9 intent excluding legal intervention')


def verify_cmf_receipt(path):
    """D16 prints an All Years suffix; validate without changing the raw receipt."""
    from .common import sha
    from .wonder import PERMISSION
    receipt=json.loads(Path(str(path)+'.receipt.json').read_text())
    if receipt.get('file')!=path.name or receipt.get('sha256')!=sha(path) or receipt.get('size_bytes')!=path.stat().st_size:
        raise ConnectorError('CMF raw byte binding changed')
    for field in ('database','database_version','query_timestamp_utc','query_parameters','population_filters'):
        if not receipt.get(field):raise ConnectorError('CMF provenance missing: '+field)
    terms=receipt.get('terms_acceptance',{})
    restrictions=RAW.parent/terms.get('restrictions_text_file','')
    if (terms.get('accepted') is not True or not restrictions.resolve().is_relative_to(RAW.resolve())
        or not restrictions.is_file() or sha(restrictions)!=terms.get('restrictions_text_sha256')):
        raise ConnectorError('CMF terms acceptance binding changed')
    if terms.get('owner_permission_path')!=str(PERMISSION) or sha(REPO/PERMISSION)!=terms.get('owner_permission_sha256'):
        raise ConnectorError('CMF owner permission binding changed')
    printed=receipt.get('query_date_as_printed_by_wonder')
    if not printed or printed not in path.read_text():raise ConnectorError('CMF query timestamp differs')
    if not re.fullmatch(r'1979-1998(?: \(All Years\))?',receipt['years_requested']):
        raise ConnectorError('CMF pinned historical window required')
    return receipt,set(range(1979,1999))


def build():
    ratio_source=BASE/'raw/nchs_comparability_ratio.html'
    source_record(ratio_source)
    ratio_scan=scan_source(ratio_source.read_bytes())
    if ratio_scan['suspicious_patterns'] or not re.search(r'<td>Homicide</td>\s*<td>1\.0020</td>',ratio_source.read_text()):
        raise ConnectorError('Pinned NCHS all-homicide ratio metadata differs')
    catalog=json.loads((REPO/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V6.json').read_text())
    specs={};scans=[];mask_audit=[]
    for original in ('wonder_homicide_deaths','wonder_firearm_homicide_deaths'):
        sid=original.removesuffix('_deaths')+'_rate';old=catalog['series'][original]
        blocks=[(RAW/(original+'__CMF_1979-1998.tsv'),1979,1998)]
        blocks.extend((REPO/e['raw_path'],*map(int,e['years_requested'].split('-'))) for e in old['exports'])
        values={};inventory=[]
        for path,first,last in sorted(blocks,key=lambda x:x[1]):
            receipt,years=(verify_cmf_receipt(path) if first<1999 else verify_receipt(path));validate_semantics(receipt,original,first,last)
            inventory.extend([source_record(path),source_record(Path(str(path)+'.receipt.json')),
                source_record(RAW.parent/receipt['terms_acceptance']['restrictions_text_file'])])
            scan=scan_source(path.read_bytes());scans.append(dict(path=str(path.relative_to(REPO)),**scan))
            if scan['suspicious_patterns']:raise ConnectorError('Suspicious source content requires review')
            parsed_path=path
            if first<1999:
                derived,masked=mask_cmf_text(path.read_text(encoding='utf-8-sig'))
                parsed_path=BASE/'derived'/path.name;parsed_path.parent.mkdir(parents=True,exist_ok=True)
                if parsed_path.exists() and parsed_path.read_text()!=derived:raise ConnectorError('Masked CMF bytes changed')
                if not parsed_path.exists():parsed_path.write_text(derived)
                inventory.append(source_record(parsed_path))
                mask_audit.append(dict(series_id=sid,raw_sha256=source_record(path)['sha256'],
                    derived=source_record(parsed_path),masked_cells=masked,raw_unchanged=True,
                    cell_values_logged=False,count_and_all_rates_masked=True))
            deaths=parse_export(parsed_path,measure='Deaths');rates=parse_export(parsed_path,measure='Crude Rate')
            if len(deaths)!=51*(last-first+1) or any(y not in years for u,y in deaths):
                raise ConnectorError('WONDER state/year census or year block incomplete')
            if set(values)&set(deaths):raise ConnectorError('WONDER source vintages overlap')
            for key,d in deaths.items():
                rate=rates[key];suppressed=d['suppressed'] or rate['suppressed']
                # The published rate, including unreliability, binds availability.
                val=None if suppressed else rate['value']
                values[key]=dict(value=val,suppressed=suppressed,unreliable=rate['unreliable'],
                    icd_version='ICD-9' if first<1999 else 'ICD-10',database=receipt['database'],
                    database_version=receipt['database_version'],query_date=receipt['query_timestamp_utc'],
                    source_count=None if suppressed else d['value'])
        rows=[];meta=[]
        for (u,y),v in sorted(values.items()):
            available=v['value'] is not None
            common=dict(unit=u,state=u,year=y,period=str(y),frequency='annual',
                suppressed=v['suppressed'],unreliable=v['unreliable'],
                series_break=BREAK if y==1999 else None,icd_version=v['icd_version'])
            rows.append(dict(common,outcome_value=v['value'],outcome_unit='deaths per 100,000 residents',
                outcome_scale='rate',source_count=v['source_count'],database=v['database'],
                database_version=v['database_version'],query_timestamp_utc=v['query_date']))
            meta.append(dict(common,available=int(available),annual_usable=int(available),
                linkage_certified=int(available),published_cell_present=True,denominator_available=True,
                missing_reason=None if available else 'suppressed_or_published_rate_unreliable'))
        inventory.append(source_record(BASE/'raw/nchs_comparability_ratio.html'))
        details=dict(series_break=dict(year=1999,type=BREAK,rescaled=False),
            suppression_rule='All derived sub-ten counts and associated rates masked; no imputation or back-calculation',
            raw_unchanged=True,rate_basis='published crude rate, no count/population reconstruction',
            cause_codes={'ICD-9':'E965.0–E965.4' if 'firearm' in sid else 'E960–E969',
                        'ICD-10':'WONDER injury intent Homicide; mechanism '+('Firearm' if 'firearm' in sid else 'All')})
        if 'firearm' not in sid:details['nchs_comparability_ratio']=COMPARABILITY
        specs[sid]=save_panel(sid,old['population_filters'],rows,meta,inventory,
            ['ICD-9→ICD-10 break in1999 retained; no rescaling.',
             'CMF1980/1990 April1 MARS populations; other pre1999 published population vintages retained.',
             'CDC WONDER database/version/query date pinned per source block; suppressions and unreliable published rates preserved.'],
            details,old['population'],old['source_url'],series_break={'year':1999,'type':BREAK,'rescaled':False},
            **({'nchs_comparability_ratio':COMPARABILITY} if 'firearm' not in sid else {}))
    write_once(BASE/'checks/WONDER_MASK_AUDIT.json',dict(rows=mask_audit,scans=scans,outcome_association_calls=0))
    return specs

if __name__=='__main__':
    print(json.dumps({k:v['observed_coverage'] for k,v in build().items()},indent=2))
