"""Validate original agent artifacts before immutable stage promotion."""
from __future__ import annotations
import argparse, json, math, re
from pathlib import Path
from urllib.parse import urlsplit
from ..storage import sha, ensure_once, public_input
from .jobs import load_job, historical_draft_evidence


def _finite(value): return type(value) in (int,float) and math.isfinite(value)
def _text(value): return isinstance(value,str) and bool(value.strip())
def _locator_text(value):
    """Accept official locators as text or losslessly serializable JSON data.

    C20 ruling 1 changes representation only. Page/line numbers are valid
    structured locators; empty, blank-only or boolean-only data is not evidence.
    """
    if _text(value): return value
    if not isinstance(value, (list, dict)) or not value:
        raise ValueError("Official source evidence missing")
    def json_data(item):
        if item is None or type(item) in (str, bool, int): return True
        if type(item) is float: return math.isfinite(item)
        if isinstance(item, list): return all(json_data(v) for v in item)
        if isinstance(item, dict):
            return all(type(k) is str and json_data(v) for k,v in item.items())
        return False
    if not json_data(value): raise ValueError("Official locator is not lossless JSON")
    def has_text(item):
        if isinstance(item, str): return bool(item.strip())
        if type(item) in (int,float): return True
        if isinstance(item, list): return any(has_text(v) for v in item)
        if isinstance(item, dict): return any(has_text(v) for v in item.values())
        return False
    if not has_text(value): raise ValueError("Official source evidence missing")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Official locator is not lossless JSON") from None

def _locator_checks(checks):
    """Keep native originals, with a canonical text alias for structured locators."""
    result=[]
    for row in checks:
        locator=row.get("official_locator")
        canonical=_locator_text(locator)
        copied=dict(row)
        if isinstance(locator,(list,dict)):
            copied["official_locator_canonical_json"]=canonical
        result.append(copied)
    return result
def _blind(value):
    keys={"other_submissions_read","outcome_associations_read"}
    if not isinstance(value,dict) or set(value)!=keys or any(value[k] is not False for k in keys): raise ValueError("Missing/contaminated blindness attestation")


def _reject_project_exposure(value):
    fields={"our_outcome_data_read","our_estimates_read","our_forecasts_read",
        "project_outcome_files_read","outcome_files_read","estimates_or_frozen_results_files_read",
        "outcomes_fitted","outcome_models_fitted","outcome_associations_computed",
        "outcome_associations_run","original_coder_submissions_read","other_submissions_read"}
    def check(item):
        if isinstance(item,dict):
            for key,entry in item.items():
                if key in fields and entry is not False:
                    raise ValueError("Contaminated V7 project blindness disclosure")
                if key in {"blindness_details","blindness_limitation","blindness_incident","blindness_attestation"}:check(entry)
    check(value)


def _public_history_incident(incident):
    """Recognize located legacy disclosures, not a bare true/no-exposure claim.

    Preserve every word in the original. Missing/ambiguous project exposure
    still rejects. C48 permits the public reports/search snippets described in
    the two legacy incident forms; this is a collector interpretation receipt.
    """
    if not isinstance(incident,dict):return False
    text=" ".join(v for v in incident.values() if isinstance(v,str))
    public=bool(re.search(r"public|published",text,re.I))
    speed=(isinstance(incident.get('detail'),str) and
        'No repository outcome panels, peer submissions' in incident['detail'] and
        'run associations' in incident['detail'] and
        'not used' in incident['detail'])
    audit=(incident.get('type')=='unsolicited_search_result_exposure' and
        isinstance(incident.get('description'),str) and
        'No such study was opened or downloaded' in incident['description'] and
        'no original coder submission or design outcome file was accessed' in incident['description'] and
        'outcome snippets were not used to determine any check' in incident['description'])
    return public and bool(speed or audit)

