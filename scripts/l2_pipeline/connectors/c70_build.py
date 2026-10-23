"""C70 prospective public outcome panels. No policy joins, fits or associations."""
import csv, io, json, zipfile, sys
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
from . import c46_core as core, c46_public as public, c46_acs_ipeds as education, c49_ucr, rates
from .common import ConnectorError, row, metadata, STATES
from .wonder import write_once
from ..storage import sha
REPO=Path(__file__).resolve().parents[3]
BASE=REPO/'data/l2_pipeline/connectors_v2/c70_20261012'
UNITS=tuple(sorted(STATES.values()))
UCR_FIELDS=('rape_legacy','rape_revised','robbery','aggravated_assault','burglary','larceny','motor_vehicle_theft')
from .support_schema import C70_SUPPORT_FIELDS
EXTRA_SUPPORT=C70_SUPPORT_FIELDS


@contextmanager
def settings(module,**changes):
    old={name:getattr(module,name) for name in changes}
    try:
        for name,value in changes.items():setattr(module,name,value)
        yield
    finally:
        for name,value in old.items():setattr(module,name,value)


def save(sid,filters,years,values,inventory,population,url,unit,limitations,flags=None,details=None,construct=None,ages=(0,120)):
    """V15-only writer; retain per-cell coverage in rows and metadata."""
    rows=[];meta=[];flags=flags or {}
    for u in UNITS:
        for y in years:
            v=values.get((u,y));f=flags.get((u,y),{})
            if set(f)-core.SUPPORT_FIELDS:raise ConnectorError('Non-metadata fields in C70 support flags')
            usable=v is not None and not any(f.get(k) for k in ('unreliable','suppressed','limited_data','partial_year','provisional'))
            rows.append(row(u,y,v,unit,outcome_scale='rate',**{k:v for k,v in f.items() if k in EXTRA_SUPPORT}))
            meta.append(metadata(rows[-1],usable,**f))
    spec=core.save_panel(sid,filters,rows,meta,inventory,limitations,details or {},population,url,
        requested_year_range=[min(years),max(years)],legal_certification_granted=False)
    spec.update(adapter='c70_artifact',adapter_version='c70_offline_v1',registered_connector='outcome_v15_'+sid,
        construct=construct or sid,ages=list(ages),series_breaks=sorted({(y,f['series_break']) for (u,y),f in flags.items() if f.get('series_break')}))
    return spec


def ucr():
    p=REPO/'data/l2_pipeline/connectors_v2/fbi_ucr_state/raw/estimated_crimes_1979_2025.extracted.csv'
    records=list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')));inv=[core.source_record(p),core.source_record(p.with_name('estimated_crimes_1979_2025.xlsx'))];specs={}
    for field in UCR_FIELDS:
        cells,years=c49_ucr.parse(records,field)
        # Definitions remain separate series, including unavailable years.
        flags={k:{n:v for n,v in c.items() if n not in {'value','count','population','available'}} for k,c in cells.items()}
        for (u,y),f in flags.items():
            if y==2013 and field in {'rape_legacy','rape_revised'}:f['series_break']='legacy_and_revised_rape_definitions_separate'
        sid='fbi_ucr_'+field+'_rate'
        specs[sid]=save(sid,dict(population='all_residents',measure=field),years,{k:c['value'] for k,c in cells.items()},inv,
            'All-age resident population; FBI estimated '+field,'https://cde.ucr.cjis.gov/','offenses per 100,000 residents',
            ['Counts divided by same-file population; no imputation.','Rape legacy and revised definitions never spliced; 2021 NIBRS transition and source caveats retained.'],flags,
            dict(numerator=field,denominator='same-file resident population'),construct='rape' if field.startswith('rape') else field)
    return specs


