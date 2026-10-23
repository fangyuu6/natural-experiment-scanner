"""T227 part 4: power / minimum detectable effect from the pipeline's own noise floor, and a recovery pilot by effect injection.
EXPLORATORY.  Read-only on L2.

(a) MDE for every gate-passing design, from the stored 999 placebo ATTs (noise_floor.permutation_draws): for an injected constant effect
    tau at a real-structure assignment the estimate is N_b + tau (N_b = placebo ATT of label draw b; the estimator is linear and exactly
    unbiased for a constant effect), and the pipeline's 'beyond noise' rule is |ATT| > q95(|N|).  power(tau) = P_b(|N_b + tau| > q95(|N|));
    MDE80 = smallest tau with power >= 0.8 (positive and negative direction, the larger is reported).  Also 2.8 x analytic SE.
(b) Pilot (5 designs): the effect is injected at the REAL adoption timing into the frozen panel for four profiles (constant, ramp,
    cohort-heterogeneous, decaying) and re-estimated with (1) the pipeline estimator (operator) and (2) static TWFE: recovery ratio =
    (estimate(y+inj) - estimate(y)) / estimand of the injected profile.  Then, over the 999 label draws, the same profiles are injected at the draw's
    own (relabelled) adoption timing and the detection rate and sign-correct rate are tabulated.  One real-engine injection per design (the
    pipeline's own estimate_design and permutation_reference) confirms the operator arithmetic.
Run: .venv_did/bin/python -I -B power.py --workers 3 --pilot id1,id2,id3,id4,id5"""
import sys, os, json, csv, time, argparse, traceback
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1"); os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from pathlib import Path
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E
S = E.S
OUT = E.ROOT / "data/l3_meta/estimator_check_20261021"
GATE = E.ROOT / "data/l3_meta/refresh/20261021_r1/sweep/GATE_REPORT.json"


def power_curve(draws, taus):
    d = np.asarray(draws, float); d = d[np.isfinite(d)]
    c = np.sort(np.abs(d))[int(np.ceil(0.95 * d.size)) - 1]
    return c, np.array([[np.mean(np.abs(d + s * t) > c) for t in taus] for s in (1, -1)])


def mde80(draws):
    d = np.asarray(draws, float); d = d[np.isfinite(d)]
    sd = d.std(ddof=1)
    taus = np.linspace(0, 8 * sd, 1601)
    c, P = power_curve(d, taus)
    out = {}
    for name, row in zip(("pos", "neg"), P):
        ok = np.where(row >= 0.8)[0]
        out[name] = float(taus[ok[0]]) if ok.size else float("nan")
    return dict(cutoff=float(c), sd=float(sd), mde80_pos=out["pos"], mde80_neg=out["neg"], mde80=max(out["pos"], out["neg"]),
                power_at_sd=float(P[0][np.argmin(abs(taus - sd))]))


def all_designs_task(t):
    did, folder = t
    rec = dict(design_id=did)
    try:
        r = json.load(open(E.ROOT / folder / "RESULT.json"))
        prim = r["registered_results"]["primary"]
        nf = prim["noise_floor"]; ov = prim["result"]["overall"]
        m = mde80(nf["permutation_draws"])
        s_pre = r.get("l3_export_v2", {}).get("s_pre")
        ci = ov.get("ci95") or [None, None]
        se = (ci[1] - ci[0]) / 3.92 if ci[0] is not None else None
        rec.update(att=ov["att"], ri_cutoff=m["cutoff"], ri_sd=m["sd"], mde80_ri=m["mde80"], mde80_ri_pos=m["mde80_pos"], mde80_ri_neg=m["mde80_neg"],
                   se_multiplier=se, mde80_se=2.8 * se if se else None, s_pre=s_pre,
                   mde80_ri_in_s_pre=m["mde80"] / s_pre if s_pre else None,
                   att_in_mde=ov["att"] / m["mde80"], null_within_noise=not bool(nf["strictly_exceeds_cutoff"]),
                   n_draws=len(nf["permutation_draws"]))
    except Exception as ex:
        rec["error"] = f"{type(ex).__name__}: {ex}"[:200]
    return rec


# ------------------------------------------------------------------ pilot
def profile(kind, e, g, rng_c):
    if kind == "constant":
        return 1.0
    if kind == "ramp":
        return min(1.0, (e + 1) / 4.0)
    if kind == "heterogeneous":
        return rng_c[g]
    if kind == "decay":
        return 2.0 * np.exp(-0.35 * e)
    raise ValueError(kind)


def injection(o, D, kind, cohort_factor):
    """vector over o.keys: profile at treated post unit-periods of design D (full exposure, supported cohorts)"""
    v = np.zeros(len(o.keys))
    sup = set(D["groups"])
    for u in D["units"]:
        g = D["adoption"][u]
        if g is None or g not in sup:
            continue
        for t in D["periods"]:
            if t >= g and D["exposure"][u].get(t, {}).get("full", False) and (u, t) in o.index:
                v[o.index[(u, t)]] = profile(kind, t - g, g, cohort_factor)
    return v


