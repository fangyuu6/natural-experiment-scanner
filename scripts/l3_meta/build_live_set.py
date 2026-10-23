#!/usr/bin/env python3
"""Live L2 set for L3: every L2 design processed to a documented decision, by one fixed rule.

Replaces the hard-coded rosters of build_v2.py (AS_OF 2026-10-10, 29 terminal runs) with a
re-runnable, versioned list.  Standard library only.  READ-ONLY on every L2 file (registry,
autopilot, did_samples, batches): nothing under scripts/l2_pipeline or data/l2_pipeline is written,
moved or locked.  Only outputs: data/l3_meta/l2_set_live.json and, when one already exists, the
previous version copied to data/l3_meta/l2_set_live_<previous as_of, compact UTC>.json.

Fixed rule (docs/plans/l2-pipeline/L2_COUNTING_RULE.md, 2026-10-10; C63 item 1; C76B2 D4=A):
one row per design, its latest conclusion only.
  COUNTED  estimated      archive prototypes (13) and hand batches 1-3 (8) [data/l3_meta/l2_set_v2.json,
                          source files re-hashed]; registry terminal runs (TERMINAL_RUNS.json: latest valid
                          run per design, COMPLETED or STOPPED with the point estimate kept; FAILED_NO_RETRY
                          originals and superseded deviations are not counted again); autopilot estimates
                          (they are registry runs under data/l2_pipeline/autopilot).
  COUNTED  not_evaluable  a named gate: support | history | connector | ri | fresh | v3 | pre_run_stop |
                          other.  Sources: registry pre-run stops (no later valid run), the 8 early S3
                          support stops carried from l2_set_v2.json, and the latest PARKED.json of every
                          design in ACTIVE_DESIGNS.json whose latest terminal marker is that park.
  NOT COUNTED             INSUFFICIENT_WORK parks; in-flight designs (latest marker STOP.json, or no terminal
                          marker yet); PRESCREEN_PARK / GATE0_PARK; superseded attempts; synthetic fixtures;
                          cards that never reached S0; the ~69 early RUN1-4/7 cards pending verification.
The latest marker per design is chosen by file mtime among PARKED / STOP / TERMINAL_APPEND / PRESCREEN /
GATE0_CURRENT; a valid registry run always wins over an older park.

Run:  python3 -B scripts/l3_meta/build_live_set.py [--dry-run] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import mmap
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "data"
L2 = DATA / "l2_pipeline"
REG = L2 / "registry"
AUTOPILOT = L2 / "autopilot"
OUT_DIR = DATA / "l3_meta"
LIVE_NAME = "l2_set_live.json"

SCHEMA = "l3-l2-live-set-v1"
RULE_DOC = "docs/plans/l2-pipeline/L2_COUNTING_RULE.md"
RULE_TEXT = (
    "One row per design, latest conclusion only (L2_COUNTING_RULE 2026-10-10 + C63 item 1 + C76B2 D4=A). "
    "Counted: estimated (archive prototypes and hand batches 1-3; registry terminal runs, COMPLETED or STOPPED "
    "with the point estimate kept; autopilot estimates) and not_evaluable (a named gate: support, history, "
    "connector, ri, fresh, v3, pre_run_stop, other). Not counted: INSUFFICIENT_WORK parks, in-flight designs, "
    "PRESCREEN_PARK / GATE0_PARK, superseded attempts, synthetic fixtures, cards that never reached S0, "
    "the ~69 early RUN1-4/7 cards pending verification."
)

GATES = ["support", "history", "connector", "ri", "fresh", "pre_run_stop", "v3", "other"]
GATE_ZH = {"support": "支持", "history": "历史未知", "connector": "连接器窗口", "ri": "RI可行性",
           "fresh": "fresh审计池", "pre_run_stop": "运行前STOP", "v3": "V3审计", "other": "其他PARK"}
C53_TO_GATE = {"fresh audit pool": "fresh", "other": "other", "connector window": "connector", "support": "support",
               "history unknowns": "history", "RI": "ri", "V3 fail": "v3"}
MARKERS = ("PARKED.json", "STOP.json", "TERMINAL_APPEND.json", "PRESCREEN.json", "GATE0_CURRENT.json")
ARCHIVE_GROUP = {"prototype13": "prototype", "batch1": "manual_batch", "batch2": "manual_batch", "batch3": "manual_batch"}
SOURCE_RANK = {"prototype": 0, "manual_batch": 1, "registry_terminal": 2, "autopilot_estimate": 3,
               "pre_run_stop": 4, "early_s3_stop": 5, "autopilot_park": 6}
LEGACY_ALIAS = {"public_btb": "legacy_btb_public"}  # TARGETS_R2 id of the hand-batch designs: legacy_<file stem>

# Order matters: the first matching rule wins.  Reason strings are the `reason` of the latest PARKED.json.
GATE_RULES = [
    ("insufficient_work", re.compile(r"INSUFFICIENT_WORK")),
    ("ri", re.compile(r"STOPPED_PRE_RUN|RI\d+/RI\d+ feasibility|RI feasibility below")),
    ("v3", re.compile(r"V3\.1 fresh audit did not pass")),
    ("history", re.compile(r"retained treated below minimum|contract replacement exhausted|^CONTRACT_EXHAUSTED")),
    ("fresh", re.compile(r"Fresh V\d+ ten-treated audit unavailable")),
    ("connector", re.compile(r"first-order outcome unavailable|No local public files declared|"
                             r"no same-construct public series|statutory target is late abortions")),
    ("support", re.compile(r"Registered support insufficient|supported post periods below minimum|"
                           r"metadata geometry/support/RI/placebo gates failed|retained controls below minimum")),
    ("other", re.compile(r"outcome registration incomplete|Nonannual normalized support adapter pending")),
    ("c43_generic", re.compile(r"C\d+ metadata/support/fresh gates remain binding")),
]

# Policy domain follows the policy metadata, not the outcome sector (data/l3_meta/CODEBOOK_V1.md):
# cigarette tax is finance; seat-belt/road safety and political institutions stay "other".
POLICY_DOMAIN = {}
for _dom, _fams in {
    "health": ["abortion_ban_post_dobbs", "abortion_gestational_limit", "abortion_provider_regulation_trap",
               "adult_use_cannabis_legalization", "alcohol_availability", "children_medicaid_chip_enrollment_rules",
               "contraceptive_insurance_mandate", "epcs_mandate", "good_samaritan_overdose_law",
               "health_insurance_benefit_tobacco_control", "health_insurance_marketplace_reinsurance",
               "immigrant_child_coverage_option", "medical_cannabis_law", "medicaid_sud_waiver", "naloxone_access_law",
               "naloxone_coprescribing", "opioid_prescribing_limit_acute_pain", "pdmp_establishment",
               "pdmp_mandatory_use", "pdmp_registration", "pharmacist_contraception_prescribing",
               "pill_mill_pain_clinic_regulation", "prescriber_education_mandate", "reproductive_health",
               "social_host_liability_minors", "underage_alcohol_access"],
    "crime": ["civil_rights_criminal_law", "criminal_justice", "criminal_penalty_property_offenses",
              "firearm_child_access_prevention", "firearms", "forensic_dna_database_expansion"],
    "labor": ["disability_employment", "employer_credit_check_restriction", "labor_mobility_restrictions",
              "pay_transparency", "unemployment_insurance_duration", "unemployment_insurance_monetary_eligibility",
              "unemployment_insurance_nonmonetary_eligibility", "wrongful_discharge_employment_at_will_exceptions"],
    "public_finance": ["cigarette_excise_tax_large_increase", "corporate_single_sales_factor_apportionment",
                       "fiscal_institutions", "state_remote_seller_sales_tax_collection", "state_transportation_finance"],
    "education": ["college_readiness", "graduation_standards", "higher_education_finance", "higher_education_tuition"],
    "environment": ["electricity_retail_restructuring", "energy_efficiency_resource_standard", "power_sector_co2_cap",
                    "renewable_portfolio_standard"],
    "other": ["bicyclist_safety", "child_passenger_safety", "complete_streets_statute", "distracted_driving_novice",
              "graduated_driver_licensing", "immigrant_driver_licensing", "immigration_enforcement_cooperation_limits",
              "motorcycle_helmet_law", "road_safety", "snap_broad_based_categorical_eligibility",
              "snap_call_center_option", "snap_interview_waiver_option", "snap_online_application_option",
              "speed_limit_increase_65mph", "speed_limits", "voting_access", "youth_alcohol_impaired_driving"],
}.items():
    for _f in _fams:
        POLICY_DOMAIN[_f] = _dom

PREFIX_RE = re.compile(r"^(?:prototype:|b\d{4}[a-z]*|bulk\d+(?:_(?:health|lab))?|c\d+_(?:cspp(?:_w|_regulation|_guncontrol)?|siegel)"
                       r"|r\d{4}|run\d+(?:_lit)?|proto|legacy)_?")


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def utc_iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


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


def try_read_json(p):
    """(obj, None) or (None, error text): the live pipeline may be mid-write."""
    try:
        return read_json(p), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def file_facts(p, hash_limit=60 << 20):
    p = Path(p)
    st = p.stat()
    out = {"path": rel(p), "bytes": st.st_size, "mtime_utc": utc_iso(st.st_mtime)}
    if st.st_size <= hash_limit:
        out["sha256"] = sha256_file(p)
    return out


def clean(obj):
    """JSON-safe copy: NaN/inf -> None, tuples -> lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def humanize(slug):
    return re.sub(r"\s+", " ", str(slug or "").replace("_", " ")).strip()