def ipeds_coverage(expected,present,source_issue=''):
    if type(expected) is not int or type(present) is not int or not 0<=present<=expected:raise ConnectorError('Invalid IPEDS institution coverage')
    share=present/expected if expected else None
    substantive=set(source_issue.split(';'))-{'','HD_institutions_without_component','IPEDS_imputed_or_adjusted_total'}
    return dict(institution_coverage=share,institution_coverage_sensitivity_99=bool(expected and present*100>=expected*99 and not substantive),
        institutions_expected=expected,institutions_reporting=present,unreliable=bool(not expected or present*100<expected*95 or substantive),source_issue=source_issue)


def ipeds():
    base=REPO/'data/l2_pipeline/connectors_v2/ipeds/raw';pop,popinv=public.total_population(2002,2024);specs={}
    for kind,end in [('enrollment',2023),('completions',2024)]:
        years=list(range(2002,end+1));values={};flags={};inv=[];details={}
        for y in years:
            hd=base/f'HD{y}.zip';component=base/(f'EF{y}A.zip' if kind=='enrollment' else f'C{y}_A.zip')
            totals,quality,info=education.ipeds_year(hd,component,y,kind);inv.extend([core.source_record(hd),core.source_record(component)]);details[str(y)]=info
            for u in UNITS:
                v=totals.get(u);values[u,y]=v/pop[u,y]*100000 if v is not None else None;c=info['by_state'][u]
                flags[u,y]=ipeds_coverage(c['expected'],c['present'],quality[u]['source_issue'])|dict(missing_reason=quality[u]['missing_reason'],series_break='race_columns_to_total_columns' if y==2008 else '')
        sid='ipeds_'+kind+'_per_population'
        specs[sid]=save(sid,{'measure':kind,'institution_scope':'all_HD_component_participants','geography':'institution_state'},years,values,inv+popinv,
            'IPEDS institutions in state over all resident population','https://nces.ed.gov/ipeds/datacenter/data/',
            'students per 100,000 residents' if kind=='enrollment' else 'awards per 100,000 residents',
            ['C66: reported institution coverage >=95% usable; >=99% registered sensitivity; no missing institution imputation.', 'Published NCES imputed/adjusted cells retained and flagged.'],flags,
            dict(year_details=details,coverage_rule=dict(primary_minimum=.95,sensitivity_minimum=.99,authority='docs/plans/l2-pipeline/HUB_RULING_C66_IPEDS_COVERAGE.md'),sensitivity_required='institution_coverage >= 0.99'),construct='postsecondary_enrollment' if kind=='enrollment' else 'postsecondary_completions')
        specs[sid]['required_sensitivities']=[dict(field='institution_coverage',operator='>=',threshold=.99,authority='docs/plans/l2-pipeline/HUB_RULING_C66_IPEDS_COVERAGE.md')]
    return specs


def parse_early_acs(base,y,table,cells):
    if y not in range(2005,2010) or table not in {'B14004','B14005'}:raise ConnectorError('Unsupported early ACS table/year')
    seq={2005:54,2006:50,2007:54,2008:53,2009:53}[y];start=(7 if table=='B14004' else 40) if y<2008 else (123 if table=='B14004' else 156)
    out={};inv=[];base=base/'seq'/str(y)
    for fips,u in STATES.items():
        g=base/(f'{u.lower()}geo.2005-1yr' if y==2005 else f'g{y}1{u.lower()}.txt')
        zpath=base/(f'{u.lower()}0000054.2005-1yr.zip' if y==2005 else f'{y}1{u.lower()}{seq:04}000.zip');inv.extend([core.source_record(g),core.source_record(zpath)])
        geos=[s for s in g.read_text(encoding='cp1252').splitlines() if s[8:11]=='040' and s[11:13]=='00' and s[6:8].upper()==u]
        if len(geos)!=1:raise ConnectorError('Early ACS state/component geography differs')
        log=geos[0][13:20];selected={}
        with zipfile.ZipFile(zpath) as z:
            for kind in ('E','M'):
                names=[n for n in z.namelist() if (Path(n).name.lower().endswith(kind.lower()+'.2005-1yr') if y==2005 else Path(n).name.lower().startswith(kind.lower()) and Path(n).suffix=='.txt')]
                if len(names)!=1:raise ConnectorError('Early ACS estimate/MOE member differs')
                records=[r for r in csv.reader(io.StringIO(z.read(names[0]).decode('cp1252'))) if r[5]==log]
                if len(records)!=1 or int(records[0][4])!=seq or records[0][2].upper()!=u or records[0][3]!='000':raise ConnectorError('Early ACS sequence/LOGRECNO differs')
                selected[kind]={n:public.number(records[0][start+n-2]) for n in cells}
        out[u]=selected
    return out,inv


