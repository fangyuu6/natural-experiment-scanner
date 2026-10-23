"""Synthetic V9 history contract checks; no legal research or model dispatch."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from . import golden_output, validate_template
from .packets import emit_fixture_job
from ..collect import collect_job
from ...storage import sha


class HistoryContractTests(unittest.TestCase):
    def collect_case(self, change):
        with tempfile.TemporaryDirectory(prefix="v9-history-contract-") as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), "s1_official_history_v9")
            value = golden_output(job)
            change(value)
            original = Path(job.output_path)
            original.parent.mkdir(parents=True)
            original.write_text(json.dumps(value))
            return collect_job(job.job_path)

    def test_all_fifty_unknown_without_named_conflicts_is_rejected(self):
        def change(value):
            for row in value["records"]:
                row.update(status="unknown", start_period=None,
                           reason="Cannot prove lifetime absence before adoption or no interruption",
                           blocker={"unit":row["unit"], "detail":row["unit"]+" missing lifetime proof",
                                    "official_locator":"https://fixture.gov/code", "search_query":row["unit"]+" history"})
        with self.assertRaisesRegex(ValueError, "V9 unknown requires a named conflict"):
            self.collect_case(change)

    def test_absence_requires_three_v2_list_checks(self):
        def change(value):
            value["records"][0].update(status="never_treated", start_period=None, evidence_status="absent (V2_LIST)",
                                       absence_basis={"official_code_history_no_earlier_enactment":True,
                                       "no_earlier_in_compilations_on_disk":True,"no_search_log_contradiction":True})
            value["records"][0]["evidence_refs"][0]["supports"] = "absence_code_history"
        self.assertTrue(self.collect_case(change)["validated"])
        def missing(value):
            change(value)
            del value["records"][0]["absence_basis"]["no_search_log_contradiction"]
        with self.assertRaisesRegex(ValueError, "three V2_LIST"):
            self.collect_case(missing)

    def test_named_binary_conflict_is_collected_without_granting_s1(self):
        def change(value):
            value["sources"].append({"source_id":"other-official", "url":"https://fixture.gov/order"})
            value["records"][0].update(status="unknown", start_period=None, reason="Law versus injunction",
                named_conflict={"kind":"sources_conflict", "binary_relevant_fact":"operative 2002 versus stayed 2002",
                                "detail":"Invented session law conflicts with judicial stay",
                                "source_ids":["fixture-official","other-official"]})
        receipt = self.collect_case(change)
        self.assertTrue(receipt["validated"])
        self.assertFalse(receipt["execution_authorized"])
        self.assertFalse(receipt["quality_gate"]["s1_ready"])

    def test_definition_ambiguity_requires_exact_question_and_ruling_route(self):
        def change(value):
            value["records"][0].update(status="unknown", start_period=None, reason="Invented definition ambiguity",
                disposition="NEEDS_RULING", named_conflict={"kind":"policy_definition_ambiguous",
                    "binary_relevant_fact":"Whether this statutory population meets the policy predicate",
                    "detail":"Invented state-specific predicate boundary", "source_ids":["fixture-official"],
                    "question":"Does the invented subset satisfy the registered state-level definition?"})
        self.assertTrue(self.collect_case(change)["validated"])
        def missing(value):
            change(value)
            del value["records"][0]["named_conflict"]["question"]
        with self.assertRaisesRegex(ValueError, "exact question"):
            self.collect_case(missing)

    def test_regeneration_is_bound_and_preserves_prior_packet_bytes(self):
        from ..jobs import load_job
        from ..history import emit_history_job
        with tempfile.TemporaryDirectory(prefix="v9-regeneration-") as tmp:
            root = Path(tmp).resolve()
            job = emit_fixture_job(root, "s1_official_history_v9")
            previous = copy.deepcopy(job.contract["previous_packet_hashes"])
            duplicate = emit_history_job(root / "previous/A.md", jobs_root=root / "jobs", attempt_id="v9_fixture")
            self.assertEqual(job, duplicate)
            self.assertEqual(previous, {p:sha(Path(p)) for p in previous})
            self.assertTrue(Path(job.template_path).is_relative_to(Path(job.repo) / "scripts"))
            validate_template(job, golden_output(job))
            self.assertEqual(load_job(job.job_path), job)
            self.assertFalse(Path(job.output_path).exists())
            self.assertFalse((Path(job.output_dir) / "DISPATCH_ONCE.json").exists())

    def test_secondary_start_requires_official_confirmation_and_per_state_searches(self):
        def secondary_only(value):
            value["sources"][0]["source_type"] = "secondary"
        with self.assertRaisesRegex(ValueError, "quoted official"):
            self.collect_case(secondary_only)
        def confirmed(value):
            value["sources"].append({"source_id":"compilation", "source_type":"secondary", "url":"https://example.invalid/table"})
            value["records"][0]["source_id"] = "compilation"
        self.assertTrue(self.collect_case(confirmed)["validated"])
        def missing_search(value):
            value["research_log"] = value["research_log"][:1]
        with self.assertRaisesRegex(ValueError, "research log for every"):
            self.collect_case(missing_search)
        def wrong_year(value):
            value["records"][0]["evidence_refs"][0]["year"] = 2005
        with self.assertRaisesRegex(ValueError, "dated official confirmation"):
            self.collect_case(wrong_year)


if __name__ == "__main__":
    unittest.main()
