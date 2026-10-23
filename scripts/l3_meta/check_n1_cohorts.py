#!/usr/bin/env python3
"""Cross-check of the N1 sets against the run: for every estimated design with sets, compare the cohort labels and sizes of T with
the cohorts (group[].g) and first-cell n_treated of group_time[] in the package's RESULT.json.

Only the cohort label g, event_time, post and n_treated fields are read; no ATT, SE or other value is read or written.
Run:  python3 -I -B scripts/l3_meta/check_n1_cohorts.py  ->  data/l3_meta/n1_sets/check_cohort_sizes_v1.csv
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

# optional: argv[1] = sets file, argv[2] = output csv (default: the V1 sets file and its check file; V2 passes its own paths)
SETS_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else C.ROOT / "data/l3_meta/n1_sets/n1_sets_v1.json"
OUT_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else C.ROOT / "data/l3_meta/n1_sets/check_cohort_sizes_v1.csv"
sets = json.loads(SETS_PATH.read_text())["designs"]
rows = []
for r in sets:
    if r["live_status"] != "estimated":
        continue
    res = json.loads((C.ROOT / r["package"] / "RESULT.json").read_text())["registered_results"]["primary"]["result"]
    gs = sorted(x["g"] for x in res["group"])
    first_event = {}
    size = {}
    for c in res["group_time"]:
        if c.get("post") and (c["g"] not in first_event or c["event_time"] < first_event[c["g"]]):
            first_event[c["g"]] = c["event_time"]
            size[c["g"]] = c["n_treated"]
    mine = {int(g): len(us) for g, us in r["T_cohorts"].items()}
    same_g = sorted(mine) == gs
    same_n = same_g and all(mine[g] == size[g] for g in gs)
    rows.append([r["design_id"], len(gs), len(mine), int(same_g), int(same_n)])
print(C.write_new(OUT_PATH,
                  C.csv_bytes(["design_id", "result_cohorts", "sets_cohorts", "cohort_labels_equal", "cohort_sizes_equal"], rows)))
print("designs", len(rows), "labels equal", sum(r[3] for r in rows), "sizes equal", sum(r[4] for r in rows))