def validate_blindness(job, value):
    """V7 covers published-study jobs and C48 legal history/purpose/audit roles.

    Legacy public-result exposure is accepted only with explicit project-data
    non-exposure disclosures. Original artifacts and their attestations stay intact.
    """
    att=value.get("blindness_attestation")
    if job.contract.get("forecast_slot"):
        keys={"other_submissions_read","this_design_outcomes_read","this_design_estimates_read","outside_slot_pack_read"}
        # REDESIGN R2: a bundle member was written by an author who read all three packs, so that one key must be true.
        expected={k:(k=="outside_slot_pack_read" and job.contract.get("bundle_member") is True) for k in keys}
        if not isinstance(att,dict) or set(att)!=keys or any(att[k] is not expected[k] for k in keys):
            raise ValueError("Forecast slot blindness unconfirmed")
        return None
    history_scope = getattr(job,'stage',None) in {"S0", "S1"}
    if job.contract.get("source_type")!="published_did_study" and not history_scope:
        _blind(att); return None
    # Keep old accepted all-false receipts byte-identical; C48 broadens the
    # permitted PUBLIC exposure for history/audit, never project/peer exposure.
    if history_scope and job.contract.get("source_type")!="published_did_study" and job.contract.get("blindness_scope")!="our_outcomes_and_estimates_v7" and att == {"other_submissions_read":False,"outcome_associations_read":False}:
        _blind(att)
        _reject_project_exposure(value)
        return None
    _reject_project_exposure(value)
    for field in ("our_outcome_data_read", "our_estimates_read"):
        if field in value and value[field] is not False:
            raise ValueError("Contaminated V7 project blindness disclosure")
    keys={"other_submissions_read","our_outcome_data_read","our_estimates_read","published_results_read"}
    if isinstance(att,dict) and set(att)==keys:
        if (any(att[k] is not False for k in keys-{"published_results_read"})
                or type(att["published_results_read"]) is not bool):
            raise ValueError("Missing/contaminated V7 blindness attestation")
        published=att["published_results_read"];basis="explicit_v7_attestation"
    elif job.contract.get("blindness_scope")=="our_outcomes_and_estimates_v7":
        raise ValueError("Missing V7 blindness attestation")
    elif isinstance(att,dict) and set(att)=={"other_submissions_read","outcome_associations_read"}:
        if att["other_submissions_read"] is not False or type(att["outcome_associations_read"]) is not bool:
            raise ValueError("Missing/contaminated blindness attestation")
        published=att["outcome_associations_read"]
        if published:
            detail=value.get("blindness_details",{});limit=value.get("blindness_limitation",{})
            for disclosure in (detail,limit):
                if not isinstance(disclosure,dict): continue
                for field in ("outcome_files_read","project_outcome_files_read","estimates_or_frozen_results_files_read",
                              "outcomes_fitted","outcome_models_fitted","our_outcome_data_read","our_estimates_read"):
                    if field in disclosure and disclosure[field] is not False:
                        raise ValueError("Contaminated legacy project blindness disclosure")
            fp=(isinstance(detail,dict) and all(detail.get(k) is False for k in
                ("outcome_files_read","estimates_or_frozen_results_files_read","outcomes_fitted"))
                and detail.get("incidental_public_search_snippet_exposure") is True
                and _text(detail.get("deviation")) and re.search(r"published",detail["deviation"],re.I))
            gdl=(isinstance(limit,dict) and all(limit.get(k) is False for k in
                ("project_outcome_files_read","outcome_models_fitted"))
                and _text(limit.get("reason")) and re.search(r"published",limit["reason"],re.I)
                and _text(limit.get("disclosure")) and re.search(r"public|tool",limit["disclosure"],re.I))
            incident=value.get("blindness_incident",{})
            public_only=_public_history_incident(incident) if history_scope else False
            if not (fp or gdl or public_only): raise ValueError("Legacy exposure lacks project blindness disclosure")
        basis="legacy_public_exposure_disclosure" if published else "legacy_all_false_attestation"
    else:
        raise ValueError("Missing/contaminated blindness attestation")
    return {"rule":"HUB_RULING_V7_LITERATURE_BLINDNESS", "scope":"our_outcomes_and_estimates",
            "other_submissions_read":False,"our_outcome_data_read":False,"our_estimates_read":False,
            "published_results_read":published,"validation_basis":basis}

def _forecast_numbers(point,bounds,level):
    if not _finite(point) or not isinstance(bounds,list) or len(bounds)!=2 or not all(_finite(x) for x in bounds) or bounds[0]>bounds[1] or not _finite(level) or not 0<level<1: raise ValueError("Invalid forecast numbers")


def _work_time(original: Path) -> dict:
    """Record host wall time; distinguish old artifact estimates from measurements."""
    result=original.parent/"DISPATCH_RESULT.json"
    reservation=original.parent/"DISPATCH_ONCE.json"
    if result.is_file():
        value=json.loads(result.read_text())
        seconds=value.get("elapsed_seconds")
        if seconds is not None:
            if not _finite(seconds) or seconds<0: raise ValueError("Invalid host elapsed time")
            return {"elapsed_seconds":seconds,"time_source":"host_dispatch",
                    "dispatch_result_sha256":sha(result)}
        if reservation.is_file():
            return {"elapsed_seconds":max(0,result.stat().st_mtime-reservation.stat().st_mtime),
                    "time_source":"legacy_dispatch_artifact_mtime_estimate",
                    "dispatch_result_sha256":sha(result),"dispatch_reservation_sha256":sha(reservation)}
    return {"elapsed_seconds":None,"time_source":"host_dispatch_unavailable"}


