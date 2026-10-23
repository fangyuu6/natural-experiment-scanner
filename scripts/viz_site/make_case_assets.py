"""Read Protocol v2 display sidecars without opening declarations or outcomes.

The sidecar has already applied the objective Other estimates row rule. Keep
those rows, aliases, diagnostics and stopped records exactly as supplied.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


# SELECTION_FREEZE.md order; selection never depends on an estimate or tier.
CASE_IDS = (
    "cigarette_tax", "adult_use_cannabis", "primary_seat_belt", "shall_issue",
    "minimum_wage_step", "right_to_work", "renewable_portfolio",
    "death_penalty_abolition", "salary_history_ban", "smoke_free_air",
)
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "results" / "cases"


class CaseAssetsError(ValueError):
    """A present sidecar cannot safely be displayed."""


def _require(condition, message):
    if not condition:
        raise CaseAssetsError(message)


def _pair(value, name):
    _require(isinstance(value, dict) and set(value) == {"en", "zh"}
             and all(isinstance(v, str) and v.strip() for v in value.values()),
             name + " requires nonempty EN/ZH labels")


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _interval(value, name):
    _require(isinstance(value, list) and len(value) == 2
             and all(_number(v) for v in value) and value[0] <= value[1],
             name + " requires an ordered finite interval")


def _scalar(record, name):
    _require(isinstance(record, dict), name + " must be an object")
    _require(isinstance(record.get("id"), str) and record["id"], name + " lacks its row identity")
    for field in ("label", "outcome", "population", "unit"):
        _pair(record.get(field), name + "." + field)
    _require(_number(record.get("estimate")), name + " lacks a finite estimate")
    _interval(record.get("interval"), name)
    periods = record.get("periods", record.get("calendar_years"))
    _require(isinstance(periods, list) and periods
             and all(type(p) is int for p in periods), name + " lacks period inventory")
    if "period_labels" in record:
        labels = record["period_labels"]
        _require(isinstance(labels, dict) and set(labels) == {"en", "zh"}
                 and all(isinstance(v, list) and len(v) == len(periods)
                         and all(isinstance(label, str) and label for label in v)
                         for v in labels.values()), name + " has invalid period labels")
    for field in ("interval_label", "comparison_line", "source_caption", "clock_precision_note"):
        if field in record:
            _pair(record[field], name + "." + field)
    if "ids" in record:
        _require(isinstance(record["ids"], list) and record["ids"]
                 and all(isinstance(i, str) and i for i in record["ids"])
                 and record["id"] in record["ids"], name + " has invalid source identities")


def validate_case_sidecar(record, case_id):
    """Validate the display v1 contract; retain optional fields without rewriting."""
    _require(isinstance(record, dict), "Sidecar must be an object")
    _require(type(record.get("version")) is int and record["version"] == 1,
             "Unsupported case sidecar schema version")
    _require(record.get("case_id") == case_id, "Case sidecar filename/identity mismatch")
    _require(record.get("data_kind") in ("real", "synthetic"), "Unknown sidecar data kind")
    status = record.get("status")
    _require(status in ("PUBLISHABLE_EXAMPLE", "INTERNAL_ONLY"), "Unknown case status")
    _require(type(record.get("shown")) is bool
             and record["shown"] == (status == "PUBLISHABLE_EXAMPLE"),
             "Case status/shown mismatch")
    for field in ("policy", "treatment", "estimand_line", "source_caption"):
        _pair(record.get(field), field)
    primary = record.get("primary_estimates")
    _require(isinstance(primary, dict), "primary_estimates must be an object")
    _require(not record["shown"] or bool(primary), "Shown case lacks a headline estimate")
    if record["shown"]:
        design = record.get("design")
        _require(isinstance(design, dict), "Shown case lacks design inventory")
        for key in ("treated_jurisdictions", "adoption_groups", "never_treated",
                    "not_yet_treated_or_future", "periods", "excluded_by_rule_count",
                    "year_corroborated_count"):
            _require(type(design.get(key)) is int and design[key] >= 0,
                     "Publishable sidecar lacks required design count: " + key)
        if 'primary_comparison_jurisdictions' in design:
            count = design['primary_comparison_jurisdictions']
            info = design.get('primary_comparison_metadata', {})
            _require(type(count) is int and count >= 0 and
                     isinstance(info.get('jurisdictions'), list) and
                     len(set(info['jurisdictions'])) == len(info['jurisdictions']) == count,
                     'Invalid primary comparison inventory')
        _require(design["periods"] > 0, "Shown case lacks included periods")
        for key in ("first_period", "last_period"):
            _pair(design.get(key), "design." + key)
        checks = record.get("checks")
        _require(isinstance(checks, dict), "Shown case lacks checks block")
        for key, fields in (("pretrend", ("joint_p_value", "joint_limit", "economic_size", "economic_limit")),
                            ("placebo", ("reading", "limit"))):
            values = checks.get(key)
            _require(isinstance(values, dict), "Missing checks." + key)
            for field in fields:
                _require(values.get(field) is None or _number(values[field]), "Invalid checks." + key + "." + field)
        for field in ("joint_passed", "economic_passed"):
            _require(checks["pretrend"].get(field) is None or type(checks["pretrend"][field]) is bool,
                     "Invalid pretrend verdict")
        _require(checks["placebo"].get("passed") is None or type(checks["placebo"]["passed"]) is bool,
                 "Invalid placebo verdict")
        if checks.get("thin_support_note") is not None:
            _pair(checks["thin_support_note"], "checks.thin_support_note")
        calibration = checks.get("calibration")
        if calibration is not None:
            _require(isinstance(calibration, dict), "Invalid checks.calibration")
            rate = calibration.get("clean_world_pass_rate_pct")
            _require(rate is None or _number(rate) and 0 <= rate <= 100, "Invalid calibration pass rate")
            _require(calibration.get("minimum_detectable_effect") is None or
                     _number(calibration["minimum_detectable_effect"]), "Invalid calibration MDE")
            if calibration.get("unit") is not None:
                _pair(calibration["unit"], "checks.calibration.unit")
            coverage = calibration.get('point_interval_coverage')
            if coverage is not None:
                _require(isinstance(coverage, dict) and type(coverage.get('covered')) is int and
                         type(coverage.get('repetitions')) is int and coverage['repetitions'] > 0 and
                         0 <= coverage['covered'] <= coverage['repetitions'] and
                         _number(coverage.get('rate')) and 0 <= coverage['rate'] <= 1 and
                         abs(coverage['rate'] - coverage['covered'] / coverage['repetitions']) < 1e-12 and
                         _number(coverage.get('nominal_level')) and 0 < coverage['nominal_level'] <= 1 and
                         isinstance(coverage.get('method'), str) and
                         coverage.get('scope') == 'pooled_declared_main_outcome_point_intervals' and
                         isinstance(coverage.get('outcome_columns'), list) and bool(coverage['outcome_columns']) and
                         isinstance(coverage.get('window'), dict) and isinstance(coverage.get('periods'), list),
                         'Invalid point interval coverage scope/counts')
            mdd = calibration.get("mdd90") or {}
            _require(isinstance(mdd, dict), "Invalid calibration mdd90")
            for world in ("anticipation_jump", "linear_pretrend"):
                threshold = mdd.get(world, {}).get("full_blocking_set")
                if threshold is not None:
                    _require(isinstance(threshold, dict) and
                             (threshold.get("value") is None or _number(threshold["value"])) and
                             isinstance(threshold.get("units"), str), "Invalid calibration threshold")
    for identity, scalar in primary.items():
        _scalar(scalar, "primary_estimates." + identity)
        _require(scalar["id"] == identity, "Headline row identity mismatch")
    other = record.get("other_estimates")
    _require(isinstance(other, list) and len(other) <= 8,
             "other_estimates must retain the objective rule's maximum eight rows")
    for index, row in enumerate(other):
        name = "other_estimates." + str(index)
        _require(isinstance(row, dict), name + " must be an object")
        if row.get("kind") == "range":
            _interval(row.get("point_range"), name + ".point_range")
            for field in ("label", "outcome", "population", "unit"):
                _pair(row.get(field), name + "." + field)
            _require(isinstance(row.get("ids"), list) and bool(row["ids"])
                     and all(isinstance(i, str) and i for i in row["ids"]),
                     name + " lacks diagnostic source identities")
            _require(row.get("headline_id") in primary, name + " references an unknown headline")
        else:
            _scalar(row, name)
    figures = record.get("figures")
    _require(isinstance(figures, dict) and set(primary).issubset(figures),
             "Headline figure bindings are missing")
    for identity, figure in figures.items():
        _require(identity in primary and isinstance(figure, dict), "Unknown figure identity")
        _pair(figure.get("caption"), "figures." + identity + ".caption")
        _require(isinstance(figure.get("points"), list), "Figure points must be a list")
        seen = set()
        for point in figure["points"]:
            _require(isinstance(point, dict) and _number(point.get("event_time"))
                     and float(point["event_time"]).is_integer()
                     and point["event_time"] not in seen and _number(point.get("estimate")),
                     "Invalid or duplicate figure event point")
            seen.add(point["event_time"])
            _require(type(point.get("treated_support")) is int and point["treated_support"] >= 0
                     and type(point.get("hollow")) is bool
                     and point["hollow"] == (point["treated_support"] < 5),
                     "Invalid figure support marker")
            band = point.get("band")
            if band is not None:
                _interval(band, "Figure band")
            _require(not point["hollow"] or band is None, "Thin-support point must not have a band")
    reasons = record.get("stop_reasons")
    _require(isinstance(reasons, list), "stop_reasons must be a list")
    _require(record["shown"] or bool(reasons), "Stopped case lacks a reason")
    for reason in reasons:
        _pair(reason, "stop_reasons")
    if "registered_results" in record:
        _require(isinstance(record["registered_results"], dict), "registered_results must be an object")
        for identity, row in record["registered_results"].items():
            _scalar(row, "registered_results." + identity)
            _require(row["id"] == identity, "Registered row identity mismatch")
    if "visible_disagreements" in record:
        _require(isinstance(record["visible_disagreements"], list), "visible_disagreements must be a list")
        for disagreement in record["visible_disagreements"]:
            _require(isinstance(disagreement, dict) and isinstance(disagreement.get("id"), str),
                     "Invalid visible disagreement")
            _pair(disagreement.get("label"), "visible_disagreements.label")
            _pair(disagreement.get("line"), "visible_disagreements.line")
    if "other_estimates_caption" in record:
        _pair(record["other_estimates_caption"], "other_estimates_caption")
    return record


def benchmark_tier(record):
    """Recover only a tier explicitly expressed by case_sidecar's exact copy.

    Sidecar v1 records the computed tier in comparison_line rather than in a
    separate field. Unknown copy yields None, never an inferred comparison.
    """
    if record.get('comparison_tier') in ('TIGHT', 'DIRECTIONAL', 'NOT_CONSISTENT', 'NOT_COMPARABLE'):
        return record['comparison_tier']
    line = record.get("comparison_line", record)
    if not isinstance(line, dict) or not isinstance(line.get("en"), str):
        return None
    text = line["en"]
    if text == "No comparable published point estimate for this window." or text.startswith((
            "Context only: published study concerns a different estimand; no comparable point estimate.",
            "Context: published estimate ",
            "Published study reports exclusion bounds; no comparable point estimate.",
            "Published study reports exclusion bounds. No comparable point estimate.",
            "Published exclusion bound: ")):
        return "NOT_COMPARABLE"
    if text.startswith("Published "):
        if "; within the preset tolerance of ±" in text and text.endswith("."):
            return "TIGHT"
        if text.endswith("; same sign and overlapping intervals, outside the preset tolerance."):
            return "DIRECTIONAL"
        if "; this estimate is not consistent with the published estimate." in text:
            return "NOT_CONSISTENT"
    return None


def load_case_selection(directory=None):
    """Read every present *_site.json, in the ten-case frozen order.

    No missing file is manufactured into a stopped result. Missing files count
    toward the specified selection but have no result or display status yet.
    """
    directory = Path(directory) if directory is not None else DEFAULT_DIRECTORY
    cases = []
    if directory.exists():
        _require(directory.is_dir(), "Case sidecar directory is not a directory")
        paths = {p.name[:-len("_site.json")]: p for p in directory.glob("*_site.json")}
        unknown = sorted(set(paths) - set(CASE_IDS))
        _require(not unknown, "Unknown case sidecar identity: " + ", ".join(unknown))
        for identity in CASE_IDS:
            if identity not in paths:
                continue
            path = paths[identity]
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                cases.append(validate_case_sidecar(record, identity))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise CaseAssetsError(str(path) + ": " + str(exc)) from exc
    shown = sum(case["shown"] for case in cases)
    return dict(cases=cases, specified_count=len(CASE_IDS), present_count=len(cases),
                shown_count=shown, stopped_count=len(cases) - shown,
                specified_case_ids=list(CASE_IDS),
                present_case_ids=[case["case_id"] for case in cases])


def merge_case_selection(batch1, case_selection):
    """Keep batch 1 first and preserve the legacy path when no cases exist."""
    merged = dict(batch1)
    if not case_selection["cases"]:
        return merged
    first = {record["registered_id"]: (record, shown)
             for shown, records in ((True, batch1["examples"]), (False, batch1["omitted"]))
             for record in records}
    order = ((batch1.get("contract") or {}).get("specified_example_ids")
             or list(first))
    entries = [dict(batch=1, id=identity, shown=first[identity][1], record=first[identity][0])
               for identity in order if identity in first]
    entries.extend(dict(batch=2, id=record["case_id"], shown=record["shown"], record=record)
                   for record in case_selection["cases"])
    merged.update(cases=case_selection["cases"], case_selection=case_selection,
                  entries=entries,
                  prepared=batch1["prepared"] + case_selection["specified_count"],
                  shown=batch1["shown"] + case_selection["shown_count"])
    return merged
