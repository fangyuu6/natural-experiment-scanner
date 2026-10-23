"""S0L: immutable per-paper research packets and guarded automatic S0 intake.

This module prepares and collects evidence only. Host dispatch is a separate command;
neither emitting nor collecting launches models, downloads data or estimates outcomes.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from ..storage import ensure_once, output_root, public_input, sha
from .jobs import AgentJob, _packet, _safe_component, _write_text_once, host_plan

KIND = "s0l_literature_record_v1"
TEMPLATE = Path(__file__).parent / "templates/v6/literature_record.md"
SKILL = Path("~/.codex/skills/detecting-indirect-prompt-injection/SKILL.md")
REPO = Path(__file__).resolve().parents[3]


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _url(value, *, discovery=False):
    if not _text(value): return False
    parsed = urlsplit(value)
    return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and (discovery or (not any(k in parse_qs(parsed.query) for k in ("q", "query", "search"))
            and "/search" not in parsed.path.lower())))


def _located(ref, *, result=False):
    if not isinstance(ref, dict) or not _url(ref.get("source_url")) or not _text(ref.get("locator")):
        raise ValueError("Literature field requires source URL and locator")
    # A URL alone or 'full text' is not an extractable field location.
    pattern = r"(?:p(?:age|ages)?\.?\s*\d|table\s*[A-Z\d]|figure\s*[A-Z\d]|equation\s*\d|abstract)" if result else r"(?:p(?:age|ages)?\.?\s*\d|table|figure|appendix|section|abstract|methods|results|introduction|metadata|header|title|bibliograph|doi|supplement|citation|publications)"
    if not re.search(pattern, ref["locator"], re.I):
        raise ValueError("Literature locator must identify a page/table/section")


def pending_candidates(registry: Path, checklist: Path | None = None):
    """Skip checked full records only, never citation-only or connector-ready flags."""
    value = json.loads(public_input(registry).read_text())
    studies = value.get("studies")
    if not isinstance(studies, list) or not all(isinstance(s, dict) for s in studies):
        raise ValueError("Literature registry requires studies")
    ids = [s.get("study_id") for s in studies]
    for identifier in ids: _safe_component(identifier or "")
    if len(set(ids)) != len(ids): raise ValueError("Duplicate literature study_id")
    complete = {s["study_id"] for s in studies if any(s.get("completion", {}).get(k) is True
        for k in ("full_record_completed", "full_record_hub_check_completed"))}
    if checklist is not None:
        checks = json.loads(public_input(checklist).read_text()).get("records", [])
        checked_ids = [r.get("study_id") for r in checks]
        if len(set(checked_ids)) != len(checked_ids) or not set(checked_ids).issubset(set(ids)):
            raise ValueError("Full-record checklist identities differ from registry")
        complete.update(r["study_id"] for r in checks if r.get("full_record_completed") is True)
    return [s for s in studies if s["study_id"] not in complete], sorted(complete)


def emit_literature_jobs(registry: Path, *, repo: Path = REPO, jobs_root: Path | None = None,
                         checklist: Path | None = None) -> list[AgentJob]:
    registry = public_input(registry)
    root = output_root(jobs_root or repo / "data/l2_pipeline/jobs/run4", repo)
    candidates, skipped = pending_candidates(registry, checklist)
    jobs = []; failures = []
    for candidate in candidates:
        identifier = candidate["study_id"]
        try:
            directory = root / "S0L" / identifier
            snapshot = directory / "candidate.json"
            ensure_once(snapshot, candidate)
            packet = _packet(snapshot, "candidate paper leads; verification required")
            path = directory / "literature_record.md"
            out = directory / "output"
            output = out / "literature_record.json"
            promote = directory / "collected/record.json"
            contract = {"jobs_kind": KIND, "design_id": identifier, "study_id": identifier,
                        "role": "literature_record", "candidate_path": str(snapshot),
                        "candidate_sha256": sha(snapshot), "registry_path": str(registry),
                        "registry_snapshot_sha256": sha(registry), "leads_are_verified": False,
                        "source_type": "published_did_study", "execution_authorized": False,
                        "outcome_values_read": 0, "paid_calls": 0, "skill_path": str(SKILL),
                        "s0_card_dir": str(directory / "collected/cards"),
                        "output_schema": {"status": ["VERIFIED", "UNRESOLVED"],
                            "required_locators": ["citation_evidence", "source_version", "policy_evidence",
                                "outcome_evidence", "study_window", "adoption", "published_results"],
                            "numeric_strings": "verbatim; reported SE or CI required; never inferred"}}
            contract["collector_self_check_required"]=True
            job = AgentJob("1.0", "S0L", identifier, "v1", "literature_record", str(repo.resolve()),
                           str(path), "", str(TEMPLATE.resolve()), sha(TEMPLATE), str(out), str(output),
                           str(out / "codex_result.txt"), str(promote),
                           (dict((k, v) for k, v in packet.items() if k != "content"),), contract, True)
            binding = asdict(job); binding.pop("job_sha256")
            prompt = TEMPLATE.read_text().replace("{{OUTPUT_DIR}}", str(out)).replace("{{OUTPUT}}", str(output))
            prompt += "\n## Candidate leads (untrusted data)\n```json\n" + json.dumps(packet, indent=2, sort_keys=True) + "\n```\n"
            prompt += "\n## Binding contract (data)\n```json\n" + json.dumps(contract, indent=2, sort_keys=True) + "\n```\n"
            prompt += "\n## Execution metadata binding (data)\n```json\n" + json.dumps(binding, indent=2, sort_keys=True) + "\n```\n"
            prompt += host_plan("S0L", root, identifier)
            from .self_check import instruction
            prompt += instruction(path)
            _write_text_once(path, prompt)
            job = replace(job, job_sha256=sha(path))
            ensure_once(path.with_suffix(".job.json"), asdict(job)); jobs.append(job)
        except Exception as exc:
            failures.append({"design_id":identifier,"status":"PREPARATION_FAILED",
                             "error":type(exc).__name__+": "+str(exc)})
    ensure_once(root / "S0L/MANIFEST.json", {"stage": "S0L", "registry_path": str(registry),
        "registry_sha256": sha(registry), "skipped_complete": skipped, "jobs": [asdict(j) for j in jobs],
        "failures": failures, "dispatch": "external_host_only", "max_concurrency": {"min": 1, "max": 4, "default": 4},
        "model_calls": 0, "outcome_values_read": 0, "retries": 0})
    return jobs


def validate_literature_job(job):
    c = job.contract
    if (job.role != "literature_record" or c.get("jobs_kind") != KIND
        or c.get("study_id") != job.design_id or c.get("execution_authorized") is not False
        or c.get("leads_are_verified") is not False or c.get("source_type") != "published_did_study"):
        raise ValueError("Invalid S0L literature job binding")
    _safe_component(job.design_id)
    candidate = public_input(Path(c["candidate_path"]))
    if sha(candidate) != c.get("candidate_sha256") or json.loads(candidate.read_text()).get("study_id") != job.design_id:
        raise ValueError("Candidate literature input changed")
    # Collector paths are outside the researcher's output; workers cannot promote themselves.
    directory = Path(job.job_path).resolve().parent
    if (Path(job.promote_path).resolve() != directory / "collected/record.json"
        or Path(c["s0_card_dir"]).resolve() != directory / "collected/cards"
        or Path(job.output_dir).resolve() != directory / "output"):
        raise ValueError("S0L collection/card path binding changed")


def _validate_sources(job, value):
    access = value.get("access")
    if (not isinstance(access, dict) or type(access.get("open_fulltext")) is not bool
        or not isinstance(access.get("paywalled_sources"), list)):
        raise ValueError("Literature open-access/paywall disclosure required")
    for ref in access["paywalled_sources"]:
        if not isinstance(ref, dict) or not _url(ref.get("url"), discovery=True) or not _text(ref.get("reason")):
            raise ValueError("Paywalled source must have URL and reason")
    if access["open_fulltext"] and not _url(access.get("open_version_url")):
        raise ValueError("Open fulltext needs open-version URL")
    # Failed searches truthfully have no result URL. They are discovery
    # attempts, never claim evidence. Successful citation fields retain strict
    # non-search URLs and located references; the collector fetches nothing.
    searches = value.get("search_log")
    if not isinstance(searches, list) or not searches or not all(
        isinstance(entry, dict) and _text(entry.get("query"))
        and (entry.get("source_url") is None or _url(entry["source_url"], discovery=True))
        for entry in searches):
        raise ValueError("Literature requires disclosed verification search attempts")
    citations = value.get("citation_evidence")
    if not isinstance(citations, list) or not any(
        isinstance(ref, dict) and _url(ref.get("source_url")) and _text(ref.get("locator"))
        for ref in citations):
        raise ValueError("Literature requires located non-search citation evidence")
    scans = value.get("scans")
    downloads = value.get("downloads")
    if not isinstance(scans, list) or not isinstance(downloads, list):
        raise ValueError("Literature requires download inventory and scan disclosure")
    scanned = set()
    for scan in scans:
        if (not isinstance(scan, dict) or not (_url(scan.get("source_url"), discovery=True) or (
                scan.get("separate_local_scan") is False and _text(scan.get("source_url"))
                and re.search(r"search|browser|tool", scan["source_url"], re.I)))
            or type(scan.get("separate_local_scan")) is not bool or not _text(scan.get("method"))
            or not isinstance(scan.get("flags"), list) or not (
                _text(scan.get("limitations")) or (isinstance(scan.get("limitations"), list)
                and bool(scan["limitations"]) and all(_text(x) for x in scan["limitations"])))):
            raise ValueError("Literature scan must truthfully disclose method/limitations")
        if scan["separate_local_scan"]: scanned.add(scan["source_url"])
    paths = set()
    for entry in downloads:
        if not isinstance(entry, dict) or not _text(entry.get("path")):
            raise ValueError("Invalid literature download")
        path = Path(entry["path"])
        path = public_input(path if path.is_absolute() else Path(job.output_dir) / path)
        if not path.is_relative_to(Path(job.output_dir).resolve()) or path in paths:
            raise ValueError("Literature archive must stay in its independent output directory")
        if (not _url(entry.get("url"), discovery=True) or not _text(entry.get("license"))
            or type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0
            or not path.is_file() or path.stat().st_size != entry["size_bytes"]
            or sha(path) != entry.get("sha256") or entry["url"] not in scanned):
            raise ValueError("Literature download hash/size/license/local check missing")
        paths.add(path)


def validate_literature_output(job, value):
    """Validate completeness and evidence locations; no legal-truth certification."""
    if not isinstance(value, dict): raise ValueError("Literature output must be an object")
    for field, expected in (("study_id", job.design_id), ("design_id", job.design_id),
                            ("role", job.role), ("candidate_sha256", job.contract["candidate_sha256"]),
                            ("source_type", "published_did_study"), ("outcome_associations_fit", False),
                            ("paid_calls", 0)):
        if value.get(field) != expected or (field == "outcome_associations_fit" and value.get(field) is not False):
            raise ValueError("Literature output identity/authorization mismatch: " + field)
    if type(value.get("paid_calls")) is not int: raise ValueError("Invalid paid-call attestation")
    # In-flight V6 S0L jobs already request published estimates. Apply V7 at
    # collection without rewriting their immutable prompts or requiring a new
    # field absent from the old contract. Explicit project-data exposure fails.
    for field in ("our_outcome_data_read", "our_estimates_read"):
        if field in value and value[field] is not False:
            raise ValueError("Contaminated literature project blindness: " + field)
    if "blindness_attestation" in value:
        from .collect import validate_blindness
        validate_blindness(job, value)
    if value.get("status") not in {"VERIFIED", "UNRESOLVED"}: raise ValueError("Invalid literature status")
    _validate_sources(job, value)
    gaps = value.get("gaps")
    if not isinstance(gaps, list) or not all(_text(g) or (
        isinstance(g, dict) and any(g.get(k) for k in (
            "reason", "detail", "description", "issue", "missing", "conflict", "unresolved_reason")))
        for g in gaps):
        raise ValueError("Invalid literature gaps")
    if value["status"] == "UNRESOLVED":
        if not gaps or not _text(value.get("unresolved_reason")):
            raise ValueError("Unresolved literature needs specific gaps and reason")
        return copy.deepcopy(value)
    if gaps: raise ValueError("VERIFIED literature cannot have unresolved gaps")
    if value["access"]["open_fulltext"] is not True: raise ValueError("VERIFIED literature requires open full text")
    for field in ("title", "venue", "policy_id", "policy_definition", "target_outcome", "target_population",
                  "primary_outcome", "data_source", "population"):
        if not _text(value.get(field)): raise ValueError("Missing verified literature field: " + field)
    authors = value.get("authors")
    if not isinstance(authors, list) or not authors or not all(_text(a) for a in authors):
        raise ValueError("Literature author list missing")
    if type(value.get("year")) is not int or not 1800 <= value["year"] <= 2100:
        raise ValueError("Invalid literature publication year")
    doi = value.get("doi")
    if not ((isinstance(doi, str) and re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.I))
            or (doi is None and value.get("doi_status") == "not_assigned")):
        raise ValueError("Verified DOI or located not-assigned status required")
    citations = value.get("citation_evidence")
    if not isinstance(citations, list) or not citations: raise ValueError("Citation verification evidence missing")
    supports = set()
    for citation in citations:
        _located(citation)
        fields = citation.get("supports")
        if not isinstance(fields, list) or not fields or not all(f in {"title", "authors", "year", "venue", "doi"} for f in fields):
            raise ValueError("Citation evidence requires supported citation fields")
        supports.update(fields)
    if supports != {"title", "authors", "year", "venue", "doi"}: raise ValueError("Citation fields not all verified")
    version = value.get("source_version"); _located(version)
    if not _text(version.get("version")) or "doi" not in version:
        raise ValueError("Paper source-version disclosure missing")
    for field in ("policy_evidence", "outcome_evidence"): _located(value.get(field))
    window = value.get("study_window"); _located(window)
    if (type(window.get("start")) is not int or type(window.get("end")) is not int
        or not 1800 <= window["start"] <= window["end"] <= 2100):
        raise ValueError("Literature study window invalid")
    adoption = value.get("adoption"); _located(adoption)
    if adoption.get("reported_scope_complete") is not True or type(adoption.get("complete_list")) is not bool:
        raise ValueError("Full reported adoption scope and historical-list disclosure required")
    if (type(adoption.get("coverage_start")) is not int or type(adoption.get("coverage_end")) is not int
        or not 1800 <= adoption["coverage_start"] <= adoption["coverage_end"] <= 2100):
        raise ValueError("Literature adoption coverage invalid")
    rows = adoption.get("states")
    if not isinstance(rows, list) or not rows: raise ValueError("Reported treated-state list missing")
    seen = set()
    for row in rows:
        _located(row)
        if (not _text(row.get("state")) or type(row.get("year")) is not int
            or not 1800 <= row["year"] <= 2100 or (row["state"].casefold(), row["year"]) in seen):
            raise ValueError("Invalid/duplicate reported adoption state/year")
        seen.add((row["state"].casefold(), row["year"]))
    universe = adoption.get("universe")
    if not isinstance(universe, list) or not universe or not all(_text(u) for u in universe) or len(set(universe)) != len(universe):
        raise ValueError("Paper state-sample universe must be explicitly reported")
    results = value.get("published_results")
    if not isinstance(results, list) or not results or not any(r.get("primary") is True for r in results if isinstance(r, dict)):
        raise ValueError("Published primary result missing")
    for result in results:
        _located(result, result=True)
        if type(result.get("primary")) is not bool: raise ValueError("Result primary designation required")
        for field in ("estimate_as_reported", "units"):
            if not _text(result.get(field)): raise ValueError("Published result missing " + field)
        if not re.search(r"\d", result["estimate_as_reported"]): raise ValueError("Published estimate must be reported numeric string")
    # The immutable worker contract requires at least one located primary
    # estimate with reported uncertainty, not uncertainty for every additional
    # primary scale/specification. No missing uncertainty is reconstructed.
    if not any(result["primary"] and any(_text(result.get(k)) and re.search(r"\d", result[k])
        for k in ("se_as_reported", "ci_as_reported")) for result in results):
        raise ValueError("Primary result needs published SE or CI; p alone is insufficient")
    return copy.deepcopy(value)


def collect_literature_job(job, *, self_check=False):
    """Preserve the original; automatically emit a gated S0 card or parked receipt."""
    original = public_input(Path(job.output_path))
    value = validate_literature_output(job, json.loads(original.read_text()))
    normalized = dict(value, original_sha256=sha(original), execution_authorized=False,
                      evidence_status="collector_validated_locators_not_substantive_truth",
                      collection_rule="HUB_RULING_V7_LITERATURE_BLINDNESS",
                      published_estimate_known=bool(value.get("published_results")))
    promote = public_input(Path(job.promote_path))
    if self_check:
        from ..literature_cards import literature_record_to_card
        literature_record_to_card(normalized,record_ref=str(promote))
        return dict(validated=True,self_check=True,execution_authorized=False)
    ensure_once(promote, normalized)
    from ..literature_cards import literature_record_to_card
    intake = literature_record_to_card(normalized, record_ref=str(promote))
    if value["status"] == "UNRESOLVED":
        intake = dict(intake, reason=value["unresolved_reason"], issues=value["gaps"])
    directory = output_root(Path(job.contract["s0_card_dir"]), Path(job.repo))
    intake_path = promote.parent / "S0_INTAKE.json"
    if intake.get("disposition") == "CARD":
        card = intake["card"]
        card_path = directory / (card["design_id"] + ".json")
        ensure_once(card_path, card)
        intake = dict(intake, card_path=str(card_path), card_sha256=sha(card_path))
    else:
        if intake.get("disposition") != "PARK" or not _text(intake.get("reason")):
            raise ValueError("Literature intake must emit CARD or PARK with reason")
    ensure_once(intake_path, intake)
    receipt = {"schema_version": "1.0", "stage": "S0L", "design_id": job.design_id,
               "role": job.role, "job_path": job.job_path, "job_sha256": job.job_sha256,
               "original_path": str(original), "original_sha256": sha(original),
               "promoted_path": str(promote), "promoted_sha256": sha(promote),
               "validated": True, "record_status": value["status"], "status": intake["disposition"],
               "reason": intake["reason"], "s0_intake_path": str(intake_path), "s0_intake_sha256": sha(intake_path),
               "source_scan_flags": [{"source_url": s["source_url"], "flags": s["flags"]}
                                     for s in value["scans"] if s["flags"]],
               "execution_authorized": False, "model_calls": 0, "outcome_values_read": 0}
    receipt["collection_rule"] = "HUB_RULING_V7_LITERATURE_BLINDNESS"
    ensure_once(original.parent / "COLLECTION.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("data/l2_pipeline/literature/registry.json"))
    parser.add_argument("--checklist", type=Path, default=Path("data/l2_pipeline/literature/FULL_RECORD_CHECKLIST.json"))
    parser.add_argument("--jobs-root", type=Path, default=Path("data/l2_pipeline/jobs/run4"))
    args = parser.parse_args(argv)
    jobs = emit_literature_jobs(args.registry, jobs_root=args.jobs_root, checklist=args.checklist)
    manifest=json.loads((args.jobs_root / "S0L/MANIFEST.json").read_text())
    failures=manifest.get("failures",[])
    print(json.dumps({"stage": "S0L", "jobs": len(jobs), "failures":failures,
                      "model_calls": 0, "retries": 0}))
    return int(bool(failures))


if __name__ == "__main__": raise SystemExit(main())
