#!/usr/bin/env python3
"""Cross-check of the leaf conversion (sweep_ext_series.py) against L2's REGISTERED chain on the three series that L2's catalog V16 binds to local files:
bls_ces_private_weekly_earnings, acs1_snap_household_receipt_rate, bls_laus_employment (the last must park at the same denominator gate).

The chain is called the way sweep_core.build_artifact_series calls it for a catalog series: a registered design card, a compact secondary entry (role secondary,
connector outcome_v16_<series>, the catalog's own paths and hashes), the full package import of scripts.l2_pipeline.connectors (read-only; python -I -B; nothing
is written under the L2 trees).  Every cell the chain returns is compared with the parsed panel of the extension (value to 1e-9 relative, eligibility equal).
Cells that only the extension has (SNAP 2011: V16 binds no 2011 file) are counted separately.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/check_sweep_ext_chain.py --out data/l3_meta/sweep_ext_v1/CHAIN_CHECK_EXT_V1.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import sweep_core as S  # noqa: E402

EXT = ROOT / "data/l3_meta/sweep_ext_v1"
CAT16 = "scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V16.json"
SERIES = [("bls_ces_private_weekly_earnings", None), ("acs1_snap_household_receipt_rate", {}), ("bls_laus_employment", None)]


def registered_card():
    import build_sweep as B
    L = S.load_l2()
    did, folder, _, _ = B.roster()[0]
    return L, L.registered_execution_card(folder, S.read_json(folder / "FREEZE_FINAL.json")), did


def chain_panel(L, card, sid, filters):
    cat = S.read_json(ROOT / CAT16)["series"][sid]
    base = copy.deepcopy(card)
    base["outcome"] = dict(base["outcome"])
    base["outcome"]["units"] = sorted(L.STATES.values())
    base["outcome"]["period_years"] = list(cat["years"])
    base["outcome"].pop("years", None)
    entry = {"role": "secondary", "outcome": sid, "connector": "outcome_v16_" + sid, "catalog_path": CAT16, "catalog_sha256": S.sha256_file(ROOT / CAT16),
             "paths": cat["paths"], "source_sha256": cat["source_sha256"]}
    if filters is not None:
        entry["population_filters"] = filters
    return L.construct(L.ConnectorRequest.from_card(L.variants.secondary_card(base, entry), ROOT))


def cells(rows, metadata):
    out = {}
    for r, m in zip(rows, metadata):
        v = r.get("outcome_value")
        out[(r["unit"], int(r["year"]))] = (bool(S.strict_eligible(m) and S._finite(v)), float(v) if S._finite(v) else float("nan"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--parsed", default=str(EXT / "parsed"))
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        sys.exit(f"{out} exists: not overwritten")
    L, card, did = registered_card()
    results = []
    for sid, filt in SERIES:
        mine = json.loads((Path(args.parsed) / f"{sid}.json").read_text())
        rec = dict(series=sid)
        try:
            pd = chain_panel(L, card, sid, filt)
        except Exception as exc:
            rec.update(chain_error=f"{type(exc).__name__}: {str(exc)[:200]}", extension_scale=mine["request"]["scale"])
            rec["agree"] = bool(sid == "bls_laus_employment" and mine["request"]["scale"] == "count" and "denominator" in str(exc))
            results.append(rec)
            continue
        a, b = cells(pd.rows, pd.metadata), cells(mine["rows"], mine["metadata"])
        both = [k for k in a if k in b]
        diff_val = [k for k in both if a[k][0] and b[k][0] and abs(a[k][1] - b[k][1]) > 1e-9 * max(1.0, abs(a[k][1]), abs(b[k][1]))]
        diff_ok = [k for k in both if a[k][0] != b[k][0] and not (k[1] not in {kk[1] for kk, v in a.items() if v[0]})]
        only_mine = [k for k in b if b[k][0] and not a.get(k, (False,))[0]]
        rec.update(chain_cells=len(a), chain_eligible=sum(1 for v in a.values() if v[0]), extension_eligible=sum(1 for v in b.values() if v[0]),
                   compared_eligible_both=sum(1 for k in both if a[k][0] and b[k][0]), value_differences=len(diff_val), eligibility_differences=len(diff_ok),
                   eligible_only_in_extension=len(only_mine), years_only_in_extension=sorted({k[1] for k in only_mine}),
                   agree=bool(not diff_val and not diff_ok), chain_units=pd.rows[0]["outcome_unit"], extension_units=mine["rows"][0]["outcome_unit"])
        results.append(rec)
    report = dict(rule_id="EX-1", version="SWEEP-EXT-V1", check="leaf conversion against L2's registered chain (catalog V16 bindings)", design_card_of=did,
                  catalog_v16_sha256=S.sha256_file(ROOT / CAT16), python=sys.version.split()[0], results=results, all_agree=all(r["agree"] for r in results))
    with open(out, "x", encoding="utf-8") as fh:
        fh.write(json.dumps(S.clean(report), indent=1, sort_keys=True) + "\n")
    print(json.dumps(dict(all_agree=report["all_agree"], results=[{k: r.get(k) for k in ("series", "agree", "compared_eligible_both", "value_differences", "eligibility_differences", "eligible_only_in_extension", "chain_error")} for r in results]), indent=1))
    return 0 if report["all_agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
