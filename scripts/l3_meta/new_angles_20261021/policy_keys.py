#!/usr/bin/env python3
"""T232: hand-set policy_key (one key per underlying law; designs that evaluate the same law on different outcomes share a key).
Written BEFORE any effect estimate was read. Output: data/l3_meta/new_angles_20261021/inputs/policy_keys.csv"""
import csv, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
live = json.load(open(ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"))
D = [x for x in live['designs'] if x['status'] == 'estimated']
OVR = {
 'medical_cannabis': ['did-82cc3e884d0ca4f8ce01', 'b1012cr_medical_cannabis_law', 'b1015q3_medical_cannabis_law_adult_binge_drinking',
                      'b1016q4_medical_cannabis_law_drinking_driver_crash_deaths', 'b1016q4_medical_cannabis_law_property_crime'],
 'adult_use_cannabis': ['prototype:adult_use_cannabis', 'b1015q3_adult_use_cannabis_legalization_adult_binge_drinking',
                        'b1015q3_adult_use_cannabis_legalization_traffic_deaths', 'b1016q4_adult_use_cannabis_legalization_property_crime'],
 'pdmp_mandatory_use': ['b1010b_pdmp_mandatory_query', 'b1015q3_pdmp_mandatory_query_opioid_overdose_deaths', 'b1016q4_pdmp_mandatory_query_drug_overdose_deaths'],
 'handheld_phone_ban': ['b1010_handheld_ban_all', 'b1016q4_handheld_ban_all_motor_vehicle_occupant_fatalities'],
 'speed_limit_65': ['b1016q4_speed_limit_65_rural_interstate_1987_motor_vehicle_occupant_fatalities', 'r1011_speed_limit_65_rural_interstate_1987'],
 'ui_alt_base_period': ['b1011cfl_ui_alternative_base_period', 'b1016q4_ui_alternative_base_period_ui_weeks_compensated'],
 'shall_issue_carry': ['prototype:shall_issue', 'b1016q4_shall_issue_carry_ucr_robbery'],
 'primary_seat_belt': ['prototype:primary_seat_belt', 'b1010_primary_seatbelt'],
 'renewable_portfolio_standard': ['prototype:renewable_portfolio', 'b1011c_rps_binding_standard'],
 'refundable_eitc': ['did-c2e2f141c074e2a2eab4', 'run7_refundable_eitc'],
 'stand_your_ground': ['prototype:castle_doctrine', 'c18_cspp_guncontrol_stand_your_ground'],
 'ban_the_box': ['did-58827a36a4167bcf0ad1', 'did-a0c08d8d5a891ede06b1'],
}
inv = {d: k for k, v in OVR.items() for d in v}
rows = []
for x in D:
    did = x['design_id']
    key = inv.get(did) or (x['policy_name'] or did).strip().lower().replace(' ', '_')
    rows.append(dict(design_id=did, policy_key=key, domain=x['domain']))
out = ROOT / "data/l3_meta/new_angles_20261021/inputs/policy_keys.csv"
with open(out, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=['design_id', 'policy_key', 'domain']); w.writeheader(); w.writerows(rows)
import collections
c = collections.Counter(r['policy_key'] for r in rows)
print(len(rows), 'designs', len(c), 'policy keys;', 'multi:', {k: v for k, v in c.items() if v > 1})
