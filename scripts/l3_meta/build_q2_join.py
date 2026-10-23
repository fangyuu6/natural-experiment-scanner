#!/usr/bin/env python3
"""L3 package B, input 3: the Q2 join (paper catalogue x snapshot v5 x L2 live set x TARGETS_R2/DESIGNS).

RESTART_PLAN_20261015 section 3: "join the catalogue field already_in_repo, snapshot v5 and the L2 live set; two blind coders
strict-match the new pairs (policy, population, outcome, unit)".  This builder makes the pair table and a blind coding sheet; it
does NOT code any match.

  q2_pairs_v1.csv           hub-facing join, one row per (paper, repo design id), with L2 state and s_pre availability
  q2_coding_sheet_v1.csv    coder-facing sheet for the NEW pairs only: design metadata from DESIGNS.json (or the card) and the
                            paper's published headline; no L2 status, no estimate, no replication result; empty coder columns
  Q2_CODING_SHEET_README_v1.md   column guide and the protocol rules (EXTRACTION_PROTOCOL.md)
  RECEIPT_q2_join_v1.json   inputs, hashes, counts

Blindness: no RESULT.json value, no estimate, SE, noise value, forecast or replication result (snapshot `ours`, grades, stage) is
copied.  s_pre availability is a yes/no flag read from the key presence of the export block, never a value.

Run:  python3 -I -B scripts/l3_meta/build_q2_join.py [--out-dir DIR]     (standard library only)
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inputs_b_common as C  # noqa: E402

ROOT = C.ROOT
L2 = ROOT / "data/l2_pipeline"
VERSION = "v1"
INPUTS = {}


def note(role, path, **extra):
    p = Path(path)
    INPUTS[role] = {"path": C.rel(p), "bytes": p.stat().st_size, "sha256": C.sha256_file(p), **extra}
    return p


def jload(p):
    return json.loads(Path(p).read_text())


def norm_doi(x):
    if not x or not isinstance(x, str):
        return None
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", x)
    return m.group(0).lower().rstrip(".,;)") if m else None


def words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2}


def cand_doi_set(c):
    out = {norm_doi(c.get("doi"))}
    for u in c.get("urls") or []:
        out.add(norm_doi(u))
    out.add(norm_doi(c.get("citation")))
    out.discard(None)
    return out


def paper_doi_set(p):
    out = {norm_doi(p.get("doi")), norm_doi(p.get("doi_or_url"))}
    out |= {norm_doi(x) for x in (p.get("alt_dois") or [])}
    out.discard(None)
    return out


def match_in_targets(paper, entry):
    """Is the paper already one of the TARGETS_R2 candidates of this design?  -> (rank, basis) or (None, '')"""
    if not entry:
        return None, ""
    pd = paper_doi_set(paper)
    for c in entry.get("candidates") or []:
        if pd & cand_doi_set(c):
            return c.get("rank"), "doi"
    fa = (paper.get("first_author") or "").lower()
    tw = words(paper.get("title"))
    for c in entry.get("candidates") or []:
        cit = (c.get("citation") or "").lower()
        yr = c.get("year")
        if fa and fa in cit and isinstance(yr, int) and isinstance(paper.get("year"), int) and abs(yr - paper["year"]) <= 1:
            cw = words(cit)
            if tw and len(tw & cw) / len(tw) >= 0.5:
                return c.get("rank"), "first_author+year+title_overlap"
    return None, ""


def best_triage(p_snap):
    recs = [t.get("record") or {} for t in (p_snap.get("triage") or [])]
    recs = [r for r in recs if isinstance(r.get("headline"), dict)]
    if not recs:
        return None
    newer = [r for r in recs if r.get("decision_new") or r.get("journal_target_source")]
    return (newer or recs)[-1]


def pub_value(paper, snap):
    """Best published number: pinned snapshot target > triage headline > catalogue headline text."""
    prim = (snap or {}).get("primary") or {}
    tg = prim.get("target") if isinstance(prim, dict) else None
    if tg and tg.get("point") is not None:
        loc = " ".join(str(x) for x in (f"Table {tg.get('table')}" if tg.get("table") else "", f"col {tg.get('column')}" if tg.get("column") else "",
                                        f"row '{tg.get('row')}'" if tg.get("row") else "", f"p.{tg.get('printed_page')}" if tg.get("printed_page") else "") if x)
        return {"source": "snapshot_v5_primary_target", "point": tg.get("point"), "se_or_ci": tg.get("se") if tg.get("se") is not None else tg.get("ci"),
                "units": tg.get("units"), "locator": loc, "version": "journal" if prim.get("journal_verification", {}).get("verified") else "pinned",
                "verified": prim.get("target_verified")}
    tr = best_triage(snap or {})
    if tr:
        h = tr["headline"]
        loc = " ".join(str(x) for x in (f"Table {h.get('table')}" if h.get("table") else "", f"col {h.get('column')}" if h.get("column") else "",
                                        f"row '{h.get('row')}'" if h.get("row") else "") if x)
        return {"source": "triage_headline", "point": h.get("point"), "se_or_ci": h.get("se_or_ci"), "units": h.get("units"),
                "locator": loc, "version": h.get("version"), "verified": h.get("verified")}
    return {"source": "catalogue_headline_text" if paper.get("headline_estimate") else "none", "point": None, "se_or_ci": paper.get("se_or_ci"),
            "units": paper.get("estimate_units"), "locator": paper.get("table_ref") or "", "version": None, "verified": paper.get("estimate_verified")}


def card_meta(design_id, card_path):
    """Blind-safe design metadata from a CARD.json (fallback for designs that DESIGNS.json lacks)."""
    p = ROOT / card_path if card_path else None
    if not p or not p.exists():
        base = L2 / "autopilot/population_resolutions" / design_id
        c = sorted(base.glob("v*/CARD.json")) if base.is_dir() else []
        p = c[-1] if c else None
    if not p:
        return None
    card = jload(p)
    t, o, fo = card.get("treatment", {}), card.get("outcome", {}), card.get("first_order", {})
    return {"policy_family": card.get("policy_family"), "policy_definition": t.get("predicate"), "track": card.get("track"),
            "primary_outcome_id": o.get("measure_id"), "target_outcome_construct": fo.get("target_construct"),
            "outcome_unit_as_registered": o.get("outcome_unit"), "population_description": (card.get("population") or {}).get("description"),
            "outcome_years": o.get("years"), "our_estimand_label": (card.get("analysis") or {}).get("estimand"),
            "our_clock": (card.get("analysis") or {}).get("clock"), "card_path": C.rel(p)}


def s_pre_flags(live):
    """design_id -> True/False: does the export block of the first certified run carry a finite s_pre?  (flag only, never the value)"""
    reg = jload(note("terminal_runs", L2 / "registry/TERMINAL_RUNS.json"))
    first = {}
    for r in reg["runs"]:
        if r["status"] in ("COMPLETED", "STOPPED") and r["design_id"] not in first:
            first[r["design_id"]] = Path(r["package_path"])
    out = {}
    for d in live["designs"]:
        if d["status"] != "estimated":
            continue
        pkg = first.get(d["design_id"])
        ok = None
        if pkg is not None and (pkg / "L3_EXPORT_DATA.json").exists():
            blk = (jload(pkg / "L3_EXPORT_DATA.json").get("l3_export_v2") or {})
            v = blk.get("s_pre")
            ok = isinstance(v, (int, float)) and v == v and v > 0
        out[d["design_id"]] = bool(ok) if pkg is not None else False
    return out


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "data/l3_meta/q2_join"))
    OUT = Path(ap.parse_args().out_dir)

    cat = {}
    cp = note("paper_catalog_v1", ROOT / "data/replication/paper_catalog_v1.jsonl")
    for line in open(cp, encoding="utf-8"):
        r = json.loads(line)
        cat[r["paper_id"]] = r
    snap_p = note("snapshot_v5_latest", ROOT / "data/replication/snapshot_v5/latest.json")
    snap = jload(snap_p)
    snap_by = {p["paper_id"]: p for p in snap["papers"]}
    T = jload(note("targets_r2", ROOT / "data/l3_meta/replication_targets/TARGETS_R2.json"))
    tby = {d["design_id"]: d for d in T["designs"]}
    D = jload(note("designs_json", ROOT / "data/l3_meta/replication_targets/DESIGNS.json"))
    dby = {d["design_id"]: d for d in D["designs"]}
    live = jload(note("l2_set_live", ROOT / "data/l3_meta/l2_set_live.json"))
    lby = {d["design_id"]: d for d in live["designs"]}
    to_live = {}                                     # repo id -> (live design id, how)
    for d in live["designs"]:
        to_live.setdefault(d["design_id"], (d["design_id"], "direct"))
        b = d.get("benchmark")
        if b and b.get("target_id"):
            to_live.setdefault(b["target_id"], (d["design_id"], "benchmark.target_id"))
        fo = d.get("benchmark_first_order_variant")
        if fo and fo.get("target_id"):
            to_live.setdefault(fo["target_id"], (d["design_id"], "first_order_variant_of"))
    spre = s_pre_flags(live)

    pairs = []
    for pid in sorted(cat):
        p = cat[pid]
        for rid in p.get("already_in_repo") or []:
            pairs.append((pid, rid))
    hub_hdr = ["pair_id", "paper_id", "repo_id", "link_to_live", "live_design_id", "fo_variant_base_design", "l2_state", "live_source_group", "policy_family", "domain",
               "has_s_pre", "targets_entry", "targets_lifecycle", "targets_q2_class", "known_in_targets", "targets_candidate_rank",
               "targets_match_basis", "targets_chosen", "targets_strict_core", "paper_first_author", "paper_year", "paper_title", "paper_venue",
               "paper_doi", "paper_tier", "paper_design", "paper_unit", "paper_sample_years", "paper_n_treated_states", "paper_policy",
               "paper_outcome", "pub_source", "pub_point", "pub_se_or_ci", "pub_units", "pub_locator", "pub_version", "pub_verified"]
    hub, sheet, stats = [], [], collections.Counter()
    for pid, rid in pairs:
        p, ent, dsg = cat[pid], tby.get(rid), dby.get(rid)
        live_id, how = to_live.get(rid, (None, ""))
        ld = lby.get(live_id) if live_id and how != "first_order_variant_of" else None
        base_note = how if how == "first_order_variant_of" else ""
        if ld:
            l2_state = "estimated" if ld["status"] == "estimated" else f"not_evaluable:{ld.get('gate')}"
        elif how == "first_order_variant_of":
            l2_state = "first_order_variant_card:not_run"
        elif ent:
            l2_state = f"not_in_live_set:{ent.get('lifecycle')}"
        else:
            l2_state = "not_in_live_set:no_targets_entry"
        rank, basis = match_in_targets(p, ent)
        chosen = bool(ent and rank is not None and ent.get("chosen_candidate_rank") == rank)
        cand = next((c for c in (ent or {}).get("candidates", []) if c.get("rank") == rank), None) if rank is not None else None
        pv = pub_value(p, snap_by.get(pid))
        meta = dsg or card_meta(rid, (lby.get(rid) or {}).get("source", {}).get("card_path"))
        fam = (ld or {}).get("policy_family") or (meta or {}).get("policy_family") or (ent or {}).get("policy_group")
        hub.append([f"{pid}|{rid}", pid, rid, how, live_id if ld else "", live_id if how == "first_order_variant_of" else "", l2_state, (ld or {}).get("source_group"), fam, (ld or {}).get("domain"),
                    (int(spre.get(live_id, False)) if ld and ld["status"] == "estimated" else ""),
                    int(bool(ent)), (ent or {}).get("lifecycle"), (ent or {}).get("q2_class"), int(rank is not None), rank, basis,
                    int(chosen) if rank is not None else "", (int(bool(cand.get("strict_core"))) if cand else ""),
                    p.get("first_author"), p.get("year"), p.get("title"), p.get("venue"), p.get("doi") or p.get("doi_or_url"), p.get("tier"),
                    p.get("design"), p.get("unit"), p.get("sample_years"), p.get("n_treated_states"), p.get("policy"), p.get("outcome"),
                    pv["source"], pv["point"], pv["se_or_ci"], pv["units"], pv["locator"], pv["version"], pv["verified"]])
        stats["pairs"] += 1
        stats["known_in_targets" if rank is not None else "new"] += 1
        if rank is None:
            stats["new_with_numeric_published_point"] += int(pv["point"] is not None)
            sheet.append([f"{pid}|{rid}", rid,
                          (meta or {}).get("policy_definition"), (meta or {}).get("track"), (meta or {}).get("primary_outcome_id"),
                          (meta or {}).get("target_outcome_construct"), (meta or {}).get("outcome_unit_as_registered"),
                          (meta or {}).get("population_description"), json.dumps((meta or {}).get("outcome_years")),
                          (meta or {}).get("our_estimand_label"), (meta or {}).get("our_clock"),
                          "DESIGNS.json" if dsg else ("CARD.json" if meta else "missing"),
                          pid, p.get("first_author"), p.get("year"), p.get("title"), p.get("venue"), p.get("doi") or p.get("doi_or_url"),
                          p.get("design"), p.get("policy"), p.get("outcome"), p.get("unit"), p.get("sample_years"), p.get("n_treated_states"),
                          p.get("data_source"), pv["point"], pv["se_or_ci"], pv["units"], pv["locator"], pv["version"], pv["verified"]])
    sheet_hdr = ["pair_id", "design_id", "our_policy_definition", "our_track", "our_primary_outcome_id", "our_target_outcome_construct",
                 "our_outcome_unit", "our_population", "our_outcome_years", "our_estimand", "our_clock", "design_metadata_source",
                 "paper_id", "paper_first_author", "paper_year", "paper_title", "paper_venue", "paper_doi", "paper_design", "paper_policy",
                 "paper_outcome", "paper_unit_of_analysis", "paper_sample_years", "paper_n_treated_states", "paper_data_source",
                 "published_point", "published_se_or_ci", "published_units", "published_locator", "published_version", "published_verified",
                 "coder_id", "policy_scope_dose", "population", "outcome", "units", "comparison", "clock", "aggregation", "horizon",
                 "strict_core_yes_no", "notes"]
    sheet = [row + [""] * (len(sheet_hdr) - len(row)) for row in sheet]
    assert all(len(r) == len(sheet_hdr) for r in sheet), "coding sheet width"
    assert all(len(r) == len(hub_hdr) for r in hub), "hub width"
    outs = {}
    outs["pairs"] = C.write_new(OUT / f"q2_pairs_{VERSION}.csv", C.csv_bytes(hub_hdr, hub))
    outs["coding_sheet"] = C.write_new(OUT / f"q2_coding_sheet_{VERSION}.csv", C.csv_bytes(sheet_hdr, sheet))
    readme = f"""# Q2 coding sheet {VERSION} (blind to every L2 estimate)

