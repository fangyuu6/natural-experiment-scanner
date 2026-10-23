"""Offline EIA electricity state-year measures. No policy inputs."""
import json, math, zipfile
from .c56_core import REPO, BASE, UNITS, save
from .wonder import write_once, scan_source
from .common import ConnectorError

RAW=REPO/'data/l2_pipeline/connectors_v2/eia_state_electricity/raw'
RENEWABLE={'Geothermal','Other Biomass','Solar Thermal and Photovoltaic','Wind','Wood and Wood Derived Fuels'}


def number(v):
    if v is None or str(v).strip() in {'NA','N/A','--','*',''}:return None
    try: n=float(v)
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) else None


def workbook(name):
    from openpyxl import load_workbook
    return load_workbook(RAW/name,read_only=True,data_only=True)


def population():
    values={}; scans=[]
    with zipfile.ZipFile(RAW/'SEDS.zip') as z, z.open('SEDS.txt') as stream:
        for line in stream:
            obj=json.loads(line); parts=obj.get('series_id','').split('.')
            if len(parts)!=4 or parts[1]!='TPOPP' or parts[2] not in UNITS or parts[3]!='A':continue
            if obj['units']!='Thousand':raise ConnectorError('SEDS population units changed')
            scans.append(scan_source(line))
            for y,v in obj['data']:
                n=number(v)
                if n is not None and n>0:values[parts[2],int(y)]=1000*n
    if any(s['suspicious_patterns'] for s in scans):raise ConnectorError('Suspicious EIA source metadata')
    write_once(BASE/'checks/EIA_SOURCE_SCAN.json',dict(sources=scans,ml_detector_run=False))
    return values


def retail():
    values={};sources=[]
    for name in ['HS861_1990-2009.xlsx','HS861_2010-.xlsx']:
        sources.append(RAW/name);w=workbook(name);it=w['Total Electric Industry'].values
        top=next(it);second=next(it);units=next(it)
        res=top.index('RESIDENTIAL');total=top.index('TOTAL')
        if units[res+3]!='Cents/kWh' or units[total+1]!='Megawatthours':raise ConnectorError('EIA history schema changed')
        for r in it:
            if not isinstance(r[0],int) or r[1] not in UNITS:continue
            key=r[1],r[0]
            if key in values:raise ConnectorError('Duplicate EIA retail state-year')
            values[key]={'residential':number(r[res+3]),'all':number(r[total+3]),'sales':number(r[total+1])}
    return values,sources


def generation():
    w=workbook('annual_generation_state.xlsx');cells={}
    for r in w.active.values:
        if not isinstance(r[0],int) or r[1] not in UNITS or r[2]!='Total Electric Power Industry':continue
        key=r[1],r[0];g=cells.setdefault(key,{})
        if r[3] in g:raise ConnectorError('Duplicate EIA generation fuel')
        g[r[3]]=number(r[4])
    result={}
    for k,g in cells.items():
        if g.get('Total') is None or g['Total']<=0:continue
        # Absent fuel rows are structural zero only when EIA's displayed source
        # components reconcile to Total; missing numeric cells never become zero.
        components=[v for f,v in g.items() if f!='Total']
        if any(v is None for v in components) or abs(sum(components)-g['Total'])>max(10,abs(g['Total'])*.00001):continue
        numerator=sum(v for f,v in g.items() if f in RENEWABLE)
        if 0<=numerator<=g['Total']:result[k]=100*numerator/g['Total']
    return result


def emissions():
    w=workbook('emission_annual.xlsx');result={}
    for r in w['State Emissions'].values:
        if not isinstance(r[0],int) or r[1] not in UNITS or r[2]!='Total Electric Power Industry' or r[3]!='All Sources':continue
        key=r[1],r[0]
        if key in result:raise ConnectorError('Duplicate EIA CO2 total')
        v=number(r[4])
        if v is not None and v>=0:result[key]=v
    return result


def build():
    pop=population();ret,sources=retail();gen=generation();co2=emissions();out={}
    defs=[('eia_retail_price_all_sectors','electricity_retail_price','cents per kWh',
           {k:v['all'] for k,v in ret.items()},sources,'Electricity sales to ultimate customers; all sectors'),
          ('eia_retail_price_residential','electricity_retail_price','cents per kWh',
           {k:v['residential'] for k,v in ret.items()},sources,'Residential electricity sales to ultimate customers'),
          ('eia_retail_sales_per_capita','electricity_retail_consumption','MWh per resident',
           {k:v['sales']/pop[k] for k,v in ret.items() if v['sales'] is not None and k in pop},sources+[RAW/'SEDS.zip'],
           'All-sector electricity retail sales divided by SEDS resident population including Armed Forces'),
          ('eia_nonhydro_renewable_generation_share','nonhydro_renewable_generation','percent of net generation',gen,
           [RAW/'annual_generation_state.xlsx'],'State electricity generation; total electric power industry; non-hydro renewables'),
          ('eia_electric_power_co2_per_capita','electric_power_co2_emissions','metric tons CO2 per resident',
           {k:v/pop[k] for k,v in co2.items() if k in pop},[RAW/'emission_annual.xlsx',RAW/'SEDS.zip'],
           'Total electric power industry CO2 emissions divided by SEDS resident population including Armed Forces')]
    for sid,construct,unit,values,inv,population_text in defs:
        end=max(y for u,y in values);years=range(1990,end+1)
        spec=save(sid,years,values,inv,population_text,'https://www.eia.gov/electricity/data/state/',unit,
             details=dict(construct=construct,nonhydro_fuels=sorted(RENEWABLE),
                          co2_scope='electric_power_industry_only',population_source='SEDS.TPOPP; thousands x 1000'),
             limitations=['Electricity generation/emissions are attributed to production state; retail sales to customer state.',
                          'CO2 excludes other sectors; per-capita measures require a published SEDS population denominator.'])
        spec['construct']=construct;out[sid]=spec
    return out
