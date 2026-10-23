#!/usr/bin/env python3
"""T232 Q3 aggregation (EXPLORATORY, EX-1): reads x6_r7 outputs (build_x6_r7.py) and writes q3_design_level.csv, q3_by_family.csv, RESULTS_Q3.json.
Design-level aggregate: per design (>= 8 cells) Spearman(z, G1) minus the mean of the same statistic over its placebo draws, in units of the draws' SD.
Run: .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/run_q3.py"""
import csv, json, math, hashlib, time, sys
from pathlib import Path
import numpy as np
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import na_core as C
ROOT = HERE.parents[2]
X = ROOT / "data/l3_meta/new_angles_20261021/x6_r7"
OUT = ROOT / "data/l3_meta/new_angles_20261021"
keys = {r["design_id"]: r["policy_key"] for r in csv.DictReader(open(OUT / "inputs/policy_keys.csv"))}
X6 = json.load(open(X / "RESULTS_X6_V1.json"))["results"]
LOO = json.load(open(X / "X6_LOO.json"))
BYD = json.load(open(X / "X6_BY_DOMAIN.json"))
rows = list(csv.DictReader(open(X / "X6_DESIGN_LEVEL.csv")))
rng = np.random.default_rng(20261021 + 3)
res = {"question": "Q3 need and gain", "refresh": "data/l3_meta/refresh/20261021_r1"}
p = X6["primary_G1"]; l5 = X6["lag5_G5"]
res["registered_slope"] = dict(n_designs=p["n_designs"], n_series=p["n_series"], n_cells=p["n_cells"], beta=p["beta_observed"], placebo_slope_mean=p["null_mean"], placebo_slope_sd=p["null_sd"],
                               excess=p["excess"], excess_in_null_sd=p["excess_in_null_sd"], p_two_sided=p["rank_p_two_sided"], family_block_ci95=p["family_block_bootstrap_ci95"],
                               family_block_interval_excludes_null_mean=p["family_block_interval_excludes_null_mean"],
                               lag5=dict(beta=l5["beta_observed"], placebo_mean=l5["null_mean"], excess=l5["excess"], excess_in_null_sd=l5["excess_in_null_sd"], p=l5["rank_p_two_sided"]),
                               kappa_scaled=dict(beta=X6["kappa_scaled_G1"]["beta_observed"], excess_in_null_sd=X6["kappa_scaled_G1"]["excess_in_null_sd"], p=X6["kappa_scaled_G1"]["rank_p_two_sided"]),
                               class_none=X6["class_none"], class_channel=X6["class_channel"])
res["earlier_run_r4"] = dict(n_designs=28, beta=0.262, placebo_slope_mean=-0.217, excess=0.479, excess_in_null_sd=2.70, p=0.007)
lo = [(f, v["excess_in_null_sd"], v["beta_observed"]) for f, v in LOO["results"].items() if v.get("feasible")]
res["leave_one_policy_family_out"] = dict(n=len(lo), excess_sd_min=min(x[1] for x in lo), excess_sd_max=max(x[1] for x in lo), slope_min=min(x[2] for x in lo), slope_max=max(x[2] for x in lo),
                                          sign_kept=all(x[2] > 0 and x[1] > 0 for x in lo), lowest_when_dropping=min(lo, key=lambda x: x[1])[0])
# outcome families
fam = []
for f, v in X6["by_family"].items():
    fam.append(dict(outcome_family=f, n_cells=v["n_cells"], n_designs=v["n_designs"], beta=v["beta_observed"], placebo_mean=v["null_mean"], excess_in_null_sd=v["excess_in_null_sd"], p=v["rank_p_two_sided"]))
