"""Person-level FARS subpopulations1975–2024; exact Census age rates."""
import csv,json,re,zipfile
from concurrent.futures import ProcessPoolExecutor
from .common import ConnectorError,STATES,NAMES,row,metadata
from .fars import _int,_state
from .c35_fars import historical_rows,RACE_SEX_1970,parse_pe19,parse_counties,POP,HIST
from .c21_fars import BASE as FARS_BASE,EXISTING,MANUAL,MANUAL_URL
from .c46_core import REPO,BASE,save_panel,source_record
from .c46_public import total_population,UNITS
from .wonder import write_once
SIDS={'driver15_20':'fars_driver_fatality_rate_age15_20','driver16_17':'fars_driver_fatality_rate_age16_17',
      'motorcyclist':'fars_motorcyclist_fatality_rate','child0_4':'fars_child_occupant_fatality_rate_age0_4','pedalcyclist':'fars_pedalcyclist_fatality_rate_all_ages'}


def motorcycle_codes(year):
    return {15,16,17,18} if year<=1981 else {20,21,27,28,29} if year<=1990 else set(range(80,90))


def classify(role,age,body,year):
    occupants={1,2,9} if year<=1981 else {1,2,3,9};cyclists={4} if year<=1981 else {6,7}
    unknown=age in ({99} if year<=2008 else {999} if year==2009 else {998,999})
    if not unknown and not 0<=age<=(97 if year<=2008 else 120):raise ConnectorError('FARS unsupported known age')
    return dict(driver15_20=role==1 and not unknown and 15<=age<=20,
        driver16_17=role==1 and not unknown and 16<=age<=17,
        motorcyclist=role in {1,2} and body in motorcycle_codes(year),
        child0_4=role in occupants and not unknown and 0<=age<=4,
        pedalcyclist=role in cyclists),dict(driver15_20=role==1 and unknown,driver16_17=role==1 and unknown,
        child0_4=role in occupants and unknown,motorcyclist=False,pedalcyclist=False)


def parse_archive(path,year):
    counts={u:{k:0 for k in SIDS} for u in UNITS};unknown={u:{k:False for k in SIDS} for u in UNITS}
    crashes={};persons=set();year_unknown=set();body_unknown=set()
    with zipfile.ZipFile(path) as z:
        for cell in historical_rows(z,'accident.csv',{'STATE','ST_CASE','YEAR','FATALS'}):
            u=_state(cell['STATE']);key=u,_int(cell['ST_CASE'],'ST_CASE');yy=_int(cell['YEAR'],'YEAR')
            if key in crashes or (yy not in {year,year%100} and not (year<=1998 and yy==99)):raise ConnectorError('FARS crash duplicate/year')
            fatalities=_int(cell['FATALS'],'FATALS')
            if not 0<=fatalities<=99:raise ConnectorError('FARS fatality census invalid')
            crashes[key]=fatalities
            if yy==99 and year<=1998:year_unknown.add(u)
        if {u for u,c in crashes}!=set(UNITS):raise ConnectorError('FARS jurisdiction missing; cannot zero-fill')
        actual={k:0 for k in crashes}
        required={'STATE','ST_CASE','VEH_NO','PER_NO','INJ_SEV','PER_TYP','AGE'}
        for cell in historical_rows(z,'person.csv',required):
            u=_state(cell['STATE']);key=u,_int(cell['ST_CASE'],'ST_CASE');aux=cell.get('N_MOT_NO','0')
            pk=(*key,_int(cell['VEH_NO'],'VEH_NO'),_int(cell['PER_NO'],'PER_NO'),None if aux=='' else _int(aux,'N_MOT_NO'))
            if key not in crashes or pk in persons:raise ConnectorError('FARS person linkage duplicate/missing')
            persons.add(pk)
            if _int(cell['INJ_SEV'],'INJ_SEV')!=4:continue
            actual[key]+=1;role=_int(cell['PER_TYP'],'PER_TYP');age=_int(cell['AGE'],'AGE')
            if 'BODY_TYP' not in cell:raise ConnectorError('FARS BODY_TYP missing')
            body=_int(cell['BODY_TYP'],'BODY_TYP') if cell['BODY_TYP'] else None
            if role in {1,2} and (body in {None,99,999} or (year>=2010 and body==98)):body_unknown.add(u)
            selected,age_unknown=classify(role,age,body,year)
            for k in SIDS:counts[u][k]+=int(selected[k]);unknown[u][k]|=age_unknown[k]
        if actual!=crashes:raise ConnectorError('Person deaths do not reproduce every accident FATALS')
    return dict(counts=counts,unknown_age=unknown,unknown_year=sorted(year_unknown),unknown_body=sorted(body_unknown),crashes=len(crashes))


