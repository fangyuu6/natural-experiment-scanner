"""V6 outcome-first RUN3 planning, purpose jobs and V5 official-auditor packets.

No dispatcher, S1 readiness receipt, forecast, signature or fit is called here.
Published DK coverage remains the audit scope, never extended to outcome years.
"""
from __future__ import annotations
import argparse
import copy
import csv
import json
from collections import Counter
from pathlib import Path

from .cards import load_card, validate_card
from .cli import run_stage
from .contracts import StageContext, Disposition
from .first_order import first_order_gate
from .storage import ensure_once, sha

ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / 'data/l2_pipeline/cards/run3'
JOBS = ROOT / 'data/l2_pipeline/jobs/run3'
META = CARDS / '_meta'


def support_inventory(periods, states, *, available_by_unit=None):
    """Count observed periods, never interpolate waves or trim the first cohort.

    Callers supply availability metadata, not outcome values. A per-unit map
    prevents a year observed in one state from certifying every other state.
    """
    periods = sorted(set(periods))
    if any(type(y) is not int for y in periods):
        raise ValueError('Availability periods must be integer years')
    starts = {u: r['adopt_year'] for u, r in states.items()
              if r.get('adopt_year') is not None}
    cohorts = [y for y in starts.values() if periods and periods[0] <= y <= periods[-1]]
    earliest = min(cohorts) if cohorts else None
    pre = [y for y in periods if earliest is not None and y < earliest]
    rows = []
    for unit, start in sorted(starts.items()):
        observed = set(periods if available_by_unit is None else available_by_unit.get(unit, []))
        available = [y for y in periods if y in observed and
                     states[unit]['start_year'] <= y <= states[unit]['end_year']]
        row = dict(unit=unit, cohort_year=start,
                   pre_years=[y for y in available if y < start],
                   post_years=[y for y in available if y > start],
                   analyzed=bool(earliest is not None and periods[0] <= start <= periods[-1]))
        row['eligible'] = row['analyzed'] and len(row['pre_years']) >= 3 and len(row['post_years']) >= 2
        rows.append(row)
    eligible = [r['unit'] for r in rows if r['eligible']]
    earliest_rows = [r for r in rows if r['analyzed'] and r['cohort_year'] == earliest]
    earliest_pre_passed = bool(earliest_rows and all(len(r['pre_years']) >= 3 for r in earliest_rows))
    return dict(passed=earliest_pre_passed and len(pre) >= 3 and len(eligible) >= 6,
                earliest_analyzed_cohort=earliest, pre_years=pre,
                earliest_cohort_pre_support_passed=earliest_pre_passed,
                eligible_treated_states=eligible, state_support=rows)


def s0_acceptance_decision(purpose, collector_validated, card, states,
                           available_by_unit):
    """V6.1 hub rule applied to an in-memory candidate, before S1 readiness.

    Statutory definitions remain verbatim; any accepted changed card still
    requires a new version and rebound packs. Availability is explicit metadata.
    """
    from .coverage import coverage_bounded_card
    from .first_order import target_mapping
    prospective = copy.deepcopy(card)
    first = prospective['first_order']
    first.update(target_outcome=purpose.get('target_outcome'),
                 target_population=purpose.get('target_population'))
    # The broader source population is an explicit proxy, not a replacement
    # definition of the legally eligible population.
    if purpose.get('status') == 'VERIFIED':
        first.update(relation='closest_proxy', proxy_justification=
            (first.get('proxy_justification', '').strip() + ' The broader published population permits a same-construct proxy with intent-to-treat dilution.').strip())
    mapping = target_mapping(prospective)
    a = bool(collector_validated and purpose.get('status') == 'VERIFIED')
    b = bool(mapping['mapped'])
    coverage = coverage_bounded_card(prospective)['automation']['coverage_window']
    actual = sorted({y for ys in available_by_unit.values() for y in ys}
                    & set(coverage['effective_period_years']))
    support = support_inventory(actual, states, available_by_unit=available_by_unit)
    c = bool(not coverage['parked'] and support['passed'])
    primary = ('collector_contract_invalid' if not collector_validated else
               'purpose_unresolved' if not a else
               'same_construct_connector_unavailable' if not b else
               'coverage_no_overlap' if coverage['status'] == 'empty' else
               'coverage_unknown' if coverage['parked'] else
               'insufficient_observed_support' if not c else 'accepted')
    return dict(decision='ACCEPT' if a and b and c else 'PARK',
                primary_reason=primary, failing_conditions=[k for k, ok in
                    (('a', a), ('b', b), ('c', c)) if not ok],
                conditions=dict(a=dict(passed=a, status=purpose.get('status')),
                    b=dict(passed=b, mapping=mapping,
                           proxy_justification=first.get('proxy_justification')),
                    c=dict(passed=c, coverage=coverage, actual_period_years=actual,
                           support=support)),
                execution_authorized=False)


