"""T227: summary tables from PER_DESIGN_ESTIMATORS.csv (+ live set, N1).  EXPLORATORY.  Writes PER_DESIGN_SUMMARY.json and a flag CSV."""
import csv, json, math, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/l3_meta/estimator_check_20261021"
rows = list(csv.DictReader(open(OUT / "PER_DESIGN_ESTIMATORS.csv")))
live = {d["design_id"]: d for d in json.load(open(ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"))["designs"]}
n1 = {d["design_id"]: d for d in json.load(open(ROOT / "data/l3_meta/refresh/20261021_r1/n1/RESULTS_N1_V2.json"))["designs"]}
def f(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception:
        return None
def b(x): return {"True": True, "False": False}.get(x)
out = []
for r in rows:
    if r["status"] != "ok":
        continue
    d = dict(design_id=r["design_id"], source_group=live.get(r["design_id"], {}).get("source_group"), family=live.get(r["design_id"], {}).get("policy_family"),
             domain=live.get(r["design_id"], {}).get("domain"), n1_code=n1.get(r["design_id"], {}).get("code"),
             pipe_control=r["pipe_control"], n_cohorts=int(r["n_cohorts"]), staggered=int(r["n_cohorts"]) > 1, n_units=int(r["n_units"]))
    att, se = f(r["pipe_att_recomputed"]), f(r["pipe_se_analytic"])
    d.update(pipe_att=att, pipe_se=se, pipe_beyond=b(r.get("pipe_ri_beyond_recomputed", r.get("pipe_ri_beyond_stored"))), pipe_p2=f(r.get("pipe_ri_p2_recomputed")),
             recompute_absdiff=f(r["pipe_recompute_absdiff"]), cells_reproduce=b(r["cells_reproduce_pipeline"]),
             alt_supported=b(r.get("alt_supported")), alt_att=f(r.get("alt_att")), alt_se=f(r.get("alt_se")), alt_beyond=b(r.get("alt_ri_beyond")), alt_p2=f(r.get("alt_ri_p2")),
             alt_mass_share=(f(r.get("alt_treated_mass")) or 0) / f(r["n_treated_mass"]) if f(r["n_treated_mass"]) else None,
             alt_group_att=f(r.get("alt_att_group_agg")), pipe_group_att=f(r.get("pipe_att_group_agg")),
             sa_att=f(r.get("sa_att")), sa_comparable=b(r.get("sa_twfe_comparable")),
             twfe_att=f(r.get("twfe_att")), twfe_beyond=b(r.get("twfe_ri_beyond")), twfe_p2=f(r.get("twfe_ri_p2")),
             h_status=r.get("h_status"), h_n_pre=f(r.get("h_n_pre")), bd_level_point=f(r.get("h_bd_level_point")), bd_level_ci=f(r.get("h_bd_level_ci")),
             bd_slope_point=f(r.get("h_bd_slope_point")), bd_slope_ci=f(r.get("h_bd_slope_ci")))
    d["alt_ratio"] = d["alt_att"] / att if (d["alt_att"] is not None and att) else None
    d["twfe_ratio"] = d["twfe_att"] / att if (d["twfe_att"] is not None and att) else None
    d["group_alt_ratio"] = d["alt_group_att"] / d["pipe_group_att"] if (d["alt_group_att"] is not None and d["pipe_group_att"]) else None
    sg = lambda x: None if x is None else (x > 0) - (x < 0)
    d["alt_sign_flip"] = (sg(d["alt_att"]) != sg(att)) if d["alt_att"] is not None else None
    d["twfe_sign_flip"] = (sg(d["twfe_att"]) != sg(att)) if d["twfe_att"] is not None else None
    d["alt_ri_change"] = (None if d["alt_beyond"] is None else ("lost" if d["pipe_beyond"] and not d["alt_beyond"] else "gained" if d["alt_beyond"] and not d["pipe_beyond"] else "same"))
    d["twfe_ri_change"] = (None if d["twfe_beyond"] is None else ("lost" if d["pipe_beyond"] and not d["twfe_beyond"] else "gained" if d["twfe_beyond"] and not d["pipe_beyond"] else "same"))
    d["pipe_z"] = att / se if se else None
    out.append(d)
cols = list(out[0])
with open(OUT / "PER_DESIGN_FLAGS.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)

def stats(sel, label):
    s = dict(label=label, n=len(sel))
    ratios = [x["alt_ratio"] for x in sel if x["alt_ratio"] is not None]
    tr = [x["twfe_ratio"] for x in sel if x["twfe_ratio"] is not None]
    s["alt_ratio_median"] = float(np.median(ratios)) if ratios else None
    s["alt_ratio_q25_q75"] = [float(np.percentile(ratios, 25)), float(np.percentile(ratios, 75))] if ratios else None
    s["alt_within_10pct"] = sum(0.9 <= x <= 1.1 for x in ratios); s["alt_n"] = len(ratios)
    s["alt_sign_flips"] = sum(1 for x in sel if x["alt_sign_flip"])
    s["alt_sign_flips_ids"] = [x["design_id"] for x in sel if x["alt_sign_flip"]]
    s["alt_ri_lost"] = [x["design_id"] for x in sel if x["alt_ri_change"] == "lost"]
    s["alt_ri_gained"] = [x["design_id"] for x in sel if x["alt_ri_change"] == "gained"]
    s["pipe_beyond"] = sum(1 for x in sel if x["pipe_beyond"]); s["alt_beyond"] = sum(1 for x in sel if x["alt_beyond"])
    s["twfe_ratio_median"] = float(np.median(tr)) if tr else None
    s["twfe_ratio_q25_q75"] = [float(np.percentile(tr, 25)), float(np.percentile(tr, 75))] if tr else None
    s["twfe_within_10pct"] = sum(0.9 <= x <= 1.1 for x in tr); s["twfe_n"] = len(tr)
    s["twfe_sign_flips"] = sum(1 for x in sel if x["twfe_sign_flip"])
    s["twfe_sign_flips_ids"] = [x["design_id"] for x in sel if x["twfe_sign_flip"]]
    s["twfe_ri_lost"] = [x["design_id"] for x in sel if x["twfe_ri_change"] == "lost"]
    s["twfe_ri_gained"] = [x["design_id"] for x in sel if x["twfe_ri_change"] == "gained"]
    s["twfe_beyond"] = sum(1 for x in sel if x["twfe_beyond"])
    sa = [abs(x["sa_att"] - x["pipe_att"]) for x in sel if x["sa_att"] is not None and x["sa_comparable"]]
    s["sa_max_absdiff_vs_pipe"] = max(sa) if sa else None; s["sa_n"] = len(sa)
    return s
summary = dict(
    all=stats(out, "all registered designs"),
    staggered_never_primary=stats([x for x in out if x["staggered"] and x["pipe_control"] == "never"], "staggered, pipeline pool never-treated"),
    staggered_nyt_primary=stats([x for x in out if x["staggered"] and x["pipe_control"] == "nyt"], "staggered, pipeline pool not-yet-treated (alt = never)"),
    single_cohort=stats([x for x in out if not x["staggered"]], "single cohort"),
    loop29=stats([x for x in out if x["source_group"] == "autopilot_estimate" and x["staggered"]], "autopilot_estimate, staggered"),
    n1_eval_staggered=stats([x for x in out if x["n1_code"] == "EVAL" and x["staggered"]], "N1 EVAL, staggered"),
    reproduction=dict(max_recompute_absdiff=max(x["recompute_absdiff"] for x in out), cells_reproduce=sum(1 for x in out if x["cells_reproduce"]), n=len(out)),
)
hon = [x for x in out if x["h_status"] == "OK"]
def q(vals): 
    v = [x for x in vals if x is not None]
    return dict(n=len(v), median=float(np.median(v)) if v else None, lt1=sum(x < 1 for x in v), lt05=sum(x < 0.5 for x in v), eq0=sum(x == 0 for x in v))
summary["honest"] = dict(n_with_ge3_pre=len(hon), n_total=len(out), level_point=q([x["bd_level_point"] for x in hon]), level_ci=q([x["bd_level_ci"] for x in hon]),
                         slope_point=q([x["bd_slope_point"] for x in hon]), slope_ci=q([x["bd_slope_ci"] for x in hon]),
                         beyond_designs=dict(n=sum(1 for x in hon if x["pipe_beyond"]), level_point=q([x["bd_level_point"] for x in hon if x["pipe_beyond"]]), slope_point=q([x["bd_slope_point"] for x in hon if x["pipe_beyond"]])))
json.dump(summary, open(OUT / "PER_DESIGN_SUMMARY.json", "w"), indent=1)
print(json.dumps(summary, indent=1)[:6000])