def short(text, n=200):
    text = re.sub(r"\s+", " ", str(text)).strip() if text is not None else None
    return text if text is None or len(text) <= n else text[: n - 1].rstrip() + "…"


def slug_name(design_id):
    return humanize(PREFIX_RE.sub("", design_id, count=1) or design_id)


# ---------------------------------------------------------------------------------------------
# streaming reader: decode only the requested top-level keys of a (possibly 100 MB) JSON object
# ---------------------------------------------------------------------------------------------
_TOKEN = re.compile(rb'"(?:[^"\\]|\\.)*"|[\[\]{}]')


def _decode_scalar(mm, j, decoder):
    n = 256
    while True:
        chunk = mm[j:j + n].decode("utf-8", "ignore")
        try:
            return decoder.raw_decode(chunk)[0]
        except json.JSONDecodeError:
            if j + n >= len(mm):
                raise
            n *= 8


def load_top_level(path, wanted):
    wanted, out, decoder = set(wanted), {}, json.JSONDecoder()
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        depth, pending = 0, None
        for m in _TOKEN.finditer(mm):
            tok = m.group()
            c = tok[:1]
            if c in b"{[":
                depth += 1
            elif c in b"}]":
                depth -= 1
                if depth == 1 and pending is not None:
                    key, a = pending
                    if key in wanted:
                        out[key] = decoder.raw_decode(mm[a:m.end()].decode("utf-8"))[0]
                    pending = None
            elif depth == 1:
                tail = mm[m.end():m.end() + 16]
                stripped = tail.lstrip()
                if stripped[:1] == b":":
                    key = json.loads(tok)
                    j = m.end() + (len(tail) - len(stripped)) + 1
                    while j < len(mm) and mm[j:j + 1] in (b" ", b"\t", b"\r", b"\n"):
                        j += 1
                    if mm[j:j + 1] in (b"{", b"["):
                        pending = (key, j)
                    else:
                        pending = None
                        if key in wanted:
                            out[key] = _decode_scalar(mm, j, decoder)
    return out


_RESULT_CACHE = {}


def summarize_result(path):
    """Primary estimate, interval, RI p and noise flag of a registered RESULT.json (secondary outcomes briefly)."""
    key = str(path)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]
    top = load_top_level(path, {"design_id", "status", "noise_floor", "registered_results", "stop_issues"})
    rr = top.get("registered_results") or {}
    prim = rr.get("primary") or {}
    ov = ((prim.get("result") or {}).get("overall")) or {}
    nf = top.get("noise_floor") or prim.get("noise_floor") or {}
    feas = nf.get("feasibility") or {}
    itv = ov.get("interval") or {}
    secondary = []
    for k, x in rr.items():
        if not k.startswith("secondary"):
            continue
        xo = ((x.get("result") or {}).get("overall")) if isinstance(x.get("result"), dict) else None
        xn = x.get("noise_floor") or {}
        secondary.append({"outcome_id": k.replace("secondary_", "", 1), "status": x.get("status"),
                          "estimate": xo.get("att") if xo else None, "ri_p": xn.get("p_two_sided"),
                          "noise_cutoff": xn.get("absolute_cutoff"), "beyond_noise": xn.get("strictly_exceeds_cutoff")})
    out = {
        "design_id": top.get("design_id"), "status": top.get("status"), "stop_issues": top.get("stop_issues") or [],
        "estimate": ov.get("att"), "se": ov.get("se"), "ci95": ov.get("ci95"),
        "interval_method": itv.get("method"), "interval_reported": itv.get("reported"),
        "interval_note": itv.get("reason"), "n_cells": ov.get("n_cells"),
        "ri_p": nf.get("p_two_sided"), "beyond_noise": nf.get("strictly_exceeds_cutoff"),
        "noise_cutoff": nf.get("absolute_cutoff"), "noise_sd": nf.get("sample_sd"), "ri_draws": nf.get("draws"),
        "ri_feasible_fraction": feas.get("feasible_fraction"), "ri_support_pass": feas.get("support_pass"),
        "secondary": secondary,
    }
    _RESULT_CACHE[key] = out
    return out


# ---------------------------------------------------------------------------------------------
# gate classification
# ---------------------------------------------------------------------------------------------
def classify_reason(reason, c53_category=None):
    """Gate name for a PARKED.json reason; 'insufficient_work' = excluded; None = cannot classify."""
    text = reason or ""
    for name, pat in GATE_RULES:
        if pat.search(text):
            if name == "c43_generic":
                return C53_TO_GATE.get(c53_category)  # generic C43 text: category frozen in C53 PARK_CLASSIFICATION
            return name
    return None


def derive_domain(policy_family, v2_design=None):
    if v2_design and v2_design.get("domain"):
        return v2_design["domain"], f"l2_set_v2: {v2_design.get('domain_source')}"
    dom = POLICY_DOMAIN.get(policy_family or "")
    if dom:
        return dom, "derived: POLICY_DOMAIN[policy_family] in build_live_set.py (CODEBOOK_V1 rule)"
    return "other", "unmapped policy_family; defaulted to other"


