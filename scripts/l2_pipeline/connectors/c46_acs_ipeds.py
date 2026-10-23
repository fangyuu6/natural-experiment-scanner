"""Offline published Census/IPEDS cells; exact universes and pinned vintages."""
import csv,io,re,zipfile
from .common import ConnectorError,STATES
from .c46_core import REPO,source_record
from .c46_public import RAW,UNITS,number,save,total_population


def table_cell(source,table,n,kind='E'):
    keys=(f'{table}_{kind}{n:03}',f'{table}_{n:03}{kind}')
    matching=[k for k in keys if k in source]
    if len(matching)!=1:raise ConnectorError('ACS published cell header differs')
    return number(source[matching[0]])


def parse_acs_year(base,y,table,cells):
    inv=[];out={}
    if y>=2018:
        p=base/'tablebased'/f'acsdt1y{y}-{table.lower()}.dat';inv.append(source_record(p))
        with p.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f,delimiter='|'):
                geo=r.get('GEO_ID',r.get('#GEO_ID',''));m=re.fullmatch(r'0400000US(\d\d)',geo)
                if not m or m[1] not in STATES:continue
                u=STATES[m[1]]
                if u in out:raise ConnectorError('ACS duplicate state')
                out[u]={kind:{n:table_cell(r,table,n,kind) for n in cells} for kind in ('E','M')}
    else:
        base=base/'seq'/str(y);p=base/f'sequence_table_lookup_{y}.txt';inv.append(source_record(p))
        lookup=[{({'position':'Start Position','seq':'Sequence Number'}.get(k,k)):v for k,v in r.items()} for r in csv.DictReader(p.open(encoding='cp1252',newline=''))]
        match=[r for r in lookup if r['Table ID']==table and r['Start Position'].strip().isdigit()]
        if len(match)!=1:raise ConnectorError('ACS exact table/sequence mapping unavailable')
        seq=int(match[0]['Sequence Number']);start=int(match[0]['Start Position'])
        for fips,u in STATES.items():
            g=base/f'g{y}1{u.lower()}.csv';zpath=base/f'{y}1{u.lower()}{seq:04}000.zip';inv.extend([source_record(g),source_record(zpath)])
            geos=[r for r in csv.reader(g.open(encoding='cp1252',newline='')) if r[2:4]==['040','00'] and r[9]==fips]
            if len(geos)!=1:raise ConnectorError('ACS geo state/component differs')
            log=geos[0][4];selected={}
            with zipfile.ZipFile(zpath) as z:
                for kind in ('E','M'):
                    names=[n for n in z.namelist() if n.lower().startswith(kind.lower()) and n.lower().endswith('.txt')]
                    if len(names)!=1:raise ConnectorError('ACS estimate/MOE sequence member differs')
                    rows=[r for r in csv.reader(io.StringIO(z.read(names[0]).decode('cp1252'))) if r[5]==log]
                    if len(rows)!=1 or int(rows[0][4])!=seq:raise ConnectorError('ACS sequence/LOGRECNO differs')
                    selected[kind]={n:number(rows[0][start-1+n-1]) for n in cells}
            out[u]=selected
    if not set(out)<=set(UNITS) or not out:raise ConnectorError('ACS invalid state census')
    return out,inv


def acs_formula(table,est,status=None):
    if table=='B14005':num=[12,26];den=[1]
    elif status=='with_disability':num=[4];den=[4,7,10]
    elif status=='without_disability':num=[5];den=[5,8,11]
    else:raise ConnectorError('Unknown ACS exact construction')
    if any(est.get(n) is None for n in set(num+den)):return None
    numerator=sum(est[n] for n in num);denominator=sum(est[n] for n in den)
    if denominator<=0:return None
    if numerator>denominator:raise ConnectorError('ACS numerator exceeds exact universe')
    return 100*numerator/denominator


def acs():
    base=RAW/'census_acs1/raw';specs={}
    for table,cells,statuses in [('B14005',[1,12,26],[None]),('C18120',[4,5,7,8,10,11],['with_disability','without_disability'])]:
        published={};inv=[]
        for y in range(2010,2025):
            if y==2020:continue
            values,inventory=parse_acs_year(base,y,table,cells);published[y]=values;inv.extend(inventory)
        for status in statuses:
            sid='acs1_dropout_share_age16_19' if table=='B14005' else 'acs1_employment_population_ratio_'+status+'_age18_64'
            values={(u,y):acs_formula(table,record['E'],status) for y,data in published.items() for u,record in data.items()}
            flags={(u,y):dict(missing_reason='ACS2020_no_standard_1year_release' if y==2020 else 'published_state_row_absent' if u not in published[y] else '' if values.get((u,y)) is not None else 'published_non_numeric_or_zero_denominator',
                suppressed=y!=2020 and u in published[y] and values.get((u,y)) is None,sampling_error=True) for u in UNITS for y in range(2010,2025)}
            filters={'age_min':16,'age_max':19,'measure':'not_enrolled_not_highschool_graduate'} if table=='B14005' else {'age_min':18,'age_max':64,'disability':status,'measure':'employment_population_ratio'}
            pop='Population16–19; neither enrolled nor high school graduate' if table=='B14005' else 'Civilian noninstitutionalized population18–64 '+status
            details=dict(table=table,formulas={'B14005':'100*(E012+E026)/E001','C18120_with_disability':'100*E004/(E004+E007+E010)','C18120_without_disability':'100*E005/(E005+E008+E011)'},
                published_component_moes={u+':'+str(y):record['M'] for y,data in published.items() for u,record in data.items()},derived_ratio_moe_computed=False)
            specs[sid]=save(sid,filters,range(2010,2025),values,inv,pop,'https://www2.census.gov/programs-surveys/acs/summary_file/','percent',
                ['2020 standard ACS1 unavailable; retained null grid.', 'Source estimate and component MOEs retained. Derived ratios have no independently computed MOE. Negative ACS sentinel cells remain null.'],flags,details)
    return specs


