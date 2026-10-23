#!/usr/bin/env python3
"""Render L2 records bound to the 2026-10-11 release snapshot.

Read-only: this module never calls an estimator or writes a research artifact.
The append-only terminal registry may grow; selected run IDs stay snapshot-bound.
"""
from functools import lru_cache
from pathlib import Path
from datetime import datetime
import csv
import hashlib
import html
import json
import math

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / 'data/l2_pipeline/registry/SCOREBOARD_SNAPSHOT_20261011.json'
SOURCES = ROOT / 'docs/plans/release-20261011/ESTIMATES_SOURCES.tsv'
GATES = {
    'support': ('Data support below threshold', '数据支持未达门槛'),
    'history': ('Too few treated states retained (7); source checks exhausted (5)', '保留处理州不足（7）；来源核验额度耗尽（5）'),
    'connector': ('No usable outcome series registered in connectors', '连接器未登记可用结局序列'),
    'ri': ('Reassignment test infeasible', '重排检验不可行'),
    'fresh': ('Too few new states for an independent audit', '可供独立审计的新州不足'),
    'pre_run_stop': ('Reassignment draws below 90% before run', '运行前可估计重排不足 90%'),
    'other': ('Outcome registration incomplete', '结局登记不全'),
    'v3': ('History audit failed', '历史审计未通过'),
}
# Text labels only; all numerical values are read from terminal records.
OUTCOME_ZH = {
    'Private-sector wages (log points)': '私营部门工资（对数差）',
    'Women 20-44 employment (pp)': '20–44 岁女性就业率（百分点）',
    'Ambulance-service employment (jobs)': '救护服务就业（岗位）',
    'Therapy-establishment employment (jobs)': '治疗机构就业（岗位）',
    'Hospital nurse weekly hours': '医院护士每周工时（小时）',
    'Imaging-facility employment (jobs)': '医学影像机构就业（岗位）',
    'Physician-office employment (jobs)': '医师诊所就业（岗位）',
    'Age 18-29 employment (pp)': '18–29 岁就业率（百分点）',
    'Women 18-44 employment (pp)': '18–44 岁女性就业率（百分点）',
    'Waste-management employment (jobs)': '废弃物管理业就业（岗位）',
    'Electricity sales per customer (source unit uncertified)': '每客户售电量（来源单位未认证）',
    'Age 18-64 employment (pp)': '18–64 岁就业率（百分点）',
    'Age-adjusted drug-poisoning mortality per 100k': '年龄调整后药物中毒死亡率（每 10 万人）',
    'Age 18-25 employment (pp)': '18–25 岁就业率（百分点）',
    'Births per 1,000 women 15-19': '15–19 岁女性出生率（每千人）',
    "Single mothers' employment (pp)": '单身母亲就业率（百分点）',
    'Firearm deaths (persons per state-year)': '枪支死亡人数（人／州年）',
    'All-mechanism homicide rate per 100k': '全部方式凶杀率（每 10 万人）',
    'Firearm homicide rate per 100k': '枪支凶杀率（每 10 万人）',
}
GATE_NAMES_EN = {
    'b1010_alr': 'Administrative license revocation',
    'b1010_bac_008_per_se': '0.08 blood-alcohol driving limit',
    'b1010_dv_mandatory_arrest': 'Mandatory arrest for domestic violence',
    'b1010_mlda21_raise': 'Minimum drinking age 21',
    'b1010_online_voter_registration': 'Online voter registration',
    'b1010_open_container': 'Open alcohol containers in vehicles',
    'b1010_parole_abolition': 'Parole abolition',
    'b1010_preregistration_youth': 'Youth voter preregistration',
    'b1010_same_day_registration': 'Same-day voter registration',
    'b1010_shall_issue_carry': 'Shall-issue or permitless carry · homicide rate, 1979–2014',
    'b1010_three_strikes': 'Three-strikes laws',
    'b1010b_bicycle_passing_3ft': 'Three-foot bicycle passing distance',
    'b1010b_epcs_mandate': 'Electronic controlled-substance prescribing',
    'b1010b_hate_crime_sexual_orientation': 'Sexual-orientation hate-crime laws',
    'b1010b_hs_exit_exam_repeal': 'High-school exit-exam repeal',
    'b1010b_in_state_tuition_undocumented': 'In-state tuition for undocumented students',
    'b1010b_mandatory_college_entrance_exam': 'Mandatory college-entrance exams',
    'b1010b_medicaid_tobacco_cessation': 'Medicaid tobacco-cessation benefits',
    'b1010b_motorcycle_helmet_repeal': 'Motorcycle helmet-law repeal',
    'b1010b_novice_cellphone_ban': 'Novice-driver cellphone bans',
    'b1010b_opioid_acute_pain_limit': 'Acute-pain opioid prescription limits',
    'b1010b_opioid_prescriber_cme': 'Opioid prescriber continuing education',
    'b1011tl_chip_waiting_period_elimination': 'CHIP waiting-period elimination',
    'bulk10_health_child_access_prevention_storage': 'Child-access firearm storage laws',
    'c35_cspp_death_penalty': 'Death-penalty abolition · homicide rate, 1979–2014',
    'r1011_good_samaritan_911': 'Good Samaritan protections for 911 calls',
    'r1011_naloxone_access_law': 'Naloxone access laws',
    'r1011_social_host_liability_minors': 'Social-host liability for underage drinking',
    'run2_autism_insurance': 'Autism insurance coverage',
    'run2_clinical_trial_insurance_coverage': 'Clinical-trial insurance coverage',
    'run2_kinship_care_program': 'Kinship-care programs',
    'run2_physician_apology_protection': 'Physician apology protections',
    'run2_residential_efficiency_tax_credit': 'Residential energy-efficiency tax credits',
    'run2_statewide_college_transfer': 'Statewide college-transfer rules',
    'run2_universal_pre_k': 'Universal prekindergarten',
    'run4_lit_gdl_staged_licensing': 'Graduated driver licensing',
    'b1010_strict_voter_id': 'Strict voter-identification laws',
    'b1010b_cc_performance_funding': 'Community-college performance funding',
    'b1010b_free_cc_promise': 'Free community-college promises',
    'b1010b_keg_registration': 'Keg registration',
    'b1010b_motorcycle_helmet_enactment': 'Motorcycle helmet-law enactment',
    'b1010b_pdmp_mandatory_registration': 'Mandatory prescription-monitoring registration',
    'b1011tl_dl_unauthorized_immigrants': 'Driver licenses for undocumented immigrants',
    'b1011tl_snap_phone_interview_waiver': 'SNAP telephone-interview waivers',
    'b1011tl_ui_duration_cap_below_26_weeks': 'Unemployment benefits capped below 26 weeks',
    'bulk10_health_dobbs_abortion_bans_gfr': 'Post-Dobbs abortion bans',
    'bulk10_health_speed_limit_70plus_rural_interstate': 'Rural interstate speed limits of at least 70 mph',
    'c36_cspp_w_abortion_parental_notice_1983_2014': 'Abortion parental-notification laws',
    'b1010b_ban_14c_subminimum': 'Section 14(c) subminimum-wage bans',
    'b1011tl_noncompete_lowwage_ban': 'Low-wage worker noncompete bans',
    'b1011tl_snap_online_application': 'SNAP online applications',
    'b1011tl_wd_implied_contract_exception': 'Wrongful-dismissal implied-contract exception',
    'b1011tl_wd_public_policy_exception': 'Wrongful-dismissal public-policy exception',
    'bulk1010_lab_credit_check_ban': 'Employer credit-check restrictions',
    'bulk1010_lab_single_sales_factor': 'Corporate single-sales-factor taxation',
    'bulk1010_lab_snap_bbce': 'SNAP broad-based categorical eligibility',
    'bulk10_health_gestational_limit_infant_mortality': 'Gestational abortion limits',
    'bulk10_health_ichia_immigrant_child_coverage': 'ICHIA immigrant-child health coverage',
    'bulk10_health_pdmp_operational_establishment': 'Prescription-monitoring program establishment',
    'bulk10_health_pharmacist_contraception_fertility': 'Pharmacist contraceptive prescribing',
    'bulk10_health_trap_provider_rules_births': 'Abortion-provider regulations',
    'c35_cspp_regulation_mandatory_seatbelts': 'Seat-belt use mandate (primary or secondary enforcement) · occupant fatality rate',
    'c36_cspp_w_guncontrol_bc_dealers': 'Firearm-dealer background checks',
    'b1010_texting_ban_all': 'All-driver texting bans',
    'b1010_truth_in_sentencing': 'Truth-in-sentencing laws',
    'b1010b_gdl_holding_night': 'Graduated-license waiting and night restrictions',
    'bulk10_health_medicaid_sud_imd_waiver': 'Medicaid substance-use institutional waivers',
    'bulk10_health_naloxone_coprescribing_mandate': 'Naloxone coprescribing mandates',
    'b1010_interlock_all_offender': 'All-offender ignition interlocks',
    'b1010b_pain_clinic_law': 'Pain-clinic regulation',
    'bulk10_health_sunday_alcohol_sales_repeal': 'Sunday alcohol-sales ban repeal',
    'c35_cspp_absvot': 'Absentee voting',
    'c35_cspp_regulation_bicycle_helmets': 'Child bicycle-helmet laws',
    'c18_siegel_dvrosurrender': 'DV restraining-order firearm surrender',
    'c18_siegel_loststolen': 'Lost/stolen firearm reporting',
    'c35_cspp_earlvot': 'Early voting',
    'b1010b_employment_first_law': 'Employment-first laws',
}


