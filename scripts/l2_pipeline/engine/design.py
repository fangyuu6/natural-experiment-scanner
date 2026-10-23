"""Build identical topology from generic declarations and observed panel keys.

Calendar labels may be direct indices (batch1) or mapped by period_years
(batches2/3). No design identifiers or policy-specific overrides are embedded.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from . import estimator
from .structure import primary_structural_design
from .exposure import annual_exposure_profile
from .types import Declaration, Design, PanelRow
from .panels import transition_year_drop_enabled, transition_year_keys


def declaration_from_support(source: Mapping[str, Any], summary: Mapping[str, Any],
                             *, key: str, variant: str = "primary",
                             operative_override: Mapping[str, int] | None = None) -> Declaration:
    """Adapt a frozen annual metadata summary without reading outcome values.

    Alternative mechanisms supply explicit registered operative_override; no
    policy-specific treatment choices are inferred from an identifier.
    """
    from ..full_year import effective_full_year_declaration
    source, routed_variant = effective_full_year_declaration(dict(source), variant)
    summary = copy.deepcopy(dict(summary))
    years = summary["period_years"]
    axis = {y: i for i, y in enumerate(years)}
    op = dict(operative_override if operative_override is not None else source["operative"])
    cohorts = {u: c["cohort_index"] for c in summary["cohort_checks"]
               if not c["dropped"] for u in c["members"]}
    group=summary.get('comparison_group', 'not_yet_treated' if routed_variant=='not_yet_controls' else 'never_treated')
    if group == 'not_yet_treated':
        cohorts.update(summary["future_adopters"])
    controls = summary["never_controls"]
    included = sorted((set(cohorts) | set(controls)) - set(summary["metadata_excluded_units"]))
    # Registered start shifts move label and exposure as in the frozen variants.
    delta = {"start_plus_one": 1, "start_minus_one": -1}.get(routed_variant, 0)
    labeled = {u: g + delta for u, g in op.items()}
    clean_onset = {u: min(op[u], labeled[u], source["actual_clean_onset"].get(u, op[u]))
                   for u in op}
    from ..support import minimum_post_periods
    case = {"panel": {"frequency": "annual", "time_basis": {"kind": "calendar_year"}},
            "windows": {"primary": {"start": 0, "end": len(years) - 1,
                        "excluded_periods": [axis[y] for y in summary["calendar_omissions"] if y in axis]}},
            "comparison": {"exclusions": []}, "sample_mask": {"include_units": included},
            "exposure_clock": {"date_column": "operative_date"}}
    if 'min_post_periods' in source or 'min_post_periods' in summary:
        case.update(min_post_periods=minimum_post_periods(source if 'min_post_periods' in source else summary),min_controls=5)
    # Stored legacy summaries without a scalar replay their historical geometry.
    # Every newly constructed summary declares the C51 minimum explicitly.
    if group=='not_yet_treated' and ('comparison_group' in source or 'min_post_periods' in case):
        case.update(v11_min_controls=5,v11_min_post_periods=minimum_post_periods(source),v11_min_pre_periods=3)
    operative = {u: next((axis[y] for y in years if y >= op[u]), len(years))
                 for u in included if u in op}
    exposure = annual_exposure_profile(years, op, clean_onset, cohorts,
                                       source['exposure_end_dates'], included)
    result = Declaration(key=key, variant=variant, case=case, cohorts=cohorts,
                       operative_years=operative, never_controls=controls,
                       exposure=exposure, period_years=years, summary=summary,
                       control_group=group,
                       CT_complete_path_reference_controls=summary.get("CT_complete_path_reference_controls"))
    if transition_year_drop_enabled(source):
        result['transition_year_drop'] = True
        result['transition_effective_years'] = op
    return result


def structural_rows(declaration: Declaration, rows: Sequence[PanelRow]) -> list[PanelRow]:
    case = declaration["case"]
    included = set(case["sample_mask"]["include_units"])
    excluded = {x["unit"] if isinstance(x, dict) else x
                for x in case.get("comparison", {}).get("exclusions", [])}
    axis = {y: i for i, y in enumerate(declaration["period_years"])} if "period_years" in declaration else None
    window = case["windows"]["primary"]
    partial = {(r["unit"], r["year"]) for r in declaration.get("partial_periods_dropped", [])}
    enabled = transition_year_drop_enabled(declaration)
    if enabled and 'transition_effective_years' not in declaration:
        raise ValueError('Transition-year drop requires the effective calendar-year mapping')
    partial |= transition_year_keys(declaration.get('transition_effective_years', {}), enabled)
    result = []
    for u, t, y, _ in rows:
        if u not in included - excluded or (u, t) in partial or (axis is not None and t not in axis):
            continue
        if u in declaration.get('censor_after_periods',{}) and t>declaration['censor_after_periods'][u]:
            continue
        period = axis[t] if axis is not None else t
        # Batch2's raw observed mask includes omitted cells; structural_design
        # preserves those as possible baseline-only cells. Batch1 removes them.
        if axis is None and not (window["start"] <= period <= window["end"]
                                  and period not in window["excluded_periods"]):
            continue
        result.append((u, period, y, declaration["cohorts"].get(u)))
    return result


def treatment_rows(declaration: Declaration) -> dict[str, dict[str, str]]:
    return {u: {"policy_status": "treated" if u in declaration["cohorts"] else "never_treated"}
            for u in declaration["case"]["sample_mask"]["include_units"]}


def build_design(declaration: Declaration, rows: Sequence[PanelRow]) -> Design:
    normalized = structural_rows(declaration, rows)
    case = copy.deepcopy(declaration["case"])
    # R6 was permanently applied during metadata preparation, never screened
    # again on shifted sensitivity labels.
    case.pop("cohort_preperiod_rule", None)
    units = case["sample_mask"]["include_units"]
    operative = {u: declaration["operative_years"].get(u) for u in units}
    periods = sorted({r[1] for r in normalized})
    exposure = copy.deepcopy(declaration.get("exposure")) or {
        u: {t: {"clean": operative[u] is None or t < operative[u],
                "full": u in declaration["cohorts"] and t >= declaration["cohorts"][u]
                and (operative[u] is None or t >= operative[u])} for t in periods} for u in units}
    for u,end in declaration.get('censor_after_periods',{}).items():
        for t,cell in exposure.get(u,{}).items():
            year=declaration['period_years'][t] if 'period_years' in declaration else t
            if year>end: cell.update(clean=False,full=False)
    if declaration.get("allow_partial_start"):
        for u, profile in exposure.items():
            for t, cell in profile.items():
                cell["full"] = u in declaration["cohorts"] and t >= declaration["cohorts"][u]
    control = declaration.get("control_group", "never_treated")
    options = dict(operative=operative, exposure_override=exposure, normalized=True, control_group=control)
    if control == "never_treated":
        result = primary_structural_design(case, normalized, treatment_rows(declaration),
                                           never_controls=declaration["never_controls"], **options)
    else:
        result = estimator.structural_design(case, normalized, treatment_rows(declaration), **options)
    if declaration.get("CT_complete_path_reference_controls") is not None:
        result["CT_complete_path_reference_controls"] = declaration["CT_complete_path_reference_controls"]
    return result
