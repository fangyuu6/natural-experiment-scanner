"""T227 part 1+2: per-design estimator comparison on the frozen panels (EXPLORATORY, read-only on L2).
Run: .venv_did/bin/python -I -B scripts/l3_meta/estimator_check_20261021/run_designs.py --workers 5 [--only id1,id2] [--no-ri]
Writes data/l3_meta/estimator_check_20261021/PER_DESIGN_ESTIMATORS.csv (+ .json)."""
import sys, os, json, csv, time, argparse, math, traceback
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1"); os.environ.setdefault("MKL_NUM_THREADS", "1")
from pathlib import Path
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E

OUT = E.ROOT / "data/l3_meta/estimator_check_20261021"
GATE = E.ROOT / "data/l3_meta/refresh/20261021_r1/sweep/GATE_REPORT.json"
LIVE = E.ROOT / "data/l3_meta/refresh/20261021_r1/live/l2_set_live.json"


def one(task):
    did, folder, do_ri = task
    t0 = time.time()
    rec = dict(design_id=did, folder=folder, status="ok")
    try:
        o = E.load_primary(did, folder)
        K = len(o.keys); D = o.D
        stored = json.load(open(E.ROOT / folder / "RESULT.json"))["registered_results"]["primary"]
        ov = stored["result"]["overall"]; nf = stored["noise_floor"]
        pc = o.pipe_control; ac = "nyt" if pc == "never" else "never"
        rec["pipe_control"] = pc
        c0 = E.make_cells(D, pc, min_controls=o.min_controls, v11=o.v11)
        key = lambda c: (c["g"], c["t"], c["base_period"], c["n_treated"], c["n_control"], bool(c["post"]))
        rec["cells_reproduce_pipeline"] = [key(c) for c in c0] == [key(c) for c in D["cells"]]
        w0 = E.agg_weights(c0, o.index, K); att0 = float(w0 @ o.yvec)
        a_se = E.overall_se(o, c0)[1]
        post0 = [c for c in c0 if c["post"]]
        rec.update(n_units=len(D["units"]), n_cohorts=len({c["g"] for c in post0}), n_post_cells=len(post0),
                   n_treated_mass=sum(c["n_treated"] for c in post0), n_pre_cells=sum(1 for c in c0 if not c["post"]),
                   window_start=D["window"]["start"], window_end=D["window"]["end"],
                   pipe_att_stored=ov["att"], pipe_att_recomputed=att0, pipe_recompute_absdiff=abs(att0 - ov["att"]),
                   pipe_ci95_lo=(ov.get("ci95") or [None, None])[0], pipe_ci95_hi=(ov.get("ci95") or [None, None])[1],
                   pipe_se_analytic=a_se, pipe_ri_p2_stored=nf["p_two_sided"], pipe_ri_cutoff_stored=nf["absolute_cutoff"],
                   pipe_ri_beyond_stored=bool(nf["strictly_exceeds_cutoff"]))
        # never-treated RI (reproduces the pipeline) -- only checked when asked
        if do_ri:
            dn = E.ri_draws(o, pc)
            ns = E.noise_stats(att0, dn)
            rec.update(pipe_ri_p2_recomputed=ns["p2"], pipe_ri_cutoff_recomputed=ns["cutoff"], pipe_ri_beyond_recomputed=ns["beyond"])
        # CS not-yet-treated
        cy = E.make_cells(D, ac, min_controls=o.min_controls, v11=o.v11)
        posy = [c for c in cy if c["post"]]
        wy = E.agg_weights(cy, o.index, K); atty = float(wy @ o.yvec) if posy else None
        rec["alt_supported"] = bool(posy)
        rec.update(alt_att=atty, alt_se=E.overall_se(o, cy)[1] if posy else None, alt_n_post_cells=len(posy),
                   alt_n_cohorts=len({c["g"] for c in posy}), alt_treated_mass=sum(c["n_treated"] for c in posy),
                   alt_mean_controls=float(np.mean([c["n_control"] for c in posy])) if posy else None, pipe_mean_controls=float(np.mean([c["n_control"] for c in post0])))
        # group aggregation (CS preferred weights) for both pools
        rec["pipe_att_group_agg"] = float(E.agg_weights(c0, o.index, K, "group") @ o.yvec)
        rec["alt_att_group_agg"] = float(E.agg_weights(cy, o.index, K, "group") @ o.yvec) if posy else None
        if do_ri and posy:
            dy = E.ri_draws(o, ac)
            ns = E.noise_stats(atty, dy)
            rec.update(alt_ri_p2=ns["p2"], alt_ri_cutoff=ns["cutoff"], alt_ri_beyond=ns["beyond"], alt_ri_n=ns["n"])
        rec["sa_twfe_comparable"] = (pc == "never")
        # Sun-Abraham (never-treated cohort as control) and TWFE
        try:
            sa, sase, nrow, miss = E.sun_abraham(o, c0)
            rec.update(sa_att=sa, sa_se_cluster=sase, sa_rows=nrow, sa_missing_cells=miss)
        except Exception as ex:
            rec["sa_error"] = f"{type(ex).__name__}: {ex}"[:200]
        try:
            tw, twse, nrow = E.twfe(o, c0)
            rec.update(twfe_att=tw, twfe_se_cluster=twse, twfe_rows=nrow)
            if do_ri:
                dt = E.twfe_ri(o)
                ns = E.noise_stats(tw, dt)
                rec.update(twfe_ri_p2=ns["p2"], twfe_ri_cutoff=ns["cutoff"], twfe_ri_beyond=ns["beyond"])
        except Exception as ex:
            rec["twfe_error"] = f"{type(ex).__name__}: {ex}"[:200]
        rec["honest"] = E.honest_breakdown(o, c0, att0, a_se)
    except Exception as ex:
        rec["status"] = "error"; rec["error"] = f"{type(ex).__name__}: {ex}"[:300]; rec["trace"] = traceback.format_exc()[-800:]
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5); ap.add_argument("--only", default=None); ap.add_argument("--no-ri", action="store_true")
    ap.add_argument("--out", default=str(OUT / "PER_DESIGN_ESTIMATORS"))
    a = ap.parse_args()
    gate = json.load(open(GATE))
    tasks = [(d["design_id"], d["folder"], not a.no_ri) for d in gate["designs"]]
    if a.only:
        keep = set(a.only.split(",")); tasks = [t for t in tasks if t[0] in keep]
    tasks.sort(key=lambda t: -len(t[0]))
    ctx = mp.get_context("spawn")
    recs = []
    t0 = time.time()
    with ctx.Pool(a.workers) as pool:
        for r in pool.imap_unordered(one, tasks, chunksize=1):
            recs.append(r); print(f"[{len(recs)}/{len(tasks)}] {r['design_id']} {r['status']} {r['seconds']}s ({time.time()-t0:.0f}s)", file=sys.stderr, flush=True)
    recs.sort(key=lambda r: r["design_id"])
    json.dump(recs, open(a.out + ".json", "w"), indent=1, default=str)
    flat = []
    for r in recs:
        f = {k: v for k, v in r.items() if k not in ("honest", "trace")}
        for k, v in (r.get("honest") or {}).items():
            if not isinstance(v, (list, dict)):
                f["h_" + k] = v
        flat.append(f)
    cols = []
    for f in flat:
        for k in f:
            if k not in cols:
                cols.append(k)
    with open(a.out + ".csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(flat)

if __name__ == "__main__":
    main()
