"""V6 outcome-blind purpose jobs and explicit hub acceptance.

Collection validates an evidence contract; only a separate hub ruling accepts
the substantive purpose. Neither step certifies connector/population coverage.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..contracts import StageContext
from ..storage import ensure_once, output_root as bounded_root, public_input, sha
from .jobs import AgentJob, _safe_component, _write_text_once, host_plan

KIND = "s0_purpose_verification_v6"
PERSONA_LENSES = {"labor": "Card", "health": "Finkelstein", "crime": "Doleac",
                  "education": "Liebman", "public_programs": "Liebman"}
TEMPLATE = Path(__file__).parent / "templates/v6/purpose_verifier_v6_3.md"
PURPOSE_RULE = "V6.2"
C64_SCHEMA = "l2_addendum_v2_c64"
C64_TEMPLATE = TEMPLATE.with_name("purpose_verifier_c86.md")  # c64 text kept for jobs already emitted; c86 fixes `supports`
R1_TEMPLATE = TEMPLATE.with_name("purpose_verifier_r1.md")  # REDESIGN R1: purpose is a label (rulings Q1, Q2, Q5)


def purpose_domain(card):
    """Use explicit intake domain first, then target/family metadata only."""
    explicit = str(card.get("automation", {}).get("intake", {}).get("domain", "")).lower()
    if "labor" in explicit: return "labor"
    if "health" in explicit or "insurance" in explicit: return "health"
    if "crime" in explicit or "transport" in explicit: return "crime"
    if "education" in explicit: return "education"
    text = (str(card.get("policy_family", "")) + " " +
            str(card.get("first_order", {}).get("target_outcome", ""))).lower()
    for domain, tokens in (
        ("labor", ("wage", "earnings", "hiring", "employment", "hours", "leave", "pfml", "salary", "licens")),
        ("health", ("health", "insurance", "medicaid", "medical", "screening", "smoking", "contracept", "opioid", "natality")),
        ("crime", ("crime", "homicide", "theft", "traffic", "dui", "carry", "standground")),
        ("education", ("college", "enroll", "education", "completion", "tuition", "meritaid")),
    ):
        if any(token in text for token in tokens): return domain
    return "public_programs"


def _bound(job, ctx) -> bool:
    return (job.design_id == ctx.card.design_id and job.design_version == ctx.card.version and
            job.contract.get("card_sha256") == sha(ctx.card.path))


def emit_purpose_verification(ctx: StageContext, output_root: Path | None = None) -> AgentJob:
    """Emit one immutable S0 packet; output_root is the dispatch jobs root."""
    _safe_component(ctx.card.design_id); _safe_component(ctx.card.version)
    root = bounded_root(output_root or ctx.repo / "data/l2_pipeline/jobs", ctx.repo)
    path = root / "S0" / ctx.card.design_id / "purpose_verifier.md"
    if path.with_suffix(".job.json").exists():
        from .jobs import load_job
        prior = load_job(path)
        if _bound(prior, ctx):
            return prior
        if prior.design_id != ctx.card.design_id or prior.design_version == ctx.card.version:
            raise ValueError("Existing purpose packet differs from card")   # same version, other card bytes: integrity
        # The design directory holds the packet of an earlier version of this design (an attempt opened by a requeue,
        # 2026-10-19): that packet stays as written and the packet of this version is written beside it, once.
        path = root / "S0" / ctx.card.design_id / "versions" / ctx.card.version / "purpose_verifier.md"
        if path.with_suffix(".job.json").exists():
            prior = load_job(path)
            if not _bound(prior, ctx):
                raise ValueError("Existing purpose packet differs from card")
            return prior
    out = bounded_root(ctx.design_dir / "agents/purpose_verifier", ctx.repo)
    original = out / "purpose.json"
    promote = bounded_root(ctx.design_dir, ctx.repo) / "PURPOSE_VERIFICATION.json"
    card = ctx.card.payload
    first = card.get("first_order", {})
    domain = purpose_domain(card)
    contract = {"design_id": ctx.card.design_id, "role": "purpose_verifier", "jobs_kind": KIND,
                "domain": domain, "internal_persona_lens": PERSONA_LENSES[domain],
                "card_path": str(public_input(ctx.card.path)), "card_sha256": sha(ctx.card.path),
                "policy_family": card.get("policy_family"), "mechanism": card.get("mechanism"),
                "population_lead": card.get("population", {}),
                "target_lead": first.get("target_outcome"),
                "statute_leads": first.get("statute_refs", []),
                "literature_leads": first.get("literature_refs", []),
                "leads_are_verified": False, "hub_acceptance_required": True,
                "purpose_rule": PURPOSE_RULE,
                "target_definition": "behavior or welfare outcome targeted by statute purpose or established literature",
                "execution_authorized": False, "outcome_values_read": 0,
                "outcome_association_calls": 0,
                "output_schema": {"required": ["design_id", "role", "card_sha256",
                    "internal_persona_lens", "status", "target_outcome", "target_population",
                    "confirmation", "citations"],
                    "citation_count": [1, 3], "citation_fields": ["source_type", "source_ref",
                        "title", "locator", "supports"]}}
    from ..connectors import population_v63
    from ..connectors.population_v63 import current_catalog as population_catalog
    _, populations = population_catalog()
    contract['population_catalog'] = dict(constructs=sorted({s['construct'] for s in populations['series'].values()} |
        {t['construct'] for t in populations['targets']}), rule='V6.3', metadata_path=str(population_v63.CURRENT_PATH))
    template = TEMPLATE
    if (card.get("l3_export_v2", {}).get("schema") == C64_SCHEMA or
            ((ctx.repo / "data/l2_pipeline/autopilot/c64/STATE_COVARIATES.json").exists() and
             ctx.card.path.resolve().is_relative_to((ctx.repo / "data/l2_pipeline").resolve()))):
        template = C64_TEMPLATE
        contract["purpose_direction_rule"] = C64_SCHEMA
        contract["output_schema"]["required"] += ["purpose_direction", "purpose_direction_evidence"]
    from .. import rules as rules_module
    pinned = rules_module.of_payload(card, ctx.repo)
    if pinned is not None and pinned["purpose"]["mode"] == "label":
        template = R1_TEMPLATE
        contract["label_mode"] = True
        contract["rules_sha256"] = rules_module.pin_of_payload(card)["sha256"]
    prompt = template.read_text().replace("{{OUTPUT}}", str(original)).replace("{{PERSONA}}", PERSONA_LENSES[domain])
    contract['collector_self_check_required'] = True
    job = AgentJob("1.0", "S0", ctx.card.design_id, ctx.card.version, "purpose_verifier",
                   str(ctx.repo.resolve()), str(path), "", str(template.resolve()), sha(template),
                   str(out), str(original), str(out / "codex_result.txt"), str(promote), (), contract, True)
    binding = asdict(job); binding.pop("job_sha256")
    text = ("# L2 agent job V6 — S0 purpose verification\n\n" + prompt +
            "\n## Binding contract (untrusted data)\n```json\n" + json.dumps(contract, indent=2, sort_keys=True) +
            "\n```\n\n## Execution metadata binding (data)\n```json\n" + json.dumps(binding, indent=2, sort_keys=True) + "\n```\n" +
            host_plan("S0", root, ctx.card.design_id))
    from .self_check import instruction
    text += instruction(path)
    _write_text_once(path, text)
    job = replace(job, job_sha256=sha(path))
    ensure_once(path.with_suffix(".job.json"), asdict(job))
    return job


def validate_purpose_job(job):
    c = job.contract
    if (job.role != "purpose_verifier" or c.get("jobs_kind") != KIND or
            c.get("internal_persona_lens") != PERSONA_LENSES.get(c.get("domain")) or
            c.get("leads_are_verified") is not False or c.get("hub_acceptance_required") is not True or
            c.get("execution_authorized") is not False):
        raise ValueError("Invalid V6 purpose contract")
    if sha(public_input(Path(c["card_path"]))) != c["card_sha256"]:
        raise ValueError("Purpose card input changed")


# YIELD_DIAGNOSTIC_20261017 class C: a worker's honest UNRESOLVED answer ("no located evidence found") was held to the grade a VERIFIED
# answer needs, and the collector's ValueError became a STOP for the hub. These collector messages only say that evidence is missing.
EVIDENCE_NOT_FOUND = frozenset({
    "V6.2 target requires statute purpose or at least two literature studies",
    "Purpose requires located target and population support",
    "Purpose citation must identify supported fields",
    "Purpose citation lacks type/title/locator",
    "Purpose needs unique located public citations, not search links",
    "Purpose requires 1–3 citations",
    "Purpose confirmation must cite every reference",
    "Purpose construct absent from reviewed catalog",
    "Purpose population metadata invalid",
})


SUPPORT_WORDS = ("target_outcome", "target_population")


def normalise_supports(fields):
    """Keep the leading field word of each entry that starts with one (in order, once); other entries are left as written."""
    result = []
    for field in fields:
        if isinstance(field, str):
            word = field.strip().split(":", 1)[0].split(" ", 1)[0].split("(", 1)[0].strip(" -—–,;")
            if word in SUPPORT_WORDS:
                field = word
        if field not in result or not isinstance(field, str):
            result.append(field)
    return result


def unresolved_reason(value, exc):
    """The worker's reason when `value` is an honest UNRESOLVED purpose rejected only for missing evidence, else None.

    Identity, card and persona mismatches, unreadable output and a missing reason are not 'evidence not found': they stay errors."""
    if not isinstance(value, dict) or value.get("status") != "UNRESOLVED":
        return None
    message = str(exc)
    if not (message in EVIDENCE_NOT_FOUND or message.startswith(("Purpose requires one-line ", "Purpose direction "))):
        return None
    reason = value.get("unresolved_reason")
    return " ".join(reason.split())[:500] if isinstance(reason, str) and reason.strip() else None


def validate_purpose_output(job, value):
    from .collect import _text
    c = job.contract
    if any(value.get(k) != expected for k, expected in (
            ("design_id", job.design_id), ("role", job.role), ("card_sha256", c["card_sha256"]),
            ("internal_persona_lens", c["internal_persona_lens"]))):
        raise ValueError("Purpose identity/card/persona mismatch")
    # V6.1 §6: this job has no peer answers. Retain any supplied disclosure
    # unchanged, but neither require it nor use it to reject purpose evidence.
    if value.get("status") not in {"VERIFIED", "UNRESOLVED"}:
        raise ValueError("Purpose status must be VERIFIED or UNRESOLVED")
    if c.get("label_mode") and value["status"] == "UNRESOLVED":
        # REDESIGN R1: UNRESOLVED is a label. It needs a reason and the list of what was tried, nothing else.
        tried = value.get("tried")
        if not _text(value.get("unresolved_reason")):
            raise ValueError("Unresolved purpose requires a reason")
        if not isinstance(tried, list) or not tried or not all(_text(t) for t in tried):
            raise ValueError("Unresolved purpose requires the list of what was tried")
        kept = {k: value[k] for k in ("design_id", "role", "card_sha256", "internal_persona_lens", "status", "unresolved_reason",
                                      "tried", "blindness_attestation") if k in value}
        kept.update({k: value[k] for k in ("target_outcome", "target_population", "confirmation") if _text(value.get(k))})
        kept["citations"] = []  # unverified citations of an unresolved answer never feed the direction coders
        return kept
    for field in ("target_outcome", "target_population", "confirmation"):
        if not _text(value.get(field)) or "\n" in value[field] or "\r" in value[field]:
            raise ValueError("Purpose requires one-line " + field)
    if value["status"] == "UNRESOLVED" and not _text(value.get("unresolved_reason")):
        raise ValueError("Unresolved purpose requires a reason")
    citations = value.get("citations")
    if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
        raise ValueError("Purpose requires 1–3 citations")
    seen = set(); supported = set()
    for ref in citations:
        if (not isinstance(ref, dict) or ref.get("source_type") not in {"statute", "paper"} or
                any(not _text(ref.get(k)) for k in ("source_ref", "title", "locator"))):
            raise ValueError("Purpose citation lacks type/title/locator")
        url = urlsplit(ref["source_ref"])
        key = ref["source_ref"].split("#", 1)[0].rstrip("/")
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or
                any(k in parse_qs(url.query) for k in ("q", "query", "term", "search")) or
                "/search" in url.path.lower() or key in seen):
            raise ValueError("Purpose needs unique located public citations, not search links")
        fields = ref.get("supports")
        if isinstance(fields, list):
            # A descriptive suffix ("target_outcome: names burglary in Table 3") is a format slip, not a different claim.
            fields = ref["supports"] = normalise_supports(fields)
        if (not isinstance(fields, list) or not fields or
                not all(isinstance(field, str) and field in {"target_outcome", "target_population"} for field in fields) or
                len(set(fields)) != len(fields)):
            raise ValueError("Purpose citation must identify supported fields")
        seen.add(key); supported.update(fields)
    if supported != {"target_outcome", "target_population"}:
        raise ValueError("Purpose requires located target and population support")
    purpose_basis(citations)
    if 'target_construct' in value:
        allowed=c.get('population_catalog',{}).get('constructs',[])
        if value['target_construct'] not in allowed+['new_construct']:
            raise ValueError('Purpose construct absent from reviewed catalog')
    if 'target_population_metadata' in value:
        population=value['target_population_metadata']
        ages=population.get('ages') if isinstance(population,dict) else None
        restrictions=population.get('restrictions') if isinstance(population,dict) else None
        if (not isinstance(ages,list) or len(ages)!=2 or any(type(v) is not int for v in ages) or
            not 0 <= ages[0] <= ages[1] <= 120 or not isinstance(restrictions,dict) or
            any(not _text(k) or not _text(v) for k,v in restrictions.items()) or
            set(population)!={'ages','restrictions'}):
            raise ValueError('Purpose population metadata invalid')
    if any("[" + str(index) + "]" not in value["confirmation"] for index in range(1, len(citations) + 1)):
        raise ValueError("Purpose confirmation must cite every reference")
    if c.get("purpose_direction_rule") == C64_SCHEMA:
        validate_purpose_direction(value, citations)
    return {k: value[k] for k in ("design_id", "role", "card_sha256", "internal_persona_lens",
        "status", "target_outcome", "target_population", "confirmation", "citations", "blindness_attestation", "target_construct", "target_population_metadata", "purpose_direction", "purpose_direction_evidence")
        if k in value} | ({"unresolved_reason": value["unresolved_reason"]} if value["status"] == "UNRESOLVED" else {})


def validate_purpose_direction(value: dict, citations: list[dict] | None = None) -> dict:
    """Validate F1 provenance without inferring a sign or selecting designs.

    The hub retains independently authored originals and verifies attestations.
    Missing coding remains unavailable; it is never encoded as unclear (zero).
    """
    from .collect import _text
    evidence = value.get("purpose_direction_evidence")
    if ("purpose_direction" not in value or not isinstance(evidence, dict) or
            evidence.get("schema") != C64_SCHEMA or evidence.get("coded_at_stage") != "S0" or
            evidence.get("target_outcome") != value.get("target_outcome") or
            not _text(evidence.get("target_outcome"))):
        raise ValueError("Purpose direction requires S0 evidence bound to the target")
    sign = value["purpose_direction"]
    if evidence.get("status") == "pending_blind_coding":
        if sign is not None or evidence.get("coverage_code") != "FIELD_MISSING" or not _text(evidence.get("reason")):
            raise ValueError("Pending blind coding requires null direction and FIELD_MISSING reason")
        return evidence
    if evidence.get("status") != "verified" or type(sign) is not int or sign not in {-1, 0, 1}:
        raise ValueError("Verified purpose_direction must be integer -1, 0 or 1")
    records = evidence.get("codings")
    if not isinstance(records, list) or len(records) not in {2, 3}:
        raise ValueError("Purpose direction requires two independent blind coders")
    if [r.get("role") if isinstance(r, dict) else None for r in records] not in (["A", "B"], ["A", "B", "third"]):
        raise ValueError("Purpose direction coding order must be A, B, optional third")
    seen_coders = set(); seen_hashes = set()
    permitted = None if citations is None else {(r.get("source_ref"), r.get("locator"))
        for r in citations if "target_outcome" in r.get("supports", [])}
    for record in records:
        coder = record.get("coder_id"); digest = record.get("original_sha256")
        if (not _text(coder) or coder in seen_coders or not isinstance(digest, str) or
                not re.fullmatch(r"[0-9a-f]{64}", digest) or digest in seen_hashes):
            raise ValueError("Purpose direction needs distinct coder identities and original hashes")
        seen_coders.add(coder); seen_hashes.add(digest)
        try:
            stamp = datetime.fromisoformat(record["recorded_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None: raise ValueError("timezone required")
        except (KeyError, TypeError, AttributeError, ValueError):
            raise ValueError("Purpose direction coding requires a timestamp with timezone") from None
        code = record.get("purpose_direction")
        if type(code) is not int or code not in {-1, 0, 1}:
            raise ValueError("Purpose direction coder sign invalid")
        blind = record.get("blindness_attestation")
        fields = ["outcome_values_read", "outcome_associations_read", "forecasts_read", "literature_effect_signs_read"]
        if record["role"] != "third": fields.append("other_codings_read")
        if not isinstance(blind, dict) or any(blind.get(k) is not False for k in fields):
            raise ValueError("Purpose direction coding must be blind to outcomes, forecasts and literature signs")
        refs = record.get("citations")
        if not isinstance(refs, list) or not refs:
            raise ValueError("Purpose direction coding requires located purpose citations")
        for ref in refs:
            if (not isinstance(ref, dict) or ref.get("evidence_kind") not in {"statute_purpose", "s0_target_definition"} or
                    any(not _text(ref.get(k)) for k in ("source_ref", "locator", "intended_change"))):
                raise ValueError("Purpose direction requires an intended-change passage, never an effect sign")
            url = urlsplit(ref["source_ref"])
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError("Purpose direction requires public source locators")
            if permitted is not None and (ref["source_ref"], ref["locator"]) not in permitted:
                raise ValueError("Purpose direction citation must match an S0 target citation")
    disagreement = records[0]["purpose_direction"] != records[1]["purpose_direction"]
    if disagreement != (len(records) == 3):
        raise ValueError("Purpose direction requires a third coder exactly on disagreement")
    if sign != records[-1]["purpose_direction"]:
        raise ValueError("Purpose direction differs from blind coding reconciliation")
    return evidence


def purpose_basis(citations: list[dict]) -> str:
    """Check evidence routes; the hub still verifies cited substantive claims."""
    target_refs = [r for r in citations if "target_outcome" in r.get("supports", [])]
    if any(r["source_type"] == "statute" for r in target_refs):
        return "statute_purpose"
    papers = {r["source_ref"].split("#", 1)[0].rstrip("/") for r in target_refs
              if r["source_type"] == "paper"}
    studies = {" ".join(r["title"].casefold().split()) for r in target_refs
               if r["source_type"] == "paper"}
    if len(papers) >= 2 and len(studies) >= 2:
        return "established_literature"
    raise ValueError("V6.2 target requires statute purpose or at least two literature studies")


def accept_purpose_verification(ctx: StageContext, decision: str, reason: str, *,
                                verified_target: str | None = None,
                                verified_population: str | None = None,
                                verified_citations: list[dict] | None = None,
                                ruling_name: str = "PURPOSE_HUB_RULING_V62.json") -> dict:
    """Hub-only accept/park ruling; return a card patch without mutating its card.

    Applying a changed target still requires remapping the prospective connector
    and a new card version before any downstream readiness or freeze.
    """
    from .jobs import load_job
    from .collect import collect_job, _text
    if decision not in {"accept", "park"} or not _text(reason):
        raise ValueError("Purpose hub ruling needs accept/park and a reason")
    if Path(ruling_name).name != ruling_name or not ruling_name.endswith(".json"):
        raise ValueError("Purpose ruling requires a local JSON filename")
    override = verified_target is not None or verified_population is not None or verified_citations is not None
    if override and (decision != "accept" or any(
            not _text(v) or "\n" in v or "\r" in v
            for v in (verified_target, verified_population))):
        raise ValueError("Explicit V6.2 review requires one-line verified target and population")
    collection_path = ctx.design_dir / "agents/purpose_verifier/COLLECTION.json"
    collection = json.loads(collection_path.read_text())
    job = load_job(Path(collection["job_path"]))
    if (job.design_id != ctx.card.design_id or job.design_version != ctx.card.version or
            job.contract["card_sha256"] != sha(ctx.card.path) or Path(job.promote_path) != ctx.design_dir.resolve() / "PURPOSE_VERIFICATION.json"):
        raise ValueError("Purpose ruling card/collection binding differs")
    if collect_job(job.job_path) != collection:
        raise ValueError("Purpose collection receipt changed")
    purpose = json.loads(Path(job.promote_path).read_text())
    if decision == "accept" and purpose["status"] != "VERIFIED" and not override:
        raise ValueError("Hub cannot accept an unresolved purpose")
    citations = purpose["citations"]
    if override:
        if (job.contract.get("purpose_direction_rule") == C64_SCHEMA and
                verified_target != purpose["target_outcome"]):
            raise ValueError("Changed purpose target requires fresh blind direction coding")
        if verified_citations is None:
            raise ValueError("Explicit V6.2 review requires verified citations bound to existing evidence")
        originals = {r["source_ref"]: r for r in citations}
        for ref in verified_citations:
            original = originals.get(ref.get("source_ref")) if isinstance(ref, dict) else None
            if original is None or any(ref.get(k) != original[k] for k in
                                      ("source_type", "source_ref", "title", "locator")):
                raise ValueError("V6.2 reviewed citations must retain original source identity and locator")
        reviewed = dict(purpose, status="VERIFIED", target_outcome=verified_target,
                        target_population=verified_population, citations=verified_citations,
                        confirmation="Hub reviewed target and population " + " ".join(
                            "[" + str(i) + "]" for i in range(1, len(verified_citations) + 1)))
        validate_purpose_output(job, reviewed)
        citations = verified_citations
    basis = purpose_basis(citations)
    patch = {} if decision == "park" else {"first_order": {
        "target_outcome": verified_target if override else purpose["target_outcome"],
        "target_population": verified_population if override else purpose["target_population"],
        "evidence_status": "verified_by_hub",
        "purpose_rule": PURPOSE_RULE, "purpose_basis": basis,
        "purpose_route": "statute" if basis == "statute_purpose" else "established_literature",
        "statute_refs": [{"source_ref": r["source_ref"], "locator": r["locator"], "supports": r["supports"]} for r in citations if r["source_type"] == "statute"],
        "literature_refs": [{"source_ref": r["source_ref"], "title": r["title"], "locator": r["locator"], "supports": r["supports"]} for r in citations if r["source_type"] == "paper"]}}
    if decision == "accept" and job.contract.get("purpose_direction_rule") == C64_SCHEMA:
        validate_purpose_direction(purpose, citations)
        patch["first_order"].update({key: purpose[key] for key in
            ("purpose_direction", "purpose_direction_evidence")})
    ruling = {"schema_version": "6.2", "purpose_rule": PURPOSE_RULE,
              "purpose_basis": basis, "explicit_hub_reverification": override,
              "design_id": ctx.card.design_id, "decision": decision,
              "reason": reason, "purpose_sha256": sha(Path(job.promote_path)),
              "collection_sha256": sha(collection_path), "card_sha256": sha(ctx.card.path),
              "execution_authorized": False, "card_patch": patch}
    if override:
        ruling["verified_citations"] = verified_citations
    previous = ctx.design_dir / "PURPOSE_HUB_RULING.json"
    if previous.exists() and previous.name != ruling_name:
        ruling["supersedes"] = {"path": str(previous), "sha256": sha(previous)}
    ensure_once(ctx.design_dir / ruling_name, ruling)
    return ruling
