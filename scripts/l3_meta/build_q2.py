#!/usr/bin/env python3
"""L3 meta, EXPLORATORY version Q2-V1 (rule id EX-1): Q2, do matched published effects shrink in our estimates?

Rules: docs/plans/l3-meta/PREREG_AMEND_20261015_Q2_V1.md (recorded before any Q2 statistic was computed; hash and T_amend in
PREREG_AMEND_20261015_Q2_HASH.txt), PREREG_AMEND_20261012_EXPLORATORY.md (EX-1) and the frozen Q2 text of PREREG_L3_META.md.

Estimand (Q2 text): G = mean_i a_i (b_lit,i - b_i) / s_i, a_i the benchmark direction locked from the literature, s_i the outcome SD in the
frozen clean preperiod (L2 export `s_pre`).  Inclusion: designs with a STRICT published match (policy scope/dose, population, outcome and
units match or arith_convertible; no documented mismatch in comparison, clock, aggregation, horizon).  Stopping rule: fewer than 20 matched
families, undefined scales or unavailable variance/covariance give a descriptive gap only.  The frozen test (family-block bootstrap plus
joint influence-score multiplier draws) needs L2 influence scores that L3 does not receive: it is NOT implemented here.

Reads (files named in the amendment, sections 1.2): the live set, the first-certified-run table of RESULTS_EX1_V3.json, TARGETS_R2.json,
q2_pairs_v1.csv (id links only) and q2_reconciled_v1.csv.  Nothing under scripts/l2_pipeline or data/l2_pipeline is opened; no network.
Outputs (new folder, written once): RESULTS_Q2_V1.json, RUN_LOG.jsonl, l2_set_live_used.json.  Every number is exploratory: no
"significant" label, no multiplicity adjustment, no licensed sentence.

Run:  .venv_did/bin/python -I -B scripts/l3_meta/build_q2.py [--dry-run] [--out-dir DIR]     (standard library only)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

RULE_ID = "EX-1"
QUESTION = "Q2"
VERSION = "Q2-V1"
DATA = ROOT / "data"
LIVE = DATA / "l3_meta" / "l2_set_live.json"
V3 = DATA / "l3_meta" / "ex1_v3" / "RESULTS_EX1_V3.json"
TARGETS = DATA / "l3_meta" / "replication_targets" / "TARGETS_R2.json"
PAIRS = DATA / "l3_meta" / "q2_join" / "q2_pairs_v1.csv"
RECON = DATA / "l3_meta" / "q2_join" / "q2_reconciled_v1.csv"
RECEIPT = DATA / "l3_meta" / "q2_join" / "RECEIPT_q2_reconciled_v1.json"
AMEND = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_Q2_V1.md"
AMEND_HASH = ROOT / "docs/plans/l3-meta/PREREG_AMEND_20261015_Q2_HASH.txt"
OUT_DIR = DATA / "l3_meta" / "q2_v1"
OUT_NAME = "RESULTS_Q2_V1.json"

MIN_FAMILIES = 20                    # frozen Q2 stopping rule: fewer than 20 matched families -> descriptive gap only
MDE_PLANNING = {50: 0.483, 100: 0.342}   # Q2 text: 80% paired-gap MDE in SD units, printed beside n
PASS = ("match", "arith_convertible")
DIMS = ["policy_scope_dose", "population", "outcome", "units", "comparison", "clock", "aggregation", "horizon"]
CORE, NONCORE = DIMS[:4], DIMS[4:]
ARCHIVE_GROUPS = ("prototype", "manual_batch")
# amendment 7.3: pairs both coders called not strict but flagged as judgment calls (Q2_THIRD_READER_LOG_20261015.md section 4)
FLIP_PAIRS = [
    "wherry2016-99815287|proto_medicaid_expansion",
    "simon2017-1651f1ce|proto_medicaid_expansion",
    "castillocarniglia2021-9d4da005|b1010b_pdmp_mandatory_registration",
    "mccourt2020-1257d44f|run8_gunshow_background_check",
    "mccourt2020-1257d44f|run8_universal_background_check",
    "doleac2022-e0686018|r1011_naloxone_access_law",
    "duska2022-9eb9cd62|bulk10_health_naloxone_coprescribing_mandate",
    "eliason2025-1c2e7a68|b1011tl_chip_waiting_period_elimination",
    "li2018-ea680c66|b1010_parole_abolition",
]


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def rel(p):
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, tuples -> lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def parse_hash_record(path=AMEND_HASH):
    """sha256 and T_amend of the amendment, and the input hashes listed in the record."""
    text = Path(path).read_text(encoding="utf-8")
    sha = re.search(r"^sha256:\s*([0-9a-f]{64})", text, re.M)
    t_amend = re.search(r"^amended_utc \(T_amend\):\s*(\S+)", text, re.M)
    listed = {m.group(1): m.group(2) for m in re.finditer(r"^\s+(\S+)\s+sha256\s+([0-9a-f]{64})", text, re.M)}
    return {"sha256": sha.group(1) if sha else None, "t_amend_utc": t_amend.group(1) if t_amend else None, "listed": listed}


# ---------------------------------------------------------------------------------------------
# pure rules (unit-tested in test_build_q2.py)
# ---------------------------------------------------------------------------------------------
def venue_is_peer_reviewed(venue):
    """Peer-review flag of a NEW pair: the venue string carries a journal citation (volume(issue):pages or volume:pages).
    Only used to choose between a new strict pair and the earlier chosen candidate (amendment 3.3); a code-level detail fixed before the run."""
    v = venue or ""
    return bool(re.search(r"\d+\(\d+\):\d+", v) or re.search(r"\b\d{1,4}:\d+-\d+", v))


def classify_targets_candidate(entry_class, cand):
    """Class of an earlier coded candidate (amendment 3.1)."""
    if entry_class == "unavailable":
        return "UNAVAILABLE"
    if cand.get("strict_core"):
        if entry_class == "strict_possible":
            return "STRICT"
        if entry_class == "non_strict":
            return "CORE_ONLY"
    return "NOT_STRICT"


def classify_known_pair(entry_class, strict_core_flag):
    """Same rule from the join's columns (targets_q2_class, targets_strict_core)."""
    return classify_targets_candidate(entry_class, {"strict_core": strict_core_flag in ("1", 1, True, "True", "true")})


