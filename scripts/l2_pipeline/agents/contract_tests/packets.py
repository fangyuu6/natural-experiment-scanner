"""Synthetic production packets for offline contract checks."""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict, replace
import json

from ...contracts import DesignCard, StageContext
from ...storage import sha, write_once
from ..jobs import emit_job

REPO = Path(__file__).resolve().parents[4]


def _context(root, kind):
    source = write_once(root / "source.json", {"fixture": "Invented answer-free complete source"})
    values = {"schema_version": "1.0", "design_id": "contract-fixture", "version": "v1",
              "policy_family": "invented_employment", "mechanism": "one", "track": "DiD",
              "unit": "state", "outcome_family": "employment", "analysis": {},
              "outcome": {"years": [2000, 2007]}, "population": {"description": "Invented adults"},
              "first_order": {"target_outcome": "employment_share"},
              "sources": [{"source_id": "fixture", "path": source.name, "sha256": sha(source),
                           "complete_list": True, "coverage_end": "2007-12-31"}],
              "treatment": {"universe": [f"U{i}" for i in range(10)], "seed": 42,
                            "complete_list_source_id": "fixture", "predicate": "invented",
                            "coverage_end": "2007-12-31", "historical_convention": "cumulative"}}
    if kind.endswith("_v7"): values["treatment"]["source_type"] = "published_did_study"
    path = write_once(root / "card.json", values)
    ctx = StageContext(DesignCard.from_mapping(values, path), root / "work", REPO)
    write_once(ctx.design_dir / "SPEC.json", {"fixture": "Invented specification"})
    write_once(ctx.design_dir / "SUPPORT.json", {"fixture": "Invented metadata geometry"})
    return ctx


def _rows(ctx):
    return [{"unit": unit, "status": "treated", "start_period": 2002,
             "mechanism": "one", "locator": "Invented section 1"}
            for unit in ctx.card.payload["treatment"]["universe"]]


def _audit_inputs(ctx):
    from ...stages.s1_treatment import audit_sample
    rows = _rows(ctx)
    paths = [write_once(ctx.design_dir / "agents" / role / "submission.json", {"records": rows})
             for role in ("A", "B")]
    write_once(ctx.design_dir / "CODING_LOCK.json", {"files": {str(path): sha(path) for path in paths}})
    write_once(ctx.design_dir / "AUDIT_SELECTION.json", {"seed": 42, "sample": audit_sample(rows, 42),
                                                        "source_sha256": sha(ctx.card.path.parent / "source.json")})


def _followup(root, ctx, role):
    from ...source_packs import build_source_pack
    from ...stages.s1_reconciliation import emit_followup
    from ...stages.s1_treatment import audit_sample
    candidate = {"source_id": "fixture", "url": "https://example.invalid/fixture", "license": "Invented fixture",
                 "as_of": "2007-12-31", "coverage_end": "2007-12-31", "predicate": "invented",
                 "universe": ctx.card.payload["treatment"]["universe"], "history_type": "cumulative_ever",
                 "complete_list": True, "verification": {"kind": "text_anchors", "anchors": ["invented anchor"],
                     "locator": "Table 1", "date_basis": "Invented date", "scope_basis": "Invented universe"}}
    build_source_pack(ctx.card, repo=REPO, pack_dir=root / "pack", candidates=[candidate],
                      fetcher=lambda url: (("invented anchor " + "invented public fixture " * 30).encode(), url, "text/plain"))
    pack = root / "pack/manifest.json"
    rows = _rows(ctx)
    originals = [write_once(root / role / "submission.json", {"records": rows}) for role in ("A", "B")]
    lock = write_once(ctx.design_dir / "CODING_LOCK.json", {"files": {str(path): sha(path) for path in originals}})
    conflict = {"kind": "known_conflict", "unit": rows[0]["unit"], "a": rows[0], "b": dict(rows[0], start_period=2003)}
    report = write_once(ctx.design_dir / "RECONCILIATION.json", {"design_id": ctx.card.design_id,
        "window": {"feasible": False}, "historical_comparison": {"accepted": rows, "gaps": [conflict]}})
    selection = write_once(ctx.design_dir / "AUDIT_SELECTION.json", {"sample": audit_sample(rows, 42),
        "treated_universe": sorted(row["unit"] for row in rows), "source_sha256": sha(pack),
        "reconciliation_sha256": sha(report)})
    return emit_followup(ctx, role, root / "jobs", report, lock, pack, selection if role == "auditor" else None)


