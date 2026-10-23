"""Outcome-blind V4 run8 selection rerun, with an additive bounded hub wrapper.

Only policy stocks and outcome catalog metadata are opened. The registry and
output-scope extension exist for this process only; frozen/source code is unchanged.
"""
from __future__ import annotations
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUTPUT = REPO / 'data/l2_pipeline/connectors_v2/wonder/run8/v4'
POLICY_CATALOG = REPO / 'data/l2_pipeline/sources/research_panel/CATALOG.json'
OUTCOME_CATALOG = REPO / 'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V4.json'
ATTEMPT = 'run8_v4_intake_20261009'

# Exact legal categories / economic floor declared before inspecting support.
RULES = {
 'ukcpr:afdctanf_3bene': (500, 'A nominal $500 monthly guarantee defines a substantial cash floor for a three-person family; same run7 rule, without outcome optimization.'),
 'cspp:w_abortion_parental_notice_1983_2014': (1, 'Source code >=1 denotes either parental notification or consent; legal category independent of effects.'),
 'cspp:w_ec_access': (2, 'Source code >=2 denotes pharmacy authority to dispense emergency contraception without a prescription.'),
 'cspp:w_guncontrol_bc_dealers': (1, 'Source code >=1 requires background checks for at least handguns; legal presence threshold.'),
 'cspp:w_guncontrol_bc_privatesales': (1, 'Source code >=1 requires private-sale background checks for at least handguns; legal presence threshold.'),
 'cspp:w_guncontrol_registration_requir': (1, 'Source code >=1 requires registration for at least handguns; legal presence threshold.'),
 'cspp:w_guncontrol_waitingperiod': (1, 'Source code >=1 requires a waiting period for at least handguns; legal presence threshold.'),
 'cspp:regulation_motorcycle_helmets': (1, 'Source documentation defines 1 as a motorcycle helmet mandate and 0 as none; legal presence threshold.'),
 'cspp:drugs_boehmke_kegreg': (1, 'Source documentation defines 1 as required beer-keg registration and 0 as none; legal presence threshold.'),
}

# Existing run7 intakes are rerun in a new batch; these are not completed L2 designs.
# A previous purpose-specific first-order cannabis design is not overwritten by
# an overdose endpoint. New targets remain pending explicit independent review.
LEADS = {
 'ukcpr:eitc_state_refundable': ('Labor/Welfare', 'refundable_eitc', 'eitc', 'cps_single_mother_employment', {},
   'Work incentives may target employment among potentially eligible single mothers; verify receipt versus employment scope.',
   'Single mothers include mothers outside credit eligibility; same employment construct with population dilution.'),
 'ukcpr:afdctanf_3bene': ('Labor/Welfare', 'tanf_cash_guarantee', 'tanf_time_limits', 'acs1_single_mother_median_family_income', {},
   'Cash guarantees change household cash resources; verify the benefit-level predicate against the proposed income endpoint.',
   'Female-householder families include noneligible families; income construct with population dilution.'),
 'cspp:w_abortion_parental_notice_1983_2014': ('Fertility', 'minor_abortion_parental_involvement', 'abortion_access', 'wonder_births_by_maternal_age', {'age_group':'15-17'},
   'Parental-involvement rules directly address abortion procedures; teen births require explicit same-construct purpose review.',
   'Ages 15–17 cover part of affected minors; abortion-versus-birth construct admissibility remains unresolved.'),
 'cspp:w_ec_access': ('Fertility', 'pharmacy_ec_access', 'contraception_access', 'wonder_births_by_maternal_age', {'age_group':'15-19'},
   'Emergency contraception aims to prevent pregnancy; live births require explicit pregnancy-versus-live-birth endpoint review.',
   'Ages 15–19 include nonusers and omit adult users; dispensing remains unmeasured.'),
 'cspp:abortion_consent_1992_2014': ('Fertility', 'abortion_informed_consent', 'abortion_access', 'wonder_births_by_maternal_age', {'age_group':'15-19'},
   'Abortion counseling targets informed decisions; teen births require explicit statutory endpoint review.',
   'Teen births omit adults and abortions; same-construct admissibility remains unresolved.'),
}
DUPLICATES = {
 'ukcpr:aca_exp':'Existing Medicaid expansion with uninsured first-order outcome',
 'cspp:drugs_medical_marijuana':'Existing legacy_medical_cannabis_fo uses authorized-patient cannabis use as purpose-specific first-order target; overdose is a different construct, requiring a separate prospectively approved scope change',
 'cspp:guncontrol_stand_your_ground':'Existing standground first-order violence design',
 'cspp:regulation_bicycle_helmets':'Existing bicycle helmet design; all motor-vehicle mortality does not measure bicycle injuries',
}