def accepted_s1_paths(report):
    """Fail closed on ledger/selector mismatch, including the empty-set case."""
    records = report['records']
    if len({r['design_id'] for r in records}) != len(records):
        raise ValueError('Duplicate acceptance designs')
    selected = [r for r in records if r['decision'] == 'ACCEPT']
    if any(r.get('failing_conditions') or not all(
            r['conditions'][k]['passed'] is True for k in ('a', 'b', 'c')) for r in selected):
        raise ValueError('Accepted card fails an acceptance condition')
    ids = [r['design_id'] for r in selected]
    paths = [r['s1_job_path'] for r in selected]
    if (report['accepted_design_ids'] != ids or report['accepted_s1_job_paths'] != paths or
            any(not isinstance(p, str) or not p for p in paths) or len(set(paths)) != len(paths)):
        raise ValueError('Acceptance/job selection mismatch')
    return paths


def dispatch_accepted_s1(path, digest, *, dry_run=False):
    """Dispatch the reviewed accepted set only; an empty set never enumerates jobs."""
    from .agents.jobs import load_job
    from .storage import public_input
    path = public_input(Path(path))
    if not digest or sha(path) != digest:
        raise ValueError('Reviewed acceptance SHA-256 missing or changed')
    report = json.loads(path.read_text())
    paths = accepted_s1_paths(report)
    for row in report['records']:
        if row['decision'] != 'ACCEPT':
            continue
        job = load_job(Path(row['s1_job_path']))
        if (job.stage != 'S1' or job.role != 'auditor' or job.design_id != row['design_id'] or
                job.job_sha256 != row['s1_job_sha256'] or
                job.design_version != row['accepted_card_version'] or
                len(job.contract.get('sample', [])) != 10 or
                job.contract.get('source_type') != 'literature_adoption_table' or
                sha(public_input(Path(row['accepted_card_path']))) != row['accepted_card_sha256']):
            raise ValueError('Accepted S1 V5 pack/card binding mismatch')
    if not paths:
        return dict(passed=True, status='EMPTY_ACCEPTED_SET_NO_DISPATCH',
                    selected_jobs=0, model_calls=0, reservations_created=0)
    from .agents.dispatch import main as dispatch_main
    argv = ['--stage', 'S1', '--dry-run' if dry_run else '--hub-outside-sandbox']
    argv += [a for p in paths for a in ('--job', p)]
    code = dispatch_main(argv)
    return dict(passed=code == 0, selected_jobs=len(paths), dry_run=dry_run)