def age_denominators():
    values={k:{} for k in ('driver15_20','driver16_17','child0_4')};totals={};inv=[]
    p=POP/'pe-19.csv';t,_=parse_pe19(p);totals.update(t);inv.append(source_record(p))
    for r in csv.reader(p.open(encoding='utf-8-sig',newline='')):
        if not r or not r[0].isdigit():continue
        u=STATES[r[1].zfill(2)];y=int(r[0]);key=u,y
        values['child0_4'][key]=values['child0_4'].get(key,0)+_int(r[4].replace(',',''),'population')
    p=POP/'s5yr8090.txt';inv.append(source_record(p));u=None;selected={};tot={}
    for line in p.read_text().splitlines():
        if line.strip() in NAMES:u=NAMES[line.strip()]
        m=re.match(r'^\s*(Total|Under\s+5 years)\s+((?:\d+\s*)+)$',line)
        if not u or not m:continue
        vals=list(map(int,m[2].split()))
        if len(vals)!=12:raise ConnectorError('Census1980s date column differs')
        target=tot if m[1]=='Total' else selected;target.setdefault(u,vals[1:11])
    if set(tot)!=set(UNITS) or set(selected)!=set(UNITS):raise ConnectorError('Census1980s incomplete roster')
    for u in UNITS:
        for i,y in enumerate(range(1980,1990)):totals[u,y]=tot[u][i];values['child0_4'][u,y]=selected[u][i]
    for y in range(1980,1990):
        p=POP/f'stiag7{y%100}.txt';inv.append(source_record(p));seen=set()
        for line in p.read_text().splitlines():
            if not re.match(r'^\d\d\s+[A-Z][A-Z]\s+\d',line):continue
            fips,u,age,both,male,female=line.split();age=int(age);v=int(both);key=u,age
            if STATES.get(fips)!=u or key in seen or v!=int(male)+int(female):raise ConnectorError('Census single age duplicate/sex sum')
            seen.add(key)
            for k,ages in [('driver15_20',range(15,21)),('driver16_17',range(16,18))]:
                if age in ages:values[k][u,y]=values[k].get((u,y),0)+v
        if seen!={(u,a) for u in UNITS for a in range(86)}:raise ConnectorError('Census single age census incomplete')
    for y in range(1990,2000):
        p=POP/f'stch-icen{y}.txt';inv.append(source_record(p));seen=set();counties=set()
        for line in p.open():
            yy,geo,band,race,origin,pop=line.split();codes=int(band),int(race),int(origin);key=geo,*codes
            if int(yy)!=y%100 or geo[:2] not in STATES or geo[2:]=='000' or key in seen or not(0<=codes[0]<=18 and 1<=codes[1]<=8 and codes[2] in {1,2}):raise ConnectorError('Census1990s exclusive strata differs')
            seen.add(key);counties.add(geo);u=STATES[geo[:2]];v=_int(pop,'population');totals[u,y]=totals.get((u,y),0)+v
            if codes[0] in {0,1}:values['child0_4'][u,y]=values['child0_4'].get((u,y),0)+v
        if len(counties)!=3141 or len(seen)!=len(counties)*304:raise ConnectorError('Census1990s missing strata')
    for filename,first,last in [('st-est00int-agesex.csv',2000,2009),('SC-EST2020-ALLDATA6.csv',2010,2019),('sc-est2025-alldata6.csv',2020,2024)]:
        p=FARS_BASE/'raw'/filename;inv.append(source_record(p));seen={k:set() for k in values}
        for r in csv.DictReader(p.open(encoding='utf-8-sig',newline='')):
            fips=r['STATE'].zfill(2)
            if fips not in STATES or r['SEX']!='0':continue
            race=r.get('RACE','0')
            if 'RACE' in r and (r['ORIGIN']!='0' or race not in {'1','2','3','4','5','6'}):continue
            age=int(r['AGE']);u=STATES[fips]
            for k,ages in [('driver15_20',range(15,21)),('driver16_17',range(16,18)),('child0_4',range(5))]:
                if age not in ages:continue
                key=u,age,race
                if key in seen[k]:raise ConnectorError('Census age/race duplicate')
                seen[k].add(key)
                for y in range(first,last+1):values[k][u,y]=values[k].get((u,y),0)+_int(r['POPESTIMATE'+str(y)],'population')
        for k,n in [('driver15_20',6),('driver16_17',2),('child0_4',5)]:
            if len(seen[k])!=51*n*(1 if first==2000 else 6):raise ConnectorError('Census exact age strata missing')
    latest,latestinv=total_population(1999,2024);totals.update(latest);inv.extend(latestinv)
    return values,totals,inv