# ---------------------------------------------------------------------------------------------
# context: static inputs
# ---------------------------------------------------------------------------------------------
def load_context():
    ctx = {"v2": read_json(OUT_DIR / "l2_set_v2.json"),
           "snapshot": read_json(REG / "SCOREBOARD_SNAPSHOT_20261011.json"),
           "targets_doc": read_json(OUT_DIR / "replication_targets" / "TARGETS_R2.json")}
    ctx["targets"] = {x["design_id"]: x for x in ctx["targets_doc"]["designs"]}
    c53 = read_json(AUTOPILOT / "c53" / "PARK_CLASSIFICATION.json")
    ctx["c53"] = {r["design_id"]: r.get("category") for r in c53["rows"]}
    tsv = {}
    p = ROOT / "docs/plans/release-20261011/ESTIMATES_SOURCES.tsv"
    if p.exists():
        with p.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                tsv[row["design_id"]] = row
    ctx["tsv"] = tsv
    zh = {}
    snap = ctx["snapshot"]
    for key in ("estimated_rows", "not_evaluable_rows", "parked_gate_unresolved_rows", "in_flight_rows"):
        for r in snap.get(key, []):
            if r.get("label_zh"):
                zh[r["design_id"]] = r["label_zh"]
    ctx["label_zh"] = zh
    ctx["v2_designs"] = {d["design_id"]: d for d in ctx["v2"]["designs"]}
    rows = defaultdict(list)
    for r in ctx["v2"]["rows"]:
        rows[r["design_id"]].append(r)
    ctx["v2_rows"] = rows
    return ctx


def names_for(ctx, design_id, target_id=None, case_key=None):
    row = ctx["tsv"].get(design_id)
    entry = ctx["targets"].get(target_id or design_id)
    if row and row.get("policy_en_draft"):
        en = row["policy_en_draft"]
    elif entry and entry.get("policy_group"):
        en = humanize(entry["policy_group"])
    else:
        en = humanize(case_key) if case_key else slug_name(design_id)
    zh = ctx["label_zh"].get(design_id) or (row or {}).get("policy_zh")
    return en, zh


# ---------------------------------------------------------------------------------------------
# benchmark (TARGETS_R2)
# ---------------------------------------------------------------------------------------------
def benchmark_block(entry, via):
    cands = entry.get("candidates") or []
    rank = entry.get("chosen_candidate_rank")
    cand, picked = None, None
    if rank is not None:
        cand = next((c for c in cands if c.get("rank") == rank), None)
        picked = "chosen" if cand else None
    if cand is None:
        cand = next((c for c in cands if (c.get("estimate") or {}).get("point") is not None), None)
        picked = "first_with_published_point" if cand else None
    est = (cand or {}).get("estimate") or {}
    block = {"target_id": entry["design_id"], "matched_via": via, "q2_class": entry.get("q2_class"),
             "extractor_class": entry.get("extractor_class"), "search_status": entry.get("search_status"),
             "chosen_candidate_rank": rank, "candidate_picked": picked, "n_candidates": len(cands),
             "class_reason": entry.get("class_reason"), "paper": None, "published_estimate": None}
    if cand:
        block["paper"] = {"citation": cand.get("citation"), "year": cand.get("year"), "venue": cand.get("venue"),
                          "doi": cand.get("doi"), "peer_reviewed": cand.get("peer_reviewed"),
                          "version": cand.get("version"), "strict_core": cand.get("strict_core")}
        block["published_estimate"] = {"point": est.get("point"), "se": est.get("se"), "ci": est.get("ci"),
                                       "units": est.get("units"), "locator": est.get("locator"),
                                       "direction": cand.get("direction_a_i")}
        block["match"] = cand.get("match")
    return block


def attach_benchmarks(ctx, designs):
    counts = Counter()
    used = set()
    for d in designs:
        base = d.pop("_target_id", None) or d["design_id"]
        entry = ctx["targets"].get(base)
        d["benchmark"] = None
        if entry:
            d["benchmark"] = benchmark_block(entry, "direct" if base == d["design_id"] else "alias")
            used.add(base)
            counts[entry["q2_class"]] += 1
        fo = ctx["targets"].get(base + "_fo")
        d["benchmark_first_order_variant"] = None
        if fo:
            d["benchmark_first_order_variant"] = benchmark_block(fo, "first_order_variant_card")
            used.add(base + "_fo")
            counts["fo_variant:" + fo["q2_class"]] += 1
    unmatched = Counter(e["lifecycle"] for tid, e in ctx["targets"].items() if tid not in used)
    legacy_unmatched = sorted(tid for tid in ctx["targets"] if tid.startswith(("legacy_", "proto_")) and tid not in used
                              and not tid.endswith("_fo"))
    return counts, {"by_lifecycle": dict(unmatched), "archive_entries_without_live_design": legacy_unmatched}


# ---------------------------------------------------------------------------------------------
# autopilot marker scan
# ---------------------------------------------------------------------------------------------
def scan_markers(design_ids):
    out = {}
    for did in design_ids:
        ddir = AUTOPILOT / did
        found = []
        if ddir.is_dir():
            for att in os.scandir(ddir):
                if not att.is_dir():
                    continue
                for name in MARKERS:
                    fp = os.path.join(att.path, name)
                    try:
                        st = os.stat(fp)
                    except FileNotFoundError:
                        continue
                    found.append((st.st_mtime_ns, att.name, name, fp))
        out[did] = sorted(found)
    return out


def fingerprint(markers):
    items = []
    for p in (REG / "TERMINAL_RUNS.json", REG / "ACTIVE_DESIGNS.json"):
        st = p.stat()
        items.append((p.name, st.st_mtime_ns, st.st_size))
    for did, found in sorted(markers.items()):
        items.append((did, tuple((m, a, n) for m, a, n, _ in found)))
    return hashlib.sha256(repr(items).encode()).hexdigest()


def read_card(path):
    try:
        return read_json(path) if path else None
    except (OSError, ValueError):
        return None


def outcome_stub(card, ctx_row=None):
    o = (card or {}).get("outcome") or {}
    pop = (card or {}).get("population") or {}
    drafted = (ctx_row or {}).get("outcome_en_draft")
    label = (drafted if drafted not in (None, "", "-") else None) or humanize(o.get("measure_id")) or None
    return {"role": "registered_primary", "outcome_id": o.get("measure_id") or o.get("series_id"), "label": label,
            "unit": o.get("outcome_unit"), "population": (pop.get("description") or "")[:240] or None,
            "estimate": None, "se": None, "ci95": None, "ri_p": None, "beyond_noise": None}