# Selection is about targets and public measurement, never estimated signs.
# Separate SPID aliases are flagged in the inventory, not counted as independent
# replications by L3. The hub must resolve aliases before an S4 specification.
CATALOG = [
    ('labor_minwage_abovefe', 'Labor', 'qcew', 'wages', 'wages'),
    ('meritaid', 'Education', 'ipeds_enrollment', 'undergraduate_enrollment_count', 'undergraduate_enrollment_count'),
    ('pbf', 'Education', 'ipeds_completions', 'bachelors_completions_count', 'bachelors_completions_count'),
    ('tu_decent', 'Education', 'unavailable', 'public_college_in_state_tuition_usd', 'public_college_in_state_tuition_usd'),
    ('sdce', 'Health', 'acs1_health_insurance', 'health_insurance_coverage_pct', 'dependent_health_insurance_coverage_pct'),
    ('goodsam911', 'Health', 'nchs_mortality', 'drug_overdose_deaths_count', 'drug_overdose_deaths_count'),
    ('dui', 'Transportation', 'fars_state', 'traffic_deaths_count', 'traffic_deaths_count'),
    ('driver license compact', 'Transportation', 'fars_state', 'traffic_deaths_count', 'traffic_deaths_count'),
    ('bikehelmet', 'Transportation', 'fars_state', 'child_bicycle_deaths_count', 'child_bicycle_deaths_count'),
    ('minfine', 'Transportation', 'fars_state', 'traffic_deaths_count', 'traffic_deaths_count'),
    ('opencont', 'Transportation', 'fars_state', 'traffic_deaths_count', 'traffic_deaths_count'),
    ('damagereduce', 'Transportation', 'fars_state', 'traffic_deaths_count', 'traffic_deaths_count'),
    ('childrest', 'Transportation', 'fars_state', 'child_traffic_deaths_count', 'child_traffic_deaths_count'),
    ('gdl', 'Transportation', 'fars_state', 'teen_traffic_deaths_count', 'teen_traffic_deaths_count'),
    ('zerotol', 'Transportation', 'fars_state', 'teen_traffic_deaths_count', 'teen_traffic_deaths_count'),
    ('mlda21', 'Transportation', 'fars_state', 'teen_traffic_deaths_count', 'teen_traffic_deaths_count'),
    ('continsurance', 'Health', 'unavailable', 'contraceptive_use_pct', 'contraceptive_use_pct'),
    ('motorvoter', 'Civil Rights', 'cps_voting', 'voter_turnout', 'voter_registration'),
    ('absvot', 'Civil Rights', 'cps_voting', 'voter_turnout', 'voter_turnout'),
    ('earlvot', 'Civil Rights', 'cps_voting', 'voter_turnout', 'voter_turnout'),
    ('anyid2', 'Civil Rights', 'cps_voting', 'voter_turnout', 'voter_turnout'),
    ('anyphotoid2', 'Civil Rights', 'cps_voting', 'voter_turnout', 'voter_turnout'),
    ('minwage', 'Labor', 'qcew', 'wages', 'wages'),
    ('smokeban', 'Health', 'brfss_state_prevalence', 'current_smoking_pct', 'smoking_exposure'),
    ('prepaid', 'Education', 'ipeds_enrollment', 'undergraduate_enrollment_count', 'education_savings_participation'),
    ('colcanscr', 'Health', 'brfss_state_prevalence', 'colorectal_screening_pct', 'guideline_colorectal_screening_pct'),
    ('breastdensity', 'Health', 'brfss_state_prevalence', 'mammography_pct', 'supplemental_breast_screening'),
    ('ccb', 'Education', 'ipeds_completions', 'bachelors_completions_count', 'community_college_bachelors_completions_count'),
    ('standground', 'Law and Crime', 'nchs_mortality', 'mortality_deaths_count', 'homicide_count'),
    ('concealed carry', 'Law and Crime', 'fbi_ucr_state', 'violent_crime_count', 'violent_crime_count'),
]

LITERATURE = {
    'Labor': ('https://www.nber.org/papers/w7519', 'wage distribution target; no result values used for selection'),
    'Macroeconomics': ('https://www.nber.org/papers/w25963', 'film production and industry employment measurement'),
    'Education': ('https://www.nber.org/papers/w7422', 'college attendance and completion target definitions'),
    'Health': ('https://www.nber.org/papers/w23995', 'smoke-free law rationale: consumption and environmental smoke exposure'),
    'Transportation': ('https://www.nber.org/papers/w3831', 'drunk-driving deterrence and alcohol-control targets'),
    'Civil Rights': ('https://www.nber.org/papers/w32941', 'voting-law mechanisms and electoral participation'),
    'Law and Crime': ('https://www.nber.org/papers/w18187', 'public safety, homicide and injury outcomes'),
}
STATUTES = {
    'Labor': ('https://legislature.mi.gov/documents/mcl/archive/2014/May/mcl-Act-154-of-1964.pdf', 'Minimum Wage Law of 1964; wage floor'),
    'Macroeconomics': ('https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_105/GS_105-130.47.html', 'film production credit; qualifying production expenditure'),
    'Education': ('https://statutes.capitol.texas.gov/Docs/ED/htm/ED.54.htm', '54.6001 prepaid higher education program purpose: college attendance'),
    'Health': ('https://www.legislature.mi.gov/Bills/Bill?ObjectName=2009-HB-4377', 'PA188/2009; smoke-free workplace and food-service establishments'),
    'Transportation': ('https://flsenate.gov/Laws/Statutes/2019/316.193', '316.193 driving under influence; BAC 0.08'),
    'Civil Rights': ('https://www.justice.gov/crt/national-voter-registration-act-1993-nvra', 'NVRA purpose: voter registration and participation'),
    'Law and Crime': ('https://flsenate.gov/Laws/Statutes/2019/812.014', '812.014 theft prohibitions; policy-specific purpose remains official-audit item'),
}

def _refs(pair):
    return [{'source_ref': pair[0], 'locator': pair[1]}]