NAMES = {
    'run2_decentralized_tuition': ('Decentralized public-college tuition setting', '公立大学学费决策分权'),
    'run2_energy_public_benefit_fund': ('Utility-funded renewable-energy and efficiency funds', '电力收费支持的可再生能源与节能基金'),
    'run8_universal_background_check': ('Background checks for all firearm sales, including private sellers', '全部枪支交易背景核查（含私人卖家）'),
    'run1_ems_compact': ('Emergency medical services licensure recognition', '急救医疗服务执照互认（EMS）'),
    'run1_pt_compact': ('Physical-therapy licensure recognition', '物理治疗师执照互认（PT）'),
    'run4_lit_family_planning_income_waiver': ('Medicaid family-planning income waivers', '医疗补助计划生育服务收入资格豁免'),
    'run4_lit_nurse_licensure_compact': ('Original nurse licensure compact', '原版护士执照互认协定'),
    'run7_refundable_eitc': ('Refundable earned income tax credit · single mothers', '可退还劳动所得税收抵免 · 单身母亲'),
    'run8_extreme_risk_protection_order': ('Extreme risk protection orders', '极端风险保护令（ERPO）'),
    'c18_cspp_guncontrol_stand_your_ground': ('Stand-your-ground laws', '无退让义务自卫法（SYG）'),
    'c18_siegel_mcdv': ('Misdemeanor domestic-violence firearm prohibition', '家庭暴力轻罪者禁枪（MCDV）'),
    'c18_siegel_mcdvsurrender': ('Misdemeanor domestic-violence firearm surrender', '家庭暴力轻罪者交枪（MCDV）'),
    'run8_domestic_violence_restraining_order_firearm_restriction': ('Domestic-violence restraining-order firearm prohibition', '家庭暴力限制令对象禁枪（DVRO）'),
}
GATE_NAMES_ZH = {
    'b1010_interlock_all_offender': '所有酒驾违法者点火联锁要求',
    'b1010_open_container': '车内已开封酒精饮料容器限制',
    'b1010_texting_ban_all': '所有驾驶员驾驶时短信禁令',
    'b1010_strict_voter_id': '严格选民身份证明要求',
    'b1010b_bicycle_passing_3ft': '超越自行车须保持 3 英尺间距',
    'bulk10_health_speed_limit_70plus_rural_interstate': '乡村州际公路限速至少 70 英里／小时',
    'b1010b_motorcycle_helmet_repeal': '废除摩托车头盔法',
    'b1010b_hate_crime_sexual_orientation': '性取向仇恨犯罪法',
    'b1010b_novice_cellphone_ban': '新手驾驶员驾驶时手机禁令',
    'b1010b_ban_14c_subminimum': '禁止第 14(c) 条低于最低工资的豁免',
    'b1010b_in_state_tuition_undocumented': '无证移民学生州内学费待遇',
    'run2_autism_insurance': '自闭症治疗保险覆盖',
    'run2_universal_pre_k': '普及学前班',
    'bulk10_health_naloxone_coprescribing_mandate': '纳洛酮联合处方要求',

    'b1010_shall_issue_carry': '符合条件即须发证或免许可携枪 · 1979–2014 年凶杀率',
    'c35_cspp_death_penalty': '废除死刑 · 1979–2014 年凶杀率',
    'c35_cspp_regulation_mandatory_seatbelts': '强制使用安全带（首要或次要执法）· 乘员死亡率',
    'c18_siegel_dvrosurrender': '家庭暴力限制令对象交枪（DVRO）',
    'bulk10_health_medicaid_sud_imd_waiver': '医疗补助物质使用障碍住院服务豁免（SUD/IMD）',
    'bulk10_health_pdmp_operational_establishment': '处方药监测计划建立（PDMP）',
    'bulk10_health_ichia_immigrant_child_coverage': '合法移民儿童医保覆盖（ICHIA）',
    'bulk10_health_trap_provider_rules_births': '堕胎服务机构特别监管（TRAP）',
    'b1010b_pdmp_mandatory_registration': '处方药监测计划强制登记（PDMP）',
    'b1010b_epcs_mandate': '受控药物电子处方（EPCS）',
    'b1010_alr': '行政驾照撤销（ALR）',
    'b1010_bac_008_per_se': '血液酒精浓度 0.08 酒驾标准',
    'b1010b_medicaid_tobacco_cessation': '医疗补助戒烟福利',
    'b1011tl_chip_waiting_period_elimination': '儿童健康保险计划等待期取消（CHIP）',
    'bulk1010_lab_snap_bbce': '营养补助广义分类资格（SNAP）',
    'b1011tl_snap_online_application': '营养补助网上申请（SNAP）',
    'b1011tl_snap_phone_interview_waiver': '营养补助电话面谈豁免（SNAP）',
}
PROXIES = {'run2_breast_density_notification', 'run2_colorectal_screening_coverage',
           'run2_contraception_insurance', 'run2_young_adult_dependent_coverage',
           'run2_meth_precursor_restrictions'}
