"""T227 part 3b: calibration numbers (D2 over-rejection, kappa spread ratio, D1 families beyond the line, Q1 line-crossers) from a sweep folder.
Pure re-computation with the functions of build_d1 (design_statistics) on npz files; run on the pipeline's own refresh sweep to validate
(must give 24 of 195, kappa 1.293, 72 of 467, 15 of 74 -> 5), then on the alternative-pool sweep.  EXPLORATORY.
Run: .venv_did/bin/python -I -B calib_alt.py --sweep-dir DIR --label NAME --out FILE.json"""
import sys, os, json, csv, math, argparse
from pathlib import Path
from collections import defaultdict
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E
S = E.S
sys.path.insert(0, str(E.ROOT / "scripts/l3_meta"))
import build_d1 as D1  # noqa: E402

REF = E.ROOT / "data/l3_meta/refresh/20261021_r1"


def analyze(sweep_dir, kappas=(1.0,)):
    sweep_dir = Path(sweep_dir)
    gate = json.load(open(REF / "sweep/GATE_REPORT.json"))
    inv_rows = S.inventory_rows(); table = S.catalog_series_table()
    reg_fams = D1.registered_families(inv_rows, table)
    nc = [r for r in csv.DictReader(open(REF / "d2/NC_CELLS_D2_V1.csv"))]
    nc_keys = {(r["design_id"], r["outcome_family"]) for r in nc}
    res = dict(designs=0, eval=0)
    fam_rows, nc_rows, series_z = [], [], defaultdict(list)
    per_design = {}
    rng = np.random.default_rng(20261021)
    refs = []
    for d in gate["designs"]:
        did = d["design_id"]
        p = sweep_dir / (did.replace(":", "_") + ".npz")
        if d["status"] != "PASS" or d.get("adapter") == "annual_full_year_v1" or not p.exists():
            continue
        f = D1.load_design_file(p)
        st = D1.design_statistics(f["sids"], f["fam"], f["code"], f["att"], f["sd"], f["placebo"], reg_fams.get(did, set()))
        res["designs"] += 1
        if st["code"] != "EVAL":
            per_design[did] = dict(code=st["code"], reason=st.get("reason")); continue
        res["eval"] += 1
        per_design[did] = dict(code="EVAL", N=st["N"], mu=st["mu"], tau=st["tau"], Z=st["Z"], p=st["p"])
        for fam, fs in st["family_stats"].items():
            fam_rows.append(dict(design_id=did, family=fam, M=fs["M"], q=fs["q"], beyond=fs["beyond"], r=fs["share_draws_beyond"]))
            if (did, fam) in nc_keys:
                nc_rows.append(dict(design_id=did, family=fam, M=fs["M"], q=fs["q"], beyond=fs["beyond"], r=fs["share_draws_beyond"]))
        # NC series z and random-timing reference columns
        sids = f["sids"]; ix = {s: i for i, s in enumerate(sids)}
        A = [ix[s] for s in st["series_used"]]
        feas = np.isfinite(f["placebo"])
        C = feas[A].all(axis=0)
        PC = f["placebo"][np.ix_(A, np.where(C)[0])]
        sd = PC.std(axis=1, ddof=1)
        z = f["att"][A] / sd
        nc_fams = {fam for (dd, fam) in nc_keys if dd == did}
        sel = [j for j, i in enumerate(A) if f["fam"][i] in nc_fams]
        if sel:
            series_z[did] = (z[sel], (PC[sel] / sd[sel, None]))
    m = len(nc_rows); nb = sum(r["beyond"] for r in nc_rows)
    obs = np.concatenate([v[0] for v in series_z.values()]) if series_z else np.array([])
    # reference: one common draw per design per replicate, pooled SD over NC series
    R = 4000
    sds = []
    for _ in range(R):
        cols = [v[1][:, rng.integers(v[1].shape[1])] for v in series_z.values()]
        sds.append(np.concatenate(cols).std(ddof=1))
    ref = float(np.mean(sds))
    kappa = float(obs.std(ddof=1) / ref) if obs.size > 1 else None
    res["D2"] = dict(m_cells=m, n_beyond=int(nb), pi_NC=nb / m if m else None, rbar=float(np.mean([r["r"] for r in nc_rows])) if m else None,
                     expected_beyond=float(np.sum([r["r"] for r in nc_rows])) if m else None, delta=(nb / m - float(np.mean([r["r"] for r in nc_rows]))) if m else None,
                     n_series=int(obs.size), sd_z=float(obs.std(ddof=1)) if obs.size > 1 else None, ref_sd=ref, kappa=kappa, mean_z=float(obs.mean()) if obs.size else None,
                     series_beyond_own_line=None)
    # D1: families beyond the line at kappa
    res["D1"] = {}
    for k in sorted(set(kappas) | ({kappa} if kappa else set())):
        res["D1"][f"{k:.4f}"] = dict(kappa=k, cells=len(fam_rows), cells_beyond=int(sum(r["M"] > k * r["q"] for r in fam_rows)),
                                    nc_cells_beyond=int(sum(r["M"] > k * r["q"] for r in nc_rows)), nc_cells=len(nc_rows),
                                    expected_cells_beyond=float(sum(r["r"] for r in fam_rows)))
    res["per_design_D1"] = per_design
    res["n_designs_p_le_05"] = sum(1 for v in per_design.values() if v.get("code") == "EVAL" and v["p"] <= 0.05)
    res["n_designs_N_ge_2"] = sum(1 for v in per_design.values() if v.get("code") == "EVAL" and v["N"] >= 2)
    res["theta_mean_Z"] = float(np.mean([v["Z"] for v in per_design.values() if v.get("code") == "EVAL"]))
    res["_nc_rows"] = nc_rows
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep-dir", required=True); ap.add_argument("--label", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    r = analyze(a.sweep_dir)
    r["label"] = a.label
    rows = r.pop("_nc_rows")
    json.dump(r, open(a.out, "w"), indent=1, default=str)
    with open(Path(a.out).with_suffix(".nc_cells.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(json.dumps({k: v for k, v in r.items() if k != "per_design_D1"}, indent=1, default=str)[:3000])

if __name__ == "__main__":
    main()
