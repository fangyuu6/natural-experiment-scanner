"""Queue-v2 intake preserves textual/legal/design lineage, never grants readiness.

Future S0 selectors should use ``rank_queue_rows`` before taking a batch slice.
The bounded history term is added to the existing declared priority score.
Evidence belongs in automation.s0_history_evidence: official_dated_histories
(official, dated_history, source_ref, locator), dated_adopters (unit,
start_year, official, source_ref, locator), and outcome_coverage
(available_through, source_ref, locator). These are reviewed availability
metadata, never database first-appearance years or desired outcome windows.
Missing evidence gets zero credit; the term cannot grant S1/S3 readiness.
V7 adds a separate bounded program term from located
automation.s0_program_evidence.broad_statutory_program declarations and the
existing same-construct public connector catalog. It cannot relax V6.1.
"""
from __future__ import annotations
import copy
import math
from typing import Any, Iterable, Mapping
from ..contracts import Disposition, StageContext, StageResult
from ..storage import artifact, ensure_once
from ..cards import validate_card
from ..first_order import first_order_gate, target_mapping

HISTORY_PRIORITY_RULE = "s0_history_priority_v1"
HISTORY_BONUS = 3
MANY_PRE2020_ADOPTERS = 10
PROGRAM_PRIORITY_RULE = "s0_broad_program_priority_v7"
PROGRAM_BONUS = 3

def _located(value: Any) -> bool:
    return (isinstance(value, Mapping) and
            all(isinstance(value.get(k), str) and value[k].strip()
                for k in ("source_ref", "locator")))

def history_priority(candidate: Mapping[str, Any]) -> dict:
    """Score legal-history/outcome availability only; inspect no outcome values.

    Give +3 for a located official dated history OR at least ten distinct
    officially dated pre-2020 adopters with located outcome data through 2024.
    The alternatives do not stack. RDD/RCT retain their own queue criteria.
    Conflicting duplicate dates earn no adopter credit. Source declarations
    still need downstream verification; list size alone supplies no dates.
    """
    automation = candidate.get("automation", {})
    evidence = automation.get("s0_history_evidence", {}) if isinstance(automation, Mapping) else {}
    if not isinstance(evidence, Mapping): evidence = {}
    histories = evidence.get("official_dated_histories", [])
    histories = histories if isinstance(histories, list) else []
    located_histories = [{k:h[k] for k in ("official", "dated_history", "source_ref", "locator")} for h in histories
                        if _located(h) and h.get("official") is True and h.get("dated_history") is True]
    starts: dict[str, set[int]] = {}
    adopters = evidence.get("dated_adopters", [])
    adopters = adopters if isinstance(adopters, list) else []
    for row in adopters:
        if (not _located(row) or row.get("official") is not True or
                not isinstance(row.get("unit"), str) or not row["unit"].strip() or
                type(row.get("start_year")) is not int or not 1800 <= row["start_year"] <= 9999):
            continue
        starts.setdefault(row["unit"].strip(), set()).add(row["start_year"])
    pre2020 = {u: next(iter(years)) for u, years in sorted(starts.items())
               if len(years) == 1 and next(iter(years)) < 2020}
    coverage = evidence.get("outcome_coverage", {})
    through = coverage.get("available_through") if _located(coverage) else None
    if type(through) is not int or not 1800 <= through <= 9999: through = None
    did = candidate.get("track") in {"did", "DiD"}
    history_available = bool(located_histories)
    mature_adopters = len(pre2020) >= MANY_PRE2020_ADOPTERS and through is not None and through >= 2024
    return {"rule": HISTORY_PRIORITY_RULE, "history_bonus": HISTORY_BONUS if did and (history_available or mature_adopters) else 0,
            "official_dated_history_available": history_available,
            "official_dated_histories": located_histories,
            "dated_pre2020_adopter_count": len(pre2020), "dated_pre2020_adopters": pre2020,
            "conflicting_dated_units": sorted(u for u, years in starts.items() if len(years) > 1),
            "outcome_available_through": through, "many_pre2020_with_2024_outcomes": mature_adopters,
            "outcome_coverage_evidence": {k:coverage[k] for k in ("source_ref", "locator")} if through is not None else {},
            "many_pre2020_threshold": MANY_PRE2020_ADOPTERS,
            "readiness_granted": False, "outcome_values_read": 0}