# Root mechanisms, fixed from definitions; handgun/product/enforcement clauses
# cannot become extra families when a longer firearm endpoint becomes available.
FIREARM_GROUPS = {
 'minimum_purchase_age': 'age18longgunsale age21handgunsale age21longgunsale',
 'minimum_possession_age': 'age18longgunpossess age21handgunpossess age21longgunpossess',
 'ammunition_purchase_age': 'amm18 amm21h',
 'assault_weapon_ban': 'assault assaultlist assaultregister assaulttransfer onefeature',
 'campus_carry_restriction': 'college collegeconcealed',
 'firearm_dealer_license': 'dealer dealerh',
 'firearm_registration': 'defactoreg defactoregh registration registrationh',
 'domestic_violence_restraining_order_firearm_restriction': 'dvro dvroremoval dvrosurrender exparte expartesurrender',
 'gunshow_background_check': 'gunshow gunshowh',
 'extreme_risk_protection_order': 'gvro gvrolawenforcement',
 'domestic_incident_firearm_removal': 'incidentall incidentremoval',
 'large_capacity_magazine_ban': 'magazine magazinepreowned tenroundlimit',
 'concealed_carry_permit': 'mayissue permitconcealed',
 'domestic_violence_misdemeanor_firearm_restriction': 'mcdv mcdvremovalallowed mcdvremovalrequired mcdvsurrender',
 'open_carry_restriction': 'opencarryh opencarryl opencarrypermith opencarrypermitl',
 'firearm_purchase_license': 'permit permith',
 'state_background_check': 'statechecks statechecksh',
 'universal_background_check': 'universal universalh universalpermit universalpermith',
 'violent_misdemeanor_firearm_restriction': 'violent violenth violentpartial',
 'firearm_waiting_period': 'waiting waitingh',
}
FIREARM_FAMILY = {code:family for family,codes in FIREARM_GROUPS.items() for code in codes.split()}
CSPP_FIREARM_FAMILY = {
 'cspp:guncontrol_assaultweapon_ban':'assault_weapon_ban',
 'cspp:guncontrol_licenses_dealers':'firearm_dealer_license',
 'cspp:guncontrol_opencarry':'open_carry_restriction',
 'cspp:w_guncontrol_bc_privatesales':'universal_background_check',
 'cspp:w_guncontrol_registration_requir':'firearm_registration',
 'cspp:w_guncontrol_waitingperiod':'firearm_waiting_period',
}


# Fixed legal root mappings; age/product and enforcement variants share a family.
EXTRA_LEADS = {
 'cspp:aborparc': ('Fertility','minor_abortion_parental_involvement','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-17'},'Parental consent access lead; official purpose review pending.','Teen live births represent population dilution relative to minors seeking abortions.'),
 'cspp:aborparn': ('Fertility','minor_abortion_parental_involvement','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-17'},'Parental notification access lead; official purpose review pending.','Teen live births represent population dilution relative to minors seeking abortions.'),
 'cspp:pgestlim': ('Fertility','abortion_gestational_limit','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-19'},'Gestational-limit access lead; official purpose review pending.','Teen live births include unaffected pregnancies; population dilution.'),
 'cspp:abortion_medicaid': ('Fertility','abortion_medicaid_funding','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-19'},'Public abortion funding changes access; official purpose review pending.','Births among ages 15–19 permit population dilution relative to all eligible women.'),
 'cspp:abortion_partial_birth': ('Fertility','late_term_abortion_restriction','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-19'},'Abortion restriction access lead; official purpose review pending.','Teen live births include pregnancies unaffected by this restriction; population dilution.'),
 'cspp:abortion_consent_1973_1991': ('Fertility','abortion_informed_consent','abortion_access','wonder_births_by_maternal_age',{'age_group':'15-19'},'Pre-Casey counseling definition; official purpose review pending.','Teen live births represent population dilution relative to patients subject to counseling.'),
 'cspp:drugs_boehmke_kegreg': ('Alcohol','beer_keg_registration','alcohol_control','wonder_alcohol_induced_deaths',{},'Keg registration is an alcohol access/enforcement lead; official purpose review pending.','All alcohol-induced mortality covers populations outside keg purchasers; population dilution.'),
 'cspp:drugs_boehmke_mlda21': ('Alcohol','minimum_legal_drinking_age','minimum_legal_drinking_age','wonder_motor_vehicle_traffic_deaths_age15_24',{},'Minimum drinking age is a youth alcohol harm lead; official purpose review pending.','Traffic deaths age15–24 include sober deaths and ages21–24; population dilution.'),
 'cspp:drugs_boehmke_zerotol': ('Alcohol','zero_tolerance_driving','zero_tolerance_driving','wonder_motor_vehicle_traffic_deaths_age15_24',{},'Youth zero tolerance is a traffic harm lead; official purpose review pending.','Traffic deaths age15–24 include sober deaths and ages21–24; population dilution.'),
 'cspp:regulation_mandatory_seatbelts': ('Traffic','mandatory_seatbelts','seat_belt','wonder_motor_vehicle_traffic_deaths',{},'Occupant protection traffic harm lead; official purpose review pending.','All road users include nonoccupants; population dilution.'),
 'cspp:regulation_motorcycle_helmets': ('Traffic','motorcycle_helmets','helmet_law','wonder_motor_vehicle_traffic_deaths',{},'Motorcyclist protection lead; official purpose review pending.','All traffic mortality includes nonmotorcyclists; population dilution.'),
 'cspp:regulation_physician_suicide': ('Suicide','physician_assisted_suicide','suicide_policy','wonder_suicide_deaths',{},'End-of-life law is a suicide-category measurement lead; official death-classification review required.','Suicide deaths may not include legally assisted deaths; construct admissibility pending independent review.'),
}

