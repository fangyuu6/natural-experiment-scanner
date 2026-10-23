"""Clock, scope, provenance, and readiness regression tests on locked pilot data."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("lean_l2", Path(__file__).with_name("l2.py"))
l2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(l2)
ROOT = Path(__file__).resolve().parents[3]


class LeanL2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        output_root = ROOT / "data/pipeline/v2"
        output_root.mkdir(parents=True, exist_ok=True)
        cls.directory = tempfile.TemporaryDirectory(prefix=".l2-v2-test-", dir=output_root)
        cls.out = Path(cls.directory.name)
        cls.summary = l2.run(ROOT, cls.out)
        cls.rows = json.loads((cls.out / "l2_analysis_values.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def row(self, design, unit, component=None):
        return next(r for r in self.rows if r["design_id"] == design and r["unit"] == unit
                    and (component is None or r["component"] == component))

    def test_primary_benefit_ignores_contribution_clock(self):
        row = self.row(l2.FAMILY, "CO")
        self.assertEqual((row["A"]["value"], row["B"]["value"]), (2024, 2024))
        self.assertEqual(row["A"]["json_path"], ["family_and_ownmedical_benefits", "start"])

    def test_month_day_precision_matches_year(self):
        row = self.row(l2.FAMILY, "RI")
        self.assertEqual(row["initial_result"], "agree")
        self.assertEqual(row["A"]["value"], 2014)
        self.assertEqual(row["B"]["value"], 2014)

    def test_core_clock_excludes_act_wide_earlier_clock(self):
        row = self.row(l2.LICENSE, "CO")
        self.assertEqual(row["A"]["value"], 2021)
        self.assertEqual(row["B"]["value"], 2021)

    def test_missouri_same_instrument_alignment(self):
        row = self.row(l2.LICENSE, "MO")
        self.assertEqual(row["initial_result"], "agree")
        self.assertEqual(row["A"]["value"], 2018)
        self.assertIn("2018", row["A"]["record_id"])

    def test_scope_difference_not_silently_dropped(self):
        row = self.row(l2.LICENSE, "NV")
        self.assertEqual(row["initial_result"], "dispute")
        self.assertNotEqual(row["A"]["value"]["scope"], row["B"]["value"]["scope"])
        wage = self.row(l2.WAGE, "WI", "WI-Act59-highway-2017")
        self.assertTrue(wage["diagnostic_only"])
        self.assertIn("scope", wage["scope_gap"])

    def test_eitc_tax_year_not_payment_year_and_phase_years(self):
        wa = self.row(l2.EITC, "WA", "WFTC eligible tax year")
        self.assertEqual(wa["A"]["value"], 2022)
        for component, year in [("37pct", 2018), ("39pct", 2019), ("40pct", 2020)]:
            row = self.row(l2.EITC, "NJ", component)
            self.assertEqual((row["A"]["value"], row["B"]["value"]), (year, year))

    def test_supplemental_credit_not_lost_in_main_code_clock(self):
        row = self.row(l2.EITC, "MI", "30pct plus TY2022 supplement")
        self.assertEqual((row["A"]["value"], row["B"]["value"]), (2022, 2022))
        self.assertEqual(row["B"]["field_id"], "scope_target_population")

    def test_missing_phase_key_does_not_invent_tax_year(self):
        inputs = l2.Inputs(ROOT)
        row = inputs.find(l2.EITC, "A", record="NJ__NJ_2018_CH45_EITC", field="effective_date")
        value = json.loads(row["value"])
        del value["schedule"]["2019"]
        row["value"] = json.dumps(value)
        projected = next(r for r in l2.did_projections(inputs)
                         if r["design_id"] == l2.EITC and r["component"] == "39pct")
        self.assertIsNone(projected["A"]["value"])
        self.assertEqual(projected["initial_result"], "one_unknown")

    def test_unknown_never_becomes_zero(self):
        self.assertFalse(l2.full_negative({"certified_through": "2023-04-14", "partial_certificate": True}))
        self.assertFalse(l2.full_negative({"certified_through": l2.END, "partial_certificate": True, "complete_window": True}))
        statuses = [r for r in self.rows if "status through sample end" in r["component"]]
        self.assertEqual(len(statuses), 204)
        self.assertTrue(all(r["A"]["value"] is None and r["B"]["value"] is None for r in statuses))
        self.assertTrue(all(r["third_read"] is None for r in statuses))

    def test_future_schedule_not_observed_treatment(self):
        row = self.row(l2.FAMILY, "DE")
        self.assertIsNone(row["B"]["value"])
        self.assertEqual(row["B"]["scheduled_year"], 2026)

    def test_addressable_provenance_for_known_values(self):
        for row in self.rows:
            for coder in "AB":
                c = row[coder]
                if c["value"] is not None:
                    self.assertIn(":", c["row_ref"])
                    self.assertTrue(c["json_path"])
                    self.assertTrue(c["source"]["url"])
                    self.assertTrue(c["source"]["locator"])

    def test_blind_vs_third_read_and_readiness_separate(self):
        self.assertEqual(self.summary["totals"]["known_agree"], 36)
        self.assertEqual(self.summary["totals"]["known_pair_denominator"], 40)
        self.assertEqual(len(self.summary["designs"]), 6)
        self.assertTrue(all(not d["source_ready"] and not d["full_data_ready"] and not d["ready_after_small_third_read"] for d in self.summary["designs"]))
        for row in self.rows:
            self.assertEqual(row["initial_result"], l2.compare(row["A"], row["B"]))
            if row["third_read"]:
                self.assertFalse(row["third_read"]["changes_blind_agreement"])

    def test_no_whole_prose_year_search(self):
        self.assertIsNone(l2.annual("payments2023 for TY2022"))
        self.assertIsNone(l2.get_path({"contributions": "2019", "benefit": "2021"}, ("unknown",)))
        self.assertEqual(l2.compare({"value": None, "availability": "unknown"}, {"value": None, "availability": "unknown"}), "both_unknown")


if __name__ == "__main__":
    unittest.main()