def _pack_incomplete(contract: dict) -> bool:
    pack=contract.get("source_pack")
    if not isinstance(pack,dict): return False
    path=pack.get("manifest_path");digest=pack.get("manifest_sha256")
    if not _text(path) or not _text(digest): raise ValueError("Source pack manifest binding missing")
    manifest=public_input(Path(path))
    if sha(manifest)!=digest: raise ValueError("Source pack manifest changed")
    value=json.loads(manifest.read_text())
    if not isinstance(value,dict) or type(value.get("complete_list_complete")) is not bool:
        raise ValueError("Source pack completeness missing")
    if pack.get("complete_list_complete") is not value["complete_list_complete"]:
        raise ValueError("Source pack completeness binding changed")
    # A dated current-stock list or a universe gap is useful for coding but is
    # still incomplete for a historical treatment path.
    return (value["complete_list_complete"] is False or
            value.get("historical_coverage_complete") is False or
            bool(value.get("missing_units")))


def _specific_blockers(rows: list[dict]) -> tuple[bool,list[str]]:
    """Check auditable unit-bound searches/locators, rejecting copied catalog excuses.

    This validates the evidence contract, not the legal truth of a blocker. The
    official-source auditor remains responsible for substantive verification.
    """
    failed=[];signatures={}
    for row in rows:
        if row["status"]!="unknown": continue
        unit=row["unit"];blocker=row.get("blocker")
        if not isinstance(blocker,dict) or blocker.get("unit")!=unit or any(
                not _text(blocker.get(k)) for k in ("detail","official_locator","search_query")):
            failed.append(unit);continue
        token=r"(?<![A-Za-z0-9])"+re.escape(unit)+r"(?![A-Za-z0-9])"
        url=urlsplit(blocker["official_locator"])
        if (url.scheme not in {"http","https"} or not url.hostname or
                not re.search(token,blocker["search_query"],re.I) or
                not re.search(token,blocker["detail"],re.I)):
            failed.append(unit);continue
        # Changing only the unit name/code does not turn a blanket source failure
        # into a unit-specific blocker.
        signature=re.sub(r"\s+"," ",re.sub(token,"<unit>",blocker["detail"],flags=re.I)).strip().lower()
        signatures.setdefault(signature,[]).append(unit)
    for units in signatures.values():
        if len(units)>1: failed.extend(units)
    return not failed,sorted(set(failed))


def coder_quality(job, rows: list[dict], original: Path) -> dict:
    known=sum(row["status"]!="unknown" for row in rows)
    share=known/len(rows) if rows else 0
    if job.contract.get("evidence_standard")=="HUB_RULING_V9_HISTORY_EVIDENCE":
        # The V9 semantic validator ran first. Genuine named conflicts are
        # allowable unresolved evidence regardless of a percentage floor.
        return {"status":"PASS", "known_units":known, "total_units":len(rows),
                "known_share":share, "evidence_standard":"V9", "unknown_units":len(rows)-known,
                "all_unknowns_named_conflicts":True, "s1_ready":False,
                "reason":"V9 per-state evidence/conflict contract passes; reconciliation and fresh audit required",
                **_work_time(original)}
    incomplete=_pack_incomplete(job.contract)
    specific,failed=_specific_blockers(rows)
    passed=share>=.5 or (incomplete and specific)
    return {"status":"PASS" if passed else "INSUFFICIENT_WORK","known_units":known,
            "total_units":len(rows),"known_share":share,"known_share_floor":.5,
            "source_pack_incomplete":incomplete,"all_unknowns_unit_specific":specific,
            "blocker_contract_failed_units":failed,"low_share_exception":share<.5 and passed,
            "reason":"Known-status coverage or documented incomplete-pack exception passes" if passed else
                     "Known-status share below 50%; requires a hash-bound incomplete source pack and a distinct unit-specific blocker for every unknown",
            **_work_time(original)}


