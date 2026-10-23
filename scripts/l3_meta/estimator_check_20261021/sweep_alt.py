"""T227 part 3b: the outcome sweep (89 + 55 series on the gate-passing designs) re-run with the ALTERNATIVE control pool
(not-yet-treated for never-treated primaries; never-treated for not-yet-treated primaries), same designs, same label bundles,
same series stores.  Writes npz files with the arrays of build_sweep / build_sweep_ext to a scratch folder outside the repo.
EXPLORATORY.  Read-only on every L2 file.  Run:
  .venv_did/bin/python -I -B sweep_alt.py --out-dir /path/to/scratch/scratch/T227/sweep_alt --workers 5 [--only id ...]"""
import sys, os, json, time, argparse, traceback
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from pathlib import Path
from collections import Counter
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E
S = E.S
import build_sweep_ext as X  # noqa: E402

REFRESH = E.ROOT / "data/l3_meta/refresh/20261021_r1/sweep"


def alt_build_ops(declaration, design, bundles, n_draws=S.N_DRAWS, with_cells=False):
    L = S.load_l2()
    keys = sorted(design["mask"]); index = {k: i for i, k in enumerate(keys)}; K = len(keys)
    family = S.family_of(design["cells"])
    if not family:
        raise ValueError("NOT_ASSESSABLE: empty supported pre family")
    pc = "nyt" if design.get("control_group") == "not_yet_treated" else "never"
    ac = pc if os.environ.get("T227_REPLICATE") else ("never" if pc == "nyt" else "nyt")   # T227_REPLICATE=1 reruns the pipeline's own pool (self-test)
    case = declaration["case"]
    mc = case.get("v11_min_controls", case.get("min_controls", 1)) if ("v11_min_controls" in case or "min_post_periods" in case) else 1
    v11 = dict(min_post=case.get("v11_min_post_periods", 2), min_pre=case.get("v11_min_pre_periods", 3)) if "v11_min_controls" in case else None
    cells = E.make_cells(design, ac, groups=design["groups"], min_controls=mc, v11=v11)
    ops = S.Ops()
    ops.keys, ops.index, ops.family = keys, index, family
    ops.cells, ops.cell_ops = [], None
    post = [c for c in cells if c["post"]]
    n = n_draws
    if not post:
        ops.overall = np.zeros(K)
        ops.feasibility = L.ri.inventory([dict(infeasible=True, reason="NOT_ASSESSABLE: alt estimator has no supported post cells")] * n)
        ops.ri_overall = np.zeros((0, K)); ops.draw_index = []
        return ops
    ops.overall = E.agg_weights(cells, index, K)
    rows, draw_idx, records = [], [], []
    for b, labels in enumerate(bundles):
        try:
            fake = L.ri.relabeled_design(declaration, design, labels)
            if S.family_of(fake["cells"]) != family:
                raise ValueError("RI lead inventory changed under relabeling")
            fc = E.make_cells(fake, ac, groups=fake["groups"], min_controls=mc, v11=v11)
            if not any(c["post"] for c in fc):
                raise ValueError("Permutation has no post support")
            rows.append(E.agg_weights(fc, index, K))
        except ValueError as exc:
            if not L.ri.support_failure(exc):
                raise
            records.append(dict(infeasible=True, reason=str(exc)))
        else:
            draw_idx.append(b); records.append(dict(infeasible=False))
    ops.feasibility = L.ri.inventory(records)
    ops.ri_overall = np.array(rows) if rows else np.zeros((0, K))
    ops.draw_index = draw_idx
    return ops


