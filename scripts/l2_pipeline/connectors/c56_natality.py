"""Published state birth/fertility rates, with fail-closed OCR validation."""
import json, logging, re, shutil
from pathlib import Path
from .c56_core import REPO, BASE, UNITS, save
from .common import NAMES, ConnectorError
from .wonder import scan_source, write_once
from ..storage import sha

RAW=REPO/'data/l2_pipeline/connectors_v2/nchs_natality_state/raw'
ALIASES={'DkStrictofColumbia':'DC','Diatriotof Columbia':'DC','Oonn@”cut':'CT',
         'NewJetsey':'NJ','South Oarolina':'SC'}


def numeric(token, *, count=False):
    if not re.fullmatch(r'\d[\d,]*(?:\.\d+)?',token):return None
    value=float(token.replace(',',''))
    if count and not value.is_integer():return None
    return int(value) if count else value


def state_rows(text):
    names=dict(NAMES,**ALIASES,**{'United States':'US'})
    records={}
    lines=text.splitlines()
    for i in range(len(lines)-1):
        if lines[i].strip() in names and lines[i+1].lstrip().startswith('.'):
            lines[i+1]=lines[i].strip()+' '+lines[i+1].strip();lines[i]=''
        elif lines[i].strip().isalpha() and (lines[i].strip()+lines[i+1].lstrip()).split('.')[0].strip() in names:
            lines[i+1]=lines[i].strip()+lines[i+1].lstrip();lines[i]=''
    for line in lines:
        line=' '.join(line.split())
        for name,u in names.items():
            pattern='^'+r'\s*'.join(re.escape(c) for c in name if not c.isspace())+r'(?:[¹²³⁴⁵]|\d)*(?=[\s.,?]|$)'
            match=re.match(pattern,line,re.I)
            if not match:continue
            rest=re.sub(r'^\s*[¹²³⁴⁵1-5]\s+(?=\.)','',line[match.end():])
            tokens=rest.lstrip(' .,?').split()
            if tokens and re.search(r'\d',tokens[0]):
                if u in records:raise ConnectorError('Duplicate natality state in table')
                records[u]=tokens
            break
    return records


def validate_ocr(records, neighbours, year, *, sum_tolerance=0.0001, neighbour_tolerance=.15):
    """Counts, not rates, are additive. No repaired or imputed numeric cells."""
    total=records.get('US',{}).get('count')
    complete=set(records)-{'US'}==set(UNITS) and all(records[u].get('count') is not None for u in UNITS)
    observed=sum(records[u]['count'] for u in UNITS) if complete else None
    aggregate=complete and total is not None and abs(observed-total)<=max(1,total*sum_tolerance)
    flags={};good={}
    for u in UNITS:
        cell=records.get(u,{});reasons=[]
        if not aggregate:reasons.append('51_state_birth_counts_do_not_reconcile_to_US')
        if any(cell.get(k) is None for k in ['count','birth_rate','fertility_rate']):reasons.append('OCR_numeric_cell_invalid')
        comparisons=[(y,neighbours.get(y,{}).get(u)) for y in [year-1,year+1]]
        comparisons=[(y,c) for y,c in comparisons if c and all(c.get(k) is not None for k in ['count','birth_rate','fertility_rate'])]
        if not comparisons:reasons.append('neighbour_validation_unavailable')
        for y,n in comparisons:
            if any(cell.get(k) is not None and abs(cell[k]/n[k]-1)>neighbour_tolerance for k in ['count','birth_rate','fertility_rate'] if n[k]>0):
                reasons.append('neighbour_year_outlier_'+str(y))
        if not reasons:good[u]=cell
        else:flags[u]=dict(missing_reason=';'.join(reasons),source_issue='OCR_validation_failure')
    return good,flags,dict(year=year,state_count_sum_pass=bool(aggregate),
        count_sum_tolerance_relative=sum_tolerance,neighbour_tolerance_relative=neighbour_tolerance,
        states_parsed=len(set(records)-{'US'}),states_retained=len(good),states_failed=sorted(flags))


