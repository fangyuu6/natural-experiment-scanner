"""Fixed mechanism selection and explicit duplicate dispositions, outcomes blind."""
import copy
import argparse
import json
from scripts.l2_pipeline.run6_adapters.paths import require
from pathlib import Path
from scripts.l2_pipeline.storage import ensure_once

from scripts.l2_pipeline.run6_adapters.paths import DATA_ROOT
ROOT = DATA_ROOT


SOURCE_FAILURES = []


def policies(domain):
    try:
        value=json.loads((ROOT/domain/'POLICIES.json').read_text())
        records=value.get('policies',[]) if isinstance(value,dict) else value
        require(isinstance(records,list), 'Policy source must contain a list: '+domain)
        return records
    except Exception as exc:
        SOURCE_FAILURES.append(dict(domain=domain,action='SOURCE_STOPPED',reason=str(exc)))
        return []


def main(argv=None):
    global ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default='v2',choices=['v2'])
    parser.add_argument("--data-root",type=Path,default=ROOT)
    args=parser.parse_args(argv)
    ROOT=args.data_root.resolve()
    SOURCE_FAILURES.clear()
    selected=[]; dispositions=[]; upgrades=[]
    # Root mechanisms are fixed before opening any outcome data. Venue,
    # vending-device and product clauses stay in source metadata, not counts.
    representative_ids={
        'cdc_628YAC_203_age21','cdc_629YAV_216_yes','cdc_640LOC_175_yes',
        'cdc_1001ETR_450_yes','cdc_1019LOC_759_yes','cdc_1021ECT_509_age21',
        'cdc_1007RES_700_ban','cdc_630PRY_222_yes'}
    for raw in policies('cdc'):
        try:
            p=copy.deepcopy(raw)
            if p.get('duplicate_existing_first_order'):
                action='EXISTING_DESIGN_SOURCE_SUPPLEMENT'
                reason='Frozen smoke_free_air already uses current smoking; venues/campus remain one family'
                upgrades.append(dict(policy_id=p['policy_id'],existing_design='smoke_free_air',
                                     source_path=p['source_path'],source_sha256=p['sha256']))
            elif p['policy_id'] not in representative_ids:
                action='SOURCE_VARIANT_RETAINED'
                reason='Root mechanism representative selected; product/venue/clause variants do not add families'
            else:
                action='S0_CANDIDATE_PENDING_GATES'
                reason=p['outcome'].get('target_gap','Purpose, population, full-exposure date, earliest cohort and official audit pending')
                if p['outcome'].get('target_population_gap') and not p['outcome'].get('target_gap'):
                    p['outcome'].update(relation='closest_proxy',proxy_justification=
                        'Adult current-cigarette-smoking prevalence measures the same smoking construct; adults outside the youth sales restriction cause population dilution, requiring explicit purpose and population approval.')
                if p['outcome'].get('target_gap'):
                    p['first_order_candidate']=False
                    # The unavailable local-policy-adoption target is direct in
                    # its own construct, but no health measure substitutes for it.
                    p['outcome']['relation']='direct'
                    p['outcome']['population_description']='Target population not measured by the current connector; no smoking/vaping substitution'
                p['duplicate_review']=dict(decision='new_mechanism_or_new_product_target_lead',
                    related_existing_design='smoke_free_air' if p['policy_family']=='smokefree_air' else None,
                    no_reuse_of_existing_results_forecasts_or_approvals=True)
                selected.append(p)
            dispositions.append(dict(domain='Health',policy_id=p['policy_id'],policy_family=p['policy_family'],action=action,reason=reason))
        except Exception as exc:
            dispositions.append(dict(policy_id=raw.get('policy_id') if isinstance(raw, dict) else None,
                                     action='INVALID_CANDIDATE_STOPPED', reason=str(exc)))
    for raw in policies('pdaps'):
        try:
            p=copy.deepcopy(raw)
            p['domain']='Health'
            prior=p.get('existing_design_id')
            if prior:
                upgrades.append(dict(policy_id=p['policy_id'],existing_design=prior,
                    source_path=p['source_path'],source_sha256=p['sha256'],history_complete=p['history_complete'],
                    action='RUN6_SOURCE_SUPPLEMENT_ONLY; prior files unchanged'))
                dispositions.append(dict(domain='Health',policy_id=p['policy_id'],policy_family=p['policy_family'],
                                         action='EXISTING_DESIGN_SOURCE_SUPPLEMENT',reason='Prior policy mechanism already registered: '+prior))
                continue
            # Preserve unavailable proximal targets as parked source cards. A
            # downstream health proxy is never silently installed in their place.
            p['first_order_candidate']=False
            if p['policy_family']=='pain_clinic_regulation':
                evidence=json.loads((ROOT/'sources/DOWNLOADS.json').read_text())[0]
                p['original_target_lead']=copy.deepcopy(p['outcome'])
                p['outcome']=dict(connector='nchs_mortality',measure_id='drug_overdose_deaths_count',
                    target_outcome='drug_overdose_deaths_count',relation='closest_proxy',years=[1999,2017],
                    proxy_justification='All-drug fatal overdoses measure the stated fatal drug-abuse harm; nonprescription drugs and people not served by pain clinics cause population dilution, requiring purpose verification across the statutory family.',
                    target_population_gap=True,target_population_description='Patients exposed to prescription-drug abuse from regulated pain clinics',
                    population_description='All state residents, all ages and sexes; final all-drug overdose deaths',population_filters={},
                    outcome_unit='deaths',measurement_url='https://data.cdc.gov/resource/bi63-dtpu.csv',
                    evidence_locator='Florida Senate2011 HB7095 summary links prescription-drug regulation to untimely deaths; summary is a one-state purpose lead, not verified all-state statutory evidence')
                p['first_order_candidate']=True
                p['purpose_evidence_lead']=dict(url=evidence['url'],path=evidence['path'],sha256=evidence['sha256'],
                    locator='Introductory paragraph: prescription drug abuse and untimely deaths',status='OFFICIAL_SUMMARY_LEAD_ONLY')
            selected.append(p)
            dispositions.append(dict(domain='Health',policy_id=p['policy_id'],policy_family=p['policy_family'],
                                     action='S0_CANDIDATE_PENDING_GATES',reason=p['outcome'].get('target_gap','One-state fatal-overdose purpose lead; all-state purpose/population and incomplete legal history remain pending')))
        except Exception as exc:
            dispositions.append(dict(policy_id=raw.get('policy_id') if isinstance(raw, dict) else None,
                                     action='INVALID_CANDIDATE_STOPPED', reason=str(exc)))
    for p in policies('cms'):
        try:
            p['outcome']['proxy_justification']='Any-insurance coverage in ages26–34 measures insurance receipt; higher-income residents and other insurance types cause population dilution, requiring purpose and population review.'
            p['duplicate_review']=dict(decision='BHP is distinct from ACA Medicaid expansion; source history still incomplete')
            selected.append(p)
            dispositions.append(dict(domain='Insurance',policy_id=p['policy_id'],policy_family=p['policy_family'],
                action='S0_CANDIDATE_PENDING_GATES',reason='Insurance outcome maps; dated narrative lacks complete all-state histories and ten-treated-state support'))
        except Exception as exc:
            dispositions.append(dict(policy_id=p.get('policy_id') if isinstance(p, dict) else None,
                                     action='INVALID_CANDIDATE_STOPPED', reason=str(exc)))
    for p in policies('social'):
        try:
            dispositions.append(dict(domain='Education' if p['tracker_id'].startswith('nces') else 'Labor',
                policy_id=p['policy_id'],policy_family=p.get('family_id',p['policy_id']),
                action='SOURCE_LEAD_PARKED_NO_CARD',reason=p.get('stop_reason','Incomplete history / duplicate / target connector absent')))
        except Exception as exc:
            dispositions.append(dict(policy_id=p.get('policy_id') if isinstance(p, dict) else None,
                                     action='INVALID_CANDIDATE_STOPPED', reason=str(exc)))
    ensure_once(ROOT/f'SELECTED_POLICIES.{args.version}.json',dict(policies=selected,selection_rule='One root mechanism representative per domain; limited product-target alternatives remain same family; unavailable leads are parked, not counted toward15'))
    ensure_once(ROOT/f'DISPOSITIONS.{args.version}.json',dict(rows=dispositions,source_failures=SOURCE_FAILURES,outcome_values_read=0,outcome_association_calls=0))
    ensure_once(ROOT/f'EXISTING_DESIGN_SOURCE_UPGRADES.{args.version}.json',dict(upgrades=upgrades,no_prior_files_modified=True,no_new_designs_for_duplicates=True))
    print(json.dumps(dict(selected_cards=len(selected),candidate_families=len({p['policy_family'] for p in selected}),
                         source_variant_dispositions=len(dispositions),source_upgrades=len(upgrades))))
    return int(bool(SOURCE_FAILURES) or any(row['action']=='INVALID_CANDIDATE_STOPPED' for row in dispositions))


if __name__=='__main__':
    raise SystemExit(main())
