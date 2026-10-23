#!/usr/bin/env python3
"""L3 meta, Q2 blockers listing (2026-10-16): what stops every strict-matched design, and every catalogue paper that could still get a strict match.

Not a Q2 statistic and not a new version of Q2: a listing built from files on disk (Q2-V2 results, TARGETS_R2, the pair join and the two blind codings,
the live set, the paper catalogue, the pre-screen dry run of the existing cards, and, read-only, two gate-0 re-evaluation receipts of the L2 registry).
Writes (new folder files, once): Q2_BLOCKERS_20261016.csv, Q2_CATALOGUE_FUNNEL_20261016.json.  Nothing under scripts/l2_pipeline or data/l2_pipeline
is written.  No network.

Sections of the CSV:
  A  the strict-matched designs (TARGETS_R2 class strict_possible), with the exact L2 blockers
  B  papers whose core cells could not be verified (UNAVAILABLE), with the design's L2 state
  C  candidates that pass 3 of the 4 core cells in some coding (one failing core cell), with the failing cell and the kind of fix it would need
  D  catalogue papers outside the join that survive a state-year DiD / level-units screen (the funnel)

Run:  python3 -I -B scripts/l3_meta/build_q2_blockers.py [--dry-run]     (standard library only)
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_q2 as V1  # noqa: E402  (pure functions: classify_known_pair, DIMS)

D = ROOT / "data" / "l3_meta"
OUT = D / "q2_v2"
OUT_CSV = OUT / "Q2_BLOCKERS_20261016.csv"
OUT_JSON = OUT / "Q2_CATALOGUE_FUNNEL_20261016.json"
PASS = ("match", "arith_convertible")
CORE = V1.DIMS[:4]
FIELDS = ["section", "id", "design_id", "paper", "year", "published_number", "published_units", "l2_lifecycle", "live_status", "live_gate", "l2_detail",
          "s_pre_status", "blockers", "failing_core_cells", "fix_needed", "actor", "priority", "note"]

# researcher annotations for section A, written from the files named in the 'note' and verified against them (see Q2_L2_REQUESTS_20261016.md)
ANNOT = {
    "proto_adult_use_cannabis": dict(
        blockers="NO_L2_S_PRE (resolved by the L3 read in Q2-V2); NOT_REPORTABLE (own placebo gate); DIRECTION_SET_AFTER_ESTIMATE",
        fix="none for eligibility; Q2-V2 n counts it", actor="L3 (done)", priority="done",
        note="archive prototype, estimate archived 2026-10-03; log income per capita, published value from the KC Fed working paper"),
    "proto_castle_doctrine": dict(
        blockers="NO_L2_S_PRE (resolved by the L3 read in Q2-V2); NOT_REPORTABLE (stopped at pretrend_joint); NO_NOISE_LINE; DIRECTION_SET_AFTER_ESTIMATE",
        fix="none for eligibility; Q2-V2 n counts it", actor="L3 (done)", priority="done",
        note="archive prototype, estimate archived 2026-10-02; published value read from the NBER working paper version"),
    "b1010_strict_voter_id": dict(
        blockers="NOT_ESTIMATED:history; HISTORY_MIN_TREATED (5 retained treated < minimum 6; 14 states excluded for conflicting, interrupted or unknown histories: AR AZ GA IA LA MO NC ND NH OH PA SC TX WI); FRESH_AUDIT_UNAVAILABLE (after prior-sample exclusions); NEEDS_RULING (gate-0 re-evaluation 2026-10-12: genuinely new construct question)",
        fix="not repairable by a card or by code: it would need a hub ruling on the construct and a change of the registered minimum of 6 treated states (a gate change that this listing does not recommend); the even-year support adapter (biennial_even_year_v1, C55) already exists and is not the blocker",
        actor="HUB_RULING (gate change)", priority="5 (structurally unlikely)",
        note="gate and reason from the live set and the PARKED record; the history gate cannot be repaired by a new card"),
    "bulk10_health_gestational_limit_infant_mortality": dict(
        blockers="NOT_ESTIMATED:connector (purpose stage); C46_SECTION_1_DOWNSTREAM (statutory target is late abortion, infant mortality is downstream; reopen only with a late-abortion series); NEEDS_RULING (gate-0 re-evaluation 2026-10-12)",
        fix="hub replication-exception ruling that lets the registered outcome be the paper's outcome (wonder_infant_mortality_rate, infant deaths per 1,000 live births = the Karletsos unit); then S0 to S6 as a normal design (fresh history pool 13 per the dry run)",
        actor="HUB_RULING", priority="1 (one ruling, no code)",
        note="dry run 2026-10-16 on the card: pre-screen PROCEED_inconclusive_only (no certified support sidecar), first_order_gate False, V6.3 mechanical series none"),
    "b1011c_yrbs_medical_cannabis_youth_use": dict(
        blockers="NOT_IN_ROSTER (card never ingested); PRESCREEN_PARK s3 (ri follows from s3): registered support insufficient on the odd-year grid, insufficient_common_clean_anchor and insufficient_treated; the odd-year adapter biennial_odd_year_v1 exists and is used; Q2_YRBS_WINDOW_GRID: 0 of 144 legal window and gap-rule configurations pass (legal = last wave not after 2013-12-31)",
        fix="no card repair exists. Only a change of the registered gates for survey series (common clean anchor, minimum 6 treated) or a fuller YRBS state source (the Socrata H28 state totals hold MA, WA, MN, OR and DC, which the SADC extracts lack) would change the verdict; neither is requested here",
        actor="HUB_RULING (gate change) or L2_CONNECTOR (fuller source)", priority="4",
        note="bound validated card: data/l2_pipeline/autopilot/c63/cards_yrbs_validated/; a pending copy sits in intake/bulk_20261011c/health_energy/cards_yrbs_pending/"),
    "b1011c_yrbs_primary_seatbelt_teen_belt_use": dict(
        blockers="NOT_IN_ROSTER (card never ingested); PRESCREEN_PARK s3 (ri follows from s3): registered support insufficient on the odd-year grid, insufficient_common_clean_anchor and insufficient_treated; the odd-year adapter biennial_odd_year_v1 exists and is used; Q2_YRBS_WINDOW_GRID: 0 of 360 legal window and gap-rule configurations pass (legal = last wave not after 2023-12-31)",
        fix="no card repair exists. Only a change of the registered gates for survey series (common clean anchor, minimum 6 treated) or a fuller YRBS state source (the Socrata H28 state totals hold MA, WA, MN, OR and DC, which the SADC extracts lack) would change the verdict; neither is requested here",
        actor="HUB_RULING (gate change) or L2_CONNECTOR (fuller source)", priority="4",
        note="working-paper benchmark (Carpenter and Stehr 2007); bound validated card in autopilot/c63/cards_yrbs_validated/"),
    "b1011c_yrbs_same_sex_marriage_suicide_attempt": dict(
        blockers="NOT_IN_ROSTER (card never ingested); PRESCREEN_PARK s3 (ri follows from s3): registered support insufficient on the odd-year grid, insufficient_common_clean_anchor and insufficient_treated (6 configurations pass only with a last wave of 2017 or later, after the declared legal end 2015-06-25, when every state is treated); the odd-year adapter biennial_odd_year_v1 exists and is used; Q2_YRBS_WINDOW_GRID: 0 of 180 legal window and gap-rule configurations pass (legal = last wave not after 2015-06-25)",
        fix="no card repair exists. Only a change of the registered gates for survey series (common clean anchor, minimum 6 treated) or a fuller YRBS state source (the Socrata H28 state totals hold MA, WA, MN, OR and DC, which the SADC extracts lack) would change the verdict; neither is requested here",
        actor="HUB_RULING (gate change) or L2_CONNECTOR (fuller source)", priority="4",
        note="the strict benchmark is Raifman et al. 2017 (JAMA Pediatrics); Anderson et al. 2021 is an alternative; bound validated card in autopilot/c63/cards_yrbs_validated/"),
    "run2_meth_precursor_restrictions_fo": dict(
        blockers="NOT_IN_ROSTER (first-order variant card, not run; the executed design run2_meth_precursor_restrictions is in D with the overdose outcome and no benchmark); NO_CONNECTOR (methamphetamine lab seizures are not a series of OUTCOME_CATALOG_V15)",
        fix="a state-year clandestine-lab seizure series with a connector, then ingest the variant card",
        actor="L2_CONNECTOR", priority="4 (a new data source)",
        note="Dobkin, Nicosia, Weinberg 2014 (monthly NCLSS counts x12 = annual counts)"),
}


def rel(p):
    try:
        return str(Path(p).resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def jl(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def cl(p):
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def core_pass(m):
    return sum(1 for d in CORE if (m or {}).get(d) in PASS)


FIX_BY_CELL = {
    "units": "units: log / ratio / percent-change / count-model result against a level (or a differently scaled rate): needs a registered log or ratio outcome in L2, or a level table in the paper",
    "population": "population: an age, sex or subgroup series (or a survey population) that the catalog does not carry for this construct",
    "outcome": "outcome: a different construct or a different series for the same construct",
    "policy_scope_dose": "policy scope or dose: a different statutory definition or intensity than our predicate",
}


def fix_for(cells):
    return "; ".join(FIX_BY_CELL[c] for c in cells) if cells else ""


# ---------------------------------------------------------------------------------------------
def gate0_receipt_text(design_id):
    """The 'inconclusive' text of the newest gate-0 park re-evaluation receipt of a design (L2 registry tree, read only)."""
    hits = sorted(glob.glob(str(ROOT / "data/l2_pipeline/autopilot" / design_id / "*" / "gate0" / "*" / "PARK_REEVALUATION.json")))
    if not hits:
        return None
    r = jl(hits[-1])
    return {"receipt": rel(hits[-1]), "inconclusive": r.get("inconclusive"), "requeued": r.get("requeued")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    res = jl(OUT / "RESULTS_Q2_V2.json")
    q2 = res["question"]["Q2"]
    live = {d["design_id"]: d for d in jl(D / "l2_set_live.json")["designs"]}
    by_target = {}
    for d in live.values():
        for key in ("benchmark", "benchmark_first_order_variant"):
            b = d.get(key)
            if b and b.get("target_id"):
                by_target.setdefault(b["target_id"], (d, key))
    targets = {e["design_id"]: e for e in jl(D / "replication_targets" / "TARGETS_R2.json")["designs"]}
    spre = {r["design_id"]: r for r in jl(D / "s_pre_l3_v1" / "S_PRE_L3_V1.json")["reads"]}
    v2rows = {r["design_id"]: r for r in res["designs"]}
    dry = {r["design_id"]: r for r in jl(OUT / "prescreen_dry_run" / "dry_run_report.json")}
    pairs = {p["pair_id"]: p for p in cl(D / "q2_join" / "q2_pairs_v1.csv")}
    recon = {r["pair_id"]: r for r in cl(D / "q2_join" / "q2_reconciled_v1.csv")}
    c1 = {r["pair_id"]: r for r in cl(D / "q2_join" / "q2_codes_coder1_v1.csv")}
    c2 = {r["pair_id"]: r for r in cl(D / "q2_join" / "q2_codes_coder2_v1.csv")}
    third = {r["pair_id"]: r for r in cl(D / "q2_join" / "q2_third_reader_v1.csv")}
    rows = []

    # ---- A: strict-matched designs
    strict = [e for e in targets.values() if e.get("q2_class") == "strict_possible"]
    for e in sorted(strict, key=lambda x: x["design_id"]):
        tid = e["design_id"]
        ch = next(c for c in e["candidates"] if c.get("rank") == e.get("chosen_candidate_rank"))
        est = ch.get("estimate") or {}
        L, key = by_target.get(tid, (None, None))
        a = ANNOT[tid]
        lifecycle, status, gate, detail = e.get("lifecycle"), None, None, ""
        if L is not None and key == "benchmark":
            status, gate = L["status"], L.get("gate")
            lifecycle = "in roster D"
            detail = (f"{L.get('gate_reason') or ''} | stage {L.get('stage')}" if L["status"] != "estimated"
                      else f"estimated {L.get('status_detail')}; reportable={L.get('reportable')}")
            g0 = gate0_receipt_text(L["design_id"]) if L["status"] != "estimated" else None
            if g0:
                detail += f" | gate-0 re-evaluation: {g0['inconclusive']}"
        else:
            lifecycle = f"outside D ({e.get('lifecycle')})"
            did = tid if tid in dry else None
            if did:
                dr = dry[did]
                detail = f"dry run 2026-10-16: pre-screen {dr['prescreen']} {dr.get('prescreen_reasons')}; first_order_gate {dr['first_order_gate']} {dr.get('first_order_issues')}"
        if tid == "bulk10_health_gestational_limit_infant_mortality":
            dr = dry[tid]
            detail += f" | dry run 2026-10-16: pre-screen {dr['pass_type']}; first_order_gate {dr['first_order_gate']} {dr.get('first_order_issues')}"
        sp = ""
        v2 = v2rows.get(L["design_id"]) if L is not None and key == "benchmark" else None
        if v2 and v2.get("live_status") == "estimated":
            r = spre.get(L["design_id"])
            sp = f"L2 export none; L3 read {r['s_pre_L3']:.4f} (n {r['n']}), used in Q2-V2" if r and r.get("used_in_q2") else "none"
        elif L is not None and key == "benchmark":
            sp = "not applicable (not estimated)"
        rows.append(OrderedDict(section="A", id=tid, design_id=(L or {}).get("design_id", tid), paper=ch.get("citation"), year=ch.get("year"),
                                published_number=est.get("point_as_printed") or est.get("point"), published_units=est.get("units"), l2_lifecycle=lifecycle,
                                live_status=status, live_gate=gate, l2_detail=detail, s_pre_status=sp, blockers=a["blockers"], failing_core_cells="",
                                fix_needed=a["fix"], actor=a["actor"], priority=a["priority"], note=a["note"]))

    # ---- B: UNAVAILABLE (core cell not verifiable from public non-paywalled text)
    n_b = 0
    for did, srcs in sorted(q2["unavailable_designs"].items()):
        L = live.get(did, {})
        for s in srcs:
            if s == "targets_r2":
                e = next((x for x in targets.values() if x["design_id"] == (L.get("benchmark") or {}).get("target_id")), None)
                if not e:
                    continue
                for c in e["candidates"]:
                    rows.append(OrderedDict(section="B", id=e["design_id"], design_id=did, paper=c.get("citation"), year=c.get("year"), published_number="",
                                            published_units="", l2_lifecycle="in roster D", live_status=L.get("status"), live_gate=L.get("gate"),
                                            l2_detail=f"{L.get('gate_reason') or ''}", s_pre_status="", blockers=f"ACCESS ({c.get('access') or c.get('evidence_access') or 'abstract only'})",
                                            failing_core_cells="unverifiable", fix_needed="full text or table of the paper from a library copy (no author contact); then the two-coder lock",
                                            actor="ACCESS (owner library / computer-use request)", priority="5",
                                            note=(e.get("class_reason") or "")[:300]))
                    n_b += 1
            else:
                p, t = pairs[s], third[s]
                rows.append(OrderedDict(section="B", id=s, design_id=did, paper=f"{p['paper_first_author']} {p['paper_year']}, {p['paper_venue']}", year=p["paper_year"],
                                        published_number=p.get("pub_point"), published_units=p.get("pub_units"), l2_lifecycle="in roster D" if did in live else "outside D",
                                        live_status=L.get("status"), live_gate=L.get("gate"), l2_detail=p.get("l2_state"), s_pre_status="",
                                        blockers="ACCESS (third reader: abstract only, paywall or 403)", failing_core_cells="unverifiable",
                                        fix_needed="full text or table from a library copy; then the lock", actor="ACCESS (owner library / computer-use request)", priority="5",
                                        note=(t.get("tr_basis") or "")[:300]))
                n_b += 1
    # UNAVAILABLE new pairs on designs outside D
    for pid, t in third.items():
        if t["tr_final_class"] == "UNAVAILABLE" and pairs[pid]["live_design_id"] not in q2["unavailable_designs"]:
            p = pairs[pid]
            rows.append(OrderedDict(section="B", id=pid, design_id=p["live_design_id"] or p["repo_id"], paper=f"{p['paper_first_author']} {p['paper_year']}, {p['paper_venue']}",
                                    year=p["paper_year"], published_number=p.get("pub_point"), published_units=p.get("pub_units"), l2_lifecycle="outside D",
                                    live_status="", live_gate="", l2_detail=p.get("l2_state"), s_pre_status="", blockers="ACCESS (third reader: abstract only, paywall or 403)",
                                    failing_core_cells="unverifiable", fix_needed="full text or table from a library copy; then the lock",
                                    actor="ACCESS (owner library / computer-use request)", priority="5", note=(t.get("tr_basis") or "")[:300]))
            n_b += 1

    # ---- C: one failing core cell
    flip_ids = set(V1.FLIP_PAIRS)
    known_pair_of = {}
    for pid, p in pairs.items():
        if str(p.get("known_in_targets")) == "1":
            known_pair_of[(p["repo_id"], p.get("targets_candidate_rank"))] = pid
    n_c_known = n_c_new = 0
    for e in sorted(targets.values(), key=lambda x: x["design_id"]):
        if e.get("q2_class") == "strict_possible":
            continue
        for c in e.get("candidates") or []:
            m = c.get("match") or {}
            if core_pass(m) == 3 and not c.get("strict_core"):
                failing = [d for d in CORE if m.get(d) not in PASS]
                pid = known_pair_of.get((e["design_id"], str(c.get("rank"))))
                L, _k = by_target.get(e["design_id"], (None, None))
                rows.append(OrderedDict(section="C", id=pid or f"{e['design_id']}#rank{c.get('rank')}", design_id=(L or {}).get("design_id", e["design_id"]),
                                        paper=c.get("citation"), year=c.get("year"), published_number=(c.get("estimate") or {}).get("point_as_printed") or (c.get("estimate") or {}).get("point"),
                                        published_units=(c.get("estimate") or {}).get("units"), l2_lifecycle=("in roster D" if L else f"outside D ({e.get('lifecycle')})"),
                                        live_status=(L or {}).get("status"), live_gate=(L or {}).get("gate"), l2_detail="", s_pre_status="",
                                        blockers=("CORE_ONLY (a non-core cell is a documented mismatch); " if e.get("q2_class") == "non_strict" and m and any(m.get(d) == "mismatch" for d in V1.DIMS[4:]) else "")
                                        + "CELL_MISMATCH:" + ",".join(failing) + ("; READING_FLIP" if pid in flip_ids else ""),
                                        failing_core_cells=",".join(failing), fix_needed=fix_for(failing), actor="L1_CARD_OR_L2_CODE (a different design, not a repair)", priority="6",
                                        note=str((c.get("match_notes") or {}).get(failing[0], "") if isinstance(c.get("match_notes"), dict) else "")[:200]))
                n_c_known += 1
    for pid in sorted(c1):
        a_pass, b_pass = core_pass(c1[pid]), core_pass(c2[pid])
        if max(a_pass, b_pass) >= 3 and recon[pid]["final_class"] != "STRICT":
            p = pairs[pid]
            best = c1[pid] if a_pass >= b_pass else c2[pid]
            failing = [d for d in CORE if best[d] not in PASS]
            rows.append(OrderedDict(section="C", id=pid, design_id=p["live_design_id"] or p["repo_id"], paper=f"{p['paper_first_author']} {p['paper_year']}, {p['paper_venue']}",
                                    year=p["paper_year"], published_number=p.get("pub_point"), published_units=p.get("pub_units"),
                                    l2_lifecycle="in roster D" if p["live_design_id"] in live else "outside D", live_status=live.get(p["live_design_id"], {}).get("status"),
                                    live_gate=live.get(p["live_design_id"], {}).get("gate"), l2_detail=p.get("l2_state"), s_pre_status="",
                                    blockers=f"CELL_MISMATCH:{','.join(failing)} (coder cells {a_pass} and {b_pass} of 4 pass; final {recon[pid]['final_class']})" + ("; READING_FLIP" if pid in flip_ids else ""),
                                    failing_core_cells=",".join(failing), fix_needed=fix_for(failing), actor="L1_CARD_OR_L2_CODE (a different design, not a repair)", priority="6",
                                    note=(third.get(pid, {}).get("tr_basis") or "")[:200]))
            n_c_new += 1

    # ---- D: catalogue funnel
    cat = [json.loads(l) for l in open(ROOT / "data/replication/paper_catalog_v1.jsonl", encoding="utf-8")]

    def f1(r):
        return r.get("design") in ("DiD", "staggered_DiD", "event_study")

    def f2(r):
        return (r.get("unit") or "").lower().startswith("state-year")

    snap = jl(ROOT / "data/replication/snapshot_v5/latest.json")
    snap_by = {s["paper_id"]: s for s in snap["papers"]}

    def best_triage(s):
        recs = [(t.get("record") or {}) for t in (s.get("triage") or [])]
        recs = [r for r in recs if isinstance(r.get("headline"), dict)]
        if not recs:
            return None
        newer = [r for r in recs if r.get("decision_new") or r.get("journal_target_source")]
        return (newer or recs)[-1]

    def best_units(r):
        """Same order as the Q2 join (build_q2_join.pub_value): snapshot pinned target, then triage headline, then the catalogue text."""
        s = snap_by.get(r["paper_id"]) or {}
        prim = s.get("primary") or {}
        tg = prim.get("target") if isinstance(prim, dict) else None
        if tg and tg.get("point") is not None:
            return "snapshot_target", tg.get("units"), tg.get("point")
        tr = best_triage(s)
        if tr:
            h = tr["headline"]
            return "triage", h.get("units"), h.get("point")
        return "catalogue", r.get("estimate_units"), r.get("headline_estimate")

    def ucls(r):
        _src, units, point = best_units(r)
        eu = (units or "").lower()
        if point in (None, ""):
            return "no_headline"
        if re.search(r"\blog\b|log point|ln\(|natural log|semi-log|elasticity", eu):
            return "log"
        if re.search(r"percent change|% change|percent (increase|decrease|reduction|higher|lower)|approximate percent|relative|percent\b", eu) and not re.search(r"percentage point", eu):
            return "percent_change"
        if re.search(r"odds|irr|ratio|hazard", eu):
            return "ratio_or_odds"
        if re.search(r"percentage point|per ?100,?000|per ?1,?000|deaths per|rate per|per capita|dollars|usd|cents|persons|births per|hours|probability", eu):
            return "level_or_pp"
        return "other"

    stages = OrderedDict()
    stages["catalogue papers"] = cat
    stages["design is DiD, staggered DiD or event study"] = [r for r in cat if f1(r)]
    stages["... and unit is state-year"] = [r for r in cat if f1(r) and f2(r)]
    stages["... and a headline with level or percentage-point units"] = [r for r in cat if f1(r) and f2(r) and ucls(r) == "level_or_pp"]
    funnel = OrderedDict((k, {"n": len(v), "linked_to_an_L2_design": sum(1 for r in v if r.get("already_in_repo")), "not_linked": sum(1 for r in v if not r.get("already_in_repo"))})
                         for k, v in stages.items())
    unit_mix_state_year = dict(Counter(ucls(r) for r in cat if f1(r) and f2(r)))
    unit_mix_all_did = dict(Counter(ucls(r) for r in cat if f1(r)))
    survivors = stages["... and a headline with level or percentage-point units"]
    for r in survivors:
        linked = r.get("already_in_repo") or []
        pcl = []
        for rid in linked:
            pid = f"{r['paper_id']}|{rid}"
            p = pairs.get(pid)
            if p is None:
                continue
            cls = V1.classify_known_pair(p.get("targets_q2_class"), p.get("targets_strict_core")) if str(p.get("known_in_targets")) == "1" else recon.get(pid, {}).get("final_class")
            pcl.append(f"{rid}: {cls}")
        why = ("linked: the pair is already coded; see the class and section C" if linked else
               "not linked: no L2 design for this policy; a card needs a certifiable state adoption list and a catalog series with the paper's construct, population and unit")
        rows.append(OrderedDict(section="D", id=r["paper_id"], design_id=",".join(linked), paper=f"{r.get('first_author')} {r.get('year')}, {r.get('venue')}", year=r.get("year"),
                                published_number=best_units(r)[2], published_units=best_units(r)[1], l2_lifecycle="linked" if linked else "no L2 design",
                                live_status="", live_gate="", l2_detail="; ".join(pcl), s_pre_status="", blockers="" if linked else "NO_L2_DESIGN",
                                failing_core_cells="", fix_needed=why, actor="", priority="7" if not linked else "", note=f"{r.get('policy_family')}; {str(r.get('outcome'))[:90]}; sample {r.get('sample_years')}; units from {best_units(r)[0]}"))

    # ---- output
    summary = OrderedDict([
        ("generated_from", {rel(p): sha256_file(p) for p in (OUT / "RESULTS_Q2_V2.json", D / "l2_set_live.json", D / "replication_targets" / "TARGETS_R2.json",
                                                              D / "q2_join" / "q2_pairs_v1.csv", D / "q2_join" / "q2_reconciled_v1.csv", D / "s_pre_l3_v1" / "S_PRE_L3_V1.json",
                                                              OUT / "prescreen_dry_run" / "dry_run_report.json", ROOT / "data/replication/paper_catalog_v1.jsonl",
                                                              ROOT / "data/replication/snapshot_v5/latest.json")}),
        ("funnel", funnel), ("unit_mix_of_state_year_did_papers", unit_mix_state_year), ("unit_mix_of_all_did_type_papers", unit_mix_all_did),
        ("section_counts", dict(sorted(Counter(r["section"] for r in rows).items()))),
        ("section_C_known_candidates", n_c_known), ("section_C_new_pairs", n_c_new),
        ("section_D_survivors", [{"paper_id": r["paper_id"], "linked": r.get("already_in_repo") or []} for r in survivors]),
        ("note", "Listing, not a Q2 statistic; no version of Q2 is changed by it. Sections A to D are in Q2_BLOCKERS_20261016.csv."),
    ])
    if not args.dry_run:
        if OUT_CSV.exists() or OUT_JSON.exists():
            sys.exit("output exists: not overwritten")
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("funnel", "unit_mix_of_state_year_did_papers", "section_counts", "section_C_known_candidates", "section_C_new_pairs")}, indent=1))
    for r in rows:
        if r["section"] == "A":
            print(f"A | {r['id']} | {r['l2_lifecycle']} | {r['live_status']} {r['live_gate']} | {r['blockers'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
