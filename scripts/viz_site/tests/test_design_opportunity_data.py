"""Acceptance and corruption tests for the verified-only L4 inventory."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.viz_site import make_design_opportunity as builder
from scripts.viz_site import validate_design_opportunity as validator


class PublicFieldValidationTests(unittest.TestCase):
    def test_rejects_every_marker_in_public_strings(self):
        for marker in ("V1", "V2", "V3", "FIX", "Researcher", "research had", "TODO", "null"):
            for value in (marker, f"({marker}): dated record", marker.lower()):
                with self.subTest(value=value):
                    errors = validator.validate_public_fields({"families": [{"jurisdictions": [{"reason": value}]}]})
                    self.assertEqual(["public.families[0].jurisdictions[0].reason: internal research marker in public field"], errors)

    def test_checks_all_values_including_links_and_bilingual_copy(self):
        for value in ("备注：TODO。", "https://example.test/FIX/record", "research\nhad a year"):
            with self.subTest(value=value):
                self.assertTrue(validator.validate_public_fields({"value": value}))

    def test_accepts_structured_unknowns_and_ignores_keys(self):
        public = {"null": None, "V1": {"TODO": 2020}, "Researcher": [None, True, 0], "date": "2020-01-01"}
        self.assertEqual([], validator.validate_public_fields(public))
        self.assertEqual([], validator.validate_public_fields(json.loads('{"date": null}')))

    def test_words_do_not_match_substrings(self):
        for value in ("fixed", "prefix", "nullification", "researchers", "V10", "V20", "V30", "TODOs"):
            with self.subTest(value=value):
                self.assertEqual([], validator.validate_public_fields(value))

    def test_errors_do_not_echo_private_research_text(self):
        errors = validator.validate_public_fields({"reason": "V1 private working details"})
        self.assertTrue(errors)
        self.assertNotIn("private working details", " ".join(errors))


class DesignOpportunityDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = builder.build_payload()

    def assert_rejected(self, payload, fragment):
        errors = validator.validate(payload)
        self.assertTrue(errors, "corrupted payload passed")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_independent_recomputation_and_plan_table(self):
        self.assertEqual([], validator.validate(self.payload))
        self.assertEqual(213, self.payload["strict_dated_total"])
        table = [(s["strict_dated_jurisdictions"], s["year_group_count"], s["modal_count"], s["modal_years"]) for s in self.payload["opportunity_summary"]]
        self.assertEqual([(26, 8, 6, [2020]), (40, 7, 17, [2017]), (43, 11, 12, [2015]), (42, 7, 19, [2015]), (18, 12, 4, [2018]), (10, 9, 2, [2020]), (18, 7, 5, [2019]), (16, 8, 4, [2019])], table)

    def test_deterministic_builder_bytes(self):
        built = builder.serialized(self.payload)
        self.assertEqual(built, builder.serialized(builder.build_payload()))
        self.assertEqual(built, (builder.DATA / "design_opportunity.json").read_text(encoding="utf-8"))

    def test_filter_accepts_only_dated_verified_or_fixed(self):
        for verification in ("verified", "fixed"):
            self.assertTrue(builder.strict_entry({"year": 2025, "date": None, "verification": verification}))
        for verification in ("single_source", "disputed", "removed", None):
            self.assertFalse(builder.strict_entry({"year": 2017, "date": "2017-01-01", "verification": verification}))
        for year in (None, 0, True, "2020", 2003, 2026):
            self.assertFalse(builder.strict_entry({"year": year, "date": None, "verification": "verified"}))
        self.assertFalse(builder.strict_entry({"year": 2025, "date": "2026-01-01", "verification": "verified"}))
        self.assertTrue(builder.strict_entry({"year": 2025, "date": "2025-12-31", "verification": "verified"}))

    def test_all_51_jurisdictions_and_distinct_exclusion_statuses(self):
        rows = self.payload["family_jurisdiction"]
        for summary in self.payload["opportunity_summary"]:
            self.assertEqual(51, sum(summary["status_accounting"].values()))
            family_rows = [r for r in rows if r["family_id"] == summary["family_id"]]
            self.assertEqual(set(builder.JURISDICTIONS), {r["jurisdiction"] for r in family_rows})
        withheld = {(r["family_id"], r["jurisdiction"]) for r in rows if r["inventory_status"] == "withheld_single_source"}
        self.assertEqual(validator.WITHHELD, withheld)
        exclusions = {(r["family_id"], r["jurisdiction"]): r["inventory_status"] for r in rows if r["exclusion_reason"] is not None}
        self.assertEqual(validator.EXCLUDED, exclusions)
        self.assertFalse(any(r["included_strict"] for r in rows if r["inventory_status"] in {"withheld_single_source", "disputed", "removed"}))

    def test_validator_rejects_single_source_in_strict_groups(self):
        payload = deepcopy(self.payload)
        row = next(r for r in payload["family_jurisdiction"] if r["inventory_status"] == "withheld_single_source")
        row["included_strict"] = True
        row["inventory_status"] = "strict_dated"
        self.assert_rejected(payload, "included_strict")

    def test_validator_rejects_source_date_and_verification_join_changes(self):
        for field, new_value in (("recorded_year", 2011), ("recorded_date", "2020-01-01"), ("sources", []), ("verification", "single_source"), ("verification_detail", {"status": "REMOVED"})):
            with self.subTest(field=field):
                payload = deepcopy(self.payload)
                payload["family_jurisdiction"][0][field] = new_value
                self.assert_rejected(payload, field)

    def test_validator_rejects_mislabeled_jurisdiction(self):
        payload = deepcopy(self.payload)
        payload["family_jurisdiction"][0]["jurisdiction_name_en"] = "Alabama"
        # Postal sort starts with AK; substituting another valid full name must
        # fail even though it is nonempty and syntactically plausible.
        self.assert_rejected(payload, "jurisdiction name")

    def test_validator_rejects_duplicate_or_missing_inventory_rows(self):
        payload = deepcopy(self.payload)
        payload["family_jurisdiction"][1] = deepcopy(payload["family_jurisdiction"][0])
        self.assert_rejected(payload, "duplicate")
        payload = deepcopy(self.payload)
        payload["family_jurisdiction"].pop()
        self.assert_rejected(payload, "408")

    def test_validator_rejects_summary_and_accounting_corruption(self):
        for field, value in (("strict_dated_jurisdictions", 27), ("modal_share", 0.5), ("year_group_sizes", []), ("status_accounting", {"strict_dated": 51})):
            with self.subTest(field=field):
                payload = deepcopy(self.payload)
                payload["opportunity_summary"][0][field] = value
                self.assert_rejected(payload, "summary/group")

    def test_cutoff_and_exact_bilingual_clocks(self):
        payload = deepcopy(self.payload)
        payload["cutoff"] = "2026-12-31"
        self.assert_rejected(payload, "cutoff")
        for key in ("clock_en", "clock_zh"):
            payload = deepcopy(self.payload)
            payload["family"][0][key] += " year"
            self.assert_rejected(payload, "exact clock")

    def test_cli_validator_rejects_markers_in_public_family_fields(self):
        for key in ("short_en", "short_zh", "clock_en", "clock_zh"):
            with self.subTest(key=key):
                payload = deepcopy(self.payload)
                payload["family"][0][key] += " V1"
                self.assert_rejected(payload, "internal research marker in public field")

    def test_cli_validator_rejects_markers_in_public_run_fields(self):
        for field in ("label", "stop_reason", "outcome"):
            for lang in ("en", "zh"):
                with self.subTest(field=field, lang=lang):
                    payload = deepcopy(self.payload)
                    row = payload["run_evidence"][0]
                    if field == "outcome":
                        row["primary_runs"][0][field] = {lang: "TODO"}
                    else:
                        row[field] = {lang: "TODO"}
                    self.assert_rejected(payload, "internal research marker in public field")

    def test_direct_script_cli_checks_public_projection(self):
        payload = deepcopy(self.payload)
        payload["family"][0]["short_en"] += " FIX"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run([sys.executable, validator.__file__, str(path)],
                                  cwd=builder.ROOT, capture_output=True, text=True)
        self.assertEqual(1, proc.returncode, proc.stderr)
        self.assertIn("internal research marker in public field", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unknowns_are_null_and_never_converted_to_control_counts(self):
        self.assertIsNone(self.payload["panel_support"])
        for summary in self.payload["opportunity_summary"]:
            self.assertIsNone(summary["eligible_comparison_count"])
            self.assertIsNone(summary["verified_nonoperative_at_cutoff"])
        payload = deepcopy(self.payload)
        row = next(r for r in payload["family_jurisdiction"] if r["recorded_year"] is None)
        row["recorded_year"] = 0
        self.assert_rejected(payload, "recorded_year")
        payload = deepcopy(self.payload)
        payload["opportunity_summary"][0]["eligible_comparison_count"] = 25
        self.assert_rejected(payload, "nulls")
        payload = deepcopy(self.payload)
        payload["panel_support"] = {"treated_cohorts": 8}
        self.assert_rejected(payload, "panel_support")

    def test_outcomes_remain_candidates_without_observed_coverage(self):
        inventory = json.loads((builder.DATA / "outcome_inventory.json").read_text())
        self.assertEqual({"survey_candidate"}, {r["status"] for r in inventory["records"]})
        for record in inventory["records"]:
            for field in ("first_observed_year", "last_observed_year", "missing_years", "source_confirmed_metadata", "local_panel_validation"):
                self.assertIsNone(record[field])
        changed = deepcopy(inventory)
        changed["records"][0]["first_observed_year"] = 1979
        payload = deepcopy(self.payload)
        payload["outcome_inventory"] = changed["records"]
        errors = validator.validate(payload, outcome_inventory=changed)
        self.assertTrue(any("unobserved metadata" in e for e in errors), errors)
        changed["records"][0]["status"] = "local_panel_validated"
        payload["outcome_inventory"] = changed["records"]
        errors = validator.validate(payload, outcome_inventory=changed)
        self.assertTrue(any("actual local-panel validation" in e for e in errors), errors)

    def test_run_hashes_links_status_stop_and_labels_are_bound(self):
        for field, value in (("result_sha256", "0" * 64), ("site_sha256", "0" * 64), ("session_sha256", "0" * 64), ("status", "SUCCESS"), ("link", {"en": "/missing", "zh": "/missing"}), ("primary_runs", [])):
            with self.subTest(field=field):
                payload = deepcopy(self.payload)
                payload["run_evidence"][0][field] = value
                self.assert_rejected(payload, "paid_sick_leave")
        castle = next(r for r in self.payload["run_evidence"] if r["example_id"] == "castle_doctrine")
        self.assertEqual("INTERNAL_ONLY", castle["status"])
        self.assertIn("pretrend", castle["stop_reason_code"])
        payload = deepcopy(self.payload)
        payload["run_evidence"][2]["stop_reason"] = None
        self.assert_rejected(payload, "stop_reason")

    def test_completed_session_requires_verified_freeze_and_all_three_cases(self):
        original_load = validator._load
        for corruption in ("failed_freeze", "freeze_issues", "missing_registered_case"):
            def changed_session(path):
                result = original_load(path)
                if path.name == f"session_{self.payload['run_evidence'][0]['session_id']}.json":
                    result = deepcopy(result)
                    if corruption == "failed_freeze":
                        result["freeze_verification"]["verified"] = False
                    elif corruption == "freeze_issues":
                        result["freeze_verification"]["issues"] = ["unverified input"]
                    else:
                        result["registered_example_ids"].pop()
                return result
            with self.subTest(corruption=corruption), patch.object(validator, "_load", side_effect=changed_session):
                self.assert_rejected(self.payload, "completed session")

    def test_validator_has_no_builder_dependency(self):
        # The acceptance check must recompute raw data rather than trust the same
        # functions that produced the output.
        tree = ast.parse(Path(validator.__file__).read_text())
        imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("make_design_opportunity" in ast.unparse(node) for node in imports))


if __name__ == "__main__":
    unittest.main()