def orientation(cand):
    d = cand.get("direction_a_i")
    return {"+1": 1, "-1": -1, "1": 1, 1: 1, -1: -1}.get(d)


def published_number(cand):
    """(value in our units, unit label, how) or (None, None, reason).  Only an exact recorded conversion or a unit `match` (amendment 3.5)."""
    est = cand.get("estimate") or {}
    conv = cand.get("conversion") or {}
    if conv and finite(conv.get("converted_point")):
        return conv["converted_point"], conv.get("to_units") or est.get("units") or "", "converted"
    if (cand.get("match") or {}).get("units") == "match" and finite(est.get("point")):
        return est["point"], est.get("units") or "", "as printed"
    return None, None, "no comparable number"


def unit_label_ok(our_unit, label):
    return bool(our_unit) and bool(label) and str(our_unit).strip().lower() in str(label).lower()


def choose_candidate(cands):
    """Amendment 3.3: peer-reviewed first, then publication year ascending, then the TARGETS candidate before a new pair, then input order."""
    ordered = sorted(cands, key=lambda c: (0 if c["peer_reviewed"] else 1, c["year"], 0 if c["source"] == "targets_r2" else 1, c["order"]))
    return (ordered[0], ordered[1:]) if ordered else (None, [])


def gap(a, b_lit, b, scale):
    """g = a (b_lit - b) / s; positive when the published effect is larger than ours along the published direction."""
    return a * (b_lit - b) / scale


def fully_strict(match):
    return bool(match) and all(match.get(d) in PASS for d in DIMS)