def pilot_task(t):
    did, folder, tau_mult = t
    rec = dict(design_id=did, status="ok", rows=[])
    try:
        L = S.load_l2()
        o = E.load_primary(did, folder)
        K = len(o.keys); D = o.D
        cells = E.make_cells(D, o.pipe_control, min_controls=o.min_controls, v11=o.v11)
        w = E.agg_weights(cells, o.index, K)
        att0 = float(w @ o.yvec)
        r = json.load(open(E.ROOT / folder / "RESULT.json"))
        nf = r["registered_results"]["primary"]["noise_floor"]
        m = mde80(nf["permutation_draws"])
        tau0 = m["mde80"]
        rng = np.random.default_rng(20261021)
        cohort_factor = {g: float(f) for g, f in zip(D["groups"], rng.choice([0.4, 1.0, 1.6], size=len(D["groups"])))}
        # relabelled operators and designs
        R, idx, _ = E.ri_draws(o, o.pipe_control, "simple", return_ops=True)
        fakes = []
        family = S.family_of(D["cells"])
        for b, labels in enumerate(S.own_bundles(D, 999)):
            if b not in set(idx):
                continue
            fakes.append(L.ri.relabeled_design(o.decl, D, labels))
        rows = []
        Nb = R @ o.yvec
        c = np.sort(np.abs(Nb))[int(np.ceil(0.95 * Nb.size)) - 1]
        for kind in ("constant", "ramp", "heterogeneous", "decay"):
            inj = injection(o, D, kind, cohort_factor)
            ibs = [injection(o, fk, kind, cohort_factor) for fk in fakes]
            Rib = [float(R[j] @ ibs[j]) for j in range(len(fakes))]
            truth_means = [float(ib[ib > 0].mean()) if (ib > 0).any() else 0.0 for ib in ibs]
            truth_est = float(inj[inj > 0].mean())   # target: mean injected effect over all treated full-exposure unit-periods (tau = 1)
            # TWFE at real timing
            tw0 = E.twfe(o, cells, se=False)[0]
            class O2: pass
            for mult in tau_mult:
                tau = mult * tau0
                y2 = o.yvec + tau * inj
                att_p = float(w @ y2)
                o2 = O2(); o2.D, o2.min_controls = o.D, o.min_controls
                o2.y = {k: float(y2[i]) for i, k in enumerate(o.keys)}
                tw1 = E.twfe(o2, cells, se=False)[0]
                tw_truth = tau * truth_est
                # detection over relabelled draws: each draw b injects the same profile at its own assignment
                det = sgn = 0; rec_ratio = []
                for j, fk in enumerate(fakes):
                    a_b = float(Nb[j] + tau * Rib[j])
                    truth_b = tau * truth_means[j]
                    det += abs(a_b) > c
                    sgn += (abs(a_b) > c) and (a_b > 0)
                    rec_ratio.append((a_b - Nb[j]) / truth_b if truth_b else np.nan)
                rows.append(dict(design_id=did, profile=kind, tau_mult_of_mde80=mult, tau=tau, mean_injected_effect=tau * truth_est, mde80=tau0, att_real=att0,
                                 mean_injected_in_mde80=tau * truth_est / tau0,
                                 recovery_pipeline_real_timing=(att_p - att0) / tw_truth if tw_truth else None,
                                 recovery_twfe_real_timing=(tw1 - tw0) / tw_truth if tw_truth else None,
                                 power_relabelled=det / len(fakes), power_right_sign=sgn / len(fakes),
                                 mean_recovery_relabelled=float(np.nanmean(rec_ratio)), n_draws=len(fakes)))
        rec["rows"] = rows
        # real-engine injection (constant, tau = MDE80): confirm the arithmetic with the pipeline's own functions
        inj = injection(o, D, "constant", cohort_factor)
        out2 = {k: float(o.yvec[i] + tau0 * inj[i]) for i, k in enumerate(o.keys)}
        fit = L.estimator.estimate_design(D, out2, B=2)
        ref = L.ri.permutation_reference(o.decl, D, out2, count=999)
        att_e = fit["overall"]["att"]
        cut_e = float(np.sort(np.abs(ref["overall"]))[int(np.ceil(0.95 * len(ref["overall"]))) - 1])
        rec["engine_check"] = dict(att_engine=att_e, att_operator=float(w @ (o.yvec + tau0 * inj)), abs_diff=abs(att_e - float(w @ (o.yvec + tau0 * inj))),
                                   engine_beyond_noise=bool(abs(att_e) > cut_e), engine_cutoff=cut_e, tau=tau0)
    except Exception as ex:
        rec["status"] = "error"; rec["error"] = f"{type(ex).__name__}: {ex}"[:300]; rec["trace"] = traceback.format_exc()[-800:]
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3); ap.add_argument("--pilot", default=None); ap.add_argument("--skip-all", action="store_true")
    a = ap.parse_args()
    gate = json.load(open(GATE))
    designs = [(d["design_id"], d["folder"]) for d in gate["designs"]]
    ctx = mp.get_context("spawn")
    if not a.skip_all:
        with ctx.Pool(a.workers) as pool:
            recs = pool.map(all_designs_task, designs)
        cols = []
        for r in recs:
            for k in r:
                if k not in cols: cols.append(k)
        with open(OUT / "MDE_ALL_DESIGNS.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(sorted(recs, key=lambda r: r["design_id"]))
        print("all-designs MDE done", len(recs), file=sys.stderr)
    if a.pilot:
        ids = a.pilot.split(",")
        folders = {d: f for d, f in designs}
        tasks = [(i, folders[i], (0.5, 1.0, 1.5)) for i in ids]
        with ctx.Pool(min(a.workers, len(tasks))) as pool:
            res = pool.map(pilot_task, tasks)
        rows = [row for r in res for row in r.get("rows", [])]
        if rows:
            with open(OUT / "POWER_PILOT.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
        json.dump([{k: v for k, v in r.items() if k != "rows"} for r in res], open(OUT / "POWER_PILOT_ENGINE_CHECK.json", "w"), indent=1, default=str)
        for r in res: print(r["design_id"], r["status"], r.get("engine_check"), r.get("error", ""), file=sys.stderr)

if __name__ == "__main__":
    main()