def program_priority(candidate: Mapping[str, Any]) -> dict:
    """Prefer located broad statutory programs with standard target measures.

    Broadness is reviewed legal metadata, never inferred from a family/title
    or the size of an outcome population. Standard measures must already map
    through the V6 catalog, including V6.1 same-construct proxy restrictions.
    Missing or narrow-program declarations earn zero; substantive purpose,
    population coverage and downstream stage readiness still need their gates.
    """
    automation = candidate.get("automation", {})
    evidence = automation.get("s0_program_evidence", {}) if isinstance(automation, Mapping) else {}
    broad = _located(evidence) and evidence.get("broad_statutory_program") is True
    first, outcome = candidate.get("first_order", {}), candidate.get("outcome", {})
    mapping = target_mapping(candidate) if isinstance(first, Mapping) and isinstance(outcome, Mapping) else {"mapped": False}
    registered = (isinstance(first, Mapping) and isinstance(outcome, Mapping) and
                  first.get("availability") == "available" and
                  first.get("primary_outcome") == outcome.get("measure_id") and
                  (first.get("relation") == "closest_proxy" or
                   (first.get("relation") == "direct" and
                    first.get("primary_outcome") == first.get("target_outcome"))))
    standard = bool(mapping["mapped"] and registered)
    return {"rule": PROGRAM_PRIORITY_RULE, "program_bonus": PROGRAM_BONUS if broad and standard else 0,
            "broad_statutory_program": bool(broad), "standard_same_construct_outcome": standard,
            "program_evidence": {k: evidence[k] for k in ("source_ref", "locator")} if broad else {},
            "connector": mapping.get("connector"), "target_outcome": mapping.get("target_outcome"),
            "measure_id": mapping.get("measure_id"), "readiness_granted": False,
            "outcome_values_read": 0, "outcome_association_calls": 0}

