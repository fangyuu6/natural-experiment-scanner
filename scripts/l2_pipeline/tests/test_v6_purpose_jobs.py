import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import emit_purpose_verification, emit_stage_jobs, collect_job, load_job, accept_purpose_verification
from scripts.l2_pipeline.agents.dispatch import main as dispatch_main, command
from scripts.l2_pipeline.agents.purpose import purpose_domain
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import write_once, sha
from scripts.l2_pipeline.tests.test_pipeline import card

REPO = Path(__file__).resolve().parents[3]


class PurposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        value = card()
        value.update(automation={"intake": {"domain": "Health"}},
                     population={"description": "Eligible residents", "filters": {}},
                     first_order={"target_outcome": "health_insurance_coverage", "primary_outcome": "health_insurance_coverage",
                                  "relation": "direct", "availability": "available", "secondary_outcomes": [],
                                  "statute_refs": [{"source_ref": "https://legislature.test/statute", "locator": "Purpose lead"}],
                                  "literature_refs": [{"source_ref": "https://papers.test/lead", "locator": "Definition lead"}]})
        value["outcome"]["paths"] = ["secret-outcome.csv"]
        value["automation"]["prior_forecast"] = "secret-prediction"
        value["treatment"]["records"] = [{"secret": "secret-treated-answer"}]
        write_once(self.root / "card.json", value)
        self.ctx = StageContext(load_card(self.root / "card.json"), self.root / "work", REPO)
        self.job = emit_purpose_verification(self.ctx, self.root / "jobs")

    def tearDown(self): self.tmp.cleanup()

    def output(self):
        return {"design_id": self.job.design_id, "role": "purpose_verifier",
                "card_sha256": self.job.contract["card_sha256"], "internal_persona_lens": "Finkelstein",
                "status": "VERIFIED", "target_outcome": "health_insurance_coverage",
                "target_population": "Eligible residents", "confirmation": "Coverage among eligible residents [1] [2]",
                "citations": [
                    {"source_type": "statute", "source_ref": "https://legislature.test/statutes/section1",
                     "title": "Invented statute", "locator": "Section 1, purpose and eligibility",
                     "supports": ["target_outcome", "target_population"]},
                    {"source_type": "paper", "source_ref": "https://papers.test/paper1",
                     "title": "Invented primary paper", "locator": "Section 2, measurement",
                     "supports": ["target_outcome"]}],
                "blindness_attestation": {"other_submissions_read": False, "outcome_associations_read": False}}

    def put(self, value):
        out = Path(self.job.output_path); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(value))

    def test_immutable_outcome_blind_emission_and_dispatch(self):
        self.assertEqual(load_job(Path(self.job.job_path)), self.job)
        self.assertEqual(emit_stage_jobs(self.ctx, "S0", jobs_root=self.root / "jobs"), [self.job])
        text = Path(self.job.job_path).read_text()
        for secret in ("secret-outcome", "secret-prediction", "secret-treated-answer"):
            self.assertNotIn(secret, text)
        self.assertIn("--search", command(self.job, "codex"))
        with patch("scripts.l2_pipeline.agents.dispatch.preflight"), patch("scripts.l2_pipeline.agents.dispatch._run") as run, contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(dispatch_main(["--jobs-root", str(self.root / "jobs"), "--stage", "S0", "--dry-run"]), 0)
        run.assert_not_called(); self.assertIn(self.job.job_path, buf.getvalue())
        self.assertFalse(Path(self.job.output_dir).exists())

    def test_persona_lenses_explicit_domain_and_legacy_fallback(self):
        for domain, expected in (("Labor", "labor"), ("Health", "health"), ("Law and Crime", "crime"), ("Education", "education"), ("Civil Rights", "public_programs")):
            self.assertEqual(purpose_domain({"automation": {"intake": {"domain": domain}}}), expected)
        for family, target, expected in (("legacy_pfml", "paid_leave_benefit_receipt_pct", "labor"), ("legacy_cannabis", "medical_patient_access_pct", "health"), ("standground", "homicide_count", "crime"), ("meritaid", "college_enrollment", "education")):
            self.assertEqual(purpose_domain({"policy_family": family, "first_order": {"target_outcome": target}}), expected)

    def test_collect_requires_hub_ruling_and_returns_verified_patch(self):
        self.put(self.output()); receipt = collect_job(self.job.job_path)
        self.assertTrue(receipt["validated"]); self.assertFalse(receipt["execution_authorized"])
        purpose = json.loads(Path(self.job.promote_path).read_text())
        self.assertEqual(purpose["evidence_status"], "awaiting_hub_acceptance")
        self.assertTrue(purpose["hub_acceptance_required"])
        ruling = accept_purpose_verification(self.ctx, "accept", "Exact instrument and population checked")
        fields = ruling["card_patch"]["first_order"]
        self.assertEqual(fields["evidence_status"], "verified_by_hub")
        self.assertEqual(ruling["purpose_rule"], "V6.2")
        self.assertEqual(fields["purpose_basis"], "statute_purpose")
        self.assertEqual(len(fields["statute_refs"]), 1); self.assertEqual(len(fields["literature_refs"]), 1)
        self.assertNotIn("availability", fields)
        self.assertEqual(collect_job(self.job.job_path), receipt)

    def test_s0_attestation_optional_and_original_disclosures_preserved(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        from scripts.l2_pipeline.agents.collect import _blind
        self.assertNotIn("blindness_attestation", self.job.contract["output_schema"]["required"])
        value = self.output(); value.pop("blindness_attestation")
        self.put(value); original_hash = sha(Path(self.job.output_path))
        receipt = collect_job(self.job.job_path)
        self.assertTrue(receipt["validated"])
        self.assertEqual(sha(Path(self.job.output_path)), original_hash)
        self.assertNotIn("blindness_attestation", json.loads(Path(self.job.promote_path).read_text()))
        for disclosure in (None, {}, {"other_submissions_read": True,
                                     "outcome_associations_read": True},
                           {"outcome_associations_read": 0}):
            with self.subTest(disclosure=disclosure):
                value = self.output(); value["blindness_attestation"] = disclosure
                normalized = validate_purpose_output(self.job, value)
                self.assertEqual(normalized["blindness_attestation"], disclosure)
                # S1/S5's shared strict validator remains unchanged.
                with self.assertRaises(ValueError): _blind(disclosure)

    def test_missing_or_invalid_fields_and_citations_refuse(self):
        edits = [lambda v: v.pop("target_population"),
                 lambda v: v.update(target_outcome="target\nsecond line"),
                 lambda v: v.update(confirmation="Missing citation markers"),
                 lambda v: v.update(citations=[]),
                 lambda v: v["citations"][1].update(source_ref=v["citations"][0]["source_ref"]),
                 lambda v: v["citations"][1].update(source_ref="https://papers.test/?term=search"),
                 lambda v: v["citations"][1].update(locator=" "),
                 lambda v: v["citations"][0].update(supports=["target_outcome"]),
                 lambda v: v.update(internal_persona_lens="Card")]
        for edit in edits:
            value = self.output(); edit(value); self.put(value)
            with self.assertRaises(ValueError): collect_job(self.job.job_path)
        self.assertFalse(Path(self.job.promote_path).exists())

    def test_v62_statute_only_purpose_and_population_is_sufficient(self):
        value = self.output()
        value["citations"] = value["citations"][:1]
        value["confirmation"] = "Coverage among eligible residents [1]"
        self.put(value); collect_job(self.job.job_path)
        ruling = accept_purpose_verification(self.ctx, "accept", "Statute purpose and eligibility reviewed")
        self.assertEqual(ruling["purpose_basis"], "statute_purpose")
        self.assertEqual(ruling["card_patch"]["first_order"]["literature_refs"], [])

    def test_v62_two_study_route_can_use_scope_only_statute(self):
        value = self.output()
        value["citations"][0]["supports"] = ["target_population"]
        value["citations"].append(copy.deepcopy(value["citations"][1]))
        value["citations"][2].update(source_ref="https://papers.test/paper2", title="Second established study")
        value["confirmation"] = "Coverage among eligible residents [1] [2] [3]"
        self.put(value); collect_job(self.job.job_path)
        ruling = accept_purpose_verification(self.ctx, "accept", "Two established studies use this policy-type target")
        self.assertEqual(ruling["purpose_basis"], "established_literature")

    def test_v62_two_study_route_and_missing_population_or_single_study_refuse(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        value = self.output()
        first = value["citations"][1]
        second = copy.deepcopy(first)
        second.update(source_ref="https://papers.test/paper2", title="Second established study",
                      supports=["target_outcome", "target_population"])
        value["citations"] = [first, second]
        self.assertEqual(validate_purpose_output(self.job, value)["citations"], value["citations"])
        missing_population = copy.deepcopy(value)
        missing_population["citations"][1]["supports"] = ["target_outcome"]
        with self.assertRaisesRegex(ValueError, "population"):
            validate_purpose_output(self.job, missing_population)
        single_study = copy.deepcopy(value)
        single_study["citations"] = [second]
        single_study["confirmation"] = "Coverage among eligible residents [1]"
        with self.assertRaisesRegex(ValueError, "at least two"):
            validate_purpose_output(self.job, single_study)

    def test_v62_publisher_and_working_paper_urls_do_not_make_two_studies(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        value = self.output()
        value["citations"][0]["supports"] = ["target_population"]
        value["citations"][1]["title"] = "A Study Of Coverage"
        duplicate = copy.deepcopy(value["citations"][1])
        duplicate.update(source_ref="https://workingpapers.test/same-study",
                         title="  a  study OF\tcoverage  ")
        value["citations"].append(duplicate)
        value["confirmation"] = "Coverage among eligible residents [1] [2] [3]"
        with self.assertRaisesRegex(ValueError, "at least two literature studies"):
            validate_purpose_output(self.job, value)
        duplicate["title"] = "A Different Study Of Coverage"
        self.assertEqual(validate_purpose_output(self.job, value)["citations"], value["citations"])

    def test_v62_hub_can_redecide_superseded_unresolved_without_editing_originals(self):
        value = self.output()
        value.update(status="UNRESOLVED", target_outcome="legal_instrument",
                     unresolved_reason="Behavior is not the same construct as the law")
        self.put(value); collect_job(self.job.job_path)
        original = Path(self.job.output_path)
        promoted = Path(self.job.promote_path)
        receipt = self.ctx.design_dir / "agents/purpose_verifier/COLLECTION.json"
        bindings = {p: sha(p) for p in (original, promoted, receipt, self.ctx.card.path)}
        old = self.ctx.design_dir / "PURPOSE_HUB_RULING.json"
        write_once(old, {"decision": "park", "reason": value["unresolved_reason"]})
        old_sha = sha(old)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            accept_purpose_verification(self.ctx, "accept", "V6.2 review")
        with self.assertRaisesRegex(ValueError, "verified target and population"):
            accept_purpose_verification(self.ctx, "accept", "V6.2 review", verified_target="health_insurance_coverage")
        ruling = accept_purpose_verification(self.ctx, "accept", "Existing purpose citations support the behavioral target under V6.2",
            verified_target="health_insurance_coverage", verified_population="Eligible residents",
            verified_citations=value["citations"])
        self.assertTrue(ruling["explicit_hub_reverification"])
        self.assertEqual(ruling["card_patch"]["first_order"]["target_outcome"], "health_insurance_coverage")
        self.assertEqual(ruling["supersedes"]["sha256"], old_sha)
        self.assertEqual(sha(old), old_sha)
        self.assertEqual({p: sha(p) for p in bindings}, bindings)
        self.assertTrue((self.ctx.design_dir / "PURPOSE_HUB_RULING_V62.json").exists())
        invented = copy.deepcopy(value["citations"])
        invented[0]["locator"] = "Invented purpose passage"
        with self.assertRaisesRegex(ValueError, "retain original"):
            accept_purpose_verification(self.ctx, "accept", "V6.2 review", ruling_name="another.json",
                verified_target="health_insurance_coverage", verified_population="Eligible residents",
                verified_citations=invented)
        with self.assertRaisesRegex(ValueError, "local JSON"):
            accept_purpose_verification(self.ctx, "park", "test", ruling_name="../escape.json")

    def test_v63_new_template_preserves_legacy_job_template_bytes(self):
        self.assertTrue(self.job.template_path.endswith("purpose_verifier_v6_3.md"))
        self.assertIn('population_catalog',self.job.contract)
        text = Path(self.job.job_path).read_text()
        self.assertIn("at least two distinct literature studies", text)
        self.assertIn("legal-instrument mismatch", text)
        self.assertIn("intent-to-treat dilution", text)
        self.assertEqual(self.job.contract["purpose_rule"], "V6.2")
        old = Path(self.job.template_path).with_name("purpose_verifier_v6_1.md")
        self.assertIn("including at least one statute", old.read_text())
        prior = Path(self.job.template_path).with_name("purpose_verifier_v6_2.md")
        self.assertNotIn('target_population_metadata',prior.read_text())

    def test_unresolved_can_park_but_cannot_accept(self):
        value = self.output(); value.update(status="UNRESOLVED", unresolved_reason="Exact population eligibility disputed")
        self.put(value); collect_job(self.job.job_path)
        with self.assertRaisesRegex(ValueError, "unresolved"): accept_purpose_verification(self.ctx, "accept", "Invented check")
        self.assertEqual(accept_purpose_verification(self.ctx, "park", "Resolve eligibility")["card_patch"], {})

    def test_card_or_original_tamper_refuses_acceptance(self):
        self.put(self.output()); collect_job(self.job.job_path)
        value = self.output(); value["target_population"] = "Another population"; self.put(value)
        with self.assertRaises(ValueError): accept_purpose_verification(self.ctx, "accept", "Invented check")
        self.ctx.card.path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "card input changed"): load_job(Path(self.job.job_path))


if __name__ == "__main__": unittest.main()