def _card(pid, domain, connector, measure, target, index, pol, metadata):
    template = json.loads((ROOT/'data/l2_pipeline/cards/run2/run2_young_adult_dependent_coverage.json').read_text())
    p = pol[pid]
    slug = ''.join(c if c.isalnum() else '_' for c in pid).strip('_')
    c = copy.deepcopy(template)
    c.update(design_id='run3_'+slug, policy_family=pid, outcome_family=measure,
             mechanism=metadata[pid]['description'])
    c['lineage'] = dict(queue_version='run3_first_order_20261008',
        screen_version='SPID_v1.2_DK_target_screen',
        source_text_ids=['spid_v1.2:'+pid, 'dk:SPID_iqt:'+str(p['dk_policy_num'])],
        legal_instrument_ids=['literature_policy:'+pid])
    c['treatment'].update(policy_id=pid, dk_policy_num=p['dk_policy_num'], predicate=c['mechanism'],
        seed=2026100800+index, source_window=p['source_window'], universe=p['universe'],
        coverage_end=str(p['source_window']['end_year'])+'-12-31')
    for s in c['sources']:
        if s.get('path'): s['path']=str((ROOT/'data/l2_pipeline/cards/run2'/s['path']).resolve())
        s['locator'] = s['locator'].replace('sdce', pid).replace('594', str(p['dk_policy_num']))
        if s['source_id']=='dk_adoption_panel': s['coverage_end']=c['treatment']['coverage_end']
    source_first, source_last = p['source_window']['start_year'], p['source_window']['end_year']
    years = [max(2011, source_first), 2024]
    filters = {}
    opts = {}
    proxy = ''
    availability='available'
    # Primary target populations that existing free parsers cannot isolate are
    # parked at S0, rather than silently labeled as covered by an aggregate.
    population_gap = pid in {'dual_enroll', 'st_wd_tran', 'dev_s1', 'dev_s4', 'ccb',
        'hostliability', 'mailreg', 'motorvoter', 'military and overseas voters act', 'breastdensity'}
    if population_gap: availability='unavailable'
    if connector=='qcew':
        years=[max(2001,source_first),2024]
        filters={'ownership':'private','industry':'722' if domain=='Labor' else '512110'}
        opts={'value_field':'annual_avg_wkly_wage' if domain=='Labor' else 'annual_avg_emplvl',
              'source_geography':'state'}
        proxy=('Private food-service weekly wages measure earnings in an exposed low-wage industry; hours and worker composition remain measurement gaps.'
               if domain=='Labor' else 'Motion-picture production employment measures local production activity; it does not count productions or expenditure directly.')
    elif connector.startswith('ipeds'):
        years=[max(1980,source_first),2024]
        filters={'sex':'all','institution_control':'public'}
        filters.update({'award_level':5,'cipcode':'99','major_number':1} if connector=='ipeds_completions' else {'level':'undergraduate'})
        if population_gap: proxy='Aggregate public-college counts cannot isolate the statutory beneficiary group; a verified institution/student linkage is required before reopening.'
    elif connector=='brfss_state_prevalence':
        question,response,label=('_RFSMOK3','RESP046','Yes')
        if measure=='colorectal_screening_pct': question,response,label=('_CRCREC','RESP192','Met USPSTF recommendations for testing')
        if measure=='mammography_pct': question,response,label=('_MAM502Y','RESP046','Yes')
        if measure=='drinking_driving_pct': question,response,label=('_DRNKDRV','RESP201','Have driven after having too much to drink')
        if measure=='binge_drinking_pct': question,response,label=('_RFBING5','RESP046','Yes')
        filters=dict(questionid=question,responseid=response,breakoutid='BO1',data_value_type='Crude Prevalence')
        opts={'response_label':label}
        if target=='smoking_exposure': proxy='Current cigarette smoking is a consumption proxy related to smoke exposure; BRFSS does not measure venue-specific secondhand smoke.'
        if target=='supplemental_breast_screening': proxy='Routine mammography cannot identify supplemental screening after density notification; this target remains unavailable.'
        if target=='underage_binge_drinking': proxy='All-adult binge prevalence cannot isolate underage drinking; age-specific published cells must be linked before reopening.'
    elif connector=='cps_voting':
        years=[max(1980,source_first+(source_first%2)),2024]
        opts={'period_years':list(range(years[0],2025,2))}
        filters={'civilian_noninstitutional':True,'citizens':True,'age_min':18}
        if population_gap: proxy='Citizen turnout cannot identify registration or military/overseas participation; these exact target cells need a separate parser.'
    elif connector=='nchs_mortality':
        years=[max(1999,source_first),2017]; filters={'cause_name':'Homicide'}
        proxy='Final state homicide deaths measure lethal violence under the self-defense law; lawful/unlawful killings cannot be separated in these totals.'
    elif connector=='fbi_ucr_state':
        years=[2010,2019]; filters={'series':'estimated_state_total'}
        if target=='retail_theft_count': proxy='Police-reported larceny includes retail theft and other theft; retail-specific coverage requires review of this explicitly broader proxy.'
    c['population']={'description':'Declared published target population; policy eligibility and proxy gaps require S1/S2 review','filters':filters}
    c['outcome'] = dict(connector=connector,measure_id=measure,outcome_id=c['design_id']+'_'+measure,
        paths=[],years=years,units=p['universe'],frequency='biennial' if connector=='cps_voting' else 'annual',
        calendar='calendar_year',population_filters=filters,source_population_filters=filters,
        outcome_unit='percentage points' if measure.endswith('_pct') or measure=='voter_turnout' else
            'US dollars per week' if measure=='wages' else 'count',**opts)
    relation='closest_proxy' if target!=measure or proxy else 'direct'
    statute=STATUTES[domain]; literature=LITERATURE[domain]
    if pid=='standground':
        statute=('https://flsenate.gov/Laws/Statutes/2019/776.012','776.012 self-defense; statutory safety rationale and duty-to-retreat scope require S1 verification')
    if pid=='colcanscr':
        statute=('https://leg.colorado.gov/bills/hb21-1100','colorectal cancer screening coverage; public statutory example, historical identity to be audited')
        literature=('https://pubmed.ncbi.nlm.nih.gov/?term=state+colorectal+cancer+screening+insurance+mandates','Locate policy-specific screening literature before S4; target screening use')
        # A search index is not a literature citation; keep this card parked.
        availability='unavailable'
    c['first_order']=dict(target_outcome=target,primary_outcome=measure,relation=relation,
        proxy_justification=proxy,statute_refs=_refs(statute),literature_refs=_refs(literature),
        availability=availability,secondary_outcomes=[])
    # Purpose examples are leads, not independently verified evidence for this
    # exact instrument. These screened candidates remain parked at S0 until the
    # hub replaces the leads with located policy-specific purpose/literature.
    c['first_order']['evidence_status']='pending_policy_specific_verification'
    if pid in {'collegesavings','prepaid'}:
        c['first_order'].update(target_outcome='education_savings_participation',
            primary_outcome='education_savings_participation',relation='direct',
            proxy_justification='',availability='unavailable')
        c['outcome']['measure_id']='education_savings_participation'
    if target=='smoking_exposure':
        c['first_order'].update(target_outcome='secondhand_smoke_exposure_pct',
            primary_outcome='secondhand_smoke_exposure_pct',relation='direct',
            proxy_justification='',availability='unavailable')
        c['outcome']['measure_id']='secondhand_smoke_exposure_pct'
    if connector=='fbi_ucr_state' or pid=='standground':
        c['first_order']['availability']='unavailable'
    c['sensitivities']=[]
    c['analysis']=dict(min_pre_periods=3,min_controls=5,min_cohorts=2,min_treated=8,
        clock='calendar_year',controls='never_treated',intake_disposition='S0_ONLY',
        spec_status='FRESH_SPEC_FORECAST_AND_FREEZE_REQUIRED',selection_expected_sign_used=False,
        source_and_data_gaps='Official purpose and policy-specific literature verification pending; observed legal source window ends '+str(source_last)+
            '; no untreated stock or reversals extrapolated. Extend verified legal coverage or revise the outcome window in a new card version before S1 readiness.',
        stop_rules=['first-order outcome unavailable','unknown legal coverage','target-population linkage unavailable','unverified purpose/literature','support failure'])
    c['automation']=dict(intake=dict(domain=domain,selection_expected_sign_used=False),
        v3_audit=dict(seed=c['treatment']['seed'],status='REQUIRED_PENDING'),
        source_type='literature_adoption_table',connector_status='Public parser available; cell and eligibility coverage not certified',
        run3=dict(stage_limit='S0',audit_packets_only=True,source_window_not_extended=True,
            available_outcome_window=years,legal_coverage_extension_needed=years[-1]>source_last,
            known_target_population_gap=population_gap,official_statute_refs_are_examples=True,
            aliases_require_reconciliation=True,forecast_reuse_allowed=False,
            outcome_values_read=0,outcome_association_calls=0))
    c['version']='v2'
    c['lineage']['queue_version']='run3_outcome_first_v6_20261008'
    c['automation']['intake']['ruling']='V6'
    if connector=='fars_state':
        years=[1975,2024]
        filters={'population':'all_traffic_fatalities'}
        if measure=='child_traffic_deaths_count':
            filters={'population':'traffic_fatalities','age_min':0,'age_max':7,'person_type':'occupant'}
        elif measure=='child_bicycle_deaths_count':
            filters={'population':'traffic_fatalities','age_min':0,'age_max':15,'person_type':'bicyclist'}
        elif measure=='teen_traffic_deaths_count':
            # Zero-tolerance statutes cover drivers under 21; GDL licensing
            # generally targets teens. Purpose job must verify exact state scope.
            filters={'population':'traffic_fatalities','age_min':15,'age_max':20 if pid!='gdl' else 17,'person_type':'driver'}
        if pid=='mlda21':
            filters={'population':'traffic_fatalities','age_min':18,'age_max':20,'person_type':'all'}
        c['population']={'description':'Road users in the declared age range; purpose verifier must confirm statutory scope and whether driver-specific linkage is needed','filters':filters}
        c['outcome'].update(years=years,population_filters=filters,source_population_filters=filters)
        c['first_order'].update(relation='direct',proxy_justification='',availability='available')
        if pid=='bikehelmet':
            c['first_order'].update(relation='closest_proxy',proxy_justification='Fatal motor-vehicle collisions involving bicyclists under age 16 proxy the safety target; bicycle-only injuries are absent and state-specific helmet ages require purpose verification.')
    elif connector=='acs1_health_insurance':
        filters={'age_group':'19-25','sex':'all'}
        c['population']={'description':'Civilian noninstitutional young adults ages 19–25','filters':filters}
        c['outcome'].update(years=[2008,2024],population_filters=filters,source_population_filters=filters)
        c['first_order'].update(relation='closest_proxy',availability='available',
            proxy_justification='Any-insurance coverage for ages 19–25 includes nondependent coverage; purpose verification must approve the population and measurement proxy.')
    elif measure.startswith('drug_overdose'):
        filters={'sex':'Both Sexes','age':'All Ages','race':'All Races-All Origins'}
        c['population']={'description':'All residents; final overdose deaths','filters':filters}
        c['outcome'].update(years=[1999,2024],population_filters=filters,source_population_filters=filters)
        c['first_order'].update(relation='direct',availability='available',proxy_justification='')
    elif connector=='brfss_state_prevalence' and target=='guideline_colorectal_screening_pct':
        c['first_order']['proxy_justification']='Published colorectal-screening prevalence is a guideline-use proxy; historical question and eligible-age definitions require verification.'
    elif connector=='cps_voting':
        c['outcome']['years']=[1980,2024]
        c['outcome']['period_years']=list(range(1980,2025,2))
    c['automation']['run3'].update(official_statute_refs_are_examples=True,
        target_mapping_provisional_until_purpose_acceptance=True)
    c['analysis']['source_and_data_gaps']='Purpose/population verification and S1 stock, dates, reversals and controls are pending; V6 uses the intersection of supplied legal audit scope and outcome coverage.'
    from .coverage import coverage_bounded_card
    c=coverage_bounded_card(c)
    c['automation']['run3']['available_outcome_window']=c['outcome'].get('years',[])
    c['automation']['run3']['legal_coverage_extension_needed']=False
    errors=validate_card(c)
    if errors: raise ValueError('; '.join(errors))
    from .connectors.rates import prospective_card
    return prospective_card(c)