STOP_REASONS = {
    'pay_secrecy': ('pre-trend check', '前趋势检查'),
    'run7_refundable_eitc': ('pre-trend check', '前趋势检查'),
    'run8_gunshow_background_check': ('placebo check', '安慰剂检查'),
    'adult_use_cannabis': ('placebo check', '安慰剂检查'),
    'primary_seat_belt': ('comparison-state date audit', '对照州日期审计'),
    'castle_doctrine': ('pre-trend check', '前趋势检查'),
}
AMENDED = {'run8_extreme_risk_protection_order', 'c18_siegel_mcdvsurrender',
           'run7_abortion_informed_consent', 'run8_domestic_violence_restraining_order_firearm_restriction'}


def pipeline_descriptor(path, key):
    """Read sample metadata only; no panel outcomes or associations are computed."""
    directory = path.parent
    first_estimate = None
    if key == 'run8_extreme_risk_protection_order':
        comparison = read_json(directory / 'DEVIATION_002_COMPARISON.json')
        original = Path(comparison['original_result_path'])
        original_result = verified_json(original, comparison['original_result_sha256'])
        first_estimate = original_result['registered_results']['primary']['result']['overall']['att']
        directory = original.parent
    card = read_json(directory / 'CARD.json')
    variants = read_json(directory / 'SUPPORT.json')['variants']
    support = next(v for v in variants if v.get('variant') == 'primary')
    cells = support.get('cells', support.get('cell_inventory', []))
    treated = support.get('retained_treated') or sorted({u for c in cells for u in c.get('treated', [])})
    controls = support.get('never_controls') or sorted({u for c in cells for u in c.get('controls', [])})
    if key == 'run8_extreme_risk_protection_order':
        amended = read_json(path)['registered_results']['primary']['result']
        treated = sorted({u for c in amended.get('group_time', []) for u in c.get('treated', [])}) or treated
    years = card['outcome'].get('period_years') or card['outcome'].get('years')
    clock = card['analysis']['clock']
    transition = support.get('transition_year_drop') or 'transition_dropped' in clock
    if clock == 'first_wholly_exposed_year' or transition:
        clock_label = ('first full year; transition year omitted', '首个完整暴露年；剔除过渡年')
    else:
        clock_label = ('adoption calendar year', '采纳日历年')
    if card['treatment'].get('complete_list_source_id') == 'dk_adoption_panel':
        clock_label = ('DellaVigna–Kim panel first-adoption year', 'DellaVigna–Kim 面板首次采纳年')
    if key in {'run4_lit_family_planning_income_waiver', 'run4_lit_nurse_licensure_compact'}:
        clock_label = ('Published-study implementation-date year; partial first year treated', '已发表研究的实施日期年份；部分首年计入处理')
        if key == 'run4_lit_family_planning_income_waiver':
            clock_label = tuple(v + suffix for v, suffix in zip(clock_label,
                ('; birth-year outcome, zero lag', '；出生年结局，零滞后')))
    connector = card['outcome']['connector']
    if 'wonder' in connector or key == 'run8_extreme_risk_protection_order':
        source = ('CDC WONDER mortality', 'CDC WONDER 死亡数据')
    elif 'teen_birth' in connector or connector == 'cdc_natality':
        source = ('NCHS teen-birth rates (y268-sna3)', 'NCHS 青少年出生率（y268-sna3）')
    elif connector == 'cdc':
        source = ('NCHS drug-poisoning mortality (xbxb-epbu)', 'NCHS 药物中毒死亡率（xbxb-epbu）')
    elif 'cps' in connector or key in {'pregnancy_accommodation', 'run1_nurse_overtime'}:
        source = ('Census CPS', '人口普查局 CPS 调查')
    elif connector == 'eia':
        source = ('EIA electricity sales', 'EIA 售电数据')
    else:
        source = ('BLS QCEW', '劳工统计局 QCEW')
    return dict(window=f'{min(years)}–{max(years)}', clock=clock_label, outcome_source=source,
                n_treated=len(treated), n_controls=len(controls), first_estimate=first_estimate)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verified_json(path, digest):
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('L2 source hash mismatch: ' + str(path.relative_to(ROOT)))
    return json.loads(payload)


