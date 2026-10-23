#!/usr/bin/env python3
"""Independent check of the ACS 2010-2019 sequence route of the extension store (SWEEP-EXT-V1): the raw Census files, read with csv and zipfile only.

check_sweep_ext_independent.py reads the derived whole-state files that L2's sequence route writes.  This check goes one step further back: for every state and every
year 2010-2019 it takes the lookup file of the year (sequence number and start position of the table), the state geography file (the whole-state record, summary level
040, component 00, and its LOGRECNO), and the estimate member of the sequence zip, and rebuilds two series from the raw cells: acs1_poverty_rate_age_all
(100 x B17001 cell 2 / cell 1) and acs1_median_household_income_age_all (B19049 cell 1).  It compares them with series_store_ext_v1.npz cell by cell.  It imports no builder
and no L2 module.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_sweep_ext_acs_sequence.py --out data/l3_meta/sweep_ext_v1/ACS_SEQUENCE_CHECK_EXT_V1.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/replication/shared_data/census-acs1-summary/acs1-sequence-by-state-2010-2019/raw"
STATES = "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
SENTINELS = {-111111111, -222222222, -333333333, -555555555, -666666666, -888888888, -999999999}


def lookup(year, table):
    """(sequence number, start position, number of cells) of a table from the year's lookup file, in either of the two layouts of the Census files."""
    with open(RAW / f"acs_lookup_{year}.txt", encoding="latin-1", newline="") as fh:
        rows = list(csv.DictReader(fh))
    hit = []
    for r in rows:
        if r.get("Table ID") != table:
            continue
        if "Start Position" in r:
            start, seq, cells = r["Start Position"].strip(), r["Sequence Number"], r["Total Cells in Table"]
        else:
            start, seq, cells = r["position"].strip(), r["seq"], r["cells"]
        if start not in ("", "."):
            hit.append((int(seq), int(start), int(cells.split()[0])))
    assert len(hit) == 1, (year, table, hit)
    return hit[0]


def logrec(year, st):
    for ext in ("csv", "txt"):
        p = RAW / f"g{year}1{st.lower()}.{ext}"
        if p.is_file():
            break
    else:
        raise FileNotFoundError(f"geography file {year} {st}")
    found = []
    with open(p, newline="", encoding="latin-1") as fh:
        for rec in csv.reader(fh):
            if len(rec) >= 10 and rec[2:4] == ["040", "00"]:
                found.append(rec[4])
    assert len(found) == 1, (year, st, found)
    return found[0]


def cells(year, st, table):
    seq, start, count = lookup(year, table)
    lr = logrec(year, st)
    with zipfile.ZipFile(RAW / f"{year}1{st.lower()}{seq:04d}000.zip") as zf:
        with zf.open(f"e{year}1{st.lower()}{seq:04d}000.txt") as raw, io.TextIOWrapper(raw, encoding="latin-1", newline="") as fh:
            picked = [rec for rec in csv.reader(fh) if len(rec) >= 6 and rec[5] == lr]
    assert len(picked) == 1, (year, st, table, len(picked))
    vals = picked[0][start - 1:start - 1 + count]
    return [None if (v.strip() == "" or int(float(v)) in SENTINELS) else float(v) for v in vals]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--store", default=str(ROOT / "data/l3_meta/sweep_ext_v1/series_store_ext_v1.npz"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists: not overwritten")
    z = np.load(args.store, allow_pickle=False)
    units = [str(u) for u in z["units"]]
    years = [int(y) for y in z["years"]]
    ui = {u: i for i, u in enumerate(units)}
    yi = {y: i for i, y in enumerate(years)}
    results = []
    for sid, table, build in (("acs1_poverty_rate_age_all", "B17001", lambda c: 100.0 * c[1] / c[0] if c[0] and c[1] is not None else None),
                              ("acs1_median_household_income_age_all", "B19049", lambda c: c[0])):
        ok, val = z[f"{sid}__ok"], z[f"{sid}__val"]
        n = bad = 0
        examples = []
        for y in range(2010, 2020):
            for st in STATES:
                v = build(cells(y, st, table))
                n += 1
                i, j = ui[st], yi[y]
                if v is None:
                    if ok[i, j]:
                        bad += 1
                        examples.append((st, y, "store eligible, raw cell missing"))
                elif not ok[i, j] or abs(val[i, j] - v) > 1e-9 * max(1.0, abs(v)):
                    bad += 1
                    examples.append((st, y, float(val[i, j]), v))
        results.append(dict(series=sid, table=table, state_years_compared=n, disagreements=bad, examples=examples[:5]))
    report = dict(rule_id="EX-1", version="SWEEP-EXT-V1", check="ACS 2010-2019 sequence route rebuilt from the raw zips, geography files and lookups", python=sys.version.split()[0],
                  numpy=np.__version__, results=results, disagreements=sum(r["disagreements"] for r in results))
    with open(out, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps(dict(disagreements=report["disagreements"], compared=[r["state_years_compared"] for r in results]), indent=1))
    return 1 if report["disagreements"] else 0


if __name__ == "__main__":
    sys.exit(main())
