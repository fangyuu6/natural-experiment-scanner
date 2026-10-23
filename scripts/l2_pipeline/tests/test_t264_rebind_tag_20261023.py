"""T264: (1) the STOP 'Stacked WLS differs from registered weighted ATT' (frozen code snapshot older than the T258 relative-tolerance fix)
has its own impact tag, so a C57 rebind can bind to the retained reason. Strings only; no data, no design state.
(2) final_decisions accepts the new gate name R3.1_INTERVAL_FALLBACK (temp ledger only)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline import final_decisions, maintenance

TAG = 'code_fix_inference_tolerance'
REASONS = (  # the retained calibrate STOP reasons of requeue_v3 (continued_weeks_claimed) and requeue_v4 (weeks_compensated), verbatim
    'ValueError: Local calibrate failed: ValueError: Stacked WLS differs from registered weighted ATT',
    'ValueError: Local calibrate failed: ValueError: Stacked WLS differs from registered weighted ATT',
)
# PATTERNS and FIXES exactly as they were before T264 (the new tag is the only addition).
OLD_PATTERNS = {
 's2_path': ('no local', 'missing declared public file', 'outcome.paths', 'source path'),
 's0_construct': ('v6.2 target/population', 'same-construct', 'construct question', 'v6.3'),
 's3_support': ('support insufficient', 's3 '),
 'ri_feasibility': ('ri199', 'ri999', 'ri feasibility'),
 'fresh_pool': ('fresh v3', 'ten-treated', 'fresh pool'),
 'history_status': ('invalid coding status', 'insufficient_work'),
 'history_universe': ('complete universe once',),
 'preparation_binding': ('existing preparation artifact changed', 'preparatory snapshot', 'preparation binding', 'single review resolution signer differs'),
 'packet_released': ('sonnet packet released by workers',),
 'review_should': ('review should', 'should requires', 'novel/science finding requires explicit validated hub closure'),
 'draft_load': ('dry-load', 'scientific card', 'metadata csv', 'metadata-selected comparison group', 'metadata named', 'comparison group', 'registered loader'),
 'agent_contract': ('contract_rejected', 'schema_format'),
 'history_blindness': ('history packet', 'screening adoption'),
 'code_fix_c84_f1': ('purpose direction requires public source locators',),
 'code_fix_c84_purpose': ('purpose citation must identify supported fields',),
 'code_fix_c78': ("'list' object has no attribute 'get'",),
 'code_fix_named_panel_c42': ('c42 overlay removed existing exclusions',),
 'code_fix_named_panel_c64': ('purpose direction requires s0 evidence bound to the target',),
 'code_fix_forecast_model_id': ('forecast model differs from registered slot',),
}
OLD_FIXES = {
 'prescreen': ['s2_path', 's0_construct', 's3_support', 'ri_feasibility', 'fresh_pool'],
 'draft_dry_load': ['draft_load', 'preparation_binding'],
 'history_contract': ['history_status', 'history_universe'],
 'population_metadata': ['s0_construct'],
 'mechanical_reprep': ['preparation_binding'],
 'evidence_should': ['review_should'],
 'collector_self_check': ['history_status', 'history_universe', 'agent_contract'],
 'history_packet_ci': ['history_blindness'],
 'scoped_requeue': ['requeue_scope'],
 'daily_hub_batch': ['hub_queue'],
 'packet_released_rebind': ['packet_released'],
}
OLD_CODE_DEFECT_FIXED = ['code_fix_c78', 'code_fix_c84_f1', 'code_fix_c84_purpose', 'code_fix_named_panel_c42', 'code_fix_named_panel_c64', 'code_fix_forecast_model_id']


class RebindTagTests(unittest.TestCase):
    def test_tag_matches_both_stop_reasons_and_nothing_else_matches(self):
        for reason in REASONS:
            self.assertEqual(maintenance.reason_tags(reason), [TAG])
        self.assertEqual(maintenance.reason_tags('stacked wls differs from registered weighted att'), [TAG])
        for other in ('ValueError: Local calibrate failed: ValueError: Stacked WLS', 'ValueError: Local calibrate failed: ValueError: something else',
                      "AttributeError: 'NoneType' object has no attribute 'get'"):
            self.assertNotIn(TAG, maintenance.reason_tags(other))

    def test_require_match_passes_for_the_rebind_tags_and_fails_without_the_new_tag(self):
        tags = [TAG, 'draft_load', 'review_should']
        for reason in REASONS:
            self.assertEqual(maintenance.require_match(reason, tags), [TAG])
            with self.assertRaises(ValueError):
                maintenance.require_match(reason, ['draft_load', 'review_should'])
            with self.assertRaises(ValueError):
                maintenance.require_match(reason, [TAG, 'not_a_tag'])
        with self.assertRaises(ValueError):  # a different retained reason does not match this tag
            maintenance.require_match('ValueError: Local calibrate failed: ValueError: something else', tags)

    def test_tag_is_a_known_code_defect_fix_but_has_no_auto_supersede(self):
        self.assertIn(TAG, maintenance.FIXES['code_defect_fixed'])
        self.assertNotIn(TAG, maintenance.CODE_FIXES)  # resume_code_fix only supersedes CODE_FIXES tags: the rebind is the only route
        self.assertEqual(sorted(maintenance.CODE_FIXES), ['code_fix_c78', 'code_fix_c84_f1', 'code_fix_c84_purpose'])

    def test_existing_tags_patterns_and_fixes_are_unchanged(self):
        self.assertEqual(set(maintenance.PATTERNS), set(OLD_PATTERNS) | {TAG})
        for tag, patterns in OLD_PATTERNS.items():
            self.assertEqual(maintenance.PATTERNS[tag], patterns, tag)
        self.assertEqual(maintenance.PATTERNS[TAG], ('stacked wls differs from registered weighted att',))
        self.assertEqual(set(maintenance.FIXES), set(OLD_FIXES) | {'code_defect_fixed'})
        for name, tags in OLD_FIXES.items():
            self.assertEqual(maintenance.FIXES[name], tags, name)
        self.assertEqual(maintenance.FIXES['code_defect_fixed'], OLD_CODE_DEFECT_FIXED + [TAG])

    def test_every_pattern_tag_is_a_declared_fix_tag_or_known_before(self):
        declared = {t for v in maintenance.FIXES.values() for t in v}
        self.assertIn(TAG, declared)
        self.assertEqual(sorted(set(maintenance.PATTERNS) - declared), sorted(set(OLD_PATTERNS) - declared))

    def test_old_reasons_keep_their_tags(self):
        self.assertEqual(maintenance.reason_tags("AttributeError: 'list' object has no attribute 'get'"), ['code_fix_c78'])
        self.assertEqual(maintenance.reason_tags('ValueError: NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure'), ['review_should'])
        self.assertEqual(maintenance.reason_tags('Forecast model differs from registered slot'), ['code_fix_forecast_model_id'])


GDL = dict(design_id='b1010b_gdl_holding_night', attempt='requeue_v5', decision='NOT_EVALUABLE', gate='R3.1_INTERVAL_FALLBACK',
           basis='Webb 922/1000 below the 0.93 floor; CT needs 5 certified never-control paths, has 1 (VT); R3.1 item 5: no headline from the randomization p alone',
           state_at_closure='STOP', park_time_utc='2026-10-22T23:36Z', source='docs/plans/l2-pipeline/HUB_RULING_20261023_LAST_STOPS.md')
OLD_GATES = {'GATE0_PARK_S3_RI', 'S0_NO_SAME_CONSTRUCT_SERIES', 'GATE0_PARK_S3_SIBLING', 'AUDIT_V3_1', 'HISTORY_CONTRACT_EXHAUSTED', 'HISTORY_INSUFFICIENT',
             'PURPOSE_NO_SERIES', 'RECONCILE_C43_GATES', 'RI_FEASIBILITY_0.90', 'S0_OUTCOME_UNAVAILABLE', 'S2_C46', 'S3_COUNT_FLOOR', 'S3_SERIES_HOLES',
             'S3_SIDECAR_PRESCREEN'}


class GateNameTests(unittest.TestCase):
    def test_validate_accepts_the_new_gate_and_only_adds_that_one(self):
        final_decisions.validate(dict(GDL))
        self.assertEqual(set(final_decisions.APPROVED_GATES), OLD_GATES | {'R3.1_INTERVAL_FALLBACK'})
        with self.assertRaises(ValueError):
            final_decisions.validate(dict(GDL, gate='R3.1_INTERVAL_FALLBACK_X'))
        with self.assertRaises(ValueError):
            final_decisions.validate(dict(GDL, decision='ESTIMATED'))

    def test_append_dry_run_and_apply_on_a_temporary_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'LEDGER.json'
            ledger.write_text(json.dumps(dict(schema='l2_final_decisions_v1', designs=[], count=0, by_gate={})))
            dry = final_decisions.append([GDL], registry=tmp, ledger='LEDGER.json', task='T264')
            self.assertEqual((dry['new'], dry['written']), (['b1010b_gdl_holding_night'], False))
            done = final_decisions.append([GDL], registry=tmp, ledger='LEDGER.json', task='T264', apply=True)
            self.assertTrue(done['written'])
            value = json.loads(ledger.read_text())
            self.assertEqual((value['count'], value['by_gate']), (1, {'R3.1_INTERVAL_FALLBACK': 1}))
            again = final_decisions.append([copy.deepcopy(GDL)], registry=tmp, ledger='LEDGER.json', task='T264X', apply=True)
            self.assertEqual((again['new'], again['present'], again['written']), ([], ['b1010b_gdl_holding_night'], False))


if __name__ == '__main__':
    unittest.main()