def extract(path,year):
    from pypdf import PdfReader
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    evidence=BASE/'tables'/('natality_'+str(year)+'.json')
    if evidence.exists():
        value=json.loads(evidence.read_text())
        if value['source_sha256']!=sha(path):raise ConnectorError('Natality table source changed')
        return value['records'],value['scan']
    rate=None;counts=None;pages=[];scans=[]
    for i,page in enumerate(PdfReader(path).pages,1):
        text=page.extract_text() or '';scan=scan_source(text.encode());scans.append(scan)
        if scan['suspicious_patterns']:raise ConnectorError('Suspicious natality PDF text')
        compact=' '.join(text.split())
        early=(year<=1994 and re.search(r'Table\s+(?:6|8)\.\s+Live births by race of mother',compact) and 'fertility rate' in compact.lower())
        integrated=bool(re.search(r'Number of births(?: and|,)? birth rates, fertility rates',compact))
        rate_only=bool(re.search(r'Table\s+\d+\.\s+Birth rates,? by age of mother',compact))
        count_only=bool(re.search(r'Table\s+\d+\.\s+(?:Births|Live births),? by (?:race|Hispanic|state)',compact))
        if not (early or integrated or rate_only or count_only):continue
        layout=page.extract_text(extraction_mode='layout',layout_mode_strip_rotated=False)
        layout_scan=scan_source(layout.encode())
        if layout_scan['suspicious_patterns']:raise ConnectorError('Suspicious natality layout text')
        rows=state_rows(layout)
        plain_rows=state_rows(text)
        if len(plain_rows)>len(rows):rows=plain_rows
        if not set(rows)&set(UNITS):continue
        pages.append(dict(page=i,text=layout,scan=layout_scan))
        if early:
            rate={u:dict(count=numeric(t[0],count=True),birth_rate=numeric(t[5] if year==1990 and len(t)>6 else t[-2]),
                        fertility_rate=numeric(t[6] if year==1990 and len(t)>6 else t[-1])) for u,t in rows.items() if len(t)>=3}
        elif integrated:
            rate={u:dict(count=numeric(t[0],count=True),birth_rate=numeric(t[1]),fertility_rate=numeric(t[2])) for u,t in rows.items() if len(t)>=3}
        elif rate_only:
            part={u:dict(birth_rate=numeric(t[0]),fertility_rate=numeric(t[1])) for u,t in rows.items() if len(t)>=2}
            rate=rate or {}
            if set(rate)&set(part):raise ConnectorError('Duplicate natality rate table state')
            rate.update(part)
        elif count_only:
            counts=counts or {}
            for u,t in rows.items():counts.setdefault(u,numeric(t[0],count=True))
    rate=rate or {}
    if counts:
        for u,c in rate.items():c.setdefault('count',counts.get(u))
    scan=dict(pages_scanned=len(scans),ml_detector_run=False,suspicious_patterns=[],
              method='local extraction, Unicode normalization, encoded-text heuristic check',clean_scan_proves_safety=False)
    write_once(evidence,dict(source_path=str(path.relative_to(REPO)),source_sha256=sha(path),
        year=year,records=rate,pages=pages,scan=scan))
    return rate,scan


def build():
    raw={};sources=[];checks=[];scans=[]
    for path in sorted(RAW.glob('births_final_*.pdf')):
        year=int(re.search(r'final_(\d{4})',path.name)[1]);sources.append(path)
        if year==1995:
            # C56 host inventory found no OCR executable. Never install one or
            # treat an image-only report as a zero birth year.
            scans.append(dict(year=year,ocr_engine=shutil.which('tesseract'),status='image_only_missing_no_local_OCR'))
            continue
        raw[year],scan=extract(path,year);scans.append(dict(year=year,**scan))
    values={'birth_rate':{},'fertility_rate':{}};flags={}
    for year in range(1990,2019):
        records=raw.get(year,{})
        if year<=1994:
            records,bad,check=validate_ocr(records,raw,year);checks.append(check)
            flags.update({(u,year):f for u,f in bad.items()})
        elif year==1995:
            flags.update({(u,year):dict(missing_reason='image_only_no_installed_OCR',source_issue='OCR_unavailable') for u in UNITS})
        for u,c in records.items():
            if u not in UNITS:continue
            for measure in values:
                v=c.get(measure)
                if v is not None and 0<v<200:values[measure][u,year]=v
    # Splice only locally present, receipt-bound 2019+ final reports. The legacy
    # connector remains available for later acquisition under its old identity.
    later=[]
    for y in range(2019,2025):
        matches=list(RAW.glob('*'+str(y)+'*.pdf'))
        if not matches:continue
        path=matches[0];records,scan=extract(path,y);sources.append(path);later.append(y)
        for u,c in records.items():
            if u in UNITS:
                for measure in values:
                    v=c.get(measure)
                    if v is not None:values[measure][u,y]=v
    write_once(BASE/'checks/NATALITY_VALIDATION.json',dict(ocr_years=checks,source_scans=scans,
        splice=dict(existing_series='nchs_summary_fertility_rate',later_local_years=later,
                    definition='All live births / resident women ages15-44 x1000',definition_changed_at_2019=False,
                    population_vintage_breaks=[2000,2010,2020],no_numeric_reconciliation_adjustment=True),
        downloads=[],outcome_association_calls=0,imputation=False))
    end=max([2018]+later);years=range(1990,end+1);out={}
    for measure,sid,pop,unit,construct in [
        ('birth_rate','nchs_state_birth_rate','All live births by maternal residence / total resident population','births per 1,000 residents','births'),
        ('fertility_rate','nchs_summary_fertility_rate','All live births by maternal residence / resident women ages15-44','births per 1,000 females aged 15–44','births')]:
        spec=save(sid,years,values[measure],sources,pop,'https://www.cdc.gov/nchs/products/nvsr.htm',unit,
            flags=flags,details=dict(validation='checks/NATALITY_VALIDATION.json',construct=construct,
                definition_changed_at_2019=False,existing_2019_plus_series='nchs_summary_fertility_rate'),
            limitations=['1990–1994 OCR requires national count reconciliation and neighbouring-year validation; failed cells missing.',
                         '1995 image-only report missing because no local OCR engine is installed.',
                         'Rates use report-vintage population denominators; census denominator revisions are retained.'])
        spec['construct']=construct;out[sid]=spec
    return out
