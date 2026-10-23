"""T227: outcome scale of every design (SD and mean of clean pre unit-years of the primary outcome), to express MDE in outcome SDs and as a share of the mean.  EXPLORATORY."""
import sys, os, csv, json
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
from pathlib import Path
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import eclib as E

def one(t):
    did, folder = t
    try:
        o = E.load_primary(did, folder)
        vals = [o.y[(u, tt)] for u in o.D["units"] for tt in o.D["periods"] if (u, tt) in o.y and o.D["exposure"][u].get(tt, {}).get("clean", False)]
        v = np.array(vals)
        tr = {u for c in o.D["cells"] if c["post"] for u in c["treated"]}
        tv = np.array([o.y[(u, tt)] for u in tr for tt in o.D["periods"] if (u, tt) in o.y and o.D["exposure"][u].get(tt, {}).get("clean", False)])
        return dict(design_id=did, sd_clean=float(v.std(ddof=1)), mean_clean=float(v.mean()), mean_treated_pre=float(tv.mean()) if tv.size else None, n=int(v.size))
    except Exception as ex:
        return dict(design_id=did, error=str(ex)[:200])

if __name__ == "__main__":
    gate = json.load(open(E.ROOT / "data/l3_meta/refresh/20261021_r1/sweep/GATE_REPORT.json"))
    with mp.get_context("spawn").Pool(3) as pool:
        res = pool.map(one, [(d["design_id"], d["folder"]) for d in gate["designs"]])
    cols = []
    for r in res:
        for k in r:
            if k not in cols: cols.append(k)
    with open(E.ROOT / "data/l3_meta/estimator_check_20261021/OUTCOME_SCALE.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(sorted(res, key=lambda r: r["design_id"]))
