"""Synthetic V6 coverage inventory; no outcomes or source bytes are read."""
import copy
import unittest

from scripts.l2_pipeline.coverage import coverage_bounded_card


CATALOG = {
    "invented": {"coverage": {"start": 1999, "end": 2024, "excluded_years": []}},
    "late": {"coverage_start": 2011, "coverage_end": 2023},
    "holes": {"coverage": {"start_year": 2008, "end_year": 2024, "excluded_years": [2020]}},
    "earlier": {"coverage_years": [1980, 2007]},
}


def card():
    return {"outcome": {"connector": "invented", "years": [1990, 2024]},
            "treatment": {"source_window": {"start_year": 1995, "end_year": 2010},
                          "coverage_end": "2010-12-31"},
            "automation": {"existing": True}, "sensitivities": []}


class V6CoverageTests(unittest.TestCase):
    def bounded(self, value):
        return coverage_bounded_card(value, catalog=CATALOG)

    def test_intersection_preserves_declared_legal_scope_and_inputs(self):
        value = card()
        before = copy.deepcopy(value)
        bounded = self.bounded(value)
        self.assertEqual(value, before)
        self.assertEqual(bounded["outcome"]["years"], [1999, 2010])
        self.assertEqual(bounded["treatment"], value["treatment"])
        report = bounded["automation"]["coverage_window"]
        self.assertEqual(report["original_requested_years"], [1990, 2024])
        self.assertTrue(report["no_extrapolation"])
        self.assertFalse(report["legal_certification_granted"])
        self.assertFalse(report["readiness_granted"])
        self.assertTrue(report["ready_for_audit"])
        self.assertEqual(report["legal_bounds"]["status"], "declared_audit_scope_pending_S1_certification")
        self.assertEqual(report["outcome_values_read"], 0)
        self.assertEqual(self.bounded(bounded), bounded)

    def test_earliest_end_wins_and_date_only_scope_is_supported(self):
        value = card()
        value["treatment"]["coverage_end"] = "2005-12-31"
        self.assertEqual(self.bounded(value)["outcome"]["years"], [1999, 2005])
        value["treatment"] = {"coverage_end": "2005-12-31"}
        self.assertEqual(self.bounded(value)["outcome"]["years"], [1999, 2005])
        value["treatment"]["coverage_end"] = "2024-12-31"
        value["outcome"]["connector"] = "earlier"
        self.assertEqual(self.bounded(value)["outcome"]["years"], [1990, 2007])

    def test_empty_overlap_parks_and_never_changes_legal_end(self):
        value = card()
        value["outcome"]["connector"] = "late"
        bounded = self.bounded(value)
        self.assertEqual(bounded["outcome"]["years"], [])
        report = bounded["automation"]["coverage_window"]
        self.assertEqual(report["status"], "empty")
        self.assertTrue(report["parked"])
        self.assertFalse(report["ready_for_audit"])
        self.assertEqual(bounded["treatment"]["source_window"]["end_year"], 2010)
        self.assertEqual(self.bounded(bounded), bounded)

    def test_missing_or_invalid_bounds_and_years_park_without_failing(self):
        alterations = [lambda c: c["outcome"].pop("years"),
                       lambda c: c.update(treatment={}),
                       lambda c: c["outcome"].update(connector="unknown"),
                       lambda c: c["outcome"].update(years=[True, 2024]),
                       lambda c: c["outcome"].update(years=[2024, 1990]),
                       lambda c: c["treatment"].update(source_window={"start_year": 2001}),
                       lambda c: c["treatment"].update(source_window={"start_year": 2011, "end_year": 2010}),
                       lambda c: c.update(treatment={"coverage_end": "2010-99-99"}),
                       lambda c: c["outcome"].update(period_years=[2002, 2000])]
        for alteration in alterations:
            with self.subTest(alteration=alteration):
                value = card()
                alteration(value)
                before = copy.deepcopy(value)
                bounded = self.bounded(value)
                report = bounded["automation"]["coverage_window"]
                self.assertEqual(report["status"], "unknown")
                self.assertTrue(report["parked"])
                self.assertEqual(value, before)
                self.assertEqual(self.bounded(bounded), bounded)

    def test_biennial_period_inventory_cannot_leak_beyond_legal_end(self):
        value = card()
        value["outcome"].update(years=[2000, 2024], period_years=list(range(2000, 2025, 2)))
        value["treatment"]["coverage_end"] = "2009-12-31"
        bounded = self.bounded(value)
        self.assertEqual(bounded["outcome"]["years"], [2000, 2008])
        self.assertEqual(bounded["outcome"]["period_years"], [2000, 2002, 2004, 2006, 2008])
        self.assertEqual(self.bounded(bounded), bounded)

    def test_catalog_holes_create_explicit_period_inventory(self):
        value = card()
        value["treatment"]["source_window"]["end_year"] = 2024
        value["treatment"]["coverage_end"] = "2024-12-31"
        value["outcome"].update(connector="holes", years=[2018, 2022])
        bounded = self.bounded(value)
        self.assertEqual(bounded["outcome"]["years"], [2018, 2022])
        self.assertEqual(bounded["outcome"]["period_years"], [2018, 2019, 2021, 2022])
        self.assertEqual(self.bounded(bounded), bounded)

    def test_sensitivity_bounds_are_separate_and_retained_idempotently(self):
        value = card()
        value["sensitivities"] = [
            {"name": "longer_request", "changes": {"outcome": {"years": [2000, 2024]}}},
            {"name": "earlier_connector", "changes": {"outcome": {"connector": "earlier"}}},
            {"name": "narrow_legal", "changes": {"treatment": {"coverage_end": "2003-12-31"}}},
        ]
        bounded = self.bounded(value)
        self.assertEqual([s["changes"]["outcome"]["years"] for s in bounded["sensitivities"]],
                         [[2000, 2010], [1995, 2007], [1999, 2003]])
        reports = bounded["automation"]["coverage_window"]["sensitivities"]
        self.assertEqual(reports["longer_request"]["original_requested_years"], [2000, 2024])
        self.assertEqual(reports["earlier_connector"]["original_requested_years"], [1990, 2024])
        self.assertEqual(self.bounded(bounded), bounded)

    def test_unknown_or_empty_sensitivity_parks_whole_card(self):
        for connector, expected in [("unknown", "unknown"), ("late", "empty")]:
            with self.subTest(connector=connector):
                value = card()
                value["sensitivities"] = [{"name": "changed", "changes": {"outcome": {"connector": connector}}}]
                bounded = self.bounded(value)
                report = bounded["automation"]["coverage_window"]
                self.assertTrue(report["parked"])
                self.assertFalse(report["ready_for_audit"])
                self.assertEqual(report["sensitivities"]["changed"]["status"], expected)
                self.assertEqual(self.bounded(bounded), bounded)

    def test_sensitivity_explicit_periods_are_reconciled(self):
        value = card()
        value["sensitivities"] = [{"name": "biennial", "changes": {"outcome": {
            "years": [2000, 2024], "period_years": list(range(2000, 2025, 2))}}}]
        bounded = self.bounded(value)
        outcome = bounded["sensitivities"][0]["changes"]["outcome"]
        self.assertEqual(outcome["years"], [2000, 2010])
        self.assertEqual(outcome["period_years"], [2000, 2002, 2004, 2006, 2008, 2010])
        self.assertEqual(self.bounded(bounded), bounded)

    def test_sensitivity_periods_replace_primary_connector_holes(self):
        value = card()
        value["treatment"] = {"source_window": {"start_year": 2008, "end_year": 2024}}
        value["outcome"].update(connector="holes", years=[2018, 2022])
        value["sensitivities"] = [{"name": "other_source", "changes": {
            "outcome": {"connector": "invented"}}}]
        bounded = self.bounded(value)
        self.assertNotIn(2020, bounded["outcome"]["period_years"])
        self.assertIn(2020, bounded["sensitivities"][0]["changes"]["outcome"]["period_years"])
        self.assertEqual(self.bounded(bounded), bounded)


if __name__ == "__main__":
    unittest.main()