def rank_queue_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Return copies ranked by existing score + bounded terms, then stable ID.

    Consumers must call this before selecting future run cards; intake and S0
    persist the identical term for auditing. No queue/source input is mutated.
    """
    ranked = []
    for row in rows:
        candidate = copy.deepcopy(dict(row))
        priority = candidate.get("priority", {})
        if not isinstance(priority, Mapping): raise ValueError("S0 priority must be an object")
        # Keep reranking idempotent when our previously ranked rows are reused.
        base = priority.get("base_score", priority.get("score", 0))
        if type(base) not in (int, float) or not math.isfinite(base):
            raise ValueError("S0 base priority score must be finite numeric metadata")
        term = history_priority(candidate)
        program = program_priority(candidate)
        mapping = target_mapping(candidate) if candidate.get('first_order') and candidate.get('outcome') else {'mapped':False}
        candidate["priority"] = dict(priority, base_score=base,
                                     target_mapped=mapping["mapped"], outcome_first_rule="outcome_first_catalog_v6",
                                     history_bonus=term["history_bonus"],
                                     program_bonus=program["program_bonus"], program=program,
                                     score=base + term["history_bonus"] + program["program_bonus"], history=term)
        ranked.append(candidate)
    ranked.sort(key=lambda row: (-int(row["priority"]["target_mapped"]), -row["priority"]["score"], row.get("policy_family", ""), row["design_id"]))
    for rank, row in enumerate(ranked, 1): row["rank"] = rank
    return ranked

def card_from_queue(row: Mapping[str, Any], defaults: Mapping[str, Any], queue_version: str) -> dict:
    records = row["source_records"]
    if not records: raise ValueError("Queue design has no source records")
    card = copy.deepcopy(dict(defaults))
    card.update(schema_version="1.0", version="v1", design_id=row["design_id"],
                track={"did":"DiD", "rdd":"RDD", "rct_lottery":"RCT"}.get(row["track"], row["track"]),
                policy_family=row.get("policy_family") or "unresolved:" + row["design_id"],
                lineage={"queue_version":queue_version,"screen_version":"existing_l1",
                         "source_text_ids":sorted({r["source_text_id"] for r in records}),
                         "legal_instrument_ids":sorted({r["legal_instrument_id"] for r in records})},
                sources=[{"source_id":r["source_record_id"],"url":r.get("source_url") or "unresolved",
                          "locator":r["source_record_id"],"license":"unverified"} for r in records])
    # Copy reviewed availability metadata explicitly; extraction years and
    # arbitrary outcome/effect fields from the queue confer no priority credit.
    row_automation = row.get("automation", {})
    if isinstance(row_automation, Mapping):
        for key in ("s0_history_evidence", "s0_program_evidence"):
            if key in row_automation:
                card.setdefault("automation", {})[key] = copy.deepcopy(row_automation[key])
    if "first_order" in row:
        card["first_order"] = copy.deepcopy(row["first_order"])
    card.setdefault("automation", {})["s0_history_priority"] = history_priority(card)
    card["automation"]["s0_program_priority"] = program_priority(card)
    from ..coverage import coverage_bounded_card
    card=coverage_bounded_card(card)
    from ..connectors.rates import prospective_card
    card = prospective_card(card)
    card['l3_export_v2'] = {'schema':'l2_addendum_v2_c64'}
    card.setdefault('first_order', {}).setdefault('purpose_direction', None)
    errors = validate_card(card)
    if errors: raise ValueError("Intake needs answer-free design defaults: " + "; ".join(errors))
    return card

def run(ctx: StageContext) -> StageResult:
    path = ensure_once(ctx.design_dir / "CARD.json", dict(ctx.card.payload))
    priority = history_priority(ctx.card.payload)
    priority_path = ensure_once(ctx.design_dir / "S0_PRIORITY.json", priority)
    program = program_priority(ctx.card.payload)
    program_path = ensure_once(ctx.design_dir / "S0_PROGRAM_PRIORITY.json", program)
    gate = first_order_gate(ctx.card.payload)
    coverage=ctx.card.payload.get('automation',{}).get('coverage_window',{})
    if not coverage and (ctx.card.payload.get('treatment',{}).get('source_window') or ctx.card.payload.get('treatment',{}).get('coverage_end')):
        from ..coverage import coverage_bounded_card
        coverage=coverage_bounded_card(ctx.card.payload)['automation']['coverage_window']
    if coverage.get('rule')=='coverage_bounded_windows_v6' and not gate['passed']:
        from ..agents.purpose import emit_purpose_verification
        job=emit_purpose_verification(ctx, ctx.workspace.parent)
        mapping=target_mapping(ctx.card.payload)
        if mapping['mapped'] and not coverage.get('parked') and ctx.card.payload.get('first_order',{}).get('evidence_status')!='verified_by_hub':
            gate=dict(gate,reason='S0 purpose verification pending')
    from ..outcome_transform import s0_issues
    transform_issues = s0_issues(ctx.card.payload)
    if transform_issues:
        return StageResult("S0", ctx.card.design_id, Disposition.PARKED, "Outcome transform gate: " + "; ".join(transform_issues),
                           "Rebuild the card as level; a log card is registered only for a strictly positive series",
                           artifacts=(artifact(path, "design_card"),), metrics={"outcome_values_read": 0})
    gate_path = ensure_once(ctx.design_dir / "FIRST_ORDER_GATE_S0.json", gate)
    artifacts = (artifact(path, "design_card"), artifact(priority_path, "intake_priority"),
                 artifact(program_path, "intake_program_priority"), artifact(gate_path, "first_order_gate"))
    if not gate["passed"]:
        return StageResult("S0", ctx.card.design_id, Disposition.PARKED, gate["reason"],
                           "Supply a free connector for the direct target or document the closest measurable proxy and located purpose/literature evidence",
                           artifacts=artifacts, metrics={"history_priority_bonus":priority["history_bonus"], "program_priority_bonus":program["program_bonus"], "first_order_issues":gate["issues"], "purpose_job_pending":coverage.get("rule")=="coverage_bounded_windows_v6", "outcome_values_read":0})
    return StageResult("S0", ctx.card.design_id, Disposition.READY, "Validated first-order intake; source and outcome readiness pending", artifacts=artifacts, metrics={"history_priority_bonus":priority["history_bonus"], "program_priority_bonus":program["program_bonus"], "outcome_values_read":0})
