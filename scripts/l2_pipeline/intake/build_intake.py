"""Metadata-only prospective research-panel intake; never opens outcomes or dispatches."""
from pathlib import Path
import json, collections, csv

from scripts.l2_pipeline.intake.config import REPO, IntakeConfig
from scripts.l2_pipeline.sources.research_panel.core import read_panel, history
from scripts.l2_pipeline.storage import sha, ensure_once
from scripts.l2_pipeline.intake import v8b_calendar as adapter

ROOT = None
SOURCE = None
CATALOG = None
EVIDENCE = None
V4 = REPO / 'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V4.json'
RATE = REPO / 'scripts/l2_pipeline/connectors/OUTCOME_RATE_CATALOG.json'


def configure(config, *, catalog, screening_evidence):
    global ROOT, SOURCE, CATALOG, EVIDENCE
    ROOT = config.root.resolve()
    SOURCE = ROOT.parent
    CATALOG = Path(catalog)
    EVIDENCE = Path(screening_evidence)
    adapter.configure(config)


def read(p): return json.loads(Path(p).read_text())
def write(p, v): return ensure_once(Path(p), v)

def existing(family, target, series, *, filters=None, population=None):
    s = read(V4)['series'][series]
    return dict(family=family, target=target, connector=s['registered_connector'], series_id=series,
                connector_exists=True, years=s['years'], population=population or s['population'],
                population_filters=filters or s.get('population_filters', {}), source_url=s['source_url'],
                free_public=s.get('public_available', s.get('access')=='public_no_account_no_key'),
                license=s.get('license', 'see receipt-bound catalog exports'), outcome_scale='rate',
                frequency='annual',purpose_status='PENDING_V62_S0', local_support_certified=False)

def old(family, target, connector, years, population, url, filters=None):
    return dict(family=family, target=target, connector=connector, series_id=target,
                connector_exists=True, years=list(years), population=population,
                population_filters=filters or {}, source_url=url, free_public=True,
                license='US federal government public-use data; attribution retained',
                outcome_scale='rate',frequency='biennial' if connector=='cps_voting' else 'annual',
                purpose_status='PENDING_V62_S0', local_support_certified=False)

def missing(family, target, connector, years, url, population, free=True):
    return dict(family=family,target=target,connector=connector,series_id=target,connector_exists=False,
                years=list(years), source_url=url, population=population, population_filters={},
                free_public=free, license='Reuse license not certified; verify before acquisition',
                outcome_scale='rate',frequency='annual',purpose_status='PENDING_V62_S0', local_support_certified=False,
                connector_public_state_year_availability='UNVERIFIED; public source access does not establish the requested historical panel',
                coverage_basis='prospective_build_window_not_verified_observed_support')