@lru_cache(maxsize=1)
def snapshot():
    counts = read_json(ROOT / 'scripts/viz_site/data/l2_counts.json')
    data = verified_json(SNAPSHOT, counts['source_sha256'])
    if data['snapshot_utc'] != '2026-10-11T20:45:03Z':
        raise ValueError('Wrong L2 release snapshot')
    for gate, n in data['counts']['not_evaluable_by_gate'].items():
        members = data['not_evaluable_gate_members'][gate]
        if len(members) != n or n != counts['gates'][gate]:
            raise ValueError('L2 gate count mismatch: ' + gate)
    return data


@lru_cache(maxsize=1)
def load_pipeline_rows():
    terminal = read_json(ROOT / 'data/l2_pipeline/registry/TERMINAL_RUNS.json')
    runs = {r['run_id']: r for r in terminal['runs']}
    fixed = {r['design_id']: r for r in snapshot()['estimated_rows']
             if r['group'] == 'registry_terminal'}
    rows = []
    with SOURCES.open(encoding='utf-8', newline='') as stream:
        labels = list(csv.DictReader(stream, delimiter='\t'))
    for label in labels:
        if label['track'] != 'estimated':
            continue
        key = label['design_id']; bound = fixed[key]
        run = runs[bound['registry_run_id']]
        path = ROOT / bound['source_path']; digest = bound['source_sha256_recorded']
        if (run['result_sha256'] != digest or label['run_id'] != run['run_id']
                or label['result_json'] != bound['source_path']):
            raise ValueError('L2 run selection mismatch: ' + key)
        result = verified_json(path, digest)
        primary = result['registered_results']['primary']['result']['overall']
        noise = result['noise_floor']
        # Require the S7 export to carry the same immutable result, including stops.
        export_paths = label['s7_exports_in_package'].split(';')
        for filename in export_paths:
            exported = read_json(path.parent / filename)
            if filename == 'EXPORT.json':
                exported = exported['estimates_page']
            if filename in ('EXPORT.json', 'SITE_EXPORT_DATA.json'):
                if (exported['provenance']['result_sha256'] != digest
                        or exported['result']['registered_results']['primary']['result']['overall'] != primary
                        or exported['result']['status'] != result['status']):
                    raise ValueError('L2 S7 mismatch: ' + key)
        if primary['att'] != bound['point_estimate'] or noise['strictly_exceeds_cutoff'] != bound['beyond_noise']:
            raise ValueError('L2 fixed result mismatch: ' + key)
        interval_meta = primary.get('interval') or {}
        certified = interval_meta.get('reported') and interval_meta.get('coverage_calibrated_for_amended_geometry', True)
        # Release ruling: the uncertified energy source unit receives no interval.
        certified = certified and key != 'run2_energy_public_benefit_fund'
        rows.append(dict(case=key, name=NAMES.get(key, (label['policy_en_draft'], label['policy_zh'])),
            label=(label['outcome_en_draft'], OUTCOME_ZH[label['outcome_en_draft']]),
            full_label=(label['outcome_en_draft'], OUTCOME_ZH[label['outcome_en_draft']]),
            estimate=primary['att'], ci95=primary.get('ci95') if certified else None,
            interval_metadata=interval_meta, reference_available=True,
            reference_threshold=noise['absolute_cutoff'], exceeds=noise['strictly_exceeds_cutoff'],
            ri_p=noise['p_two_sided'], stopped=result['status']=='STOPPED',
            source=bound['source_path'], sha256=digest, run_id=run['run_id'],
            original_units='', order=len(rows)+24, amended=key in AMENDED,
            **pipeline_descriptor(path, key)))
        rows[-1]['label'] = rows[-1]['full_label'] = tuple(v.replace('log points', 'log difference') for v in rows[-1]['label'])
        if key in PROXIES:
            labels = list(rows[-1]['label'])
            if key == 'run2_meth_precursor_restrictions':
                labels = ['All-drug poisoning mortality per 100k, age-adjusted', '全部药物中毒死亡率（每 10 万人，年龄调整）']
            rows[-1]['label'] = rows[-1]['full_label'] = (labels[0] + ' · proxy', labels[1] + ' · 代理结局')
    if len(rows) != 27 or sum(r['stopped'] for r in rows) != 3:
        raise ValueError('L2 pipeline roster changed')
    return rows


