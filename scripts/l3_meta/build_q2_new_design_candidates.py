#!/usr/bin/env python3
"""L3 meta, Q2 input for the next coding round (2026-10-16): benchmark candidates for the designs ingested after the Q2 roster cutoff.

The 20 cards of intake/bulk_20261015/queue_v3_next20/cards were ingested at 2026-10-15T19:42-19:45Z (CODE_TASK_LAST_END: INGEST_B1015) and are in history coding;
none is estimated.  Q2 requires the strict matches to be locked by two blinded extractors BEFORE a design's outcome exists, so this list is the input of that round.
It lists, for every design, the studies the card itself cites (first_order.literature_refs: authors, year, DOI, title, journal, the fit label the card author gave and
the locator text), plus the last ledger event of the design.  It codes NOTHING: the fit label is the card author's context citation, not a Q2 match.

Reads (read-only): the 20 card files, data/l2_pipeline/registry/ACTIVE_DESIGNS.json, the tail of AUTOPILOT_LEDGER.jsonl.  Writes (once)
data/l3_meta/q2_v2/Q2_NEW_DESIGN_BENCHMARK_CANDIDATES_20261016.csv.  No outcome value is read; no network.

Run:  python3 -I -B scripts/l3_meta/build_q2_new_design_candidates.py
"""
from __future__ import annotations

import csv
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CARDS = ROOT / "data/l2_pipeline/intake/bulk_20261015/queue_v3_next20/cards"
ACTIVE = ROOT / "data/l2_pipeline/registry/ACTIVE_DESIGNS.json"
LEDGER = ROOT / "data/l2_pipeline/registry/AUTOPILOT_LEDGER.jsonl"
OUT = ROOT / "data/l3_meta/q2_v2/Q2_NEW_DESIGN_BENCHMARK_CANDIDATES_20261016.csv"
FIELDS = ["design_id", "in_active_designs", "last_ledger_event", "policy_family", "outcome_series", "outcome_unit", "window", "paper", "year", "journal", "doi",
          "card_fit_label", "card_fit_yes", "locator", "next_step"]


def main():
    if OUT.exists():
        sys.exit("output exists: not overwritten")
    active = {d["design_id"] for d in json.loads(ACTIVE.read_text())["designs"]}
    last = {}
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("design_id"):
            last[r["design_id"]] = r
    rows = []
    for p in sorted(glob.glob(str(CARDS / "*.json"))):
        c = json.loads(Path(p).read_text())
        did = c["design_id"]
        ev = last.get(did) or {}
        ev_txt = f"{ev.get('action')} {ev.get('decision')} {ev.get('time', '')[:19]}Z" if ev else ""
        for lit in c["first_order"].get("literature_refs", []):
            rows.append(dict(design_id=did, in_active_designs=did in active, last_ledger_event=ev_txt, policy_family=c["policy_family"],
                             outcome_series=c["outcome"]["series_id"], outcome_unit=c["outcome"].get("outcome_unit"), window=f"{c['outcome']['years'][0]}-{c['outcome']['years'][1]}",
                             paper=lit.get("authors"), year=lit.get("year"), journal=lit.get("journal"), doi=lit.get("doi"), card_fit_label=lit.get("first_order_fit"),
                             card_fit_yes=str(lit.get("first_order_fit", "")).startswith("yes"), locator=(lit.get("locator") or "")[:300],
                             next_step="two blinded extractors: policy scope/dose, population, outcome, units against this design's frozen card, before its S6; then a third read on differences"))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    designs = sorted({r["design_id"] for r in rows})
    print(len(designs), "designs,", len(rows), "paper rows,", sum(r["card_fit_yes"] for r in rows), "with a 'yes' fit label; in ACTIVE_DESIGNS:", sum(1 for d in designs if d in active))
    return 0


if __name__ == "__main__":
    sys.exit(main())
