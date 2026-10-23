#!/usr/bin/env python3
"""Dependency-free shape, lineage, transition and freeze validation. No estimator."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

SCHEMA = json.loads(Path(__file__).with_name("schema.json").read_text())
TRANSITIONS = {
    "queued": {"verifying", "stopped"},
    "verifying": {"verifying", "data-ready", "stopped"},
    "data-ready": {"frozen", "stopped"},
    "frozen": {"run", "stopped"},
    "run": {"shown", "stopped"},
    "shown": {"stopped"},
    "stopped": {"verifying"},
}
COMMON_CHECKS = {"legal_identity_verified", "independent_coding_verified", "outcome_linkage_verified",
                 "free_public_data_verified", "target_population_verified", "confounding_reviewed"}
TRACK_CHECKS = {
    "did": {"treatment_clock_verified", "exposure_path_verified", "controls_verified", "clean_preperiod_verified", "panel_support_verified"},
    "rdd": {"threshold_verified", "running_variable_measured", "local_support_verified", "enforcement_verified", "manipulation_reviewed", "co_threshold_reviewed"},
    "rct_lottery": {"mechanism_verified", "assignment_records_verified", "probabilities_verified", "compliance_reviewed", "attrition_reviewed"},
}
FREEZE_ROLES = {"treatment", "outcome", "spec", "code", "environment", "predictions", "verification"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def spec_payload(spec):
    """The spec artifact must encode exactly this object, binding IDs and units too."""
    return {k: v for k, v in spec.items() if k != "freeze_bundle"}


def bundle_digest(bundle):
    return digest({k: v for k, v in bundle.items() if k != "sha256"})


def result_payload(run):
    return {k: run[k] for k in ("run_id", "design_id", "outcome_spec_id", "freeze_bundle_sha256", "result")}


def _timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _shape(value, schema, path="$", errors=None):
    """Interpret every keyword used by our published JSON Schema, offline."""
    errors = [] if errors is None else errors
    if "$ref" in schema:
        return _shape(value, SCHEMA["$defs"][schema["$ref"].split("/")[-1]], path, errors)
    if "anyOf" in schema and not any(not _shape(value, child, path) for child in schema["anyOf"]):
        errors.append(f"{path}: matches no anyOf branch")
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: invalid enum {value!r}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    predicates = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
                  "string": lambda: isinstance(value, str), "null": lambda: value is None,
                  "boolean": lambda: isinstance(value, bool), "integer": lambda: type(value) is int,
                  "number": lambda: type(value) in (int, float) and math.isfinite(value)}
    if types and not any(predicates[t]() for t in types):
        errors.append(f"{path}: expected {'/'.join(types)}")
        return errors
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                _shape(child, properties[key], f"{path}.{key}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unknown field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _shape(child, schema["additionalProperties"], f"{path}.{key}", errors)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            errors.append(f"{path}: invalid array length")
        if schema.get("uniqueItems") and len({canonical(x) for x in value}) != len(value):
            errors.append(f"{path}: duplicate array items")
        for i, child in enumerate(value):
            if "items" in schema:
                _shape(child, schema["items"], f"{path}[{i}]", errors)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            errors.append(f"{path}: invalid string length")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: invalid pattern")
        if schema.get("format") == "date-time":
            try:
                if _timestamp(value).utcoffset() is None:
                    raise ValueError("timezone missing")
            except ValueError:
                errors.append(f"{path}: invalid timestamp")
    if type(value) in (int, float):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{path}: number outside bounds")
    return errors


def _artifact_path(root, path):
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact paths must be relative and cannot contain '..'")
    if any(p in {".env", ".aws", ".codex", "auth.json"} or p.startswith(".env.") or "wrangler" in p.lower() for p in relative.parts):
        raise ValueError("credential/configuration paths cannot be registry artifacts")
    full = (root / relative).resolve()
    if not full.is_relative_to(root.resolve()):
        raise ValueError("artifact symlink escapes artifact root")
    return full


def validate_registry(registry, artifact_root=None, previous=None):
    """Return errors; a frozen record requires artifact bytes, never just claimed hashes."""
    errors = _shape(registry, SCHEMA)
    if errors:
        return errors
    indexes = {}
    keys = {"source_texts": "source_text_id", "policy_families": "policy_family_id",
            "legal_instruments": "legal_instrument_id", "designs": "design_id",
            "outcome_specs": "outcome_spec_id", "runs": "run_id"}
    for collection, key in keys.items():
        indexes[collection] = {x[key]: x for x in registry[collection]}
        if len(indexes[collection]) != len(registry[collection]):
            errors.append(f"{collection}: duplicate {key}")
    for source in registry["source_texts"]:
        records = source.get("extraction_records", [])
        if len({r["source_record_id"] for r in records}) != len(records):
            errors.append(f"{source['source_text_id']}: duplicate extraction source_record_id")
    def ref(collection, id_, owner):
        result = indexes[collection].get(id_)
        if result is None:
            errors.append(f"{owner}: unknown {collection} reference {id_}")
        return result
    checked_files = {}
    def artifact(art, owner):
        if artifact_root is None:
            errors.append(f"{owner}: artifact_root required for frozen/run records")
            return None
        try:
            full = _artifact_path(Path(artifact_root), art["path"])
            if full not in checked_files:
                h = hashlib.sha256()
                with full.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                checked_files[full] = h.hexdigest()
            if checked_files[full] != art["sha256"]:
                errors.append(f"{owner}: artifact hash mismatch {art['path']}")
            return full
        except (OSError, ValueError) as exc:
            errors.append(f"{owner}: artifact {art['path']}: {exc}")
            return None
    for ins in registry["legal_instruments"]:
        id_ = ins["legal_instrument_id"]
        for family_id in ins["policy_family_ids"]:
            ref("policy_families", family_id, id_)
        for source in ins["source_text_ids"]:
            ref("source_texts", source, id_)
        if ins["identity_status"] == "verified" and not ins["legal_citation"]:
            errors.append(f"{id_}: verified legal identity requires citation")
    spec_versions = set()
    for spec in registry["outcome_specs"]:
        id_ = spec["outcome_spec_id"]
        ref("designs", spec["design_id"], id_)
        version_key = (spec["design_id"], spec["outcome_id"], spec["spec_version"])
        if version_key in spec_versions:
            errors.append(f"{id_}: duplicate outcome/spec version")
        spec_versions.add(version_key)
        if spec["supersedes"]:
            old = ref("outcome_specs", spec["supersedes"], id_)
            if old and (old["design_id"] != spec["design_id"] or old["outcome_id"] != spec["outcome_id"] or
                        int(old["spec_version"][1:]) >= int(spec["spec_version"][1:])):
                errors.append(f"{id_}: supersedes must refer to an earlier version of the same outcome/design")
        bundle = spec["freeze_bundle"]
        if bundle:
            if bundle["sha256"] != bundle_digest(bundle):
                errors.append(f"{id_}: freeze bundle digest mismatch")
            roles = {a["role"] for a in bundle["artifacts"]}
            if not FREEZE_ROLES <= roles:
                errors.append(f"{id_}: freeze bundle missing roles {sorted(FREEZE_ROLES - roles)}")
            if len([a for a in bundle["artifacts"] if a["role"] == "spec"]) != 1:
                errors.append(f"{id_}: exactly one spec artifact required")
            if len({a["path"] for a in bundle["artifacts"]}) != len(bundle["artifacts"]):
                errors.append(f"{id_}: freeze artifacts must have distinct paths")
            for art in bundle["artifacts"]:
                full = artifact(art, id_)
                if art["role"] == "spec" and full:
                    try:
                        if json.loads(full.read_text()) != spec_payload(spec):
                            errors.append(f"{id_}: spec artifact differs from registry specification")
                    except (OSError, ValueError, UnicodeError):
                        errors.append(f"{id_}: spec artifact is not readable JSON")
    frozen_events, run_events, event_ids = set(), set(), set()
    for design in registry["designs"]:
        id_ = design["design_id"]
        family = ref("policy_families", design["policy_family_id"], id_)
        if family and design["domain"] not in family["domains"]:
            errors.append(f"{id_}: domain differs from family")
        instrument_sources, instruments = set(), []
        for ins_id in design["legal_instrument_ids"]:
            ins = ref("legal_instruments", ins_id, id_)
            if ins:
                instruments.append(ins)
                instrument_sources.update(ins["source_text_ids"])
                if design["policy_family_id"] not in ins["policy_family_ids"]:
                    errors.append(f"{id_}: instrument belongs to another family")
        for source in design["source_text_ids"]:
            ref("source_texts", source, id_)
        if not set(design["source_text_ids"]) <= instrument_sources:
            errors.append(f"{id_}: source text lineage missing from legal instruments")
        last_status, last_time, used_freezes, last_ready = None, None, set(), None
        for event in design["history"]:
            if event["event_id"] in event_ids:
                errors.append(f"{id_}: duplicate event_id {event['event_id']}")
            event_ids.add(event["event_id"])
            if event["from_status"] != last_status:
                errors.append(f"{id_}: broken transition chain")
            if last_status is None:
                if event["to_status"] != "queued":
                    errors.append(f"{id_}: first event must queue design")
            elif event["to_status"] not in TRANSITIONS[last_status]:
                errors.append(f"{id_}: illegal transition {last_status} -> {event['to_status']}")
            if last_status == event["to_status"] == "verifying" and not event["evidence_refs"]:
                errors.append(f"{id_}: verification review requires evidence refs")
            at = _timestamp(event["at"])
            if last_time and at < last_time:
                errors.append(f"{id_}: history timestamps decrease")
            if event["to_status"] == "stopped" and not event["reopen_condition"]:
                errors.append(f"{id_}: stopped requires reopen condition")
            if event["to_status"] != "stopped" and event["reopen_condition"] is not None:
                errors.append(f"{id_}: reopen condition belongs to stopped status")
            if event["to_status"] == "data-ready":
                snapshot = event["readiness_snapshot"]
                needed = COMMON_CHECKS | TRACK_CHECKS[design["design_type"]]
                missing = sorted(x for x in needed if not snapshot or snapshot["checks"].get(x) is not True)
                if missing or not snapshot or snapshot["missing"] or not snapshot["evidence_refs"]:
                    errors.append(f"{id_}: data-ready gate incomplete {missing}")
                if any(i["identity_status"] != "verified" for i in instruments):
                    errors.append(f"{id_}: data-ready requires verified legal instruments")
                last_ready = snapshot
            elif event["readiness_snapshot"] is not None:
                errors.append(f"{id_}: readiness snapshot belongs to data-ready event")
            if last_status == "stopped" and not event["evidence_refs"]:
                errors.append(f"{id_}: reopen requires evidence refs")
            spec = ref("outcome_specs", event["outcome_spec_id"], id_) if event["outcome_spec_id"] else None
            run = ref("runs", event["run_id"], id_) if event["run_id"] else None
            if spec and spec["design_id"] != id_:
                errors.append(f"{id_}: event spec belongs to another design")
            if run and (run["design_id"] != id_ or run["outcome_spec_id"] != event["outcome_spec_id"]):
                errors.append(f"{id_}: event run/spec lineage mismatch")
            if event["to_status"] in {"frozen", "run", "shown"}:
                if not spec or not spec["freeze_bundle"]:
                    errors.append(f"{id_}: {event['to_status']} requires frozen outcome/spec")
                elif _timestamp(spec["freeze_bundle"]["frozen_at"]) > at:
                    errors.append(f"{id_}: freeze occurs after event")
            if event["to_status"] in {"run", "shown"} and spec and spec["outcome_spec_id"] not in used_freezes:
                errors.append(f"{id_}: execution/display spec has no prior freeze in this design")
            if event["to_status"] == "run" and last_status == "frozen" and event["outcome_spec_id"] != prior_event["outcome_spec_id"]:
                errors.append(f"{id_}: execution must use immediately preceding frozen spec")
            if event["to_status"] == "shown" and last_status == "run" and event["run_id"] != prior_event["run_id"]:
                errors.append(f"{id_}: display must use immediately preceding run")
            if event["to_status"] == "frozen" and spec:
                if spec["outcome_spec_id"] in used_freezes:
                    errors.append(f"{id_}: reopening requires a new outcome/spec version")
                used_freezes.add(spec["outcome_spec_id"])
                frozen_events.add(spec["outcome_spec_id"])
            if event["to_status"] in {"run", "shown"}:
                if not run:
                    errors.append(f"{id_}: {event['to_status']} requires run record")
                elif event["to_status"] == "run":
                    run_events.add(run["run_id"])
                    if _timestamp(run["started_at"]) != at:
                        errors.append(f"{id_}: run event timestamp must equal started_at")
                elif run["status"] != "completed" or run["result"] is None:
                    errors.append(f"{id_}: shown requires completed run and structured result")
                elif run["finished_at"] is None or _timestamp(run["finished_at"]) > at:
                    errors.append(f"{id_}: display cannot precede run completion")
            last_status, last_time = event["to_status"], at
            prior_event = event
        latest = design["history"][-1]
        if design["status"] in {"data-ready", "frozen", "run", "shown"} and design["readiness"] != last_ready:
            errors.append(f"{id_}: current readiness differs from last data-ready attestation; stop/reopen to revise")
        for field, history_field in [("status", "to_status"), ("reason", "reason"), ("reopen_condition", "reopen_condition"),
                                     ("active_outcome_spec_id", "outcome_spec_id"), ("active_run_id", "run_id")]:
            if design[field] != latest[history_field]:
                errors.append(f"{id_}: current {field} differs from latest event")
    for spec in registry["outcome_specs"]:
        if spec["freeze_bundle"] and spec["outcome_spec_id"] not in frozen_events:
            errors.append(f"{spec['outcome_spec_id']}: frozen bundle has no frozen event")
    for run in registry["runs"]:
        id_ = run["run_id"]
        ref("designs", run["design_id"], id_)
        spec = ref("outcome_specs", run["outcome_spec_id"], id_)
        if id_ not in run_events:
            errors.append(f"{id_}: run has no execution event")
        if not spec or spec["design_id"] != run["design_id"] or not spec["freeze_bundle"]:
            errors.append(f"{id_}: run requires matching frozen spec")
        elif run["freeze_bundle_sha256"] != spec["freeze_bundle"]["sha256"]:
            errors.append(f"{id_}: run freeze hash differs from frozen bundle")
        elif _timestamp(run["started_at"]) < _timestamp(spec["freeze_bundle"]["frozen_at"]):
            errors.append(f"{id_}: run starts before freeze")
        if run["status"] == "executing":
            if run["finished_at"] is not None or run["result"] is not None:
                errors.append(f"{id_}: executing run cannot have finish/result")
        elif run["finished_at"] is None:
            errors.append(f"{id_}: terminal run requires finished_at")
        if run["finished_at"] and _timestamp(run["finished_at"]) < _timestamp(run["started_at"]):
            errors.append(f"{id_}: run finishes before start")
        if run["status"] in {"stopped", "failed"} and not run["reopen_condition"]:
            errors.append(f"{id_}: stopped/failed run requires reopen condition")
        if run["status"] == "completed" and run["result"] is None:
            errors.append(f"{id_}: completed run requires result")
        for art in run["artifacts"]:
            full = artifact(art, id_)
            if art["role"] == "result" and run["result"] is not None and full:
                try:
                    if json.loads(full.read_text()) != result_payload(run):
                        errors.append(f"{id_}: result artifact differs from registry result")
                except (OSError, ValueError, UnicodeError):
                    errors.append(f"{id_}: result artifact is not readable JSON")
        if run["status"] == "completed" and not {"result", "diagnostics", "reference", "support"} <= {a["role"] for a in run["artifacts"]}:
            errors.append(f"{id_}: completed run requires result/diagnostics/reference/support artifacts")
        if run["status"] == "completed" and len([a for a in run["artifacts"] if a["role"] == "result"]) != 1:
            errors.append(f"{id_}: exactly one result artifact required")
        if run["result"]:
            result = run["result"]
            if spec and result["unit"] != spec["unit"]:
                errors.append(f"{id_}: result unit differs from outcome/spec")
            if result["interval"][0] > result["interval"][1] or not 0 < result["interval_level"] < 1:
                errors.append(f"{id_}: invalid result interval")
    if previous is not None:
        errors.extend(validate_update(registry, previous))
    return errors


def validate_update(current, previous):
    """Compare snapshots: frozen versions, run terminals, and audit history are append-only."""
    errors = [f"previous snapshot: {e}" for e in _shape(previous, SCHEMA)]
    if errors:
        return errors
    keymap = {"source_texts": "source_text_id", "policy_families": "policy_family_id", "legal_instruments": "legal_instrument_id",
              "designs": "design_id", "outcome_specs": "outcome_spec_id", "runs": "run_id"}
    for name, key in keymap.items():
        new = {row[key]: row for row in current[name]}
        for old in previous[name]:
            id_ = old[key]
            if id_ not in new:
                errors.append(f"{id_}: prior record deleted")
                continue
            row = new[id_]
            if name == "source_texts":
                identity = ("source_family", "extraction_version", "locator")
                old_records = {r["source_record_id"]: r for r in old.get("extraction_records", [])}
                new_records = {r["source_record_id"]: r for r in row.get("extraction_records", [])}
                if any(row[k] != old[k] for k in identity) or (old["text_sha256"] is not None and row["text_sha256"] != old["text_sha256"]) or any(new_records.get(k) != v for k, v in old_records.items()):
                    errors.append(f"{id_}: source identity/evidence rewrite; append new evidence instead")
            if name == "policy_families" and (row["label"] != old["label"] or not set(old["domains"]) <= set(row["domains"])):
                errors.append(f"{id_}: family identity/domain rewrite")
            if name == "legal_instruments":
                if not set(old["policy_family_ids"]) <= set(row["policy_family_ids"]) or not set(old["source_text_ids"]) <= set(row["source_text_ids"]):
                    errors.append(f"{id_}: legal lineage deletion/rewrite")
                if old["identity_status"] == "verified" and any(row[k] != old[k] for k in ("identity_status", "jurisdiction", "legal_citation")):
                    errors.append(f"{id_}: verified legal identity rewrite")
            if name == "designs":
                if row["history"][:len(old["history"])] != old["history"]:
                    errors.append(f"{id_}: history must be append-only")
                identity = ("policy_family_id", "source_text_ids", "legal_instrument_ids", "design_type", "domain", "screen_version", "queue_version")
                if any(row[k] != old[k] for k in identity):
                    errors.append(f"{id_}: design lineage/selection-version rewrite")
            if name == "outcome_specs" and old["freeze_bundle"] and row != old:
                errors.append(f"{id_}: frozen spec is immutable; create a new version")
            if name == "runs":
                if old["status"] != "executing" and row != old:
                    errors.append(f"{id_}: terminal run is immutable; create a new run")
                fields = ("design_id", "outcome_spec_id", "freeze_bundle_sha256", "started_at")
                if any(row[k] != old[k] for k in fields):
                    errors.append(f"{id_}: run lineage/freeze binding rewrite")
    if _timestamp(current["generated_at"]) < _timestamp(previous["generated_at"]):
        errors.append("generated_at: snapshot timestamp decreases")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--previous", type=Path, help="Previous immutable snapshot for update auditing")
    args = parser.parse_args()
    try:
        registry = json.loads(args.registry.read_text())
        previous = json.loads(args.previous.read_text()) if args.previous else None
        errors = validate_registry(registry, args.artifact_root, previous)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
