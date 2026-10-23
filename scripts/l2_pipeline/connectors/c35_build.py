"""Build C35 outcome-only artifacts and append a separately versioned catalog."""
import copy,json
from .c35_core import BASE,REPO,write_once,source_record


def build():
    from . import c35_wonder,c35_fars,c35_public
    specs={}
    for builder in (c35_wonder.build,c35_fars.build,c35_public.build):
        fragment=builder()
        if specs.keys()&fragment.keys():raise ValueError('Duplicate C35 series')
        specs.update(fragment)
    old=REPO/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V6.json'
    data=copy.deepcopy(json.loads(old.read_text()))
    data.update(schema_version=7,catalog_id='first_order_outcomes_v7',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v7.register',
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v7.construct',
        acquisition_entrypoint='Offline only: python -B -m scripts.l2_pipeline.connectors.c35_build',
        inherited_catalog=source_record(old) if old.is_relative_to(REPO/'data') else dict(path=str(old.relative_to(REPO)),sha256=__import__('hashlib').sha256(old.read_bytes()).hexdigest()))
    data['series'].update(specs)
    for sid,s in data['series'].items():s['registered_connector']='outcome_v7_'+sid
    targets={
        'homicide_rate':['wonder_homicide_rate','fbi_ucr_homicide_rate'],
        'homicide_mortality':['wonder_homicide_rate'],
        'firearm_homicide_rate':['wonder_firearm_homicide_rate'],
        'voter_turnout':['turnout_vep_highest_office_rate','turnout_vep_total_ballots_rate'],
        'occupant_fatality_rate':['fars_motor_vehicle_occupant_fatality_rate'],
        'child_pedalcyclist_fatality_rate':['fars_pedalcyclist_fatality_rate_age5_14']}
    for t,sids in targets.items():data['targets'][t]=dict(series=sids,selection_authorized=True,purpose_status='pending_V6.2_S0')
    data['discipline'].update(outcome_association_calls=0,turnout_analysis_only=True,
        turnout_republication_prohibited=True,historical_raw_unchanged=True,cmf_subten_masked_in_derived=True)
    write_once(REPO/'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V7.json',data)
    write_once(BASE/'CATALOG_FRAGMENT.json',dict(schema_version=7,series=specs,targets=targets,outcome_association_calls=0))
    print(json.dumps({k:s['observed_coverage'] for k,s in specs.items()},indent=2))
    return data

if __name__=='__main__':build()
