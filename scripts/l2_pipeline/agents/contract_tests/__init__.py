"""Offline template/collector contract probes used by CI and wave preflight.

Fixtures are invented; no model, subprocess, network, or outcome data is read.
The real emitters and collector run in independent temporary work directories.
"""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

from ..collect import collect_job
from .packets import emit_fixture_job

FIXTURES = Path(__file__).with_name("fixtures.json")
JOB_TYPES = (
    "s0_direction", "s0_purpose", "s0l_literature", "s1_coder", "s1_coder_v7",
    "s1_auditor", "s1_auditor_v7", "s1_followup_auditor", "s1_third_read", "s1_official_history_v9",
    "s5_forecast", "s5_forecast_v7", "s5_template_forecast", "s5_template_forecast_v7",
    "s6_draft_reviewer", "s6_sealed_reviewer",
)
SCHEMA_MARKER = "## Machine-readable output contract\n```json\n"


def job_type(job):
    """Classify semantic output contracts, including published-paper variants."""
    suffix = "_v7" if job.contract.get("source_type") == "published_did_study" else ""
    if job.stage == "S0": return "s0_direction" if job.contract.get("jobs_kind")=="s0_purpose_direction_c83" else "s0_purpose"
    if job.stage == "S0L": return "s0l_literature"
    if job.stage == "S1":
        if job.contract.get("evidence_standard") == "HUB_RULING_V9_HISTORY_EVIDENCE":
            return "s1_official_history_v9"
        if job.role in {"A", "B"}: return "s1_coder" + suffix
        if job.role == "third_reader": return "s1_third_read"
        if job.role == "auditor":
            return "s1_followup_auditor" if job.contract.get("jobs_kind") == "s1_reconciliation_followup" else "s1_auditor" + suffix
    if job.stage == "S5":
        return ("s5_template_forecast" if job.contract.get("forecast_template") else "s5_forecast") + suffix
    if job.stage == "S6" and job.role == "reviewer":
        scope = job.contract.get("review_scope")
        if scope in {"draft_readiness", "sealed_candidate"}:
            return "s6_draft_reviewer" if scope == "draft_readiness" else "s6_sealed_reviewer"
    raise ValueError("No contract test registered for job type")


def _fixture_key(kind):
    if kind.startswith("s6_"): return "s6_review"
    if kind == "s1_followup_auditor": return "s1_auditor"
    return kind.removesuffix("_v7")


def _bindings(job):
    values = dict(job.contract, design_id=job.design_id, role=job.role)
    values["blindness"] = ({"other_submissions_read": False, "our_outcome_data_read": False,
                            "our_estimates_read": False, "published_results_read": False}
                           if job.contract.get("source_type") == "published_did_study" or job.contract.get("blindness_scope")=="our_outcomes_and_estimates_v7" else
                           {"other_submissions_read": False, "outcome_associations_read": False})
    values["records"] = [{"unit": unit, "status": "treated", "start_period": 2002,
                          "mechanism": "one", "locator": "Invented official section 1"}
                         for unit in job.contract.get("manual", {}).get("universe", [])]
    if job.contract.get("evidence_standard") == "HUB_RULING_V9_HISTORY_EVIDENCE":
        for row in values["records"]:
            row["mechanism"] = job.contract["mechanism"]
        values["records"] = [dict(row, source_id="fixture-official", confidence="high",
                                  evidence_status="known_start", best_evidence_start_year=2001,
                                  evidence_refs=[{"source_id":"fixture-official", "locator":"Invented section 1",
                                                  "quote":"Invented official effective year 2001", "supports":"start_year", "year":2001}])
                             for row in values["records"]]
        values["history_research_log"] = [{"unit":row["unit"], "query":row["unit"]+" invented official start",
                                           "finding":"Invented official date"} for row in values["records"]]
    values["audit_checks"] = [{"unit": unit, "status": "match", "official_locator": "https://example.invalid/law"}
                              for unit in job.contract.get("sample", [])]
    values["third_checks"] = [dict(gap["a"], resolution="A", official_locator="https://example.invalid/law",
                                  rationale="Invented official scope and annual clock")
                              for gap in job.contract.get("conflicts", [])]
    return values


def _render(value, bindings):
    if isinstance(value, str) and value.startswith("$"):
        return copy.deepcopy(bindings[value[1:]])
    if isinstance(value, list): return [_render(item, bindings) for item in value]
    if isinstance(value, dict):
        output = _render(value["$merge"], bindings) if "$merge" in value else {}
        output.update({key: _render(item, bindings) for key, item in value.items() if key != "$merge"})
        return output
    return value


def golden_output(job):
    if job.stage=="S5" and job.role=="forecast_bundle":   # REDESIGN R2: one author, one ledger per registered member
        members={persona:dict(persona=persona,design_id=job.design_id,spec_sha256=job.contract["spec_sha256"],point=0,interval=[-1,1],
            interval_level=0.8,rationale="Invented original-unit ATT",disclosures=[],
            author_provenance=dict(worker_id="synthetic-bundle",model_id=member["model_id"]),
            blindness_attestation=dict(other_submissions_read=False,this_design_outcomes_read=False,this_design_estimates_read=False,outside_slot_pack_read=True))
            for persona,member in job.contract["bundle_members"].items()}
        return dict(design_id=job.design_id,spec_sha256=job.contract["spec_sha256"],members=members)
    if job_type(job)=="s0_direction":
        return dict(design_id=job.design_id,role=job.role,coder_id="fixture-worker",recorded_at="2026-10-12T00:00:00Z",target_outcome=job.contract["target_outcome"],purpose_direction=-1,
            citations=[dict(source_ref="https://example.invalid/statute",locator="section 1",intended_change="reduce target",evidence_kind="statute_purpose")],
            blindness_attestation={k:False for k in ("outcome_values_read","outcome_associations_read","forecasts_read","literature_effect_signs_read","other_codings_read")})
    fixture = json.loads(FIXTURES.read_text())[_fixture_key(job_type(job))]
    value = _render(fixture["golden"], _bindings(job))
    if job.stage == "S5" and job.contract.get("source_type") == "published_did_study":
        value["published_estimate_known"] = False
    if job.contract.get('forecast_slot'):
        value['author_provenance']=dict(worker_id='synthetic-'+job.role,model_id=job.contract['forecast_slot']['model_id'])
        value['blindness_attestation']=dict(other_submissions_read=False,this_design_outcomes_read=False,this_design_estimates_read=False,outside_slot_pack_read=False)
    return value