def design_candidates(entry, new_pairs):
    """All candidates of one design: earlier coded (TARGETS_R2 entry) and new reconciled pairs.

    Returns dict(strict=[...], core_only=[...], unavailable=[...]).  An earlier STRICT candidate is the chosen candidate of a
    strict_possible entry (amendment 3.1); new pairs come with their final class and, for STRICT/CORE_ONLY, their eight cells."""
    out = {"strict": [], "core_only": [], "unavailable": []}
    if entry:
        ec = entry.get("q2_class")
        chosen_rank = entry.get("chosen_candidate_rank")
        for i, c in enumerate(entry.get("candidates") or []):
            cls = classify_targets_candidate(ec, c)
            rec = {"source": "targets_r2", "order": i, "rank": c.get("rank"), "citation": c.get("citation"), "year": c.get("year") if isinstance(c.get("year"), int) else 9999,
                   "peer_reviewed": bool(c.get("peer_reviewed")), "version": c.get("version"), "cand": c, "match": c.get("match") or {}}
            if cls == "STRICT" and c.get("rank") == chosen_rank:
                out["strict"].append(rec)
            elif cls == "CORE_ONLY":
                out["core_only"].append(rec)
            elif cls == "UNAVAILABLE":
                out["unavailable"].append(rec)
    for j, p in enumerate(new_pairs):
        rec = {"source": "reconciled", "order": 1000 + j, "rank": None, "citation": f"{p.get('first_author')} {p.get('year')}, {p.get('venue')}",
               "year": p["year"] if isinstance(p.get("year"), int) else 9999, "peer_reviewed": venue_is_peer_reviewed(p.get("venue")),
               "version": None, "cand": {}, "match": {d: p.get(d) for d in DIMS}, "pair_id": p["pair_id"]}
        cls = p["final_class"]
        if cls == "STRICT":
            out["strict"].append(rec)
        elif cls == "CORE_ONLY":
            out["core_only"].append(rec)
        elif cls == "UNAVAILABLE":
            out["unavailable"].append(rec)
    return out


def evaluate_design(live_row, v3rec, entry, new_pairs):
    """One design of D -> row with its coverage code and, for matched estimated designs, the gap pieces (amendment 3-5)."""
    did = live_row["design_id"]
    row = {"design_id": did, "family": live_row.get("policy_family") or did, "domain": live_row.get("domain"), "live_status": live_row["status"],
           "gate": live_row.get("gate"), "source_group": live_row.get("source_group"), "reportable": bool(live_row.get("reportable")),
           "targets_class": entry.get("q2_class") if entry else None, "targets_id": entry.get("design_id") if entry else None,
           "unit": live_row.get("outcome_unit")}
    cands = design_candidates(entry, new_pairs)
    row["n_new_pairs"] = len(new_pairs)
    row["new_pairs_by_class"] = dict(Counter(p["final_class"] for p in new_pairs))
    row["has_strict"] = bool(cands["strict"])
    row["has_core_only"] = bool(cands["core_only"])
    row["unavailable_from"] = sorted({u.get("pair_id") or "targets_r2" for u in cands["unavailable"]})
    reasons = []
    if cands["strict"]:
        chosen, alts = choose_candidate(cands["strict"])
        row["chosen"] = {"source": chosen["source"], "citation": chosen["citation"], "year": chosen["year"], "peer_reviewed": chosen["peer_reviewed"],
                         "version": chosen["version"], "rank": chosen["rank"], "pair_id": chosen.get("pair_id")}
        row["alternatives"] = [{"source": a["source"], "citation": a["citation"], "year": a["year"], "peer_reviewed": a["peer_reviewed"], "pair_id": a.get("pair_id")} for a in alts]
        row["fully_strict"] = fully_strict(chosen["match"])
        row["match_cells"] = {d: chosen["match"].get(d) for d in DIMS}
        a_i = orientation(chosen["cand"]) if chosen["source"] == "targets_r2" else None
        b_lit, label, how = (published_number(chosen["cand"]) if chosen["source"] == "targets_r2" else (None, None, "no locked number"))
        row.update({"a_i": a_i, "b_lit": b_lit, "b_lit_unit_label": label, "b_lit_how": how if b_lit is not None else None,
                    "published_point_as_printed": (chosen["cand"].get("estimate") or {}).get("point_as_printed") if chosen["source"] == "targets_r2" else None,
                    "published_locator": (chosen["cand"].get("estimate") or {}).get("locator") if chosen["source"] == "targets_r2" else None})
        if live_row["status"] != "estimated":
            code = f"STRICT_NOT_ESTIMATED:{live_row.get('gate')}"
        else:
            b = v3rec.get("b") if v3rec else None
            noise = v3rec.get("noise_sd") if v3rec else None
            s_pre = v3rec.get("s_pre") if v3rec else None
            row.update({"b": b, "noise_sd": noise, "s_pre": s_pre})
            # order of the amendment (section 5): STRICT_NO_NUMBER, ORIENTATION_MISSING, NO_COMPARABLE_NUMBER, UNIT_MISMATCH (then ESTIMATE_MISSING)
            if chosen["source"] != "targets_r2":
                reasons.append("STRICT_NO_NUMBER")
            if a_i is None and chosen["source"] == "targets_r2":
                reasons.append("ORIENTATION_MISSING")
            if b_lit is None and chosen["source"] == "targets_r2":
                reasons.append("NO_COMPARABLE_NUMBER")
            if b_lit is not None and not unit_label_ok(live_row.get("outcome_unit"), label):
                reasons.append("UNIT_MISMATCH")
            if not finite(b):
                reasons.append("ESTIMATE_MISSING")
            if reasons:
                code = reasons[0]
            else:
                row["raw_gap"] = gap(a_i, b_lit, b, 1.0)
                scale_ok = finite(s_pre) and s_pre > 0
                noise_ok = finite(noise) and noise > 0
                row["noise_scale_ok"] = noise_ok
                if noise_ok:
                    row["g_noise"] = gap(a_i, b_lit, b, noise)
                if scale_ok:
                    row["g"] = gap(a_i, b_lit, b, s_pre)
                    code = "EVAL"
                else:
                    code = "SCALE_UNDEFINED"
    elif cands["unavailable"]:
        code = "UNAVAILABLE"
    elif entry is None:
        code = "NO_TARGETS_ENTRY"
    elif entry.get("q2_class") == "none_found":
        code = "NONE_FOUND"
    else:
        code = "NON_STRICT"
    row["code"] = code
    row["reasons"] = reasons
    return row


