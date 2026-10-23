"""Receipt-bound offline CDC/FBI rates; no treatment or association inputs."""
import csv,io,json,math,re,zipfile
from html.parser import HTMLParser
from .common import ConnectorError,ConnectorRequest,STATES,NAMES,row,metadata
from .c46_core import BASE,REPO,save_panel,source_record
from .rates import _census_total
from .wonder import scan_source,write_once
UNITS=tuple(sorted(STATES.values()))
RAW=REPO/'data/l2_pipeline/connectors_v2'


def number(text):
    if text is None or str(text).strip().lower() in {'','null','na','n/a','suppressed','--','*','nan'}:return None
    try:v=float(str(text).replace(',',''))
    except ValueError as exc:raise ConnectorError('Invalid published numeric cell') from exc
    if not math.isfinite(v) or v<0:return None
    return v


def total_population(first,last):
    request=ConnectorRequest({}, {}, tuple(range(first,last+1)),UNITS,'annual',REPO)
    values,inv=_census_total(request)
    return values,[source_record(i['path'])|dict(url=i['url'],license=i['license']) for i in inv]


def save(sid,filters,years,values,inventory,population,url,unit,limitations=(),flags=None,details=None):
    rows=[];meta=[];flags=flags or {}
    for u in UNITS:
        for y in years:
            key=u,y;value=values.get(key);extra=flags.get(key,{})
            suppressed=bool(extra.get('suppressed',False));usable=value is not None and not suppressed
            r=row(u,y,value,unit,outcome_scale='rate');rows.append(r)
            m=metadata(r,usable,suppressed=int(suppressed),unreliable=int(extra.get('unreliable',False)),
                missing_reason=extra.get('missing_reason','' if usable else 'published_cell_absent'),**{k:v for k,v in extra.items() if k not in {'suppressed','unreliable','missing_reason'}})
            if any(extra.get(k) for k in ('unreliable','partial_year','provisional')):m['annual_usable']=0
            meta.append(m)
    return save_panel(sid,filters,rows,meta,inventory,limitations,details or {},population,url,
        requested_year_range=[min(years),max(years)],legal_certification_granted=False)