def build():
    denominators,total,popinv=age_denominators();paths=[(HIST if y<=1998 else FARS_BASE/'raw' if y==2024 else EXISTING)/f'FARS{y}NationalCSV.zip' for y in range(1975,2025)]
    inv=[source_record(p) for p in paths]+popinv+[source_record(MANUAL)]
    with ProcessPoolExecutor(max_workers=4) as pool:parsed=dict(zip(range(1975,2025),pool.map(parse_archive,paths,range(1975,2025))))
    specs={}
    for k,sid in SIDS.items():
        rows=[];meta=[];agegroup={'driver15_20':[15,20],'driver16_17':[16,17],'child0_4':[0,4]}.get(k)
        filters={'person_type':k,'population':'traffic_fatalities'}
        if agegroup:filters.update(age_min=agegroup[0],age_max=agegroup[1])
        for u in UNITS:
            for y in range(1975,2025):
                count=parsed[y]['counts'][u][k];den=(denominators[k] if agegroup else total).get((u,y));unknown=parsed[y]['unknown_age'][u][k]
                body_unknown=k=='motorcyclist' and u in parsed[y]['unknown_body'];value=count/den*100000 if den else None
                r=row(u,y,value,'deaths per 100,000 '+(f'residents aged{agegroup[0]}–{agegroup[1]}' if agegroup else 'residents'),outcome_scale='rate',fatality_count=count,population_denominator=den);rows.append(r)
                m=metadata(r,value is not None,linkage_certified=not unknown and not body_unknown,
                    suppressed=0,denominator_available=int(den is not None),unknown_age_present=unknown,unknown_year_present=u in parsed[y]['unknown_year'],
                    missing_reason='exact_age_denominator_unavailable' if den is None else '',
                    source_issue='unknown_motor_vehicle_body_type' if body_unknown else '',
                    series_break=';'.join(v for c,v in [(y==1982,'person_role_body_type_recoding'),(y==1991 and k=='motorcyclist','motorcycle_body_recoding'),(y==2017 and k=='motorcyclist','motorcycle_subtype_recoding'),(y in {1999,2000,2010,2020},'Census_population_vintage_boundary')] if c))
                meta.append(m)
        pop=f'Crash-state {k}; Census resident denominator '+(f'ages{agegroup[0]}–{agegroup[1]}' if agegroup else 'all ages')
        specs[sid]=save_panel(sid,filters,rows,meta,inv,[
            'Every annual crash FATALS reconciled to fatal persons; genuine subgroup zero allowed only within complete national crash census.',
            'Youth15–20 and16–17 exact annual Census denominators absent1975–79 and1990–99; no age-bin approximations.',
            'Unknown relevant ages/body types flagged and linkage uncertified; no imputation. Crash-state numerators and resident denominators have different geographic concepts.',
            'Broad child0–4 occupant series includes motorcycle and unknown occupant types; from1982 includes not-in-transport occupants. Child-restraint exposure still needs separate vehicle/eligibility audit.',
            'Motorcyclists are in-transport operators/passengers; historical code sets pinned. Resident denominator does not measure riders or exposure mileage.'],
            dict(all_crashes_reconciled=True,motorcycle_codes={'1975–81':[15,16,17,18],'1982–90':[20,21,27,28,29],'1991+':list(range(80,90))},raw_years=list(range(1975,2025))),pop,
            'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars',requested_year_range=[1975,2024],legal_certification_granted=False)
    write_once(BASE/'checks/FARS_RECONCILIATION.json',dict(years={str(y):dict(crashes=parsed[y]['crashes'],every_crash_reconciled=True) for y in parsed},outcome_association_calls=0))
    return specs
