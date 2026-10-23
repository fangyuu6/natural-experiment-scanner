#!/usr/bin/env python3
"""L3 meta, EXPLORATORY (rule id EX-1): derive Q4 inputs (s_pre, H, v) and N6 inputs (lead covariance) from frozen L2 packages (T175).

Rules: docs/plans/l3-meta/PREREG_AMEND_20261020_Q4_N6_INPUTS.md (hash record PREREG_AMEND_20261020_Q4_N6_INPUTS_HASH.txt), written
before this script was run on the roster.  Method: scripts/l3_meta/replay_l2.py (read-only replay of the frozen estimator, reproduction
gate 1e-8 on every stored ATT).  Nothing under scripts/l2_pipeline or data/l2_pipeline is written; no network.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/derive_q4_n6_inputs.py [--dry-run] [--out-dir DIR] [--only ID ...]
Output folder (new, write-once): DERIVED_INPUTS.json, DERIVED_INPUTS.csv, LEADS_COV.json, PSI_OVERALL.csv, JOINT_COV_OVERALL.csv, RUN_LOG.jsonl
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import replay_l2 as R  # noqa: E402

RULE_ID = "EX-1"
VERSION = "Q4_N6_INPUTS_V1"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_Q4_N6_INPUTS.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261020_Q4_N6_INPUTS_HASH.txt"
LIVE = ROOT / "data/l3_meta/refresh/20261019_r3/live/l2_set_live.json"
PARTY = ROOT / "data/l3_meta/party_coding/design_summary_v2.csv"
OUT_DIR = ROOT / "data/l3_meta/q4_n6_inputs_20261020"
FAMILY_THRESHOLD = 20
TOL_SPRE_REL = 1e-9


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def roster(live_path, only=None):
    live = json.loads(Path(live_path).read_text())
    out = []
    for d in live["designs"]:
        if d["status"] != "estimated" or "terminal L2 pipeline" not in d["origin"]:
            continue
        if only and d["design_id"] not in only:
            continue
        src = ROOT / d["source"]["path"]
        out.append(dict(design_id=d["design_id"], policy_family=d["policy_family"], domain=d["domain"], package=src.parent, result=src,
                        live_point=d["point_estimate"], live_se=d["se"], live_ci95=d["ci95"]))
    return live, out


def stored_summary(result_path):
    """Stored overall/event-study ATTs, the stored interval and, when present, the exported s_pre/H (read-only)."""
    res = json.loads(Path(result_path).read_text())
    prim = res["registered_results"]["primary"]["result"]
    exp = res.get("l3_export_v2") or {}
    return dict(att=prim["overall"]["att"], se=prim["overall"].get("se"), ci95=prim["overall"].get("ci95"),
                interval=prim["overall"].get("interval"), events=[dict(event_time=r["event_time"], att=r["att"]) for r in prim["event_study"]],
                exported=dict(s_pre=exp.get("s_pre"), s_pre_n=exp.get("s_pre_n"), H=exp.get("H")) if exp.get("s_pre") is not None else None,
                status=res.get("status"))


def process(item, l2):
    row = dict(design_id=item["design_id"], policy_family=item["policy_family"], domain=item["domain"], package=str(item["package"].relative_to(ROOT)))
    t0 = time.time()
    stored = stored_summary(item["result"])
    row["stored_att"] = stored["att"]
    loaded = R.load_package(item["package"], l2)
    design = loaded["design"]
    rep = R.influence_table(design["units"], design["cells"], loaded["outcomes"])
    gate = R.gate_against_stored(rep, stored["att"], stored["events"])
    row["gate"] = gate
    if not gate["passed"]:
        row["status"] = "DROPPED_GATE"
        row["seconds"] = round(time.time() - t0, 1)
        return row, None
    sh = R.observed_s_pre_h(loaded, l2)
    row.update(status="DERIVED", s_pre=sh["s_pre"], s_pre_n=sh["s_pre_n"], H=sh["H"], balanced_status=sh["balanced_status"])
    if stored["exported"] is not None:
        ex = stored["exported"]
        rd = abs(sh["s_pre"] - ex["s_pre"]) / abs(ex["s_pre"]) if ex["s_pre"] else (0.0 if sh["s_pre"] == ex["s_pre"] else float("inf"))
        row["export_check"] = dict(exported_s_pre=ex["s_pre"], exported_H=ex["H"], exported_n=ex["s_pre_n"], rel_diff=rd,
                                   n_equal=ex["s_pre_n"] == sh["s_pre_n"], H_equal=ex["H"] == sh["H"],
                                   passed=bool(rd <= TOL_SPRE_REL and ex["s_pre_n"] == sh["s_pre_n"] and ex["H"] == sh["H"]))
    att, psi = rep["overall"]
    v_mult = R.variance_from_psi(psi)
    ci = stored["ci95"]
    row.update(b=att, n_units=rep["n"], n_treated_overall=int(sum(c["n_treated"] for c in rep["cells"] if c["post"])), v_mult=v_mult,
               se_mult=math.sqrt(v_mult), stored_se=stored["se"], stored_ci95=ci, stored_interval=stored["interval"],
               v_ci=R.variance_from_ci(ci), v_stored_se=(stored["se"] ** 2 if isinstance(stored["se"], (int, float)) else None))
    row["v_max_mult_ci"] = max(v_mult, row["v_ci"]) if row["v_ci"] is not None else v_mult
    row["ratio_se_ci_over_mult"] = math.sqrt(row["v_ci"] / v_mult) if row["v_ci"] and v_mult > 0 else None
    row["ratio_stored_se_over_mult"] = stored["se"] / math.sqrt(v_mult) if isinstance(stored["se"], (int, float)) and v_mult > 0 else None
    lead = R.lead_block(rep["events"])
    row["n_leads"] = len(lead["event_times"])
    if lead["cov"]:
        import numpy as _np
        ev = _np.linalg.eigvalsh(_np.array(lead["cov"]))
        row["lead_cov_min_eig"] = float(ev[0]); row["lead_cov_max_eig"] = float(ev[-1])
        row["lead_cov_rank"] = int((ev > 1e-10 * max(ev[-1], 1e-300)).sum())
    row["seconds"] = round(time.time() - t0, 1)
    return row, dict(lead=lead, psi=psi.tolist(), units=rep["units"])


def party_table():
    out = {}
    if PARTY.exists():
        with open(PARTY, newline="") as fh:
            for r in csv.DictReader(fh):
                out[r["design_id"]] = r
    return out


def q4_family_count(rows):
    party = party_table()
    full, fam_full, fam_ge20 = [], set(), set()
    for r in rows:
        if r.get("status") != "DERIVED" or r.get("H") is None or not r.get("s_pre"):
            continue
        full.append(r["design_id"])
        p = party.get(r["design_id"])
        if p and p.get("both_groups_ge20") == "1":
            fam_ge20.add(r["policy_family"])
    return dict(designs_with_s_pre_H_v=len(full), families_with_party_history_both_groups_ge20=len(fam_ge20), families_list=sorted(fam_ge20),
                threshold=FAMILY_THRESHOLD, short_by=max(0, FAMILY_THRESHOLD - len(fam_ge20)),
                caveat="party history is the single-pass mechanical build (design_summary_v2.csv), not independently certified; "
                       "the certified count is lower (L3_STATE_20261016 section 3: 11 families)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--live", default=str(LIVE))
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    hr = AMEND_HASH.read_text() if AMEND_HASH.exists() else ""
    want = next((l.split()[1] for l in hr.splitlines() if l.startswith("sha256:")), None)
    if want is None or sha256_file(AMEND) != want:
        print("REFUSED: amendment missing or its sha256 differs from the hash record", file=sys.stderr)
        return 2
    live, items = roster(args.live, set(args.only) if args.only else None)
    print(f"roster: {len(items)} estimated pipeline designs")
    if args.dry_run:
        for it in items:
            print(" ", it["design_id"], (it["package"] / "FREEZE_FINAL.json").exists())
        return 0
    out = Path(args.out_dir)
    if out.exists():
        print(f"REFUSED: {out} exists (write-once)", file=sys.stderr)
        return 2
    out.mkdir(parents=True)
    l2 = R._import_l2()
    rows, extras = [], {}
    with open(out / "RUN_LOG.jsonl", "w") as log:
        for it in items:
            try:
                if not (it["package"] / "FREEZE_FINAL.json").exists():
                    row = dict(design_id=it["design_id"], policy_family=it["policy_family"], package=str(it["package"].relative_to(ROOT)),
                               status="NOT_REPLAYABLE", reason="no FREEZE_FINAL.json in the package")
                    extra = None
                else:
                    row, extra = process(it, l2)
            except Exception as exc:  # recorded, never repaired
                row = dict(design_id=it["design_id"], policy_family=it["policy_family"], package=str(it["package"].relative_to(ROOT)),
                           status="NOT_REPLAYABLE", reason=f"{type(exc).__name__}: {str(exc)[:300]}")
                extra = None
            rows.append(row)
            if extra:
                extras[it["design_id"]] = extra
            log.write(json.dumps(clean(dict(utc=utc_now(), **{k: row.get(k) for k in ("design_id", "status", "reason", "seconds")})), sort_keys=True) + "\n")
            log.flush()
            print(row["design_id"], row["status"], row.get("reason", ""), row.get("seconds"), flush=True)
    derived = [r for r in rows if r["status"] == "DERIVED"]
    ok_export = [r for r in derived if "export_check" in r]
    stamp = dict(rule_id=RULE_ID, version=VERSION, run_utc=utc_now(), code=["scripts/l3_meta/replay_l2.py", "scripts/l3_meta/derive_q4_n6_inputs.py"],
                 code_sha256={n: sha256_file(HERE / n) for n in ("replay_l2.py", "derive_q4_n6_inputs.py")}, amendment_sha256=sha256_file(AMEND),
                 live_set=str(Path(args.live).relative_to(ROOT)) if str(args.live).startswith(str(ROOT)) else args.live, live_set_sha256=sha256_file(args.live),
                 python=sys.version.split()[0])
    summary = dict(roster=len(items), derived=len(derived), dropped_gate=sum(r["status"] == "DROPPED_GATE" for r in rows),
                   not_replayable=sum(r["status"] == "NOT_REPLAYABLE" for r in rows),
                   export_validation=dict(designs=len(ok_export), passed=sum(r["export_check"]["passed"] for r in ok_export)),
                   with_stored_interval=sum(r.get("v_ci") is not None for r in derived), leads_available=sum((r.get("n_leads") or 0) > 0 for r in derived),
                   q4=q4_family_count(rows))
    (out / "DERIVED_INPUTS.json").write_text(json.dumps(clean(dict(schema="l3-meta-q4-n6-inputs-v1", evidence_status="EXPLORATORY (EX-1); labelled L3 replay, not an L2 export; no confirmatory claim",
                                                                 stamp=stamp, summary=summary, designs=rows)), indent=1, sort_keys=True))
    (out / "LEADS_COV.json").write_text(json.dumps(clean({k: v["lead"] for k, v in extras.items()}), indent=1, sort_keys=True))
    cols = ["design_id", "policy_family", "domain", "status", "b", "s_pre", "s_pre_n", "H", "v_mult", "v_ci", "v_stored_se", "v_max_mult_ci", "lead_cov_rank", "lead_cov_min_eig", "ratio_se_ci_over_mult", "n_units", "n_leads"]
    with open(out / "DERIVED_INPUTS.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    with open(out / "PSI_OVERALL.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["design_id", "unit", "psi"])
        for d, e in extras.items():
            for u, p in zip(e["units"], e["psi"]):
                w.writerow([d, u, repr(p)])
    ids, c = R.joint_covariance({d: e["psi"] for d, e in extras.items()}, {d: e["units"] for d, e in extras.items()})
    with open(out / "JOINT_COV_OVERALL.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([""] + ids)
        for i, d in enumerate(ids):
            w.writerow([d] + [repr(float(x)) for x in c[i]])
    print(json.dumps(clean(summary), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
