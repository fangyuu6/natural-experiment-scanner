"""Connector dispatch constructs all registered panels via one shared path."""
from __future__ import annotations
import json
from pathlib import Path
from ..cards import variant_card
from ..variants import registered_cards
from ..contracts import Disposition, StageContext, StageResult
from ..storage import artifact, ensure_once, public_input, sha

def run(ctx: StageContext) -> StageResult:
    from ..panel_builder import build_panel
    from ..connectors import ConnectorNotReady
    if ctx.card.unit!="state": return StageResult("S2",ctx.card.design_id,Disposition.PARKED,"Non-state panel adapter pending","Implement card unit adapter")
    from ..v11_v12 import comparison_group, register_card
    from .s3_support import variant_treatment
    treatment=ctx.read('TREATMENT.json')['records']
    value=dict(ctx.card.payload)
    if value['track']=='DiD' and not value.get('automation',{}).get('archival_s1_import'):
        value=register_card(value,comparison_group(variant_treatment(treatment,value)))
    variants=list(registered_cards(value).items())
    built=[]; artifacts=[]
    try:
        for name, card in variants:
            directory=ctx.design_dir / "panels" / name
            ensure_once(directory/"VARIANT_CARD.json",card)
            constructed=directory/"CONSTRUCTION.json"
            if constructed.exists():
                record=json.loads(constructed.read_text())
                for field in ("panel","metadata"):
                    path=Path(record[field+"_path"])
                    if path.resolve().parent!=directory.resolve() or sha(path)!=record[field+"_sha256"]: raise ValueError("Constructed panel binding changed")
                for source in record["source_files"]:
                    if sha(public_input(Path(source["path"])))!=source["sha256"]: raise ValueError("Panel source bytes changed")
            else:
                try:
                    if card.get('_secondary_unsupported_reason'): raise ConnectorNotReady(card['_secondary_unsupported_reason'])
                    record=build_panel(card,ctx.card.path.parent,directory)
                except ConnectorNotReady as exc:
                    if not name.startswith('secondary_'): raise
                    built.append(dict(variant=name,state='UNSUPPORTED',reason=str(exc)))
                    artifacts.append(artifact(directory/'VARIANT_CARD.json','variant_card'))
                    continue
            built.append({"variant":name,**record})
            artifacts.extend(artifact(directory/file,role) for file,role in (("VARIANT_CARD.json","variant_card"),("panel.csv","outcome"),("metadata.csv","support_metadata"),("CONSTRUCTION.json","construction")))
    except ConnectorNotReady as exc:
        return StageResult("S2",ctx.card.design_id,Disposition.PARKED,str(exc),"Implement connector or supply verified public local files",tuple(artifacts))
    path=ensure_once(ctx.design_dir / "PANELS.json",{"variants":built})
    artifacts.append(artifact(path,"panel_inventory"))
    return StageResult("S2",ctx.card.design_id,Disposition.READY,"Primary and sensitivities constructed through shared path",artifacts=tuple(artifacts),metrics={"panel_count":len(built)})
