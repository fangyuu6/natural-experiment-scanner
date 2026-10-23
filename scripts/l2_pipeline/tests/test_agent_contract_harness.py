"""Current agent templates and production collectors agree on invented packets."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents.contract_tests import (
    FIXTURES, JOB_TYPES, SCHEMA_MARKER, check_job_types, golden_output,
    run_contract, validate_template,
)
from scripts.l2_pipeline.agents.contract_tests.packets import emit_fixture_job


class AgentContractHarnessTests(unittest.TestCase):
    def test_every_current_job_contract_accepts_golden_and_rejects_several_invalid(self):
        for kind in JOB_TYPES:
            with self.subTest(kind=kind):
                report = run_contract(kind)
                self.assertEqual(report["status"], "PASS")
                self.assertEqual(report["cases"][0], "golden")
                self.assertGreaterEqual(len(report["cases"]), 4)
                self.assertEqual(report["model_calls"], 0)

    def test_minimal_purpose_has_no_required_blindness_and_search_failure_is_null(self):
        fixtures = json.loads(FIXTURES.read_text())
        self.assertNotIn("blindness_attestation", fixtures["s0_purpose"]["golden"])
        self.assertIsNone(fixtures["s0l_literature"]["golden"]["search_log"][0]["source_url"])
        self.assertIn("legacy_string_search_log", [item["name"] for item in fixtures["s0l_literature"]["invalid"]])

    def test_contract_gate_uses_real_collector_and_no_model_or_network_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), "s5_forecast")
            with patch("subprocess.run", side_effect=AssertionError("Model/CLI call forbidden")), \
                 patch("urllib.request.urlopen", side_effect=AssertionError("Network call forbidden")):
                reports = check_job_types([job, job])
            self.assertEqual([report["job_type"] for report in reports], ["s5_forecast"])
            self.assertFalse(Path(job.output_path).exists())
            self.assertFalse((Path(job.output_dir) / "DISPATCH_ONCE.json").exists())

    def test_gate_detects_collector_regression_accepting_invalid_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), "s5_forecast")
            with patch("scripts.l2_pipeline.agents.contract_tests.collect_job",
                       return_value={"validated": True, "execution_authorized": False}):
                with self.assertRaisesRegex(ValueError, "invalid output accepted"):
                    check_job_types([job])
            self.assertFalse(Path(job.output_path).exists())

    def test_exact_wave_template_must_accept_its_bound_golden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            job = emit_fixture_job(root, "s5_forecast")
            incompatible = root / "old-template.md"
            incompatible.write_text(SCHEMA_MARKER + json.dumps({"type": "object", "required": ["wrong_search_format"]}) + "\n```\n")
            with self.assertRaisesRegex(ValueError, "template.*required field missing"):
                check_job_types([replace(job, template_path=str(incompatible))])

    def test_template_schema_detects_the_original_mechanical_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            purpose = emit_fixture_job(root / "purpose", "s0_purpose")
            value = golden_output(purpose)
            validate_template(purpose, value)
            incompatible = root / "incompatible.md"
            schema = {"type": "object", "required": ["blindness_attestation"]}
            incompatible.write_text(SCHEMA_MARKER + json.dumps(schema) + "\n```\n")
            with self.assertRaisesRegex(ValueError, "required field missing"):
                validate_template(replace(purpose, template_path=str(incompatible)), value)
            literature = emit_fixture_job(root / "literature", "s0l_literature")
            value = golden_output(literature)
            value["search_log"] = ["legacy unstructured search"]
            with self.assertRaisesRegex(ValueError, "template output shape"):
                validate_template(literature, value)

    def test_current_template_plans_use_hub_and_document_concurrency_range(self):
        templates = Path(__file__).resolve().parents[1] / "agents/templates"
        current = ("v6/purpose_verifier_v6_1.md", "v6/purpose_verifier_v6_2.md", "v6/literature_record.md",
                   "v3/coder.md", "v7/coder.md", "v1/auditor.md", "v3/auditor.md",
                   "v7/auditor.md", "v3/third_reader.md", "v2/persona.md",
                   "v7/persona.md", "v3/reviewer.md", "v9/official_history_coder.md",
                   "v9/official_history_coder_fx10.md")
        for relative in current:
            with self.subTest(template=relative):
                text = (templates / relative).read_text()
                self.assertIn("scripts.l2_pipeline.hub dispatch", text)
                self.assertIn("--max-concurrency 4", text)
                self.assertIn("accepts 1..4", text)

    def test_active_template_resources_are_bound_into_freeze_dependencies(self):
        from scripts.l2_pipeline.dependency_closure import dependency_inventory
        from scripts.l2_pipeline.storage import sha
        repo = Path(__file__).resolve().parents[3]
        root = repo / "scripts/l2_pipeline/agents/templates"
        entries = sorted(root.glob("*/dependencies.py"))
        self.assertEqual(len(entries), 6)
        inventory = dependency_inventory(repo, entries)
        resources = [Path(path) for path in inventory if Path(path).suffix == ".md"]
        self.assertEqual(len(resources), 14)
        self.assertIn(root / "v6/purpose_verifier_v6_2.md", resources)
        self.assertIn(root / "v9/official_history_coder_fx10.md", resources)
        aliases = root / "v9/mechanism_aliases_fx10.json"
        self.assertEqual(inventory[str(aliases)], sha(aliases))
        for path in resources:
            self.assertEqual(inventory[str(path)], sha(path))

    def test_unsupported_job_type_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), "s5_forecast")
            with self.assertRaisesRegex(ValueError, "No contract test"):
                check_job_types([replace(job, stage="S9")])


if __name__ == "__main__": unittest.main()
