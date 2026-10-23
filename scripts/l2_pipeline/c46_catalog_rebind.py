"""Explicit revision of C46's two unsealed V8 cards; old versions retained."""
from copy import deepcopy
from scripts.l2_pipeline import c46
from scripts.l2_pipeline.autopilot import Autopilot,read
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.cards import validate_card
from scripts.l2_pipeline.connectors import outcome_v8
p=Autopilot();data=outcome_v8.catalog(verify_sources=False);prepared=[]
for r in read(p.registry/'ACTIVE_DESIGNS.json')['designs']:
    did=r['design_id']
    if did not in c46.TARGETS or c46.TARGETS[did]=='park':continue
    old=read(r['card']);assert old['version']=='v2';new=deepcopy(old);new['version']='v3';sid=new['outcome']['series_id']
    support=c46.support_sidecar(sid,data['series'][sid],revision='v3')
    new['outcome'].update(catalog_sha256=sha(outcome_v8.CATALOG_PATH),support_path=str(support.relative_to(c46.ROOT)),support_sha256=sha(support))
    if validate_card(new):raise ValueError(validate_card(new))
    path=ensure_once(c46.OUT/'cards/v3'/(did+'.json'),new)
    ruling=deepcopy(read(c46.OUT/'rulings'/(did+'.json')))
    ruling.update(prior_card_sha256=sha(r['card']),card_sha256=sha(path),reason=ruling['reason']+' V8 metadata review corrections: BODY_TYP98 and incomplete FBI2025; same outcome series/rates, no estimation.')
    rp=ensure_once(c46.OUT/'rulings/v3'/(did+'.json'),ruling)
    if not p.stop_record(r) and not (p.case_dir(r)/'PARKED.json').exists():p.stop(r,'catalog','C46 unsealed catalog metadata correction requires new card binding')
    prepared.append(dict(design_id=did,card=str(path),ruling=str(rp)))
ensure_once(c46.OUT/'PREPARED_REVIEW_RULINGS.json',dict(designs=prepared,old_versions_preserved=True,dispatches=0,outcome_values_read=0))
print(prepared)
