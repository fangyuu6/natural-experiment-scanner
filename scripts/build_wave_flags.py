"""Multi-state adoption-wave detector + confound flags, derived from the 483k CSV.

wave definition: for a state-level enacted row in policy_family F at year Y,
wave_n_states = count of DISTINCT states with an enacted row in F within [Y-1, Y+1].
wave_flag = 1 if wave_n_states >= 3 (contaminated-controls risk for single-state DID).
Rows with blank/placeholder family ("", "other", "unknown", "none") are never wave-flagged.

Usage: python scripts/build_wave_flags.py [--csv data/output/policy_db_20260415_483k.csv]
"""
import argparse, csv, os, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
csv.field_size_limit(2**31 - 1)

from extraction.confound_calendar import flag_confounds

_SKIP_FAMILIES = {"", "other", "unknown", "none"}


def _is_state(jur):
    j = (jur or "").strip().lower()
    return j not in ("", "us federal") and not j.startswith("international")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/output/policy_db_20260415_483k.csv")
    ap.add_argument("--out", default="data/output/derived/confound_wave.csv")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    fam_year_states = defaultdict(set)  # (family, year) -> set of states
    with open(args.csv, newline="", encoding="utf-8", errors="replace") as fin:
        for row in csv.DictReader(fin):
            fam = (row.get("policy_family") or "").strip().lower()
            if fam in _SKIP_FAMILIES or not _is_state(row.get("jurisdiction")):
                continue
            if (row.get("status") or "").lower() != "enacted":
                continue
            try:
                y = int(row.get("year") or 0)
            except ValueError:
                continue
            if y:
                fam_year_states[(fam, y)].add(row["jurisdiction"].strip().lower())

    n_flagged = 0
    with open(args.csv, newline="", encoding="utf-8", errors="replace") as fin, \
         open(args.out, "w", newline="", encoding="utf-8") as fout:
        w = csv.writer(fout)
        w.writerow(["policy_id", "confound_flags", "wave_n_states", "wave_flag"])
        for row in csv.DictReader(fin):
            flags = flag_confounds(row.get("date"), row.get("policy_topic"),
                                   row.get("policy_family"), row.get("title"))
            fam = (row.get("policy_family") or "").strip().lower()
            wave_n = 0
            if fam not in _SKIP_FAMILIES and _is_state(row.get("jurisdiction")):
                try:
                    y = int(row.get("year") or 0)
                except ValueError:
                    y = 0
                if y:
                    states = set()
                    for yy in (y - 1, y, y + 1):
                        states |= fam_year_states.get((fam, yy), set())
                    wave_n = len(states)
            wave_flag = int(wave_n >= 3)
            n_flagged += wave_flag
            w.writerow([row.get("policy_id"), "|".join(flags), wave_n, wave_flag])
    print(f"wave_flagged\t{n_flagged}")


if __name__ == "__main__":
    main()
