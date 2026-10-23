"""R3.1 RI feasibility floor (HUB_RULING_20261022_GATE_RELAX section 1). Invented records only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline import gate_relax as G
from scripts.l2_pipeline.engine import ri
from scripts.l2_pipeline.storage import sha

LEAD = 'RI lead inventory changed under relabeling'
ID = 'b1010_open_container'
GOOD_BLOCK = dict(rule='R3.1', floor=0.8, ruling_sha256='a' * 64, statement=G.STATEMENT)


def records(n, infeasible, reason=LEAD):
    return [dict(infeasible=True, reason=reason) if i < infeasible else dict(infeasible=False) for i in range(n)]


def block(**changes):
    value = dict(GOOD_BLOCK, **changes)
    return {k: v for k, v in value.items() if v is not None}


class DefaultPathTests(unittest.TestCase):
    def test_default_inventory_literal_output(self):
        # Literal expected dicts (identical to the pristine base copy's inventory output).
        got = ri.inventory([dict(infeasible=False), dict(infeasible=True, reason='X')])
        self.assertEqual(got, dict(requested_draws=2, feasible_draws=1, infeasible_count=1,
            infeasible_draws=[dict(draw_index=1, reason='X')], feasible_fraction=.5,
            minimum_feasible_fraction=.9, support_pass=False, redrawn=0,
            rule='Fixed draws; condition on feasible assignments; exclude without redraw'))
        got = ri.inventory([dict(infeasible=False)] * 9 + [dict(infeasible=True, reason='Y')])
        self.assertEqual(got['support_pass'], True)
        self.assertEqual(got['minimum_feasible_fraction'], .9)
        self.assertNotIn('ri_floor_version', got)
        self.assertNotIn('reason_codes', got)

    def test_default_threshold_089_fails_090_passes(self):
        self.assertFalse(ri.inventory(records(100, 11))['support_pass'])   # 89 of 100
        self.assertTrue(ri.inventory(records(100, 10))['support_pass'])    # 90 of 100
        self.assertIsNone(ri.inventory([])['feasible_fraction'])

    def test_default_has_no_r31_keys_and_source_floor_is_point_nine(self):
        src = Path(ri.__file__).read_text()
        self.assertIn('minimum_feasible_fraction=.9', src)
        self.assertIn('10*feasible>=9*n', src)

    def test_other_thresholds_unchanged(self):
        from scripts.l2_pipeline import support
        self.assertEqual(support.ENGINE_MINIMUMS, dict(min_treated=6, min_cohorts=2, min_controls=5, min_pre_periods=3))
        self.assertEqual(support.V7_SUPPORT_GAP_RULE, 'drop_affected_state_v7')
        self.assertEqual(support.minimum_post_periods({}), 2)
        self.assertEqual(support.minimum_post_periods({'min_post_periods': 1}), 2)


class BoundaryTests(unittest.TestCase):
    def test_999_boundary_799_fails_800_passes(self):
        self.assertFalse(ri.inventory(records(999, 200), floor=.8)['support_pass'])  # 799
        self.assertTrue(ri.inventory(records(999, 199), floor=.8)['support_pass'])   # 800

    def test_199_boundary_159_fails_160_passes(self):
        self.assertFalse(ri.inventory(records(199, 40), floor=.8)['support_pass'])   # 159
        self.assertTrue(ri.inventory(records(199, 39), floor=.8)['support_pass'])    # 160

    def test_exactly_080_passes_and_089_still_fails_at_default(self):
        self.assertTrue(ri.inventory(records(5, 1), floor=.8)['support_pass'])      # 4 of 5 = exactly 0.80
        self.assertFalse(ri.inventory(records(5, 2), floor=.8)['support_pass'])     # 3 of 5
        self.assertEqual(G.required_draws(5, .8), 4)
        self.assertEqual(G.required_draws(199, .9), 180)
        self.assertFalse(ri.inventory(records(100, 11))['support_pass'])            # 0.89 fails at default
        self.assertTrue(ri.inventory(records(100, 10))['support_pass'])            # 0.90 passes at default

    def test_floor_other_than_080_refused_by_inventory(self):
        for bad in (.85, .7, .9):
            with self.assertRaisesRegex(ValueError, 'exactly 0.8'):
                ri.inventory(records(199, 0), floor=bad)

    def test_require_feasible_names_actual_floor(self):
        with self.assertRaisesRegex(ValueError, 'below 80%'):
            ri.require_feasible(ri.inventory(records(199, 60), floor=.8))
        with self.assertRaisesRegex(ValueError, 'below 90%'):
            ri.require_feasible(ri.inventory(records(199, 30)))


class CardBlockTests(unittest.TestCase):
    def test_whitelist_and_valid_block(self):
        self.assertEqual(G.ri_floor(ID, {'ri_feasibility_floor': GOOD_BLOCK}), .8)
        self.assertIsNone(G.ri_floor('other_design', {}))
        for d in ('b1010_bac_008_per_se', 'b1010_texting_ban_all', 'b1010b_pain_clinic_law_x'):
            with self.assertRaisesRegex(ValueError, 'outside the ruling'):
                G.ri_floor(d, {'ri_feasibility_floor': GOOD_BLOCK})

    def test_wrong_floor_rule_sha_statement_and_extra_keys_refused(self):
        cases = [block(floor=.85), block(floor=.7), block(floor=.9), block(rule='R3.2'),
                 block(ruling_sha256='ABC'), block(ruling_sha256='a' * 63), block(ruling_sha256=None),
                 block(statement='pre-registered'), dict(GOOD_BLOCK, extra=1), {k: v for k, v in GOOD_BLOCK.items() if k != 'rule'},
                 block(floor=True)]
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                G.ri_floor(ID, {'ri_feasibility_floor': bad})

    def test_declaration_floor(self):
        self.assertIsNone(G.floor_from_declaration({'key': ID}))
        self.assertEqual(G.floor_from_declaration({'key': ID, 'ri_feasibility_floor_rule': GOOD_BLOCK}), .8)
        with self.assertRaises(ValueError):
            G.floor_from_declaration({'key': 'b1010_bac_008_per_se', 'ri_feasibility_floor_rule': GOOD_BLOCK})
        # the wrapper in registered.numerical_declaration names the design explicitly (declaration key may differ)
        self.assertEqual(G.floor_from_declaration({'key': 'metadata', 'ri_feasibility_floor_rule': GOOD_BLOCK,
                                                   'ri_feasibility_floor_design': ID}), .8)
        with self.assertRaises(ValueError):
            G.floor_from_declaration({'key': ID, 'ri_feasibility_floor_rule': GOOD_BLOCK,
                                      'ri_feasibility_floor_design': 'b1010_bac_008_per_se'})

    def test_numerical_declaration_wrapper_default_and_r31(self):
        from unittest import mock
        from scripts.l2_pipeline import registered
        base = {'key': ID, 'case': {}}
        with mock.patch.object(registered, '_numerical_declaration_base', return_value=dict(base)):
            default = registered.numerical_declaration({'design_id': ID, 'analysis': {}}, {}, {}, 'primary')
            r31 = registered.numerical_declaration({'design_id': ID, 'analysis': {'ri_feasibility_floor': GOOD_BLOCK}}, {}, {}, 'primary')
            with self.assertRaises(ValueError):
                registered.numerical_declaration({'design_id': 'b1010_bac_008_per_se',
                                                  'analysis': {'ri_feasibility_floor': GOOD_BLOCK}}, {}, {}, 'primary')
        self.assertEqual(default, base)
        self.assertEqual(r31['ri_feasibility_floor_rule'], GOOD_BLOCK)
        self.assertEqual(r31['ri_feasibility_floor_design'], ID)


class ReasonCodeTests(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(G.reason_code(LEAD), 'lead_inventory')
        self.assertEqual(G.reason_code('RI supported family changes under relabeling; no redraw permitted'), 'lead_inventory')
        self.assertEqual(G.reason_code('Permutation has no post support'), 'support_geometry')
        self.assertEqual(G.reason_code('NOT_ASSESSABLE: empty supported pre family'), 'support_geometry')
        self.assertIsNone(G.reason_code('some other failure'))
        self.assertIsNone(G.reason_code(None))

    def test_uncoded_or_foreign_draw_fails_support(self):
        recs = records(199, 0)
        recs[0] = dict(infeasible=True, reason='outcome hole in state CA')
        self.assertFalse(ri.inventory(recs, floor=.8)['support_pass'])
        recs[0] = dict(infeasible=True, reason='Permutation has no post support')
        self.assertTrue(ri.inventory(recs, floor=.8)['support_pass'])

    def test_r31_inventory_keys_and_counts(self):
        out = ri.inventory(records(199, 3), floor=.8)
        self.assertEqual(out['ri_floor_version'], 'R3.1')
        self.assertEqual(out['minimum_feasible_fraction'], .8)
        self.assertEqual(out['reason_codes'], dict(lead_inventory=3, support_geometry=0))


class ScreenRiTests(unittest.TestCase):
    def _run(self, floor_block, failing_after):
        declaration = {'key': ID}
        if floor_block:
            declaration['ri_feasibility_floor_rule'] = GOOD_BLOCK
        calls = [0]

        def build(decl, design, labels):
            calls[0] += 1
            if calls[0] > failing_after:
                raise ValueError(LEAD)
            return {}

        with patch.object(ri, 'predraw_labels', lambda design, count=999: [None] * count), \
             patch.object(ri, 'pre_family', lambda design: [-1]), \
             patch.object(ri, 'relabeled_design', build):
            from scripts.l2_pipeline.prescreen import screen_ri
            result = screen_ri(declaration, None)
        return result, calls[0]

    def test_early_stop_199_default_20_and_r31_40(self):
        # Every draw infeasible from the first: default stops once failures exceed 19 (20th draw).
        result_default, calls_default = self._run(False, failing_after=0)
        result_r31, calls_r31 = self._run(True, failing_after=0)
        self.assertFalse(result_default['passed']); self.assertEqual(calls_default, 20)
        self.assertFalse(result_r31['passed']); self.assertEqual(calls_r31, 40)
        self.assertEqual(result_default['checks']['RI199']['evaluated'], 20)
        self.assertEqual(result_r31['checks']['RI199']['evaluated'], 40)

    def test_early_stop_999_default_100_and_r31_200(self):
        result_default, calls_default = self._run(False, failing_after=199)
        result_r31, calls_r31 = self._run(True, failing_after=199)
        self.assertEqual(calls_default, 299)
        self.assertEqual(calls_r31, 399)
        self.assertEqual(result_default['checks']['RI999']['evaluated'], 299)
        self.assertEqual(result_r31['checks']['RI999']['evaluated'], 399)


class CalibrationAndHeadlineTests(unittest.TestCase):
    def _record(self, infeasible, floor):
        recs = records(199, infeasible)
        feas = ri.inventory(recs, floor=floor)
        return dict(ri_feasibility=feas)

    def test_validate_calibration_accepts_085_only_for_r31(self):
        from scripts.l2_pipeline.registered import validate_calibration
        from scripts.l2_pipeline.engine.estimator import CHECKS
        from scripts.l2_pipeline.engine.simulation import COUNTS, OFFSETS
        from scripts.l2_pipeline.engine.estimator import SEED, RI_SEED

        def base(floor):
            r = dict(counts=COUNTS, seeds=dict(base=SEED, offsets=OFFSETS, RI=RI_SEED),
                     design_binding=dict(design_sha256='a' * 64, operator_sha256='b' * 64),
                     training_tail_probability=.01, targets_met=True, status='ready',
                     critical_values={k: 1.0 for k in CHECKS})
            r.update(self._record(29, floor))  # 170 of 199 feasible = 0.854
            return r
        # Default refuses 0.85 (fails the RI count check before any held-out read).
        with self.assertRaisesRegex(ValueError, 'RI feasibility|below 90%') as default_ctx:
            validate_calibration(base(None))
        self.assertIn('below 90%', str(default_ctx.exception))
        # R3.1 record passes the RI check and reaches the held-out check.
        with self.assertRaises(Exception) as r31_ctx:
            validate_calibration(base(.8))
        self.assertNotIn('RI feasibility', str(r31_ctx.exception))
        self.assertNotIn('below 90%', str(r31_ctx.exception))

    def test_headline_guard_refuses_point_plus_p_under_r31(self):
        from scripts.l2_pipeline.engine import calibration
        with patch('scripts.l2_pipeline.engine.inference.select_method', lambda *a, **k: None):
            with self.assertRaisesRegex(ValueError, 'NOT_ASSESSABLE: R3.1 forbids a headline from the randomization p alone'):
                calibration.certify_intervals({}, expected_designs=[ID], ri_floor_designs=[ID])
            try:
                calibration.certify_intervals({}, expected_designs=[ID])
            except ValueError as exc:
                self.assertNotIn('R3.1', str(exc))


class RequeueHookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        ruling = self.root / G.RULING_PATH
        ruling.parent.mkdir(parents=True)
        ruling.write_bytes(b'invented ruling bytes for test')
        self.ruling = ruling
        self.case = self.root / 'case'
        self.case.mkdir()
        (self.case / 'PARKED.json').write_text(json.dumps(dict(action='ri', reason=G.PARK_REASON)))
        self.ruling_sha = sha(ruling)
        self.old_payload = dict(design_id=ID, version='v1', track='DiD', analysis=dict(min_post_periods=2))
        self.old_card = self.root / 'old.json'
        self.old_card.write_text(json.dumps(self.old_payload))
        self.old = dict(design_id=ID, card=str(self.old_card))
        self.pilot = type('P', (), dict(repo=self.root, case_dir=lambda s, row: self.case))()

    def tearDown(self):
        self._tmp.cleanup()

    def new_payload(self, **changes):
        new = json.loads(json.dumps(self.old_payload))
        new['version'] = 'v2'
        new['analysis']['ri_feasibility_floor'] = dict(rule='R3.1', floor=.8, ruling_sha256=self.ruling_sha, statement=G.STATEMENT)
        new.update(changes)
        return new

    def decision(self, **changes):
        value = dict(decision='resume', execution_authorized=False, superseded_status=G.SUPERSEDED,
                     implementation_rule=G.IMPLEMENTATION, authority=dict(path=str(self.ruling), sha256=self.ruling_sha))
        value.update(changes)
        return value

    def check(self, new, decision, old=None):
        return G.validate_ruling(self.pilot, old or self.old, new, decision, self.root / 'new.json')

    def test_valid_pair_passes(self):
        self.assertEqual(self.check(self.new_payload(), self.decision())['floor'], .8)

    def test_other_changes_refused(self):
        for changes in (dict(outcome={'x': 1}), dict(analysis=dict(min_post_periods=3, ri_feasibility_floor=self.new_payload()['analysis']['ri_feasibility_floor']))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(self.new_payload(**changes), self.decision())

    def test_wrong_design_status_authority_refused(self):
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision(), old=dict(self.old, design_id='b1010_bac_008_per_se'))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision(superseded_status='SUPERSEDED_C46'))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision(authority=dict(path=str(self.ruling), sha256='0' * 64)))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision(decision='accept'))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision(execution_authorized=True))
        bad = self.new_payload()
        bad['analysis']['ri_feasibility_floor']['ruling_sha256'] = '1' * 64
        with self.assertRaises(ValueError):
            self.check(bad, self.decision())

    def test_park_reason_must_match(self):
        (self.case / 'PARKED.json').write_text(json.dumps(dict(action='ri', reason='other')))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision())

    def test_carried_ri_park_with_null_action_is_accepted_other_actions_refused(self):
        from scripts.l2_pipeline import gate_relax
        (self.case / 'PARKED.json').write_text(json.dumps(dict(action=None, reason=gate_relax.PARK_REASON)))
        self.check(self.new_payload(), self.decision())
        (self.case / 'PARKED.json').write_text(json.dumps(dict(action='S3', reason=gate_relax.PARK_REASON)))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(), self.decision())


class DesignListTests(unittest.TestCase):
    FIVE = ['b1010_open_container', 'b1010_truth_in_sentencing', 'b1010b_gdl_holding_night',
            'b1010b_opioid_prescriber_cme', 'b1010b_pain_clinic_law']
    EERS = ['b1011c_eers_electric', 'b1016q4_eers_electric_electric_power_co2_emissions']

    def test_five_original_ids_exactly_once_still_listed(self):
        self.assertEqual(len(self.FIVE), len(set(self.FIVE)))
        self.assertTrue(set(self.FIVE) <= G.R31_DESIGNS)

    def test_eers_pair_added_by_name_hub_ruling_20261023_q1(self):
        # HUB_RULING_20261023_PARK_REOPEN section 4, Q1 (panel 5-0): exactly these two, by name.
        self.assertEqual(len(self.EERS), len(set(self.EERS)))
        self.assertTrue(set(self.EERS) <= G.R31_DESIGNS)
        for design in self.EERS:
            self.assertEqual(G.ri_floor(design, {'ri_feasibility_floor': dict(GOOD_BLOCK)}), .8)

    def test_list_is_frozen_at_exactly_seven_members(self):
        # The list is frozen after the 2026-10-23 addition: any new member must fail this test and needs a new panel ruling.
        self.assertEqual(G.R31_DESIGNS, frozenset(self.FIVE + self.EERS))
        self.assertEqual(len(G.R31_DESIGNS), 7)
        self.assertIsInstance(G.R31_DESIGNS, frozenset)

    def test_unlisted_neighbours_still_refused(self):
        for design in ('b1010_bac_008_per_se', 'b1010_texting_ban_all', 'b1011c_eers_electric_x', 'b1011cfl_ui_part_time_worker_eligibility',
                       'b1016q4_ui_part_time_worker_eligibility_ui_weeks_compensated'):
            with self.subTest(design=design), self.assertRaises(ValueError):
                G.ri_floor(design, {'ri_feasibility_floor': dict(GOOD_BLOCK)})

    def test_floor_unchanged_for_eers(self):
        self.assertEqual((G.FLOOR, G.DEFAULT_FLOOR, G.RULE, G.IMPLEMENTATION), (0.8, 0.9, 'R3.1', 'GATE_RELAX_R31'))
        for design in self.EERS:
            for floor in (0.85, 0.79, 0.7, 0.9, 0.5):
                with self.subTest(design=design, floor=floor), self.assertRaises(ValueError):
                    G.ri_floor(design, {'ri_feasibility_floor': dict(GOOD_BLOCK, floor=floor)})
        # the real EERS counts sit between 0.80 and 0.90: pass at the R3.1 floor, fail at the default
        self.assertTrue(ri.inventory(records(199, 20), floor=.8)['support_pass'])    # 179 of 199
        self.assertFalse(ri.inventory(records(199, 20))['support_pass'])
        self.assertTrue(ri.inventory(records(999, 101), floor=.8)['support_pass'])   # 898 of 999
        self.assertFalse(ri.inventory(records(999, 101))['support_pass'])

    def test_wording_and_ruling_unchanged_for_eers(self):
        # Same wording, same ruling file and same stop reason as the five (the one-shot refusal is tested in EersRequeueHookTests).
        self.assertEqual(G.STATEMENT, 'added after the RI stop, before any forecast or outcome calculation')
        self.assertEqual(G.RULING_PATH, 'docs/plans/l2-pipeline/HUB_RULING_20261022_GATE_RELAX.md')
        self.assertEqual(G.PARK_REASON, 'STOPPED_PRE_RUN: RI199/RI999 feasibility below 90%')


class EersRequeueHookTests(RequeueHookTests):
    """The requeue hook behaves identically for the EERS pair (same ruling file, same one-shot rule)."""
    def setUp(self):
        super().setUp()
        self.design = DesignListTests.EERS[0]
        self.old_payload = dict(self.old_payload, design_id=self.design)
        self.old_card.write_text(json.dumps(self.old_payload))
        self.old = dict(design_id=self.design, card=str(self.old_card))

    def new_payload(self, **changes):
        return super().new_payload(**dict(dict(design_id=self.design), **changes))

    def test_second_eers_design_valid(self):
        old_payload = dict(self.old_payload, design_id=DesignListTests.EERS[1])
        self.old_card.write_text(json.dumps(old_payload))
        old = dict(design_id=DesignListTests.EERS[1], card=str(self.old_card))
        new = self.new_payload(design_id=DesignListTests.EERS[1])
        self.assertEqual(self.check(new, self.decision(), old=old)['design_id'], DesignListTests.EERS[1])

    def test_prior_card_with_block_refused_one_shot(self):
        # one shot: once the rescued attempt's card carries the block, a further R3.1 requeue is refused
        carried = self.new_payload()
        self.old_card.write_text(json.dumps(carried))
        with self.assertRaises(ValueError):
            self.check(self.new_payload(version='v3'), self.decision())

    def test_pair_cannot_borrow_other_design_card(self):
        with self.assertRaises(ValueError):
            self.check(self.new_payload(design_id=DesignListTests.EERS[1]), self.decision())


if __name__ == '__main__':
    unittest.main()
