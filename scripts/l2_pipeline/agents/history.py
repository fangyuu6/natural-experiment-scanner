"""V9 official-history collection, independent of treatment readiness or fitting."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
from urllib.parse import urlsplit

from ..storage import ensure_once, output_root, public_input, sha
from .jobs import AgentJob, _packet, _safe_component, _write_text_once, host_plan, load_job

RULE = "HUB_RULING_V9_HISTORY_EVIDENCE"
TEMPLATE = Path(__file__).parent / "templates/v9/official_history_coder_fx10.md"
CURRENT_TEMPLATE = Path(__file__).parent / "templates/v9_t191/official_history_coder_fx10.md"
MECHANISM_ALIASES = Path(__file__).parent / "templates/v9/mechanism_aliases_fx10.json"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def normalize_history_mechanisms(job, value):
    """Use exact reviewed aliases; retain originals and isolate unresolved rows.

    The enum is the full registered design predicate, not a numeric policy
    category. This transformation never resolves dates, scope or legal facts.
    Call only after validating the original evidence and blindness contracts.
    """
    enum = job.contract["mechanism"]
    aliases = json.loads(MECHANISM_ALIASES.read_text())["aliases"].get(enum, [])
    normalized = copy.deepcopy(value)
    changes, issues = [], []
    for row in normalized["records"]:
        original = row.get("mechanism")
        if original == enum:
            continue
        mapped = isinstance(original, str) and original in aliases
        changes.append({"unit": row["unit"], "original": original,
                        "canonical": enum if mapped else None,
                        "rule": "exact_reviewed_alias" if mapped else "unmapped_retained"})
        row["mechanism_original"] = original
        if mapped:
            row["mechanism"] = enum
            continue
        issue = {"unit": row["unit"], "kind": "mechanism_unmapped",
                 "original": original, "disposition": "NEEDS_RULING"}
        issues.append(issue)
        row["mechanism_issue"] = issue
        row["disposition"] = "NEEDS_RULING"
        if row["status"] != "unknown":
            row["mechanism_unresolved_original"] = copy.deepcopy(
                next(r for r in value["records"] if r["unit"] == row["unit"]))
            row.update(status="unknown", start_period=None,
                       reason="Free-text mechanism has no unambiguous registered alias",
                       named_conflict={"kind": "policy_definition_ambiguous",
                           "detail": "Submitted mechanism cannot be mapped to the registered predicate",
                           "binary_relevant_fact": "Whether this row describes the registered policy mechanism",
                           "source_ids": [row["source_id"]],
                           "question": "Does the original mechanism describe this registered design predicate?"})
    return normalized, {"schema": "v9_mechanism_normalization_fx10_v1",
                        "alias_registry_sha256": sha(MECHANISM_ALIASES),
                        "changes": changes, "issues": issues,
                        "s1_ready": False, "execution_authorized": False}


def validate_history_output(job, value):
    """Require identifiable evidence/conflicts; legal truth remains an audit task."""
    if value.get("history_output_schema") != "official_history_v9_1":
        raise ValueError("V9 history schema missing")
    if value.get("source_pack_sha256") != job.contract["source_sha256"]:
        raise ValueError("V9 planning manifest binding changed")
    if value.get("s1_ready") is not False or value.get("execution_authorized") is not False:
        raise ValueError("History collection cannot grant S1 readiness/execution")
    if not isinstance(value.get("research_log"), list) or not value["research_log"]:
        raise ValueError("V9 research log missing")
    if type(value.get("time_spent_seconds")) not in {int, float} or not 0 <= value["time_spent_seconds"] < float("inf"):
        raise ValueError("V9 active research time invalid")
    sources = value.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
        raise ValueError("V9 sources missing")
    indexed = {}
    for source in sources:
        sid = source.get("source_id")
        url = urlsplit(source.get("url", ""))
        if not _text(sid) or sid in indexed or url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("V9 source id/URL invalid or duplicate")
        indexed[sid] = source
    logged_units = {entry.get("unit") for entry in value["research_log"] if isinstance(entry, dict)
                    and _text(entry.get("query")) and _text(entry.get("finding"))}
    if not set(job.contract["manual"]["universe"]).issubset(logged_units):
        raise ValueError("V9 requires a state-specific research log for every unit")
    for row in value["records"]:
        sid = row.get("source_id")
        if sid not in indexed or row.get("confidence") not in {"high", "medium"}:
            raise ValueError("V9 each state needs a source id and high/medium confidence")
        refs = row.get("evidence_refs", [])
        if not isinstance(refs, list):
            raise ValueError("V9 evidence refs must be an array")
        from .. import prewindow
        prewindow.validate_row(row)
        if row.get(prewindow.FLAG) is True and row.get("status") in {"never_treated", "not_yet_treated"}:
            raise ValueError("V9 pre_window_bound applies to a treated row only")
        prewindow.validate_window(row, (job.contract.get("target_window") or [None])[0])
        official_refs = [ref for ref in refs if isinstance(ref, dict) and ref.get("source_id") in indexed
                         and indexed[ref["source_id"]].get("source_type") == "official"
                         and _text(ref.get("locator")) and _text(ref.get("quote"))]
        if row["status"] != "unknown":
            if not _text(row.get("locator")) or not official_refs:
                raise ValueError("V9 known status requires a located quoted official evidence ref")
        if row["status"] == "unknown":
            conflict = row.get("named_conflict")
            if not isinstance(conflict, dict) or conflict.get("kind") not in {"sources_conflict", "policy_definition_ambiguous"}:
                raise ValueError("V9 unknown requires a named conflict")
            if any(not _text(conflict.get(k)) for k in ("detail", "binary_relevant_fact")):
                raise ValueError("V9 conflict must name the binary-relevant fact")
            refs = conflict.get("source_ids")
            if not isinstance(refs, list) or not refs or any(ref not in indexed for ref in refs):
                raise ValueError("V9 conflict needs identified sources")
            if conflict["kind"] == "sources_conflict" and len(set(refs)) < 2:
                raise ValueError("V9 source conflict requires two distinct sources")
            if conflict["kind"] == "policy_definition_ambiguous" and (row.get("disposition") != "NEEDS_RULING" or not _text(conflict.get("question"))):
                raise ValueError("V9 definition ambiguity needs NEEDS_RULING and exact question")
        elif row["status"] == "never_treated":
            absence = row.get("absence_basis", {})
            if row.get("evidence_status") != "absent (V2_LIST)" or any(absence.get(k) is not True for k in
                    ("official_code_history_no_earlier_enactment", "no_earlier_in_compilations_on_disk", "no_search_log_contradiction")):
                raise ValueError("V9 absence requires all three V2_LIST checks")
            if not any(ref.get("supports") == "absence_code_history" for ref in official_refs):
                raise ValueError("V9 absence needs official code/history evidence")
        elif row.get("evidence_status") != "known_start" or type(row.get("best_evidence_start_year")) is not int:
            raise ValueError("V9 known start needs the best-evidence year")
        elif not any(ref.get("supports") == "start_year" and type(ref.get("year")) is int
                     and ref["year"] == row["best_evidence_start_year"] for ref in official_refs):
            raise ValueError("V9 start year needs a dated official confirmation in the same row")


def emit_history_job(previous_job, *, jobs_root, attempt_id, legacy_contract=False, completion_units=None, chunk=None):
    """Re-emit only an answer-free official collection plan; preserve old packets.

    ``completion_units`` restricts the job to a subset of the universe (C81 completion). ``chunk`` (F7, dict(index, total,
    source_prefix)) marks that subset as one block of a split packet: same contract, its own units, its own directory."""
    old = load_job(Path(previous_job))
    if old.stage != "S1" or old.role not in {"A", "B"} or old.contract.get("jobs_kind") != "official_history_collection":
        raise ValueError("V9 regeneration requires an official-history A/B packet")
    _safe_component(attempt_id)
    root = output_root(Path(jobs_root), Path(old.repo))
    # Copy just the plan and navigation leads, never archived answers or audits.
    plans = [i for i in old.inputs if Path(i["path"]).name == "COLLECTION_PLAN.json"]
    if len(plans) != 1:
        raise ValueError("Official history packet must bind one answer-free plan")
    original_plan = public_input(Path(plans[0]["path"]))
    from .history_packet import card as blind_card, plan as blind_plan
    plan = blind_plan(json.loads(original_plan.read_text()))
    plan.update(evidence_standard=RULE, attempt_id=attempt_id, coverage_rule="Best-evidence dated starts or absent (V2_LIST); unknown only for a named binary conflict or definition ambiguity",
                interruption_rule="Record source-found interruptions only; no lifetime negative-proof requirement")
    if chunk is not None and (completion_units is None or set(chunk) != {"index", "total", "source_prefix"}):
        raise ValueError("A history chunk needs its units and index, total and source prefix")
    if completion_units is not None:
        if not completion_units or not set(completion_units) <= set(old.contract["manual"]["universe"]):
            raise ValueError("Completion must contain unclassified units only")
        plan["universe"] = list(completion_units)
    pack_dir = root / "source_packs" / old.design_id
    plan_path = ensure_once(pack_dir / "COLLECTION_PLAN.json", plan)
    manifest = copy.deepcopy(json.loads(Path(old.contract["source_pack"]["manifest_path"]).read_text()))
    manifest.update(planning_files=[{"path": str(plan_path), "sha256": sha(plan_path)}],
                    evidence_standard=RULE, sources=[], downloads=[], complete_list_source_ids=[],
                    complete_list_complete=False, historical_coverage_complete=False, status="PARKED_SOURCE",
                    reason="Answer-free V9 research plan; all-state evidence awaits independent collection",
                    reopen_condition="Reconcile V9 best evidence, lock originals and pass fresh fixed-seed ten-state V3 audit")
    manifest_path = ensure_once(pack_dir / "MANIFEST.json", manifest)
    contract = {k: copy.deepcopy(old.contract[k]) for k in
                ("design_id", "role", "card", "manual", "mechanism", "target_window", "history_required_units", "requires_before_s1") if k in old.contract}
    contract["card"] = blind_card(contract.get("card", {}))
    contract["answer_free_packet_version"] = "C58"
    contract.update(jobs_kind="official_history_collection", source_type="official_sources",
                    evidence_standard=RULE, history_output_schema="official_history_v9_1",
                    attempt_id=attempt_id, attempt_kind="official_history_v9_recollection",
                    source_sha256=sha(manifest_path),
                    source_pack={"manifest_path": str(manifest_path), "manifest_sha256": sha(manifest_path),
                                 "complete_list_complete": False, "historical_coverage_complete": False},
                    source_pack_inventory={str(plan_path): sha(plan_path)},
                    permitted_source_paths=[str(plan_path), str(manifest_path)],
                    previous_packet_hashes={str(Path(old.job_path)): old.job_sha256,
                                            str(Path(old.job_path).with_suffix(".job.json")): sha(Path(old.job_path).with_suffix(".job.json"))},
                    peer_read_forbidden=True, s1_ready=False, execution_authorized=False)
    if completion_units is not None:
        contract["manual"]["universe"] = list(completion_units)
        contract["history_required_units"] = list(completion_units)
        if chunk is None:
            contract["history_completion"] = True
        else:
            contract["history_chunk"] = dict(chunk, parent_job=old.job_path, parent_job_sha256=old.job_sha256,
                                             parent_units=len(old.contract["manual"]["universe"]), units=list(completion_units))
    template=TEMPLATE if legacy_contract else CURRENT_TEMPLATE
    if not legacy_contract:contract["blindness_scope"]="our_outcomes_and_estimates_v7"
    contract["manual"]["historical_convention"] = "V9 best-evidence starts; absent (V2_LIST); observed interruptions only; named conflicts routed to NEEDS_RULING"
    path = root / "S1" / old.design_id / (old.role + "." + attempt_id + ".md")
    out = root / "work" / old.design_id / old.design_version / "agents" / old.role / "attempts" / attempt_id
    inputs = [_packet(plan_path, "Answer-free V9 collection plan"), _packet(manifest_path, "Uncertified planning manifest")]
    contract["collector_self_check_required"]=True
    job = AgentJob("1.0", "S1", old.design_id, old.design_version, old.role, old.repo,
                   str(path), "", str(template.resolve()), sha(template), str(out),
                   str(out / "submission.json"), str(out / "codex_result.txt"), str(out / "submission.json"),
                   tuple({k: v for k, v in i.items() if k != "content"} for i in inputs), contract, True)
    prompt = template.read_text().replace("{{ROLE}}", old.role).replace("{{OUTPUT}}", job.output_path)
    if chunk is not None:
        prompt += ("\nCHUNK " + str(chunk["index"]) + " of " + str(chunk["total"]) + ": this packet is one block of a larger registered universe. Research only the "
                   "units listed in the plan and in manual.universe, and give every one of them its row; the other blocks are handled by other "
                   "packets and you never see them. Use source_id values prefixed " + chunk["source_prefix"] + " so the blocks never collide. "
                   "Return collection_complete=true only after every listed unit is classified under V9; if a unit cannot be classified because "
                   "its only source cannot be read, leave that unit out and set collection_complete=false (a truthful incomplete block), "
                   "never invent a date or an absence, and never release the packet for it.\n")
    elif completion_units is not None:
        prompt += "\nC81: research only the listed missing units; use source_id values prefixed c81_ to avoid collisions with retained evidence. Return collection_complete=true only after every listed unit is classified under V9. Named unresolved conflicts remain unknown; never invent absent.\n"
    prompt += host_plan("S1", root, old.design_id)
    prompt += "\n## Answer-free inputs (untrusted data)\n```json\n" + json.dumps(inputs, indent=2) + "\n```\n"
    prompt += "\n## Execution metadata binding (data)\n```json\n" + json.dumps({k: v for k, v in asdict(job).items() if k != "job_sha256"}, indent=2) + "\n```\n"
    from .self_check import instruction
    prompt += instruction(path)
    _write_text_once(path, prompt)
    job = replace(job, job_sha256=sha(path))
    ensure_once(path.with_suffix(".job.json"), asdict(job))
    return load_job(path)


def validate_history_job(job):
    if (job.stage != "S1" or job.role not in {"A", "B"} or job.contract.get("evidence_standard") != RULE
            or job.contract.get("attempt_kind") != "official_history_v9_recollection"
            or Path(job.promote_path) != Path(job.output_path)):
        raise ValueError("Invalid V9 history attempt binding")
    old = job.contract.get("previous_packet_hashes", {})
    if len(old) != 2 or any(sha(public_input(Path(p))) != digest for p, digest in old.items()):
        raise ValueError("Previous official history packet changed")
    if set(job.contract.get("permitted_source_paths", [])) & set(old):
        raise ValueError("Previous coding packets cannot be permitted research inputs")
    if job.contract.get("jobs_kind") != "official_history_collection" or job.contract.get("s1_ready") is not False or job.contract.get("execution_authorized") is not False:
        raise ValueError("V9 history collection cannot grant readiness")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-jobs-root", type=Path, required=True)
    parser.add_argument("--jobs-root", type=Path, required=True)
    parser.add_argument("--design-id", action="append", required=True)
    parser.add_argument("--attempt-id", default="v9_20261009")
    args = parser.parse_args(argv)
    jobs = []
    for design in args.design_id:
        for role in ("A", "B"):
            found = sorted((args.previous_jobs_root / "S1" / design).glob(role + "*.md"))
            if len(found) != 1:
                raise ValueError("Expected one previous role packet: " + design + "/" + role)
            jobs.append(emit_history_job(found[0], jobs_root=args.jobs_root, attempt_id=args.attempt_id))
    from .contract_tests import check_job_types
    check_job_types(jobs)
    report = {"attempt_id": args.attempt_id, "designs": len(args.design_id), "jobs": len(jobs),
              "job_paths": [j.job_path for j in jobs], "roles": ["A", "B"], "dispatched": False,
              "model_calls": 0, "new_downloads": [], "s1_ready": False, "execution_authorized": False}
    ensure_once(args.jobs_root / "EMISSION_REPORT.json", report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