def college_formula(est,public_only=False):
    nums=[5,21] if public_only else [5,10,21,26];dens=[5,10,15,21,26,31]
    if any(est.get(n) is None for n in dens):return None
    den=sum(est[n] for n in dens);num=sum(est[n] for n in nums)
    if num>den:raise ConnectorError('ACS enrollment exceeds exact age universe')
    return 100*num/den if den>0 else None


def acs():
    base=REPO/'data/l2_pipeline/connectors_v2/census_acs1/raw';specs={}
    for table,cells in [('B14004',[5,10,15,21,26,31]),('B14005',[1,12,26])]:
        published={};inv=[];years=[y for y in range(2005,2025) if y!=2020]
        for y in years:
            data,inventory=(parse_early_acs(base,y,table,cells) if y<2010 else education.parse_acs_year(base,y,table,cells));published[y]=data;inv.extend(inventory)
        for public_only in ([False,True] if table=='B14004' else [False]):
            sid=('acs1_public_college_enrollment_share_age18_24' if public_only else 'acs1_college_enrollment_share_age18_24') if table=='B14004' else 'acs1_dropout_share_age16_19'
            values={(u,y):(college_formula(r['E'],public_only) if table=='B14004' else education.acs_formula(table,r['E'])) for y,data in published.items() for u,r in data.items()}
            flags={(u,y):dict(sampling_error=True,series_break='group_quarters_added_to_ACS_universe' if y==2006 else '',missing_reason='' if values.get((u,y)) is not None else 'published_cell_missing') for u in UNITS for y in years}
            filters=dict(age_min=18,age_max=24,measure='public_college_enrollment' if public_only else 'college_enrollment') if table=='B14004' else dict(age_min=16,age_max=19,measure='not_enrolled_not_highschool_graduate')
            specs[sid]=save(sid,filters,years,values,inv,'Resident ages18–24; college or graduate school enrollment' if table=='B14004' else 'Resident ages16–19; not enrolled and not high school graduate',
                'https://www2.census.gov/programs-surveys/acs/summary_file/','percent',
                ['2005 household population; group quarters added in2006. 2020 standard ACS1 absent, excluded.','Published component MOEs retained; derived ratio MOE not computed.'],flags,
                dict(table=table,formula='100*(E005+E021)/(E005+E010+E015+E021+E026+E031)' if public_only else '100*(E005+E010+E021+E026)/(E005+E010+E015+E021+E026+E031)' if table=='B14004' else '100*(E012+E026)/E001',component_moes={u+':'+str(y):r['M'] for y,data in published.items() for u,r in data.items()},derived_ratio_moe_computed=False),
                construct='public_college_enrollment' if public_only else 'college_enrollment' if table=='B14004' else 'high_school_noncompletion',ages=(18,24) if table=='B14004' else (16,19))
    return specs