def mapping(v):
    pid=v['policy_id']; n=pid.split(':')[1]; label=v.get('label',v['definition'])
    if pid.startswith('siegel:') or n.startswith('guncontrol') or n.startswith('w_guncontrol'):
        return existing('firearms','firearm_mortality','wonder_firearm_deaths',
                        population='All-age all-sex state residents; closest proxy for statutory subgroup; dilution to be reviewed by S0')
    if 'eitc' in n or n=='earned_income_taxcredit':
        return existing('income_support','single_mother_employment','cps_single_mother_employment')
    if n.startswith('ssi'):
        return missing('disability_income','eligible_aged_or_disabled_poverty','cps_asec_state_aged_disabled_poverty_rate',range(1980,2025),
            'https://www.census.gov/programs-surveys/cps/data/datasets.html','SSI eligible aged or disabled individuals; single-mother income cannot substitute')
    if n.startswith('snap_'):
        return missing('nutrition_support','eligible_family_food_insecurity','cps_state_food_security_prevalence',range(1995,2025),
            'https://www.ers.usda.gov/topics/food-nutrition-assistance/food-security-in-the-u-s','Eligible family food security; cash income is a different construct')
    if any(t in n for t in ('tanf','afdc')):
        return existing('income_support','eligible_family_income','acs1_single_mother_median_family_income')
    if 'minwage' in n or 'minimumwage' in n:
        return existing('minimum_wage','teen_employment','cps_teen_employment')
    if 'prevailing_wage' in n:
        return old('prevailing_wage','annual_avg_wkly_wage','qcew_annual',range(2001,2025),
                   'Private construction jobs; aggregate sector proxy for covered public works workers',
                   'https://www.bls.gov/cew/downloadable-data-files.htm',{'ownership':'private','industry':'23'})
    if n in {'aborparc','aborparn'} or 'abortion_parental' in n:
        return existing('reproductive_health','minor_birth_rate','cdc_teen_birth_rate',filters={'age_group':'15-17'},
                        population='Females age15-17; same birth construct; state statutory age limits require S0 review')
    if 'abortion' in n or n in {'foca','pgestlim','w_ec_access'}:
        if n=='abortion_reform_preroe' or n=='abortion_consent_1973_1991':
            return missing('reproductive_health','abortion_incidence','cdc_historical_state_abortion_rate',range(1969,1992),
                'https://www.cdc.gov/abortion-surveillance/about/index.html','Women of reproductive age; reporting states only')
        return existing('reproductive_health','birth_rate_reproductive_age','wonder_fertility_rate_by_maternal_age',
                        filters={'age_group':'20-24'},population='Women20-24; same birth construct; age aggregation dilution subject to S0')
    if n in {'absvot','earlvot','elecdayreg','mailreg','motorvoter'}:
        return old('voting_access','voter_turnout','cps_voting',range(1980,2025,2),
            'Citizen civilian noninstitutional population age18+; even election years only',
            'https://www.census.gov/topics/public-sector/voting/data/tables.html',
            {'civilian_noninstitutional':True,'citizens':True,'age_min':18})
    if n in {'regulation_bicycle_helmets','regulation_mandatory_seatbelts','drugs_boehmke_mlda21','drugs_boehmke_zerotol','regulation_motorcycle_helmets'}:
        measure='child_bicycle_deaths_count' if 'bicycle' in n else 'teen_traffic_deaths_count' if 'drugs_' in n else 'traffic_deaths_count'
        return old('road_safety',measure,'fars_state',range(1982,2023),
            'Exact road-user/age scope must match statute; rate needs matching resident denominator',
            'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars',
            {'population':'traffic_fatalities','age_min':0,'age_max':17,'person_type':'bicyclist'} if 'bicycle' in n else
            {'population':'traffic_fatalities','age_min':15,'age_max':20,'person_type':'all'} if 'drugs_' in n else
            {'population':'all_traffic_fatalities'})
    if n=='drugs_boehmke_kegreg':
        return missing('alcohol','youth_alcohol_involved_fatality_rate','fars_youth_alcohol_involved_rate',range(1982,2023),
            'https://www.nhtsa.gov/research-data/fatality-analysis-reporting-system-fars','Age15-20 alcohol-involved traffic deaths')
    if 'smoking_ban' in n or 'cigarette' in n or n=='cig_taxes_binary':
        return old('tobacco','current_smoking_pct','brfss_state_prevalence',range(2011,2025),
            'Published adult current smoking prevalence; occupation coverage requires dilution review',
            'https://data.cdc.gov/resource/dttw-5yxu.csv',
            {'questionid':'_RFSMOK3','responseid':'RESP046','breakoutid':'BO1','data_value_type':'Crude Prevalence'})
    if 'medical_marijuana' in n or 'marijuana_decriminal' in n:
        return missing('cannabis','cannabis_use_prevalence','nsduh_state_cannabis_prevalence',range(1999,2025),
            'https://www.samhsa.gov/data/data-we-collect/nsduh-national-survey-drug-use-and-health','NSDUH age-specific state use prevalence; two-year estimates need annual-support review')
    if n=='death_penalty':
        return missing('criminal_justice','homicide_rate','fbi_historical_state_homicide_rate',range(1960,2025),
            'https://cde.ucr.cjis.gov/','Full-state homicide incidents; historic keyless download availability unverified',free='public_data; keyless bulk access unverified')
    if n in {'fhpriv','fhpub','fhurb','race_boehmke_fhpub','race_boehmke_fhurb','w_race_fair_housing_private'}:
        return missing('housing_rights','housing_discrimination_complaint_rate','hud_state_housing_discrimination_complaints',range(1959,2025),
            'https://www.hud.gov/program_offices/fair_housing_equal_opp','Affected housing applicants; historic state coverage not verified')
    if n in {'regulation_housing_directstateai','regulation_housing_enabling_federal_aid','urbrenen'}:
        return missing('housing_supply','assisted_or_redeveloped_housing_units','hud_historical_state_assisted_housing_rate',range(1939,2025),
            'https://www.huduser.gov/portal/datasets/picture/yearlydata.html','Policy-specific assisted/redeveloped housing; long historical coverage not verified')
    if n=='miglab':
        return missing('farm_labor','covered_farmworker_labor_conditions','dol_state_migrant_farmworker_conditions',range(1950,2025),
            'https://www.dol.gov/agencies/eta/national-agricultural-workers-survey','Covered migrant farmworkers; public microdata may lack state identifiers',free='UNKNOWN')
    if n=='regulation_pain_suffering_limits':
        return missing('civil_liability','civil_claims_and_damages','state_civil_claim_damages_rate',range(1975,2025),
            'https://www.courtstatistics.org/','Claimants in covered suits; comparable public state-year damages series not verified',free='UNKNOWN')
    if n in {'regulation_rfra','regulation_sedition_laws','regulation_boehmke_shield'}:
        return missing('civil_liberties','affected_rights_litigation','state_civil_liberties_litigation_rate',range(v['coverage']['start_year'],2025),
            'https://www.courtstatistics.org/','Covered religious-freedom/sedition/reporter cases; long public state panel not verified',free='UNKNOWN')
    if 'hate_crimes' in n or n=='gayrights_hatecrimes':
        return missing('civil_rights','hate_crime_victimization_rate','fbi_state_hate_crime_rate',range(1991,2025),
            'https://cde.ucr.cjis.gov/','Reported hate-crime victims; agency reporting coverage must be certified',free='public_data; keyless bulk access unverified')
    if n in {'equalpay','genderrights_gender_equal_pay','genderrights_gender_discrimination','genderrights_gender_discrimination_laws_post1964','fairemp','race_fair_employ_comm_post1964','race_fair_employment_commissions','labor_age_discrimination'}:
        return missing('employment_rights','affected_group_earnings_gap','cps_state_demographic_earnings_gap',range(1979,2025),
            'https://www.census.gov/data/datasets/time-series/demo/cps/cps-basic.html','Exact sex/race/age groups and eligible workforce; no aggregate earnings substitution')
    if n=='genderrights_nofault_divorce':
        return existing('family_law','suicide_mortality','wonder_suicide_deaths',population='All-sex all-age residents; proxy dilution for covered spouses requires V6.2 literature review')
    if n=='regulation_mandatory_car_insuran':
        return missing('auto_insurance','uninsured_motorist_share','state_uninsured_motorist_rate',range(1980,2025),
            'https://content.naic.org/','Driver insurance coverage; free annual50-state evidence not verified',free='UNKNOWN')
    if n=='regulation_boehmke_livingwill':
        return missing('end_of_life','advance_directive_use','state_advance_directive_utilization',range(1976,2025),
            'https://www.cms.gov/data-research','End-of-life patients; no long free annual50-state series verified',free='UNKNOWN')
    if n in {'inctax','income_taxes','corporate_incometax','estate_tax','sales_taxes_binary_pre1946','x_sales_taxes','x_tax_rate_rich','x_top_corporateincometaxrate','tels','regulations_state_debt_limitatio'}:
        return missing('public_finance','state_tax_revenue','census_state_tax_receipts_real_per_capita',range(1951,2025),
            'https://www.census.gov/programs-surveys/stc/data.html','State tax receipts with population/price denominators')
    if n=='environment_ghg_cap':
        return missing('climate','power_sector_co2_per_capita','eia_state_power_sector_co2_rate',range(1990,2025),
            'https://www.eia.gov/electricity/data.php','Electric-generating sector CO2; cap coverage and annual vintage require review')
    if n in {'fswp','fwtldrps'}:
        return missing('wetlands','wetland_restored_or_lost_acres','state_wetland_restoration_loss_rate',range(1980,2025),
            'https://www.fws.gov/program/national-wetlands-inventory','Covered wetlands acreage; comparable annual state panel not established',free='UNKNOWN')
    if n in {'fesasap','w_environment_endangered_species'}:
        return missing('species','protected_species_or_habitat_harm','state_species_habitat_outcome_panel',range(1980,2025),
            'https://ecos.fws.gov/','Policy-covered species and habitats; annual state aggregation not established',free='UNKNOWN')
    if n in {'environ','environment_state_nepas','environment_ghg_cap','fsmlp','fswp','fesasap','fwtldrps','w_environment_endangered_species','environment_air_pollution_contro','environment_ca_car_emissions_sta','transboundary_pollution_reciproc','regulation_boehmke_cogrowman'}:
        return missing('environment','environmental_review_and_permitting','state_environmental_review_permitting_panel',range(1980,2025),
            'https://www.epa.gov/nepa','Covered land-use/environmental review decisions; target must be confirmed by S0; historical state-year panel not established',free='UNKNOWN')
    if n in {'ewaste','environment_electronic_waste','environment_bottlebill'}:
        return missing('waste','recycled_waste_share','state_recycling_material_recovery_rate',range(1990,2025),
            'https://www.epa.gov/facts-and-figures-about-materials-waste-and-recycling','Policy-specific material recovery; free national data do not establish state coverage',free='UNKNOWN')
    if n in {'pubcamfun','candlim','corporlimits','indlimit'}:
        return missing('campaign_finance','campaign_contribution_concentration','state_campaign_contribution_panel',range(1978,2025),
            'https://www.followthemoney.org/','State elections/candidate donor concentration; bulk access rights unverified',free='public_browsing; bulk access unverified')
    if n=='immigration_english_language':
        return missing('language','language_access_to_state_services','state_language_service_access_rate',range(1980,2025),
            'https://www.lep.gov/','Persons needing language access in state services; comparable historical series not established',free='UNKNOWN')
    # Remaining variables retain their source-specific first-order leads, with an
    # explicit unresolved measurement definition rather than a different proxy.
    leads=v.get('first_order_outcome_candidates',[])
    category=v.get('category','other_policy')
    target='; '.join(leads) or 'statutory behavior/welfare target needs purpose research'
    return missing(str(category), target, 'build_'+n, range(v['coverage']['start_year'],min(v['coverage']['end_year'],2024)+1),
                   v.get('official_source_url','https://www.census.gov/data.html'),
                   label+' statutory beneficiaries; measurement definition requires S0',free='UNKNOWN')

