#!/usr/bin/env python3
"""L3 meta, Q2 blockers: can a card change rescue the three strictly matched YRBS designs at the L2 pre-screen?  (value-free, read-only)

For each of the cards b1011c_yrbs_medical_cannabis_youth_use, b1011c_yrbs_primary_seatbelt_teen_belt_use and b1011c_yrbs_same_sex_marriage_suicide_attempt
(bound copies of the C63 validated cards, copied to data/l3_meta/q2_v2/prescreen_dry_run/cards/), re-run the biennial S3 support check of the L2 pre-screen
(`scripts.l2_pipeline.prescreen` helpers and `scripts.l2_pipeline.biennial.support`, imported read-only) over a grid of declared outcome windows and the two
registered gap-rule options (none, drop_affected_state_v7), with the card's own comparison group and treatment screening list.  Only support metadata of the
registered YRBS sidecars is read: no outcome value, no estimate.  A configuration is 'legal' when its last wave is not after the card's declared legal coverage
end (`treatment.coverage_end`): a later wave would need the adoption history re-audited to a later date and would also change who is untreated.

Writes (once) data/l3_meta/q2_v2/Q2_YRBS_WINDOW_GRID_20261016.csv.  Nothing under scripts/l2_pipeline or data/l2_pipeline is written.

Run:  PYTHONPATH=$HOME/Library/Python/3.9/lib/python/site-packages .venv_did/bin/python -B scripts/l3_meta/yrbs_window_grid.py
"""
from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.l2_pipeline import prescreen as P  # noqa: E402
from scripts.l2_pipeline.biennial import support as biennial_support  # noqa: E402
from scripts.l2_pipeline.stages.s3_support import variant_treatment  # noqa: E402
from scripts.l2_pipeline.variants import registered_cards  # noqa: E402

CARDS = ROOT / "data/l3_meta/q2_v2/prescreen_dry_run/cards"
OUT = ROOT / "data/l3_meta/q2_v2/Q2_YRBS_WINDOW_GRID_20261016.csv"
NAMES = ["b1011c_yrbs_medical_cannabis_youth_use", "b1011c_yrbs_primary_seatbelt_teen_belt_use", "b1011c_yrbs_same_sex_marriage_suicide_attempt"]
FIELDS = ["design_id", "window_lo", "window_hi", "gap_rule", "comparison", "waves", "legal_window", "s3_passed", "retained_treated", "issues"]


def main():
    if OUT.exists():
        sys.exit("output exists: not overwritten")
    rows = []
    for name in NAMES:
        card = json.loads((CARDS / (name + ".json")).read_text())
        legal_hi = int(card["treatment"]["coverage_end"][:4])
        base = registered_cards(card)["primary"]
        spec = P.catalog_spec(base["outcome"], ROOT) or {}
        if not spec.get("support_artifact") and base["outcome"].get("support_path"):
            spec = dict(spec, series_id=base["outcome"]["series_id"],
                        support_artifact=dict(path=base["outcome"]["support_path"], sha256=base["outcome"]["support_sha256"]))
        keys = P.flags(spec, ROOT)
        for lo in range(1991, 2014, 2):
            for hi in range(lo + 8, 2024, 2):          # at least five survey waves
                for gap in (None, "drop_affected_state_v7"):
                    for comp in ("not_yet_treated", "never_treated"):
                        v = copy.deepcopy(base)
                        v["outcome"]["years"] = [lo, hi]
                        v["outcome"]["period_years"] = [y for y in range(lo, hi + 1) if y % 2 == 1]
                        v["analysis"]["controls"] = comp
                        v["analysis"]["comparison_group"] = comp
                        if gap:
                            v["analysis"]["support_gap_rule"] = gap
                        else:
                            v["analysis"].pop("support_gap_rule", None)
                        try:
                            tr = variant_treatment(P.records(v, 0), v)
                            s = biennial_support(keys, tr, v["analysis"], v["outcome"], v.get("transition_year_drop", False))
                            res = (bool(s["passed"]), len(s.get("retained_treated", []) or []), json.dumps(s.get("issues"), sort_keys=True))
                        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                            res = (False, 0, "ERROR " + type(exc).__name__ + ": " + str(exc)[:80])
                        rows.append(dict(design_id=name, window_lo=lo, window_hi=hi, gap_rule=gap or "none", comparison=comp,
                                         waves=len(v["outcome"]["period_years"]), legal_window=hi <= legal_hi, s3_passed=res[0],
                                         retained_treated=res[1], issues=res[2]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    for name in NAMES:
        mine = [r for r in rows if r["design_id"] == name]
        print(name, "configs", len(mine), "pass", sum(r["s3_passed"] for r in mine), "pass within legal window", sum(r["s3_passed"] and r["legal_window"] for r in mine),
              "legal configs", sum(r["legal_window"] for r in mine), "max retained treated (legal)", max([r["retained_treated"] for r in mine if r["legal_window"]] or [0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