# ---------------------------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------------------------
def archive_designs(ctx):
    out = []
    for d in ctx["v2"]["designs"]:
        grp = ARCHIVE_GROUP.get(d.get("source_group"))
        if not grp:
            continue
        did = d["design_id"]
        rows = ctx["v2_rows"].get(did) or []
        if not rows:
            continue
        flagged = [r for r in rows if r.get("designated_l3_primary")]
        outcomes = []
        for r in rows:
            role = ("primary" if r in flagged else "secondary") if flagged else "joint_primary"
            nz = r.get("noise") or {}
            label = r.get("outcome_label")
            outcomes.append({"role": role, "outcome_id": r.get("outcome_id"),
                             "label": short(label.get("en") if isinstance(label, dict) else label),
                             "unit": r.get("outcome_unit"), "estimate": r.get("b"), "se": r.get("se"),
                             "se_method": r.get("se_method"), "ci95": r.get("ci95"),
                             "interval_method": r.get("interval_method"), "interval_reported": bool(r.get("ci95")),
                             "ri_p": nz.get("p_two_sided"), "beyond_noise": r.get("noise_exceedance"),
                             "noise_cutoff": nz.get("absolute_cutoff"), "noise_sd": nz.get("sample_sd")})
        outcomes.sort(key=lambda o: {"primary": 0, "joint_primary": 0, "secondary": 1}[o["role"]])
        src = rows[0]["source"]
        sp = ROOT / src["path"]
        sha_ok = sp.exists() and sha256_file(sp) == src["sha256"]
        try:
            ts = load_top_level(sp, {"timestamp_utc"}).get("timestamp_utc") if sp.exists() else None
        except (OSError, ValueError):
            ts = None
        run_date = ts[:10] if ts else (utc_iso(sp.stat().st_mtime)[:10] if sp.exists() else None)
        case_key = d.get("case_key")
        target = ("proto_" + case_key) if grp == "prototype" else LEGACY_ALIAS.get(Path(src["path"]).stem, "legacy_" + Path(src["path"]).stem)
        en, zh = names_for(ctx, did, target, case_key)
        fam = d.get("policy_family") or d.get("policy_family_id") or case_key
        out.append({"design_id": did, "policy_name": en, "policy_name_zh": zh, "policy_family": fam,
                    "outcome_family": None, "domain": d["domain"], "domain_source": f"l2_set_v2: {d.get('domain_source')}",
                    "topic_cluster": (ctx["targets"].get(target) or {}).get("cluster"),
                    "source_group": grp, "origin": "archive hand-picked (carried from l2_set_v1/v2)",
                    "status": "estimated", "status_detail": d["status"], "reportable": bool(d.get("current_project_reportable")),
                    "gate": None, "gate_reason": None, "stage": None,
                    "stop_reasons": (d.get("stop_reasons") or [])[:5] or None,
                    "primary_outcomes": outcomes, "secondary_outcomes": [],
                    "run_date": run_date, "run_date_basis": "timestamp_utc in source file" if ts else "source file mtime",
                    "decided_at": ts[:19] + "Z" if ts else None,
                    "source": {"path": src["path"], "sha256": src["sha256"], "pointer": src.get("pointer"),
                               "sha256_match": sha_ok, "run_id": None},
                    "_target_id": target})
    return out


def read_f1(export_path):
    """F1 (purpose_direction) from the hash-bound L2 export, never from a work-dir file (HUB_RULING_20261017_F1_DIRECTION Q5).

    Returns (purpose_direction, purpose_direction_coded_at, coding_epoch); each None when absent.  Looks in
    l3_export_v2.first_order, then a top-level first_order / registration.first_order.  coded_at: the explicit field
    `purpose_direction_coded_at` (or `recorded_at`) when the export carries one, else the latest `recorded_at` of the
    evidence codings.  coding_epoch: the export's own value if present; the caller derives it when it is None.
    """
    if not export_path or not Path(export_path).exists():
        return None, None, None
    try:
        data = read_json(export_path)
    except (OSError, ValueError):
        return None, None, None
    fo = None
    for holder in ((data.get("l3_export_v2") or {}), data, (data.get("registration") or {})):
        cand = holder.get("first_order") if isinstance(holder, dict) else None
        if isinstance(cand, dict) and cand.get("purpose_direction") is not None:
            fo = cand
            break
    if fo is None:
        return None, None, None
    sign = fo.get("purpose_direction")
    if sign not in (-1, 0, 1):
        return None, None, None
    coded = fo.get("purpose_direction_coded_at") or fo.get("recorded_at")
    if not coded:
        stamps = [c.get("recorded_at") for c in ((fo.get("purpose_direction_evidence") or {}).get("codings") or [])
                  if isinstance(c, dict) and c.get("recorded_at")]
        coded = max(stamps) if stamps else None
    return sign, coded, fo.get("coding_epoch")


def derive_epoch(coded_at, decided_at, given):
    """retrospective if coded after the estimate was decided, prospective if before; None when either time is missing."""
    if given in ("retrospective", "prospective"):
        return given
    if not coded_at or not decided_at:
        return None
    return "retrospective" if str(coded_at)[:19] > str(decided_at)[:19] else "prospective"


def registry_parts(ctx, reg):
    """First certified run per design (T18); returns (estimated designs, pre-run-only stops, unclassified, superseded)."""
    by_design = OrderedDict()
    for r in reg["runs"]:
        by_design.setdefault(r["design_id"], []).append(r)
    est, unclassified, superseded = [], [], []
    for did, runs in by_design.items():
        valid = [r for r in runs if r["status"] in ("COMPLETED", "STOPPED")]
        if not valid:
            unclassified.append({"design_id": did, "why": "registry runs exist but none is COMPLETED/STOPPED "
                                 f"({', '.join(sorted({r['status'] for r in runs}))})"})
            continue
        # T18 (2026-10-17): the FIRST CERTIFIED run (EX-1 section 1: COMPLETED/STOPPED, study_estimated not False, finite point estimate)
        # is the live row; later certified runs (e.g. a timing-correction deviation) stay listed as superseded, never read.
        run, facts, err = None, None, None
        for cand in valid:
            if cand.get("study_estimated") is False:
                continue
            try:
                f = summarize_result(Path(cand["result_path"]))
            except (OSError, ValueError) as exc:
                err = f"cannot read {rel(Path(cand['result_path']))}: {type(exc).__name__}: {exc}"
                break
            if f["estimate"] is not None:
                run, facts = cand, f
                break
        if run is None:
            unclassified.append({"design_id": did, "why": err or "no valid run keeps a point estimate (no certified run)"})
            continue
        for x in runs:
            if x is not run:
                superseded.append({"design_id": did, "run_id": x["run_id"], "status": x["status"], "replaced_by": run["run_id"]})
        rp = Path(run["result_path"])
        card, card_path = {}, None
        for x in reversed(runs):
            cp = Path(x["package_path"]) / "CARD.json"
            if cp.exists():
                card, card_path = read_json(cp), cp
                break
        v2d = ctx["v2_designs"].get(did)
        fam = card.get("policy_family") or (v2d or {}).get("policy_family")
        dom, dom_src = derive_domain(fam, v2d)
        tsv = ctx["tsv"].get(did)
        stub = outcome_stub(card, tsv)
        autopilot = "/autopilot/" in str(run["package_path"])
        sha_ok = sha256_file(rp) == run["result_sha256"]
        en, zh = names_for(ctx, did)
        l3x = Path(run["package_path"]) / "L3_EXPORT_DATA.json"
        f1_sign, f1_at, f1_epoch = read_f1(l3x)
        f1_decided = utc_iso(rp.stat().st_mtime)
        po = dict(stub, role="primary", estimate=facts["estimate"], se=facts["se"], ci95=facts["ci95"],
                  interval_method=facts["interval_method"], interval_reported=bool(facts["interval_reported"]),
                  interval_note=facts["interval_note"], ri_p=facts["ri_p"], beyond_noise=facts["beyond_noise"],
                  noise_cutoff=facts["noise_cutoff"], noise_sd=facts["noise_sd"], ri_draws=facts["ri_draws"],
                  ri_feasible_fraction=facts["ri_feasible_fraction"], n_cells=facts["n_cells"])
        est.append({"design_id": did, "policy_name": en, "policy_name_zh": zh, "policy_family": fam,
                    "outcome_family": card.get("outcome_family"), "domain": dom, "domain_source": dom_src,
                    "topic_cluster": (ctx["targets"].get(did) or {}).get("cluster"),
                    "source_group": "autopilot_estimate" if autopilot else "registry_terminal",
                    "origin": "terminal L2 pipeline run (first certified)", "status": "estimated",
                    "status_detail": run["status"], "reportable": run["status"] == "COMPLETED",
                    "gate": None, "gate_reason": None, "stage": None,
                    "stop_reasons": facts["stop_issues"] or None,
                    "primary_outcomes": [po], "secondary_outcomes": facts["secondary"],
                    "run_date": utc_iso(rp.stat().st_mtime)[:10], "run_date_basis": "RESULT.json mtime (UTC)",
                    "decided_at": utc_iso(rp.stat().st_mtime),
                    "purpose_direction": f1_sign, "purpose_direction_coded_at": f1_at,
                    "coding_epoch": derive_epoch(f1_at, f1_decided, f1_epoch) if f1_sign is not None else None,
                    "queue_version": (card.get("lineage") or {}).get("queue_version"),
                    "source": {"path": rel(rp), "sha256": run["result_sha256"], "sha256_match": sha_ok,
                               "result_design_id_match": facts["design_id"] == did,
                               "result_status_match": facts["status"] == run["status"],
                               "pointer": "/registered_results/primary/result/overall", "run_id": run["run_id"],
                               "spec_version": run.get("spec_version"), "is_deviation": run.get("is_deviation"),
                               "attempts_in_registry": len(runs),
                               "card_path": rel(card_path) if card_path else None,
                               "l3_export_path": rel(l3x) if l3x.exists() else None,
                               "l3_export_bytes": l3x.stat().st_size if l3x.exists() else None}})
    prerun = {}
    for p in reg.get("pre_run_stops", []):
        if p["design_id"] in {e["design_id"] for e in est}:
            superseded.append({"design_id": p["design_id"], "run_id": p["run_id"], "status": "STOPPED (pre-run)",
                               "replaced_by": "later valid registry run"})
        else:
            prerun[p["design_id"]] = p
    return est, prerun, unclassified, superseded