def prior_packets():
    by=collections.defaultdict(lambda:dict(states=set(),packets=[]))
    # Audit contracts only: do not open forecasts, outcome panels or results.
    for p in sorted((REPO/'data/l2_pipeline').rglob('*.job.json')):
        if p.is_relative_to(ROOT) or 'results' in p.parts or not p.name.startswith('auditor'): continue
        j=read(p); c=j.get('contract',j)
        if c.get('source_type')!='research_panel' or j.get('role',c.get('role'))!='auditor':continue
        pid=c.get('policy_id')
        if not pid and c.get('card_path'):pid=read(c['card_path'])['treatment']['policy_id']
        if pid:
            by[pid]['states'].update(c.get('sample',[]));by[pid]['packets'].append(p)
    return by

def geometry(imported, m):
    years=sorted(set(m['years']) & set(range(imported['coverage']['start_year'],imported['coverage']['end_year']+1)))
    states=[]; treated=[]; controls=[]; cohort_controls={}
    for r in imported['records']:
        g=r['start_period']; retained=[y for y in years if y!=g]
        pre=[y for y in retained if g is not None and y<g]
        post=[y for y in retained if g is not None and y>g]
        qualified=(r['status']=='treated' and not r['primary_excluded'] and g in years and len(pre)>=3 and len(post)>=2)
        if qualified:treated.append(r['unit'])
        if r['never_control_eligible']:controls.append(r['unit'])
        states.append(dict(unit=r['unit'],panel_onset=g,status=r['status'],pre_n=len(pre),post_n=len(post),
                           transition_drop=g if g in years else None,qualified_treated=qualified,
                           source_excluded=r['primary_excluded'],reversals=r['reversal_years']))
    tr={r['unit']:r for r in imported['records']}; groups=sorted({tr[u]['start_period'] for u in treated})
    for g in groups:
        used=[y for y in years if y<g][-3:]+[y for y in years if y>g][:2]
        horizon=max(used)
        pool=[r['unit'] for r in imported['records'] if not r['primary_excluded'] and
              (r['never_control_eligible'] or (r['status']=='treated' and r['start_period']>horizon))]
        cohort_controls[str(g)]=dict(control_n=len(pool),units=pool,pre_post_years=used)
    dropped=[u for u in treated if cohort_controls[str(tr[u]['start_period'])]['control_n']<5]
    support_treated=[u for u in treated if u not in dropped]
    supported_groups=sorted({tr[u]['start_period'] for u in support_treated})
    minimum=min((cohort_controls[str(g)]['control_n'] for g in supported_groups),default=0)
    return dict(outcome_retained_years=years,raw_prepost_treated_n=len(treated),treated_n=len(support_treated),treated_states=support_treated,
                dropped_insufficient_controls=dropped,
                never_control_n=len(controls),never_controls=controls,cohorts_n=len(groups),
                supported_cohorts_n=len(supported_groups),
                min_cohort_controls=minimum,cohort_controls=cohort_controls,state_counts=states,
                passed=len(support_treated)>=6 and minimum>=5 and len(supported_groups)>=2,
                rule='>=6 supported treated with3pre/2post retained periods; >=5 controls at each retained cohort3pre/2post horizon; >=2 cohorts(V7); drop unsupported cohorts prospectively',
                source_geometry_only=True,suppression_or_population_support_not_certified=True)

