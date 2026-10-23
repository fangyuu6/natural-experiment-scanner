"""Census published fiscal totals in nominal dollars per resident; outcome only."""
import csv, io, json, zipfile
from openpyxl import load_workbook
from .c59_core import REPO, BASE, UNITS, save
from .common import STATES, ConnectorError
from .c56_eia import number
ROOT=REPO/'data/l2_pipeline/connectors_v2'
POP=ROOT/'eia_state_electricity/raw/SEDS.zip'


def population():
    # Read published resident population without mutating inherited C56 artifacts.
    out={}
    with zipfile.ZipFile(POP) as z,z.open('SEDS.txt') as stream:
        for line in stream:
            r=json.loads(line);parts=r.get('series_id','').split('.')
            if len(parts)!=4 or parts[1]!='TPOPP' or parts[2] not in UNITS or parts[3]!='A':continue
            if r['units']!='Thousand':raise ConnectorError('SEDS denominator units changed')
            for y,v in r['data']:
                n=number(v)
                if n is not None and n>0:out[parts[2],int(y)]=1000*n
    return out


def per_capita(values,pop,multiplier=1000):
    return {k:multiplier*v/pop[k] for k,v in values.items() if k in pop and pop[k]>0}


def stc():
    path=ROOT/'census_stc/raw/STC-Historical-DB.xlsx'
    w=load_workbook(path,read_only=True,data_only=True);it=w.active.values
    header=next(it);labels=next(it)
    codes={'C105':('total_tax_revenue','total_taxes'),'T40':('individual_income_tax_revenue','individual_income_tax'),
           'T41':('corporate_income_tax_revenue','corporate_income_tax'),'T50':('death_and_gift_tax_revenue','estate_and_gift_tax'),
           'T09':('general_sales_tax_revenue','general_sales_tax')}
    out={k:{} for k in codes};missing={k:{} for k in codes}
    for r in it:
        if not isinstance(r[0],int) or not isinstance(r[2],str):continue
        state=r[2].split()[0] # Census Government state codes are NOT FIPS; use explicit abbreviation.
        if state not in UNITS:continue
        k=state,r[0]
        for code in codes:
            n=number(r[header.index(code)])
            if n is not None:
                if k in out[code]:raise ConnectorError('Duplicate STC state-year')
                out[code][k]=n
            else:missing[code][k]={'missing_reason':'publisher_nonnumeric_'+str(r[header.index(code)])}
    w.close();return codes,out,missing,path


def asfin():
    path=ROOT/'census_asfin/raw/GS00STATEFIN01.zip'
    # Exact publisher aggregates avoid reconstructing functional totals from overlapping components.
    codes={'SF0132':('total_state_expenditure','total_expenditure'),
           'SF0133':('general_state_expenditure','general_expenditure'),
           'SF0177':('state_current_operations_expenditure','direct_current_operations'),
           'SF0178':('state_capital_outlay','direct_capital_outlay'),
           'SF0204':('state_direct_elementary_secondary_education_expenditure','direct_elementary_secondary_education')}
    out={k:{} for k in codes};labels={}
    with zipfile.ZipFile(path) as z:
        fields=z.read('GS00STATEFIN01_FIELDS.txt').decode()
        if 'Amount ($1,000)' not in fields:raise ConnectorError('ASFIN source units changed')
        for r in csv.DictReader(io.StringIO(z.read('GS00STATEFIN01.dat').decode()),delimiter='|'):
            code=r['AGG_DESC'];state=STATES.get(r['ST'])
            if code not in codes or not state or r['GOVTYPE']!='002':continue
            if r['AMOUNT_F'].strip():continue
            n=number(r['AMOUNT']);k=state,int(r['#YEAR'])
            if n is not None:
                if k in out[code]:raise ConnectorError('Duplicate ASFIN aggregate')
                out[code][k]=n
            labels[code]=r['AGG_DESC_LABEL']
    return codes,out,labels,path


def build():
    pop=population();out={};codes,values,flags,path=stc()
    for code,(construct,label) in codes.items():
        sid='census_stc_'+label+'_per_capita';ys=range(1960,2026)
        f=dict(flags[code]);f.update({k:{'missing_reason':'population_denominator_absent','denominator_available':False} for k in values[code] if k not in pop})
        spec=save(sid,ys,per_capita(values[code],pop),[path,POP],
            'State government tax collections divided by all resident population; fiscal year ending in labeled year',
            'https://www.census.gov/programs-surveys/stc.html','nominal USD per resident',filters={'tax_code':code},flags=f,
            details={'source_code':code,'source_amount_units':'thousands USD','time_basis':'state fiscal year ending in label year','population':'SEDS TPOPP thousands x 1000'},
            limitations=['Nominal dollars, no inflation adjustment. Fiscal years differ across states; denominator is same calendar-year resident population.',
                         'Non-numeric X and historical missing cells stay missing; no interpolation. 2025 denominator absent in pinned SEDS.'])
        spec.update(construct=construct,population_metadata={'ages':[0,120],'restrictions':{},'population_unit':'state_fiscal_measure','aliases':[construct,construct+'_per_capita']});out[sid]=spec
    codes,values,labels,path=asfin()
    for code,(construct,label) in codes.items():
        sid='census_asfin_'+label+'_per_capita'
        spec=save(sid,range(2012,2025),per_capita(values[code],pop),[path,POP],
            labels[code]+'; state governments; fiscal year divided by resident population',
            'https://www.census.gov/programs-surveys/state.html','nominal USD per resident',filters={'aggregate_code':code},
            details={'source_label':labels[code],'source_amount_units':'thousands USD','time_basis':'state fiscal year ending in label year'},
            limitations=['Direct expenditure categories exclude intergovernmental payments; total/general expenditure include them.',
                         'Labeled current Census aggregate file covers 2012–2024. Older unlabeled layouts were not silently stitched. Fiscal/calendar population timing differs; nominal dollars.'])
        spec.update(construct=construct,population_metadata={'ages':[0,120],'restrictions':{},'population_unit':'state_fiscal_measure','aliases':[construct,construct+'_per_capita']});out[sid]=spec
    return out