def prerun_designs(ctx, prerun):
    out = []
    for did, p in prerun.items():
        v2d = ctx["v2_designs"].get(did)
        rows = ctx["v2_rows"].get(did) or []
        card = {}
        spec_dir = Path(p["spec_path"]).parent if p.get("spec_path") else None
        if spec_dir and (spec_dir / "CARD.json").exists():
            card = read_json(spec_dir / "CARD.json")
        fam = card.get("policy_family") or (v2d or {}).get("policy_family")
        dom, dom_src = derive_domain(fam, v2d)
        tsv = ctx["tsv"].get(did)
        stub = outcome_stub(card, tsv)
        if rows:
            lab = rows[0].get("outcome_label")
            stub["outcome_id"] = stub["outcome_id"] or rows[0].get("outcome_id")
            stub["label"] = (tsv or {}).get("outcome_en_draft") if (tsv or {}).get("outcome_en_draft") not in (None, "-") else short(
                lab.get("en") if isinstance(lab, dict) else lab)
            stub["unit"] = stub["unit"] or rows[0].get("outcome_unit")
        tp = Path(p["terminal_record_path"])
        en, zh = names_for(ctx, did)
        out.append({"design_id": did, "policy_name": en, "policy_name_zh": zh, "policy_family": fam,
                    "outcome_family": card.get("outcome_family"), "domain": dom, "domain_source": dom_src,
                    "topic_cluster": (ctx["targets"].get(did) or {}).get("cluster"),
                    "source_group": "pre_run_stop", "origin": "registry pre-run terminal stop (never executed)",
                    "status": "not_evaluable", "status_detail": "PRE_RUN_STOP", "reportable": False,
                    "gate": "pre_run_stop", "gate_reason": f"{p['reason']} (feasible {p['feasible_draws']}/{p['requested_draws']} draws; "
                                                          f"rule {p['stop_rule']})",
                    "stage": "registry", "stop_reasons": [p["reason"]], "primary_outcomes": [stub], "secondary_outcomes": [],
                    "run_date": None, "run_date_basis": None,
                    "decided_at": utc_iso(tp.stat().st_mtime) if tp.exists() else None,
                    "source": {"path": rel(tp), "sha256": p.get("terminal_record_sha256"), "run_id": p["run_id"],
                               "sha256_match": (sha256_file(tp) == p.get("terminal_record_sha256")) if tp.exists() else False}})
    return out


def early_s3_designs(ctx, skip):
    out = []
    for d in ctx["v2"]["designs"]:
        if d.get("status") != "SUPPORT_STOPPED" or d["design_id"] in skip:
            continue
        did = d["design_id"]
        r = ctx["v2_rows"][did][0]
        lab = r.get("outcome_label")
        en, zh = names_for(ctx, did)
        reasons = d.get("stop_reasons")
        out.append({"design_id": did, "policy_name": en, "policy_name_zh": zh,
                    "policy_family": d.get("policy_family") or d.get("policy_family_id"), "outcome_family": None,
                    "domain": d["domain"], "domain_source": f"l2_set_v2: {d.get('domain_source')}",
                    "topic_cluster": (ctx["targets"].get(did) or {}).get("cluster"),
                    "source_group": "early_s3_stop", "origin": "early S3 support stop (carried from l2_set_v1/v2; not in registry)",
                    "status": "not_evaluable", "status_detail": "SUPPORT_STOPPED", "reportable": False,
                    "gate": "support", "gate_reason": reasons if isinstance(reasons, str) else "; ".join(reasons or []) or "Registered support insufficient",
                    "stage": "S3", "stop_reasons": None,
                    "primary_outcomes": [{"role": "registered_primary", "outcome_id": r.get("outcome_id"),
                                          "label": short(lab.get("en") if isinstance(lab, dict) else lab), "unit": r.get("outcome_unit"),
                                          "estimate": None, "se": None, "ci95": None, "ri_p": None, "beyond_noise": None}],
                    "secondary_outcomes": [], "run_date": None, "run_date_basis": None,
                    "decided_at": None, "decided_at_note": "not recorded per design (carried roster)",
                    "source": {"path": r["source"].get("path"), "sha256": r["source"].get("sha256"), "run_id": None,
                               "sha256_match": (ROOT / r["source"]["path"]).exists()
                               and sha256_file(ROOT / r["source"]["path"]) == r["source"].get("sha256")}})
    return out


