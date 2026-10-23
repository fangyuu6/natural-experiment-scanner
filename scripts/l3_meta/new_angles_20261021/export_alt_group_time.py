#!/usr/bin/env python3
"""T232 robustness input (EXPLORATORY, EX-1): group-time ATTs of T227's Callaway-Sant'Anna-style estimator with the OPPOSITE comparison pool
(pipeline never-treated -> not-yet-treated; pipeline not-yet-treated -> never-treated), on the frozen panels, using T227's eclib read-only.
Writes data/l3_meta/new_angles_20261021/inputs/alt_group_time_export.csv. Touches no L2 file.
Run: VECLIB_MAXIMUM_THREADS=1 OMP_NUM_THREADS=1 .venv_did/bin/python -I -B scripts/l3_meta/new_angles_20261021/export_alt_group_time.py"""
import sys, os, json, csv, time
os.environ.setdefault("OMP_NUM_THREADS", "1"); os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1"); os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
from pathlib import Path
import multiprocessing as mp
import numpy as np
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/l3_meta/estimator_check_20261021"))
import eclib as E
GATE = ROOT / "data/l3_meta/refresh/20261021_r1/sweep/GATE_REPORT.json"
OUT = ROOT / "data/l3_meta/new_angles_20261021/inputs/alt_group_time_export.csv"


def one(task):
    did, folder = task
    try:
        o = E.load_primary(did, folder)
        K = len(o.keys)
        pc = o.pipe_control; ac = "nyt" if pc == "never" else "never"
        cells = E.make_cells(o.D, ac, min_controls=o.min_controls, v11=o.v11)
        rows = []
        for c in cells:
            att = float(E.cell_vector(c, o.index, K) @ o.yvec)
            rows.append(dict(design_id=did, alt_control=ac, g=c["g"], event_time=c["event_time"], att=att, n_treated=c["n_treated"], n_control=c["n_control"], post=bool(c["post"])))
        return did, rows, None
    except Exception as ex:
        return did, [], f"{type(ex).__name__}: {ex}"[:300]


if __name__ == "__main__":
    gate = json.load(open(GATE))
    tasks = [(d["design_id"], d["folder"]) for d in gate["designs"]]
    ctx = mp.get_context("spawn")
    allrows, errs = [], {}
    t0 = time.time()
    with ctx.Pool(4) as pool:
        for did, rows, err in pool.imap_unordered(one, tasks, chunksize=1):
            allrows += rows
            if err:
                errs[did] = err
            print(did, len(rows), err or "", flush=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["design_id", "alt_control", "g", "event_time", "att", "n_treated", "n_control", "post"]); w.writeheader(); w.writerows(allrows)
    print("designs with rows", len({r["design_id"] for r in allrows}), "of", len(tasks), "errors", errs, round(time.time() - t0, 1), "s")