def zip_csv(path):
    with zipfile.ZipFile(path) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.csv') and not n.lower().endswith('_rv.csv')]
        if len(names)!=1:raise ConnectorError('IPEDS single component CSV required')
        raw=z.read(names[0])
        try:body=raw.decode('utf-8-sig')
        except UnicodeDecodeError:body=raw.decode('cp1252')
        for r in csv.DictReader(io.StringIO(body)):
            yield {k.strip().upper():v.strip() for k,v in r.items()}


def ipeds_year(hd,component,y,kind):
    institutions={};values={};flags={};seen=set()
    for r in zip_csv(hd):
        fips=r['FIPS'].zfill(2)
        if fips not in STATES or r.get('SECTOR')=='0':continue
        uid=r['UNITID']
        if uid in institutions:raise ConnectorError('IPEDS duplicate HD institution')
        institutions[uid]=STATES[fips]
    field=('EFTOTLT' if y>=2008 else 'EFRACE24') if kind=='enrollment' else ('CTOTALT' if y>=2008 else 'CRACE24')
    observed=set();imputed=set()
    for r in zip_csv(component):
        uid=r['UNITID']
        if uid not in institutions:continue
        if kind=='enrollment':
            if int(r['EFALEVEL'])!=1:continue
            key=uid
        else:
            if float(r['CIPCODE'])!=99 or int(r.get('MAJORNUM','1'))!=1:continue
            award=int(r['AWLEVEL'])
            if award not in set(range(1,22)):raise ConnectorError('IPEDS award-level contract unknown')
            key=uid,award
        if key in seen:raise ConnectorError('IPEDS duplicate aggregate row')
        seen.add(key);observed.add(uid);u=institutions[uid];v=number(r[field]);values.setdefault(u,0)
        if v is None:flags.setdefault(u,set()).add('published_total_missing')
        else:values[u]+=v
        flagkey=('XEFTOTLT' if y>=2008 else 'XEFRAC24') if kind=='enrollment' else ('XCTOTALT' if y>=2008 else 'XCRACE24')
        if r.get(flagkey) not in {'','R','Z'}:imputed.add(u)
    for u in UNITS:
        missing=sum(1 for uid,state in institutions.items() if state==u and uid not in observed)
        # HD includes nonparticipants/inactive schools; absent components are
        # flagged but observed institution aggregate remains explicitly partial.
        flags.setdefault(u,set())
        if u in imputed:flags[u].add('IPEDS_imputed_or_adjusted_total')
        if missing:flags[u].add('HD_institutions_without_component')
        if u not in values:flags[u].add('no_component_records')
    return values,{u:dict(source_issue=';'.join(sorted(f)),unreliable=bool(f-{'IPEDS_imputed_or_adjusted_total'}),missing_reason='no_component_records' if u not in values else '') for u,f in flags.items()},dict(hd_institutions=len(institutions),observed_institutions=len(observed),by_state={u:dict(expected=sum(s==u for s in institutions.values()),present=sum(institutions[uid]==u for uid in observed),missing=sum(s==u and uid not in observed for uid,s in institutions.items())) for u in UNITS},states_with_imputed_cells=sorted(imputed))


def ipeds():
    base=RAW/'ipeds/raw';pop,popinv=total_population(2002,2024);specs={}
    for kind,end in [('enrollment',2023),('completions',2024)]:
        years=range(2002,end+1);values={};flags={};inv=[];details={}
        for y in years:
            hd=base/f'HD{y}.zip';p=base/(f'EF{y}A.zip' if kind=='enrollment' else f'C{y}_A.zip')
            inv.extend([source_record(hd),source_record(p)])
            totals,quality,info=ipeds_year(hd,p,y,kind);details[str(y)]=info
            for u in UNITS:
                key=u,y;v=totals.get(u);values[key]=None if v is None else v/pop[key]*100000
                flags[key]=quality[u]|dict(series_break='race_columns_to_total_columns' if y==2008 else '')
        sid='ipeds_'+kind+'_per_population'
        specs[sid]=save(sid,{'measure':kind,'institution_scope':'all_HD_component_participants','geography':'institution_state'},years,values,inv+popinv,
            'IPEDS institutions in state over all resident population','https://nces.ed.gov/ipeds/datacenter/data/','students per 100,000 residents' if kind=='enrollment' else 'awards per 100,000 residents',
            ['All observed HD-linked institutions; enrollment fall total EFALEVEL1; completions CIP99, first major, sum disjoint award levels. Awards count credentials, not distinct people.', 'IPEDS imputed totals retained and flagged; absent HD components flagged as partial/unreliable; source population is institutions not residents.', 'Enrollment2024 unavailable; completions2024 available. No absent school components are filled with zero.'],flags,dict(year_details=details))
    return specs
