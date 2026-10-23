"""S6 draft/sealed packets advertise the collector's per-finding evidence contract."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.agents.collect import collect_job
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.tests.test_pipeline import card, REPO


class ReviewEvidenceTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def emit(self, scope, suffix):
        root = self.root / suffix
        ctx = StageContext(load_card(write_once(root / "card.json", card())), root / "work", REPO)
        write_once(ctx.design_dir / "SPEC.json", {"fixture": "invented registered specification"})
        write_once(ctx.design_dir / "SUPPORT.json", {"metadata_only": True})
        if scope == "sealed_candidate":
            write_once(ctx.design_dir / "FREEZE_FINAL.json", {"fixture": "invented sealed candidate"})
        return emit_job(ctx, "S6", "reviewer", jobs_root=root / "jobs", allow_draft=True)

    def review(self, job, bucket="must", evidence="fixture.py:12; invented omitted result reproduction"):
        finding = {"id": "M01", "description": "Invented registered result omitted",
                   "evidence": evidence, "impact": "missing_registered_result"}
        return {"design_id": job.design_id, "candidate_sha256": job.contract["candidate_sha256"],
                "review_scope": job.contract["review_scope"], "complete_sweeps": 1, "fix_cycles": 0,
                "must": [finding] if bucket == "must" else [],
                "should": [finding] if bucket == "should" else [],
                "verdict": "STOP" if bucket == "must" else "PASS",
                "independence_attestation": {"wrote_implementation": False, "wrote_fixes": False,
                                             "real_outcome_associations_read": False}}

    def put(self, job, value):
        output = Path(job.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value))

    def test_both_scopes_bind_evidence_schema_and_versioned_template(self):
        for scope in ("draft_readiness", "sealed_candidate"):
            with self.subTest(scope=scope):
                job = self.emit(scope, scope)
                self.assertEqual(load_job(Path(job.job_path)), job)
                self.assertEqual(Path(job.template_path).parent.name, "v3")
                self.assertEqual(job.contract["review_scope"], scope)
                schema = job.contract["review_output_schema"]
                for bucket in ("must", "should"):
                    finding = schema["properties"][bucket]["items"]
                    self.assertEqual(set(finding["required"]), {"id", "description", "evidence", "impact"})
                    self.assertEqual(finding["properties"]["evidence"],
                                     {"type": "string", "minLength": 1, "pattern": r"\S"})
                prompt = Path(job.job_path).read_text()
                self.assertIn("The evidence field is mandatory for draft_readiness and sealed_candidate reviews", prompt)
                self.assertIn("every finding contains its own evidence string", prompt)
                self.assertFalse(job.contract["execution_authorized"])

    def test_missing_blank_or_nonstring_evidence_rejected_in_both_buckets_and_scopes(self):
        for scope in ("draft_readiness", "sealed_candidate"):
            for bucket in ("must", "should"):
                job = self.emit(scope, scope + bucket)
                for evidence in (None, "", " \n\t", 123, {"locator": "fixture.py:12"}):
                    with self.subTest(scope=scope, bucket=bucket, evidence=evidence):
                        value = self.review(job, bucket, evidence)
                        self.put(job, value)
                        with self.assertRaisesRegex(ValueError, "Review finding lacks evidence"):
                            collect_job(Path(job.job_path))
                value = self.review(job, bucket)
                del value[bucket][0]["evidence"]
                value[bucket][0]["locator"] = "fixture.py:12"
                self.put(job, value)
                with self.assertRaisesRegex(ValueError, "Review finding lacks evidence"):
                    collect_job(Path(job.job_path))
                self.assertFalse(Path(job.promote_path).exists())

    def test_evidenced_findings_collect_and_keep_execution_unauthorized(self):
        for scope in ("draft_readiness", "sealed_candidate"):
            for bucket in ("must", "should"):
                with self.subTest(scope=scope, bucket=bucket):
                    job = self.emit(scope, scope + bucket)
                    self.put(job, self.review(job, bucket))
                    receipt = collect_job(Path(job.job_path))
                    self.assertTrue(receipt["validated"])
                    self.assertFalse(receipt["execution_authorized"])

    def test_empty_findings_collect_without_placeholder_evidence(self):
        for scope in ("draft_readiness", "sealed_candidate"):
            with self.subTest(scope=scope):
                job = self.emit(scope, scope)
                value = self.review(job)
                value.update(must=[], should=[], verdict="PASS")
                self.put(job, value)
                self.assertTrue(collect_job(Path(job.job_path))["validated"])


if __name__ == "__main__":
    unittest.main()
