#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version N1-V2 (rule id EX-1): the N1 computation of build_n1.py (unchanged, same code hash) on the V2 sets file.

Rules: docs/plans/l3-meta/PREREG_AMEND_20261017_N1_V2.md (written before any N1-V2 statistic), hash record PREREG_AMEND_20261017_N1_V2_HASH.txt,
panel ruling HUB_RULING_20261017_N1_SETS.md.  This module only rebinds the version constants of build_n1.py (sets file, amendment, hash record,
output folder and name, version label), calls its main(), and then writes N1_V2_SPLIT.json (amendment section 3: Z by frozen V1 sets and
appended sets; descriptive, no interval).  build_n1.py is not edited because other amendment records pin its sha256.
Run: .venv_did/bin/python -I -B scripts/l3_meta/build_n1_v2.py [--dry-run] [--out-dir DIR] [--live-set-changed-ok]
Output: data/l3_meta/n1_v2/{RESULTS_N1_V2.json, N1_V2_SPLIT.json, RUN_LOG.jsonl, l2_set_live_used.json}
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402

import build_n1 as B  # noqa: E402

ROOT = B.ROOT
FROZEN_V1_SETS = B.DATA / "l3_meta" / "n1_sets" / "n1_sets_v1.json"
V2 = dict(VERSION="N1-V2", SETS=B.DATA / "l3_meta" / "n1_sets_v2_20261019" / "n1_sets_v2.json",
          AMEND=ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261019_N1_V2B.md",
          AMEND_HASH=ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261019_N1_V2B_HASH.txt",
          OUT_DIR=B.DATA / "l3_meta" / "n1_v2", OUT_NAME="RESULTS_N1_V2.json")


def bind_v2():
    for k, v in V2.items():
        setattr(B, k, v)
    orig = B.parse_hash_record
    B.parse_hash_record = lambda path=None: orig(path or V2["AMEND_HASH"])


def split(results_path):
    res = json.loads(Path(results_path).read_text(encoding="utf-8"))
    frozen = {d["design_id"] for d in json.loads(FROZEN_V1_SETS.read_text())["designs"]}
    ev = [r for r in res["designs"] if r["code"] == "EVAL"]
    out = {"version": "N1-V2", "note": "descriptive, no interval (amendment section 3); frozen = V1 sets file records, appended = added by N1-V2",
           "results_file": B.rel(Path(results_path)), "code": B.rel(Path(__file__))}
    for name, rows in (("frozen_V1_sets", [r for r in ev if r["design_id"] in frozen]), ("appended_V2_sets", [r for r in ev if r["design_id"] not in frozen])):
        out[name] = {"n": len(rows), "F": len({r["family"] for r in rows}), "primary": B.z_block(rows, "primary") if rows else None,
                     "S1": B.z_block(rows, "S1") if rows else None}
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bind_v2()
    rc = B.main(argv)
    if "--dry-run" not in argv:
        out_dir = Path(argv[argv.index("--out-dir") + 1]) if "--out-dir" in argv else V2["OUT_DIR"]
        target = out_dir / "N1_V2_SPLIT.json"
        if not target.exists():
            target.write_text(json.dumps(B.clean(split(out_dir / V2["OUT_NAME"])), indent=1, allow_nan=False) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