@lru_cache(maxsize=1)
def archived_rows():
    from scripts.viz_site.make_synthesize import load_l2
    rows = load_l2()
    fixed = {r['source_path']: r for r in snapshot()['estimated_rows']}
    for row in rows:
        if row.get('row_id'):
            outcome_id = row['row_id'].split('/')[1]
            primary = next(r for r in synthesis_rows() if r['design_id'] == 'prototype:' + row['case']
                           and (r['designated_l3_primary'] or row['case'] == 'paid_sick_leave'))
            row['outcome_role'] = ('joint-primary' if row['case'] == 'paid_sick_leave' else
                                   'primary' if outcome_id == primary['outcome_id'] or row['case'] == 'medicaid_expansion' else 'secondary')
            metadata = dict(row.get('interval_metadata') or {})
            provenance = row['provenance']
            site = verified_json(ROOT / provenance['label_path'], provenance['label_sha256'])
            coverage = (site.get('checks', {}).get('calibration') or {}).get('point_interval_coverage')
            if coverage:
                metadata['simulated_coverage'] = coverage['rate']
            row['interval_metadata'] = metadata
        if row['case'] == 'medicaid_expansion':
            row['name'] = (row['name'][0], '医疗补助扩张')
        row['stopped'] = False
        path = row.get('provenance', {}).get('result_path')
        if path:
            # Batch-1 amendment 001 changed the sensitivity loader only. The
            # fixed snapshot binds the retained original; verify its primary
            # estimate, interval and noise cutoff against the amended display.
            bound = fixed.get(path)
            if bound is None:
                bound = next((r for r in fixed.values()
                    if r['group'] == 'manual_batch' and Path(r['source_path']).stem == row['case']), None)
            if bound is None:
                raise ValueError('Archive result absent from fixed snapshot: ' + path)
            result = verified_json(ROOT / bound['source_path'], bound['source_sha256_recorded'])
            if bound['group'] == 'manual_batch':
                primary = result['registered_results']['primary']['result']['overall']
                if (primary['att'] != row['estimate'] or primary['ci95'] != row['ci95']
                        or result['noise_floor']['absolute_cutoff'] != row['reference_threshold']):
                    raise ValueError('Archive primary disagrees with fixed snapshot: ' + row['case'])
            row.update(source=bound['source_path'], sha256=bound['source_sha256_recorded'],
                       run_id=bound['design_id'])
            if path != bound['source_path']:
                row.update(amended_source=path,
                    amended_sha256=hashlib.sha256((ROOT / path).read_bytes()).hexdigest())
    return rows


def number(v):
    digits = max(0, 2 - math.floor(math.log10(abs(v)))) if v else 0
    return f'{float(format(v, ".3g")):,.{digits}f}'.replace('-', '−')


def esc(value):
    return html.escape(str(value), quote=True)


def interval_text(row, lang):
    ci = row.get('ci95')
    if not ci:
        return '无区间' if lang == 'zh' else 'no interval'
    approx = (row.get('interval_metadata') or {}).get('approximate')
    prefix = ('近似名义 95%' if approx else '名义 95%') if lang == 'zh' else ('Approx. nominal 95%' if approx else 'Nominal 95%')
    return f'{prefix} [{number(ci[0])}, {number(ci[1])}]'


def noise_status(row, lang):
    if row.get('stopped'):
        active = dict(row, stopped=False)
        return ('已停止：' if lang == 'zh' else 'Stopped: ') + STOP_REASONS[row['case']][lang == 'zh'] + ' · ' + noise_status(active, lang)
    if not row['reference_available']:
        return '重排比较待补' if lang == 'zh' else 'Reassignment comparison pending'
    return ('超噪声底线' if row['exceeds'] else '噪声底线内') if lang == 'zh' else ('Beyond noise floor' if row['exceeds'] else 'Within noise floor')


def attrs(row):
    return f'data-outcome-role="{row.get("outcome_role", "primary")}" data-reference="{str(row["reference_available"]).lower()}" data-exceeds="{str(row["exceeds"] is True).lower()}" ' + ' '.join(f'data-{key}="{esc(row[value])}"' for key, value in
                    [('design', 'case'), ('source', 'source'), ('source-sha256', 'sha256'), ('run-id', 'run_id'), ('amended-source', 'amended_source'),
                     ('amended-source-sha256', 'amended_sha256')] if value in row)


def method_tag(row, lang):
    if not row.get('ci95'):
        return ''
    method = (row.get('interval_metadata') or {}).get('method', '')
    if method == 'webb_wcu_t':
        return 'Webb wild-cluster-t'
    if method == 'control_path_ct':
        return 'control-path Conley–Taber'
    if 'Mammen' in method:
        return 'Mammen multiplier' if lang == 'en' else 'Mammen 乘子'
    if 'Gaussian' in method:
        coverage = (row.get('interval_metadata') or {}).get('simulated_coverage')
        tag = 'Gaussian multiplier' if lang == 'en' else '高斯乘子'
        if coverage is not None:
            tag += (f' · {coverage:.1%} simulated coverage' if lang == 'en' else f' · 模拟覆盖率 {coverage:.1%}')
        return tag
    raise ValueError('Missing interval method: ' + row['case'])


def display_row(row):
    row = dict(row)
    if row['original_units'] == 'proportion':
        row['estimate'] *= 100
        row['ci95'] = [v * 100 for v in row['ci95']] if row['ci95'] else None
        if row['reference_threshold'] is not None:
            row['reference_threshold'] *= 100
        row['original_units'] = 'percentage points'
    return row


