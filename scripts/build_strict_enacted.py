"""Stream the 483k CSV once; emit data/output/derived/strict_enacted.csv.

Usage: python scripts/build_strict_enacted.py [--csv data/output/policy_db_20260415_483k.csv]
"""
import argparse, csv, os, sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
csv.field_size_limit(2**31 - 1)

from extraction.enactment_rules import classify_enactment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/output/policy_db_20260415_483k.csv")
    ap.add_argument("--out", default="data/output/derived/strict_enacted.csv")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tally = Counter()
    with open(args.csv, newline="", encoding="utf-8", errors="replace") as fin, \
         open(args.out, "w", newline="", encoding="utf-8") as fout:
        rdr = csv.DictReader(fin)
        w = csv.writer(fout)
        w.writerow(["policy_id", "strict_enacted", "reason", "verify_level"])
        for row in rdr:
            ok, reason, level = classify_enactment(
                row.get("policy_id"), row.get("status"),
                row.get("jurisdiction"), row.get("source_family"))
            w.writerow([row.get("policy_id"), int(ok), reason, level])
            tally[reason] += 1
    for reason, n in tally.most_common():
        print(f"{reason}\t{n}")


if __name__ == "__main__":
    main()