def _audit_inputs(ctx, p):
    """Lock supplied adoption tuples for audit only; do not certify study zeros."""
    from .stages.s1_treatment import audit_sample
    source=ROOT/'data/l3_pooled/downloads/DVKim_SPID_iqt.dta'
    records=[]
    for unit,a in sorted(p['states'].items()):
        records.append(dict(unit=unit,status='never_treated' if a['adopt_year'] is None else 'treated',
            start_period=a['adopt_year'],mechanism=ctx.card.mechanism,
            locator=f'DK policy={p["policy_id"]}; state={unit}; supplied {a["start_year"]}-{a["end_year"]}',
            source_window={'start_year':a['start_year'],'end_year':a['end_year']},outside_source_window='unknown'))
    projection=ensure_once(ctx.design_dir/'LITERATURE_SOURCE_PROJECTION.json',dict(p,
        source_path=str(source),source_sha256=sha(source),audit_only=True,outcome_values_read=0,
        source_window_not_extended=True))
    imported=ensure_once(ctx.design_dir/'LITERATURE_IMPORT.json',dict(records=records,source_type='literature_adoption_table',
        source_sha256=sha(source),published_values_preserved=True,s1_ready=False,audit_only=True))
    lock=ensure_once(ctx.design_dir/'LITERATURE_IMPORT_LOCK.json',dict(files={str(imported):sha(imported),str(projection):sha(projection)},
        source_path=str(source),source_sha256=sha(source),card_path=str(ctx.card.path),card_sha256=sha(ctx.card.path),no_coder_originals_fabricated=True))
    selection=ensure_once(ctx.design_dir/'AUDIT_SELECTION.json',dict(seed=ctx.card.payload['treatment']['seed'],
        sample=audit_sample(records,ctx.card.payload['treatment']['seed']),source_sha256=sha(source),
        treated_universe=sorted(r['unit'] for r in records if r['status']=='treated'),source_type='literature_adoption_table',import_sha256=sha(imported)))
    return records,projection,imported,lock,selection