def outcome_role(row, lang):
    return {'primary': ('Primary', '主结局'), 'secondary': ('Secondary', '次结局'),
            'joint-primary': ('Joint primary', '共同主结局')}[row.get('outcome_role', 'primary')][lang == 'zh']


@lru_cache(maxsize=1)
def synthesis_rows():
    return verified_json(ROOT / 'data/l3_meta/l2_set_v2.json',
        'fe273c0c29a05ad068190fe06c48331ed68dd24f5ab1b89c40f8934963760651')['rows']


def render_additions(lang):
    from scripts.viz_site.make_synthesize import STUDY_RECORDS
    zh = lang == 'zh'; idx = int(zh)
    headers = ('政策与结局', '估计 [名义 95% 区间] · 噪声底线') if zh else ('Policy and outcome', 'Estimate [nominal 95% interval] · Noise floor')
    body = []
    for raw in archived_rows() + load_pipeline_rows() + stopped_prototypes():
        row = display_row(raw)
        outcome = outcome_role(row, lang) + ' · ' + row['label'][idx]
        unit = display_unit(row, lang)
        if unit:
            outcome += ' · ' + unit
        metadata = []
        if row['case'] in STUDY_RECORDS:
            window, clock, source = STUDY_RECORDS[row['case']]
            metadata += [window, clock[idx], source[idx]]
            result = verified_json(ROOT / row['source'], row['sha256'])['registered_results']['primary']['result']
            cohorts = {}
            cells = result['group_time']
            for cell in cells:
                cohorts[cell['g']] = max(cohorts.get(cell['g'], 0), cell['n_treated'])
            controls = sorted({c['n_control'] for c in cells})
            control_n = str(controls[0]) if len(controls) == 1 else f'{controls[0]}–{controls[-1]}'
            metadata.append(f'{sum(cohorts.values())} 处理州 / {control_n} 对照州' if zh else f'{sum(cohorts.values())} treated / {control_n} comparison states')
        if 'window' in row:
            metadata += [row['window'], row['clock'][idx],
                         f'{row["n_treated"]} 处理州 / {row["n_controls"]} 对照州' if zh else f'{row["n_treated"]} treated / {row["n_controls"]} comparison states', row['outcome_source'][idx]]
        ci = row['ci95']
        estimate = number(row['estimate'])
        if ci:
            estimate += f' [{number(ci[0])}, {number(ci[1])}]'
        elif not row['stopped']:
            estimate += ' · 无区间' if zh else ' · no interval'
        floor = row['reference_threshold']
        if floor is not None:
            estimate += f' <span class="l2-floor">±{number(floor)}</span>'
        tag = method_tag(row, lang)
        if tag:
            estimate += f'<span class="l2-method">{esc(tag)}</span>'
        if row['stopped']:
            estimate += f'<span class="l2-muted">{esc(noise_status(row, lang))}</span>'
        if row.get('first_estimate') is not None:
            estimate += '<span class="l2-muted">'+('首次运行：' if zh else 'First run: ')+number(row['first_estimate'])+'</span>'
        body.append(f'<tr {attrs(row)}><td><b>{esc(display_name(row,lang))}</b><span class="l2-outcome">{esc(outcome)}</span></td><td class="l2-result-value">{estimate}</td></tr>')
        if metadata:
            body.append('<tr class="l2-sample"><td colspan="2">'+esc(' · '.join(metadata))+'</td></tr>')
    stops = {r['design_id']: r for r in snapshot()['not_evaluable_rows'] if r['gate_en']=='pre_run_stop'}
    for key, row in stops.items():
        label = GATE_NAMES_ZH.get(key, row['label_zh']) if zh else GATE_NAMES_EN[key]
        detail = f'{row["feasible_draws"]}/{row["requested_draws"]}'
        desc = f'运行前停止：{detail} 次重排可估计；规定至少 90%' if zh else f'Stopped before run: reassignment estimable in {detail} draws; rule requires 90%'
        body.append(f'<tr data-design="{esc(key)}" data-prerun="true"><td><b>{esc(label)}</b></td><td>{desc}</td></tr>')
    clock_note = ('十项 DellaVigna–Kim 行使用面板首次采纳年。护士协定与计划生育服务豁免使用已发表研究的实施日期年份，部分首年计入处理。' if zh else 'Ten DellaVigna–Kim rows use panel first-adoption years. The nurse compact and family-planning waivers use published-study implementation-date years, counting partial first years as treated.')
    return '<section class="l2-additions"><p class="l2-muted">'+clock_note+'</p><table class="we-summary l2-results"><thead><tr>'+''.join(f'<th scope="col">{h}</th>' for h in headers)+'</tr></thead><tbody>'+''.join(body)+'</tbody></table></section>'


def render_gates(lang):
    zh = lang == 'zh'; idx = int(zh)
    rows_by_id = {r['design_id']: r for r in snapshot()['not_evaluable_rows']}
    groups = []
    for gate, labels in GATES.items():
        ids = snapshot()['not_evaluable_gate_members'][gate]
        names = [esc(GATE_NAMES_ZH.get(key, rows_by_id[key]['label_zh']) if zh else GATE_NAMES_EN[key]) for key in ids]
        groups.append(f'<div data-gate="{gate}" data-count="{len(ids)}"><h4>{labels[idx]} · {len(ids)}</h4><p>'+('；' if zh else '; ').join(names)+'</p></div>')
    n = snapshot()['counts']['overall']['not_evaluable']
    title = f'未估计：{n} 个设计' if zh else f'Not estimated: {n} designs'
    support = ('支持门槛：处理州、采纳队列、对照州及前后期长度，见' if zh else 'Support minimums cover treated states, adoption cohorts, comparison states and pre/post periods; see')
    return f'<details class="l2-gates"><summary>{title}</summary><p>{support} <a href="{"/zh" if zh else ""}/documentation.html#synthesis-appendix">'+('方法' if zh else 'Method')+'</a>'+('。' if zh else '.')+'</p>'+''.join(groups)+'</details>'+render_sources(lang)