def parse_brfss(records):
    values={};flags={};question_ids={'_HCVU651','_HCVU652','_HCVU653','_HCVU654','_HCVU65'}
    for r in records:
        if r['locationabbr'] not in UNITS:continue
        q=r['questionid'];y=int(r['year']);response=r['response']
        if q not in question_ids:continue
        if response not in {'Yes','Have some form of health insurance'}:continue
        if r['break_out']!='Overall' or r['data_value_type']!='Crude Prevalence' or r['data_value_unit']!='%' or '18-64' not in r['question']:raise ConnectorError('BRFSS exact age/weight/response differs')
        key=r['locationabbr'],y
        if key in values or not 1995<=y<=2025:raise ConnectorError('BRFSS duplicate or outside requested years')
        v=public.number(r['data_value'])
        if v is not None and v>100:raise ConnectorError('BRFSS outside 0..100')
        values[key]=v;note=r.get('data_value_footnote','')
        flags[key]=dict(question_id=q,suppressed=bool(note and v is None),missing_reason=note if v is None else '',series_break='cellphone_sampling_and_raking_methodology' if y==2011 else 'health_coverage_question_revision' if y==2021 else '')
    first_by_question={q:min(y for (u,y),f in flags.items() if f['question_id']==q) for q in {f['question_id'] for f in flags.values()}}
    for (u,y),f in flags.items():
        if y>2011 and y==first_by_question[f['question_id']]:f['series_break']='health_coverage_question_id_change_'+f['question_id']
    return values,flags