def _forecast_template(root, ctx):
    bound = {}
    for field in ("spec", "spec_json", "methods"):
        path = write_once(root / (field + ".json"), {"fixture": "Invented " + field})
        bound[field + "_path"] = str(path)
        bound[field + "_sha256"] = sha(path)
    blank = {"design_id": "invented-original", "outcome_spec_id": "invented-primary", **bound,
             "reviewer_perspective": "Card", "reviewer_identity": "Internal fixture", "unit": "percentage points",
             "status": "BLANK_PRE_ESTIMATION_TEMPLATE", "freeze_manifest_sha256": None}
    template = write_once(root / "blank.json", blank)
    values = dict(ctx.card.payload, automation={"prediction_templates": {"Card": str(template)}})
    return StageContext(DesignCard.from_mapping(values, ctx.card.path), ctx.workspace, ctx.repo)


def emit_fixture_job(root, kind):
    if kind == "s0l_literature":
        from ..literature import emit_literature_jobs
        registry = write_once(root / "registry.json", {"studies": [{"study_id": "contract-fixture", "title": "Invented paper"}]})
        return emit_literature_jobs(registry, repo=REPO, jobs_root=root / "jobs")[0]
    ctx = _context(root, kind)
    if kind=="s0_direction":
        from ..purpose_direction import emit
        write_once(ctx.design_dir/"PURPOSE_VERIFICATION.json",dict(citations=[dict(source_ref="https://example.invalid/statute",locator="section 1",source_type="statute",supports=["target_outcome"])]))
        return emit(ctx,"purpose_direction_A",root/"jobs")
    kwargs = {"jobs_root": root / "jobs"}
    if kind == "s1_official_history_v9":
        from ..history import emit_history_job
        from ..jobs import _packet, _write_text_once
        universe = [f"U{i}" for i in range(50)]
        values = dict(ctx.card.payload, treatment=dict(ctx.card.payload["treatment"], universe=universe))
        ctx = StageContext(DesignCard.from_mapping(values, ctx.card.path), ctx.workspace, ctx.repo)
        old = emit_job(ctx, "S1", "A", jobs_root=root / "generic")
        plan = write_once(root / "planning/COLLECTION_PLAN.json", {"design_id":ctx.card.design_id,
                          "universe":universe, "navigation_candidates":[], "type":"answer_free_official_collection_plan"})
        manifest = write_once(root / "planning/MANIFEST.json", {"schema_version":"1.0", "stage":"S1_SOURCE_PACK",
            "status":"PARKED_SOURCE", "complete_list_complete":False, "historical_coverage_complete":False,
            "complete_list_source_ids":[], "sources":[], "downloads":[]})
        c = dict(old.contract, jobs_kind="official_history_collection", source_sha256=sha(manifest),
                 history_required_units=universe, source_pack={"manifest_path":str(manifest),
                 "manifest_sha256":sha(manifest), "complete_list_complete":False, "historical_coverage_complete":False})
        inputs = [_packet(plan, "Invented answer-free planning fixture"), _packet(manifest, "Invented incomplete pack")]
        old = replace(old, job_path=str(root / "previous/A.md"), contract=c,
                      inputs=tuple({k:v for k,v in i.items() if k!="content"} for i in inputs))
        text = Path(old.template_path).read_text() + "\n## Execution metadata binding (data)\n```json\n"
        text += json.dumps({k:v for k,v in asdict(old).items() if k!="job_sha256"}) + "\n```\n"
        _write_text_once(Path(old.job_path), text)
        old = replace(old, job_sha256=sha(Path(old.job_path)))
        write_once(Path(old.job_path).with_suffix(".job.json"), asdict(old))
        return emit_history_job(old.job_path, jobs_root=root / "jobs", attempt_id="v9_fixture")
    if kind == "s0_purpose": return emit_job(ctx, "S0", "purpose_verifier", **kwargs)
    if kind.startswith("s1_coder"): return emit_job(ctx, "S1", "A", **kwargs)
    if kind in {"s1_auditor", "s1_auditor_v7"}:
        _audit_inputs(ctx)
        return emit_job(ctx, "S1", "auditor", **kwargs)
    if kind in {"s1_followup_auditor", "s1_third_read"}:
        return _followup(root, ctx, "auditor" if kind == "s1_followup_auditor" else "third_reader")
    if kind.startswith("s5_"):
        if kind.startswith("s5_template"): ctx = _forecast_template(root, ctx)
        return emit_job(ctx, "S5", "Card", **kwargs)
    if kind == "s6_sealed_reviewer": write_once(ctx.design_dir / "FREEZE_FINAL.json", {"fixture": "Invented sealed candidate"})
    if kind.startswith("s6_"): return emit_job(ctx, "S6", "reviewer", allow_draft=True, **kwargs)
    raise ValueError("Unsupported fixture job type")
