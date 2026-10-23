#!/usr/bin/env python3
"""L3 N1 sets file V2 = the frozen N1-V1 sets file (every old record byte-identical) + appended records.

Rule (docs/plans/l3-meta/PREREG_AMEND_20261017_N1_V2.md section 1, HUB_RULING_20261017_N1_SETS.md):
  - every record of the V1 sets file ("designs" and the 'skipped' rows that stay skipped) is kept unchanged;
  - a design with a record in the rebuilt file (build_n1_sets.py on the 20261017_r3 live set) and no record in the V1 "designs"
    is appended with the rebuilt record; a V1 'skipped' row of such a design is replaced by that record;
  - a rebuilt 'skipped' row of a design absent from the V1 file is appended to 'skipped'.
No statistic is computed; no L2 file is opened.  Output: data/l3_meta/n1_sets_v2/{n1_sets_v2.json, RECEIPT_n1_sets_v2.json}.
Run: python3 -I -B scripts/l3_meta/build_n1_sets_v2.py [--v1 F] [--rebuilt F] [--out-dir DIR]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", default=str(ROOT / "data/l3_meta/n1_sets/n1_sets_v1.json"))
    ap.add_argument("--rebuilt", default=str(ROOT / "data/l3_meta/n1_sets_20261017_r3/n1_sets_v1.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/n1_sets_v2"))
    a = ap.parse_args(argv)
    v1, nw = json.loads(Path(a.v1).read_text()), json.loads(Path(a.rebuilt).read_text())
    old = {d["design_id"] for d in v1["designs"]}
    app = sorted((d for d in nw["designs"] if d["design_id"] not in old), key=lambda d: d["design_id"])
    app_ids = {d["design_id"] for d in app}
    skipped_keep = [s for s in v1["skipped"] if s["design_id"] not in app_ids]
    seen = {s["design_id"] for s in v1["skipped"]} | old
    skipped_new = sorted((s for s in nw["skipped"] if s["design_id"] not in seen), key=lambda s: s["design_id"])
    out = {"designs": v1["designs"] + app, "skipped": skipped_keep + skipped_new}
    changed = sorted(d["design_id"] for d in nw["designs"] if d["design_id"] in old and
                     json.dumps(d, sort_keys=True) != json.dumps(next(x for x in v1["designs"] if x["design_id"] == d["design_id"]), sort_keys=True))
    lost = sorted(old - {d["design_id"] for d in nw["designs"]})
    outs = {"sets_json": C.write_new(Path(a.out_dir) / "n1_sets_v2.json", C.json_bytes(out))}
    ev = lambda rs: sum(1 for r in rs if r.get("eval_n1_spec"))
    receipt = {"schema": "l3_n1_sets_v2_receipt", "version": "v2", "built_utc": C.utc_now(),
               "builder": {"path": C.rel(__file__), "sha256": C.sha256_file(Path(__file__))},
               "inputs": {"v1_sets": {"path": C.rel(a.v1), "sha256": C.sha256_file(Path(a.v1))},
                          "rebuilt_sets": {"path": C.rel(a.rebuilt), "sha256": C.sha256_file(Path(a.rebuilt))}},
               "outputs": outs,
               "counts": {"designs": len(out["designs"]), "frozen": len(v1["designs"]), "appended": len(app), "appended_eval_n1_spec": ev(app),
                          "frozen_eval_n1_spec": ev(v1["designs"]), "eval_n1_spec_total": ev(out["designs"]),
                          "skipped": len(out["skipped"]), "skipped_v1_moved_to_designs": len(v1["skipped"]) - len(skipped_keep),
                          "skipped_appended": len(skipped_new)},
               "appended_design_ids": [d["design_id"] for d in app],
               "appended_not_eval": [d["design_id"] for d in app if not d.get("eval_n1_spec")],
               "skipped_appended_ids": [s["design_id"] for s in skipped_new],
               "frozen_designs_whose_rebuild_differs": changed, "frozen_designs_absent_from_rebuild_kept": lost}
    outs["receipt"] = C.write_new(Path(a.out_dir) / "RECEIPT_n1_sets_v2.json", C.json_bytes(receipt))
    print(json.dumps(receipt["counts"], indent=1)); print(outs["sets_json"])


if __name__ == "__main__":
    main()