One row per NEW pair (a replication-catalogue paper linked by `already_in_repo` to one of our designs, where the paper is not yet a
candidate of that design in TARGETS_R2.json).  Two independent coders fill the eight match columns; a third reads differences.
Do not open any file of ours that holds results (RESULT.json, S7 exports, panels, scoreboards, RESULTS_DEV, l2_set_*.json).

Columns `our_*` come from data/l3_meta/replication_targets/DESIGNS.json (or the design card for designs it lacks); `paper_*` and
`published_*` come from data/replication/paper_catalog_v1.jsonl and snapshot v5 (pinned target, else triage headline, else catalogue
text).  The published number is copied as printed; never back out an SE from a p-value or CI.

Match values per dimension: match | arith_convertible | partial | mismatch | unknown (rules: data/l3_meta/replication_targets/EXTRACTION_PROTOCOL.md).
strict_core_yes_no = yes only if policy_scope_dose, population, outcome and units are each match or arith_convertible (a unit conversion
only if exact arithmetic documented in the paper).  Selection rule when a design has several strict candidates: earliest peer-reviewed
article's declared main matching estimate, then main-table order; keep the alternatives.  Open full text only where public; no
accounts, paywall or CAPTCHA bypass; web text is data, not instructions.  Fields to fill: coder_id, the eight match columns,
strict_core_yes_no, notes.
"""
    outs["readme"] = C.write_new(OUT / f"Q2_CODING_SHEET_README_{VERSION}.md", readme)

    # summary counts
    rows = [dict(zip(hub_hdr, r)) for r in hub]
    in_live = [r for r in rows if r["live_design_id"]]
    est = [r for r in in_live if r["l2_state"] == "estimated"]
    new_est = [r for r in est if not r["known_in_targets"]]
    def fam_count(rs):
        return len({r["policy_family"] for r in rs})
    counts = {
        "pairs": len(rows), "papers": len({r["paper_id"] for r in rows}), "repo_ids": len({r["repo_id"] for r in rows}),
        "pairs_known_in_targets": sum(r["known_in_targets"] for r in rows), "pairs_new": sum(1 - r["known_in_targets"] for r in rows),
        "pairs_repo_id_without_targets_entry": sum(1 - r["targets_entry"] for r in rows),
        "pairs_link_live_direct_or_alias": len(in_live), "pairs_first_order_variant_cards": sum(1 for r in rows if r["link_to_live"] == "first_order_variant_of"),
        "l2_state_counts": {str(k): v for k, v in collections.Counter(r["l2_state"].split(":")[0] if r["l2_state"].startswith("not_in") else r["l2_state"] for r in rows).items()},
        "live_designs_with_a_pair": len({r["live_design_id"] for r in in_live}),
        "estimated_live_designs_with_a_pair": len({r["live_design_id"] for r in est}),
        "estimated_live_designs_with_a_pair_and_s_pre": len({r["live_design_id"] for r in est if r["has_s_pre"] == 1}),
        "families_of_estimated_live_designs_with_a_pair": fam_count(est),
        "estimated_designs_with_a_NEW_pair": len({r["live_design_id"] for r in new_est}),
        "families_of_estimated_designs_with_a_NEW_pair": fam_count(new_est),
        "estimated_designs_with_a_NEW_pair_and_numeric_published_point": len({r["live_design_id"] for r in new_est if r["pub_point"] not in ("", None)}),
        "families_of_estimated_designs_with_a_NEW_pair_and_numeric_published_point": fam_count([r for r in new_est if r["pub_point"] not in ("", None)]),
        "new_pairs_with_numeric_published_point": stats["new_with_numeric_published_point"],
        "pub_source_counts": {str(k): v for k, v in collections.Counter(r["pub_source"] for r in rows).items()},
        "targets_q2_class_of_linked_designs": {str(k): v for k, v in collections.Counter(r["targets_q2_class"] for r in rows).items()},
        "strict_possible_designs_in_targets_r2": sum(1 for d in T["designs"] if d.get("q2_class") == "strict_possible"),
        "live_estimated_strict_possible_with_s_pre": sorted(d["design_id"] for d in live["designs"] if d["status"] == "estimated"
                                                            and ((d.get("benchmark") or {}).get("q2_class") == "strict_possible"
                                                                 or (d.get("benchmark_first_order_variant") or {}).get("q2_class") == "strict_possible")),
    }
    counts["live_estimated_strict_possible_with_s_pre_flag"] = {d: spre.get(d, False) for d in counts["live_estimated_strict_possible_with_s_pre"]}
    receipt = {"schema": "l3_inputs_b_q2_join_receipt_v1", "version": VERSION, "built_utc": C.utc_now(),
               "builder": {"path": C.rel(__file__), "sha256": C.sha256_file(Path(__file__)), "python": sys.version.split()[0]},
               "snapshot_v5_as_of": snap.get("as_of"), "live_set_as_of": live.get("as_of"),
               "rules": {"pairs": "one row per (catalogue paper, already_in_repo id); ids resolve to TARGETS_R2 design ids (same namespace) and to live designs directly, via benchmark.target_id, or as first-order variant cards",
                         "known_vs_new": "a pair is known if the paper is a TARGETS_R2 candidate of that design (same DOI, or first author + year within 1 + >= 50% title-word overlap)",
                         "published_value": "pinned snapshot target > triage headline (newest) > catalogue headline text; no replication result copied",
                         "coding": "none done here: the eight match columns of the sheet are empty for two blind coders"},
               "inputs": INPUTS, "outputs": outs, "counts": counts,
               "limitations": ["Pairs are candidates, not matches: a paper linked to a repo design by policy family may measure another outcome or population.",
                               "Snapshot v5 is the 2026-10-13 capture; the live set is the 2026-10-15T16:14:57Z build; later versions need a new join version.",
                               "has_s_pre reads the export block of the first certified run; only the 7 autopilot designs carry it at this live set."]}
    outs["receipt"] = C.write_new(OUT / f"RECEIPT_q2_join_{VERSION}.json", C.json_bytes(receipt))
    for k, v in outs.items():
        print(k, v["status"], v["path"], v["bytes"])
    print(json.dumps(counts, indent=1))


if __name__ == "__main__":
    main()
