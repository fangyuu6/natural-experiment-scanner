"""Prospective, outcome-blind RUN2 specification preparation.

The original intake card and audited S1 import stay immutable. A separate
write-once declaration binds additions needed by S2–S4 before any estimation.
This package is a draft; it grants no registration or execution authority.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

from .cards import validate_card
from .contracts import StageContext
from .storage import ensure_once, sha


def effective_context(ctx: StageContext) -> StageContext:
    """Restore the bound scientific declaration without changing intake identity."""
    path = ctx.design_dir / "RUN2_PREPARATION.json"
    if not path.is_file():
        return ctx
    declaration = ctx.read("RUN2_PREPARATION.json")
    value = declaration.get("prepared_payload")
    if (declaration.get("schema_version") != "run2_preparation_v1"
            or declaration.get("design_id") != ctx.card.design_id
            or declaration.get("version") != ctx.card.version
            or Path(declaration.get("original_card_path", "")).resolve() != ctx.card.path.resolve()
            or declaration.get("original_card_sha256") != sha(ctx.card.path)
            or declaration.get("treatment_sha256") != sha(ctx.design_dir / "TREATMENT.json")
            or not isinstance(value, dict) or validate_card(value)
            or any(value.get(k) != getattr(ctx.card, k) for k in
                   ("design_id", "version", "policy_family", "mechanism", "track", "unit", "outcome_family"))
            or declaration.get("outcome_association_calls") != 0
            or declaration.get("execution_authorized") is not False):
        raise ValueError("Prepared scientific payload intake/treatment binding changed")
    return replace(ctx, card=replace(ctx.card, payload=copy.deepcopy(value)))


def prepared_payload(payload: dict, records: list[dict]) -> dict:
    """Declare a uniform clean-pre-period rule from clocks, never values."""
    value = copy.deepcopy(payload)
    analysis = value["analysis"]
    first, last = value["outcome"]["years"]
    earliest = first + analysis["min_pre_periods"]
    excluded = set(value["treatment"].get("excluded_units", []))
    excluded.update(r["unit"] for r in records
                    if r["status"] == "treated" and r["start_period"] < earliest)
    value["treatment"]["excluded_units"] = sorted(excluded)
    analysis.update({
        "intake_disposition": "S1_AUDIT_PASSED",
        "spec_status": "PROSPECTIVE_DRAFT_REQUIRES_REVIEW_AND_FREEZE",
        "estimand": "Overall ATT across supported post-adoption cohort-period cells, "
                    "weighted by cohort treated-state counts; retained annual adoption "
                    "cohorts have at least the declared clean pre-period window.",
        "weights": "Equal states within cohort-period contrasts; cohort treated-state "
                   "counts across supported post cells; no outcome-based weights.",
        "inference": "State-cluster Webb multiplier intervals (4999 draws); "
                     "CT complete-control-path fallback only after prospective "
                     "certification; otherwise point estimate plus fixed-label RI.",
        "multiplier_count": 4999,
        "diagnostics": ["Supported clean pre-adoption leads", "Fixed pre-adoption placebo",
                        "Fixed-label randomization reference; at least 90% feasible draws"],
        "stop_rules": ["Registered support thresholds fail", "Unknown treatment scope",
                       "Uncertified source/population linkage", "Independent contrast disagreement",
                       "Unsigned or changed freeze package", "Unclosed MUST/SHOULD review findings"],
        "cohort_eligibility_rule": {
            "basis": "Declared outcome calendar and audited annual treatment clocks only",
            "earliest_retained_adoption": earliest,
            "last_retained_adoption": last,
            "excluded_units": sorted(excluded),
            "historical_treated_reclassified_as_controls": False,
            "later_missing_cells_trigger_support_stop": True,
            "outcome_values_used": False,
        },
    })
    value["automation"]["personas"] = ["Card", "Finkelstein", "Roth", "Liebman", "Boehmke"]
    errors = validate_card(value)
    if errors:
        raise ValueError("Invalid prepared RUN2 declaration: " + "; ".join(errors))
    return value


def prepare_context(ctx: StageContext) -> StageContext:
    """Apply a source plan and preserve the hash-bound original intake identity."""
    from .connectors.run2_public import apply_outcome_plan
    ctx = apply_outcome_plan(ctx)
    accepted = ctx.read("TREATMENT.json")
    value = prepared_payload(dict(ctx.card.payload), accepted["records"])
    value["automation"]["v3_audit"] = dict(value["automation"].get("v3_audit", {}),
                                              status="PASSED", metrics=accepted["audit"])
    value["automation"]["connector_status"] = "Bound public-source plan; S2 construction required"
    # Out-of-coverage audited tuples have a separate sensitivity declaration.
    scope = accepted.get("scope_sensitivity_records", [])
    if scope:
        sensitivity_records = accepted["records"] + scope
        value["sensitivities"] = value["sensitivities"] + [{
            "name": "audit_outside_source_window",
            "changes": {"treatment": {"records": sensitivity_records}},
            "scope": "Hub-authorized sensitivity; primary excludes corrections outside source coverage",
        }]
    declaration = {
        "schema_version": "run2_preparation_v1", "design_id": ctx.card.design_id,
        "version": ctx.card.version, "original_card_path": str(ctx.card.path),
        "original_card_sha256": sha(ctx.card.path),
        "treatment_sha256": sha(ctx.design_dir / "TREATMENT.json"),
        "prepared_payload": value, "preparation_code_sha256": sha(Path(__file__)),
        "outcome_association_calls": 0, "execution_authorized": False,
        "review_and_freeze_required": True,
    }
    ensure_once(ctx.design_dir / "RUN2_PREPARATION.json", declaration)
    return replace(ctx, card=replace(ctx.card, payload=value))
