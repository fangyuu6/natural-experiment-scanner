"""Metadata-only legacy completion projection and first-order re-intake drafts.

This helper reads only an exact whitelist of status summaries, registration
specifications and legal identity metadata. It never opens per-case results,
outcome panels, calibration records or forecast ledgers. The fixed replacement
targets come from the hub task. Real generation requires a clean author's
explicit attestation; writing this helper does not grant that attestation.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
from typing import Any

from .cards import validate_card, load_card
from .contracts import StageContext
from .storage import encoded, ensure_once, public_input, sha

ROOT = Path(__file__).resolve().parents[2]
CARD_ROOT = Path('data/l2_pipeline/cards/first_order')
META_ROOT = CARD_ROOT / '_meta'
WORK_ROOT = Path('data/l2_pipeline/jobs/first_order/legacy_work')
SUMMARIES = {
    'batch1':'scripts/l2_batch1/results/batch1_20261007_dev001/SUMMARY.json',
    'batch2':'scripts/l2_batch2/results/batch2_20261007_final/SUMMARY.json',
    'batch3':'scripts/l2_batch3/results/batch3_20261007_final/SUMMARY.json',
}
SPECS = {
    'pfml':'scripts/l2_batch1/specs/pfml_v1_2.md',
    'eitc_first_refundable':'scripts/l2_batch1/specs/eitc_first_refundable_v1_1_1.md',
    'automatic_voter':'scripts/l2_batch2/specs/automatic_voter_v1_1.json',
    'btb_private':'scripts/l2_batch2/specs/btb_private_v1_1.json',
    'medical_cannabis':'scripts/l2_batch2/specs/medical_cannabis_v1_1.json',
    'film_refundable':'scripts/l2_batch2/specs/film_refundable_v1_1.json',
    'public_btb':'scripts/l2_batch3/specs/public_btb_v1.json',
    'mw_indexing':'scripts/l2_batch3/specs/mw_indexing_v1.json',
}
LEGAL = {
    'batch2':'scratchpad/l2_batch2/v3_review/LEGAL_REVIEW.json',
    'public_btb':'scratchpad/l2_batch3/reconciled/public_btb/LEGAL_REVIEW.json',
    'mw_indexing':'scratchpad/l2_batch3/reconciled/mw_indexing/LEGAL_REVIEW.json',
}
WHITELIST = frozenset(SUMMARIES.values()) | frozenset(SPECS.values()) | frozenset(LEGAL.values())

# Hub-fixed targets, before this helper's author encountered incidental
# documentation labels. Clean card author must independently verify evidence.
TARGETS = {
    'pfml': {'target':'paid_family_leave_uptake_pct', 'primary':'paid_family_leave_uptake_pct',
        'filters':{'covered_private_worker':True,'family_leave_eligible':True}, 'years':[1994,2024],
        'statute_url':'https://paidleave.wa.gov/history/',
        'statute_locator':'Washington SB 5975 program history; mandatory benefits for qualifying family leave; claims/benefit launch distinguished from enactment.',
        'literature_url':'https://www.nber.org/papers/w17715',
        'literature_locator':'Rossin-Slater, Ruhm and Waldfogel; leave-taking outcome and March CPS design, as verified in DID_PAID_FAMILY_LEAVE.md.',
        'gap':'The registered basic-CPS employment connector does not measure paid family-leave uptake among eligible covered workers; harmonized leave reasons, eligibility and program claims coverage are not certified.'},
    'eitc_first_refundable': {'target':'state_refundable_eitc_receipt_pct','primary':'state_refundable_eitc_receipt_pct',
        'filters':{'state_eitc_eligible_tax_unit':True}, 'years':[2000,2024],
        'statute_url':'https://www.legis.delaware.gov/SessionLaws/Chapter?id=36243',
        'statute_locator':'Delaware enacted law approved August 10, 2021; election of a refundable 4.5% federal-EITC amount; receipt/tax-year clocks require fresh review.',
        'literature_url':'https://www.nber.org/papers/w11729',
        'literature_locator':'Eissa and Hoynes EITC review; refundable credit/tax-transfer mechanism; verified literature reference in DID_EARNED_INCOME_TAX_CREDIT.md.',
        'gap':'Free IRS federal-EITC aggregates do not measure state refundable-credit receipt; no certified public state-year eligible-tax-unit receipt or after-tax-income connector is available.'},
    'automatic_voter': {'target':'voter_registration_pct','primary':'voter_registration_pct',
        'filters':{'citizen':True,'age_min':18,'voter_registration_eligible':True}, 'years':[1994,2024],
        'literature_url':'https://www.ncsl.org/elections-and-campaigns/automatic-voter-registration',
        'literature_locator':'Policy review of automatic-registration mechanisms; distinguish registration from election turnout.',
        'gap':'The existing published CPS voting connector measures reported turnout; registered-to-vote columns, eligible-citizen denominator and consistent state/election-wave coverage need a new certified public adapter.'},
    'btb_private': {'target':'record_holder_private_hiring_pct','primary':'record_holder_private_hiring_pct',
        'filters':{'criminal_record_holder':True,'private_job_applicant':True}, 'years':[1994,2020],
        'literature_url':'https://www.nelp.org/app/uploads/2021/10/Ban-the-Box-Fair-Chance-State-and-Local-Guide-Oct-2021.pdf',
        'literature_locator':'NELP October 2021 fair-chance guide; initial-application criminal-history removal, private-employer scope and affected applicants.',
        'gap':'CPS employment for all men ages 25–54 does not identify criminal-record holders or covered private applicants; no certified free state-year target-population hiring connector is available.'},
    'medical_cannabis': {'target':'authorized_medical_cannabis_patient_use_pct','primary':'authorized_medical_cannabis_patient_use_pct',
        'filters':{'qualifying_medical_cannabis_patient':True}, 'years':[1994,2020],
        'literature_url':'https://www.nber.org/papers/w29296',
        'literature_locator':'The Public Health Effects of Legalizing Marijuana; medical-law mechanism and patient/cannabis-use outcomes; verify patient-use population before specification.',
        'gap':'Basic CPS employment does not identify qualifying medical patients or authorized use; no harmonized free state-year patient-use/registration series with certified program-operation timing is linked.'},
    'public_btb': {'target':'record_holder_public_hiring_pct','primary':'record_holder_public_hiring_pct',
        'filters':{'criminal_record_holder':True,'state_government_job_applicant':True}, 'years':[1994,2020],
        'literature_url':'https://www.nelp.org/app/uploads/2021/10/Ban-the-Box-Fair-Chance-State-and-Local-Guide-Oct-2021.pdf',
        'literature_locator':'NELP October 2021 fair-chance guide; state-government applicant scope and criminal-history inquiry timing.',
        'gap':'CPS resident employment across all sectors does not identify record-holder state-government applicants; no certified public state-year hiring series for this population is available.'},
    'mw_indexing': {'target':'wages','primary':'wages','relation':'closest_proxy','connector':'qcew_annual',
        'filters':{'ownership':'private','industry':'722'}, 'years':[1994,2014],
        'literature_url':'https://www.nber.org/papers/w4509',
        'literature_locator':'Card and Krueger minimum-wage study; fast-food wage measurement as context for the low-wage-sector proxy; independent scope/measurement review required.',
        'justification':'Private food-service QCEW weekly wages proxy the indexed wage floor, but mix hours and composition; covered-worker alignment, inflation deflation and historical support must be fixed before freeze.',
        'gap':'QCEW wage parser exists, but this intake has no certified covered-worker alignment, deflator, 1994–2014 public-file coverage or independent support; nominal sector weekly pay cannot be declared a ready real-earnings outcome.'},
}


def read_metadata(repo: Path, relative: str) -> Any:
    if relative not in WHITELIST:
        raise ValueError('Refuse input outside exact legacy metadata whitelist')
    path = public_input(repo / relative)
    if not path.resolve().is_relative_to(repo.resolve()):
        raise ValueError('Legacy metadata path escapes repository')
    return json.loads(path.read_text()) if path.suffix == '.json' else path.read_text()


def completion_projection(repo: Path = ROOT) -> dict:
    """Project statuses only; reject a summary that puts values in statuses."""
    cases=[]; sources=[]
    for batch, relative in SUMMARIES.items():
        value=read_metadata(repo,relative)
        allowed={'actual_data_estimation_calls','approval','case_order','case_statuses','finished_at_utc','started_at_utc','run_id','status'}
        if set(value)-allowed:
            raise ValueError('Summary contains unsupported fields; use status-only completion metadata')
        statuses=value.get('case_statuses')
        if value.get('status')!='COMPLETED' or not isinstance(statuses,dict):
            raise ValueError('Legacy summary lacks completed case-status metadata')
        if any(not isinstance(k,str) or not isinstance(v,str) for k,v in statuses.items()):
            raise ValueError('Summary case_statuses must contain status strings only')
        sources.append({'batch':batch,'path':relative,'sha256':sha(repo/relative),'run_id':value.get('run_id'),'status':value['status']})
        for key,status in sorted(statuses.items()):
            if status!='PUBLISHABLE_EXAMPLE': continue
            if key not in SPECS: raise ValueError('Unreviewed completed legacy case: '+key)
            cases.append({'batch':batch,'case_key':key,'prior_case_status':status,
                'summary_path':relative,'summary_sha256':sha(repo/relative),'spec_path':SPECS[key],
                'replacement_required':key in TARGETS,
                'target_mapping_source':'Hub target mappings fixed before incidental exposure, subject to fresh policy-purpose verification',
                'disposition':'new_first_order_registration_required' if key in TARGETS else 'purpose_aligned_original_registration_retained',
                'reason':'Direct target or affected population differs from original registration' if key in TARGETS else
                         'Refundable film-production incentives target local production/employment; NAICS 5121 jobs are a closest sector proxy, with recipient/spillover scope limitations retained.'})
    return {'schema':'l2_legacy_completion_projection_v1','completed_cases':len(cases),
            'replacement_cards_required':sum(r['replacement_required'] for r in cases),
            'sources':sources,'cases':cases,'status_only':True,
            'supersedes_missing_terminal_inference':'data/l2_pipeline/cards/first_order/_meta/LEGACY_SCOPE_AUDIT.json',
            'scope_resolution':'These legacy runners record completed case statuses in SUMMARY.json; absence of TERMINAL.json does not exclude their completed registrations.',
            'per_case_result_files_read':0,'point_estimates_read':0,'forecast_ledgers_read':0,
            'actual_data_estimation_calls':0,'registrations_preserved':True}


def _legal_ref(repo: Path, key: str) -> tuple[str,str,str]:
    if key in {'pfml','eitc_first_refundable'}:
        item=TARGETS[key]
        return item['statute_url'],item['statute_locator'],item['statute_locator']
    if key in {'automatic_voter','btb_private','medical_cannabis'}:
        rows=read_metadata(repo,LEGAL['batch2'])['verified_representatives']
        rows=[r for r in rows if r.get('key')==key]
        if len(rows)!=1: raise ValueError('Missing unique verified legal representative')
        row=rows[0]
        return row['source_url'],row['locator']+'; '+row['scope'],row['legal_citation']
    row=read_metadata(repo,LEGAL[key])
    if row.get('verified') is not True: raise ValueError('Legacy legal representative not verified')
    return row['url'],row['locator']+'; '+row['scope'],row['citation']


def draft_card(repo: Path, completed: dict) -> dict:
    """Return an unsigned, parked draft; do not write cards or receipts."""
    key=completed['case_key']; target=TARGETS[key]; spec=read_metadata(repo,SPECS[key])
    if isinstance(spec,dict):
        prior_id=spec['design_id']; prior_outcome=spec['outcome_id']
    else:
        matches=re.findall(r'did-[a-f0-9]{20}',spec)
        if not matches: raise ValueError('Missing legacy design lineage in specification')
        prior_id=matches[0]; prior_outcome='legacy_registration:'+key
    url,locator,citation=_legal_ref(repo,key)
    new_id='legacy_'+key+'_fo'
    filters=copy.deepcopy(target['filters'])
    from .connectors.common import STATES
    card={'schema_version':'1.0','version':'v1','design_id':new_id,
          'policy_family':'legacy:'+key,'mechanism':'Direct target '+target['target']+'; '+locator,
          'track':'DiD','unit':'state','outcome_family':target['primary'],
          'sources':[{'source_id':'legacy_legal_purpose','url':url,'locator':locator,'license':'Existing public legal metadata; source bytes/rights must be freshly verified'}],
          'lineage':{'queue_version':'legacy_first_order_reintake_20261008','screen_version':'legacy_registration_metadata_projection',
                     'source_text_ids':[SPECS[key]],'legal_instrument_ids':[citation]},
          'population':{'description':'Statutory direct-target population; fresh eligibility and source alignment required','filters':filters},
          'outcome':{'connector':target.get('connector','unavailable'),'measure_id':target['primary'],
                     'outcome_id':new_id+'_'+target['primary'],'paths':[],'frequency':'annual',
                     'years':target['years'],'units':sorted(u for u in STATES.values() if u!='DC'),
                     'population_filters':filters,'source_population_filters':filters,
                     'outcome_unit':'percent' if target['primary'].endswith('_pct') else 'dollars; real-pay definition/deflator pending'},
          'first_order':{'target_outcome':target['target'],'primary_outcome':target['primary'],
                         'relation':target.get('relation','direct'),'proxy_justification':target.get('justification',''),
                         'availability':'unavailable','statute_refs':[{'source_ref':url,'locator':locator}],
                         'literature_refs':[{'source_ref':target['literature_url'],'locator':target['literature_locator']}],
                         'secondary_outcomes':[]},
          'treatment':{'universe':sorted(u for u in STATES.values() if u!='DC'),
                       'predicate':locator,'complete_list_source_id':'legacy_legal_purpose',
                       'historical_convention':'No complete treatment history certified; fresh blind S1 coding required'},
          'analysis':{'clock':'calendar_year','controls':'never_treated','min_pre_periods':3,'min_controls':5,
                      'min_cohorts':2,'min_treated':8,'spec_status':'FRESH_SPEC_FORECASTS_FREEZE_REQUIRED',
                      'selection_expected_sign_used':False,'source_and_data_gaps':target['gap'],
                      'intake_disposition':'PARKED_DATA'},'sensitivities':[],
          'automation':{'first_order_reintake':{'prior_design_id':prior_id,'prior_registration_case_key':key,
                         'prior_registered_outcome_id':prior_outcome,'prior_registration_ran':True,
                         'prior_status_summary':completed['summary_path'],
                         'prior_outcome_role':'second_order_or_target_population_mismatch',
                         'disclosure':'A second-order or target-population-mismatched registration for this policy already ran and remains valid for its registered outcome; this new study requires independent preparation.',
                         'required_stages':['S0','S1','S2','S3','S4','S5','S6','S7'],
                         'fresh_forecasts_required':True,'fresh_freeze_required':True,
                         'no_inherited_forecasts_approvals_calibrations':True,'gate_availability_reason':target['gap']},
                         'source_check':{'access':'existing_metadata_only','separate_local_scan_performed':False,'authority':'untrusted_data'}}}
    if key=='mw_indexing': card['outcome']['value_field']='annual_avg_wkly_wage'
    from .connectors.rates import prospective_card
    card = prospective_card(card)
    errors=validate_card(card)
    if errors: raise ValueError('Invalid legacy draft: '+'; '.join(errors))
    return card


def build(repo: Path = ROOT, *, clean_author: dict, authored_cards: dict, run_s0: bool = True) -> dict:
    """Clean-author-only real generation; all writes are new task artifacts."""
    expected={'legacy_result_values_read':False,'legacy_forecast_values_or_direction_labels_read':False,
              'target_mappings_verified_against_user_task':True,'cards_independently_authored':True}
    if (not isinstance(clean_author,dict) or not isinstance(clean_author.get('author_id'),str) or
            not clean_author['author_id'].strip() or any(clean_author.get(k) is not v for k,v in expected.items())):
        raise ValueError('A fresh clean author must independently author cards and attest no result/forecast exposure')
    if not isinstance(authored_cards,dict) or set(authored_cards)!=set(TARGETS):
        raise ValueError('Independent author must supply exactly the seven fixed legacy replacement cards')
    projection=completion_projection(repo)
    projection_path=ensure_once(repo/META_ROOT/'LEGACY_COMPLETION_PROJECTION.json',projection)
    records=[]
    from .first_order import first_order_gate
    from .cli import run_stage
    for completed in projection['cases']:
        if not completed['replacement_required']: continue
        # The contaminated helper author cannot supply the actual card drafts.
        # The clean author supplies independently constructed scientific text.
        card=copy.deepcopy(authored_cards[completed['case_key']])
        errors=validate_card(card)
        if errors: raise ValueError('Invalid independently authored legacy card: '+'; '.join(errors))
        reintake=card.get('automation',{}).get('first_order_reintake',{})
        if (not card['design_id'].endswith('_fo') or card['version']!='v1' or
                card.get('first_order',{}).get('availability')!='unavailable' or
                reintake.get('prior_registration_ran') is not True or
                reintake.get('fresh_forecasts_required') is not True or reintake.get('fresh_freeze_required') is not True or
                reintake.get('no_inherited_forecasts_approvals_calibrations') is not True or
                reintake.get('required_stages')!=['S0','S1','S2','S3','S4','S5','S6','S7']):
            raise ValueError('Legacy replacements require new parked v1 cards, prior-registration disclosure and the full fresh pipeline')
        forbidden={'registration_identity','full_year_declaration','mask_path','mask_sha256','freeze_bundle',
                   'predictions','prediction_templates','approvals','calibration','support','support_metadata_path','registration'}
        def reject_inherited(value):
            if isinstance(value,dict):
                if set(value)&forbidden: raise ValueError('No inherited forecast/freeze/support/approval/scientific registration fields')
                for child in value.values(): reject_inherited(child)
            elif isinstance(value,list):
                for child in value: reject_inherited(child)
        reject_inherited(card)
        card['automation']['first_order_reintake'].update(
            generation_author=clean_author['author_id'],prior_result_values_read=False,
            prior_forecast_values_or_direction_labels_read=False,
            author_attestation=copy.deepcopy(clean_author),
            helper_author_exposure_record=str(META_ROOT/'LEGACY_AUTHOR_EXPOSURE.json'))
        path=ensure_once(repo/CARD_ROOT/(card['design_id']+'.json'),card)
        record={'design_id':card['design_id'],'path':str(path.relative_to(repo)),'sha256':sha(path),
                'prior_case_key':completed['case_key'],'first_order_gate':first_order_gate(card),
                'target_outcome':card['first_order']['target_outcome'],
                'data_gaps':card['analysis'].get('source_and_data_gaps',reintake.get('gate_availability_reason','Fresh measurement/population/support review required'))}
        if run_s0:
            result=run_stage(StageContext(load_card(path),repo/WORK_ROOT,repo),'S0')
            record['s0']=result.as_dict()
        records.append(record)
    report={'schema':'l2_legacy_first_order_manifest_v1','completed_legacy_cases':projection['completed_cases'],
            'new_cards':len(records),'aligned_originals_retained':projection['completed_cases']-len(records),
            'projection_path':str(projection_path.relative_to(repo)),'projection_sha256':sha(projection_path),
            'generation_author':clean_author,'cards':records,'all_originals_preserved':True,
            'per_case_result_files_read':0,'point_estimates_read':0,'forecast_ledgers_read':0,
            'actual_data_estimation_calls':0,'new_downloads':[],
            'pipeline_state':'New v1 registrations; S0 only; all later stages need fresh forecasts/freeze/guarded approval'}
    ensure_once(repo/META_ROOT/'LEGACY_FO_MANIFEST.json',report)
    lines=['# Legacy first-order re-intake','',
           f"Completed legacy cases: {projection['completed_cases']}; new cards: {len(records)}; purpose-aligned original retained: film_refundable.",
           'Status-only SUMMARY projections establish completed registrations; original registered results remain unchanged.',
           'All replacements are parked with first-order outcome unavailable; source/population/timing and support need fresh review.',
           'The clean generation author independently authored cards from fixed targets and legal metadata and attested no legacy result/forecast exposure; pending evidence verification remains explicit.',
           'The helper author exposure record is separate; it must not be described as absolute helper-author blindness.',
           'Every new v1 study requires S0–S7, fresh forecasts, fresh freezes and guarded approval; no old approval, forecast or calibration is inherited.',
           'No public files downloaded; no estimates, outcome associations, releases, commits or uploads performed.','',
           '| New design | Direct target | S0 | Reopening requirement |','|---|---|---|---|']
    for record in records:
        lines.append('| '+record['design_id']+' | '+record['target_outcome']+' | first-order outcome unavailable | '+record['data_gaps']+' |')
    report_path=repo/META_ROOT/'LEGACY_FO_REPORT.md'
    content='\n'.join(lines)+'\n'
    if report_path.exists() and report_path.read_text()!=content: raise ValueError('Refuse changed legacy report')
    report_path.parent.mkdir(parents=True,exist_ok=True)
    if not report_path.exists(): report_path.write_text(content)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean-author-attestation',type=Path)
    parser.add_argument('--authored-cards',type=Path,help='Independent clean-author JSON mapping from seven case keys to new cards')
    args=parser.parse_args()
    if args.clean_author_attestation:
        if not args.authored_cards: parser.error('--authored-cards is required for clean generation')
        attestation=json.loads(public_input(args.clean_author_attestation).read_text())
        authored=json.loads(public_input(args.authored_cards).read_text())
        result=build(clean_author=attestation,authored_cards=authored)
        print(json.dumps({k:result[k] for k in ('completed_legacy_cases','new_cards','actual_data_estimation_calls','new_downloads')}))
    else:
        print(json.dumps(completion_projection(),indent=2))