def autopilot_parts(ctx, active, markers, registry_time_ns):
    """Classify every design in ACTIVE_DESIGNS.json that has no valid registry run.

    Latest terminal marker (by mtime) decides: PARKED -> named gate / INSUFFICIENT_WORK; STOP or no marker -> in flight;
    PRESCREEN / GATE0 receipt -> PRESCREEN_PARK / GATE0_PARK (kept apart, C63 item 1, C76B2 D4=A).
    """
    designs, excluded, unclassified, conflicts = [], defaultdict(list), [], []
    card_of = {a["design_id"]: a.get("card") for a in active}
    unresolved_base = {r["design_id"] for r in ctx["snapshot"].get("parked_gate_unresolved_rows", [])}
    for a in active:
        did = a["design_id"]
        found = markers.get(did) or []
        if did in registry_time_ns:  # a valid registry run wins over any park; flag a park/stop written after it
            if found and found[-1][2] in ("PARKED.json", "STOP.json") and found[-1][0] > registry_time_ns[did]:
                conflicts.append({"design_id": did, "marker": found[-1][2], "attempt": found[-1][1],
                                  "marker_time_utc": utc_iso(found[-1][0] / 1e9),
                                  "why": "park/stop written after the registry run; the estimate is kept"})
            continue
        if not found:
            excluded["in_flight"].append({"design_id": did, "why": "no terminal marker yet"})
            continue
        mtime_ns, attempt, name, path = found[-1]
        info = {"design_id": did, "attempt": attempt, "marker": name, "marker_time_utc": utc_iso(mtime_ns / 1e9)}
        if name in ("PRESCREEN.json", "GATE0_CURRENT.json"):
            kinds = []
            ps = [f for f in found if f[2] == "PRESCREEN.json"]
            g0 = [f for f in found if f[2] == "GATE0_CURRENT.json"]
            if ps:
                rec = try_read_json(ps[-1][3])[0] or {}
                if rec.get("status") == "PRESCREEN_PARK" and rec.get("processed") is False:
                    kinds.append(("prescreen_park", "PRESCREEN_PARK: " + ",".join(rec.get("reasons") or []), rel(ps[-1][3])))
            rec = {}
            if g0:
                cur = try_read_json(g0[-1][3])[0] or {}
                rec = try_read_json(cur["receipt"])[0] if cur.get("receipt") else None
                rec = rec or {}
                if rec.get("status") == "GATE0_PARK" and rec.get("pre_stopped") and not (rec.get("audit") or {}).get("selected"):
                    kinds.append(("gate0_park", "GATE0_PARK: " + ",".join(rec.get("reasons") or []), rel(cur["receipt"])))
            for kind, why, receipt in kinds:
                excluded[kind].append(dict(info, why=why, receipt=receipt))
            if not kinds:
                excluded["in_flight"].append(dict(info, why=f"gate 0 {rec.get('status', 'receipt unreadable')}; chain running"))
            continue
        if name == "TERMINAL_APPEND.json":
            unclassified.append({"design_id": did, "why": "TERMINAL_APPEND.json present but no valid run in TERMINAL_RUNS.json",
                                 "path": rel(path)})
            continue
        obj, err = try_read_json(path)
        if obj is None:
            unclassified.append({"design_id": did, "why": f"{name} unreadable ({err}); rerun when the pipeline is idle",
                                 "path": rel(path)})
            continue
        if name == "STOP.json":
            excluded["in_flight"].append(dict(info, why=f"{obj.get('status')}: {str(obj.get('reason'))[:160]}"))
            continue
        parked = obj  # PARKED.json
        reason = parked.get("reason")
        gate = classify_reason(reason, ctx["c53"].get(did))
        if gate == "insufficient_work":
            excluded["insufficient_work"].append(dict(info, why=reason, in_baseline_unresolved=did in unresolved_base))
            continue
        if gate is None:
            unclassified.append({"design_id": did, "why": f"cannot map PARKED reason to a gate: {str(reason)[:160]}",
                                 "path": rel(path)})
            continue
        card = read_card(card_of.get(did))
        fam = (card or {}).get("policy_family")
        dom, dom_src = derive_domain(fam, ctx["v2_designs"].get(did))
        en, zh = names_for(ctx, did)
        designs.append({"design_id": did, "policy_name": en, "policy_name_zh": zh, "policy_family": fam,
                        "outcome_family": (card or {}).get("outcome_family"), "domain": dom, "domain_source": dom_src,
                        "topic_cluster": (ctx["targets"].get(did) or {}).get("cluster"),
                        "source_group": "autopilot_park", "origin": "autopilot PARKED.json (latest terminal marker)",
                        "status": "not_evaluable", "status_detail": "PARKED", "reportable": False,
                        "gate": gate, "gate_reason": reason, "stage": parked.get("action"), "attempt": attempt,
                        "stop_reasons": None, "primary_outcomes": [outcome_stub(card, ctx["tsv"].get(did))],
                        "secondary_outcomes": [], "run_date": None, "run_date_basis": None,
                        "decided_at": utc_iso(mtime_ns / 1e9),
                        "queue_version": ((card or {}).get("lineage") or {}).get("queue_version"),
                        "source": {"path": rel(path), "sha256": sha256_file(path), "run_id": None,
                                   "card_path": rel(card_of[did]) if card else None}})
    return designs, excluded, unclassified, conflicts


def sort_key(d):
    return (SOURCE_RANK[d["source_group"]], d["design_id"])


def assemble(ctx, markers):
    reg = read_json(REG / "TERMINAL_RUNS.json")
    active = read_json(REG / "ACTIVE_DESIGNS.json")["designs"]
    est, prerun_only, unclassified, superseded = registry_parts(ctx, reg)
    registry_time_ns = {d["design_id"]: int(Path(ROOT / d["source"]["path"]).stat().st_mtime_ns) for d in est}
    arch = archive_designs(ctx)
    ap, excluded, unc2, conflicts = autopilot_parts(ctx, active, markers, registry_time_ns)
    unclassified += unc2
    in_active = {a["design_id"] for a in active}
    pre = prerun_designs(ctx, {k: v for k, v in prerun_only.items() if k not in in_active})
    pre_ids = {d["design_id"] for d in pre}
    # an active design that also has a registry pre-run stop but no valid run is judged by its autopilot marker
    taken = {d["design_id"] for d in est} | {d["design_id"] for d in arch} | {d["design_id"] for d in ap} | pre_ids | in_active
    early = early_s3_designs(ctx, skip=taken)
    for d in ctx["v2"]["designs"]:  # carried S3 stop superseded by a newer record elsewhere
        if d.get("status") == "SUPPORT_STOPPED" and d["design_id"] in taken:
            superseded.append({"design_id": d["design_id"], "run_id": None, "status": "early S3 support stop (carried)",
                               "replaced_by": "newer autopilot/registry record"})
    designs, seen = [], set()
    for d in est + arch + pre + early + ap:
        if d["design_id"] in seen:
            unclassified.append({"design_id": d["design_id"], "why": "duplicate design id across sources"})
            continue
        seen.add(d["design_id"])
        designs.append(d)
    designs.sort(key=sort_key)
    return {"designs": designs, "excluded": excluded, "unclassified": unclassified, "superseded": superseded,
            "conflicts": conflicts, "registry": reg, "active": active}


# ---------------------------------------------------------------------------------------------
# counts / reconciliation / checks
# ---------------------------------------------------------------------------------------------
def headline(d):
    """Flat copy of the first primary outcome (joint-primary designs: the first listed)."""
    po = d["primary_outcomes"][0] if d["primary_outcomes"] else {}
    est = d["status"] == "estimated"
    for k in ("purpose_direction", "purpose_direction_coded_at", "coding_epoch"):  # F1-D: null unless the export carries it
        d.setdefault(k, None)
    d["point_estimate"] = po.get("estimate") if est else None
    d["se"] = po.get("se") if est else None
    d["ci95"] = po.get("ci95") if est else None
    d["ri_p"] = po.get("ri_p") if est else None
    d["beyond_noise"] = po.get("beyond_noise") if est else None
    d["outcome_unit"] = po.get("unit")
    d["headline_outcome_id"] = po.get("outcome_id")


