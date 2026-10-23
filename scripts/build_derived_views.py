"""Merge Task 3/4/5 derived CSVs + min-gate score into one derived view.

Stream the 483k CSV once; emit data/output/derived/derived_views.csv with
columns policy_id,strict_enacted,verify_level,confound_flags,wave_flag,
data_verdict,min_gate,composite_score,viable_strict.

viable_strict makes the importance-vs-evaluability disjointness visible: a
policy can be important (high composite_score) yet not viable_strict because
it fails strict-enactment verification, sits in a multi-state adoption wave,
has no public microdata, or has a low min-gate sub-score (the binding
constraint from the four LLM sub-scores, not their mean -- see
src/extraction/min_gate.py).

Usage: python scripts/build_derived_views.py [--csv data/output/policy_db_20260415_483k.csv]
                                              [--derived data/output/derived]
"""
import argparse, csv, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
csv.field_size_limit(2**31 - 1)

from extraction.min_gate import compute_min_gate


def _load(path, cols):
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d[row["policy_id"]] = tuple(row[c] for c in cols)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/output/policy_db_20260415_483k.csv")
    ap.add_argument("--derived", default="data/output/derived")
    args = ap.parse_args()

    strict = _load(os.path.join(args.derived, "strict_enacted.csv"),
                   ["strict_enacted", "verify_level"])
    wave = _load(os.path.join(args.derived, "confound_wave.csv"),
                 ["confound_flags", "wave_flag"])
    access = _load(os.path.join(args.derived, "data_access.csv"), ["verdict"])

    out_path = os.path.join(args.derived, "derived_views.csv")
    total = viable = 0
    top = []
    with open(args.csv, newline="", encoding="utf-8", errors="replace") as fin, \
         open(out_path, "w", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        w.writerow(["policy_id", "strict_enacted", "verify_level", "confound_flags",
                    "wave_flag", "data_verdict", "min_gate", "composite_score",
                    "viable_strict"])
        for row in csv.DictReader(fin):
            pid = row.get("policy_id")
            total += 1
            se, vl = strict.get(pid, ("0", ""))
            cf, wf = wave.get(pid, ("", "0"))
            dv = access.get(pid, ("empty",))[0]
            try:
                sub = json.loads(row.get("detail_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                sub = {}
            mg = compute_min_gate(sub)
            ok = (se == "1" and wf == "0" and dv in ("public_ok", "mixed")
                  and mg is not None and mg >= 3 and "covid_window" not in cf)
            viable += ok
            w.writerow([pid, se, vl, cf, wf, dv,
                        "" if mg is None else mg, row.get("composite_score"), int(ok)])
            if ok:
                top.append((mg, row.get("composite_score"), pid, (row.get("title") or "")[:60]))
    top.sort(reverse=True)
    print(f"total\t{total}\nviable_strict\t{viable}")
    for mg, comp, pid, title in top[:20]:
        print(f"{pid} | {title} | min_gate={mg} | composite={comp}")


if __name__ == "__main__":
    main()