def collect_bundle(job, *, self_check: bool = False) -> dict:
    """REDESIGN R2: split one forecast bundle into the members' ledgers, each collected through its own job.

    All members are validated first (write-free), so a rejected bundle writes nothing and the one replacement can be issued."""
    c=job.contract;original=public_input(Path(job.output_path));value=json.loads(original.read_text())
    members=c["bundle_members"]
    if not isinstance(value,dict): raise ValueError("Agent output must be an object")
    if value.get("design_id")!=job.design_id or value.get("spec_sha256")!=c["spec_sha256"]: raise ValueError("Forecast identity mismatch")
    ledgers=value.get("members")
    if not isinstance(ledgers,dict) or set(ledgers)!=set(members) or any(not isinstance(v,dict) for v in ledgers.values()):
        raise ValueError("Forecast bundle must hold exactly the registered members")
    workers=set()
    for persona,ledger in ledgers.items():
        author=ledger.get("author_provenance")
        if isinstance(author,dict) and isinstance(author.get("worker_id"),str): workers.add(author["worker_id"])
        member=members[persona]
        meta=load_job(Path(member["job_path"]))
        if meta.job_sha256!=member["job_sha256"] or Path(meta.output_path)!=Path(member["output_path"]) or not meta.contract.get("bundle_member"):
            raise ValueError("Forecast bundle member job changed")
        collect_job(member["job_path"],self_check=True,_preloaded=ledger)
    if len(workers)!=1: raise ValueError("Forecast bundle members must share one author")
    if self_check:
        return dict(validated=True,self_check=True,stage=job.stage,design_id=job.design_id,role=job.role,original_sha256=sha(original),
                    execution_authorized=False,mechanism_issues=[])
    receipts={}
    for persona in sorted(members):
        member=members[persona];target=Path(member["output_path"])
        ensure_once(target,ledgers[persona])
        receipts[persona]=collect_job(member["job_path"])
        if receipts[persona].get("validated") is not True: raise ValueError("Forecast bundle member collection rejected: "+persona)
    from ..manual_evidence import collection_directory
    receipt_dir=collection_directory(job);promote=public_input(Path(job.promote_path))
    if promote!=original: ensure_once(promote,value)
    receipt={"schema_version":"1.0","stage":job.stage,"design_id":job.design_id,"role":job.role,"job_path":job.job_path,"job_sha256":job.job_sha256,
             "original_path":str(original),"original_sha256":sha(original),"promoted_path":str(promote),"promoted_sha256":sha(promote),
             "validated":True,"execution_authorized":False,"single_author_bundle":True,
             "members":{p:{"job_path":r["job_path"],"collection_sha256":sha(Path(r["original_path"]).parent/"COLLECTION.json") if (Path(r["original_path"]).parent/"COLLECTION.json").is_file() else None,
                          "promoted_sha256":r["promoted_sha256"]} for p,r in receipts.items()}}
    ensure_once(receipt_dir/"COLLECTION.json",receipt)
    if c.get("contract_replacement"):
        from .replacement import publish_receipt
        publish_receipt(job,receipt)
    elif c.get("attempt_id"):
        ensure_once(public_input(Path(c["canonical_collection_path"])),receipt)
    return receipt


