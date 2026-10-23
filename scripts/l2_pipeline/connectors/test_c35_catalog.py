"""Prospective V7 regression contracts: immutable older names and value-free metadata."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from . import outcome_v7,c35_core
from .common import ConnectorError,ConnectorRequest

class C35CatalogTests(unittest.TestCase):
    def test_register_preserves_every_existing_entry_and_adapter(self):
        from scripts.l2_pipeline import connectors
        catalog=copy.deepcopy(connectors.CONNECTOR_CATALOG);builders=dict(connectors.ADAPTERS)
        outcome_v7.register()
        for name,entry in catalog.items():self.assertEqual(entry,connectors.CONNECTOR_CATALOG[name])
        for name,adapter in builders.items():self.assertIs(adapter,connectors.ADAPTERS[name])
    def test_new_catalog_identity_coverage_and_license_contracts(self):
        data=outcome_v7.catalog()
        expected={'wonder_homicide_rate':(1979,2024),'wonder_firearm_homicide_rate':(1979,2024),
            'fars_motor_vehicle_occupant_fatality_rate':(1975,2024),'fars_pedalcyclist_fatality_rate_age5_14':(1975,2024),
            'turnout_vep_highest_office_rate':(1980,2024),'turnout_vep_total_ballots_rate':(1980,2024),'fbi_ucr_homicide_rate':(1979,2025)}
        for sid,limits in expected.items():
            s=data['series'][sid];self.assertEqual((min(s['years']),max(s['years'])),limits)
            self.assertTrue(s['public_available']);self.assertEqual(s['adapter'],'c35_artifact')
        for sid in ['turnout_vep_highest_office_rate','turnout_vep_total_ballots_rate']:
            s=data['series'][sid];self.assertTrue(s['analysis_only']);self.assertTrue(s['republication_prohibited'])
            self.assertTrue(all(y%2==0 for y in s['years']))
        self.assertEqual(data['series']['wonder_homicide_rate']['nchs_comparability_ratio']['ratio'],1.002)
        self.assertNotIn('nchs_comparability_ratio',data['series']['wonder_firearm_homicide_rate'])
    def test_all_new_support_sidecars_are_value_free_and_bound(self):
        data=outcome_v7.catalog(verify_sources=False)
        for s in data['series'].values():
            if s['adapter']!='c35_artifact':continue
            record=s['support_artifact'];p=c35_core.REPO/record['path']
            self.assertEqual(c35_core.sha(p),record['sha256']);support=json.loads(p.read_text())
            self.assertFalse(support['outcome_values_in_sidecar'])
            for m in support['metadata']:self.assertLessEqual(set(m),c35_core.SUPPORT_FIELDS)
    def test_wrong_population_refused_before_outcome_artifact_open(self):
        s=outcome_v7.catalog(verify_sources=False)['series']['wonder_homicide_rate']
        request=ConnectorRequest(dict(series_id=s['series_id']),{'sex':'female'},(1979,),('AL',),'annual',c35_core.REPO)
        with self.assertRaises(ConnectorError):c35_core.construct(request,s)
    def test_fars_c21_overlap_proof(self):
        proof=json.loads((c35_core.BASE/'checks/FARS_OVERLAP.json').read_text())
        for sid,p in proof['series'].items():
            self.assertTrue(p['equal']);self.assertEqual(p['c21_canonical_row_sha256'],p['c35_canonical_row_sha256'])
    def test_repo_catalog_paths_work_from_nested_card_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder);p=repo/'data/panel.json';p.parent.mkdir()
            payload=dict(series_id='synthetic',population_filters={},inventory=[],limitations=[],details={},
                rows=[dict(unit='AL',year=1999,outcome_value=42)],
                metadata=[dict(unit='AL',year=1999,available=1,annual_usable=1,linkage_certified=1)])
            p.write_text(json.dumps(payload));digest=c35_core.sha(p)
            spec=dict(series_id='synthetic',population_filters={},frequency=['annual'],years=[1999],artifact=dict(path='data/panel.json',sha256=digest))
            request=ConnectorRequest(dict(paths=['data/panel.json'],source_sha256={'data/panel.json':digest}),{},(1999,),('AL',),'annual',repo/'nested/cards')
            with patch.object(c35_core,'REPO',repo):data=c35_core.construct(request,spec)
            self.assertEqual(data.rows[0]['outcome_value'],42)

if __name__=='__main__':unittest.main()