def decide(n, families):
    """Amendment 6.1-6.2: the stopping rule and the unavailable engine."""
    out = {"n": n, "F": families, "min_families": MIN_FAMILIES, "families_ge_min": families >= MIN_FAMILIES,
           "engine_status": "not run: the frozen test needs the family-block bootstrap AND the joint influence-score multiplier draws; L3 receives no influence scores or panels, so the test cannot be run for any n (amendment 6.2)",
           "interval": None, "p_value": None, "sentence": None}
    if n == 0:
        out["status"] = "NOT_COMPUTABLE"
        out["reading"] = "no design has a strict published match, a usable estimate and a frozen preperiod SD at the same time: G is not computable (undefined scales: descriptive gap only)"
    else:
        out["status"] = "DESCRIPTIVE_GAP_ONLY"
        out["reading"] = ("G is a descriptive mean over the eligible designs; " +
                          ("fewer than 20 matched families" if families < MIN_FAMILIES else "the 20-family minimum is met but the test engine is unavailable") +
                          ": no interval, no p-value, no sentence")
    out["planning_mde"] = {str(k): v for k, v in MDE_PLANNING.items()}
    return out


def assemble(live, v3, targets, pairs, recon, receipt=None):
    """The whole Q2-V1 computation on loaded objects (no file access): returns (result dict, per-design rows, checks)."""
    designs = live["designs"]
    entries = {e["design_id"]: e for e in targets["designs"]}
    v3_by = {d["design_id"]: d for d in v3["designs"]}
    pair_row = {p["pair_id"]: p for p in pairs}
    recon_by = {r["pair_id"]: r for r in recon}
    live_ids = {d["design_id"] for d in designs}

    # new pairs per live design (pair -> live design via the join; first-order-variant pairs are never attached to the executed design)
    new_by_design = {}
    for pid, r in recon_by.items():
        p = pair_row.get(pid)
        if p is None:
            continue
        ld = p.get("live_design_id") or ""
        if ld and ld in live_ids and not p.get("fo_variant_base_design"):
            new_by_design.setdefault(ld, []).append({
                "pair_id": pid, "final_class": r["final_class"], "first_author": p.get("paper_first_author"),
                "year": int(p["paper_year"]) if str(p.get("paper_year") or "").isdigit() else None, "venue": p.get("paper_venue"),
                **{d: r.get(d) for d in DIMS}})

    rows, mismatch_class, mismatch_point, mismatch_b = [], [], [], []
    for d in designs:
        did = d["design_id"]
        bm = d.get("benchmark") or {}
        entry = entries.get(bm.get("target_id")) if bm.get("target_id") else None
        if entry is not None and bm.get("q2_class") != entry.get("q2_class"):
            mismatch_class.append({"design_id": did, "live": bm.get("q2_class"), "targets_r2": entry.get("q2_class")})
        if entry is not None and entry.get("q2_class") == "strict_possible":
            ch = next((c for c in entry["candidates"] if c.get("rank") == entry.get("chosen_candidate_rank")), None)
            lp = (bm.get("published_estimate") or {}).get("point")
            tp = ((ch or {}).get("estimate") or {}).get("point")
            if lp != tp:
                mismatch_point.append({"design_id": did, "live": lp, "targets_r2": tp})
        v3rec = v3_by.get(did)
        if d["status"] == "estimated":
            if v3rec is None or not finite(v3rec.get("b")) or not finite(d.get("point_estimate")) or abs(v3rec["b"] - d["point_estimate"]) > 1e-12 * max(1.0, abs(v3rec["b"])):
                mismatch_b.append({"design_id": did, "v3_b": (v3rec or {}).get("b"), "live_point_estimate": d.get("point_estimate")})
        rows.append(evaluate_design(d, v3rec, entry, new_by_design.get(did, [])))

    D = len(rows)
    codes = dict(sorted(Counter(r["code"] for r in rows).items()))
    ev = [r for r in rows if r["code"] == "EVAL"]
    n = len(ev)
    fams = sorted({r["family"] for r in ev})
    matched_est = [r for r in rows if r["has_strict"] and r["live_status"] == "estimated"]
    matched_all = [r for r in rows if r["has_strict"]]
    with_number = [r for r in matched_est if r.get("raw_gap") is not None]
    noise_set = [r for r in with_number if r.get("g_noise") is not None]
    G = mean(r["g"] for r in ev) if ev else None
    G_noise = mean(r["g_noise"] for r in noise_set) if noise_set else None

    # strict matches outside D
    live_target_ids = {(d.get("benchmark") or {}).get("target_id") for d in designs}
    live_fo_ids = {(d.get("benchmark_first_order_variant") or {}).get("target_id") for d in designs}
    outside = []
    for e in targets["designs"]:
        if e.get("q2_class") == "strict_possible" and e["design_id"] not in live_target_ids:
            outside.append({"targets_id": e["design_id"], "lifecycle": e.get("lifecycle"),
                            "first_order_variant_of_a_live_design": e["design_id"] in live_fo_ids})

    # pair level: the 302 pairs of the join, earlier coded (known) and new (reconciled)
    pair_classes = Counter()
    pair_unaccounted = []
    for p in pairs:
        if str(p.get("known_in_targets")) == "1":
            cls = classify_known_pair(p.get("targets_q2_class"), p.get("targets_strict_core"))
            pair_classes[("known", cls)] += 1
        elif p["pair_id"] in recon_by:
            pair_classes[("new", recon_by[p["pair_id"]]["final_class"])] += 1
        else:
            pair_unaccounted.append(p["pair_id"])
    new_by_state_class = Counter((pair_row[pid]["l2_state"], recon_by[pid]["final_class"]) for pid in recon_by if pid in pair_row)

    # sensitivities (amendment 7)
    gate_pass = [r for r in ev if r["reportable"]]
    core_only_designs = [r for r in rows if r["has_strict"] or r["has_core_only"]]
    sens = {
        "gate_passing_EVAL": {"n": len(gate_pass), "G": mean(r["g"] for r in gate_pass) if gate_pass else None},
        "strict_matched_designs": {"in_D": len(matched_all), "estimated": len(matched_est), "EVAL": n,
                                   "reportable_estimated": sum(1 for r in matched_est if r["reportable"]),
                                   "fully_strict_in_D": sum(1 for r in matched_all if r.get("fully_strict")),
                                   "fully_strict_estimated": sum(1 for r in matched_est if r.get("fully_strict"))},
        "core_only_rule": {"designs_in_D": len(core_only_designs), "estimated": sum(1 for r in core_only_designs if r["live_status"] == "estimated"),
                           "estimated_with_s_pre": sum(1 for r in core_only_designs if r["live_status"] == "estimated" and finite((v3_by.get(r["design_id"]) or {}).get("s_pre"))),
                           "design_ids_estimated": [r["design_id"] for r in core_only_designs if r["live_status"] == "estimated"]},
    }
    flips = []
    for pid in FLIP_PAIRS:
        p = pair_row.get(pid)
        if p is None:
            flips.append({"pair_id": pid, "found": False})
            continue
        ld = p.get("live_design_id") or ""
        lrow = next((d for d in designs if d["design_id"] == ld), None)
        v3r = v3_by.get(ld) or {}
        est = bool(lrow and lrow["status"] == "estimated")
        flips.append({"pair_id": pid, "found": True, "final_class": recon_by.get(pid, {}).get("final_class"), "live_design": ld or None,
                      "l2_state": p.get("l2_state"), "estimated": est,
                      "finite_b": est and finite(v3r.get("b")),
                      "has_s_pre": est and finite(v3r.get("s_pre")) and v3r["s_pre"] > 0,
                      "has_noise_sd": est and finite(v3r.get("noise_sd")) and v3r["noise_sd"] > 0,
                      "would_enter_G": bool(est and finite(v3r.get("b")) and finite(v3r.get("s_pre")) and v3r["s_pre"] > 0),
                      "would_enter_G_noise": bool(est and finite(v3r.get("b")) and finite(v3r.get("noise_sd")) and v3r["noise_sd"] > 0)})
    sens["reading_flips"] = {"pairs": flips,
                             "any_would_enter_G": any(f.get("would_enter_G") for f in flips),
                             "any_would_enter_G_noise": any(f.get("would_enter_G_noise") for f in flips)}
    sens["splits_matched_estimated"] = {"archive_hand_picked": sum(1 for r in matched_est if r["source_group"] in ARCHIVE_GROUPS),
                                        "pipeline": sum(1 for r in matched_est if r["source_group"] not in ARCHIVE_GROUPS)}

    raw_gaps = [{"design_id": r["design_id"], "unit": r["unit"], "a_i": r["a_i"], "b_lit": r["b_lit"], "b": r["b"], "raw_gap_a_times_blit_minus_b": r["raw_gap"],
                 "s_pre": r["s_pre"], "noise_sd": r["noise_sd"], "g_noise": r.get("g_noise"), "reportable": r["reportable"], "published": r["chosen"]["citation"],
                 "published_locator": r["published_locator"], "recorded_direction_basis": "set after the archive estimate existed" if r["source_group"] in ARCHIVE_GROUPS else "set before the run"}
                for r in with_number]
    result = OrderedDict([
        ("D", D), ("n", n), ("F", len(fams)), ("coverage_codes", codes),
        ("decision", decide(n, len(fams))),
        ("G", {"definition": "mean_i a_i (b_lit,i - b_i) / s_pre,i over EVAL designs, equal design weights", "value": G, "n": n, "F": len(fams), "families": fams,
               "per_design": [{"design_id": r["design_id"], "g": r["g"]} for r in ev]}),
        ("G_noise_variant", {"definition": "mean_i a_i (b_lit,i - b_i) / noise_sd,i over strict-matched estimated designs with a finite positive noise SD; labelled stand-in scale, never pooled with G",
                             "value": G_noise, "n": len(noise_set), "F": len({r["family"] for r in noise_set}), "design_ids": [r["design_id"] for r in noise_set],
                             "per_design": [{"design_id": r["design_id"], "g_noise": r["g_noise"]} for r in noise_set]}),
        ("descriptive_raw_gaps", raw_gaps),
        ("matched_coverage", {"strict_matched_designs_in_D": len(matched_all), "strict_matched_estimated": len(matched_est), "EVAL": n,
                              "strict_matched_design_ids": [r["design_id"] for r in matched_all], "strict_matched_outside_D": outside,
                              "per_design": [{k: r.get(k) for k in ("design_id", "code", "live_status", "gate", "chosen", "alternatives", "fully_strict", "match_cells")} for r in matched_all]}),
        ("pairs", {"known_pairs_and_new_pairs_by_class": {f"{a}:{b}": c for (a, b), c in sorted(pair_classes.items())},
                   "pairs_total": len(pairs), "pairs_unaccounted": pair_unaccounted,
                   "new_pairs_by_live_state_and_class": {f"{a} | {b}": c for (a, b), c in sorted(new_by_state_class.items())}}),
        ("sensitivities", sens),
        ("designs_by_targets_class_and_live_status", dict(sorted(Counter(f"{r['targets_class'] or 'no_entry'} | {r['live_status']}" for r in rows).items()))),
        ("unavailable_designs", {r["design_id"]: r["unavailable_from"] for r in rows if r["code"] == "UNAVAILABLE"}),
        ("reconciliation", {"pairs": len(recon), "routes": dict(sorted(Counter(r.get("route") for r in recon).items())),
                            "final_classes": dict(sorted(Counter(r.get("final_class") for r in recon).items())),
                            "agreement_before_third_reader": (receipt or {}).get("agreement_before_third_reader")}),
    ])
    checks = {"live_vs_targets_class_mismatch": mismatch_class, "live_vs_targets_chosen_point_mismatch": mismatch_point,
              "v3_b_vs_live_point_estimate_mismatch": mismatch_b, "pairs_unaccounted": pair_unaccounted,
              "recon_pairs_missing_in_join": sorted(set(recon_by) - set(pair_row))}
    return result, rows, checks


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def load_inputs():
    live = read_json(LIVE)
    v3 = read_json(V3)
    targets = read_json(TARGETS)
    pairs = read_csv(PAIRS)
    recon = read_csv(RECON)
    return live, v3, targets, pairs, recon


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--live-set-changed-ok", action="store_true", help="accept a live set whose hash differs from the amendment record")
    args = ap.parse_args(argv)
    out_dir = Path(args.out_dir)

    rec = parse_hash_record()
    amend_sha = sha256_file(AMEND)
    if rec["sha256"] != amend_sha:
        sys.exit("amendment file differs from its hash record: not run")
    shas = {rel(p): sha256_file(p) for p in (LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT)}
    for p in (V3, TARGETS, PAIRS, RECON, RECEIPT):
        if rec["listed"].get(rel(p)) != shas[rel(p)]:
            sys.exit(f"{rel(p)} differs from the amendment record: not run")
    live_same = rec["listed"].get(rel(LIVE)) == shas[rel(LIVE)]
    if not live_same and not args.live_set_changed_ok:
        sys.exit("live set differs from the amendment record: not run (use --live-set-changed-ok for a new roster)")

    live, v3, targets, pairs, recon = load_inputs()
    result, rows, checks = assemble(live, v3, targets, pairs, recon, read_json(RECEIPT))
    me = Path(__file__).resolve()
    stamp = {"rule_id": RULE_ID, "question": QUESTION, "version": VERSION, "cutoff_utc": live["as_of"], "run_utc": utc_now(),
             "live_set_sha256": shas[rel(LIVE)], "live_set_matches_amendment_record": live_same, "first_run_table_sha256": shas[rel(V3)],
             "targets_r2_sha256": shas[rel(TARGETS)], "pairs_sha256": shas[rel(PAIRS)], "reconciled_sha256": shas[rel(RECON)],
             "code_sha256": sha256_file(me), "code": rel(me), "amendment_sha256": amend_sha, "t_amend_utc": rec["t_amend_utc"],
             "D": result["D"], "estimated": live["counts"]["estimated"], "not_evaluable": live["counts"]["not_evaluable"],
             "outside_D": {k: len(v) for k, v in live["excluded_not_counted"].items()},
             "n": result["n"], "F": result["F"], "python": sys.version.split()[0], "no_significance_label": True}
    payload = clean(OrderedDict([
        ("schema_version", "l3-meta-q2-v1"), ("evidence_status", "EXPLORATORY (EX-1); no confirmatory claim"), ("stamp", stamp),
        ("inputs", {rel(p): {"sha256": shas[rel(p)], "bytes": p.stat().st_size} for p in (LIVE, V3, TARGETS, PAIRS, RECON, RECEIPT)} | {rel(AMEND): {"sha256": amend_sha, "bytes": AMEND.stat().st_size}}),
        ("question", OrderedDict([("Q2", result)])), ("checks", checks), ("designs", rows),
        ("audit", {"l2_files_written": 0, "l2_trees_opened": 0, "downloads": 0, "network": False})]))
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / OUT_NAME
        if target.exists():
            sys.exit(f"{rel(target)} exists: not overwritten (use --out-dir for a new version folder)")
        text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        target.write_text(text, encoding="utf-8")
        copy = out_dir / "l2_set_live_used.json"
        shutil.copy2(LIVE, copy)
        assert sha256_file(copy) == shas[rel(LIVE)]
        with open(out_dir / "RUN_LOG.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_utc": stamp["run_utc"], "command": "build_q2.py " + " ".join(argv if argv is not None else sys.argv[1:]),
                                 "code_sha256": stamp["code_sha256"], "live_set_sha256": stamp["live_set_sha256"], "amendment_sha256": amend_sha,
                                 "results_file": rel(target), "results_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "exit": 0}) + "\n")
    brief = {"stamp": stamp, "Q2": payload["question"]["Q2"], "checks": payload["checks"]}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
    if not args.dry_run:
        print(f"wrote {rel(out_dir / OUT_NAME)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
