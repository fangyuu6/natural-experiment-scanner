"""Primary never-control restriction and R6 support; metadata only."""
from .estimator import structural_design
def cohort_preperiod_screen(case, rows, cohorts, operative, exposure_override=None):
    """R6: common observed clean years, after calendar/sample masks; no y access.

    Remove the entire failing in-window cohort from both pools. Future adopters
    are outside this test, and neither partial nor omitted years count as pre.
    """
    window = case["windows"]["primary"]
    included = set(case["sample_mask"]["include_units"])
    excluded = {x['unit'] if isinstance(x, dict) else x
                for x in case["comparison"].get("exclusions", [])}
    mask = {(r[0],r[1]) for r in rows if r[0] in included-excluded
            and window["start"] <= r[1] <= window["end"]
            and r[1] not in window["excluded_periods"]}
    candidates = {u:g for u,g in cohorts.items() if u in included-excluded
                  and g is not None and window["start"] <= g <= window["end"]}
    records = []
    for g in sorted(set(candidates.values())):
        members = sorted(u for u in candidates if candidates[u] == g)
        common = [t for t in range(window["start"],g) if all(
            (u,t) in mask and t < operative[u]
            and (exposure_override is None or exposure_override[u].get(t,{}).get("clean",False))
            for u in members)]
        records.append({"cohort":g,"units":members,"observed_clean_pre_years":common,
                        "observed_clean_pre_n":len(common),"dropped":len(common)<3})
    dropped = sorted(u for record in records if record["dropped"] for u in record["units"])
    return {"ruling":"R6", "minimum_observed_clean_pre_years":3,
            "cohort_checks":records,"excluded_units":dropped,
            "excluded_units_never_controls":True}

def primary_structural_design(case, rows, treatment, *, never_controls, partial_periods_dropped=(), **kwargs):
    """Frozen structural engine plus the exact declared primary never pool.

    A cohort whose only post year is missing otherwise qualifies as a future
    control under the engine's observed-window clean test. Restrict controls to
    the source-certified never list without adding rows or altering exposure.
    This adapter is metadata-only and must also bind any future primary run.
    Registered not-yet-treated variants use their distinct declared control rule.
    """
    window = case["windows"]["primary"]
    dropped = {(r["unit"],r["year"]) for r in partial_periods_dropped}
    excluded = {x['unit'] if isinstance(x, dict) else x
                for x in case["comparison"].get("exclusions", [])}
    rows = [r for r in rows if window["start"] <= r[1] <= window["end"]
            and r[1] not in window["excluded_periods"] and (r[0],r[1]) not in dropped
            and r[0] in case["sample_mask"]["include_units"]
            and r[0] not in excluded]
    r6 = None
    if case.get("cohort_preperiod_rule"):
        r6 = cohort_preperiod_screen(case,rows,{r[0]:r[3] for r in rows},
                                    kwargs["operative"],kwargs.get("exposure_override"))
        rejected = set(r6["excluded_units"]) | set(case["cohort_preperiod_rule"]["excluded_units"])
        rows = [r for r in rows if r[0] not in rejected]
    design = structural_design(case, rows, treatment, **kwargs)
    design["cohort_preperiod_screen"] = r6
    allowed = set(never_controls)
    cells = []
    for cell in design["cells"]:
        controls = [u for u in cell["controls"] if u in allowed]
        if len(controls) >= case.get("min_controls",1):
            cells.append(dict(cell, controls=controls, n_control=len(controls)))
    if 'min_post_periods' in case:
        from ..support import minimum_post_periods
        minimum = minimum_post_periods(case)
        dropped = {g for g in design['groups'] if len({c['t'] for c in cells
                   if c['g']==g and c['post']}) < minimum}
        rejected = {u for u in design['units'] if design['adoption'][u] in dropped}
        if rejected:
            # Rebuild both pools and the anchor mask; removed treated units
            # cannot silently become never-treated controls.
            return primary_structural_design(case, [r for r in rows if r[0] not in rejected],
                treatment, never_controls=never_controls, **kwargs)
    design["cells"] = cells
    design["whole_window_clean"] = {u:clean and u in allowed
                                    for u,clean in design["whole_window_clean"].items()}
    if not any(c["post"] for c in cells):
        raise ValueError("No supported post cells with the declared primary never pool")
    return design