def _candidate_record(value, path, jobs_root, pol, *, prior_card_path=None):
    from .agents.purpose import emit_purpose_verification
    from .literature_adoption_intake import emit_auditor
    from .first_order import target_mapping
    ctx=StageContext(load_card(path),jobs_root/'work',ROOT)
    result=run_stage(ctx,'S0')
    if result.disposition==Disposition.FAILED:
        raise ValueError('S0 failed: '+value['design_id']+': '+result.reason)
    purpose=emit_purpose_verification(ctx,jobs_root)
    mapping=target_mapping(value)
    pid=value['treatment'].get('policy_id')
    audit=None
    if mapping['mapped'] and pid in pol:
        audit=emit_auditor(ctx,*_audit_inputs(ctx,pol[pid]),jobs_root)
    return dict(design_id=value['design_id'],version=value['version'],policy_id=pid,
        domain=value.get('automation',{}).get('intake',{}).get('domain','Purpose lens inferred from target'),
        card_path=str(path.relative_to(ROOT)),card_sha256=sha(path),gate=first_order_gate(value),
        mapping=mapping,s0_disposition=result.disposition,s0_reason=result.reason,
        purpose_job_path=purpose.job_path,job_path=audit.job_path if audit else None,
        prior_card_path=str(prior_card_path.relative_to(ROOT)) if prior_card_path else None,
        source_window=value['treatment'].get('source_window'),outcome_window=value['outcome'].get('years'),
        coverage_window=value['automation']['coverage_window'],legal_coverage_extension_needed=False)


