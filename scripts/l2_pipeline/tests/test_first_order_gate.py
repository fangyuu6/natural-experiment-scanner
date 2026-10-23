from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.cards import load_card, validate_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.first_order import first_order_gate, UNAVAILABLE
from scripts.l2_pipeline.stages import s0_intake, s4_specs
from scripts.l2_pipeline.tests.test_pipeline import card as legacy_card


def card() -> dict:
    value = legacy_card()
    value["outcome"] = {"connector":"cps_monthly_aggregates", "measure_id":"employment_share", "population_filters":{}}
    value["first_order"] = {
        "target_outcome":"employment_share", "primary_outcome":"employment_share",
        "relation":"direct", "availability":"available",
        "statute_refs":[{"source_ref":"source1", "locator":"purpose section"}],
        "literature_refs":[{"source_ref":"fixture literature", "locator":"outcome definition"}],
        "secondary_outcomes":[],
    }
    value["analysis"] = {"estimand":"ATT", "clock":"calendar_year", "controls":"never_treated",
                         "weights":"equal states", "inference":"registered", "diagnostics":[],
                         "stop_rules":["support"], "min_pre_periods":3, "min_controls":5, "min_cohorts":2}
    return value


class FirstOrderGateTests(unittest.TestCase):
    def test_unverified_purpose_leads_cannot_confer_readiness(self):
        value = card()
        value['first_order']['evidence_status'] = 'pending_policy_specific_verification'
        report = first_order_gate(value)
        self.assertFalse(report['passed'])
        self.assertIn('policy-specific verification', ' '.join(report['issues']))

    def test_direct_target_binds_without_reading_outcomes(self):
        value = card()
        value["outcome"]["paths"] = ["must-never-open.csv"]
        with patch("builtins.open", side_effect=AssertionError("outcome opened")):
            report = first_order_gate(value)
        self.assertTrue(report["passed"])
        self.assertEqual(report["outcome_values_read"], 0)

    def test_legacy_card_remains_readable_but_no_new_gate_passage(self):
        value = legacy_card()
        value.pop("first_order", None)
        self.assertEqual(validate_card(value), [])
        self.assertFalse(first_order_gate(value)["passed"])

    def test_v62_located_statute_or_two_distinct_literature_studies_required(self):
        value = card()
        value["first_order"]["literature_refs"] = []
        self.assertTrue(first_order_gate(value)["passed"])
        value["first_order"]["statute_refs"] = []
        papers = [{"source_ref":"paper1", "locator":"primary outcome"},
                  {"source_ref":"paper2", "locator":"same policy-type outcome"}]
        value["first_order"]["literature_refs"] = papers
        self.assertTrue(first_order_gate(value)["passed"])
        for invalid in ([], papers[:1], [papers[0], dict(papers[0], source_ref="paper1#other")],
                        [papers[0], dict(papers[1], locator=" ")]):
            value["first_order"]["literature_refs"] = invalid
            self.assertFalse(first_order_gate(value)["passed"])
        value["first_order"]["statute_refs"] = [{"source_ref":"statute", "locator":" "}]
        value["first_order"]["literature_refs"] = []
        self.assertFalse(first_order_gate(value)["passed"])

    def test_measure_id_cannot_drift(self):
        value = card()
        value["outcome"]["measure_id"] = "wages"
        self.assertFalse(first_order_gate(value)["passed"])

    def test_cps_cannot_be_relabeled_insurance_or_wages(self):
        for unsupported in ("health_insurance_coverage_pct", "wages"):
            value = card()
            value["first_order"].update(target_outcome=unsupported, primary_outcome=unsupported)
            value["outcome"]["measure_id"] = unsupported
            report = first_order_gate(value)
            self.assertFalse(report["passed"])
            self.assertEqual(report["reason"], UNAVAILABLE)

    def test_labor_cannot_be_closest_proxy_for_coverage(self):
        value = card()
        value["first_order"].update(target_outcome="health_insurance_coverage_pct", relation="closest_proxy",
                                    proxy_justification="Employment is available in CPS.")
        report = first_order_gate(value)
        self.assertFalse(report["passed"])
        self.assertTrue(any("downstream" in issue for issue in report["issues"]))

    def test_proxy_has_one_line_justification(self):
        for invalid in ("", "a\nb", "a\rb", None):
            value = card()
            value["first_order"].update(target_outcome="employment", relation="closest_proxy", proxy_justification=invalid)
            self.assertFalse(first_order_gate(value)["passed"])
        value["first_order"]["proxy_justification"] = "Resident employment share measures the statute's employment target."
        self.assertTrue(first_order_gate(value)["passed"])

    def test_unavailable_parks_exact_reason_without_substituting(self):
        value = card()
        value["first_order"]["availability"] = "unavailable"
        report = first_order_gate(value)
        self.assertEqual(report["reason"], UNAVAILABLE)
        self.assertFalse(report["passed"])
        self.assertEqual(value["outcome"]["connector"], "cps_monthly_aggregates")

    def test_unknown_connector_never_grants_measurement(self):
        value = card()
        value["outcome"]["connector"] = "unimplemented"
        self.assertEqual(first_order_gate(value)["reason"], UNAVAILABLE)

    def test_new_connector_capability_extension(self):
        from scripts.l2_pipeline import connectors
        value = card()
        value["outcome"].update(connector="health_fixture", measure_id="health_insurance_coverage_pct")
        value["first_order"].update(target_outcome="health_insurance_coverage_pct", primary_outcome="health_insurance_coverage_pct")
        with patch.dict(connectors.ADAPTERS, {"health_fixture":lambda request: None}), \
             patch.object(connectors, "FIRST_ORDER_MEASURES", {"health_fixture":{"health_insurance_coverage_pct"}}, create=True):
            self.assertTrue(first_order_gate(value)["passed"])

    def test_qcew_wage_primary_requires_wage_field(self):
        value = card()
        value["outcome"].update(connector="qcew_annual", measure_id="wages")
        value["first_order"].update(target_outcome="wages", primary_outcome="wages")
        self.assertFalse(first_order_gate(value)["passed"])
        value["outcome"]["value_field"] = "annual_avg_wkly_wage"
        self.assertTrue(first_order_gate(value)["passed"])

    def test_nchs_cause_support_is_required_at_intake(self):
        value = card()
        value["outcome"].update(connector="nchs_mortality", measure_id="mortality_deaths_count")
        value["first_order"].update(target_outcome="mortality_deaths_count", primary_outcome="mortality_deaths_count")
        for cause in ("Unknown cause", "Homicide", None):
            value["outcome"]["population_filters"] = {"cause_name":cause}
            report = first_order_gate(value)
            self.assertFalse(report["passed"])
            self.assertEqual(report["reason"], UNAVAILABLE)
        value["outcome"]["population_filters"] = {"cause_name":"Stroke"}
        self.assertTrue(first_order_gate(value)["passed"])

    def test_s0_unsupported_nchs_cause_parks_before_outcome_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = card()
            filters = {"cause_name":"Homicide"}
            value["population"]["filters"] = filters
            value["outcome"].update(connector="nchs_mortality", measure_id="mortality_deaths_count", population_filters=filters,
                                    paths=["must_not_open.csv"])
            value["first_order"].update(target_outcome="mortality_deaths_count", primary_outcome="mortality_deaths_count")
            path = root / "card.json"
            path.write_text(json.dumps(value))
            result = s0_intake.run(StageContext(load_card(path), root / "work", root))
            self.assertEqual(result.disposition.value,"parked")
            self.assertEqual(result.reason,UNAVAILABLE)
            self.assertEqual(result.metrics["outcome_values_read"],0)

    def test_generic_panel_requires_located_source_measure(self):
        value = card()
        value["outcome"]["connector"] = "standardized_csv"
        self.assertFalse(first_order_gate(value)["passed"])
        value["outcome"].update(source_measure_id="employment_share", measurement_refs=[{"source_ref":"dataset", "locator":"employment column"}])
        self.assertTrue(first_order_gate(value)["passed"])

    def test_secondaries_are_explicit_and_preserved_in_spec(self):
        value = card()
        value["first_order"]["secondary_outcomes"] = [{"outcome":"wages", "role":"secondary", "connector":"qcew_annual"}]
        spec = s4_specs.generate_spec(value, {"primary_passed":True, "variants":[{"variant":"primary"}]})
        self.assertEqual(spec["secondary_outcomes"], value["first_order"]["secondary_outcomes"])
        self.assertEqual(spec["first_order"], value["first_order"])
        value["first_order"]["secondary_outcomes"][0]["role"] = "primary"
        self.assertFalse(first_order_gate(value)["passed"])

    def test_unregistered_or_duplicate_secondaries_refused(self):
        value = card()
        value["analysis"]["secondary_outcomes"] = ["wages"]
        self.assertFalse(first_order_gate(value)["passed"])
        del value["analysis"]["secondary_outcomes"]
        secondary = {"outcome":"wages", "role":"secondary", "connector":"qcew_annual"}
        value["first_order"]["secondary_outcomes"] = [secondary, copy.deepcopy(secondary)]
        self.assertFalse(first_order_gate(value)["passed"])

    def test_s0_and_s4_gate_fail_before_support_or_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            value = card()
            value["first_order"]["availability"] = "unavailable"
            path = root / "card.json"
            path.write_text(json.dumps(value))
            context = StageContext(load_card(path), root / "work", root)
            for stage in (s0_intake, s4_specs):
                result = stage.run(context)
                self.assertEqual(result.disposition.value, "parked")
                self.assertEqual(result.reason, UNAVAILABLE)
            self.assertFalse((context.design_dir / "SPEC.json").exists())
            self.assertTrue((context.design_dir / "FIRST_ORDER_GATE_S0.json").is_file())
            self.assertTrue((context.design_dir / "FIRST_ORDER_GATE_S4.json").is_file())

    def test_s4_cannot_generate_legacy_or_drifted_spec(self):
        value = card()
        value.pop("first_order")
        with self.assertRaisesRegex(ValueError, "first-order"):
            s4_specs.generate_spec(value, {"primary_passed":True, "variants":[{"variant":"primary"}]})

    def test_sensitivity_cannot_switch_to_downstream_measurement(self):
        value = card()
        value["sensitivities"] = [{"name":"downstream", "changes":{"outcome":{"measure_id":"wages", "connector":"qcew_annual", "value_field":"annual_avg_wkly_wage"}}}]
        self.assertFalse(first_order_gate(value)["passed"])
        value["sensitivities"] = [{"name":"other_years", "changes":{"outcome":{"years":[2000,2020]}}}]
        self.assertTrue(first_order_gate(value)["passed"])


if __name__ == "__main__":
    unittest.main()