def _shape(schema, value, location="output"):
    """Validate the deliberately small JSON Schema subset declared by templates."""
    supported = {"type", "required", "properties", "items", "enum", "const", "oneOf",
                 "additionalProperties", "minItems", "maxItems"}
    if set(schema) - supported: raise ValueError("Unsupported template schema keyword")
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try: _shape(option, value, location)
            except ValueError: continue
            matches += 1
        if matches != 1: raise ValueError(location + " must match exactly one template output shape")
    types = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool,
             "null": value is None}
    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(types.get(item, False) for item in wanted): raise ValueError(location + " template type mismatch")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(location + " template constant mismatch")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise ValueError(location + " template enum mismatch")
    if isinstance(value, dict):
        if not set(schema.get("required", [])).issubset(value): raise ValueError(location + " template required field missing")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(location + " unexpected template field")
        for key, child in props.items():
            if key in value: _shape(child, value[key], location + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(location + " template array size mismatch")
        for index, item in enumerate(value):
            if "items" in schema: _shape(schema["items"], item, f"{location}[{index}]")


def validate_template(job, value):
    text = Path(job.template_path).read_text()
    if text.count(SCHEMA_MARKER) != 1: raise ValueError("Template lacks one machine-readable output contract")
    schema = json.loads(text.split(SCHEMA_MARKER, 1)[1].split("\n```", 1)[0])
    _shape(schema, value)


def _edit(value, mutation):
    parent = value
    for component in mutation["path"][:-1]: parent = parent[component]
    final = mutation["path"][-1]
    if mutation.get("delete"): del parent[final]
    else: parent[final] = copy.deepcopy(mutation["value"])


def run_contract(kind):
    """Run current emitter/template and real collector; return a reviewable report."""
    if kind not in JOB_TYPES: raise ValueError("Unknown contract job type: " + kind)
    fixture = json.loads(FIXTURES.read_text())[_fixture_key(kind)]
    tested = []
    # Every output gets a fresh job and promotion directory. Rejection cannot
    # be caused by a prior valid promotion's immutable-write collision.
    for mutation in [None, *fixture["invalid"]]:
        name = "golden" if mutation is None else mutation["name"]
        with tempfile.TemporaryDirectory(prefix="l2-contract-") as tmp:
            job = emit_fixture_job(Path(tmp).resolve(), kind)
            if job_type(job) != kind: raise ValueError("Emitter selected a different output contract")
            value = golden_output(job)
            validate_template(job, value)
            if mutation is not None: _edit(value, mutation)
            path = Path(job.output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, allow_nan=False))
            try:
                receipt = collect_job(job.job_path)
            except ValueError as exc:
                if mutation is None: raise ValueError(f"{kind}: golden rejected: {exc}") from exc
                if (Path(job.output_dir) / "COLLECTION.json").exists():
                    raise ValueError(f"{kind}: invalid output produced a collection receipt") from exc
                if Path(job.promote_path) != path and Path(job.promote_path).exists():
                    raise ValueError(f"{kind}: invalid output was partially promoted") from exc
                tested.append(name)
            else:
                if mutation is not None: raise ValueError(f"{kind}: invalid output accepted: {name}")
                if receipt.get("validated") is not True or receipt.get("execution_authorized") is not False:
                    raise ValueError(f"{kind}: golden lacks validated non-authorizing receipt")
                tested.append(name)
    return {"job_type": kind, "status": "PASS", "cases": tested, "model_calls": 0}


def check_job_types(jobs):
    """Fail closed before launch if any requested current job contract fails.

    No success cache: a template, collector, or fixture change is rechecked at
    each wave. Hash validation of actual jobs remains load_job's responsibility.
    """
    kinds = sorted({job_type(job) for job in jobs})
    reports = []
    for kind in kinds:
        try: reports.append(run_contract(kind))
        except Exception as exc:
            raise ValueError(f"Contract preflight failed for {kind}: {exc}") from exc
    # Also check the exact archived template the wave will receive. A job type's
    # current contract passing does not excuse an incompatible older prompt.
    for job in jobs:
        from ..history_packet import check
        check(job)
        try: validate_template(job, golden_output(job))
        except Exception as exc:
            raise ValueError(f"Contract preflight failed for {job_type(job)} template: {exc}") from exc
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-type", choices=JOB_TYPES, action="append")
    args = parser.parse_args(argv)
    reports = []
    for kind in args.job_type or JOB_TYPES:
        try: reports.append(run_contract(kind))
        except Exception as exc: reports.append({"job_type": kind, "status": "FAIL", "reason": str(exc)})
    print(json.dumps(reports, indent=2))
    return 0 if all(report["status"] == "PASS" for report in reports) else 1
