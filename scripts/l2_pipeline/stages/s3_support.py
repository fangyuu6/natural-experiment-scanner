from __future__ import annotations
import csv, json
from pathlib import Path
from ..cards import variant_card
from ..variants import registered_cards
from ..contracts import Disposition, StageContext, StageResult
from ..support import support_check, declared_annual_periods, registered_support_gap_rule, minimum_post_periods
from ..engine.panels import metadata_eligible_keys
from ..storage import artifact, ensure_once, sha

def variant_treatment(records: list[dict], card: dict) -> list[dict]:
    treatment=card["treatment"]
    selected=treatment.get("universe",[r["unit"] for r in records])
    shift=treatment.get("start_shift",0)
    if type(shift) is not int: raise ValueError("Timing shift must be integer analysis periods")
    if treatment.get("records") is not None: records=treatment["records"]
    from ..prewindow import forbid
    forbid(records, "S3/RI/variant treatment (T192)")
    excluded=set(treatment.get("excluded_units",[]))
    kept=[dict(r,**({"operative_period":r.get("operative_period",r["start_period"])} if shift and r.get("start_period") is not None else {}),start_period=r["start_period"]+shift if r.get("start_period") is not None else None) for r in records if r["unit"] in selected and r["unit"] not in excluded]
    truncate=treatment.get("cohort_truncate_after")
    if truncate is not None:
        # R-S3C1 run (ii) (fixed composition): the listed units end their follow-up at the interruption cutoff (existing censor mechanism).
        if not isinstance(truncate,dict) or not truncate or any(type(y) is not int for y in truncate.values()) or set(truncate)-{r["unit"] for r in kept if r.get("status")=="treated"}:
            raise ValueError("cohort_truncate_after must map retained treated units to integer periods")
        kept=[dict(r,censor_after_period=truncate[r["unit"]]) if r["unit"] in truncate else r for r in kept]
    overrides=treatment.get("start_overrides")
    if overrides is None: return kept
    # C57 authorised re-dating sensitivity (HUB_RULING_20261020_C57_SENSITIVITY.md): named treated units get another start period, the
    # transition-year drop follows the new date (no operative_period is kept). Fail closed on anything else.
    if not isinstance(overrides,dict) or not overrides or shift: raise ValueError("start_overrides must be a non-empty unit->year map and cannot combine with start_shift")
    by_unit={r["unit"]:r for r in kept}
    for unit,year in overrides.items():
        if type(year) is not int or unit not in by_unit or by_unit[unit].get("status")!="treated" or by_unit[unit].get("start_period") is None:
            raise ValueError("start_overrides must re-date retained treated units to integer periods: "+str(unit))
    return [({k:v for k,v in r.items() if k!="operative_period"}|{"start_period":overrides[r["unit"]]}) if r["unit"] in overrides else r for r in kept]

def run(ctx: StageContext) -> StageResult:
    from .. import cohort_censor_rule
    cohort_censor_rule.check_card(dict(ctx.card.payload))   # R-S3C1 whitelist and floors; no-op without the block
    if ctx.card.payload["analysis"].get("support_adapter")=="annual_full_year_v1": return run_full_year(ctx)
    if ctx.card.track!="DiD": return StageResult("S3",ctx.card.design_id,Disposition.PARKED,"Track-specific support adapter pending","Implement RDD/RCT metadata checks")
    treatment=ctx.read("TREATMENT.json")["records"]
    cards=registered_cards(dict(ctx.card.payload))
    records=[]
    for variant in ctx.read("PANELS.json")["variants"]:
        if variant.get('state')=='UNSUPPORTED' and variant['variant'].startswith('secondary_'):
            records.append(dict(variant=variant['variant'],passed=False,support_pass=False,state='UNSUPPORTED',disposition='stopped',reason=variant['reason']))
            continue
        card=cards[variant["variant"]];cfg=card["analysis"]
        if variant['variant'].startswith('secondary_'):
            reason=None
            if card['outcome'].get('frequency','annual') not in {'annual','biennial'}: reason='No certified secondary nonannual support adapter'
            if reason:
                records.append(dict(variant=variant['variant'],passed=False,support_pass=False,state='UNSUPPORTED',disposition='stopped',reason=reason))
                continue
        if card["outcome"].get("frequency","annual") not in {"annual","biennial"}: return StageResult("S3",ctx.card.design_id,Disposition.PARKED,"Nonannual normalized support adapter pending","Register biennial/monthly calendar mapping and endpoint support")
        if cfg.get("controls","never_treated") not in {"never_treated","not_yet_treated"}: return StageResult("S3",ctx.card.design_id,Disposition.PARKED,"Future-control support adapter pending","Integrate clean-eligible nonannual/future-control engine geometry")
        if not {"min_pre_periods","min_controls","min_cohorts"}<=set(cfg): return StageResult("S3",ctx.card.design_id,Disposition.PARKED,"Support thresholds not registered","Set design-specific thresholds")
        if any(type(cfg[k]) is not int or cfg[k]<1 for k in ("min_pre_periods","min_controls","min_cohorts")): raise ValueError("Invalid support thresholds")
        directory=ctx.design_dir / "panels" / variant["variant"]
        if sha(directory/"metadata.csv")!=variant["metadata_sha256"] or sha(directory/"panel.csv")!=variant["panel_sha256"]: raise ValueError("Outcome/metadata binding changed")
        with (directory/"metadata.csv").open(newline="") as handle: rows=list(csv.DictReader(handle))
        keys=metadata_eligible_keys(directory/"metadata.csv")
        if card['outcome'].get('frequency')=='biennial':
            from ..biennial import support,ADAPTER,adapter
            if cfg.get('support_adapter',ADAPTER)!=adapter(card['outcome']):raise ValueError('Biennial support adapter must be registered')
            summary=support(keys,variant_treatment(treatment,card),cfg,card['outcome'],card.get('transition_year_drop',False))
        else:
            summary=support_check(keys,variant_treatment(treatment,card),cfg,
                period_years=declared_annual_periods(card['outcome']),transition_year_drop=card.get('transition_year_drop',False))
        records.append({'variant':variant['variant'],**summary})
    primary=next(r for r in records if r["variant"]=="primary")
    extra={}
    cells=cohort_censor_rule.cells_payload(ctx.card.design_id,sha(ctx.card.path),primary) if "cohort_censor_rule" in ctx.card.payload["analysis"] else None
    if cells is not None:
        # R-S3C1: the cell list is written and hashed here, before S4/S5 and any estimate; the card bytes are named inside it.
        extra["cohort_cells_sha256"]=sha(ensure_once(ctx.design_dir/cohort_censor_rule.CELLS_FILE,cells))
    path=ensure_once(ctx.design_dir / "SUPPORT.json",{"variants":records,"primary_passed":primary["passed"],"outcome_values_parsed":0,**extra})
    return StageResult("S3",ctx.card.design_id,Disposition(primary["disposition"]),primary["reason"],None if primary["passed"] else "New prospective version resolving support",(artifact(path,"support"),),{"outcome_values_parsed":0,"support_gap_excluded_units":primary.get("support_gap_excluded_units",[])})


