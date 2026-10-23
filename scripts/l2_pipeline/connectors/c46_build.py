"""C46 additive V8 offline build; old catalog and design bindings unchanged."""
import copy,json
from .c46_core import BASE,REPO,write_once
from .common import sha


def build():
    from . import c46_public,c46_acs_ipeds,c46_fars
    specs={}
    for builder in (c46_public.opioid,c46_public.brfss,c46_acs_ipeds.acs,c46_acs_ipeds.ipeds,c46_public.hate_crime,c46_fars.build):
        fragment=builder()
        if specs.keys()&fragment.keys():raise ValueError('Duplicate C46 series')
        specs.update(fragment)
        print('Built '+', '.join(fragment),flush=True)
    return assemble(specs)


def assemble(specs):
    old=REPO/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V7.json';data=copy.deepcopy(json.loads(old.read_text()))
    data.update(schema_version=8,catalog_id='first_order_outcomes_v8',registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v8.register',registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v8.construct',
        acquisition_entrypoint='Offline only: python -B -m scripts.l2_pipeline.connectors.c46_build',inherited_catalog=dict(path=str(old.relative_to(REPO)),sha256=sha(old)))
    data['series'].update(specs)
    for sid,s in data['series'].items():s['registered_connector']='outcome_v8_'+sid
    for sid in specs:data['targets'][sid]=dict(series=[sid],selection_authorized=True,purpose_status='pending_V6.2_S0',legal_certification_granted=False)
    data['targets'].update(opioid_involved_overdose_mortality=dict(series=['wonder_opioid_overdose_deaths'],selection_authorized=True,purpose_status='HUB_RULINGS_C46_2'),child_firearm_mortality_all_intents=dict(series=['wonder_firearm_deaths'],selection_authorized=True,purpose_status='HUB_RULINGS_C46_3'))
    data['discipline'].update(outcome_association_calls=0,policy_outcome_association_calls=0,historical_raw_unchanged=True,suppressed_cells_never_imputed=True)
    write_once(REPO/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V8.json',data)
    write_once(BASE/'CATALOG_FRAGMENT.json',dict(schema_version=8,series=specs,skipped_sources=[],new_downloads=[],outcome_association_calls=0))
    inventory={i['path']:i for s in specs.values() for i in json.loads((REPO/s['artifact']['path']).read_text())['inventory']}
    write_once(BASE/'SOURCE_INVENTORY.json',dict(files=list(inventory.values()),new_downloads=[],source_license_status='Inherited receipt claims; unverified where receipt says unverified',outcome_association_calls=0))
    return data

if __name__=='__main__':build()