def stopped_prototypes():
    names = {
        'adult_use_cannabis': ('Adult-use cannabis legalization', '成人大麻合法化', 'Income per capita · log difference', '人均收入 · 对数差'),
        'primary_seat_belt': ('Primary seat-belt enforcement', '安全带首要执法', 'Occupant fatality rate · log difference', '乘员死亡率 · 对数差'),
        'castle_doctrine': ('Castle doctrine', '城堡法', 'Homicide rate · log difference', '凶杀率 · 对数差'),
    }
    rows = []
    for bound in snapshot()['estimated_rows']:
        key = bound['design_id'].removeprefix('prototype:')
        if key not in names:
            continue
        verified_json(ROOT / bound['source_path'], bound['source_sha256_recorded'])
        meta = next(r for r in synthesis_rows() if r['design_id'] == 'prototype:' + key)
        if meta['b'] != bound['point_estimate'] or meta['source']['sha256'] != bound['source_sha256_recorded']:
            raise ValueError('Stopped prototype differs from synthesis: ' + key)
        floor = meta['noise']['absolute_cutoff']
        labels = names[key]
        rows.append(dict(case=key, name=labels[:2], label=labels[2:], full_label=labels[2:],
            estimate=bound['point_estimate'], ci95=None, reference_available=floor is not None,
            reference_threshold=floor, exceeds=meta['noise_exceedance'], stopped=True, original_units='',
            source=bound['source_path'], sha256=bound['source_sha256_recorded'],
            run_id=bound['design_id'], order=51+len(rows)))
    return rows


def display_unit(row, lang):
    unit = row['original_units']
    if lang == 'zh':
        return {'jobs': '个岗位', 'proportion': '比例', 'percentage points': '百分点',
                'log points': '对数差'}.get(unit, unit)
    return 'log difference' if unit == 'log points' else unit


def display_name(row, lang):
    name = row['name'][lang == 'zh']
    if lang == 'zh':
        name = name.replace('须发放持枪许可', '符合条件即须发证的隐蔽持枪许可法').replace('薪资历史禁问', '禁止询问薪资历史').replace('流产知情同意', '人工流产知情同意')
    if row['case'] == 'death_penalty_abolition':
        name += ' · 1996–2019'
    if row.get('amended'):
        name += ' · 修订重跑' if lang == 'zh' else ' · Amended rerun'
    return name


def render_noise(lang):
    from scripts.viz_site.make_synthesize import svg_plot, axis
    zh = lang == 'zh'; idx = int(zh)
    rows = [dict(row) for row in archived_rows() + load_pipeline_rows() + stopped_prototypes()]
    rows = [display_row(row) for row in rows]
    active = [r for r in rows if r['reference_available']
              and r['case'] != 'run2_energy_public_benefit_fund']
    active.sort(key=lambda r: -abs(r['estimate']) / r['reference_threshold'])
    rest = [r for r in rows if r not in active]
    extent = math.ceil(max(abs(v) / r['reference_threshold'] for r in active
                          for v in [r['estimate'], *(r['ci95'] or [])])) + .25
    pieces = []
    heading = '估计／噪声底线' if zh else 'Estimate / noise floor'
    pieces.append(f'<div class="l2-noise-row l2-shared-axis"><span>{heading}</span><div>'+axis(-extent, extent, ticks=[-extent, -1, 0, 1, extent])+'</div><span>'+('原单位估计' if zh else 'Native estimate')+'</span></div>')
    for row in active + rest:
        label = display_name(row, lang)
        outcome = outcome_role(row, lang) + ' · ' + row['full_label'][idx]
        if row['case'] == 'run2_energy_public_benefit_fund':
            outcome = outcome_role(row, lang) + ' · ' + ('每客户售电量' if zh else 'Electricity sales per customer')
        unit = display_unit(row, lang)
        if unit:
            outcome += ' · ' + unit
        status = noise_status(row, lang)
        reading = number(row['estimate'])
        if row in active:
            threshold = row['reference_threshold']
            description = f'{outcome}; {reading}; {interval_text(row, lang)}; ±{number(threshold)}; {status}'
            plot = svg_plot('l2-noise-'+str(row['order']), label, description,
                row['estimate']/threshold,
                [v/threshold for v in row['ci95']] if row['ci95'] else None,
                -extent, extent, threshold=1, color='within', hollow=not row['exceeds'])
            if row['stopped']:
                plot = '<div class="l2-stopped">' + plot + '</div>'
            else:
                status = ('超噪声底线' if zh else 'Beyond noise floor') if row['exceeds'] else ''
        elif row['stopped']:
            plot = f'<span class="l2-stopped">{esc(status)}</span>'
            status = ''
        elif row['case'] == 'run2_energy_public_benefit_fund':
            plot = '<span class="l2-muted">'+('单位待认证' if zh else 'Unit awaiting certification')+'</span>'
            reading = '—'
        else:
            plot = '<span class="l2-muted">'+('参照待补' if zh else 'Reference pending')+'</span>'; status = ''
        tag = method_tag(row, lang)
        if tag:
            short_tag = {'Webb wild-cluster-t': 'Webb t', 'control-path Conley–Taber': 'Conley–Taber', 'Gaussian multiplier': 'Gaussian', 'Mammen multiplier': 'Mammen'}.get(tag, tag)
            coverage = (row.get('interval_metadata') or {}).get('simulated_coverage')
            if 'Gaussian' in tag or '高斯' in tag:
                short_tag = ('Gaussian' if not zh else '高斯') + (f' · {coverage:.1%}' if coverage is not None else '')
            status += f'<span title="{esc(tag)}">{esc(short_tag)}</span>'
        pieces.append(f'<div class="l2-noise-row" data-outcome-order="{row["order"]}" {attrs(row)}>'
            f'<div class="l2-noise-label"><b>{esc(label)}</b><span>{esc(outcome)}</span></div>'
            f'<div class="l2-noise-plot">{plot}</div>'
            f'<div class="l2-noise-reading"><b>{reading}</b><span>{status}</span></div></div>')
    title = '估计相对于噪声底线' if zh else 'Estimates relative to their noise floor'
    key = ('实心点超出灰带；停止行用灰色；线为名义 95% 区间。主、次结局按绝对估计／底线降序。' if zh else
           'Filled dots lie outside the grey band; stopped rows are grey; lines are nominal 95% intervals. Primary and secondary outcomes, sorted by absolute estimate / floor.')
    definition = ('无可绘参照的行置末。主结局含诊断停止，与综合页 5/45（手工 4/18，流水线 1/27）对应。噪声底线：在符合条件的州之间随机重排采纳日期所得估计绝对值的第 95 百分位。流水线与手工批次无区间表示未通过适用的至少 93% 覆盖率校准；原型高斯区间逐行标模拟覆盖率。能源基金因单位未认证省略图点与区间。' if zh else
                  'Unplotted references last. Primary outcomes include diagnostic stops and match Synthesize: 5/45 (hand-built 4/18, pipeline 1/27). Noise floor: the 95th percentile of absolute estimates from randomly reassigning adoption dates across eligible states. Missing pipeline and hand-built batch intervals did not pass the applicable 93% coverage calibration; prototype Gaussian intervals carry their simulated coverage by row. The energy-fund point and interval are omitted for uncertified units.')
    more = '噪声底线与区间' if zh else 'Noise floor and intervals'
    return f'<section class="l2-noise" id="cross-case-noise-floor"><figure><figcaption><h2>{title}</h2><p class="l2-muted">{key}</p><details class="l2-definition"><summary>{more}</summary><p class="l2-muted">{definition}</p></details></figcaption>'+''.join(pieces)+'</figure></section>'