def run_full_year(ctx: StageContext) -> StageResult:
    """Independently regenerate every declared opaque panel mask from metadata."""
    from ..full_year import construct_full_year
    from ..storage import public_input
    cfg=ctx.card.payload["analysis"];accepted=ctx.read("TREATMENT.json")
    if any(type(cfg.get(k)) is not int or cfg[k]!=v for k,v in {"min_pre_periods":3,"min_controls":5,"min_cohorts":2,"min_treated":6}.items()): raise ValueError("Annual full-year v1 thresholds differ; register a new support adapter version")
    declaration=ctx.card.payload["treatment"]["full_year_declaration"]
    if registered_support_gap_rule(cfg)!=registered_support_gap_rule(declaration): raise ValueError("Annual full-year support gap rule differs from registered declaration")
    if accepted.get("full_year_declaration")!=declaration: raise ValueError("Accepted annual declaration differs from card")
    source=public_input(ctx.card.path.parent/cfg["support_metadata_path"])
    if sha(source)!=cfg["support_metadata_sha256"]: raise ValueError("Annual source metadata changed")
    with source.open(newline="") as handle: metadata=list(csv.DictReader(handle))
    cards=registered_cards(dict(ctx.card.payload))
    records=[]
    for panel in ctx.read("PANELS.json")["variants"]:
        if panel.get('state')=='UNSUPPORTED' and panel['variant'].startswith('secondary_'):
            records.append(dict(variant=panel['variant'],passed=False,support_pass=False,state='UNSUPPORTED',disposition='stopped',reason=panel['reason'],panel_sha256=None,metadata_sha256=None))
            continue
        name=panel["variant"];card=cards[name]
        if name.startswith('secondary_'):
            records.append(dict(variant=name,passed=False,support_pass=False,state='UNSUPPORTED',disposition='stopped',
                reason='No certified secondary annual full-year support adapter; primary metadata cannot confer secondary support',
                panel_sha256=panel['panel_sha256'],metadata_sha256=panel['metadata_sha256']))
            continue
        variant_path=ctx.design_dir/'panels'/name/'VARIANT_CARD.json'
        if variant_path.exists():
            stage_card=json.loads(variant_path.read_text())
            if stage_card.get('comparison_group'):
                card=stage_card
        declaration=dict(card["treatment"]["full_year_declaration"])
        declaration["min_post_periods"]=minimum_post_periods(card["analysis"])
        if card.get('transition_year_drop',False): declaration['transition_year_drop']=True
        summary,required=construct_full_year(declaration,card["analysis"]["full_year_variant"],metadata,accepted["source_checks"])
        if name.startswith('secondary_'): summary['variant']=name
        directory=ctx.design_dir/"panels"/name
        if sha(directory/"panel.csv")!=panel["panel_sha256"] or sha(directory/"metadata.csv")!=panel["metadata_sha256"]: raise ValueError("Annual panel binding changed")
        with (directory/"metadata.csv").open(newline="") as handle: rows=list(csv.DictReader(handle))
        keys={(r["unit"],int(r["year"])) for r in rows}
        if keys!=required or len(keys)!=len(rows): raise ValueError("Annual exact-mask panel inventory differs from registered constructor")
        summary.update(panel_path=str(directory/"panel.csv"),panel_sha256=panel["panel_sha256"],metadata_path=str(directory/"metadata.csv"),metadata_sha256=panel["metadata_sha256"],inventory_test_pass=True,state="SUPPORTED" if summary["support_pass"] else "UNSUPPORTED",passed=summary["support_pass"],disposition="ready" if summary["support_pass"] else "stopped")
        records.append(summary)
    primary=next(r for r in records if r["variant"]=="primary")
    path=ensure_once(ctx.design_dir/"SUPPORT.json",{"variants":records,"primary_passed":primary["support_pass"],"outcome_values_parsed":0,"adapter":"annual_full_year_v1"})
    return StageResult("S3",ctx.card.design_id,Disposition.READY if primary["support_pass"] else Disposition.STOPPED,"Annual full-year support generated on exact variant masks",artifacts=(artifact(path,"support"),),metrics={"outcome_values_parsed":0,"unsupported_variants":sum(not r["support_pass"] for r in records)})
