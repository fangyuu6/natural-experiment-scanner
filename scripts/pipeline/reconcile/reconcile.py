#!/usr/bin/env python3
"""Reconcile locked pilot submissions without estimating outcomes or inventing zeros.

Administrative baseline rows align by template unit/field, not coder-specific IDs.
Discovered rows require an explicit reviewed crosswalk; otherwise retain each side
as missing on the other side. Whole-field comparisons deliberately do not turn
different JSON schemas or partly overlapping legal histories into agreement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

UNKNOWN = {"unknown"}
INCLUDE_SCOPE = ("scope_role", "date_precision", "date_lower", "date_upper",
                 "coverage_start", "coverage_end")


def canonical(value):
    """Object keys and unordered sets normalize; strings/legal clocks do not."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        values = {json.dumps(canonical(v), sort_keys=True, ensure_ascii=False)
                  for v in value}
        return [json.loads(v) for v in sorted(values)]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("Nonfinite JSON value")
        return int(value) if value == int(value) else value
    return value


def dump(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def value_token(row):
    if row is None:
        return None
    status = row["value_status"]
    if status in UNKNOWN:
        return dump([status])  # Blocker prose is not a legal answer.
    try:
        value = canonical(json.loads(row["value"]))
    except json.JSONDecodeError:
        value = row["value"].strip()
    return dump([status, value, {k: row.get(k, "") for k in INCLUDE_SCOPE}])


def addressable(row):
    """Evidence addressability only; a human still checks legal sufficiency."""
    if not row or row["value_status"] != "known":
        return False
    try:
        evidence = json.loads(row.get("evidence_json") or "[]")
    except ValueError:
        return False
    return isinstance(evidence,list) and bool(evidence) and all(
        isinstance(e,dict) and
        e.get("url") and e.get("locator") and e.get("accessed_date")
        and (e.get("official_citation") or e.get("instrument_id"))
        for e in evidence
    )


def compare(a, b, sufficient=False):
    if a is None or b is None:
        return "disputed", "missing_coder_row"
    if a["value_status"] == b["value_status"] == "unknown":
        return "both-unknown", "common_missingness_not_certification"
    if a["value_status"] == b["value_status"] == "not_stated":
        return "disputed", "textual_silence_requires_matched_complete_version"
    if a["value_status"] == b["value_status"] == "known" and value_token(a) == value_token(b):
        if addressable(a) and addressable(b) and sufficient:
            return "accepted_identical", "same_scoped_value_human_evidence_sufficiency_review"
        return "disputed", "identical_value_evidence_sufficiency_unreviewed"
    return "disputed", "different_status_value_scope_or_clock"


def kappa(pairs):
    """Unweighted kappa on paired observations; missing rows never agree."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    n = len(pairs)
    if not n:
        return None
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    po = sum(a == b for a, b in pairs) / n
    pe = sum(ca[k] * cb[k] for k in ca.keys() | cb.keys()) / n**2
    return None if math.isclose(pe, 1) else (po - pe) / (1 - pe)


def adjudicate(a, b, review=None):
    """A source recheck may resolve a whole field; component rulings cannot."""
    review = review or {}
    initial, reason = compare(a,b,bool(review.get("identical_evidence_sufficient")))
    final, value = initial, None
    if review.get("whole_field_resolved"):
        if not review.get("evidence") or "ruling_value" not in review:
            raise ValueError("Whole-field recheck requires material evidence and a ruling")
        statuses = {r["value_status"] for r in (a,b) if r}
        if (review.get("exact_quote_matched") and review.get("matched_source_version_verified")
                and review.get("all_material_components_reviewed") and statuses == {"known","not_stated"}):
            final = "accepted_evidence"
        else:
            final = "accepted_rechecked"
        value = review["ruling_value"]
    elif initial == "accepted_identical":
        value = a["value"]
    return initial, final, reason, value


def read_rows(path):
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    for i, row in enumerate(rows, 2):
        row["row_ref"] = f"{path.as_posix()}:{i}"
    return rows


def validate_submission(rows, template):
    issues = []
    seen = set()
    assigned = {(r["unit_id"], r["field_id"]) for r in template}
    observed = {(r["unit_id"], r["field_id"]) for r in rows}
    for key in sorted(assigned - observed):
        issues.append({"issue": "missing_assigned_field", "key": key})
    for row in rows:
        key = tuple(row.get(k, "") for k in ("unit_id", "field_id", "record_id", "version_id"))
        if key in seen:
            issues.append({"issue": "duplicate_record_version_field", "row": row["row_ref"]})
        seen.add(key)
        if not row.get("record_id") or not row.get("version_id"):
            issues.append({"issue": "unassigned_record_or_version_id", "row": row["row_ref"]})
        if row["value_status"] not in {"known", "unknown", "not_stated", "not_applicable", "disputed"}:
            issues.append({"issue": "invalid_status", "row": row["row_ref"]})
        for name in ("evidence_json", "search_log_json"):
            if not row.get(name):
                # Original submissions use blanks for unavailable evidence.
                # Preserve this incomplete metadata separately from malformed JSON.
                issues.append({"issue": "blank_json_metadata", "column": name, "row": row["row_ref"]})
                continue
            try:
                json.loads(row[name])
            except (ValueError, KeyError):
                issues.append({"issue": "invalid_json", "column": name, "row": row["row_ref"]})
        if row["value_status"] == "known" and not addressable(row):
            issues.append({"issue": "known_evidence_not_addressable", "row": row["row_ref"]})
        if row["value_status"] == "known" and not row.get("value"):
            issues.append({"issue": "known_value_blank", "row": row["row_ref"]})
        valid_dates={}
        for name in ("date_lower","date_upper","coverage_start","coverage_end"):
            if not row.get(name):
                continue
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",row[name]):
                    raise ValueError("wrong date syntax")
                valid_dates[name]=date.fromisoformat(row[name])
            except ValueError:
                issues.append({"issue":"invalid_declared_date","column":name,"row":row["row_ref"]})
        for lower,upper in (("date_lower","date_upper"),("coverage_start","coverage_end")):
            if lower in valid_dates and upper in valid_dates and valid_dates[lower]>valid_dates[upper]:
                issues.append({"issue":"reversed_date_bounds","columns":[lower,upper],"row":row["row_ref"]})
        if row.get("date_precision") == "day" and row.get("date_lower") and row.get("date_upper") and row["date_lower"] != row["date_upper"]:
            issues.append({"issue":"day_precision_has_interval","row":row["row_ref"]})
        if row["field_id"] == "never_treated_certification" and row["value_status"] == "known":
            end=valid_dates.get("coverage_end")
            if end is None or end<date(2025,12,31):
                issues.append({"issue":"partial_negative_certificate_not_full_pilot_window","row":row["row_ref"]})
        if len(row.get("quote", "")) > 300:
            issues.append({"issue": "quote_over_300_characters", "row": row["row_ref"]})
        try:
            minutes = float(row["minutes_spent"] or 0)
            if not math.isfinite(minutes) or minutes < 0:
                raise ValueError("negative/nonfinite")
            if not row["minutes_spent"]:
                issues.append({"issue": "blank_minutes_shared_allocation_unverified", "row": row["row_ref"]})
        except ValueError:
            issues.append({"issue": "invalid_minutes", "row": row["row_ref"]})
    return issues


def align(a, b, template, reviewed_pairs=()):
    """Align assigned baselines once; never greedily match discoveries by answer."""
    assigned = {(r["unit_id"], r["field_id"]) for r in template}
    groups = []
    used = set()
    for key in sorted(assigned):
        aa = next((r for r in a if (r["unit_id"], r["field_id"]) == key), None)
        bb = next((r for r in b if (r["unit_id"], r["field_id"]) == key), None)
        groups.append((aa, bb, "assigned_baseline", key))
        used.update(r["row_ref"] for r in (aa, bb) if r)
    byref = {r["row_ref"]: r for r in a + b}
    for pair in reviewed_pairs:
        aa, bb = byref[pair["A_row_ref"]], byref[pair["B_row_ref"]]
        if aa["row_ref"] in used or bb["row_ref"] in used:
            raise ValueError("Crosswalk reuses baseline/already matched row")
        if aa["field_id"] != bb["field_id"] or not pair.get("identity_basis"):
            raise ValueError("Crosswalk needs matched field and explicit legal identity basis")
        used.update((aa["row_ref"], bb["row_ref"]))
        groups.append((aa, bb, "reviewed_discovery_crosswalk", (aa["unit_id"], aa["field_id"])))
    for side, rows in (("A", a), ("B", b)):
        for row in rows:
            if row["row_ref"] not in used:
                groups.append((row if side == "A" else None,
                               row if side == "B" else None,
                               "unmatched_discovered_record", (row["unit_id"], row["field_id"])))
    return groups


def metrics(rows):
    n = len(rows)
    paired = [r for r in rows if r["A"] is not None and r["B"] is not None]
    known = [r for r in paired if r["A"]["value_status"] == r["B"]["value_status"] == "known"]
    anchored = [r for r in known if addressable(r["A"]) and addressable(r["B"])]
    exact = sum(value_token(r["A"]) == value_token(r["B"]) for r in paired)
    unknown = sum(r["initial_status"] == "both-unknown" for r in rows)
    status_counts = Counter(r["final_status"] for r in rows)
    for status in ("accepted_identical", "accepted_evidence", "accepted_rechecked", "disputed", "both-unknown"):
        status_counts.setdefault(status, 0)
    # Both unknown/one-sided missing/disputed fields are unresolved for production.
    unresolved = sum(r["final_status"] in {"disputed", "both-unknown"} for r in rows)
    shared_known = Counter(value_token(r["A"]) for r in known
                           if value_token(r["A"]) == value_token(r["B"]))
    informative = len(shared_known) >= 2 and any(v > 1 for v in shared_known.values())
    status_informative = all(len({r[c]["value_status"] for r in paired}) > 1 for c in ("A","B"))
    return {"union_fields": n, "paired_fields": len(paired), "missing_side_fields": n-len(paired),
            "raw_agreements": exact, "raw_agreement_union": exact/n if n else None,
            "raw_agreement_paired": exact/len(paired) if paired else None,
            "whole_field_kappa": kappa([(value_token(r["A"]), value_token(r["B"])) for r in rows]) if informative else None,
            "whole_field_kappa_informative": informative,
            "whole_field_kappa_note": "No shared repeated known categories; free-text JSON schemas differ." if not informative else "Paired rows only; union missingness separately reported.",
            "status_kappa_supplemental": kappa([(r["A"]["value_status"],r["B"]["value_status"]) for r in paired]) if status_informative else None,
            "status_kappa_informative": status_informative,
            "status_kappa_note": "Status labels only; does not certify legal-value agreement. Undefined/uninformative with a constant coder margin.",
            "both_known_fields": len(known), "both_known_addressable_evidence_fields": len(anchored),
            "known_supported_agreements": sum(r["initial_status"] == "accepted_identical" for r in anchored),
            "known_supported_agreement": sum(r["initial_status"] == "accepted_identical" for r in anchored)/len(anchored) if anchored else None,
            "known_supported_denominator_note": "Both known with field-addressable cited evidence; agreement additionally requires human legal-sufficiency review. Partial evidence never certifies full histories.",
            "both_unknown_fields": unknown, "both_unknown_share": unknown/n if n else None,
            "final_counts": dict(status_counts), "unresolved_fields": unresolved,
            "unresolved_share": unresolved/n if n else None,
            "production_paused": unresolved/n > .2 if n else True}


def write_csv(path, rows, columns):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(root, output, review=None):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    review = review or {"crosswalk": [], "field_reviews": []}
    all_rows, crosswalk, validations, hashes = [], [], {}, {}
    for path in sorted((root/"templates").glob("*.csv")):
        design = path.stem
        if not (root/"A"/path.name).exists() or not (root/"B"/path.name).exists():
            raise ValueError(f"Incomplete design submission {design}")
        template = read_rows(path)
        a, b = read_rows(root/"A"/path.name), read_rows(root/"B"/path.name)
        for c, rows in (("A",a),("B",b)):
            validations[f"{design}/{c}"] = validate_submission(rows, template)
            for p in (root/c/path.name, (root/c/path.name).with_suffix(".md"), path):
                hashes[p.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        pairs = [p for p in review.get("crosswalk", []) if p["design_id"] == design]
        for i, (aa, bb, basis, key) in enumerate(align(a,b,template,pairs), 1):
            comparison_id = f"{design}:{i:04}"
            refpair = (aa["row_ref"] if aa else "", bb["row_ref"] if bb else "")
            recheck = next((r for r in review.get("field_reviews", [])
                            if (r.get("A_row_ref", ""),r.get("B_row_ref", "")) == refpair), None)
            initial, final, reason, value = adjudicate(aa,bb,recheck)
            row = {"comparison_id":comparison_id,"design_id":design,"unit_id":key[0],
                   "field_id":key[1],"alignment_basis":basis,"A":aa,"B":bb,
                   "initial_status":initial,"final_status":final,"basis":reason,
                   "reconciled_value":value,"review":recheck}
            all_rows.append(row)
            crosswalk.append({"comparison_id":comparison_id,"design_id":design,"unit_id":key[0],
                              "field_id":key[1],"alignment_basis":basis,
                              "A_row_ref":refpair[0],"B_row_ref":refpair[1],
                              "A_record_id":aa["record_id"] if aa else "",
                              "B_record_id":bb["record_id"] if bb else "",
                              "A_version_id":aa["version_id"] if aa else "",
                              "B_version_id":bb["version_id"] if bb else ""})
    metrics_out = {"designs":{},"fields":{}}
    for design in sorted({r["design_id"] for r in all_rows}):
        rows = [r for r in all_rows if r["design_id"] == design]
        metrics_out["designs"][design] = metrics(rows)
        metrics_out["fields"][design] = {f:metrics([r for r in rows if r["field_id"] == f])
                                            for f in sorted({r["field_id"] for r in rows})}
    metrics_out["overall"] = metrics(all_rows)
    columns = ["comparison_id","design_id","unit_id","field_id","alignment_basis",
               "initial_status","final_status","basis","reconciled_value","A_json","B_json","review_json"]
    flat = [{**{k:r[k] if r[k] is not None else "" for k in columns if k in r},
             "A_json":dump(r["A"]),"B_json":dump(r["B"]),"review_json":dump(r["review"])} for r in all_rows]
    write_csv(output/"reconciled_fields.csv",flat,columns)
    write_csv(output/"disputes.csv",[r for r in flat if r["final_status"] == "disputed"],columns)
    write_csv(output/"crosswalk.csv",crosswalk,list(crosswalk[0]))
    for name, obj in (("metrics.json",metrics_out),("submission_validation.json",validations),
                      ("reconciliation_input_hashes.json",hashes)):
        (output/name).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+"\n")
    return metrics_out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("data/pipeline/coding"))
    parser.add_argument("--output",type=Path,default=Path("data/pipeline/coding/reconciled"))
    parser.add_argument("--review",type=Path)
    args = parser.parse_args()
    result = run(args.root,args.output,json.loads(args.review.read_text()) if args.review else None)
    print(json.dumps(result["overall"],indent=2))


if __name__ == "__main__":
    main()
