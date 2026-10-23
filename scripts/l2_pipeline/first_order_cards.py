"""Create new, answer-free first-order registrations from completed intake cards.

Only CARD/TERMINAL and official legal-audit metadata are permitted inputs. No
result, prediction, approval, support mask, or calibration bytes are copied.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'data/l2_pipeline/cards/first_order'
ROOTS = [REPO/'data/l2_pipeline/jobs/run2/postdispatch/work',
         REPO/'data/l2_pipeline/jobs/run1/_v5/work',
         REPO/'data/l2_pipeline/jobs/run2/postdispatch/wave2_nurse_work',
         REPO/'data/l2_pipeline/acceptance/batch4']

# target, primary, connector, filters, literature URL/locator, gap.
TARGETS = {
 'pregnancy_accommodation': ('pregnancy_workplace_accommodation_receipt_pct','pregnancy_workplace_accommodation_receipt_pct','unavailable',
  {'sex':'female','pregnant_or_recent_childbirth':True,'employed_private_worker':True}, 'https://pmc.ncbi.nlm.nih.gov/articles/PMC12315298/',
  'Sociodemographic and occupational factors influencing pregnant workers’ awareness and utilization of the NYC Pregnant Workers Fairness Act (2025); abstract and Methods, accommodation utilization among pregnant workers',
  'No public state-year connector measures accommodation receipt or pregnant-worker retention; aggregate employment of all women ages 20–44 does not identify the statutory target population.'),
 'run2_contraception_insurance': ('contraceptive_use_pct','contraceptive_use_pct','unavailable',
  {'sex':'female','age_min':18,'age_max':44}, 'https://pubmed.ncbi.nlm.nih.gov/24433431/',
  'Changes in state prescription contraceptive mandates for insurers; abstract Methods, contraception use among women at risk of unintended pregnancy, BRFSS 1998–2010',
  'Comparable optional-module contraceptive-use cells for the certified 1996–2010 source window are not implemented; natality births cannot identify contraceptive use or coverage.'),
 'run2_gender_identity_employment': ('gender_identity_employment_discrimination_pct','gender_identity_employment_discrimination_pct','unavailable',
  {'population':'transgender_and_gender_nonconforming_working_age'}, 'https://www.sciencedirect.com/science/article/pii/S0927537120300646',
  'Hiring Discrimination Against Transgender People: Evidence from a Field Experiment; abstract, target-specific hiring outcomes',
  'CPS aggregate adult employment has no consistent gender-identity indicator in the certified 1994–2014 window; no free state-year target-specific discrimination connector.'),
 'run2_colorectal_screening_coverage': ('guideline_colorectal_screening_pct','colorectal_screening_pct','brfss_state_prevalence',
  {'questionid':'_COL10YR','responseid':'RESP207','breakoutid':'BO1','data_value_type':'Crude Prevalence'}, 'https://pubmed.ncbi.nlm.nih.gov/30941767/',
  'Changes in screening colonoscopy following Medicare reimbursement and cost-sharing changes; abstract Methods, screening rates and state coverage mandates',
  'BRFSS public prevalence connector begins after the existing DK certified window ends in 2010; obtain historical BRFSS screening cells and renew source coverage before S1–S4.'),
 'run2_electronic_waste_recycling': ('electronic_waste_collection_kg_per_capita','electronic_waste_collection_kg_per_capita','unavailable',
  {'material':'covered_household_electronics'}, 'https://www.sciencedirect.com/science/article/abs/pii/S0921344919302940',
  'Towards comprehensive e-waste legislation in the United States (2019); abstract and Dependent variable, annual state collection rates',
  'No comparable free state-year electronic-waste collection/recycling connector across untreated and treated states; QCEW waste jobs are downstream.'),
 'run2_breast_density_notification': ('breast_density_awareness_pct','breast_density_awareness_pct','unavailable',
  {'sex':'female','recent_mammogram':True}, 'https://pubmed.ncbi.nlm.nih.gov/30287178/',
  'The Impact of Breast Density Reporting Laws on Women’s Awareness of Density-Associated Risks (2019); abstract Objective and Methods',
  'Standard BRFSS mammography prevalence cannot measure receipt/understanding of breast-density notification or supplementary imaging; no comparable state-year target connector.'),
 'run2_young_adult_dependent_coverage': ('dependent_health_insurance_coverage_pct','health_insurance_coverage_pct','acs1_health_insurance',
  {'age_group':'19-25','sex':'all'}, 'https://www.nber.org/papers/w18254',
  'Cantor, Monheit, DeLia and Lloyd (2012), The Role of Federal and State Dependent Coverage Eligibility Policies on the Health Insurance Status of Young Adults; abstract, insurance-status target',
  'ACS insurance begins 2008 while the existing DK certified source window ends 2008: no clean pre-period overlap; refresh adoption coverage and handle common federal ACA onset prospectively.'),
 'run2_community_college_bachelor': ('community_college_bachelors_completions_count','bachelors_completions_count','ipeds_completions',
  {'award_level':5,'cipcode':'99','major_number':1,'sex':'all','institution_control':'public'}, 'https://eric.ed.gov/?id=ED608173',
  'Meza (2020), Community College Baccalaureate Degree Completion in Washington, Data Note 4; abstract, CCB completion outcome',
  'All public institutions are an aggregate proxy; S1 must identify authorized community colleges by stable UNITID across sector changes and S2 must bind historical vintages overlapping 1994–2006.'),
 'run2_decentralized_tuition': ('public_college_in_state_tuition_usd','public_college_in_state_tuition_usd','unavailable',
  {'institution_control':'public','residency':'in_state'}, 'https://www.rsfjournal.org/content/2/1/112',
  'Pricing and University Autonomy: Tuition Deregulation in Texas (2016); abstract and data, in-state tuition/fees after authority decentralization',
  'IPEDS completions/enrollment do not measure tuition prices; add and certify an IPEDS institutional-characteristics tuition connector before proceeding.'),
 'run2_meth_precursor_restrictions': ('methamphetamine_supply_availability','methamphetamine_lab_incidents_count','unavailable',
  {'drug':'methamphetamine','incident':'clandestine_lab'}, 'https://pmc.ncbi.nlm.nih.gov/articles/PMC2883188/',
  'The War on Drugs: Methamphetamine, Public Health, and Crime (2009); Methamphetamine Precursor Regulation and Supply, direct supply mechanism',
  'All-drug overdose mortality is downstream and cannot isolate precursor access or methamphetamine production; no free certified statewide lab/supply connector in the existing set.'),
 'run1_ems_compact': ('ems_interstate_practice_count','ems_interstate_practice_count','unavailable',
  {'occupation':'ems_clinicians','practice':'interstate'}, 'https://www.nber.org/papers/w24107',
  'Johnson and Kleiner (2017), Is Occupational Licensing a Barrier to Interstate Migration?; abstract, interstate movement and reciprocity; general mechanism evidence, not an EMS-specific effect benchmark',
  'No free comparable state-year EMS interstate-practice/privilege-use connector; ambulance-industry jobs do not identify interstate mobility.'),
 'run1_pt_compact': ('pt_interstate_practice_count','pt_compact_privileges_count','unavailable',
  {'occupation':'physical_therapists_and_assistants','practice':'interstate'}, 'https://www.nber.org/papers/w24107',
  'Johnson and Kleiner (2017), Is Occupational Licensing a Barrier to Interstate Migration?; abstract, interstate movement and reciprocity; general mechanism evidence, not a PT-specific effect benchmark',
  'PT Commission annual reports contain privileges, but no certified comparable state-year historical connector/crosswalk; physician/therapy-office jobs do not identify interstate practice.'),
}

PROXIES = {
 'run2_colorectal_screening_coverage':'BRFSS adults ages 50–75 reporting colonoscopy within ten years measure a guideline screening modality; other recommended screening modalities are omitted.',
 'run2_young_adult_dependent_coverage':'ACS civil noninstitutional ages 19–25 any-insurance coverage is the closest public summary proxy; it includes nondependent coverage and excludes age 18.',
 'run2_community_college_bachelor':'Public-institution bachelor completions measure credential production in the affected state; the eligible-community-college UNITID crosswalk must narrow the proxy before freeze.',
 'run2_meth_precursor_restrictions':'Detected methamphetamine laboratories proxy domestic production availability; enforcement/reporting coverage must be fixed before a future registration.',
 'run1_pt_compact':'Issued remote-state compact privileges proxy authorized interstate practice; they do not prove service use and require a prospective utilization limitation.',
}

def _read(path: Path) -> dict:
    original = path.parent.name in {'run1','run2','batch4'} and path.stem in TARGETS
    if path.name not in {'CARD.json','TERMINAL.json','OFFICIAL_AUDIT.json'} and not original:
        raise ValueError('Input is outside the metadata whitelist')
    return json.loads(path.read_text())

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build() -> dict:
    from .cards import validate_card
    from .first_order import first_order_gate
    OUT.mkdir(parents=True, exist_ok=True)
    inventory=[]; cards=[]; observed=set()
    for root in ROOTS:
        for terminal in sorted(root.glob('*/v*/TERMINAL.json')):
            if _read(terminal).get('status') != 'COMPLETED': continue
            registered = _read(terminal.with_name('CARD.json'))
            old_id=registered['design_id']
            prior_outcome_id=registered['outcome'].get('outcome_id', old_id+'_registered_employment_percentage')
            if old_id in observed: raise ValueError('Duplicate completed design: '+old_id)
            observed.add(old_id)
            inventory.append({'design_id':old_id,'terminal_metadata':str(terminal.relative_to(REPO)),
                              'registered_outcome_id':prior_outcome_id,
                              'replacement_required':old_id in TARGETS})
            if old_id not in TARGETS: continue
            batch = 'batch4' if old_id=='pregnancy_accommodation' else old_id.split('_')[0]
            origin = REPO/f'data/l2_pipeline/cards/{batch}/{old_id}.json'
            base=_read(origin)
            target,primary,connector,filters,lit_url,lit_locator,gap=TARGETS[old_id]
            card={k:copy.deepcopy(base[k]) for k in ('schema_version','policy_family','mechanism','track','unit','sources','lineage')}
            card.update(design_id=old_id+'_fo',version='v1',outcome_family=primary,
                        population={'description':'Direct statutory target population; source definition and eligibility require fresh S1–S3 review.','filters':filters})
            for source in card['sources']:
                if source.get('path'): source['path']=str((origin.parent/source['path']).resolve())
            card['lineage']['queue_version']='first_order_reintake_20261008'
            card['mechanism']='First-order target: '+target+'. '+base['mechanism']
            card['outcome']={'connector':connector,'outcome_id':card['design_id']+'_'+primary,'measure_id':primary,
                             'value_field':primary,'frequency':'annual','calendar':'calendar_year','paths':[],
                             'population_filters':filters,'source_population_filters':filters,
                             'units':base['outcome']['units'],'years':base['outcome']['years'],
                             'outcome_unit':'percent' if primary.endswith('_pct') else 'count' if primary.endswith('_count') else 'source original unit; certification pending'}
            if old_id=='run2_colorectal_screening_coverage': card['outcome']['response_label']='Received a colonoscopy'
            # Intake treatment fields only; numerical declarations/masks are never reused.
            card['treatment']=copy.deepcopy(base['treatment'])
            for stale in ('full_year_declaration','interval_path','support_metadata_path','support_metadata_sha256','registration'):
                card['treatment'].pop(stale,None)
            if old_id=='pregnancy_accommodation':
                statute_refs=[{'source_ref':'https://leg.colorado.gov/bills/hb16-1438',
                               'locator':'Colorado HB16-1438; signed act June 1, 2016; legislative purpose: reasonable employer accommodations for pregnancy-related conditions'}]
            else:
                audit=_read(terminal.with_name('OFFICIAL_AUDIT.json'))
                located=[r for r in audit['checks'] if r.get('status')=='match' and r.get('official_locator')]
                legal=located[0] if located else audit['checks'][0]
                statute_refs=[{'source_ref':str(terminal.with_name('OFFICIAL_AUDIT.json').relative_to(REPO)),
                               'locator':legal['official_locator']}]
            # A parser alone is insufficient when the eligible population or
            # certified legal/outcome time overlap has not yet been linked.
            available=False
            card['first_order']={'target_outcome':target,'primary_outcome':primary,
                'relation':'closest_proxy' if old_id in PROXIES else 'direct',
                'proxy_justification':PROXIES.get(old_id,''),
                'statute_refs':statute_refs,
                'literature_refs':[{'source_ref':lit_url,'locator':lit_locator}],
                'availability':'available' if available else 'unavailable','secondary_outcomes':[]}
            card['analysis']={'clock':'calendar_year','controls':'never_treated','min_cohorts':2,'min_controls':5,
                'min_pre_periods':3,'min_treated':8,'intake_disposition':'PARKED_AUDIT' if available else 'PARKED_DATA',
                'spec_status':'FRESH_SPEC_FORECASTS_FREEZE_REQUIRED','selection_expected_sign_used':False,
                'source_and_data_gaps':gap,'window_status':'Existing source window retained provisionally; no extrapolation or inherited support certification.'}
            card['sensitivities']=[]
            card['automation']={'connector_status':'S2 construction pending' if available else 'first-order outcome unavailable',
                'first_order_reintake':{'prior_design_id':old_id,'prior_registered_outcome_id':prior_outcome_id,
                    'prior_registration_ran':True,'prior_outcome_role':'second_order_or_target_population_mismatch',
                    'disclosure':'A second-order registration for the same policy already ran and remains valid for its registered outcome; its result values were not inspected for this new preparation.',
                    'prior_result_values_read':False,'required_stages':['S0','S1','S2','S3','S4','S5','S6','S7'],
                    'fresh_forecasts_required':True,'fresh_freeze_required':True,'no_inherited_forecasts_approvals_calibrations':True,
                    'gate_availability_reason':gap,'target_evidence_review':'Existing official purpose locator plus primary literature; independently verify scope before S4.'},
                'source_check':{'skill':'detecting-indirect-prompt-injection','access':'web_search_tool_results_and_existing_legal_metadata',
                    'separate_local_scan_performed':False,'suspicious_instructions_observed':False,'authority':'untrusted_data'}}
            from .connectors.rates import prospective_card
            card = prospective_card(card)
            errors=validate_card(card)
            if errors: raise ValueError(card['design_id']+': '+'; '.join(errors))
            path=OUT/(card['design_id']+'.json')
            content=json.dumps(card,indent=2,sort_keys=True,ensure_ascii=False)+'\n'
            if path.exists() and path.read_text()!=content: raise ValueError('Refuse to overwrite existing new registration: '+str(path))
            path.write_text(content)
            cards.append({'design_id':card['design_id'],'path':str(path.relative_to(REPO)),
                'sha256':_sha(path),'prior_design_id':old_id,'first_order_gate':first_order_gate(card),'data_gaps':gap})
    missing=set(TARGETS)-observed
    if missing: raise ValueError('Expected completed metadata missing: '+','.join(sorted(missing)))
    report={'schema':'l2_first_order_reintake_v1','completed_metadata_inspected':len(inventory),
        'new_cards':len(cards),'same_policy_originals_preserved':True,'registered_outcome_values_read':0,
        'actual_data_estimation_calls':0,'forecasts_imported':0,'approvals_inherited':0,
        'new_downloads':[],'inventory':inventory,'cards':cards,
        'full_pipeline_status':'Fresh S0–S7 required; no S1 readiness, forecasts, approval or freeze granted by this intake.'}
    (OUT/'_meta').mkdir(exist_ok=True)
    (OUT/'_meta'/'MANIFEST.json').write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False)+'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.parse_args()
    result=build()
    print(json.dumps({k:result[k] for k in ('completed_metadata_inspected','new_cards','registered_outcome_values_read','actual_data_estimation_calls','new_downloads')}))