def collect_job(path: Path | str, *, allow_historical_draft: bool = False, self_check: bool = False, _preloaded=None) -> dict:
    job=load_job(Path(path),allow_historical_draft=allow_historical_draft)
    if job.stage=="S5" and job.role=="forecast_bundle": return collect_bundle(job,self_check=self_check)
    from .. import snapshots
    contract = job.contract
    if contract.get('review_scope') == 'sealed_candidate' and contract.get('code_snapshot') and snapshots.active() is None:
        frozen = snapshots.manifest_for(Path(contract['snapshot_root']))
        # Legacy candidates retain their strict original validation path. Only
        # a new snapshot-bound final freeze can launch its bound collector.
        if frozen is not None:
            if self_check:
                tree=snapshots.validate(Path(contract['snapshot_root']),frozen['code_snapshot'],frozen['code_inventory'])
                if 'self_check: bool = False' not in (tree/'scripts/l2_pipeline/agents/collect.py').read_text():
                    raise ValueError('Legacy sealed collector has no write-free self-check; retain original package')
            if frozen['code_snapshot'] != contract['code_snapshot']:
                raise ValueError('Sealed review snapshot differs from candidate')
            return snapshots.call(Path(contract['snapshot_root']), frozen, 'collect',
                                  dict(job_path=str(Path(path).resolve()), allow_historical_draft=allow_historical_draft, self_check=self_check))
    historical=historical_draft_evidence(job) if allow_historical_draft else None
    if job.stage == "S0L":
        from .literature import collect_literature_job
        return collect_literature_job(job, self_check=self_check)
    original=public_input(Path(job.output_path));value=_preloaded if _preloaded is not None else json.loads(original.read_text())
    if not isinstance(value,dict): raise ValueError("Agent output must be an object")
    c=job.contract; normalized=value; normalization=None;quality=None;blindness=None;manual_evidence=None;evidence_report=None
    if job.stage=="S0":
        _reject_project_exposure(value)
        if c.get("jobs_kind")=="s0_purpose_direction_c83":
            from .purpose_direction import validate_output
            normalized=validate_output(job,value)
        else:
            from .purpose import validate_purpose_output
            normalized=validate_purpose_output(job,value)
            normalized.update(original_sha256=sha(original),hub_acceptance_required=True,
                              evidence_status="awaiting_hub_acceptance",execution_authorized=False)
    elif job.stage=="S1":
        if value.get("design_id")!=job.design_id or value.get("role")!=job.role or value.get("source_sha256")!=c["source_sha256"]: raise ValueError("Coding source/identity mismatch")
        blindness=validate_blindness(job,value)
        if job.role=="auditor":
            if value.get("selection_sha256")!=c["selection_sha256"]: raise ValueError("Audit selection changed")
            checks=value.get("checks")
            if not isinstance(checks,list) or not all(isinstance(r,dict) for r in checks): raise ValueError("Invalid audit checks")
            checks=_locator_checks(checks)
            value=dict(value, checks=checks)
            from ..stages.s1_treatment import evaluate_v3
            for r in checks:
                if r.get("status")=="known_error" and type(r.get("corrected_start_period")) is not int: raise ValueError("Known error lacks integer correction")
            if c.get('jobs_kind') == 'v8b_calendar_year':
                from ..v8b import validate_audit_clock
                clock = validate_audit_clock(job, value)
                result = evaluate_v3(c['sample'], checks)
                normalized = dict(value, audit=result, original_sha256=sha(original), **clock)
            elif c.get('source_type') == 'research_panel':
                from ..sources.research_panel.s1 import collect as collect_research
                paths = {Path(r['path']).name: Path(r['path']) for r in c['bound_inputs']}
                imported = json.loads(paths['PANEL_IMPORT.json'].read_text())
                selection = json.loads(paths['AUDIT_SELECTION.json'].read_text())
                answer, result = collect_research(Path(job.output_dir), imported, selection, agent_job=job)
                # Preserve the official evidence receipts and predicate/clock
                # confirmations required by the source-specific readiness gate.
                normalized = dict(answer, audit=result, original_sha256=sha(original))
            else:
                census = False
                if c.get("jobs_kind") == "autopilot_official_v3":
                    from ..autopilot_history import validate_job
                    census = validate_job(job)
                result=evaluate_v3(c["sample"],checks,remainder_census=census)
                normalized={"checks":checks,"source_sha256":c["source_sha256"],"selection_sha256":c["selection_sha256"],"audit":result,"original_sha256":sha(original)}
                if c.get("conflict_units"):
                    # REDESIGN R2: one row per prelocked A/B conflict state; they are outside the V3 error count.
                    from ..intake.official_history import resolve_conflicts
                    resolve_conflicts(c["conflict_units"],value.get("conflict_checks"))
                    normalized["conflict_checks"]=value["conflict_checks"]
        elif job.role=="third_reader":
            if value.get("reconciliation_sha256")!=c["reconciliation_sha256"]:
                raise ValueError("Third read reconciliation changed")
            checks=value.get("checks")
            if not isinstance(checks,list) or not all(isinstance(r,dict) for r in checks) or len(checks)!=len(c["units"]) or {r.get("unit") for r in checks}!=set(c["units"]):
                raise ValueError("Third read must match exact disputed units")
            checks=_locator_checks(checks)
            from ..stages.s1_treatment import reconcile
            tuples=[dict(r,locator=r.get("official_locator")) for r in checks]
            reconcile(tuples,tuples,c["units"])
            for r in checks:
                if r.get("resolution") not in {"A","B","other","unresolved"} or not _text(r.get("rationale")) or r.get("mechanism")!=c["mechanism"]:
                    raise ValueError("Third read lacks scope/official rationale")
                if (r["status"]=="unknown")!=(r["resolution"]=="unresolved") or (r["status"]=="unknown" and r.get("start_period") is not None):
                    raise ValueError("Third read unresolved status differs")
                if r["resolution"] in {"A","B"}:
                    conflict=next(g for g in c["conflicts"] if g["unit"]==r["unit"])
                    chosen=conflict[r["resolution"].lower()]
                    if any(r.get(k)!=chosen.get(k) for k in ("status","start_period","mechanism")):
                        raise ValueError("Third read chosen coder tuple differs")
            normalized={"checks":checks,"source_sha256":c["source_sha256"],"reconciliation_sha256":c["reconciliation_sha256"],"original_sha256":sha(original),"s1_ready":False}
        else:
            if c.get("evidence_standard")=="HUB_RULING_V9_HISTORY_EVIDENCE":
                from ..c59_queue import require_complete_history
                from ..c83_history import completed_value
                value = completed_value(job, value)
                require_complete_history(job,value)
                from ..manual_evidence import for_job
                value,manual_evidence=for_job(job,value)
            rows=value.get("records");universe=c["manual"]["universe"]
            if not isinstance(rows,list) or not all(isinstance(r,dict) for r in rows) or len(rows)!=len(universe) or {r.get("unit") for r in rows}!=set(universe): raise ValueError("Coder must include complete universe once")
            from ..stages.s1_treatment import reconcile
            reconcile(rows,rows,universe)
            for r in rows:
                if r.get("status")=="unknown" and (r.get("start_period") is not None or not _text(r.get("reason"))): raise ValueError("Unknown needs null period and reason")
                if c.get("evidence_standard")!="HUB_RULING_V9_HISTORY_EVIDENCE" and r.get("status")!="unknown" and r.get("mechanism")!=c["mechanism"]: raise ValueError("Coder mixed mechanisms")
            if c.get("evidence_standard")=="HUB_RULING_V9_HISTORY_EVIDENCE":
                from .history import validate_history_output, normalize_history_mechanisms
                validate_history_output(job,value)
                normalized,normalization=normalize_history_mechanisms(job,value)
                normalization.update(original_sha256=sha(original))
                # NAMED_PANEL Q3: a packet claimed under the evidence rule keeps a known row only with fetched-and-hashed,
                # quote-verified evidence; other known rows become unknown with the original retained.
                from .evidence_fetch import apply_evidence_rule
                normalized,evidence_report=apply_evidence_rule(job,normalized)
                rows=normalized["records"]
            quality=coder_quality(job,rows,original)
            if c.get("evidence_standard")!="HUB_RULING_V9_HISTORY_EVIDENCE" and c.get("source_pack") and quality["known_share"]>=.5 and not quality["all_unknowns_unit_specific"]:
                raise ValueError("Unknown requires a distinct unit-specific blocker")
            if quality["status"]=="INSUFFICIENT_WORK":
                rejection={"schema_version":"1.0","stage":job.stage,"design_id":job.design_id,
                           "role":job.role,"job_path":job.job_path,"job_sha256":job.job_sha256,
                           "original_path":str(original),"original_sha256":sha(original),
                           "status":"INSUFFICIENT_WORK","validated":False,
                           "execution_authorized":False,"quality_gate":quality}
                if not self_check:ensure_once(original.parent/"QUALITY_GATE.json",rejection)
                return rejection
    elif job.stage=="S5":
        if c.get("forecast_slot"):
            from ..forecast_slots import provenance
            normalized=dict(normalized,forecast_slot=provenance(job,value))
            if c.get("bundle_member") is True: normalized["single_author_bundle"]=True
        blindness=validate_blindness(job,value)
        if not isinstance(value.get("disclosures"),list) or not all(_text(x) for x in value["disclosures"]): raise ValueError("Forecast disclosures missing/invalid")
        template=c.get("forecast_template")
        if template:
            identity=("design_id","outcome_spec_id","spec_path","spec_sha256","spec_json_path","spec_json_sha256","methods_path","methods_sha256","reviewer_perspective","reviewer_identity","unit")
            if any(value.get(k)!=template.get(k) for k in identity): raise ValueError("Original forecast identity or specification changed")
            if value.get("status")!="FILLED_PRE_ESTIMATION" or value.get("outcome_associations_seen_attestation") is not False or value.get("freeze_manifest_sha256") is not None: raise ValueError("Forecast status/blindness/freeze invalid")
            _forecast_numbers(value.get("central_ATT"),value.get("interval_80"),.8)
            keys=("prob_positive","prob_negative","prob_near_zero","prob_diagnostics_pass")
            if any(not _finite(value.get(k)) or not 0<=value[k]<=1 for k in keys): raise ValueError("Forecast probability outside [0,1]")
            if abs(sum(value[k] for k in keys[:3])-1)>1e-8: raise ValueError("Direction probabilities must sum to one")
            if not _finite(value.get("near_zero_threshold")) or value["near_zero_threshold"]<0: raise ValueError("Invalid near-zero threshold")
            if value.get("direction") not in {"positive","negative","near_zero"}: raise ValueError("Invalid forecast direction")
            threshold=value["near_zero_threshold"];point=value["central_ATT"]
            expected="positive" if point>threshold else "negative" if point < -threshold else "near_zero"
            if value["direction"]!=expected: raise ValueError("Direction contradicts central forecast/threshold")
            for k in ("entered_at","mechanism","signature"):
                if not _text(value.get(k)): raise ValueError("Unfilled forecast field: "+k)
            if not isinstance(value.get("assumptions"),list) or not value["assumptions"] or not all(_text(x) for x in value["assumptions"]): raise ValueError("Forecast assumptions missing")
            normalization={"original_sha256":sha(original),"template_sha256":c["forecast_template_sha256"],"field_mapping":{"central_ATT":"point","interval_80":"interval","mechanism+assumptions":"rationale"},"original_design_id":value["design_id"],"original_spec_sha256":value["spec_sha256"],"pipeline_design_id":job.design_id,"pipeline_spec_sha256":c["spec_sha256"],"interval_level":.8,"scientific_values_changed":False,"disclosure":"Original template-bound forecast retained unchanged; normalized aliases and pipeline identity/spec bindings recorded for the pipeline lock."}
            normalized={"persona":job.role,"design_id":job.design_id,"spec_sha256":c["spec_sha256"],"point":point,"interval":value["interval_80"],"interval_level":.8,"rationale":value["mechanism"]+" Assumptions: "+"; ".join(value["assumptions"]),"disclosures":value["disclosures"]+[normalization["disclosure"]],"original_sha256":sha(original),"normalization":normalization,"blindness_attestation":value["blindness_attestation"]}
        else:
            if value.get("persona")!=job.role or value.get("design_id")!=job.design_id or value.get("spec_sha256")!=c["spec_sha256"]: raise ValueError("Forecast identity mismatch")
            _forecast_numbers(value.get("point"),value.get("interval"),value.get("interval_level"))
            if not _text(value.get("rationale")): raise ValueError("Forecast rationale missing")
        if blindness is not None:
            known=value.get("published_estimate_known",blindness["published_results_read"])
            if type(known) is not bool or known is not blindness["published_results_read"]:
                raise ValueError("Published estimate knowledge contradicts attestation")
            if c.get("blindness_scope")=="our_outcomes_and_estimates_v7" and "published_estimate_known" not in value:
                raise ValueError("Published estimate knowledge disclosure missing")
            normalized=dict(normalized,published_estimate_known=known,
                published_estimate_knowledge="published estimate known" if known else "published estimate not read",
                source_type="published_did_study",blindness_scope="our_outcomes_and_estimates_v7",
                v7_blindness_validation=blindness)
    else:
        if value.get("design_id")!=job.design_id or value.get("candidate_sha256")!=c["candidate_sha256"] or value.get("review_scope")!=c["review_scope"]: raise ValueError("Review candidate/scope mismatch")
        if type(value.get("complete_sweeps")) is not int or value["complete_sweeps"]!=1 or type(value.get("fix_cycles")) is not int or not 0<=value["fix_cycles"]<=2: raise ValueError("Review needs exactly one complete sweep/bounded fixes")
        if "expected_fix_cycles" in c and value["fix_cycles"]!=c["expected_fix_cycles"]: raise ValueError("Review fix cycle differs from registered attempt")
        att=value.get("independence_attestation");keys={"wrote_implementation","wrote_fixes","real_outcome_associations_read"}
        if not isinstance(att,dict) or set(att)!=keys or any(att[k] is not False for k in keys): raise ValueError("Reviewer independence unconfirmed")
        ids=set()
        for bucket in ("must","should"):
            if not isinstance(value.get(bucket),list): raise ValueError("Incomplete consolidated review")
            for finding in value[bucket]:
                if not isinstance(finding,dict) or any(not _text(finding.get(k)) for k in ("id","description","evidence","impact")): raise ValueError("Review finding lacks evidence")
                if finding["id"] in ids: raise ValueError("Duplicate review finding")
                ids.add(finding["id"])
                if bucket=="must" and finding["impact"] not in {"incorrect_registered_number","missing_registered_result"}: raise ValueError("MUST does not meet registered definition")
        if value.get("verdict")!=("STOP" if value["must"] else "PASS"): raise ValueError("Review verdict contradicts MUST list")
        if c["review_scope"]=="draft_readiness":
            normalized=dict(value,execution_authorized=False,missing_readiness_artifacts=c["missing_readiness_artifacts"],sealed_review_required=c.get("single_review") is not True)
            if historical is not None: normalized["historical_collection"]=historical
    if self_check:
        return dict(validated=True,self_check=True,stage=job.stage,design_id=job.design_id,role=job.role,original_sha256=sha(original) if _preloaded is None else None,execution_authorized=False,mechanism_issues=(normalization or {}).get('issues',[]))
    from ..manual_evidence import collection_directory
    receipt_dir=collection_directory(job)
    promote=public_input(Path(job.promote_path))
    if job.stage=="S1" and c.get("evidence_standard")=="HUB_RULING_V9_HISTORY_EVIDENCE":
        # Existing V9 jobs promote to their original. Preserve those bytes and
        # expose the normalized history as an explicitly receipted sidecar.
        promote=receipt_dir/"NORMALIZED_HISTORY.json"
    if promote!=original: ensure_once(promote,normalized)
    if normalization: ensure_once(receipt_dir/"NORMALIZATION.json",normalization)
    if manual_evidence:
        ensure_once(receipt_dir/"MANUAL_EVIDENCE.json",manual_evidence)
    if evidence_report:
        ensure_once(receipt_dir/"EVIDENCE_VERIFICATION.json",evidence_report)
    receipt={"schema_version":"1.0","stage":job.stage,"design_id":job.design_id,"role":job.role,"job_path":job.job_path,"job_sha256":job.job_sha256,"original_path":str(original),"original_sha256":sha(original),"promoted_path":str(promote),"promoted_sha256":sha(promote),"validated":True,"execution_authorized":False}
    if c.get("forecast_slot"): receipt["forecast_slot"]=normalized["forecast_slot"]
    if blindness is not None: receipt["v7_blindness_validation"]=blindness
    if manual_evidence:
        receipt["manual_evidence"]={"path":str(receipt_dir/"MANUAL_EVIDENCE.json"),"sha256":sha(receipt_dir/"MANUAL_EVIDENCE.json")}
    if evidence_report:
        receipt["evidence_verification"]={"path":str(receipt_dir/"EVIDENCE_VERIFICATION.json"),"sha256":sha(receipt_dir/"EVIDENCE_VERIFICATION.json"),
            "rows_downgraded":evidence_report["rows_downgraded"]}
    if quality is not None: receipt.update(status="VALIDATED",quality_gate=quality)
    if normalization and normalization.get("schema")=="v9_mechanism_normalization_fx10_v1":
        receipt["mechanism_issues"]=normalization["issues"]
        receipt["normalization_path"]=str(receipt_dir/"NORMALIZATION.json")
        receipt["normalization_sha256"]=sha(receipt_dir/"NORMALIZATION.json")
    if historical is not None: receipt["historical_collection"]=historical
    ensure_once(receipt_dir/"COLLECTION.json",receipt)
    if c.get('contract_replacement'):
        from .replacement import publish_receipt
        publish_receipt(job, receipt)
    if job.stage=="S5" and c.get("attempt_id") and not c.get('contract_replacement'):
        # The canonical roster lock must bind the successfully collected
        # original in its attempt directory rather than an invalid old output.
        if normalization: ensure_once(public_input(Path(c["canonical_normalization_path"])),normalization)
        ensure_once(public_input(Path(c["canonical_collection_path"])),receipt)
    return receipt


