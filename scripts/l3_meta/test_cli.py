"""Synthetic-only checks for prospective metadata safety and planning math."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I mode drops the script folder from sys.path
import cli  # noqa: E402


def synthetic_design(design_id="synthetic-A", status="pending"):
    design = {
        "design_id": design_id, "registry_design_id": design_id,
        "policy_id": "synthetic-policy", "policy_family_id": "synthetic-family",
        "source_text_ids": ["synthetic-source"], "domain": "labor", "design_type": "did",
        "status": status, "stop_reason": "synthetic lack of controls" if status == "stopped" else None,
        "enrolled_at": "2026-10-01T00:00:00Z", "status_updated_at": "2026-10-02T00:00:00Z",
        "terminal_at": "2026-10-02T00:00:00Z" if status in {"estimated", "stopped", "unestimated"} else None,
        "exposed_before_prereg": False, "spec_sha256": None,
        "outcomes": [{"outcome_id": None, "definition": None, "unit": None, "sign_alignment": None}],
        "forecast": None, "literature": None, "adoption": None,
        "missing_reasons": {field: "synthetic not collected" for field in ["spec_sha256", "forecast", "literature", "adoption", "outcome.outcome_id", "outcome.definition", "outcome.unit", "outcome.sign_alignment"]},
        "provenance": {"registry_snapshot_sha256": "0" * 64, "source_record_ids": ["synthetic-registry-record"], "metadata_reviewed_at": "synthetic timestamp"},
    }
    if design["terminal_at"] is None:
        design["missing_reasons"]["terminal_at"] = "synthetic disposition still open"
    return design


def synthetic_forecast(semantics="positive_effect"):
    value = {"ledger_sha256": "1" * 64, "locked_at": "synthetic timestamp", "lock_verified_before_estimation": True, "estimator_target": "synthetic frozen estimator", "probability_semantics": semantics}
    if semantics == "positive_effect":
        value["forecasters"] = [{"forecaster_id": "synthetic-f", "p_positive": 0.0}]
    else:
        value["threshold_spec_id"] = "synthetic-threshold"
        value["forecasters"] = [{"forecaster_id": "synthetic-f", "p_exceeds_positive": 0.2, "p_inside_noise": 0.5, "p_exceeds_negative": 0.3}]
    return value


def bundle(*designs):
    return {"schema_version": "l3-meta-metadata-v1", "designs": list(designs)}


class MetadataTests(unittest.TestCase):
    def test_existing_other_domain_and_lottery_track_are_retained(self):
        d = synthetic_design()
        d["domain"] = "other"
        d["design_type"] = "rct_lottery"
        self.assertEqual(cli.validate_metadata(bundle(d))["registry_designs"], 1)

    def test_stopped_unestimated_and_unknown_outcomes_remain_in_denominator(self):
        result = cli.validate_metadata(bundle(synthetic_design("a", "stopped"), synthetic_design("b", "unestimated")))
        self.assertEqual(result["registry_designs"], 2)
        self.assertEqual(result["status_counts"]["stopped"], 1)
        self.assertEqual(result["status_counts"]["unestimated"], 1)
        self.assertFalse(result["outcome_associations_computed"])

    def test_duplicate_design_rejected(self):
        d = synthetic_design()
        with self.assertRaisesRegex(ValueError, "duplicate design"):
            cli.validate_metadata(bundle(d, copy.deepcopy(d)))

    def test_secondary_outcome_cannot_inflate_design_count(self):
        d = synthetic_design()
        d["outcomes"].append(copy.deepcopy(d["outcomes"][0]))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            cli.validate_metadata(bundle(d))

    def test_raw_result_and_outcome_fields_refused(self):
        for location, field in [("design", "run"), ("design", "result"), ("outcome", "estimate"), ("provenance", "result_path")]:
            with self.subTest(location=location, field=field):
                d = synthetic_design()
                target = d if location == "design" else d["outcomes"][0] if location == "outcome" else d["provenance"]
                target[field] = 0.0
                with self.assertRaisesRegex(ValueError, "unexpected"):
                    cli.validate_metadata(bundle(d))

    def test_missing_requires_reason_and_cannot_be_zero(self):
        d = synthetic_design()
        del d["missing_reasons"]["forecast"]
        with self.assertRaisesRegex(ValueError, "null needs"):
            cli.validate_metadata(bundle(d))
        d = synthetic_design()
        d["outcomes"][0]["sign_alignment"] = 0
        del d["missing_reasons"]["outcome.sign_alignment"]
        with self.assertRaisesRegex(ValueError, "expected .1/-1"):
            cli.validate_metadata(bundle(d))

    def test_actual_zero_probability_is_valid(self):
        d = synthetic_design()
        d["forecast"] = synthetic_forecast()
        del d["missing_reasons"]["forecast"]
        self.assertEqual(cli.validate_metadata(bundle(d))["registry_designs"], 1)

    def test_exclusive_forecasts_require_threshold_and_unit_sum(self):
        for mutation in ["no_threshold", "wrong_sum", "positive_only_field"]:
            d = synthetic_design()
            d["forecast"] = synthetic_forecast("exclusive_noise_categories")
            del d["missing_reasons"]["forecast"]
            if mutation == "no_threshold":
                del d["forecast"]["threshold_spec_id"]
            elif mutation == "wrong_sum":
                d["forecast"]["forecasters"][0]["p_inside_noise"] = 0.1
            else:
                d["forecast"]["forecasters"][0]["p_positive"] = 0.4
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                cli.validate_metadata(bundle(d))

    def test_unknown_probability_semantics_cannot_be_assumed(self):
        d = synthetic_design()
        d["forecast"] = synthetic_forecast()
        d["forecast"]["probability_semantics"] = "direction_or_threshold_unspecified"
        del d["missing_reasons"]["forecast"]
        with self.assertRaisesRegex(ValueError, "unknown probability semantics"):
            cli.validate_metadata(bundle(d))

    def test_nonfinite_and_boolean_probabilities_rejected(self):
        for value in [float("nan"), float("inf"), True, -0.1, 1.1]:
            d = synthetic_design()
            d["forecast"] = synthetic_forecast()
            d["forecast"]["forecasters"][0]["p_positive"] = value
            del d["missing_reasons"]["forecast"]
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite probability"):
                cli.validate_metadata(bundle(d))

    def test_exposed_designs_do_not_become_pristine(self):
        d = synthetic_design()
        d["exposed_before_prereg"] = True
        self.assertEqual(cli.validate_metadata(bundle(d))["prospective_unexposed"], 0)

    def test_registry_identity_and_stop_reason_required(self):
        d = synthetic_design()
        d["registry_design_id"] = "other"
        with self.assertRaisesRegex(ValueError, "registry identity"):
            cli.validate_metadata(bundle(d))
        d = synthetic_design(status="stopped")
        d["stop_reason"] = None
        with self.assertRaisesRegex(ValueError, "stop_reason"):
            cli.validate_metadata(bundle(d))

    def test_terminal_dates_cannot_erase_or_backdate_dispositions(self):
        d = synthetic_design(status="stopped")
        d["terminal_at"] = None
        with self.assertRaisesRegex(ValueError, "terminal timestamp"):
            cli.validate_metadata(bundle(d))
        d = synthetic_design()
        d["status_updated_at"] = "2026-09-30T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "precedes enrollment"):
            cli.validate_metadata(bundle(d))


class PlanningTests(unittest.TestCase):
    def test_binomial_tail_known_small_distribution(self):
        self.assertAlmostEqual(cli.binomial_tail(2, 1, 0.5), 0.75)
        self.assertAlmostEqual(cli.binomial_tail(2, 2, 0.5), 0.25)
        self.assertEqual(cli.binomial_tail(2, 3, 0.5), 0)

    def test_exact_alpha_cutoff_and_power_inversion(self):
        for n in [29, 50, 79, 100]:
            result = cli.exact_binomial_design(n)
            k = result["reject_at_or_above"]
            self.assertLessEqual(result["size_at_null"], 0.01)
            self.assertGreater(cli.binomial_tail(n, k - 1, 0.05), 0.01)
            self.assertAlmostEqual(cli.binomial_tail(n, k, result["rate_for_80_percent_power"]), 0.8, places=10)

    def test_precision_improves_with_eligible_n_and_marks_dependence(self):
        result = cli.power_table()
        rows = {row["eligible_independent_designs"]: row for row in result["rows"]}
        self.assertLess(rows[100]["paired_mean_mde_in_sd"], rows[50]["paired_mean_mde_in_sd"])
        self.assertLess(rows[79]["correlation_mde_fisher_approx"], rows[29]["correlation_mde_fisher_approx"])
        self.assertAlmostEqual(rows[50]["balanced_two_group_mde_in_sd"], 2 * rows[50]["paired_mean_mde_in_sd"])
        self.assertTrue(any("effective N" in item for item in result["limits"]))

    def test_proposal_has_no_fabricated_approval(self):
        plan = json.loads((cli.HERE / "plan.json").read_text())
        self.assertEqual(plan["status"], "PROPOSAL_NOT_SIGNED_NOT_FROZEN")
        self.assertEqual(plan["inference"]["questions"], len(plan["questions"]))
        self.assertEqual(plan["target_registry_designs"] - plan["known_exposed_designs_at_proposal"], plan["maximum_pristine_designs_at_target"])

    def test_estimation_refuses_even_when_assertions_are_disabled(self):
        for flags in [[], ["-O"]]:
            result = subprocess.run([sys.executable, *flags, "-B", str(cli.HERE / "cli.py"), "estimate"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("NOT_IMPLEMENTED", result.stderr)
            self.assertEqual(result.stdout, "")

    def test_cli_only_reads_explicit_normalized_metadata(self):
        with tempfile.TemporaryDirectory(prefix="l3-meta-synthetic-") as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(bundle(synthetic_design())), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(cli.HERE / "cli.py"), "validate-metadata", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["outcome_associations_computed"])
            raw = Path(directory) / "raw.json"
            raw.write_text(json.dumps({"run": {"result": {"estimate": 0.1}}}), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(cli.HERE / "cli.py"), "validate-metadata", str(raw)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
