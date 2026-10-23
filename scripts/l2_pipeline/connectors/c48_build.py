"""Build additive V9 and construct/population adjunct from pinned local files."""
from copy import deepcopy
from pathlib import Path
import json
from . import outcome_v8,c48_fars
from .c46_core import REPO
from .wonder import write_once
from ..storage import sha
CATALOG=Path(__file__).with_name('OUTCOME_CATALOG_V9.json')
POPULATIONS=Path(__file__).with_name('OUTCOME_POPULATIONS_V9.json')


def assemble():
    catalog=deepcopy(outcome_v8.catalog(verify_sources=False))
    catalog.update(schema_version=9,catalog_id='first_order_outcomes_v9',
        inherited_catalog=dict(path=str(outcome_v8.CATALOG_PATH.relative_to(REPO)),sha256=sha(outcome_v8.CATALOG_PATH)),
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v9.construct',
        registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v9.register',
        acquisition_entrypoint='scripts.l2_pipeline.connectors.c48_build.assemble')
    for sid,spec in catalog['series'].items():spec['registered_connector']='outcome_v9_'+sid
    fragment=c48_fars.BASE/'CATALOG_FRAGMENT.json'
    if not fragment.exists():write_once(fragment,dict(series={c48_fars.SID:c48_fars.build()},downloads=[],outcome_association_calls=0))
    catalog['series'].update(json.loads(fragment.read_text())['series'])
    catalog['targets']['drinking_driver_crash_deaths']=dict(series=[c48_fars.SID],selection_authorized=True)
    catalog['targets']['snap_participation']=dict(series=['acs1_snap_household_receipt_rate'],selection_authorized=True)
    write_once(CATALOG,catalog)
    legacy=Path(__file__).with_name('OUTCOME_POPULATIONS_V8.json')
    meta=json.loads(legacy.read_text());meta.update(catalog_id=catalog['catalog_id'],inherited_population_metadata=dict(path=str(legacy.relative_to(REPO)),sha256=sha(legacy)))
    definitions={
        'fars_all_road_fatality_rate':('traffic_fatalities_all_road_users',[0,120],{}),
        'fars_motor_vehicle_occupant_fatality_rate':('motor_vehicle_occupant_traffic_deaths',[0,120],{}),
        'fars_child_occupant_fatality_rate_age0_4':('motor_vehicle_occupant_traffic_deaths',[0,4],{}),
        'fars_driver_fatality_rate_age15_20':('driver_traffic_deaths',[15,20],{}),
        'fars_driver_fatality_rate_age16_17':('driver_traffic_deaths',[16,17],{}),
        'fars_motorcyclist_fatality_rate':('motorcyclist_traffic_deaths',[0,120],{}),
        'fars_pedalcyclist_fatality_rate_all_ages':('pedalcyclist_traffic_deaths',[0,120],{}),
        'fars_pedalcyclist_fatality_rate_age5_14':('pedalcyclist_traffic_deaths',[5,14],{}),
        c48_fars.SID:('drinking_driver_crash_deaths',[0,120],{}),
        'brfss_current_smoking_pct':('adult_cigarette_smoking',[18,120],{}),
        'brfss_binge_drinking_pct':('adult_binge_drinking',[18,120],{}),
        'brfss_current_ecigarette_pct':('adult_ecigarette_use',[18,120],{}),
        'ipeds_enrollment_per_population':('college_enrollment',[0,120],{}),
        'ipeds_completions_per_population':('college_credential_completion',[0,120],{}),
        'fbi_hate_crime_incident_rate':('reported_hate_crime_incidents',[0,120],{}),
        'fbi_ucr_homicide_rate':('homicide_offenses',[0,120],{}),
        'acs1_dropout_share_age16_19':('high_school_noncompletion',[16,19],{}),
        'acs1_snap_household_receipt_rate':('snap_participation',[0,120],{})}
    for sid,(construct,ages,restrictions) in definitions.items():
        s=catalog['series'][sid]
        meta['series'][sid]=dict(construct=construct,ages=ages,restrictions=restrictions,
            filters=s.get('population_filters',{}),population_basis=s['population'],
            metadata_basis='C48 additive V9: declared source population, filters and units; no outcome associations',
            population_unit='household' if sid.startswith('acs1_snap') else 'institution_award' if sid.startswith('ipeds_completions') else 'institution_student' if sid.startswith('ipeds_enrollment') else 'person')
    aliases={
        'traffic_fatalities_all_road_users':['motor_vehicle_crash_deaths','all_road_traffic_deaths'],
        'snap_participation':['SNAP participation (receipt of SNAP/food-stamp benefits)','SNAP participation','household_snap_receipt','snap_household_receipt'],
        'drinking_driver_crash_deaths':['alcohol_involved_crash_deaths','drinking_driver_crash_fatalities'],
        'high_school_noncompletion':['school_dropout'],
        'adult_cigarette_smoking':['cigarette_smoking'],
        'homicide_offenses':['homicide']}
    for construct in sorted({d[0] for d in definitions.values()}):
        if not any(t['construct']==construct for t in meta['targets']):
            meta['targets'].append(dict(construct=construct,aliases=[construct]+aliases.get(construct,[]),ages=[0,120],restrictions={},
                rule='C48 / V6.3 same-construct population selection; legal eligible ages/restrictions come from verified purpose'))
    # C48 alcohol-driving ruling is instrument-specific; do not relabel the
    # construct for unrelated all-road policies or infer alcohol from names.
    for family in ('bac_per_se_008','youth_alcohol_impaired_driving','beer_keg_registration'):
        meta['targets'].append(dict(family=family,construct='drinking_driver_crash_deaths',
            aliases=['traffic_fatalities_all_road_users','driver_traffic_deaths','drinking_driver_crash_deaths'],
            ages=[0,120],restrictions={},rule='C48 §3 alcohol-driving policy primary: ACCIDENT.DRUNK_DR > 0 crash deaths; all-road deaths secondary'))
    write_once(POPULATIONS,meta)
    write_once(c48_fars.BASE/'BUILD_RECEIPT.json',dict(catalog=dict(path=str(CATALOG.relative_to(REPO)),sha256=sha(CATALOG)),
        populations=dict(path=str(POPULATIONS.relative_to(REPO)),sha256=sha(POPULATIONS)),downloads=[],outcome_association_calls=0,actual_data_estimation_calls=0))
    return catalog,meta

if __name__=='__main__':assemble()