def build(config, *, catalog, screening_evidence, mapping_resolver=None,
          geometry_resolver=None, rescreen_all=False, extra_bindings=(),
          completed_policy_ids=()):
    configure(config, catalog=catalog, screening_evidence=screening_evidence)
    c=read(CATALOG); e=read(EVIDENCE); audit={r['policy_id'] for r in e['rows']}
    passed={r['policy_id'] for r in e['rows'] if r['screening_pass']};used={r['policy_id'] for r in e['rows'] if r['emit_eligible']}
    panelrows={p['path']:read_panel(p['path']) for p in c['panels']}
    priors=prior_packets(); rows=[]; imported_by={}
    for v in c['variables']:
        pid=v['policy_id']; m=(mapping_resolver or mapping)(v)
        in_scope=pid in passed-used or pid not in audit
        row=dict(policy_id=pid,design_id=config.design_prefix+'_'+pid.replace(':','_'),kind=v['kind'],label=v.get('label',v['definition']),
                 mapping=m,in_scope=in_scope,screening_pass=pid in passed,screening_status='PASS' if pid in passed else 'FAIL' if pid in audit else 'UNAUDITED',
                 already_used=pid in used,emit_eligible=False,codebook_confirmed=False,s0_accept=False,purpose_accepted=False)
        if not in_scope and not rescreen_all:row['disposition']='USED_OR_FAILED_SCREEN';rows.append(row);continue
        windows=v['coverage']['historical_complete_50_state_windows']
        if v['kind']!='binary_adoption':row['disposition']='PARKED_PREDECLARED_THRESHOLD_REQUIRED';rows.append(row);continue
        if not windows:row['disposition']='PARKED_NO_COMPLETE_HISTORICAL_SOURCE_WINDOW';rows.append(row);continue
        window=min(windows,key=lambda w:(-(w[1]-w[0]+1),w[0]));p=next(p for p in c['panels'] if pid in p['policy_ids'])
        imported=history(panelrows[p['path']],v,window);imported_by[pid]=(v,p,imported)
        row['source_window']=window;row['geometry']=(geometry_resolver or geometry)(imported,m)
        row['disposition']='GEOMETRY_PASS' if row['geometry']['passed'] else 'PARKED_GEOMETRY'
        row['eligibility_basis']='screening_requires_disjoint_calibration' if pid in passed else 'unaudited_direction_requires_disjoint_calibration'
        if not in_scope:row['disposition']='USED_OR_FAILED_SCREEN'
        if pid in completed_policy_ids:
            row['in_scope']=False;row['already_completed']=True;row['disposition']='EXISTING_COMPLETED_DESIGN_PRESERVED'
        rows.append(row)
    # Rank by family round-robin, then screened-before-unaudited and policy id.
    pools=collections.defaultdict(list)
    for row in rows:
        if row['in_scope'] and row.get('geometry',{}).get('passed') and row['mapping']['connector_exists']:
            pid=row['policy_id']; previous=priors[pid]
            plan=adapter.select_states(imported_by[pid][2]['records'],previous['states'])
            row['sample_plan']=plan
            if plan['status']=='PREPARED':pools[row['mapping']['family']].append(row)
            else:row['disposition']=plan['status']
    for pool in pools.values():pool.sort(key=lambda r:(not r['screening_pass'],r['policy_id']))
    ranked=[]
    for index in range(config.family_cap):
        for family in sorted(pools):
            if index<len(pools[family]):ranked.append(pools[family][index])
    for rank,row in enumerate(ranked,1):row.update(rank=rank,emit_eligible=True,disposition='EMITTED_PENDING_PURPOSE_AND_CALIBRATION')
    for pool in pools.values():
        for row in pool[config.family_cap:]:row['disposition']='QUEUED_FAMILY_CAP_'+str(config.family_cap)
    report=dict(schema_version=config.schema_version,attempt_id=ROOT.name,rows=rows,
        input_bindings=[dict(path=str(p),sha256=sha(p)) for p in (CATALOG,EVIDENCE,V4,RATE,*extra_bindings)],
        summary=dict(catalog_variables=len(c['variables']),direction_pass_rows=sum(r['screening_pass'] for r in e['rows']),
            direction_pass_unique=len(passed),used_unique=len(used),unused_screened_unique=len(passed-used),
            scoped_variables=sum(r['in_scope'] for r in rows),scoped_binary=sum(r['in_scope'] and r['kind']=='binary_adoption' for r in rows),
            geometry_pass=sum(r.get('geometry',{}).get('passed',False) for r in rows),
            connector_and_geometry=sum(r.get('geometry',{}).get('passed',False) and r['mapping']['connector_exists'] for r in rows),
            emitted=len(ranked),families=dict(collections.Counter(r['mapping']['family'] for r in ranked)),
            screened_emitted=sum(r['screening_pass'] for r in ranked)),
        outcome_values_read=0,model_calls=0,actual_data_estimation_calls=0,new_downloads=[])
    evidence_path=write(ROOT/'INTAKE.json',report)
    buildqueue=[r for r in rows if r.get('geometry',{}).get('passed') and not r['mapping']['connector_exists']]
    write(ROOT/'CONNECTOR_BUILD_QUEUE.json',dict(rows=buildqueue,coverage_is_prospective=True,new_downloads=[]))
    # Source inventory is inherited; no files are acquired by this attempt.
    write(ROOT/'DOWNLOAD_INVENTORY.json',dict(new_downloads=[],inherited_files=c['files']))
    from scripts.l2_pipeline.contracts import DesignCard,StageContext
    from scripts.l2_pipeline.agents.purpose import emit_purpose_verification
    adapter.install()
    jobs=[]
    for row in ranked:
        pid=row['policy_id'];v,p,imported=imported_by[pid];m=row['mapping'];did=row['design_id']
        cfg=dict(source_type='research_panel',policy_id=pid,predicate=v['definition'],window=row['source_window'],
                 catalog_path=str(CATALOG),catalog_sha256=sha(CATALOG),panel_path=p['path'],panel_sha256=p['sha256'],
                 seed=adapter.AUDIT_SEED,transition_year_drop=True)
        card=dict(design_id=did,version=ROOT.name,policy_family=m['family'],mechanism=v['definition'],track='DiD',unit='state',
                  outcome_family=m['target'],treatment=cfg,transition_year_drop=True,
                  population=dict(target=m['population'],filters=m['population_filters']),
                  first_order=dict(target_outcome=m['series_id'],target_population=m['population'],evidence_status='pending',
                    purpose_rule='V6.2',purpose_basis='PENDING',statute_refs=[],literature_refs=[],
                    proxy_reason='Same behavior/welfare construct; broader published population dilutes policy effect; S0 must verify.'),
                  outcome=dict(connector=m['connector'],series_id=m['series_id'],measure=m['series_id'],measure_id=m['series_id'],frequency=m['frequency'],
                    period_years=row['geometry']['outcome_retained_years'],
                    outcome_scale=m['outcome_scale'],population_filters=m['population_filters'],source_validation_required=True),
                  intake_status='PROSPECTIVE_UNREGISTERED',execution_authorized=False,selection_rule='family_round_robin_cap'+str(config.family_cap)+'_screen_then_policy_id')
        for key in ('paths','source_sha256','outcome_unit','license','analysis_only','republication_prohibited',
                    'catalog_path','catalog_sha256','support_path','support_sha256'):
            if key in m:card['outcome'][key]=m[key]
        cp=write(ROOT/'cards'/f'{did}.json',card);ctx=StageContext(DesignCard.from_mapping(card,cp),ROOT/'purpose_work',REPO)
        job=emit_purpose_verification(ctx,ROOT/'s0_jobs');jobs.append(job.job_path)
        ip=write(ROOT/'imports'/did/'PANEL_IMPORT.json',imported)
        source=dict(contract=dict(card_path=str(cp),bound_inputs=[dict(path=str(ip),sha256=sha(ip))]))
        for kind in ('calibration','fresh_audit'):
            jobs.append(str(adapter.emit_job(row,source,row['sample_plan'],kind,ROOT,evidence_path=evidence_path,
                                            prior_packets=priors[pid]['packets'])))
    write(ROOT/'JOBS_MANIFEST.json',dict(attempt_id=ROOT.name,jobs=[dict(path=j,sha256=sha(Path(j))) for j in jobs],
        fresh_audit_root=str(ROOT/'fresh_audit_jobs'),fresh_dispatch_requires='Hash-bound confirming calibration + direction/Purpose hub acceptance; use dispatch.py wrapper',
        model_calls=0))
    with (ROOT/'VARIABLE_COUNTS.csv').open('x',newline='') as f:
        fields=['policy_id','in_scope','screening_status','family','target','connector','connector_exists','source_window','treated_n','never_control_n','min_cohort_controls','cohorts_n','disposition','rank']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in rows:
            d={k:r.get(k,'') for k in fields};d.update({k:r['mapping'].get(k,'') for k in ('family','target','connector','connector_exists')})
            d.update({k:r.get('geometry',{}).get(k,'NA') for k in ('treated_n','never_control_n','min_cohort_controls','cohorts_n')});w.writerow(d)
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intake-root', type=Path, required=True)
    parser.add_argument('--calibration-seed', type=int, required=True)
    parser.add_argument('--audit-seed', type=int, required=True)
    parser.add_argument('--design-prefix', required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--screening-evidence', type=Path, required=True)
    parser.add_argument('--schema-version', default='research_panel_intake_v1')
    args = parser.parse_args()
    build(IntakeConfig(args.intake_root, args.calibration_seed, args.audit_seed,
                       args.design_prefix, args.schema_version),
          catalog=args.catalog, screening_evidence=args.screening_evidence)