def prepare(audit_attempt='v6_final_verified20261008'):
    from .literature_adoption_intake import table_map
    from .agents.jobs import _safe_component
    from .coverage import coverage_bounded_card
    from .first_order import target_mapping
    from .stages.s0_intake import rank_queue_rows
    audit_attempt=audit_attempt or 'v6_final_verified20261008'
    _safe_component(audit_attempt)
    jobs_root=JOBS/audit_attempt
    meta_root=META/audit_attempt
    card_root=CARDS/audit_attempt
    pol=table_map()
    with (ROOT/'data/l3_pooled/downloads/SPID_v1.2_policies.tab').open() as handle:
        metadata={r['policy']:r for r in csv.DictReader(handle,delimiter='\t')}
    candidates=[_card(*row,index,pol,metadata) for index,row in enumerate(CATALOG,1)]
    ranked=rank_queue_rows(candidates)
    records=[]
    for value in ranked[:30]:
        value['automation']['intake'].update(priority=value.pop('priority'),rank=value.pop('rank'))
        path=ensure_once(card_root/(value['design_id']+'.json'),value)
        records.append(_candidate_record(value,path,jobs_root,pol))
    fo_records=[]
    for original in sorted((ROOT/'data/l2_pipeline/cards/first_order').glob('*_fo.json')):
        value=copy.deepcopy(load_card(original).payload)
        value['version']='v2'
        value.setdefault('automation',{}).setdefault('intake',{})['ruling']='V6'
        value['automation']['v6_reassessment']=dict(prior_card_path=str(original.relative_to(ROOT)),
            prior_card_sha256=sha(original),prior_results_read=False,prior_forecasts_read=False,
            fresh_forecasts_required=True,fresh_freeze_required=True,execution_authorized=False)
        # Existing public-roster CCB aggregate cannot isolate authorized colleges.
        if 'community_college' in value['first_order']['target_outcome'] and not value['outcome'].get('institution_ids'):
            value['automation']['v6_reassessment']['target_population_gap']=True
        value['first_order']['evidence_status']='pending_policy_specific_verification'
        value=coverage_bounded_card(value)
        mapped=target_mapping(value)['mapped']
        value['first_order']['availability']='available' if mapped else 'unavailable'
        errors=validate_card(value)
        if errors: raise ValueError('; '.join(errors))
        path=ensure_once(card_root/'first_order'/(value['design_id']+'.json'),value)
        fo_records.append(_candidate_record(value,path,jobs_root,pol,prior_card_path=original))
    if len(fo_records)!=19: raise ValueError('Expected exactly 19 original _fo cards')
    all_records=records+fo_records
    selected=Counter(r['domain'] for r in records)
    report=dict(schema='l2_run3_s0_plan_v6',ruling='V6',cards=len(records),first_order_cards=len(fo_records),
        distinct_spid_policy_ids=len({r['policy_id'] for r in records}),domains=dict(selected),
        connector_mapped=sum(r['mapping']['mapped'] for r in records),
        mapped_with_bounded_window=sum(r['mapping']['mapped'] and not r['coverage_window']['parked'] for r in records),
        first_order_connector_mapped=sum(r['mapping']['mapped'] for r in fo_records),
        first_order_bounded_windows=sum(not r['coverage_window']['parked'] for r in fo_records),
        s0_dispositions=dict(Counter(r['s0_disposition'] for r in records)),
        first_order_s0_dispositions=dict(Counter(r['s0_disposition'] for r in fo_records)),
        purpose_jobs=len(all_records),auditor_jobs=sum(r['job_path'] is not None for r in all_records),
        source_type='literature_adoption_table',source_windows_not_extended=True,
        selection_rule='connector-target mapping first, then declared history/base priority, then stable policy/design ID',
        purpose_verification_pending=len(all_records),model_calls=0,dispatched=False,
        actual_data_estimation_calls=0,forecasts_created=0,freezes_created=0,s1_readiness_granted=False,
        new_downloads=[],audit_attempt=audit_attempt,records=records,first_order_records=fo_records)
    ensure_once(meta_root/'PLAN.json',report)
    return {k:v for k,v in report.items() if k not in {'records','first_order_records'}}


