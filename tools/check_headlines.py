#!/usr/bin/env python3
"""Re-derive the headline numbers in README.md from the files in results/. Standard library only.

    python3 tools/check_headlines.py

Exits 0 when every number matches, 1 otherwise.
"""
import csv, json, sys
from collections import Counter
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "results"
ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(("ok   " if good else "FAIL ") + f"{label}: {got}" + ("" if good else f"  (README says {want})"))


# L1: screening funnel
f = json.loads((R / "l1_funnel_counts.json").read_text())
check("L1 policy texts screened", f["total"], 483414)
check("L1 candidates after five rules", f["stages"][-1]["n_out"], 7781)
fr = f["by_source_family"]["Federal Regulations"]["n"][-1]
check("L1 candidates that are federal regulations", fr, 5994)

# L2: decision table (one row per design, latest conclusion)
rows = list(csv.DictReader(open(R / "l2_design_decisions.csv", encoding="utf-8")))
st = Counter(r["status"] for r in rows)
check("L2 designs processed", len(rows), 170)
check("L2 designs with an estimate", st["estimated"], 91)
check("L2 designs not evaluable", st["not_evaluable"], 79)
gates = Counter(r["gate"] for r in rows if r["status"] == "not_evaluable")
check("L2 not evaluable at the support gate", gates["support"], 55)
hand = sum(1 for r in rows if r["route"] in ("prototype", "manual_batch", "registry_terminal"))
auto = sum(1 for r in rows if r["route"] in ("autopilot_estimate", "autopilot_park"))
early = sum(1 for r in rows if r["route"] in ("early_s3_stop", "pre_run_stop"))
check("L2 run by hand / by the automated loop / stopped before a run", (hand, auto, early), (48, 112, 10))
check("L2 automated runs with an estimate", sum(1 for r in rows if r["route"] == "autopilot_estimate"), 43)

# Re-estimation of published papers
rep = list(csv.DictReader(open(R / "replication_reestimates.csv", encoding="utf-8")))
c = Counter(r["category"] for r in rep)
check("Papers re-estimated", len(rep), 53)
check("Within 10% of the published figure", c["within_10pct"], 36)
check("Same direction / not comparable / opposite sign", (c["same_direction"], c["not_comparable"], c["opposite_sign"]), (8, 8, 1))

# L3: the lead exploratory finding
q3 = json.loads((R / "l3/new_angles_20261021/RESULTS_Q3.json").read_text())["result"]["registered_slope"]
check("L3 slope (designs, outcome series)", (q3["n_designs"], q3["n_series"]), (50, 106))
check("L3 slope, rounded", round(q3["beta"], 2), 0.33)
check("L3 arithmetic-only slope, rounded", round(q3["placebo_slope_mean"], 2), -0.20)
check("L3 distance in null SDs, rounded", round(q3["excess_in_null_sd"], 1), 3.9)

sys.exit(0 if ok else 1)
