"""
Research feasibility scoring system (v3 — 1-5 scale).

Single path: composite_score (Python-computed) + rule-based adjustments.
No fallback path. Confound is info-only (no score penalty).

Weights: 0.35*identification + 0.25*policy_significance + 0.20*treatment_intensity + 0.20*data_feasibility
All sub-scores are 1-5 integers. composite_score is 1.0-5.0 weighted average.
"""
from typing import Any, Dict, Optional


def _to_bool(v) -> bool:
    """Convert various truthy values to bool."""
    if v is True:
        return True
    return str(v).lower() == "true" if v is not None else False


def _safe_float(v, default: float = 1.0) -> float:
    """Safely convert to float."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _literature_bonus(result: Dict[str, Any]) -> float:
    """
    Bonus/penalty based on LLM-assessed literature saturation.
    Scaled for 1-5 range (±0.4 ≈ 8% of range).
    """
    sat = (result.get("llm_literature_saturation") or "").strip().lower()
    return {
        "blue_ocean": 0.4,
        "emerging": 0.2,
        "competitive": -0.15,
        "crowded": -0.4,
    }.get(sat, 0)


def calculate_feasibility_score(result: Dict[str, Any]) -> Optional[float]:
    """
    Calculate research feasibility score (1.0-5.0).
    Returns None if composite_score is missing (scoring failed).

    Pipeline: composite + literature ± data_access ± mismatch ± power ± border_county
    Confound severity is info-only — does not affect score.
    """
    if result.get("composite_score") is None:
        return None

    try:
        composite = float(result["composite_score"])
    except (TypeError, ValueError):
        return None

    score = composite + _literature_bonus(result)

    # P4 fix: identification hard cap — weak identification cannot produce
    # "good" feasibility regardless of other sub-scores
    identification = _safe_float(result.get("identification"), 1.0)
    if identification <= 2:
        score = min(score, 2.5)

    # Data access penalty (RD only — RD needs individual-level data near cutoff)
    if _to_bool(result.get("rd_candidate")):
        da = (result.get("data_access_difficulty") or "").lower()
        if da == "rdc_only":
            score -= 0.5
        elif da == "state_agency_only":
            score -= 0.3

    # Measurement mismatch
    mismatch = (result.get("measurement_mismatch_risk") or "").lower()
    score += {"severe": -0.4, "moderate": -0.2}.get(mismatch, 0)

    # Power risk — insufficient is a hard constraint (cap at 2)
    power = (result.get("power_risk") or "").lower()
    if power == "insufficient":
        score = min(score, 2.0)  # gate: can't do research if can't detect effect
    elif power == "marginal":
        score -= 0.2

    # Border-county DID bonus (stronger design option available)
    if (result.get("did_control_quality") or "").lower() == "border_county_possible":
        score += 0.25

    return round(min(max(score, 1.0), 5.0), 1)


def get_feasibility_label(score) -> str:
    """Return feasibility label based on score (1-5 scale)."""
    if score is None:
        return "difficult"
    if score >= 4.5:
        return "excellent"
    elif score >= 3.5:
        return "good"
    elif score >= 2.5:
        return "moderate"
    elif score >= 1.5:
        return "challenging"
    else:
        return "difficult"


def get_evidence_tier(score) -> Optional[int]:
    """Map feasibility score to evidence tier (1=best, 3=worst)."""
    if score is None:
        return None
    if score >= 3.5:
        return 1
    if score >= 2.5:
        return 2
    return 3


def get_is_feasible(result: Dict[str, Any], feasibility_score) -> bool:
    """
    Five-condition gate for is_feasible (1-5 scale).
    No confound logic — confound is info-only.
    """
    if feasibility_score is None:
        return False
    identification = _safe_float(result.get("identification"), 1.0)
    ps = _safe_float(result.get("policy_significance"), 1.0)
    power = (result.get("power_risk") or "").lower()
    return (
        feasibility_score >= 2.5
        and identification >= 2
        and ps >= 2                            # exclude trivial/ceremonial
        and power != "insufficient"            # can't detect effect = can't do research
        and result.get("policy_status") != "proposed"
    )


def get_feasibility_recommendations(result: Dict[str, Any], score) -> str:
    """Generate recommendations. Prefer AER verdict + red_flags when present."""
    if result.get("verdict") or result.get("red_flags"):
        parts = []
        for p in [result.get("verdict"), result.get("red_flags")]:
            if not p:
                continue
            if isinstance(p, list):
                parts.extend(str(x) for x in p)
            else:
                parts.append(str(p))
        return " | ".join(parts)
    recommendations = []

    if result.get("policy_status") == "proposed":
        recommendations.append("Bill not yet enacted; consider waiting or studying anticipation effects")

    if not result.get("recommended_datasets"):
        recommendations.append("No datasets recommended; manual search needed")

    if result.get("manipulation_risk") == "high":
        if result.get("rd_type") == "fuzzy":
            recommendations.append("High manipulation risk; fuzzy RD — use IV/2SLS with threshold-crossing as instrument, report first-stage F-statistic")
        else:
            recommendations.append("High manipulation risk; check for bunching and consider fuzzy RD with IV/2SLS")
    elif result.get("manipulation_risk") == "medium":
        recommendations.append("Medium manipulation risk; recommend McCrary density test")

    if not recommendations:
        recommendations.append("Strong research opportunity across all dimensions")

    return " | ".join(recommendations)