def dry_run(audit_attempt='v6_final_verified20261008'):
    from .agents.jobs import load_job, _safe_component
    from .first_order import target_mapping
    audit_attempt=audit_attempt or 'v6_final_verified20261008'
    _safe_component(audit_attempt)
    jobs_root=JOBS/audit_attempt
    meta_root=META/audit_attempt
    plan=json.loads((meta_root/'PLAN.json').read_text())
    issues=[]
    records=plan['records']+plan['first_order_records']
    for r in records:
        path=ROOT/r['card_path'];card=load_card(path)
        if (sha(path)!=r['card_sha256'] or first_order_gate(card.payload)!=r['gate'] or
                target_mapping(card.payload)!=r['mapping']):
            issues.append('Card/gate/mapping changed: '+r['design_id'])
        window=r['coverage_window']
        if not window['parked'] and window['effective_years'][-1]>window['legal_bounds']['end_year']:
            issues.append('Window exceeds legal audit scope: '+r['design_id'])
        for field in ('purpose_job_path','job_path'):
            if not r[field]: continue
            try:
                job=load_job(Path(r[field]))
                if job.design_id!=r['design_id'] or job.design_version!=r['version']:
                    issues.append('Job identity binding: '+r['design_id'])
                if field=='job_path' and (not r['mapping']['mapped'] or len(job.contract['sample'])!=10):
                    issues.append('Audit mapping/sample: '+r['design_id'])
                for p in job.inputs:
                    if sha(Path(p['path']))!=p['sha256']: issues.append('Input changed: '+p['path'])
            except (OSError,ValueError) as exc:
                issues.append('Packet '+r['design_id']+': '+str(exc))
        root=jobs_root/'work'/r['design_id']/r['version']
        if any((root/n).exists() for n in ('TREATMENT.json','PREDICTIONS_LOCK.json','FREEZE_PREP.json','FREEZE_FINAL.json','RESULT.json')):
            issues.append('S0-only scope exceeded: '+r['design_id'])
    report=dict(schema='l2_run3_dry_run_v6',passed=not issues,issues=issues,cards=plan['cards'],
        first_order_cards=plan['first_order_cards'],purpose_jobs=plan['purpose_jobs'],auditor_jobs=plan['auditor_jobs'],
        connector_mapped=plan['connector_mapped'],s0_dispositions=plan['s0_dispositions'],model_calls=0,
        dispatched=False,actual_data_estimation_calls=0,source_windows_not_extended=True)
    ensure_once(meta_root/'DRY_RUN.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action',choices=['prepare','dry-run','dispatch-s1'],required=True)
    parser.add_argument('--audit-attempt',default='v6_final_verified20261008',help='New immutable V6 packet workspace; earlier cards/jobs remain archived')
    parser.add_argument('--acceptance', type=Path, default=JOBS/'S0_ACCEPTANCE_v2.json')
    parser.add_argument('--acceptance-sha256', help='Exact reviewed acceptance report SHA-256')
    parser.add_argument('--dry-run', action='store_true', help='For dispatch-s1: validate selected packs without model calls')
    args=parser.parse_args(argv)
    report=(dispatch_accepted_s1(args.acceptance,args.acceptance_sha256,dry_run=args.dry_run)
            if args.action=='dispatch-s1' else prepare(args.audit_attempt)
            if args.action=='prepare' else dry_run(args.audit_attempt))
    print(json.dumps(report,indent=2))
    return 1 if report.get('passed') is False else 0

if __name__=='__main__': raise SystemExit(main())
