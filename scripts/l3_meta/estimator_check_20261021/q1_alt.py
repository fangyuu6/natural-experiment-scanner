"""T227: Q1 line-crossers (designs whose estimate is beyond its own random-timing line) under the alternative control pool and TWFE.
74 = 56 registry designs (re-estimated here) + 18 archive designs (original rho kept).  EXPLORATORY.
Run: .venv_did/bin/python -I -B q1_alt.py --kappa-alt 1.xx --out FILE.json"""
import csv, json, math, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/l3_meta/estimator_check_20261021"
ap = argparse.ArgumentParser(); ap.add_argument("--kappa-alt", type=float, required=True); ap.add_argument("--out", default=str(OUT / "Q1_ALT.json"))
a = ap.parse_args()
cal = json.load(open(ROOT / "data/l3_meta/refresh/20261021_r1/cal/RESULTS_CAL_V1.json"))
kap = cal["question"]["CAL"]["kappa"]["kappa_hat"]
base = {r["design_id"]: r for r in cal["designs_q1"]}
per = {r["design_id"]: r for r in csv.DictReader(open(OUT / "PER_DESIGN_ESTIMATORS.csv")) if r["status"] == "ok"}
def f(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception: return None
rows = []
for did, b in base.items():
    row = dict(design_id=did, group3=b["group3"], rho_pipeline=b["rho"], swept=b["swept"])
    p = per.get(did)
    if p:
        att, cut = f(p.get("alt_att")), f(p.get("alt_ri_cutoff"))
        row["rho_alt"] = abs(att) / cut if att is not None and cut else None
        tw, tcut = f(p.get("twfe_att")), f(p.get("twfe_ri_cutoff"))
        row["rho_twfe"] = abs(tw) / tcut if tw is not None and tcut else None
        row["alt_supported"] = p.get("alt_supported")
    rows.append(row)
def count(key, k, only=None):
    n = kk = 0
    for r in rows:
        v = r.get(key)
        if only == "registry" and r["design_id"] not in per: continue
        if v is None: continue
        n += 1; kk += v > k
    return n, kk
out = dict(kappa_pipeline_hat=kap, kappa_alt_hat=a.kappa_alt)
for name, key in (("pipeline", "rho_pipeline"), ("alt_pool", "rho_alt"), ("twfe", "rho_twfe")):
    block = {}
    for label, k in (("kappa_1", 1.0), ("kappa_1.29_pipeline", kap), ("kappa_alt", a.kappa_alt)):
        # 74-design mix: registry designs from this estimator, archive designs keep the pipeline value
        n = kk = 0
        for r in rows:
            v = r.get(key) if r["design_id"] in per else r["rho_pipeline"]
            if key != "rho_pipeline" and r["design_id"] in per and v is None:
                v = None
            if v is None: continue
            n += 1; kk += v > k
        n56, k56 = count(key, k, "registry")
        block[label] = dict(kappa=k, n74=n, k74=kk, n56=n56, k56=k56)
    out[name] = block
out["expected_74_nominal"] = 3.7
json.dump(out, open(a.out, "w"), indent=1)
with open(Path(a.out).with_suffix(".csv"), "w", newline="") as fh:
    cols = sorted({k for r in rows for k in r}); w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
print(json.dumps(out, indent=1))