fam.sort(key=lambda x: -x["excess_in_null_sd"])
with open(OUT / "q3_by_outcome_family.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(fam[0].keys())); w.writeheader(); w.writerows(fam)
res["outcome_families_with_excess_ge_2sd"] = [x for x in fam if x["excess_in_null_sd"] >= 2.0]
res["outcome_families_n"] = len(fam)
# domains
dom = {}
for u, v in BYD["results"].items():
    dom[u] = dict(n_designs=v["n_designs"], n_cells=v["n_cells"], beta=v["beta_observed"], placebo_mean=v["null_mean"], excess_in_null_sd=v["excess_in_null_sd"], p=v["rank_p_two_sided"]) if v.get("feasible") else v
res["by_domain"] = dom
res["domains_too_few"] = {u: n for u, n in BYD["stamp"]["design_domain_counts"].items() if u not in BYD["results"]}
# design level
out = []
for r in rows:
    n = int(r["n_cells"]); real = float(r["spearman_real"]); nm = float(r["spearman_null_mean"]); ns = float(r["spearman_null_sd"])
    out.append(dict(design_id=r["design"], domain=r["domain"], policy_family=r["policy_family"], law=keys.get(r["design"], ""), n_cells=n, spearman_real=real, spearman_placebo_mean=nm,
                    excess=real - nm, excess_z=(real - nm) / ns if ns > 0 else float("nan")))
with open(OUT / "q3_design_level.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
ex = np.array([o["excess"] for o in out]); z = np.array([o["excess_z"] for o in out])
res["design_level"] = dict(n_designs=len(out), median_excess=float(np.median(ex)), share_positive=float(np.mean(ex > 0)), mean_excess_z=float(np.nanmean(z)),
                           approx_z_of_mean=float(np.nanmean(z) * math.sqrt(len(z))), note="designs share states, so the sqrt(n) scaling overstates; block bootstrap below is the guard")
fams = sorted({o["policy_family"] for o in out})
boot = []
for _ in range(9999):
    pick = rng.integers(0, len(fams), len(fams))
    vals = [o["excess"] for g in pick for o in out if o["policy_family"] == fams[g]]
    boot.append(np.median(vals))
res["design_level"]["median_excess_family_block_ci95"] = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]
laws = sorted({o["law"] for o in out})
res["design_level"]["one_vote_per_law"] = dict(n_laws=len(laws), median_excess=float(np.median([np.mean([o["excess"] for o in out if o["law"] == l]) for l in laws])),
                                               share_positive=float(np.mean([np.mean([o["excess"] for o in out if o["law"] == l]) > 0 for l in laws])))
bd = {}
for u in sorted({o["domain"] for o in out}):
    v = [o["excess"] for o in out if o["domain"] == u]
    bd[u] = dict(n=len(v), median_excess=float(np.median(v)), share_positive=float(np.mean(np.array(v) > 0))) if len(v) >= 6 else f"too few ({len(v)})"
res["design_level"]["by_domain"] = bd
pr = dict(excess_ge_2sd=p["excess_in_null_sd"] >= 2.0, family_block_interval_excludes_placebo_mean=bool(p["family_block_interval_excludes_null_mean"]),
          lag5_same_sign_and_excess_positive=bool(l5["excess"] > 0 and l5["beta_observed"] > 0), leave_one_family_out_sign_kept=res["leave_one_policy_family_out"]["sign_kept"])
res["pattern_rule"] = pr; res["pattern"] = bool(all(pr.values()))
stamp = dict(rule_id="EX-1", plan_sha256=hashlib.sha256((ROOT / "docs/plans/l3-meta/PREPLAN_T232_20261021.md").read_bytes()).hexdigest(), run_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             x6_results_sha256=hashlib.sha256((X / "RESULTS_X6_V1.json").read_bytes()).hexdigest())
(OUT / "RESULTS_Q3.json").write_text(json.dumps(dict(stamp=stamp, result=res), indent=1, allow_nan=False, default=float))
print(json.dumps(res["design_level"], indent=1, default=float)[:1500]); print(res["pattern_rule"], res["pattern"])