class Tables(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.cells=None;self.parts=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.cells=[]
        if tag in {'td','th'} and self.cells is not None:self.parts=[]
    def handle_data(self,data):
        if self.parts is not None:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.parts is not None:
            self.cells.append(' '.join(''.join(self.parts).split()));self.parts=None
        if tag=='tr' and self.cells is not None:self.rows.append(self.cells);self.cells=None


def parse_opioid_html(body):
    p=Tables();p.feed(body);values={}
    for cells in p.rows:
        if len(cells)!=3 or cells[1] not in UNITS:continue
        if NAMES.get(cells[0])!=cells[1] or cells[1] in values:raise ConnectorError('Duplicate/inconsistent opioid state')
        values[cells[1]]=number(cells[2])
    if set(values)!=set(UNITS):raise ConnectorError('Opioid HTML requires all 51 jurisdictions')
    return values


def opioid():
    base=RAW/'cdc_opioid_dispensing/raw';values={};inv=[];scans=[]
    for y in range(2006,2020):
        p=base/f'state{y}.html';inv.append(source_record(p));scan=scan_source(p.read_bytes());scans.append(dict(path=str(p.relative_to(REPO)),**scan))
        if scan['suspicious_patterns']:raise ConnectorError('Suspicious source text: independent review required')
        values.update({(u,y):v for u,v in parse_opioid_html(p.read_text()).items()})
    p=base/'state-opioid-dispensing_2019_2024.json';inv.append(source_record(p));seen=set()
    for r in json.loads(p.read_text())['data']:
        y=int(r['YEAR']);u=r['STATE_ABBREV']
        if u not in UNITS:continue
        if y not in range(2019,2025) or NAMES.get(r['STATE_NAME'])!=u or (u,y) in seen:raise ConnectorError('Opioid JSON schema/duplicates')
        seen.add((u,y))
        if y!=2019:values[u,y]=number(r['opioid_dispensing_rate'])
    if seen!={(u,y) for u in UNITS for y in range(2019,2025)}:raise ConnectorError('Opioid JSON coverage differs')
    write_once(BASE/'checks/OPIOID_SOURCE_SCAN.json',dict(sources=scans,ml_detector_run=False,clean_scan_proves_safety=False))
    sid='cdc_opioid_dispensing_rate';spec=save(sid,{'population':'all_residents','measure':'opioid_dispensing'},range(2006,2025),values,inv,'All residents; IQVIA retail dispensing','https://www.cdc.gov/overdose-prevention/data-research/facts-stats/opioid-dispensing-rate-maps.html','opioid prescriptions per 100 residents',
        ['2006–2018 pharmacy location; 2019–2024 prescriber location. Analyze separately across definition break.', '2019 values use archived precise table; rounded current JSON 2019 is not used.'],
        flags={(u,y):dict(series_break='pharmacy_to_prescriber_location' if y==2019 else '') for u in UNITS for y in range(2006,2025)},
        details=dict(analysis_segments=[[2006,2018],[2019,2024]],year2019_source='archived_HTML'))
    return {sid:spec}


def brfss():
    p=RAW/'cdc_brfss/raw/brfss_smoking_ecig_binge_overall_2011_2025.csv';specs={}
    rows=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
    selectors={'brfss_current_smoking_pct':({'_RFSMOK3'},'Yes'), 'brfss_binge_drinking_pct':({'_RFBING5','_RFBING6'},'Yes'), 'brfss_current_ecigarette_pct':({'_CURECIG','_CURECI1','_CURECI2','_CURECI3'},'Current E-cigarette user')}
    for sid,(questions,response) in selectors.items():
        values={};flags={}
        for r in rows:
            if r['questionid'] not in questions or r['response']!=response or r['locationabbr'] not in UNITS:continue
            if r['break_out']!='Overall' or r['data_value_type']!='Crude Prevalence' or r['data_value_unit']!='%':raise ConnectorError('BRFSS subgroup/weighting differs')
            key=r['locationabbr'],int(r['year'])
            if key in values:raise ConnectorError('BRFSS duplicate published cell')
            v=number(r['data_value']);values[key]=v
            if v is not None and v>100:raise ConnectorError('BRFSS prevalence outside 0..100')
            footnote=r['data_value_footnote'];flags[key]=dict(suppressed=bool(footnote and v is None),suppression_reason=footnote,
                missing_reason=footnote if v is None else '',series_break='binge_variable_RFBING5_to_RFBING6' if sid=='brfss_binge_drinking_pct' and key[1]==2022 else '')
        years=range(2011,2026) if 'ecigarette' not in sid else range(2016,2026)
        spec=save(sid,{'age_min':18,'breakout':'Overall','measure':sid},years,values,[source_record(p)],'BRFSS surveyed adults age18+; weighted crude prevalence', 'https://data.cdc.gov/resource/dttw-5yxu.csv','percent',
            ['Survey weights and source suppression retained; territory rows excluded.', 'E-cigarette 2018–2020 not published; 2025 state gaps retained; binge variable changes in2022.'],flags,
            dict(published_years=sorted({y for u,y in values}),missing_calendar_years=sorted(set(years)-{y for u,y in values})))
        specs[sid]=spec
    return specs


def parse_hate(path):
    incidents={};agencies={};seen={}
    with zipfile.ZipFile(path) as z, z.open('hate_crime.csv') as f,io.TextIOWrapper(f,encoding='utf-8-sig',newline='') as handle:
        for r in csv.DictReader(handle):
            u=r['state_abbr'];y=int(r['data_year'])
            if u not in UNITS:continue
            if NAMES.get(r['state_name'])!=u or not 1991<=y<=2025:raise ConnectorError('Hate crime state/year differs')
            identity=r['incident_id'];descriptor=(u,y,r['ori'],r['incident_date'])
            if identity in seen:
                if seen[identity]!=descriptor:raise ConnectorError('Hate crime inconsistent incident identity')
                continue
            seen[identity]=descriptor;incidents.setdefault((u,y),set()).add(identity);agencies.setdefault((u,y),set()).add(r['ori'])
    return {k:len(v) for k,v in incidents.items()},agencies


def hate_crime():
    p=RAW/'fbi_hate_crime/raw/hate_crime.zip';counts,agencies=parse_hate(p);pop,inv=total_population(1991,2025)
    values={k:c/pop[k]*100000 for k,c in counts.items() if k in pop}
    flags={(u,y):dict(reporting_coverage_unknown=True,partial_year=y==2025,provisional=y==2025,
        missing_reason='No incident records; zero vs nonreporting unknown' if (u,y) not in counts else '',
        series_break='NIBRS_transition_reporting_change' if y==2021 else '') for u in UNITS for y in range(1991,2026)}
    sid='fbi_hate_crime_incident_rate'
    spec=save(sid,{'measure':'reported_hate_crime_incidents','population':'all_residents'},range(1991,2026),values,[source_record(p)]+inv,
        'FBI reported incidents by agency state over resident population','https://cde.ucr.cjis.gov/','reported incidents per 100,000 residents',
        ['Voluntary agency reporting; reporting-population denominator unavailable. Rate measures reported incidents over all residents.', 'Absent state/year incident records remain null; cannot certify zero. NIBRS2021 break; 2025 provisional/incomplete vintage.'],flags,
        dict(unique_incident_id=True,reporting_agencies_by_stateyear={u+':'+str(y):len(v) for (u,y),v in agencies.items()},reporting_coverage_certified=False))
    return {sid:spec}
