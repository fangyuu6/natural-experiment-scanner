"""DiD template is explicit; RDD/RCT cannot silently inherit DiD thresholds."""
from __future__ import annotations
from ..contracts import Disposition, StageContext, StageResult
from ..storage import artifact, sha, ensure_once
from ..first_order import first_order_gate, require_first_order
from ..variants import registered_names

def generate_spec(card: dict, support: dict, *, hygiene=False) -> dict:
    require_first_order(card)
    if card["track"] != "DiD": return {"track":card["track"],"status":"stub","required_adapter":"local_linear_bias_corrected" if card["track"]=="RDD" else "ITT_assignment_randomization"}
    required={"estimand","clock","controls","weights","inference","diagnostics","stop_rules","min_pre_periods","min_controls","min_cohorts"}
    if not required<=set(card["analysis"]): raise ValueError("Analysis template incomplete: "+str(sorted(required-set(card["analysis"]))))
    if support.get("primary_passed") is not True: raise ValueError("Primary metadata support failed")
    variants={r["variant"] for r in support["variants"]}
    expected=set(registered_names(card));base={"primary"}|{s["name"] for s in card["sensitivities"]}
    if variants==base and expected!=base:
        # Legacy draft generation may precede the additive secondary preparation.
        # Keep every missing row explicit; the final loader still requires its
        # frozen panel/support artifacts before numerical execution.
        support=dict(support,variants=[*support['variants'],*[dict(variant=name,passed=False,state='UNSUPPORTED',
            reason='Registered secondary metadata support not prepared') for name in sorted(expected-base)]])
    elif variants!=expected: raise ValueError("Registered sensitivity support inventory incomplete")
    from ..v11_v12 import register_card
    primary=next(r for r in support['variants'] if r['variant']=='primary')
    if primary.get('comparison_group'):
        card=register_card(card,primary['comparison_group'])
    result = {"schema":"l2_spec_v1","design_id":card["design_id"],"version":card["version"],"track":card["track"],
            "mechanism":card["mechanism"],"population":card["population"],"outcome":card["outcome"],"analysis":card["analysis"],"treatment":card["treatment"],"sensitivities":card["sensitivities"],
            "first_order":card["first_order"],"secondary_outcomes":card["first_order"]["secondary_outcomes"],
            "interval_path":card["analysis"].get("interval_path",["Webb","CT_complete_control_path","point_plus_RI_if_uncertified"]),"ri":{"fixed_draws":True,"redraw":False,"minimum_feasible_fraction":.9},
            "sensitivity_rescues_primary":False,"support":support}
    if 'comparison_group' in card: result['comparison_group']=card['comparison_group']
    if 'transition_year_drop' in card:
        result['transition_year_drop']=card['transition_year_drop']
    from ..c64 import contract
    result["l3_export_v2"] = contract(card)
    result['c76b2']={'class_table':2,'export_references':1}
    if hygiene:
        from ..hygiene import plan
        result['c79_hygiene'],result['sensitivities']=plan(card,support,smo_rule=True)
    return result

def run(ctx: StageContext) -> StageResult:
    gate = first_order_gate(ctx.card.payload)
    gate_path = ensure_once(ctx.design_dir / "FIRST_ORDER_GATE_S4.json", gate)
    if not gate["passed"]:
        return StageResult("S4",ctx.card.design_id,Disposition.PARKED,gate["reason"],
                           "Register the direct target or a justified measurable proxy in a fresh card version",
                           artifacts=(artifact(gate_path,"first_order_gate"),),metrics={"first_order_issues":gate["issues"],"outcome_values_read":0})
    if ctx.card.track!="DiD": return StageResult("S4",ctx.card.design_id,Disposition.PARKED,"RDD/RCT specification template stub","Implement and independently certify dedicated estimator")
    if (ctx.design_dir/'SPEC.json').exists():
        return StageResult("S4",ctx.card.design_id,Disposition.READY,"Existing specification retained",artifacts=(artifact(ctx.design_dir/'SPEC.json',"spec"),))
    spec=generate_spec(dict(ctx.card.payload),ctx.read("SUPPORT.json"),hygiene=True)
    from ..hygiene import prepare_support
    prepared_card=dict(ctx.card.payload,analysis=spec['analysis'],treatment=spec['treatment'])
    support,panels=prepare_support(prepared_card,ctx.read('SUPPORT.json'),
        spec['c79_hygiene'],spec['sensitivities'],ctx.read('TREATMENT.json'),ctx.design_dir,ctx.card.path.parent)
    spec['support']=support
    ensure_once(ctx.design_dir/'SUPPORT_C79.json',support)
    ensure_once(ctx.design_dir/'PANELS_C79.json',panels)
    spec["card_sha256"]=sha(ctx.card.path)
    path=ensure_once(ctx.design_dir / "SPEC.json",spec)
    artifacts=[artifact(path,"spec"),artifact(gate_path,"first_order_gate")]
    if ctx.card.payload["analysis"].get("support_adapter")=="annual_full_year_v1":
        draft=ctx.card.payload["automation"]["registration_identity"]
        draft=dict(draft,specification=dict(ctx.card.payload["analysis"]["registration"]))
        draft_path=ensure_once(ctx.design_dir/"SPEC_DRAFT.json",draft)
        inventory=ensure_once(ctx.design_dir/"SENSITIVITY_MANIFEST.json",{"status":"DRAFT_UNSIGNED","variants":[{"variant":r["variant"],"state":r["state"],"panel_sha256":r["panel_sha256"],"metadata_sha256":r["metadata_sha256"]} for r in spec["support"]["variants"]],"primary_path":"annual_full_year_v1 -> opaque declared mask for every variant","no_freeze_or_estimation":True})
        artifacts.extend([artifact(draft_path,"registration_draft"),artifact(inventory,"sensitivity_inventory")])
    return StageResult("S4",ctx.card.design_id,Disposition.READY,"DiD template and sensitivity inventory generated",artifacts=tuple(artifacts))