def make_counts(designs, excluded, unclassified):
    est = [d for d in designs if d["status"] == "estimated"]
    ne = [d for d in designs if d["status"] == "not_evaluable"]
    by_gate = {g: 0 for g in GATES}
    by_gate.update(Counter(d["gate"] for d in ne))
    pipe = [d for d in designs if d["source_group"] not in ("prototype", "manual_batch")]
    return {
        "processed": len(designs), "estimated": len(est), "not_evaluable": len(ne), "by_gate": by_gate,
        "estimated_reportable": sum(1 for d in est if d["reportable"]),
        "estimated_stopped_after_run_estimate_kept": sum(1 for d in est if not d["reportable"]),
        "pipeline_only": {"processed": len(pipe), "estimated": sum(1 for d in pipe if d["status"] == "estimated"),
                          "not_evaluable": sum(1 for d in pipe if d["status"] == "not_evaluable")},
        "by_source": dict(sorted(Counter(d["source_group"] for d in designs).items(), key=lambda kv: SOURCE_RANK[kv[0]])),
        "by_domain": dict(sorted(Counter(d["domain"] for d in designs).items())),
        "excluded_not_counted": {k: len(v) for k, v in sorted(excluded.items())},
        "unclassified": len(unclassified),
    }


def reconcile(ctx, designs, excluded):
    snap = ctx["snapshot"]
    base_est = {r["design_id"]: r for r in snap["estimated_rows"]}
    base_ne = {r["design_id"]: r for r in snap["not_evaluable_rows"]}
    live = {d["design_id"]: d for d in designs}
    live_est = {k for k, d in live.items() if d["status"] == "estimated"}
    live_ne = {k for k, d in live.items() if d["status"] == "not_evaluable"}
    added_est = sorted(live_est - set(base_est) - set(base_ne))
    added_ne = sorted(live_ne - set(base_est) - set(base_ne))
    removed = sorted((set(base_est) | set(base_ne)) - set(live))
    moved = sorted(k for k in set(base_est) & live_ne) + sorted(k for k in set(base_ne) & live_est)
    gate_changed = [{"design_id": k, "baseline": base_ne[k]["gate_en"], "live": live[k]["gate"]}
                    for k in sorted(set(base_ne) & live_ne) if base_ne[k]["gate_en"] != live[k]["gate"]]
    value_mismatch, run_mismatch = [], []
    for k, r in base_est.items():
        d = live.get(k)
        if not d or d["status"] != "estimated":
            continue
        pe, be = d.get("point_estimate"), d.get("beyond_noise")
        if pe is None or abs(pe - r["point_estimate"]) > 1e-12 * max(1.0, abs(r["point_estimate"])) or be != r.get("beyond_noise"):
            value_mismatch.append({"design_id": k, "baseline": [r["point_estimate"], r.get("beyond_noise")], "live": [pe, be]})
        if r.get("registry_run_id") and d["source"].get("run_id") != r["registry_run_id"]:
            run_mismatch.append({"design_id": k, "baseline_run": r["registry_run_id"], "live_run": d["source"].get("run_id")})
    in_flight_base = [r["design_id"] for r in snap.get("in_flight_rows", [])]
    base_n = snap["counts"]["overall"]
    explained = (len(live) == base_n["processed"] + len(added_est) + len(added_ne) - len(removed)
                 and not moved and not removed)
    unresolved_base = [r["design_id"] for r in snap.get("parked_gate_unresolved_rows", [])]
    iw = {x["design_id"] for x in excluded.get("insufficient_work", [])}
    return {
        "baseline": {"file": rel(REG / "SCOREBOARD_SNAPSHOT_20261011.json"), "snapshot_utc": snap["snapshot_utc"],
                     "processed": base_n["processed"], "estimated": base_n["estimated"], "not_evaluable": base_n["not_evaluable"],
                     "by_gate": snap["counts"]["not_evaluable_by_gate"]},
        "live": {"processed": len(live), "estimated": len(live_est), "not_evaluable": len(live_ne)},
        "by_gate_delta": {g: sum(1 for k in live_ne if live[k]["gate"] == g) - int(snap["counts"]["not_evaluable_by_gate"].get(g, 0))
                          for g in GATES},
        "added_estimated": added_est, "added_not_evaluable": added_ne,
        "added_not_evaluable_by_gate": dict(Counter(live[k]["gate"] for k in added_ne)),
        "removed_since_baseline": removed, "status_moved_since_baseline": moved, "gate_changed": gate_changed,
        "baseline_in_flight_now_estimated": sorted(set(in_flight_base) & live_est),
        "baseline_in_flight_still_not_counted": sorted(set(in_flight_base) - live_est - live_ne),
        "baseline_unresolved_insufficient_work_still_excluded": sorted(set(unresolved_base) & iw),
        "baseline_unresolved_insufficient_work_now_counted": sorted(set(unresolved_base) & (live_est | live_ne)),
        "estimate_mismatch_vs_baseline": value_mismatch, "run_id_mismatch_vs_baseline": run_mismatch,
        "arithmetic": f"{base_n['processed']} + {len(added_est)} estimated + {len(added_ne)} not evaluable - {len(removed)} removed = "
                      f"{base_n['processed'] + len(added_est) + len(added_ne) - len(removed)} (live {len(live)})",
        "explained": bool(explained and not value_mismatch and not run_mismatch and not gate_changed),
    }


def run_checks(parts, designs, markers):
    reg, active = parts["registry"], parts["active"]
    reg_designs = {r["design_id"] for r in reg["runs"]}
    checks = {
        "registry_design_count_field": reg.get("design_count"), "registry_designs_seen": len(reg_designs),
        "registry_run_count_field": reg.get("run_count"), "registry_runs_seen": len(reg["runs"]),
        "active_designs": len(active), "autopilot_dirs_with_markers": sum(1 for v in markers.values() if v),
        "source_hash_mismatches": [d["design_id"] for d in designs if d["source"].get("sha256_match") is False],
        "result_header_mismatches": [d["design_id"] for d in designs if d["source"].get("result_design_id_match") is False
                                     or d["source"].get("result_status_match") is False],
        "unmapped_policy_families": sorted({d["policy_family"] for d in designs
                                            if d["domain_source"].startswith("unmapped") and d["policy_family"]}),
    }
    checks["registry_counters_consistent"] = (checks["registry_design_count_field"] == checks["registry_designs_seen"]
                                              and checks["registry_run_count_field"] == checks["registry_runs_seen"])
    try:
        ls = read_json(REG / "LOOP_STATE.json")
        checks["loop_state"] = {"time": ls.get("time"), "next_counts": ls.get("next_counts")}
    except (OSError, ValueError):
        checks["loop_state"] = None
    return checks


# ---------------------------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------------------------
def compact_stamp(as_of):
    return re.sub(r"[-:]", "", as_of)


def write_versioned(payload, out_dir, as_of):
    out_dir.mkdir(parents=True, exist_ok=True)
    live = out_dir / LIVE_NAME
    archived = None
    fd, tmp = tempfile.mkstemp(prefix=".l2_set_live_", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, allow_nan=False)
            fh.write("\n")
        if live.exists():
            try:
                prev_as_of = read_json(live).get("as_of") or utc_iso(live.stat().st_mtime)
            except ValueError:
                prev_as_of = utc_iso(live.stat().st_mtime)
            archived = out_dir / f"l2_set_live_{compact_stamp(prev_as_of)}.json"
            n = 1
            while archived.exists():
                archived = out_dir / f"l2_set_live_{compact_stamp(prev_as_of)}_{n}.json"
                n += 1
            shutil.copy2(live, archived)
        os.chmod(tmp, 0o644)
        os.replace(tmp, live)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return live, archived


