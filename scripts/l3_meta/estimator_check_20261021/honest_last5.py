"""T227: HonestDiD-style breakdown using only the last 5 pre event times (EXPLORATORY).  Run from repo root with .venv_did python -I -B."""
import sys, os, json, csv
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
from pathlib import Path
import multiprocessing as mp
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E

def one(t):
    did, folder, att, se = t
    try:
        o = E.load_primary(did, folder)
        c0 = E.make_cells(o.D, o.pipe_control, min_controls=o.min_controls, v11=o.v11)
        h = E.honest_breakdown(o, c0, att, se, last_k=5)
        return dict(design_id=did, **{("h5_" + k): v for k, v in h.items() if not isinstance(v, (list, dict))})
    except Exception as ex:
        return dict(design_id=did, error=str(ex)[:200])

if __name__ == "__main__":
    rows = list(csv.DictReader(open(E.ROOT / "data/l3_meta/estimator_check_20261021/PER_DESIGN_ESTIMATORS.csv")))
    tasks = [(r["design_id"], r["folder"], float(r["pipe_att_recomputed"]), float(r["pipe_se_analytic"])) for r in rows if r["status"] == "ok"]
    with mp.get_context("spawn").Pool(3) as pool:
        res = pool.map(one, tasks)
    cols = []
    for r in res:
        for k in r:
            if k not in cols: cols.append(k)
    with open(E.ROOT / "data/l3_meta/estimator_check_20261021/HONEST_LAST5.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(sorted(res, key=lambda r: r["design_id"]))
