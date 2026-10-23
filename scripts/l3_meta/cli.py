#!/usr/bin/env python3
"""Proposal-only L3 metadata/power tools; never ingest or estimate outcomes."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
from statistics import NormalDist
import sys

HERE = Path(__file__).resolve().parent
DOMAINS = {"labor", "public_finance", "health", "crime", "education", "environment", "other"}
STATUSES = {"pending", "ready", "estimated", "stopped", "unestimated"}


def keys(value, required, optional=(), path="record"):
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f"{path}: missing={sorted(missing)}, unexpected={sorted(extra)}")


def string(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: expected nonempty string")


def strings(value, path):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}: expected nonempty string array")
    for item in value:
        string(item, path)
    if len(set(value)) != len(value):
        raise ValueError(f"{path}: duplicate identifiers")


def digest(value, path):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{path}: expected lowercase SHA-256")


def timestamp(value, path):
    string(value, path)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{path}: expected ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{path}: timestamp needs timezone")
    return parsed


def probability(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{path}: expected finite probability in [0,1]")


def validate_metadata(bundle):
    """Strict allowlist: no run.result, estimates, noise arrays, or outcome panels."""
    keys(bundle, ["schema_version", "designs"])
    if bundle["schema_version"] != "l3-meta-metadata-v1":
        raise ValueError("schema_version: unsupported")
    if not isinstance(bundle["designs"], list):
        raise ValueError("designs: expected array")
    seen = set()
    counts = {status: 0 for status in sorted(STATUSES)}
    prospective = 0
    for i, design in enumerate(bundle["designs"]):
        path = f"designs[{i}]"
        keys(design, ["design_id", "registry_design_id", "policy_id", "policy_family_id", "source_text_ids", "domain", "design_type", "status", "enrolled_at", "status_updated_at", "terminal_at", "stop_reason", "exposed_before_prereg", "spec_sha256", "outcomes", "forecast", "literature", "adoption", "missing_reasons", "provenance"], path=path)
        for field in ["design_id", "registry_design_id", "policy_id", "policy_family_id"]:
            string(design[field], f"{path}.{field}")
        if design["design_id"] != design["registry_design_id"]:
            raise ValueError(f"{path}: design identity must equal registry identity")
        if design["design_id"] in seen:
            raise ValueError(f"{path}: duplicate design_id")
        seen.add(design["design_id"])
        strings(design["source_text_ids"], f"{path}.source_text_ids")
        if design["domain"] not in DOMAINS or design["design_type"] not in {"did", "rdd", "rct", "rct_lottery"} or design["status"] not in STATUSES:
            raise ValueError(f"{path}: invalid domain, design type, or status")
        if type(design["exposed_before_prereg"]) is not bool:
            raise ValueError(f"{path}.exposed_before_prereg: expected boolean")
        prospective += not design["exposed_before_prereg"]
        counts[design["status"]] += 1
        enrolled = timestamp(design["enrolled_at"], f"{path}.enrolled_at")
        updated = timestamp(design["status_updated_at"], f"{path}.status_updated_at")
        if updated < enrolled:
            raise ValueError(f"{path}: status update precedes enrollment")
        if design["terminal_at"] is not None:
            terminal = timestamp(design["terminal_at"], f"{path}.terminal_at")
            if terminal < enrolled or terminal > updated:
                raise ValueError(f"{path}: terminal timestamp outside enrollment/status window")
            if design["status"] not in {"estimated", "stopped", "unestimated"}:
                raise ValueError(f"{path}: nonterminal status has terminal timestamp")
        elif design["status"] in {"estimated", "stopped", "unestimated"}:
            raise ValueError(f"{path}: terminal disposition needs terminal timestamp")
        if design["status"] == "stopped":
            string(design["stop_reason"], f"{path}.stop_reason")
        elif design["stop_reason"] is not None:
            raise ValueError(f"{path}.stop_reason: only stopped designs carry a stop reason")
        reasons = design["missing_reasons"]
        if not isinstance(reasons, dict):
            raise ValueError(f"{path}.missing_reasons: expected object")
        nullable = {"terminal_at", "spec_sha256", "forecast", "literature", "adoption", "outcome.outcome_id", "outcome.definition", "outcome.unit", "outcome.sign_alignment"}
        if reasons.keys() - nullable:
            raise ValueError(f"{path}.missing_reasons: unknown nullable field")
        for field, reason in reasons.items():
            string(reason, f"{path}.missing_reasons.{field}")
        def check_missing(field, value):
            if value is None and field not in reasons:
                raise ValueError(f"{path}.{field}: null needs explicit missing reason")
            if value is not None and field in reasons:
                raise ValueError(f"{path}.{field}: populated field cannot be marked missing")
        for field in ["terminal_at", "spec_sha256", "forecast", "literature", "adoption"]:
            check_missing(field, design[field])
        if design["spec_sha256"] is not None:
            digest(design["spec_sha256"], f"{path}.spec_sha256")
        outcomes = design["outcomes"]
        if not isinstance(outcomes, list) or len(outcomes) != 1:
            raise ValueError(f"{path}.outcomes: exactly one prespecified primary outcome per design")
        outcome = outcomes[0]
        keys(outcome, ["outcome_id", "definition", "unit", "sign_alignment"], path=f"{path}.outcomes[0]")
        for field in ["outcome_id", "definition", "unit", "sign_alignment"]:
            check_missing(f"outcome.{field}", outcome[field])
        for field in ["outcome_id", "definition", "unit"]:
            if outcome[field] is not None:
                string(outcome[field], f"{path}.outcome.{field}")
        if outcome["sign_alignment"] is not None and (type(outcome["sign_alignment"]) is not int or outcome["sign_alignment"] not in {-1, 1}):
            raise ValueError(f"{path}.outcome.sign_alignment: expected +1/-1 or null")
        provenance = design["provenance"]
        keys(provenance, ["registry_snapshot_sha256", "source_record_ids", "metadata_reviewed_at"], path=f"{path}.provenance")
        digest(provenance["registry_snapshot_sha256"], f"{path}.provenance.registry_snapshot_sha256")
        strings(provenance["source_record_ids"], f"{path}.provenance.source_record_ids")
        string(provenance["metadata_reviewed_at"], f"{path}.provenance.metadata_reviewed_at")
        forecast = design["forecast"]
        if forecast is not None:
            keys(forecast, ["ledger_sha256", "locked_at", "lock_verified_before_estimation", "estimator_target", "probability_semantics", "forecasters"], ["threshold_spec_id"], f"{path}.forecast")
            digest(forecast["ledger_sha256"], f"{path}.forecast.ledger_sha256")
            for field in ["locked_at", "estimator_target"]:
                string(forecast[field], f"{path}.forecast.{field}")
            if type(forecast["lock_verified_before_estimation"]) is not bool:
                raise ValueError(f"{path}.forecast: lock verification must be boolean")
            semantics = forecast["probability_semantics"]
            if semantics not in {"positive_effect", "exclusive_noise_categories"}:
                raise ValueError(f"{path}.forecast: unknown probability semantics")
            if semantics == "exclusive_noise_categories":
                string(forecast.get("threshold_spec_id"), f"{path}.forecast.threshold_spec_id")
            elif "threshold_spec_id" in forecast:
                raise ValueError(f"{path}.forecast: positive-only forecasts have no noise threshold")
            forecasts = forecast["forecasters"]
            if not isinstance(forecasts, list) or not forecasts:
                raise ValueError(f"{path}.forecast.forecasters: expected nonempty array")
            forecaster_ids = set()
            for f in forecasts:
                fields = ["forecaster_id", "p_positive"] if semantics == "positive_effect" else ["forecaster_id", "p_exceeds_positive", "p_inside_noise", "p_exceeds_negative"]
                keys(f, fields, path=f"{path}.forecast.forecasters")
                string(f["forecaster_id"], f"{path}.forecast.forecaster_id")
                if f["forecaster_id"] in forecaster_ids:
                    raise ValueError(f"{path}.forecast: duplicate forecaster")
                forecaster_ids.add(f["forecaster_id"])
                for field in fields[1:]:
                    probability(f[field], f"{path}.forecast.{field}")
                if semantics == "exclusive_noise_categories" and not math.isclose(sum(f[field] for field in fields[1:]), 1.0, abs_tol=1e-9):
                    raise ValueError(f"{path}.forecast: exclusive probabilities must sum to one")
        for field, fields in [("literature", ["match_id", "source_url", "comparison_spec_sha256", "match_verified"]), ("adoption", ["covariate_spec_id", "source_record_ids", "lock_sha256"])]:
            value = design[field]
            if value is None:
                continue
            keys(value, fields, path=f"{path}.{field}")
            for name in fields:
                if name.endswith("sha256"):
                    digest(value[name], f"{path}.{field}.{name}")
                elif name == "source_record_ids":
                    strings(value[name], f"{path}.{field}.{name}")
                elif name == "match_verified":
                    if type(value[name]) is not bool:
                        raise ValueError(f"{path}.{field}.{name}: expected boolean")
                else:
                    string(value[name], f"{path}.{field}.{name}")
    return {"registry_designs": len(seen), "prospective_unexposed": prospective, "status_counts": counts, "outcome_associations_computed": False}


def binomial_tail(n, k, p):
    if k > n:
        return 0.0
    return math.fsum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))


def exact_binomial_design(n, alpha=0.01, null_rate=0.05, target_power=0.80):
    k = next((k for k in range(n + 1) if binomial_tail(n, k, null_rate) <= alpha), n + 1)
    lo, hi = null_rate, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binomial_tail(n, k, mid) < target_power:
            lo = mid
        else:
            hi = mid
    return {"reject_at_or_above": k, "size_at_null": binomial_tail(n, k, null_rate), "rate_for_80_percent_power": hi if k <= n else None}


def power_table():
    zcrit = NormalDist().inv_cdf(0.995)
    zpower = NormalDist().inv_cdf(0.80)
    rows = []
    # Rounded roots of P(chi-square_5(lambda=N*w^2) > q_.99) = .80.
    # Calculated from artificial planning assumptions, not project estimates.
    six_domain_reference = {50: 0.598, 100: 0.423, 29: 0.785, 79: 0.476}
    for n in [50, 100, 29, 79]:
        rows.append({"eligible_independent_designs": n, "paired_mean_mde_in_sd": (zcrit + zpower) / math.sqrt(n), "balanced_two_group_mde_in_sd": 2 * (zcrit + zpower) / math.sqrt(n), "correlation_mde_fisher_approx": math.tanh((zcrit + zpower) / math.sqrt(n - 3)), "proportion_99_percent_worst_case_half_width_normal_approx": zcrit * 0.5 / math.sqrt(n), "q1_exact_one_sided_binomial_vs_0_05": exact_binomial_design(n)})
    return {"alpha_per_question": 0.01, "target_power": 0.8, "input_type": "analytic_design_assumptions_only", "rows": rows, "q5_ideal_independent_chi_square_df5_cohen_w_80_percent": six_domain_reference, "limits": ["N50/N100 are hypothetical eligible independent designs; with 21 exposed, total milestones permit at most 29/79 pristine designs.", "Matched literature, locked probability forecasts, valid variance and adoption coverage can further reduce eligible N.", "Shared policy family, state panels and outcomes reduce effective N; these calculations are optimistic independent-design benchmarks.", "Paired MDE applies to standardized paired differences with unknown SD; two-group MDE is a contrast benchmark, not six-domain omnibus power.", "Q5 tabulated w roots are rounded ideal df5 references; the primary wave/family score test requires a frozen synthetic simulation with actual allocation, missingness, rank and dependence assumptions before execution.", "Q4 variance correction needs unbiased point estimates, valid sampling variance and artificial recovery checks.", "Q1 binomial size assumes independent null exceedances with probability at most .05; failed placebo exchangeability invalidates that calibration."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    commands.add_parser("power")
    validate = commands.add_parser("validate-metadata")
    validate.add_argument("path", type=Path, help="explicit normalized metadata JSON, never a result bundle")
    commands.add_parser("estimate", help="reserved; always refuses")
    args = parser.parse_args(argv)
    if args.command == "estimate":
        print("NOT_IMPLEMENTED: estimation is disabled; signed freeze and reviewed outcome adapter are required.", file=sys.stderr)
        return 2
    try:
        if args.command == "plan":
            result = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
        elif args.command == "power":
            result = power_table()
        else:
            result = validate_metadata(json.loads(args.path.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
