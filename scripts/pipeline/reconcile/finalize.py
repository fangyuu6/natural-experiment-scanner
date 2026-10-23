#!/usr/bin/env python3
"""Bind third-read components to immutable pilot rows and build review artifacts."""
import csv
import hashlib
import json
from pathlib import Path

from reconcile import align, dump, read_rows, run, write_csv

OUT = Path("data/pipeline/coding/reconciled")
ROOT = OUT.parent
LABELS = {"did-12289964c5c16b8620de":"Licensing recognition",
          "did-3ee1dcd04a7f41715f72":"Paid family leave",
          "did-88990ee6c1033c449c9c":"Prevailing wage",
          "did-c2e2f141c074e2a2eab4":"EITC",
          "rdd-88c354e13eddfb55a29c":"WIA designation",
          "rct-f4f160db1eb3f29d5af0":"H-1B lottery"}


def load(name):
    return json.loads((OUT/name).read_text())


def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n")


def identity(row):
    return tuple(row.get(k, "") for k in ("unit_id","field_id","record_id","version_id"))


def originals(ruling,coder):
    return ruling.get("original",{}).get(coder,ruling.get("original_"+coder,[]))


def citations(ruling):
    return ruling.get("evidence",ruling.get("sources",[]))


def main():
    # Stop if either coder changes a submitted file while reconciliation runs.
    lock=load("lock_manifest.json")
    changed=[x["path"] for x in lock["input_files"]
             if hashlib.sha256(Path(x["path"]).read_bytes()).hexdigest()!=x["sha256"]]
    if changed:
        raise ValueError("Locked input changed: "+str(changed))
    did,other,root=load("did_review.json"),load("rdd_lottery_review.json"),load("root_review.json")
    rulings=did["component_rulings"]+other["rulings"]+root["component_rulings"]
    for i,r in enumerate(did["set_certification_reviews"],1):
        rulings.append({**r,"ruling_id":f"DID-SET-CERT-{i}","unit_id":"ALL_ASSIGNED_STATES",
                        "evidence":[],"scope":"Full assigned common-window history remains unknown"})
    rows={}
    for design in LABELS:
        rows[design]={c:read_rows(ROOT/c/(design+".csv")) for c in ("A","B")}
    crosswalk=[]
    # These two legal decision identities were explicitly reopened in the third read.
    # Their coder-specific unit labels differ; answers are never used as match keys.
    for ruling in other["rulings"]:
        if not ruling["ruling_id"].startswith("WIA-R4"):
            continue
        pair={}
        for c in ("A","B"):
            original=next(r for r in originals(ruling,c) if r["field_id"]=="designation_records")
            pair[c+"_row_ref"]=next(r["row_ref"] for r in rows[ruling["design_id"]][c]
                                         if identity(r)==identity(original))
        crosswalk.append({**pair,"design_id":ruling["design_id"],
                          "identity_basis":"Same local-area decision in Alaska AO182 (1999-06-03); "+ruling["ruling_id"]})
    field_reviews=[]
    for design in LABELS:
        byref={c:{identity(r):r for r in rows[design][c]} for c in ("A","B")}
        refs={}
        for ruling in (r for r in rulings if r["design_id"]==design):
            for c in ("A","B"):
                for original in originals(ruling,c):
                    row=byref[c].get(identity(original))
                    if row:
                        refs.setdefault(row["row_ref"],[]).append(ruling["ruling_id"])
        template=read_rows(ROOT/"templates"/(design+".csv"))
        for a,b,_,_ in align(rows[design]["A"],rows[design]["B"],template,
                              [p for p in crosswalk if p["design_id"]==design]):
            linked=sorted(set(refs.get(a["row_ref"],[]) if a else [])|
                          set(refs.get(b["row_ref"],[]) if b else []))
            if linked:
                field_reviews.append({"A_row_ref":a["row_ref"] if a else "",
                                      "B_row_ref":b["row_ref"] if b else "",
                                      "ruling_ids":linked,"whole_field_resolved":False,
                                      "reason":"Component/version findings retain complete-history and source gaps.",
                                      "adjudication_ref":"data/pipeline/coding/reconciled/adjudication_batch1.csv"})
    decisions={"crosswalk":crosswalk,"field_reviews":field_reviews,
               "policy":"Original whole-field/status agreement is never inflated by third-read component findings."}
    save("reviewer_decisions.json",decisions)
    result=run(ROOT,OUT,decisions)
    flat=[]
    for r in rulings:
        flat.append({"ruling_id":r["ruling_id"],"design_id":r["design_id"],"unit_id":r["unit_id"],
                     "field_ids":dump(r["field_ids"]),"original_A_json":dump(originals(r,"A")),
                     "original_B_json":dump(originals(r,"B")),"ruling_value_json":dump(r["ruling_value"]),
                     "disposition":r["disposition"],"whole_field_resolved":False,
                     "reason":r.get("reason",""),"evidence_json":dump(citations(r)),
                     "scope":r.get("scope","Selected component only; not full-field certification")})
    write_csv(OUT/"adjudication_batch1.csv",flat,list(flat[0]))
    audit=did["l1_accuracy_audit"]+[
        {"design_id":d,"confirmed_error_count":a["L1_error_count"],"errors":a["errors"],
         "dossier_error_count":a["dossier_error_count"],"pattern":a.get("pattern_summary",a.get("pattern","")),
         "unverified_or_omissions":a.get("nonerrors_or_updates",[])}
        for d,a in other["l1_accuracy_audit"].items()]
    for a in audit:
        a.setdefault("dossier_error_count",0)
        if a["design_id"] == "did-c2e2f141c074e2a2eab4":
            a["unverified_or_omissions"] = [
                "MD final-law visual check confirms 100% and age21 proposals are struck; original L1 whole-source extraction does not assert100%, so no additional L1 error counted."
                if str(v).startswith("MD100%") else v for v in a["unverified_or_omissions"]]
    save("l1_accuracy_audit.json",audit)
    lines=["# L1 claim audit", "", "Counts are lower bounds of distinct contradicted claim cells, not an accuracy rate. Omissions and already corrected dossier cautions are separate. WIA has three distinct cells about one assignment mechanism; H-1B has one compound rule/fee timing claim.", ""]
    for a in audit:
        lines += [f"## {LABELS[a['design_id']]} — L1 errors {a['confirmed_error_count']}; dossier errors {a['dossier_error_count']}", ""]
        for error in a["errors"]:
            evidence=error.get("sources",[])
            url=error.get("evidence_url",evidence[0]["url"] if evidence else "")
            locator=error.get("locator",evidence[0].get("locator","") if evidence else "")
            correction=error['correction'].rstrip('.')
            lines += [f"- {error.get('claim_origin',error.get('field','L1'))}: {error['claim']} → {correction}. [Official source]({url}), {locator}."]
        if not a["errors"]:
            lines += ["No specific contradicted claim established in the materials checked."]
        lines += ["",a.get("pattern", ""), ""]
        for omission in a.get("unverified_or_omissions",[]):
            lines += ["- Unverified/omission: "+str(omission)]
        lines.append("")
    (OUT/"L1_ACCURACY.md").write_text("\n".join(lines)+"\n")
    lines=["# Third read on the cited materials", "", "Original A/B values, statuses, evidence and identifiers are retained in adjudication_batch1.csv and the per-track JSON reviews. These rulings resolve selected components only; no complete whole-field history is accepted. Reviewers reopened the same cited versions; inaccessible/conflicting material stays explicit. A new neutral crosswalk aligns two Alaska designation decisions by official AO182 identity.", ""]
    for r in rulings:
        sources="; ".join(f"[source]({e['url']}) ({e.get('locator','')})" for e in citations(r))
        lines += [f"## {r['ruling_id']} — {LABELS[r['design_id']]} / {r['unit_id']}", "",
                  f"Fields: {', '.join(r['field_ids'])}. {r['disposition']}.", "",
                  "Ruling: `"+dump(r["ruling_value"])+"`", "",r.get("reason",""), "", sources, ""]
    (OUT/"THIRD_READ.md").write_text("\n".join(lines)+"\n")
    receipt=load("receipt_audit.json")
    download_columns=["coder","design_id","source_id","url","bytes_reported","license_or_use_terms",
                      "sha256_reported","local_path","receipt_matches_local","verification"]
    write_csv(OUT/"downloads.csv",[{k:d.get(k,"") for k in download_columns} for d in receipt["downloads"]],download_columns)
    throughput=[]
    for d in LABELS:
        a=next(t for t in receipt["throughput"] if t["coder"]=="A" and t["design_id"]==d)
        b=next(t for t in receipt["throughput"] if t["coder"]=="B" and t["design_id"]==d)
        throughput.append({"design_id":d,"label":LABELS[d],"A_R1_minutes":a["csv_minutes_sum"],
                           "A_prior_stopped_minutes":a["prior_stopped_minutes"],
                           "A_cumulative_minutes":a["cumulative_measured_minutes"],
                           "B_csv_minutes":b["csv_minutes_sum"],"B_notes_active_minutes":b["notes_active_minutes"],
                           "B_notes_minus_csv_minutes":b["notes_minus_csv_minutes"],"data_ready_designs":0})
    write_csv(OUT/"throughput.csv",throughput,list(throughput[0]))
    prior_path=Path("data/pipeline/queue_v2/first_batch_registry.json")
    previous=json.loads(prior_path.read_text())
    save("registry.previous.json",previous)
    reviews=[]
    for d in LABELS:
        old=next(x for x in previous["designs"] if x["design_id"]==d)
        if d.startswith("did-"):
            finding=next(s for s in did["set_certification_reviews"] if s["design_id"]==d)
            status="verifying"
            reason="Core exposure/control certification incomplete: "+finding["reason"]
            missing=finding["before_freeze"]
            reopen=None
        else:
            finding=other["readiness"][d]
            status=finding["recommended_status"];reason=finding["reason"]
            missing=finding["before_freeze"]
            reopen=finding["reopen_condition"] if status=="stopped" else None
        refs=["data/pipeline/coding/reconciled/reconciled_fields.csv#"+d,
              "data/pipeline/coding/reconciled/adjudication_batch1.csv#"+d,
              "data/pipeline/coding/reconciled/metrics.json#fields/"+d]
        checks={k:False for k in old["readiness"]["checks"]}
        # All legal instruments remain provisional; do not promote partial components
        # to the all-version design readiness booleans (including threshold).
        reviews.append({"design_id":d,"status":status,"reason":reason,"reopen_condition":reopen,
                        "evidence_refs":refs,"readiness":{"checks":checks,"evidence_refs":refs,
                        "missing":missing+["Version/source crosswalk and independently comparable complete fields; paused field comparisons (>20% unresolved)"]}})
    save("registry_reviews.json",reviews)
    # This output is the reviewed registry snapshot; restricted output scope leaves
    # queue-v2 intake bytes untouched. The registry review/validator tools apply it.
    save("finalization_receipt.json",{"input_lock_verified":True,"locked_files":len(lock["input_files"]),
                                     "component_rulings":len(rulings)-4,"set_certification_reviews":4,
                                     "linked_comparison_fields":len(field_reviews),
                                     "identity_crosswalk_pairs":len(crosswalk),
                                     "registry_input_path":str(prior_path),
                                     "registry_input_sha256":hashlib.sha256(prior_path.read_bytes()).hexdigest(),
                                     "output_registry_path":"data/pipeline/coding/reconciled/registry.json",
                                     "new_downloads":[],"estimation_calls":0,"outcome_association_tables":0,
                                     "overall":result["overall"]})
    print(dump({"linked_fields":len(field_reviews),"rulings":len(rulings),"union_fields":result["overall"]["union_fields"]}))


if __name__ == "__main__":
    main()
