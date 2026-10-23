import copy
import unittest

from scripts.l2_pipeline.c9_rulings import apply_c9_card_ruling, DILUTION_NOTE
from scripts.l2_pipeline.cards import validate_card
from scripts.l2_pipeline.first_order import first_order_gate, target_mapping
from scripts.l2_pipeline.tests.test_first_order_gate import card


class C9RulingTests(unittest.TestCase):
    def traffic_card(self, design, target):
        value = card()
        value["design_id"] = design
        value["outcome"].update(connector="fars_state", measure_id="traffic_deaths_count")
        value["first_order"].update(target_outcome=target, primary_outcome="traffic_deaths_count")
        return value

    def test_reviewed_traffic_population_proxies_pass_without_changing_citations(self):
        for design, target in (("run3_minfine", "alcohol_impaired_driving"),
                               ("run3_opencont", "alcohol_involved_motor_vehicle_crash_fatalities")):
            with self.subTest(design=design):
                original = self.traffic_card(design, target)
                snapshot = copy.deepcopy(original)
                revised = apply_c9_card_ruling(original)
                self.assertEqual(original, snapshot)
                self.assertEqual(validate_card(revised), [])
                self.assertTrue(first_order_gate(revised)["passed"])
                self.assertTrue(target_mapping(revised)["mapped"])
                self.assertIn(DILUTION_NOTE, revised["notes"])
                self.assertIn("dilut", revised["first_order"]["proxy_justification"])
                for refs in ("literature_refs", "statute_refs"):
                    self.assertEqual(revised["first_order"][refs], original["first_order"][refs])
                self.assertFalse(revised["automation"]["c9_ruling"]["execution_authorized"])
                self.assertTrue(revised["automation"]["c9_ruling"]["s1_still_required"])

    def test_same_target_in_unreviewed_design_does_not_gain_alias(self):
        original = self.traffic_card("run3_opencont", "alcohol_involved_motor_vehicle_crash_fatalities")
        revised = apply_c9_card_ruling(original)
        revised["design_id"] = "unreviewed_design"
        self.assertFalse(first_order_gate(revised)["passed"])
        self.assertFalse(target_mapping(revised)["mapped"])

    def test_ruling_does_not_bypass_unverified_purpose_or_unavailable_measurement(self):
        revised = apply_c9_card_ruling(self.traffic_card("run3_minfine", "alcohol_impaired_driving"))
        revised["first_order"]["evidence_status"] = "pending_policy_specific_verification"
        self.assertFalse(first_order_gate(revised)["passed"])
        revised["first_order"].pop("evidence_status")
        revised["first_order"]["availability"] = "unavailable"
        self.assertFalse(first_order_gate(revised)["passed"])

    def test_unknown_ruling_and_unreviewed_outcome_refuse(self):
        with self.assertRaisesRegex(ValueError, "No C9"):
            apply_c9_card_ruling(card())
        with self.assertRaisesRegex(ValueError, "reviewed target"):
            apply_c9_card_ruling(self.traffic_card("run3_minfine", "unreviewed_target"))

    def test_statute_route_with_empty_literature_loads_and_cannot_fabricate_citations(self):
        value = card()
        value["design_id"] = "run3_driver_license_compact"
        value["first_order"]["literature_refs"] = []
        revised = apply_c9_card_ruling(value)
        self.assertEqual(revised["first_order"]["purpose_route"], "statute")
        self.assertEqual(validate_card(revised), [])
        self.assertTrue(first_order_gate(revised)["passed"])
        self.assertEqual(revised["first_order"]["literature_refs"], [])
        self.assertEqual(revised["first_order"]["statute_refs"], value["first_order"]["statute_refs"])
        self.assertEqual(apply_c9_card_ruling(revised), revised)

    def test_empty_literature_requires_explicit_statute_route_and_located_target_support(self):
        value = card()
        value["first_order"]["literature_refs"] = []
        self.assertTrue(validate_card(value))
        value["first_order"]["purpose_route"] = "statute"
        self.assertEqual(validate_card(value), [])
        for refs in ([], [{"source_ref": "statute", "locator": " "}],
                     [{"source_ref": "statute", "locator": "eligibility", "supports": ["target_population"]}]):
            value["first_order"]["statute_refs"] = refs
            self.assertTrue(validate_card(value))
            self.assertFalse(first_order_gate(value)["passed"])

    def test_compact_route_keeps_unrelated_construct_failure(self):
        value = self.traffic_card("run3_driver_license_compact", "motor_vehicle_operation_law_compliance")
        value["first_order"]["literature_refs"] = []
        revised = apply_c9_card_ruling(value)
        self.assertEqual(validate_card(revised), [])
        self.assertFalse(first_order_gate(revised)["passed"])
        self.assertFalse(target_mapping(revised)["mapped"])


if __name__ == "__main__":
    unittest.main()
