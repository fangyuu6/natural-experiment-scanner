"""Synthetic contract tests. No downloaded data or outcome estimation."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

try:
    from .export_static import export_static, inline_safe_json
    from .import_queue import import_queue
    from .validate import COMMON_CHECKS, FREEZE_ROLES, TRACK_CHECKS, bundle_digest, result_payload, spec_payload, validate_registry
except ImportError:
    from export_static import export_static, inline_safe_json
    from import_queue import import_queue
    from validate import COMMON_CHECKS, FREEZE_ROLES, TRACK_CHECKS, bundle_digest, result_payload, spec_payload, validate_registry


def queued(track="did"):
    readiness = {"checks": {k: False for k in sorted(COMMON_CHECKS | TRACK_CHECKS[track])},
                 "evidence_refs": ["synthetic:extract"], "missing": ["Independent verification"]}
    event = {"event_id": "e0", "at": "2026-10-06T00:00:00Z", "from_status": None,
             "to_status": "queued", "actor": "synthetic-test", "reason": "L1 candidate", "reopen_condition": None,
             "evidence_refs": ["synthetic:extract"], "outcome_spec_id": None, "run_id": None, "readiness_snapshot": None}
    design = {"design_id": "d1", "policy_family_id": "f1", "legal_instrument_ids": ["l1"], "source_text_ids": ["t1"],
              "design_type": track, "domain": "labor_market", "label": "Synthetic policy", "screen_version": "l1-existing",
              "queue_version": "queue_v2.0", "priority": {"evidence_strength": 3, "free_data_availability": 0,
              "domain_priority": 5, "rationale": "Evidence, free data and domain only"}, "readiness": readiness,
              "status": "queued", "reason": event["reason"], "reopen_condition": None,
              "active_outcome_spec_id": None, "active_run_id": None, "history": [event]}
    return {"registry_version": "1.0.0", "generated_at": "2026-10-06T00:10:00Z",
            "source_texts": [{"source_text_id": "t1", "source_family": "Synthetic fixture", "extraction_version": "existing",
                              "locator": "synthetic:text", "text_sha256": None}],
            "policy_families": [{"policy_family_id": "f1", "label": "Synthetic policy", "domains": ["labor_market"]}],
            "legal_instruments": [{"legal_instrument_id": "l1", "policy_family_ids": ["f1"], "jurisdiction": "Synthetic state",
                                   "legal_citation": None, "source_text_ids": ["t1"], "identity_status": "provisional"}],
            "designs": [design], "outcome_specs": [], "runs": []}


def event(registry, status, spec=None, run=None, reopen=None, refs=None):
    d = registry["designs"][0]
    i = len(d["history"])
    e = {"event_id": f"e{i}", "at": f"2026-10-06T00:00:{i:02d}Z", "from_status": d["status"], "to_status": status,
         "actor": "synthetic-test", "reason": "Synthetic " + status, "reopen_condition": reopen,
         "evidence_refs": refs or ["synthetic:evidence"], "outcome_spec_id": spec, "run_id": run,
         "readiness_snapshot": copy.deepcopy(d["readiness"]) if status == "data-ready" else None}
    d["history"].append(e)
    d.update(status=status, reason=e["reason"], reopen_condition=reopen, active_outcome_spec_id=spec, active_run_id=run)
    return e


def ready(registry):
    event(registry, "verifying")
    ins = registry["legal_instruments"][0]
    ins.update(identity_status="verified", legal_citation="Synthetic Act 1")
    r = registry["designs"][0]["readiness"]
    r["checks"] = {k: True for k in r["checks"]}
    r["missing"] = []
    event(registry, "data-ready")


def freeze(registry, root, version=1):
    spec = {"outcome_spec_id": f"s{version}", "design_id": "d1", "outcome_id": "o1", "spec_version": f"v{version}",
            "outcome_label": "Synthetic outcome", "unit": "synthetic units",
            "specification": {k: "Synthetic preregistered " + k for k in ["estimand", "sample", "clock", "controls", "weights", "inference", "diagnostics", "stop_rules"]},
            "supersedes": f"s{version-1}" if version > 1 else None, "freeze_bundle": None}
    artifacts = []
    for role in sorted(FREEZE_ROLES):
        path = f"v{version}_{role}.json"
        raw = json.dumps(spec_payload(spec) if role == "spec" else {"synthetic": role}).encode()
        (root / path).write_bytes(raw)
        artifacts.append({"role": role, "path": path, "sha256": hashlib.sha256(raw).hexdigest()})
    at = f"2026-10-06T00:00:{len(registry['designs'][0]['history']):02d}Z"
    bundle = {"frozen_at": at, "frozen_by": "synthetic-test", "artifacts": artifacts, "sha256": ""}
    bundle["sha256"] = bundle_digest(bundle)
    spec["freeze_bundle"] = bundle
    registry["outcome_specs"].append(spec)
    event(registry, "frozen", spec=spec["outcome_spec_id"])
    return spec


def completed(registry, root):
    spec = registry["outcome_specs"][-1]
    e = event(registry, "run", spec=spec["outcome_spec_id"], run="r1")
    artifacts = []
    for role in ["result", "diagnostics", "reference", "support"]:
        path = role + ".json"
        raw = json.dumps({"synthetic": role}).encode()
        (root / path).write_bytes(raw)
        artifacts.append({"role": role, "path": path, "sha256": hashlib.sha256(raw).hexdigest()})
    run = {"run_id": "r1", "design_id": "d1", "outcome_spec_id": spec["outcome_spec_id"],
           "freeze_bundle_sha256": spec["freeze_bundle"]["sha256"], "started_at": e["at"],
           "finished_at": "2026-10-06T00:00:05Z", "status": "completed", "reason": "Synthetic success",
           "reopen_condition": None, "artifacts": artifacts,
           "result": {"estimate": -1, "interval": [-5, 3], "interval_level": 0.95, "unit": "synthetic units",
                      "reference": "Synthetic reference", "support": "Synthetic support", "diagnostics": "Synthetic diagnostics"}}
    raw = json.dumps(result_payload(run)).encode()
    (root / "result.json").write_bytes(raw)
    next(a for a in artifacts if a["role"] == "result")["sha256"] = hashlib.sha256(raw).hexdigest()
    registry["runs"].append(run)
    return run


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.r = queued()

    def tearDown(self):
        self.temp.cleanup()

    def assertValid(self, registry=None, previous=None):
        self.assertEqual(validate_registry(registry or self.r, self.root, previous), [])

    def assertInvalid(self, message, registry=None, previous=None):
        errors = validate_registry(registry or self.r, self.root, previous)
        self.assertTrue(any(message in e for e in errors), errors)

    def test_all_tracks_queue_without_verified_claim(self):
        for track in TRACK_CHECKS:
            with self.subTest(track=track):
                self.assertValid(queued(track))

    def test_unknown_fields_cannot_smuggle_sign_into_ranking(self):
        self.r["designs"][0]["priority"]["expected_sign"] = "positive"
        self.assertInvalid("unknown field expected_sign")

    def test_duplicate_and_dangling_ids(self):
        self.r["source_texts"].append(copy.deepcopy(self.r["source_texts"][0]))
        self.r["designs"][0]["legal_instrument_ids"] = ["missing"]
        self.assertInvalid("duplicate source_text_id")
        self.assertInvalid("unknown legal_instruments reference")

    def test_multiple_families_can_share_one_law(self):
        self.r["policy_families"].append({"policy_family_id": "f2", "label": "Second synthetic family", "domains": ["taxation_fiscal"]})
        self.r["legal_instruments"][0]["policy_family_ids"].append("f2")
        self.assertValid()

    def test_lineage_cannot_cross_policy_family(self):
        self.r["policy_families"].append({"policy_family_id": "f2", "label": "Other family", "domains": ["labor_market"]})
        self.r["designs"][0]["policy_family_id"] = "f2"
        self.assertInvalid("instrument belongs to another family")

    def test_cannot_skip_verification(self):
        event(self.r, "frozen")
        self.assertInvalid("illegal transition queued -> frozen")

    def test_stopped_needs_reason_and_reopen_condition(self):
        event(self.r, "stopped")
        self.assertInvalid("stopped requires reopen condition")
        self.r["designs"][0]["reason"] = ""
        self.assertInvalid("invalid string length")

    def test_stop_reopen_preserves_historical_attestation(self):
        ready(self.r)
        freeze(self.r, self.root)
        event(self.r, "stopped", spec="s1", reopen="Resolve newly discovered source contradiction")
        old = copy.deepcopy(self.r)
        self.r["designs"][0]["readiness"]["checks"]["treatment_clock_verified"] = False
        self.r["designs"][0]["readiness"]["missing"] = ["Resolve date contradiction"]
        event(self.r, "verifying", spec="s1", refs=["synthetic:contradiction"])
        self.assertValid(previous=old)
        self.assertTrue(self.r["designs"][0]["history"][2]["readiness_snapshot"]["checks"]["treatment_clock_verified"])

    def test_reopen_requires_evidence(self):
        event(self.r, "stopped", reopen="Supply official law")
        e = event(self.r, "verifying")
        e["evidence_refs"] = []
        self.assertInvalid("reopen requires evidence refs")

    def test_design_specific_ready_gates(self):
        for track, missing_check in [("did", "controls_verified"), ("rdd", "running_variable_measured"), ("rct_lottery", "assignment_records_verified")]:
            with self.subTest(track=track):
                r = queued(track)
                ready(r)
                self.assertValid(r)
                r["designs"][0]["history"][-1]["readiness_snapshot"]["checks"][missing_check] = False
                self.assertInvalid("data-ready gate incomplete", r)

    def test_freeze_requires_real_bytes_and_all_roles(self):
        ready(self.r)
        spec = freeze(self.r, self.root)
        self.assertValid()
        self.assertTrue(any("artifact_root required" in e for e in validate_registry(self.r)))
        spec["freeze_bundle"]["artifacts"][-1]["role"] = "result"
        self.assertInvalid("freeze bundle missing roles")

    def test_input_mutation_detected(self):
        ready(self.r)
        freeze(self.r, self.root)
        (self.root / "v1_treatment.json").write_text("changed")
        self.assertInvalid("artifact hash mismatch v1_treatment.json")

    def test_registry_spec_change_cannot_keep_artifact_hash(self):
        ready(self.r)
        s = freeze(self.r, self.root)
        s["specification"]["sample"] = "Different sample"
        self.assertInvalid("spec artifact differs from registry specification")

    def test_bundle_metadata_mutation_detected(self):
        ready(self.r)
        s = freeze(self.r, self.root)
        s["freeze_bundle"]["frozen_by"] = "somebody-else"
        self.assertInvalid("freeze bundle digest mismatch")

    def test_traversal_and_symlink_escape_rejected(self):
        ready(self.r)
        s = freeze(self.r, self.root)
        s["freeze_bundle"]["artifacts"][0]["path"] = "../outside.json"
        self.assertInvalid("cannot contain '..'")
        s["freeze_bundle"]["artifacts"][0]["path"] = "escape.json"
        (self.root / "escape.json").symlink_to(self.root.parent / "outside.json")
        self.assertInvalid("symlink escapes")

    def test_credential_path_rejected_before_reading(self):
        ready(self.r)
        s = freeze(self.r, self.root)
        s["freeze_bundle"]["artifacts"][0]["path"] = ".env"
        self.assertInvalid("credential/configuration paths")

    def test_history_is_append_only_between_snapshots(self):
        old = copy.deepcopy(self.r)
        self.r["designs"][0]["history"][0]["actor"] = "rewritten"
        self.assertInvalid("history must be append-only", previous=old)

    def test_source_archive_enrichment_preserves_original_source_id(self):
        old = copy.deepcopy(self.r)
        source = self.r["source_texts"][0]
        source["text_sha256"] = "a" * 64
        source["extraction_records"] = [{"source_record_id": "t1::new-chapter", "chapter_id": "new-chapter",
                                         "payload_sha256": "b" * 64, "locator": "synthetic:record", "source_url": None}]
        self.assertValid(previous=old)
        archived = copy.deepcopy(self.r)
        source["text_sha256"] = "c" * 64
        self.assertInvalid("source identity/evidence rewrite", previous=archived)

    def test_family_and_legal_relationships_can_be_enriched_without_rewriting_identity(self):
        self.r["legal_instruments"][0].update(identity_status="verified", legal_citation="Synthetic Act 1")
        old = copy.deepcopy(self.r)
        self.r["policy_families"][0]["domains"].append("taxation_fiscal")
        self.r["policy_families"].append({"policy_family_id": "f2", "label": "New chapter family", "domains": ["taxation_fiscal"]})
        self.r["legal_instruments"][0]["policy_family_ids"].append("f2")
        self.assertValid(previous=old)
        self.r["legal_instruments"][0]["legal_citation"] = "Different law"
        self.assertInvalid("verified legal identity rewrite", previous=old)

    def test_frozen_spec_and_terminal_runs_are_immutable(self):
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        self.assertValid()
        old = copy.deepcopy(self.r)
        run["result"]["estimate"] = 99
        self.r["outcome_specs"][0]["unit"] = "rewritten units"
        self.assertInvalid("frozen spec is immutable", previous=old)
        self.assertInvalid("terminal run is immutable", previous=old)

    def test_reopened_freeze_requires_new_version(self):
        ready(self.r)
        freeze(self.r, self.root)
        event(self.r, "stopped", spec="s1", reopen="New support evidence")
        event(self.r, "verifying", spec="s1")
        event(self.r, "data-ready", spec="s1")
        event(self.r, "frozen", spec="s1")
        self.assertInvalid("reopening requires a new outcome/spec version")

    def test_new_version_after_reopen_preserves_old_freeze(self):
        ready(self.r)
        freeze(self.r, self.root)
        event(self.r, "stopped", spec="s1", reopen="New support evidence")
        old = copy.deepcopy(self.r)
        event(self.r, "verifying", spec="s1")
        event(self.r, "data-ready", spec="s1")
        freeze(self.r, self.root, version=2)
        self.assertValid(previous=old)

    def test_run_cannot_start_before_bound_freeze(self):
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        run["started_at"] = "2026-10-06T00:00:00Z"
        self.assertInvalid("run starts before freeze")
        run["started_at"] = self.r["designs"][0]["history"][-1]["at"]
        run["freeze_bundle_sha256"] = "0" * 64
        self.assertInvalid("run freeze hash differs")

    def test_display_requires_completed_result(self):
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        event(self.r, "shown", spec="s1", run="r1")
        self.assertValid()
        run.update(status="failed", result=None, reopen_condition="Correct an infrastructure failure under the same frozen spec")
        self.assertInvalid("shown requires completed run")

    def test_display_cannot_precede_completion(self):
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        event(self.r, "shown", spec="s1", run="r1")
        run["finished_at"] = "2026-10-06T00:00:06Z"
        self.assertInvalid("display cannot precede run completion")

    def test_result_summary_is_bound_to_hashed_result_bytes(self):
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        run["result"]["estimate"] = 99
        self.assertInvalid("result artifact differs from registry result")

    def test_ready_attestation_cannot_be_silently_revoked(self):
        ready(self.r)
        self.r["designs"][0]["readiness"]["checks"]["controls_verified"] = False
        self.assertInvalid("current readiness differs from last data-ready attestation")

    def test_finite_numbers_and_priority_integer(self):
        self.r["designs"][0]["priority"]["evidence_strength"] = True
        self.assertInvalid("expected integer")
        self.r["designs"][0]["priority"]["evidence_strength"] = 3
        ready(self.r)
        freeze(self.r, self.root)
        run = completed(self.r, self.root)
        run["result"]["estimate"] = float("nan")
        self.assertInvalid("matches no anyOf branch")

    def test_static_export_retains_negative_estimate_and_stopped_record(self):
        ready(self.r)
        freeze(self.r, self.root)
        completed(self.r, self.root)
        event(self.r, "stopped", spec="s1", run="r1", reopen="Resolve identifying assumption")
        result = export_static(self.r, self.root)
        self.assertEqual(result["counts"]["status"], {"stopped": 1})
        self.assertEqual(result["records"][0]["runs"][0]["result"]["estimate"], -1)
        self.assertEqual(result["records"][0]["reopen_condition"], "Resolve identifying assumption")

    def test_export_safe_to_inline_untrusted_text(self):
        payload = {"text": "</script><script>bad()</script>\u2028\u2029"}
        text = inline_safe_json(payload)
        self.assertNotIn("<", text)
        self.assertEqual(json.loads(text), payload)

    def test_static_export_refuses_invalid_registry(self):
        self.r["designs"][0]["status"] = "shown"
        with self.assertRaises(ValueError):
            export_static(self.r)

    def test_queue_bridge_preserves_ids_hashes_and_provisional_status(self):
        source = {"source_text_id": "t1", "source_record_id": "t1::c1", "chapter_id": "c1", "source": "chapters",
                  "policy_family_id": "f1", "legal_instrument_id": "l1", "jurisdiction": "Synthetic state",
                  "payload_sha256": "a" * 64, "source_url": "https://example.invalid/law", "evidence_strength": 3}
        (self.root / "summary.json").write_text(json.dumps({"version": "queue_v2.0"}))
        for filename in ["did_designs.jsonl", "rdd_candidates.jsonl", "rct_mentions.jsonl"]:
            (self.root / filename).write_text("")
        row = {"design_id": "d1", "domain": "labor_market", "policy_family": "Synthetic policy",
               "source_records": [source], "data_alias_verdict": "public_ok", "evidence_status": "Unverified extracted evidence"}
        (self.root / "did_designs.jsonl").write_text(json.dumps(row) + "\n")
        result = import_queue(self.root, {"d1"}, "2026-10-06T00:10:00Z")
        self.assertValid(result)
        self.assertEqual(result["designs"][0]["design_id"], "d1")
        self.assertEqual(result["source_texts"][0]["extraction_records"][0]["payload_sha256"], "a" * 64)
        self.assertIsNone(result["source_texts"][0]["text_sha256"])
        self.assertEqual(result["legal_instruments"][0]["identity_status"], "provisional")
        self.assertFalse(any(result["designs"][0]["readiness"]["checks"].values()))
        with self.assertRaisesRegex(ValueError, "does not contain"):
            import_queue(self.root, {"missing"})


if __name__ == "__main__":
    unittest.main()
