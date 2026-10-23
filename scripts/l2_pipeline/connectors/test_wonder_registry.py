"""V4 prospective registration and exact published population contracts."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.l2_pipeline import connectors
from scripts.l2_pipeline.connectors.common import ConnectorRequest, ConnectorError
from scripts.l2_pipeline.connectors import wonder_registry as registry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        registry.register()

    def request(self, sid, filters=None, years=(1999,), **options):
        spec=registry.catalog()['series'][sid]
        filters=spec.get('population_filters',{}) if filters is None else filters
        out=dict(connector='outcome_v4_'+sid,series_id=sid,measure_id=sid,
                 frequency='annual',population_filters=filters,
                 paths=spec.get('local_source_paths',[spec.get('local_source_path','')]), **options)
        return ConnectorRequest(out,filters,years,('AL',),'annual',registry.ROOT)

    def test_additive_idempotent_and_no_v2_changes(self):
        old={n:deepcopy(e) for n,e in connectors.CONNECTOR_CATALOG.items() if not n.startswith('outcome_v4_')}
        old_builders={n:b for n,b in connectors.ADAPTERS.items() if not n.startswith('outcome_v4_')}
        self.assertEqual(registry.register(),registry.register())
        for n,e in old.items(): self.assertEqual(connectors.CONNECTOR_CATALOG[n],e)
        for n,b in old_builders.items(): self.assertIs(connectors.ADAPTERS[n],b)

    def test_available_wonder_count_series_across_seam(self):
        sid='wonder_firearm_deaths'
        panel=connectors.construct(self.request(sid,years=(2017,2018)))
        self.assertEqual([r['year'] for r in panel.rows],[2017,2018])
        self.assertTrue(all(m['available'] for m in panel.metadata))
        self.assertEqual(len(panel.inventory),2)
        self.assertEqual(panel.details['outcome_association_calls'],0)

    def test_maternal_age_coverage_cannot_invent_pre_2003_subbins(self):
        sid='wonder_births_by_maternal_age'
        with self.assertRaises(ConnectorError): registry.construct(self.request(sid,{'age_group':'15-17'},(1999,)))
        panel=registry.construct(self.request(sid,{'age_group':'15-17'},(2003,2024)))
        self.assertEqual(len(panel.rows),2)
        self.assertTrue(all(m['available'] for m in panel.metadata))
        total=registry.construct(self.request(sid,{'age_group':'15-19'},(1999,2024)))
        self.assertTrue(all(m['available'] for m in total.metadata))
        self.assertEqual(len(total.inventory),3)
        self.assertEqual(len(panel.inventory),2)

    def test_fertility_distinct_and_rate_unavailable_kept_unavailable(self):
        sid='wonder_fertility_rate_by_maternal_age'
        p=registry.construct(self.request(sid,{'age_group':'15-19'},(2003,2024)))
        self.assertIn('per 1,000 women',p.rows[0]['outcome_unit'])
        for age,year in [('15-19',1999),('under15',2003),('50+',2024)]:
            with self.assertRaises(ConnectorError): registry.construct(self.request(sid,{'age_group':age},(year,)))
        self.assertFalse(connectors.CONNECTOR_CATALOG['outcome_v4_wonder_birth_rate_by_maternal_age']['available'])

    def test_infant_linked_death_rate_uses_year_of_death(self):
        p=registry.construct(self.request('wonder_infant_mortality_rate',years=(1999,2003,2007,2024)))
        self.assertEqual(len(p.inventory),3)
        self.assertTrue(all(m['available'] for m in p.metadata))
        self.assertEqual(registry.catalog()['series']['wonder_infant_mortality_rate']['value_field'],'Death Rate')

    def test_exact_population_and_years_checked(self):
        for sid,filters,years in [('wonder_firearm_deaths',{},(1999,)),('wonder_firearm_deaths',None,(1998,)),('wonder_births_by_maternal_age',{'age_group':'16-17'},(2003,))]:
            with self.assertRaises(ConnectorError): registry.construct(self.request(sid,filters,years))

    def test_historical_drug_and_suicide_alias(self):
        p=registry.construct(self.request('nchs_historical_drug_overdose_deaths',years=(1999,2018)))
        self.assertTrue(all(m['available'] for m in p.metadata))
        alias=registry.construct(self.request('nchs_historical_suicide_deaths'))
        base=registry.construct(self.request('cdc_leading_cause_deaths',{'cause_name':'Suicide'}))
        self.assertEqual(alias.rows[0]['outcome_value'],base.rows[0]['outcome_value'])
        self.assertEqual(alias.details['alias_of'],'cdc_leading_cause_deaths')

    def test_all_explicit_target_mappings(self):
        drug=['pdmp','naloxone_access','pill_mill_regulation','good_samaritan','cannabis_policy']
        for target in drug:
            for sid in ['wonder_drug_overdose_deaths','wonder_opioid_overdose_deaths']:
                self.assertTrue(connectors.target_mapped('outcome_v4_'+sid,target,sid))
        for target in ['alcohol_tax','bac_limits','zero_tolerance_driving','minimum_legal_drinking_age','alcohol_control']:
            self.assertTrue(connectors.target_mapped('outcome_v4_wonder_motor_vehicle_traffic_deaths_age15_24',target))
        for target in ['abortion_access','contraception_access']:
            self.assertTrue(connectors.target_mapped('outcome_v4_wonder_fertility_rate_by_maternal_age',target))
        self.assertFalse(connectors.target_mapped('outcome_v4_wonder_infant_mortality_rate','firearm_law'))

    def test_receipt_guard_and_catalog_activation_fail_closed(self):
        with self.assertRaises(ConnectorError): registry._verified_artifact({'path':'~/.codex/auth.json','sha256':'not-read'})
        spec=deepcopy(registry.catalog()['series']['wonder_firearm_deaths'])
        spec['browser_receipts_verified']=False
        with self.assertRaises(ConnectorError): registry._verified_wonder(spec)
        spec['browser_receipts_verified']=True
        spec['exports'][0]['population_filters']={}
        with self.assertRaises(ConnectorError): registry._verified_wonder(spec)

    def test_registration_conflict_does_not_partially_mutate(self):
        name='outcome_v4_wonder_firearm_deaths'
        entry=dict(connectors.CONNECTOR_CATALOG[name],available=False)
        with patch.dict(connectors.CONNECTOR_CATALOG,{name:entry}):
            before=deepcopy(connectors.CONNECTOR_CATALOG)
            with self.assertRaises(ConnectorError): registry.register()
            self.assertEqual(before,connectors.CONNECTOR_CATALOG)

    def test_inherited_v2_adapter_stays_bound(self):
        req=self.request('cps_teen_employment')
        with patch.object(registry,'construct_v2',return_value='sentinel') as build:
            self.assertEqual(registry.construct(req),'sentinel')
            build.assert_called_once_with(req)

    def test_outcome_only_inputs(self):
        with self.assertRaises(ConnectorError): registry.construct(self.request('wonder_firearm_deaths',party='not-read'))


if __name__=='__main__': unittest.main()