def _save(path, did, recs, res, extra_meta):
    sids = list(recs)
    placebo = np.vstack([recs[k]["placebo"] for k in sids])
    stats = [S.draw_statistics(recs[k]["att"], recs[k]["placebo"]) if recs[k]["code"] in ("SUPPORTED", "RI_FEASIBLE_LT_90") else
             dict(sd=float("nan"), cutoff=float("nan"), p2=float("nan"), z=float("nan")) for k in sids]
    meta = dict(design_id=did, estimator="alternative control pool (T227)", units=res["units"], treated_primary=res["treated_primary"], years=res["years"],
                patterns=res["patterns"], **extra_meta)
    arr = dict(
        series_ids=np.array(sids), family=np.array([recs[k]["family"] for k in sids]), code=np.array([recs[k]["code"] for k in sids]),
        detail=np.array([recs[k]["detail"] for k in sids]), fingerprint=np.array([recs[k]["fingerprint"] for k in sids]),
        n_cells=np.array([recs[k]["n_cells"] for k in sids], dtype=np.int64), n_units=np.array([recs[k]["n_units"] for k in sids], dtype=np.int64),
        kept=np.array([recs[k]["kept"] for k in sids], dtype=float), n_feasible=np.array([recs[k]["n_feasible"] for k in sids], dtype=np.int64),
        support_pass_l2=np.array([-1 if recs[k]["support_pass_l2"] is None else int(recs[k]["support_pass_l2"]) for k in sids], dtype=np.int64),
        strict_dropped=np.array([recs[k]["strict_dropped"] for k in sids], dtype=np.int64),
        att=np.array([recs[k]["att"] for k in sids], dtype=float), sd=np.array([x["sd"] for x in stats], dtype=float),
        cutoff=np.array([x["cutoff"] for x in stats], dtype=float), p2=np.array([x["p2"] for x in stats], dtype=float),
        z=np.array([x["z"] for x in stats], dtype=float), placebo=placebo, meta_json=np.array(json.dumps(S.clean(meta), sort_keys=True)))
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **arr)
    return dict(codes=dict(Counter(recs[k]["code"] for k in sids)), n_patterns=len(res["patterns"]))


def task(t):
    did, folder, scale_mode, out_dir = t
    t0 = time.time()
    out = dict(design_id=did, status="ERROR")
    try:
        S.load_l2()
        S.build_ops = alt_build_ops          # sweep_design resolves build_ops from sweep_core globals
        ctx = S.load_design_context(did, E.ROOT / folder)
        # V1 series
        store1 = S.SeriesStore(REFRESH / "series_store_v1.npz", REFRESH / "series_store_v1_count.npz").view(scale_mode)
        table1 = S.catalog_series_table()
        table1 = {sid: table1[sid] for sid in S.SeriesStore(REFRESH / "series_store_v1.npz", REFRESH / "series_store_v1_count.npz").sids}
        res1 = S.sweep_design(ctx, store1, table1)
        o1 = _save(Path(out_dir) / "v1" / (did.replace(":", "_") + ".npz"), did, res1["records"], res1, dict(scale_mode=scale_mode, set="v1"))
        if os.environ.get("T227_NO_EXT"):
            out.update(status="OK", v1=o1, seconds=round(time.time() - t0, 1)); return out
        # extension series
        sx = S.SeriesStore(X.EXT_STORE)
        store2 = sx.view("rate")
        table2 = X.ext_series_table()
        res2 = S.sweep_design(ctx, store2, table2)
        o2 = _save(Path(out_dir) / "ext" / (did.replace(":", "_") + ".npz"), did, res2["records"], res2, dict(scale_mode="rate", set="ext"))
        out.update(status="OK", v1=o1, ext=o2)
    except Exception as ex:
        out["error"] = f"{type(ex).__name__}: {str(ex)[:400]}"; out["trace"] = traceback.format_exc()[-1500:]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True); ap.add_argument("--workers", type=int, default=5); ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    gate = json.load(open(REFRESH / "GATE_REPORT.json"))
    todo = []
    for d in gate["designs"]:
        if d["status"] != "PASS" or d.get("adapter") == "annual_full_year_v1":
            continue
        if a.only and d["design_id"] not in a.only:
            continue
        todo.append((d["design_id"], d["folder"], d.get("scale_mode") or "rate", a.out_dir))
    todo.sort(key=lambda t: -len(t[0]))
    ctx = mp.get_context("spawn")
    res = []
    t0 = time.time()
    with ctx.Pool(a.workers) as pool:
        for r in pool.imap_unordered(task, todo, chunksize=1):
            res.append(r); print(f"[{len(res)}/{len(todo)}] {r['design_id']} {r['status']} {r.get('seconds')}s {r.get('error','')[:200]} ({time.time()-t0:.0f}s)", file=sys.stderr, flush=True)
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    json.dump(sorted(res, key=lambda r: r["design_id"]), open(Path(a.out_dir) / "SWEEP_ALT_RUN.json", "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