def brfss():
    paths=sorted((BASE/'raw').glob('brfss_coverage*.csv'));records=[]
    for p in paths:records.extend(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
    values,flags=parse_brfss(records)
    if not values:raise ConnectorError('BRFSS selected coverage series absent')
    sid='brfss_health_coverage_pct_age18_64'
    with settings(core,BASE=BASE/'review1'):
        spec=save(sid,dict(age_min=18,age_max=64,breakout='Overall',measure='health_coverage'),range(1995,2026),values,[core.source_record(p) for p in paths],
        'BRFSS surveyed adults ages18–64; weighted crude prevalence','https://data.cdc.gov/resource/dttw-5yxu.csv','percent',
        ['Legacy1995–2010 spliced to2011–2025 with 2011 cellphone/raking methodology break. Analyze registered segments separately.','Unpublished/suppressed state years remain unavailable.'],flags,
        dict(analysis_segments=[[1995,2010],[2011,2020],[2021,2025]],observed_years=sorted({y for u,y in values})),construct='health_insurance_coverage',ages=(18,64))
    return {sid:spec}


def ftc():
    p=REPO/'data/l2_pipeline/connectors_v2/ftc_csn/parsed/ftc_idtheft_state_year.csv';pop,inv=public.total_population(2002,2024);values={};flags={};checks={}
    draft=json.loads((REPO/'data/l2_pipeline/intake/bulk_20261012/consumer_breach/needs_connector.json').read_text())['items'][0]['catalog_entry']['series_entry']
    breaks={r['year']:r['note'] for r in draft['series_breaks']}
    for r in csv.DictReader(p.open(newline='')):
        u=r['state_abbr'];y=int(r['year']);key=u,y
        if u not in UNITS or y not in range(2002,2025) or key in values:raise ConnectorError('FTC duplicate/state/year mismatch')
        count=public.number(r['reports'])
        if count is None or count!=int(count):raise ConnectorError('FTC noninteger/missing reported complaints')
        values[key]=count/pop[key]*100000;note=r['notes'];checks[u+':'+str(y)]=dict(reports=count,population=pop[key],published_rate=r['per_100k_published'],source_file=r['source_file'])
        flags[key]=dict(source_issue=note,unit_term='victims' if y<=2004 else 'complaints' if y<=2016 else 'reports',contributor_exclusion='exclu' in note.lower(),series_break=breaks.get(y,''))
    if len(values)!=1173:raise ConnectorError('FTC requires complete 51 x 23 source grid')
    sid='ftc_csn_idtheft_reports_per_100k';spec=save(sid,dict(measure='identity_theft_reports',population='all_residents'),range(2002,2025),values,[core.source_record(p)]+inv,
        'All-age residents; reported identity-theft complaints by victim state','https://www.ftc.gov/enforcement/consumer-sentinel-network/reports','reported complaints per 100,000 residents',
        ['Reported complaints, not victimization. Contributor exclusions and wording changes retained per cell.','Recomputed using Census July1 population; printed rates retained as checks only, including DC footnotes.'],flags,
        dict(construct_label='reported complaints',published_checks=checks),construct='reported_identity_theft')
    spec['series_breaks']=draft['series_breaks'];spec['construct_label']='reported complaints';return {sid:spec}


def _assemble():
    new={**ucr(),**ipeds(),**acs(),**brfss(),**ftc()}
    directory=Path(__file__).parent;old=directory/'OUTCOME_CATALOG_V14.json';catalog=json.loads(old.read_text())
    catalog.update(schema_version=15,catalog_id='first_order_outcomes_v15',inherited_catalog=dict(path='scripts/l2_pipeline/connectors/'+old.name,sha256=sha(old)),registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v15.construct',registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v15.register',acquisition_entrypoint='scripts.l2_pipeline.connectors.c70_build.assemble')
    catalog['series'].update(new)
    for sid,s in catalog['series'].items():s['registered_connector']='outcome_v15_'+sid
    previous=directory/'OUTCOME_POPULATIONS_V14.json';meta=json.loads(previous.read_text());meta.update(catalog_id=catalog['catalog_id'],inherited_population_metadata=dict(path='scripts/l2_pipeline/connectors/'+previous.name,sha256=sha(previous)))
    for sid,s in new.items():
        construct=meta['series'].get(sid,{}).get('construct',s['construct']);s['construct']=construct
        catalog['targets'].setdefault(construct,dict(series=[],selection_authorized=True))
        if sid not in catalog['targets'][construct]['series']:catalog['targets'][construct]['series'].append(sid)
        meta['series'][sid]=dict(construct=construct,ages=s['ages'],restrictions={},filters=s['population_filters'],population_basis=s['population'],population_unit='institution_student' if sid=='ipeds_enrollment_per_population' else 'institution_award' if sid=='ipeds_completions_per_population' else 'person',metadata_basis='C70 published source population and C66 coverage ruling; no policy/outcome associations')
        if not any(t['construct']==construct for t in meta['targets']):meta['targets'].append(dict(construct=construct,aliases=[construct],ages=s['ages'],restrictions={},rule='C70 exact published construct'))
    write_once(directory/'OUTCOME_CATALOG_V15.json',catalog);write_once(directory/'OUTCOME_POPULATIONS_V15.json',meta)
    write_once(BASE/'BUILD_RECEIPT_FINAL.json',dict(series={sid:dict(artifact=s['artifact'],support_artifact=s['support_artifact'],coverage=s['observed_coverage']) for sid,s in new.items()},outcome_association_calls=0,actual_data_estimation_calls=0))
    downloads=[]
    for p in sorted((BASE/'raw').glob('*.receipt.json')):
        r=json.loads(p.read_text());downloads.append(dict(file=str(p.with_name(p.name.removesuffix('.receipt.json')).relative_to(REPO)),**r))
    write_once(BASE/'SOURCE_FILES.json',dict(new_downloads=downloads,outcome_association_calls=0))
    return catalog,meta

def assemble(repo=None):
    """Scoped build roots; preserve all runtime globals even if a build fails."""
    root=Path(repo).resolve() if repo is not None else REPO
    base=root/'data/l2_pipeline/connectors_v2/c70_20261012'
    with ExitStack() as stack:
        for module,changes in [(sys.modules[__name__],dict(REPO=root,BASE=base)),(core,dict(REPO=root,BASE=base)),(public,dict(REPO=root)),(rates,dict(ROOT=root))]:
            stack.enter_context(settings(module,**changes))
        return _assemble()

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--source-repo');args=parser.parse_args();assemble(args.source_repo)