def declared_periods(spec, filters):
    """Exact observed population-specific metadata only; never read outcomes."""
    age=filters.get('age_group')
    coverage=spec.get('age_group_years',spec.get('observed_years_by_age_group',spec.get('years_by_age_group',{})))
    if age is not None and coverage:
        return list(coverage.get(age,[]))
    return list(spec['years'])


def outcome_sources(spec):
    exports=spec.get('exports',[])
    return dict(paths=[str((REPO / p).resolve()) for p in spec.get('local_source_paths',[])],
        source_sha256={str((REPO / e['raw_path']).resolve()):e['sha256'] for e in exports},
        source_urls={str((REPO / e['raw_path']).resolve()):e['url'] for e in exports},
        license=spec.get('license','Source license retained in catalog/download receipts'))


def emit(path, value):
    path = Path(path).resolve()
    if not path.is_relative_to(OUTPUT.resolve()):
        raise ValueError('Run8 artifact outside assigned tree')
    content = json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n'
    if path.exists() and path.read_text()!=content:
        raise ValueError('Existing immutable artifact differs: '+str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists(): path.write_text(content)
    return path


def series_available(spec):
    return spec.get('public_available', True) and bool(spec.get('years')) and not str(spec.get('status','')).startswith(('blocked','planned','unavailable'))


def supported_records(records, periods):
    """Run7 rule: first treated year counts as the first post period."""
    eligible = [r for r in records if r['status']=='treated' and not r['primary_excluded']]
    supported = [r for r in eligible if sum(y<r['start_period'] for y in periods)>=3
                 and sum(y>=r['start_period'] for y in periods)>=2]
    return eligible, supported


def legal_window(variable):
    windows = variable['coverage']['historical_complete_50_state_windows']
    return min(windows,key=lambda w:(-(w[1]-w[0]+1),w[0])) if windows else None


def duplicate_reason(pid):
    if pid in DUPLICATES: return DUPLICATES[pid]
    if pid in ('siegel:mayissue','siegel:permitconcealed'):
        return 'Existing concealed carry first-order public-safety design'
    if pid=='siegel:nosyg': return 'Existing standground first-order violence design; inverse source clause does not add a family'
    if any(k in pid for k in ('minwage','minimumwage','mw_')):
        return 'Existing minimum-wage first-order wage designs'
    if ('eitc' in pid or 'earned_income_tax' in pid) and pid not in LEADS:
        return 'One refundable-state EITC family retained; other source variants are duplicates'
    return None


def candidate_lead(variable, outcome_catalog):
    pid = variable['policy_id']
    if pid in LEADS: return LEADS[pid]
    if pid.startswith('siegel:') or 'guncontrol' in pid:
        if variable['kind']=='continuous_intensity' and pid not in RULES: return None
        if pid=='siegel:lawtotal': return None
        family = FIREARM_FAMILY.get(pid.split(':',1)[1], 'firearm_'+pid.split(':',1)[1]) if pid.startswith('siegel:') else CSPP_FIREARM_FAMILY.get(pid,pid.replace(':','_'))
        return ('Firearm',family,'firearm_law','wonder_firearm_deaths',{},
                'Firearm mortality is a measurement lead; verify statutory harm purpose before accepting it.',
                'All-age all-intent firearm deaths may dilute the affected population; purpose-specific review required.')
    if pid=='cspp:drugs_marijuana_decriminalization':
        return ('Drug', 'marijuana_criminalization', 'cannabis_policy',
                'wonder_drug_overdose_deaths', {},
                'Source 1 means possession is criminal; preserve observed 0→1 transitions, never invert the treatment clock to claim decriminalization.',
                'All-drug fatal overdose versus marijuana criminalization is a construct lead requiring independent rejection or acceptance.')
    return EXTRA_LEADS.get(pid)


def declaration(variable):
    pid=variable['policy_id']
    if pid not in RULES: return None
    path=OUTPUT/'thresholds'/(pid.replace(':','_')+'.json')
    cut, reason=RULES[pid]
    stamp=json.loads(path.read_text())['declared_at'] if path.exists() else datetime.now(timezone.utc).isoformat()
    rule=dict(policy_id=pid, unit=variable['unit'], operator='>=', threshold=cut,
              rationale=reason, declared_at=stamp, outcome_associations_examined=False,
              selection_rule='Fixed legal category or cash floor; no alternate cutpoints screened')
    emit(path,rule)
    return rule


def build_card(variable, panel, catalog, outcome_catalog, lead, window, history, eligible, supported, periods):
    from scripts.l2_pipeline.sources.research_panel.core import STATES
    from scripts.l2_pipeline.storage import sha
    from scripts.l2_pipeline.cards import validate_card
    from scripts.l2_pipeline.coverage import coverage_bounded_card
    from scripts.l2_pipeline.connectors import v2_supported
    domain,family,target,series,filters,purpose,proxy=lead
    spec=outcome_catalog['series'][series]
    filters={**copy.deepcopy(spec.get('population_filters',{})), **filters}
    card=json.loads((REPO/'scripts/l2_pipeline/examples/design.json').read_text())
    card.update(design_id='run8_'+family,policy_family=family,mechanism=variable['definition'],
                outcome_family=target,sensitivities=[])
    source_ids=variable.get('source_ids',[variable['source_id']])
    receipts=[r for r in catalog['files'] if r.get('source_id') in source_ids]
    if not receipts: raise ValueError('No source receipts for '+variable['policy_id'])
    source=receipts[0]
    card['sources']=[dict(source_id=variable['source_id'],url=source['url'],locator=variable.get('locator',variable['policy_id']),
                         license=source['license'],path=source['path'],sha256=source['sha256'],coverage_end=f'{window[1]}-12-31',complete_list=False)]
    card['lineage']=dict(queue_version='run8_research_panel_x_outcomes_v4',screen_version='research_panel_catalog_v1',source_text_ids=[variable['policy_id']])
    card['population']=dict(description=spec['population'],filters=filters)
    card['outcome']=dict(connector='outcome_v4_'+series,series_id=series,measure_id=series,source_measure_id=series,
        paths=spec.get('local_source_paths',[]),years=[periods[0],periods[-1]],period_years=periods,units=list(STATES),frequency='annual',
        population_filters=filters,source_population_filters=filters,outcome_unit=spec['units'],
        measurement_refs=[dict(source_ref=spec['source_url'],locator=series)])
    card['outcome'].update(outcome_sources(spec))
    if not v2_supported(card['outcome'],series): raise ValueError('Exact series/population binding rejected')
    cfg=dict(source_type='research_panel',universe=list(STATES),seed=51009,policy_id=variable['policy_id'],
        predicate=variable['definition'],window=window,source_window=dict(start_year=window[0],end_year=window[1]),
        coverage_end=f'{window[1]}-12-31',catalog_path=str(POLICY_CATALOG),catalog_sha256=sha(POLICY_CATALOG),
        panel_path=panel['path'],panel_sha256=panel['sha256'],clock=variable['clock'],
        historical_convention='Observed source stock paths; no inferred enactment or full-year certification',
        audit_attempt_id=ATTEMPT,audit_jobs_root=str(OUTPUT/'jobs'),
        excluded_units=sorted(r['unit'] for r in history['records'] if r['primary_excluded'] or r['status']=='treated' and r not in supported))
    if variable['policy_id'] in RULES:
        path=OUTPUT/'thresholds'/(variable['policy_id'].replace(':','_')+'.json')
        cfg.update(threshold_path=str(path),threshold_sha256=sha(path))
    card['treatment']=cfg
    card['analysis'].update(min_pre_periods=3,min_post_periods=2,min_treated=6,min_cohorts=2,min_controls=5,
        cohort_eligibility_rule=dict(basis='Policy clock and V4 declared periods; first treated year counts as post as in run7',
            eligible_states=[r['unit'] for r in supported],historical_treated_reclassified_as_controls=False),
        spec_status='S0_CANDIDATE_PENDING_PURPOSE_AND_OFFICIAL_AUDIT')
    card['first_order']=dict(target_outcome=target,primary_outcome=series,relation='closest_proxy',proxy_justification=proxy,
        availability='available',evidence_status='pending_policy_specific_verification',
        statute_refs=[dict(source_ref=source['url'],locator='Research-panel definition lead; official statute verification pending')],
        literature_refs=[dict(source_ref=source['url'],locator='Documentation lead; independently identify policy-specific literature')],secondary_outcomes=[])
    support=dict(full_history_window=window,outcome_periods=periods,eligible_treated=len(supported),
                 legal_audit_eligible_treated=len(eligible),treated_states=[r['unit'] for r in supported],
                 audit_sample_scope='Ten seeded nonreversed treated states from full policy history; unsupported cohorts excluded from estimation')
    prior={'prior_intake':'run7_'+family,'action':'rerun_intake_metadata_only; never count run7 and run8 as two families'} if variable['policy_id'] in LEADS else {'prior_design':None}
    card['automation']=dict(intake=dict(domain='Health' if domain=='Fertility' else domain,source_type='research_panel'),
        run8=dict(policy_id=variable['policy_id'],domain=domain,purpose_lead=purpose,
          catalog_bindings=[dict(path=str(POLICY_CATALOG),sha256=sha(POLICY_CATALOG)),dict(path=str(OUTCOME_CATALOG),sha256=sha(OUTCOME_CATALOG))],
          endpoint_admissibility='Pending independent purpose review; unavailable or rejected proxies park',duplicate_review=prior,
          support=support,outcome_values_read=0,outcome_association_calls=0,estimation_calls=0))
    card=coverage_bounded_card(card)
    from .rates import prospective_card
    card = prospective_card(card)
    errors=validate_card(card)
    if errors or card['automation']['coverage_window']['parked']: raise ValueError(errors or card['automation']['coverage_window'])
    return card


def longest_tracker_window(policy):
    """Longest complete source-only annual block, earliest tie; no event search."""
    units=set(policy.get('states',[]))
    start,end=policy['coverage']['start_year'],policy['coverage']['end_year']
    rows=policy.get('annual_rows',[])
    if rows and all(all(type(r.get(k)) is int for k in ('missing_days','unknown_days','conflict_days')) for r in rows):
        good={y for y in range(start,end+1) if
              {r['unit'] for r in rows if r['year']==y and all(r[k]==0 for k in ('missing_days','unknown_days','conflict_days'))}==units}
        blocks=[]
        for y in sorted(good):
            if blocks and y==blocks[-1][1]+1:blocks[-1][1]=y
            else:blocks.append([y,y])
        return min(blocks,key=lambda w:(-(w[1]-w[0]+1),w[0])) if blocks else None
    return [start,end] if policy.get('history_complete') is True else None


def tracker_lead(policy):
    """Only prospective mortality targets explicitly named for this task."""
    pid=policy['policy_id'];tracker=policy['tracker_id'];family=policy.get('policy_family')
    if tracker=='pdaps_pain_clinic' and pid=='PMC_Law' or family=='pain_clinic_regulation':
        return ('Drug','pain_clinic_regulation','pill_mill_regulation','wonder_opioid_overdose_deaths',{},
            'Pain-clinic regulation is an opioid harm lead authorized for V4; official purpose review pending.',
            'All opioid overdose mortality includes deaths unrelated to pain clinics; population dilution.')
    if family=='opioid_informed_consent':
        return ('Drug',family,'drug_overdose_policy','wonder_opioid_overdose_deaths',{},
            'Opioid counseling is a harm lead; statutory purpose and construct review pending.',
            'Opioid overdose includes patients outside counseling scope; population dilution.')
    return None


def select_trackers(outcomes,families):
    from scripts.l2_pipeline.policy_tracker_intake import normalize_history,build_card as tracker_card
    from scripts.l2_pipeline.storage import sha
    from scripts.l2_pipeline.coverage import coverage_bounded_card
    files=['runs/run6/pdaps/POLICIES.json','runs/run6/cdc/POLICIES.json'] + [
        'runs/run5/sources/'+s+'.policies.json' for s in ('pdaps_naloxone','pdaps_good_samaritan','pdaps_pain_clinic','pdmp_mandates')]
    dispositions=[];selected=[];verified={}
    for name in files:
        path=REPO/'data/l2_pipeline'/name;value=json.loads(path.read_text())
        policies=value['policies'] if isinstance(value,dict) else value
        for policy in policies:
            pid=policy['policy_id'];lead=tracker_lead(policy)
            row=dict(policy_id=pid,tracker_id=policy['tracker_id'],source_catalog=str(path),kind='tracker_binary',domain=lead[0] if lead else policy.get('domain','Health'))
            existing=policy.get('existing_design_id') or policy.get('existing_design_reference')
            tid=policy['tracker_id']
            if existing or policy.get('duplicate_existing_first_order') or tid in ('pdmp_mandates','pdaps_good_samaritan','pdaps_naloxone'):
                row.update(action='DUPLICATE_EXISTING_FIRST_ORDER',reason='Existing L2 root or clause/source supplement; not a new distinct family',existing_design=existing or {'pdmp_mandates':'run1_pdmp_mandatory_query','pdaps_good_samaritan':'run2_overdose_good_samaritan / run3_goodsam911','pdaps_naloxone':'batch2_naloxone_standing; broader authorization needs independent root-scope decision'}.get(tid))
            elif not lead:
                row.update(action='PARKED_TARGET_GAP',reason='No exact V4 first-order construct binding; tobacco enforcement/preemption/cessation and drug-service targets require separate series; no unrelated mortality proxy')
            else:
                window=longest_tracker_window(policy)
                if window is None:
                    row.update(action='PARKED_NO_COMPLETE_POLICY_WINDOW',reason='No source-only complete annual block; days/gaps not inferred')
                else:
                    imported=normalize_history(policy,scope=window)
                    spec=outcomes['series'][lead[3]];periods=[y for y in declared_periods(spec,lead[4]) if window[0]<=y<=window[1]]
                    eligible,supported=supported_records(imported['records'],periods)
                    row.update(outcome=lead[3],full_history_window=window,outcome_periods=periods,supported_treated=len(supported),full_history_treated=len(eligible),complete_history=imported['history_complete'])
                    if not imported['history_complete'] or not imported['repeals_included']:
                        row.update(action='PARKED_NO_COMPLETE_POLICY_WINDOW',reason='Complete history/repeal inventory unresolved')
                    elif len(supported)<6:
                        row.update(action='PARKED_SUPPORT',reason='Fewer than six treated states with >=3pre >=2post')
                    elif len(eligible)<10:
                        row.update(action='PARKED_AUDIT_SUPPORT',reason='Ten-treated-state V5 official audit support fails')
                    elif lead[1] in families:
                        row.update(action='SOURCE_VARIANT_DUPLICATE',reason='Distinct root family already retained')
                    else:
                        raw=Path(policy['source_path']).resolve()
                        if raw not in verified: verified[raw]=sha(raw)
                        if verified[raw]!=policy['sha256'] or raw.stat().st_size!=policy['size_bytes']:raise ValueError('Tracker source receipt mismatch')
                        imported['raw_source_sha256']=policy['sha256']
                        hp=emit(OUTPUT/'histories'/(lead[1]+'.json'),imported)
                        filters={**spec.get('population_filters',{}),**lead[4]}
                        outcome=dict(connector='outcome_v4_'+lead[3],series_id=lead[3],measure_id=lead[3],source_measure_id=lead[3],target_outcome=lead[2],years=[periods[0],periods[-1]],period_years=periods,population_filters=filters,source_population_filters=filters,outcome_unit=spec['units'],population_description=spec['population'],relation='closest_proxy',proxy_justification=lead[6],measurement_url=spec['source_url'],measurement_refs=[dict(source_ref=spec['source_url'],locator=lead[3])])
                        outcome.update(outcome_sources(spec))
                        scoped=dict(policy,coverage={**policy['coverage'],'start_year':window[0],'end_year':window[1]},policy_family=lead[1])
                        card=tracker_card(scoped,outcome,design_id='run8_'+lead[1],history_path=hp)
                        card['outcome'].update(outcome_sources(spec))
                        card['treatment'].update(audit_attempt_id=ATTEMPT,audit_jobs_root=str(OUTPUT/'jobs'),excluded_units=sorted(r['unit'] for r in imported['records'] if r['primary_excluded'] or r['status']=='treated' and r not in supported))
                        card['analysis'].update(min_treated=6,min_pre_periods=3,min_post_periods=2,spec_status='S0_CANDIDATE_PENDING_PURPOSE_AND_OFFICIAL_AUDIT')
                        support=dict(full_history_window=window,outcome_periods=periods,eligible_treated=len(supported),legal_audit_eligible_treated=len(eligible),treated_states=[r['unit'] for r in supported],audit_sample_scope='Ten seeded full-history eligible states; no replacement')
                        card['lineage']['queue_version']='run8_tracker_x_outcomes_v4'
                        card['automation']['run8']=dict(policy_id=pid,domain=lead[0],support=support,purpose_lead=lead[5],catalog_bindings=[dict(path=str(path),sha256=sha(path)),dict(path=str(OUTCOME_CATALOG),sha256=sha(OUTCOME_CATALOG))],outcome_values_read=0,outcome_association_calls=0,estimation_calls=0)
                        card=coverage_bounded_card(card)
                        cp=emit(OUTPUT/'cards'/(card['design_id']+'.json'),card);families.add(lead[1])
                        selected.append(dict(design_id=card['design_id'],policy_id=pid,policy_family=lead[1],domain=lead[0],outcome=lead[3],card_path=str(cp),support=support,purpose_status='PENDING_S0_VERIFICATION'))
                        row.update(action='S0_CANDIDATE_PENDING_GATES',reason='Official treatment audit and independent purpose review pending')
            window=longest_tracker_window(policy)
            row['source_coverage']=policy['coverage']
            row['source_family']=policy.get('policy_family') or tid
            row['complete_source_only_window']=window
            row['source_observed_cells']=len(policy.get('annual_rows',[]))+len(policy.get('quarterly_rows',[]))
            if window is not None:
                metadata_history=normalize_history(policy,scope=window)
                row['full_history_eligible_treated']=sum(r['status']=='treated' and not r['primary_excluded'] for r in metadata_history['records'])
                row['full_history_never_treated']=sum(r['status']=='never_treated' and not r['primary_excluded'] for r in metadata_history['records'])
                row['reversals_retained']=sum(bool(r['reversal_years']) for r in metadata_history['records'])
            dispositions.append(row)
    return dispositions,selected


def select():
    from .wonder_registry import register
    register()
    from scripts.l2_pipeline.sources.research_panel.core import history, read_panel
    catalog=json.loads(POLICY_CATALOG.read_text()); outcomes=json.loads(OUTCOME_CATALOG.read_text())
    by={v['policy_id']:v for v in catalog['variables']}
    # Declare all fixed rules before reading source stock panels or counting support.
    rules={pid:declaration(by[pid]) for pid in RULES if pid in by}
    panels={p['path']:read_panel(p['path']) for p in catalog['panels']}
    selected=[];dispositions=[];families=set()
    order=lambda v:(0 if v['policy_id'].startswith('ukcpr:') else 1,v['policy_id'])
    for variable in sorted(catalog['variables'],key=order):
        pid=variable['policy_id']; window=legal_window(variable);lead=candidate_lead(variable,outcomes)
        row=dict(policy_id=pid,kind=variable['kind'],domain=lead[0] if lead else variable.get('category','Other'))
        reason=duplicate_reason(pid)
        if reason: row.update(action='DUPLICATE_EXISTING_FIRST_ORDER',reason=reason)
        elif lead and lead[0]=='Firearm' and not pid.startswith(('siegel:','rand:')):
            row.update(action='PARKED_FIREARM_HISTORY',reason='Firearm laws require complete RAND/Siegel history; CSPP source variant not independently selected')
        elif not window: row.update(action='PARKED_NO_COMPLETE_POLICY_WINDOW',reason='No complete historical 50-state window')
        elif variable['kind']!='binary_adoption' and pid not in rules:
            row.update(action='PARKED_NO_LEGAL_THRESHOLD',reason='No prospectively declared threshold; no outcome-driven cutpoint search')
        elif not lead: row.update(action='PARKED_TARGET_GAP',reason='No policy-specific same-construct V4 lead selected')
        else:
            domain,family,target,series,*_=lead
            spec=outcomes['series'].get(series,{})
            if not series_available(spec):
                row.update(action='PARKED_OUTCOME_BLOCKED',reason='V4 endpoint blocked/unimplemented; prospective years grant no support')
            else:
                imported=history(panels[next(p['path'] for p in catalog['panels'] if pid in p['policy_ids'])],variable,window,threshold=rules.get(pid))
                periods=[y for y in declared_periods(spec,lead[4]) if window[0]<=y<=window[1]]
                eligible,supported=supported_records(imported['records'],periods)
                row.update(outcome=series,full_history_window=window,outcome_periods=periods,
                           supported_treated=len(supported),full_history_treated=len(eligible))
                if len(supported)<6: row.update(action='PARKED_SUPPORT',reason='Fewer than six treated states with >=3 pre and >=2 post periods')
                elif len(eligible)<10: row.update(action='PARKED_AUDIT_SUPPORT',reason='Run7 ten-treated-state legal audit rule fails')
                elif family in families: row.update(action='SOURCE_VARIANT_DUPLICATE',reason='One distinct mechanism family already retained')
                else:
                    panel=next(p for p in catalog['panels'] if pid in p['policy_ids'])
                    card=build_card(variable,panel,catalog,outcomes,lead,window,imported,eligible,supported,periods)
                    path=emit(OUTPUT/'cards'/(card['design_id']+'.json'),card);families.add(family)
                    selected.append(dict(design_id=card['design_id'],policy_id=pid,policy_family=family,domain=domain,
                        outcome=series,card_path=str(path),support=card['automation']['run8']['support'],purpose_status='PENDING_S0_VERIFICATION'))
                    row.update(action='S0_CANDIDATE_PENDING_GATES',reason='Independent purpose review and official treatment audit pending')
        dispositions.append(row)
    tracker_rows,tracker_cards=select_trackers(outcomes,families)
    dispositions.extend(tracker_rows);selected.extend(tracker_cards)
    report=dict(schema_version='run8_selection_v4',catalog_variables=len(catalog['variables']),tracker_variables=len(tracker_rows),outcome_series=len(outcomes['series']),
       catalog_path=str(OUTCOME_CATALOG),cards=selected,cards_emitted=len(selected),distinct_families=len(families),
       counts_by_domain={d:sum(s['domain']==d for s in selected) for d in ('Labor/Welfare','Firearm','Drug','Alcohol','Traffic','Suicide','Fertility','Tax')},dispositions=dispositions,
       tracker_family_inventory={f:sum(r.get('source_family')==f for r in tracker_rows) for f in sorted({r['source_family'] for r in tracker_rows})},
       thresholds_declared=len(rules),selection_rule='Run7: longest complete policy history (earliest tie), exact V4 periods; >=3 pre, >=2 post including first treated year; >=6 treated, >=10 full-history treated for legal audit; one distinct family; existing first-order duplicates excluded',
       target_cards=30,target_met=len(selected)>=30,shortfall=max(0,30-len(selected)),
       shortfall_rule='Missing complete legal histories, pre/post support, first-order targets, or distinct roots stay parked; never count provision/intent duplicates to meet numeric target',
       blocked_wonder_selected=0,outcome_values_read=0,outcome_association_calls=0,estimation_calls=0,new_downloads=[])
    emit(OUTPUT/'RUN8_INTAKE_REPORT.json',report)
    print(json.dumps({k:report[k] for k in ('catalog_variables','cards_emitted','counts_by_domain','thresholds_declared')}))
    return 0


@contextmanager
def bounded_research_outputs():
    """Extend the legacy adapter only to this task's assigned run8 tree."""
    from scripts.l2_pipeline.sources.research_panel import core, s1, registration
    original=core.owned
    def owned(path):
        path=Path(path).resolve()
        if path.is_relative_to(OUTPUT.resolve()): return path
        return original(path)
    modules=(core,s1,registration)
    previous=[m.owned for m in modules]
    try:
        for module in modules: module.owned=owned
        yield
    finally:
        for module, prior in zip(modules,previous): module.owned=prior


def hub(argv):
    from .wonder_registry import register
    register()
    if not argv: raise ValueError('Hub command required')
    if argv[0] not in ('run','prepare','status','dispatch','collect','advance'):
        raise ValueError('Wrapper permits local task hub work only')
    if argv[0]=='dispatch' and not any(flag in argv for flag in ('--dry-run','--preflight-only')):
        raise ValueError('No model calls authorized; dispatch requires --dry-run or --preflight-only')
    if argv[0] in ('run','prepare','dispatch') and (len(argv)<2 or argv[1] not in ('S0','S1')):
        raise ValueError('Task permits S0/S1 only; no outcome analysis or estimation')
    if argv[0]=='dispatch' and '--jobs-root' not in argv:
        raise ValueError('Task dispatch requires an explicit run8 jobs root')
    for option in ('--batch','--work-dir','--jobs-root','--job','--design'):
        if option in argv:
            path=Path(argv[argv.index(option)+1]).resolve()
            if not path.is_relative_to(OUTPUT.resolve()): raise ValueError('Hub task path outside run8 tree')
    from scripts.l2_pipeline.hub import main
    with bounded_research_outputs(): return main(argv)


def main(argv=None):
    import sys
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='hub': return hub(argv[1:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['select'])
    parser.parse_args(argv)
    return select()

if __name__=='__main__': raise SystemExit(main())
