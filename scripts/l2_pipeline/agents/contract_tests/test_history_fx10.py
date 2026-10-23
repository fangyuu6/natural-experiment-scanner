"""Real V9 regression fixtures plus conservative per-item mechanism handling."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from . import golden_output, validate_template
from .packets import emit_fixture_job
from ..collect import collect_job
from ..history import emit_history_job, normalize_history_mechanisms
from ..jobs import load_job
from ...storage import sha

REPO = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).with_name("v9_fx10_real_fixtures.json")
RUN8 = REPO / "data/l2_pipeline/connectors_v2/wonder/run8/v4/jobs/v9_20261009/S1"


class HistoryFX10Tests(unittest.TestCase):
    def test_five_real_submissions_collect_and_preserve_semantic_tuples(self):
        for fixture in json.loads(FIXTURES.read_text()):
            with self.subTest(design=fixture["design_id"], role=fixture["role"]):
                source = REPO / fixture["submission_path"]
                self.assertEqual(sha(source), fixture["sha256"])
                value = json.loads(source.read_text())
                old = load_job(REPO / fixture["job_path"])
                validate_template(old, value)
                with tempfile.TemporaryDirectory(prefix="v9-fx10-real-") as tmp:
                    job = emit_history_job(old.job_path, jobs_root=Path(tmp).resolve(), attempt_id="fx10_fixture", legacy_contract=True)
                    # Only planning-manifest bindings differ in the isolated fixture.
                    value["source_sha256"] = value["source_pack_sha256"] = job.contract["source_sha256"]
                    original = Path(job.output_path)
                    original.parent.mkdir(parents=True)
                    original.write_text(json.dumps(value))
                    before = sha(original)
                    receipt = collect_job(job.job_path)
                    self.assertTrue(receipt["validated"])
                    self.assertFalse(receipt["execution_authorized"])
                    self.assertFalse(receipt["quality_gate"]["s1_ready"])
                    self.assertEqual(sha(original), before)
                    normalized = json.loads(Path(receipt["promoted_path"]).read_text())
                    originals = {row["unit"]: row for row in value["records"]}
                    for raw, row in zip(value["records"], normalized["records"]):
                        self.assertEqual((raw["unit"], raw["status"], raw["start_period"]),
                                         (row["unit"], row["status"], row["start_period"]))
                        expected_mechanism = (raw["mechanism"] if "mechanism_issue" in row
                                              else job.contract["mechanism"])
                        self.assertEqual(row["mechanism"], expected_mechanism)
                        # Scope, clocks, histories and all evidence stay intact.
                        self.assertEqual({k: row[k] for k in raw if k != "mechanism"},
                                         {k: v for k, v in raw.items() if k != "mechanism"})
                    self.assertTrue(all(originals[issue["unit"]]["status"] == "unknown"
                        for issue in receipt["mechanism_issues"]))

    def collect_synthetic(self, change):
        with tempfile.TemporaryDirectory(prefix="v9-fx10-unit-") as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), "s1_official_history_v9")
            value = golden_output(job)
            change(value)
            p = Path(job.output_path)
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(value))
            receipt = collect_job(job.job_path)
            return receipt, json.loads(Path(receipt["promoted_path"]).read_text())

    def test_exact_enum_and_unrecognized_text_are_handled_per_state(self):
        def ambiguous(value):
            value["records"][0]["mechanism"] = "Possibly a different mechanism"
        receipt, value = self.collect_synthetic(ambiguous)
        self.assertTrue(receipt["validated"])
        self.assertEqual(len(receipt["mechanism_issues"]), 1)
        row = value["records"][0]
        self.assertEqual(row["status"], "unknown")
        self.assertIsNone(row["start_period"])
        self.assertEqual(row["disposition"], "NEEDS_RULING")
        self.assertEqual(row["mechanism"], "Possibly a different mechanism")
        self.assertEqual(row["mechanism_unresolved_original"]["start_period"], 2002)
        self.assertEqual(value["records"][1]["status"], "treated")
        self.assertEqual(value["records"][1]["mechanism"], "one")

    def test_wrong_policy_and_numeric_code_cannot_become_known(self):
        for label in ("General organ transplant consent", "1", 1):
            with self.subTest(label=label):
                receipt, value = self.collect_synthetic(lambda v: v["records"][0].update(mechanism=label))
                self.assertEqual(value["records"][0]["status"], "unknown")
                self.assertEqual(len(receipt["mechanism_issues"]), 1)

    def test_aliases_are_scoped_to_the_full_registered_predicate(self):
        label = "Enforceable state law mandates specified information or counseling before abortion; generic consent alone excluded."
        receipt, value = self.collect_synthetic(lambda v: v["records"][0].update(mechanism=label))
        self.assertEqual(value["records"][0]["status"], "unknown")
        self.assertEqual(len(receipt["mechanism_issues"]), 1)

    def test_dependency_inventory_binds_new_and_legacy_templates_and_aliases(self):
        from ...dependency_closure import dependency_inventory
        from ..history import MECHANISM_ALIASES, TEMPLATE
        inventory = dependency_inventory(REPO, [], pipeline_root=REPO / "scripts/l2_pipeline")
        for p in (MECHANISM_ALIASES, TEMPLATE, TEMPLATE.with_name("official_history_coder.md")):
            self.assertEqual(inventory[str(p.resolve())], sha(p))

    def test_free_text_does_not_bypass_blindness_or_official_evidence(self):
        def contaminated(value):
            value["records"][0]["mechanism"] = "Unlisted text"
            value["blindness_attestation"]["our_outcome_data_read"] = True
        with self.assertRaisesRegex(ValueError, "Contaminated V7 project blindness"):
            self.collect_synthetic(contaminated)
        def no_official(value):
            value["records"][0]["mechanism"] = "Unlisted text"
            value["records"][0]["evidence_refs"] = []
        with self.assertRaisesRegex(ValueError, "quoted official"):
            self.collect_synthetic(no_official)
        def no_log(value):
            value["records"][0]["mechanism"] = "Unlisted text"
            value["research_log"] = value["research_log"][1:]
        with self.assertRaisesRegex(ValueError, "research log for every"):
            self.collect_synthetic(no_log)

    def test_current_run8_packets_still_load_and_their_template_shape_passes(self):
        paths = sorted(RUN8.glob("*/*.md"))
        self.assertEqual(len(paths), 14)
        for path in paths:
            with self.subTest(path=path.name, design=path.parent.name):
                job = load_job(path)
                value = golden_output(job)
                validate_template(job, value)
                normalized, audit = normalize_history_mechanisms(job, value)
                self.assertEqual(normalized, value)
                self.assertEqual(audit["issues"], [])
                free = copy.deepcopy(value)
                free["records"][0]["mechanism"] = "Legacy unlisted description"
                normalized, audit = normalize_history_mechanisms(job, free)
                self.assertEqual(normalized["records"][0]["status"], "unknown")
                self.assertEqual(len(audit["issues"]), 1)
