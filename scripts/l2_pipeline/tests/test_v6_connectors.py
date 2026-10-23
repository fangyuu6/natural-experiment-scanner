"""Synthetic V6 source/target/population contracts, also valid under python -O."""
import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from scripts.l2_pipeline.connectors import (CONNECTOR_CATALOG, ConnectorError,
    ConnectorNotReady, ConnectorRequest, construct, download_plan, target_mapped)
from scripts.l2_pipeline.connectors.first_order_downloads import _url
from scripts.l2_pipeline.connectors.state_crime import FIELDS, SOURCE_URL


class V6ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def req(self, connector='fars_state', measure='traffic_deaths_count', filters=None,
            years=(1985,), units=('CA',), **opts):
        return ConnectorRequest(dict(connector=connector, measure_id=measure, **opts),
            filters or {'population':'all_traffic_fatalities'}, years, units, 'annual', self.base)
    def csvtext(self, rows):
        out=io.StringIO(); w=csv.DictWriter(out,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        return out.getvalue()
    def fars(self, accident=None, person=None):
        accident=accident or [dict(STATE=6, ST_CASE=60001, YEAR=1985, FATALS=2)]
        p=self.base/'FARS1985NationalCSV.zip'
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('national/ACCIDENT.CSV',self.csvtext(accident))
            if person is not None:z.writestr('national/PERSON.CSV',self.csvtext(person))
        return [p.name]
    def persons(self):
        return [dict(STATE=6,ST_CASE=60001,VEH_NO=1,PER_NO=1,AGE=17,PER_TYP=1,INJ_SEV=4),
                dict(STATE=6,ST_CASE=60001,VEH_NO=1,PER_NO=2,AGE=7,PER_TYP=2,INJ_SEV=4)]
    def test_catalog_exact_construct_binding_and_blocked_access(self):
        self.assertTrue(target_mapped('fars_state','traffic_deaths_count','traffic_deaths_count'))
        self.assertTrue(target_mapped('acs1_health_insurance','dependent_health_insurance_coverage_pct','health_insurance_coverage_pct'))
        self.assertTrue(target_mapped('brfss_state_prevalence','guideline_colorectal_screening_pct','colorectal_screening_pct'))
        self.assertFalse(target_mapped('brfss_state_prevalence','secondhand_smoke_exposure_pct','current_smoking_pct'))
        self.assertFalse(target_mapped('nchs_mortality','homicide_count','mortality_deaths_count'))
        self.assertFalse(target_mapped('qcew','wages','jobs'))
        self.assertFalse(target_mapped('fbi_ucr_state','violent_crime_count'))
        self.assertFalse(target_mapped('ca_doj_crime','violent_crime_count'))
        self.assertFalse(target_mapped(None, ['employment']))
        self.assertEqual(CONNECTOR_CATALOG['ca_doj_crime']['geographic_scope'],('CA',))
        self.assertIn(2020,CONNECTOR_CATALOG['acs1_health_insurance']['coverage']['excluded_years'])
    def test_fars_national_census_totals_and_structural_zero(self):
        paths=self.fars()
        p=construct(self.req(paths=paths,units=('CA','NY')))
        self.assertEqual([r['outcome_value'] for r in p.rows],[2,0])
        self.assertEqual(construct(self.req(measure='fatal_crashes_count',paths=paths)).rows[0]['outcome_value'],1)
    def test_fars_original_two_digit_year_binds_to_archive_vintage(self):
        paths=self.fars(accident=[dict(STATE=6,ST_CASE=60001,YEAR=85,FATALS=2)])
        self.assertEqual(construct(self.req(paths=paths)).rows[0]['outcome_value'],2)
        paths=self.fars(accident=[dict(STATE=6,ST_CASE=60001,YEAR=84,FATALS=2)])
        with self.assertRaises(ConnectorError):construct(self.req(paths=paths))
    def test_fars_age_and_role_are_applied_before_subgroup_count(self):
        paths=self.fars(person=self.persons())
        filters={'population':'traffic_fatalities','age_min':15,'age_max':20,'person_type':'driver'}
        self.assertEqual(construct(self.req(measure='teen_traffic_deaths_count',filters=filters,paths=paths)).rows[0]['outcome_value'],1)
        filters={'population':'traffic_fatalities','age_min':0,'age_max':7,'person_type':'occupant'}
        self.assertEqual(construct(self.req(measure='child_traffic_deaths_count',filters=filters,paths=paths)).rows[0]['outcome_value'],1)
    def test_fars_child_bicyclist_excludes_other_cyclists_and_drivers(self):
        people=self.persons();people[0].update(AGE=15,PER_TYP=6);people[1].update(AGE=10,PER_TYP=7)
        paths=self.fars(person=people)
        filters={'population':'traffic_fatalities','age_min':0,'age_max':15,'person_type':'bicyclist'}
        p=construct(self.req(measure='child_bicycle_deaths_count',filters=filters,paths=paths))
        self.assertEqual(p.rows[0]['outcome_value'],1)
        self.assertTrue(target_mapped('fars_state','child_bicycle_deaths_count','child_bicycle_deaths_count'))
        filters['person_type']='all'
        with self.assertRaises(ConnectorError):construct(self.req(measure='child_bicycle_deaths_count',filters=filters,paths=paths))
    def test_fars_unknown_age_is_not_assigned_to_target_and_blocks_linkage(self):
        people=self.persons();people[0]['AGE']=99
        paths=self.fars(person=people)
        p=construct(self.req(measure='teen_traffic_deaths_count',
            filters={'population':'traffic_fatalities','age_min':15,'age_max':20,'person_type':'driver'},paths=paths))
        self.assertEqual(p.rows[0]['outcome_value'],0)
        self.assertEqual(p.metadata[0]['fatal_person_age_unknown'],1)
        self.assertEqual(p.metadata[0]['linkage_certified'],0)
    def test_fars_missing_person_file_duplicate_or_unmatched_source_fail_closed(self):
        f={'population':'traffic_fatalities','age_min':15,'age_max':20}
        with self.assertRaises(ConnectorError):construct(self.req(measure='teen_traffic_deaths_count',filters=f,paths=self.fars()))
        people=self.persons();people.append(people[0])
        with self.assertRaises(ConnectorError):construct(self.req(measure='teen_traffic_deaths_count',filters=f,paths=self.fars(person=people)))
        with self.assertRaises(ConnectorError):construct(self.req(measure='teen_traffic_deaths_count',filters=f,paths=self.fars(person=self.persons()[:1])))
    def test_fars_never_substitutes_all_traffic_for_children_or_unsupported_population(self):
        with self.assertRaises(ConnectorNotReady):construct(self.req(measure='child_traffic_deaths_count',paths=self.fars()))
        with self.assertRaises(ConnectorError):construct(self.req(measure='teen_traffic_deaths_count',
            filters={'population':'traffic_fatalities','age_min':0,'age_max':17},paths=self.fars()))
        with self.assertRaises(ConnectorNotReady):download_plan(self.req(years=(1970,)))
    def ca(self, rows=None, **opts):
        fields=dict.fromkeys(FIELDS.values(), '2')
        rows=rows or [dict(Year=1985,County='Sacramento',NCICCode='CA0340100',**fields)]
        p=self.base/'ca.csv';p.write_text(self.csvtext(rows))
        options=dict(paths=[p.name],expected_agencies_by_year={'1985':['CA0340100']},
            agency_coverage_refs=[dict(source_ref='https://data-openjustice.doj.ca.gov/coverage',locator='synthetic certified roster')],
            license_reviewed=True,license='Synthetic permitted fixture',
            license_ref=dict(source_ref='https://data-openjustice.doj.ca.gov/rights',locator='synthetic reviewed rights'))
        options.update(opts)
        return self.req('ca_doj_crime','violent_crime_count',{'series':'reported_agency_offenses','state':'CA'},**options)
    def test_ca_public_download_plan_and_population_scope(self):
        plan=download_plan(self.ca())[0]
        self.assertEqual(plan['url'],SOURCE_URL);_url(plan['url'])
        self.assertIn('explicit redistribution license not located',plan['license'])
        with self.assertRaises(ConnectorNotReady):download_plan(self.req('ca_doj_crime','violent_crime_count',
            {'series':'reported_agency_offenses','state':'CA'},units=('CA','NY')))
    def test_ca_rights_roster_and_missing_agency_are_binding(self):
        self.assertEqual(construct(self.ca()).rows[0]['outcome_value'],2)
        with self.assertRaises(ConnectorNotReady):construct(self.ca(license_reviewed=False))
        with self.assertRaises(ConnectorNotReady):construct(self.ca(agency_coverage_refs=[]))
        p=construct(self.ca(expected_agencies_by_year={'1985':['CA0340100','CA0340200']}))
        self.assertEqual(p.rows[0]['outcome_value'],'');self.assertEqual(p.metadata[0]['linkage_certified'],0)
        with self.assertRaises(ConnectorError):construct(self.ca(expected_agencies_by_year={'1985':['CA0340200']}))
    def test_fars_downloads_are_no_key_and_only_declared_vintages(self):
        plans=download_plan(self.req(years=(1982,2011)))
        self.assertEqual(len(plans),2)
        for p in plans:
            _url(p['url']);self.assertNotIn('key=',p['url']);self.assertEqual(p['status'],'planned_not_downloaded')
        with self.assertRaises(ConnectorError):_url('https://static.nhtsa.gov/file?api_key=secret')


if __name__=='__main__': unittest.main()