def collect_jobs(paths, *, allow_historical_draft=False):
    """One malformed or unavailable artifact cannot abort other designs."""
    receipts=[]
    for path in paths:
        try:
            receipts.append(collect_job(path,allow_historical_draft=allow_historical_draft))
        except Exception as exc:
            receipts.append({"job_path":str(path),"status":"COLLECTION_FAILED",
                             "validated":False,"execution_authorized":False,
                             "error":type(exc).__name__+": "+str(exc)})
    return receipts

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--job",required=True,type=Path,action="append")
    parser.add_argument("--allow-historical-draft",action="store_true",help="Collect completed S6 draft findings with historical code bindings; never grants execution")
    parser.add_argument("--self-check",action="store_true",help="Run the same validation without promotion or collection writes")
    args=parser.parse_args(argv)
    if args.self_check:
        receipts=[collect_job(p,allow_historical_draft=args.allow_historical_draft,self_check=True) for p in args.job]
        print(json.dumps(receipts));return int(any(not r["validated"] or r.get("mechanism_issues") for r in receipts))
    receipts=collect_jobs(args.job,allow_historical_draft=args.allow_historical_draft)
    print(json.dumps(receipts[0] if len(receipts)==1 else {"jobs":receipts},indent=2))
    return int(any(not receipt["validated"] for receipt in receipts))

if __name__=="__main__": raise SystemExit(main())