def print_scoreboard(payload):
    c, r = payload["counts"], payload["reconciliation_vs_20261011"]
    print(f"L2 live set as of {payload['as_of']}   (rule: {RULE_DOC})")
    print(f"  processed        {c['processed']:>4}   (pipeline only {c['pipeline_only']['processed']} = "
          f"{c['pipeline_only']['estimated']} + {c['pipeline_only']['not_evaluable']})")
    print(f"    estimated      {c['estimated']:>4}   (reportable {c['estimated_reportable']}, "
          f"stopped-after-run estimate kept {c['estimated_stopped_after_run_estimate_kept']})")
    print(f"    not evaluable  {c['not_evaluable']:>4}")
    for g in GATES:
        print(f"      {g:<13}{c['by_gate'][g]:>4}   {GATE_ZH[g]}")
    print("  by source        " + ", ".join(f"{k} {v}" for k, v in c["by_source"].items()))
    print("  not counted      " + (", ".join(f"{k} {v}" for k, v in c["excluded_not_counted"].items()) or "none"))
    b, lv = r["baseline"], r["live"]
    print(f"  baseline {b['snapshot_utc'][:10]}: {b['processed']} = {b['estimated']} + {b['not_evaluable']}"
          f"  ->  live {lv['processed']} = {lv['estimated']} + {lv['not_evaluable']}")
    print(f"    {r['arithmetic']}")
    print(f"    + estimated ({len(r['added_estimated'])}): {', '.join(r['added_estimated']) or '-'}")
    print(f"    + not evaluable ({len(r['added_not_evaluable'])}): "
          + (', '.join(f"{k}[{payload_gate(payload, k)}]" for k in r['added_not_evaluable']) or "-"))
    print(f"    removed {len(r['removed_since_baseline'])}, status moved {len(r['status_moved_since_baseline'])}, "
          f"gate changed {len(r['gate_changed'])}, estimate/run mismatches "
          f"{len(r['estimate_mismatch_vs_baseline'])}/{len(r['run_id_mismatch_vs_baseline'])}; explained = {r['explained']}")
    u = payload["unclassified"]
    print(f"  unclassified     {len(u)}" + ("" if not u else ": " + "; ".join(f"{x['design_id']} ({x['why'][:80]})" for x in u)))
    bm = payload["benchmark_summary"]
    print(f"  TARGETS_R2 entry {bm['designs_with_entry']} designs ({', '.join(f'{k} {v}' for k, v in bm['by_class'].items())});"
          f" first-order-variant entries {bm['designs_with_first_order_variant_entry']}")


def payload_gate(payload, design_id):
    for d in payload["designs"]:
        if d["design_id"] == design_id:
            return d["gate"]
    return "?"


def build(out_dir=OUT_DIR, dry_run=False):
    started = now_utc()
    as_of = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx = load_context()
    parts, markers, tries = None, None, 0
    while True:  # re-assemble if the live pipeline wrote a marker or the registry while we were reading
        active_ids = [a["design_id"] for a in read_json(REG / "ACTIVE_DESIGNS.json")["designs"]]
        markers = scan_markers(active_ids)
        before = fingerprint(markers)
        parts = assemble(ctx, markers)
        after = fingerprint(scan_markers([a["design_id"] for a in read_json(REG / "ACTIVE_DESIGNS.json")["designs"]]))
        tries += 1
        if before == after or tries >= 3:
            break
    designs = parts["designs"]
    for d in designs:
        headline(d)
    bench_counts, bench_unmatched = attach_benchmarks(ctx, designs)
    counts = make_counts(designs, parts["excluded"], parts["unclassified"])
    recon = reconcile(ctx, designs, parts["excluded"])
    checks = run_checks(parts, designs, markers)
    checks["assembly_passes"] = tries
    checks["inputs_stable_during_run"] = before == after
    with_entry = [d for d in designs if d["benchmark"]]
    benchmark_summary = {
        "source": rel(OUT_DIR / "replication_targets" / "TARGETS_R2.json"),
        "designs_with_entry": len(with_entry),
        "by_class": dict(Counter(d["benchmark"]["q2_class"] for d in with_entry)),
        "by_class_estimated": dict(Counter(d["benchmark"]["q2_class"] for d in with_entry if d["status"] == "estimated")),
        "with_candidate_paper": sum(1 for d in with_entry if d["benchmark"]["n_candidates"] > 0),
        "with_published_estimate": sum(1 for d in with_entry if d["benchmark"]["published_estimate"]
                                       and d["benchmark"]["published_estimate"]["point"] is not None),
        "designs_with_first_order_variant_entry": sum(1 for d in designs if d["benchmark_first_order_variant"]),
        "first_order_variant_by_class": dict(Counter(d["benchmark_first_order_variant"]["q2_class"] for d in designs
                                                     if d["benchmark_first_order_variant"])),
        "targets_without_live_design": bench_unmatched,
        "note": "q2_class: strict_possible / non_strict / none_found / unavailable (TARGETS_R2 class_definitions). "
                "A first-order-variant entry (<id>_fo) benchmarks a different outcome card than the executed design.",
    }
    inputs = {}
    for p in (REG / "TERMINAL_RUNS.json", REG / "ACTIVE_DESIGNS.json", REG / "SCOREBOARD_SNAPSHOT_20261011.json",
              OUT_DIR / "l2_set_v2.json", OUT_DIR / "replication_targets" / "TARGETS_R2.json",
              AUTOPILOT / "c53" / "PARK_CLASSIFICATION.json", ROOT / RULE_DOC,
              ROOT / "docs/plans/release-20261011/ESTIMATES_SOURCES.tsv"):
        if p.exists():
            inputs[rel(p)] = file_facts(p)
    payload = clean({
        "schema_version": SCHEMA, "as_of": as_of, "rule": RULE_TEXT, "rule_source": RULE_DOC,
        "generator": {"script": rel(Path(__file__)), "script_sha256": sha256_file(Path(__file__)),
                      "scan_started_utc": as_of, "scan_finished_utc": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "l2_files_modified": 0},
        "gate_labels": {g: {"en": g, "zh": GATE_ZH[g]} for g in GATES},
        "counts": counts, "designs": designs,
        "excluded_not_counted": {k: sorted(v, key=lambda x: x["design_id"]) for k, v in sorted(parts["excluded"].items())},
        "unclassified": parts["unclassified"], "superseded_records": parts["superseded"],
        "conflicts": parts["conflicts"],
        "reconciliation_vs_20261011": recon, "benchmark_summary": benchmark_summary,
        "checks": checks, "inputs": inputs,
    })
    if dry_run:
        return payload, None, None
    live, archived = write_versioned(payload, out_dir, as_of)
    return payload, live, archived


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="build and print, write nothing")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="directory for l2_set_live.json (default data/l3_meta)")
    args = ap.parse_args(argv)
    payload, live, archived = build(Path(args.out_dir), args.dry_run)
    print_scoreboard(payload)
    if live:
        print(f"  wrote {rel(live)}" + (f"; previous version kept as {rel(archived)}" if archived else "; no previous version"))
    else:
        print("  dry run: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