@lru_cache(maxsize=1)
def wonder_receipts():
    raw = ROOT / 'data/l2_pipeline/connectors_v2/wonder/raw'
    names = (
        'wonder_homicide_deaths__D76_1999-2017',
        'wonder_firearm_deaths__D76_1999-2017',
        'wonder_firearm_deaths__D158_2018-2024',
        'wonder_firearm_homicide_deaths__D76_1999-2017',
        'wonder_firearm_homicide_deaths__D158_2018-2024',
    )
    rows = []
    for name in names:
        receipt = read_json(raw / (name + '.tsv.receipt.json'))
        # Read bytes only to check provenance; do not parse or expose small cells.
        if hashlib.sha256((raw / receipt['file']).read_bytes()).hexdigest() != receipt['sha256']:
            raise ValueError('WONDER source hash mismatch: ' + name)
        date = datetime.strptime(receipt['query_date_as_printed_by_wonder'], '%b %d, %Y %I:%M:%S %p')
        rows.append(dict(dataset='D76' if '__D76_' in name else 'D158', measure=('firearm_homicide' if 'firearm_homicide' in name else 'firearm' if 'firearm_' in name else 'homicide'), database=receipt['database'], version=receipt['database_version'],
                         query_date=date.strftime('%Y-%m-%d'), sha256=receipt['sha256']))
    return rows


def render_sources(lang):
    zh = lang == 'zh'
    date = snapshot()['snapshot_utc'][:10]
    title = '来源、版本与查询日期' if zh else 'Sources, versions and query dates'
    records = []
    for r in wonder_receipts():
        measure = {'firearm_homicide': '枪支凶杀', 'firearm': '枪支死亡', 'homicide': '全部方式凶杀'}[r['measure']]
        version = ('D76：2020 版，合并种族分类；取 1999–2017 年' if r['dataset'] == 'D76' else 'D158：2024 版，单一种族分类；取 2018–2024 年')
        label = ('CDC WONDER ' + measure + ' · ' + version) if zh else ('CDC WONDER ' + r['dataset'] + ' · ' + r['database'] + ' · ' + r['measure'].replace('_', ' ') + ' · ' + r['version'].replace('dataset_vintage ', 'version '))
        records.append(f'<li data-source-sha256="{r["sha256"]}">{esc(label)} · {r["query_date"]}</li>')
    records += [
        '<li><a href="https://data.cdc.gov/resource/y268-sna3.csv">NCHS '+('各州青少年出生率，1990–2019' if zh else 'state teen-birth rates, 1990–2019')+'</a> · y268-sna3</li>',
        '<li><a href="https://data.cdc.gov/resource/xbxb-epbu.csv">NCHS '+('药物中毒死亡率' if zh else 'drug-poisoning mortality')+'</a> · xbxb-epbu</li>',
    ]
    amendment = ('批次 1 修订 001 修复敏感性加载；主估计保持原值。四项后续修订重跑已逐行标记。' if zh else
                 'Batch 1 amendment 001 repaired sensitivity loading; primary estimates are unchanged. Four later amended reruns are tagged by row.')
    return f'<footer class="l2-sources"><details><summary>{title} · {date}</summary><ul>'+''.join(records)+f'</ul><p>{amendment}</p></details></footer>'
