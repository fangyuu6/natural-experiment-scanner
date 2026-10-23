"""C49 additive V10 and hub-approved equivalence table; V9 remains immutable."""
from copy import deepcopy
from pathlib import Path
import json
from . import outcome_v9,c49_ucr
from .c46_core import REPO
from .wonder import write_once
from ..storage import sha
CATALOG=Path(__file__).with_name('OUTCOME_CATALOG_V10_REVIEW1.json')
POPULATIONS=Path(__file__).with_name('OUTCOME_POPULATIONS_V10_REVIEW1.json')
TABLE=Path(__file__).with_name('CONSTRUCT_EQUIVALENCES_C49.json')
AUTH='docs/plans/l2-pipeline/HUB_RULINGS_C49.md'


def equivalences():
    groups=[('voter_turnout','turnout_vep_highest_office_rate',['voter turnout','general election turnout','young-adult turnout','voter_turnout_in_elections','general_election_voter_turnout','young_adult_voter_turnout_rate']),
        ('traffic_fatalities_all_road_users','fars_all_road_fatality_rate',['motor-vehicle crash deaths','traffic fatalities (all road users)','motor_vehicle_crash_deaths']),
        ('drinking_driver_crash_deaths','fars_drinking_driver_crash_fatality_rate',['alcohol-related fatal crashes','alcohol-involved fatal crashes','drinking-driver crashes','alcohol_related_fatal_motor_vehicle_crash_involvement','alcohol_related_fatal_crashes_involving_drinking_drivers_younger_than_21']),
        ('reported_violent_crime','fbi_ucr_violent_crime_rate',['reported crime incidence','UCR violent crime rates','reported_crime_incidence_measured_as_ucr_violent_and_property_offense_rates']),
        ('reported_property_crime','fbi_ucr_property_crime_rate',['UCR property crime rates'])]
    return [dict(card_construct=a,construct=c,primary_series=s,secondary_series=['fbi_ucr_property_crime_rate'] if c=='reported_violent_crime' and a in {'reported crime incidence','reported_crime_incidence_measured_as_ucr_violent_and_property_offense_rates'} else [],
        citation=AUTH+' §1'+(' / §2' if c.startswith('reported_') else ''),authority_sha256=sha(REPO/AUTH)) for c,s,aliases in groups for a in aliases]


def assemble():
    catalog=deepcopy(outcome_v9.catalog(verify_sources=False));catalog.update(schema_version=10,catalog_id='first_order_outcomes_v10',
        inherited_catalog=dict(path=str(outcome_v9.CATALOG_PATH.relative_to(REPO)),sha256=sha(outcome_v9.CATALOG_PATH)),
        registered_entrypoint='scripts.l2_pipeline.connectors.outcome_v10.construct',registration_entrypoint='scripts.l2_pipeline.connectors.outcome_v10.register',acquisition_entrypoint='scripts.l2_pipeline.connectors.c49_build.assemble')
    for sid,spec in catalog['series'].items():spec['registered_connector']='outcome_v10_'+sid
    catalog['series'].update(c49_ucr.build())
    table=equivalences();write_once(TABLE,dict(schema='l2_construct_equivalences_c49_v1',entries=table))
    catalog['construct_equivalences']=dict(path=str(TABLE.relative_to(REPO)),sha256=sha(TABLE))
    for e in table:catalog['targets'][e['card_construct']]=dict(series=[e['primary_series']]+e['secondary_series'],selection_authorized=True,citation=e['citation'])
    write_once(CATALOG,catalog)
    legacy=Path(__file__).with_name('OUTCOME_POPULATIONS_V9.json');meta=json.loads(legacy.read_text());meta.update(catalog_id=catalog['catalog_id'],inherited_population_metadata=dict(path=str(legacy.relative_to(REPO)),sha256=sha(legacy)),construct_equivalences=catalog['construct_equivalences'])
    for sid,construct,ages in [('turnout_vep_highest_office_rate','voter_turnout',[18,120]),('fbi_ucr_violent_crime_rate','reported_violent_crime',[0,120]),('fbi_ucr_property_crime_rate','reported_property_crime',[0,120])]:
        s=catalog['series'][sid];meta['series'][sid]=dict(construct=construct,ages=ages,restrictions={},filters=s['population_filters'],population_basis=s['population'],population_unit='person',metadata_basis=AUTH+' §1/2; source population, no outcome associations')
    for e in table:
        meta['targets'].append(dict(construct=e['construct'],aliases=[e['card_construct']],ages=[18,120] if e['construct']=='voter_turnout' else [0,120],restrictions={},
            preferred_series=e['primary_series'],secondary_series=e['secondary_series'],rule=e['citation'],equivalence_citation=e['citation']))
    # Canonical labels also need first-order construct validation.
    for c in ('voter_turnout','reported_violent_crime','reported_property_crime'):
        meta['targets'].append(dict(construct=c,aliases=[c],ages=[18,120] if c=='voter_turnout' else [0,120],restrictions={},rule=AUTH+' §1/2'))
    write_once(POPULATIONS,meta)
    return catalog,meta

if __name__=='__main__':assemble()
