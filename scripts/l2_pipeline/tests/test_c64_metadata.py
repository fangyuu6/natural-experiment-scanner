"""Synthetic C64 metadata exports and byte-preservation regression."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline import c64_metadata as c

class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / c.COVARIATES_PATH
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps(dict(fields=[dict(field='urban_population_share',status='NEEDS_DATA')], new_downloads=[])))
    def test_contract_binds_missing_covariates_and_s6_only(self):
        value = c.export_contract({'first_order': {}}, self.root)
        self.assertEqual(value['schema'], 'l2_addendum_v2_c64')
        self.assertEqual(value['outcome_compute_stage'], 'S6')
        self.assertFalse(value['calibration_outcome_computation'])
        self.assertIsNone(value['first_order']['purpose_direction'])
        self.assertEqual(value['geography']['state_covariates']['sha256'], c.sha(self.manifest))
        self.assertEqual(value['geography']['state_covariates']['manifest']['fields'][0]['status'], 'NEEDS_DATA')
        self.assertEqual(value['s_pre']['ddof'], 1)
        self.assertEqual(value['placebo_profiles']['event_times'], list(range(6)))
        self.assertEqual(value['trough']['minimum_baseline_years'], 3)
        self.assertEqual(value['unregistered_tests'], ['N6','N8','N11','N12'])
    def test_missing_covariate_manifest_fails_closed(self):
        self.manifest.unlink()
        with patch.object(c, '_repo', return_value=self.root):
            with self.assertRaises(FileNotFoundError): c.export_contract({}, self.root)
    def test_finished_frozen_and_sealed_bytes_remain_identical(self):
        base = self.root / 'data/l2_pipeline/jobs'
        for name, marker in [('finished','RESULT.json'), ('frozen','FREEZE_PREP.json'),('sealed','SEALED_PACKAGE.json')]:
            package = base / name; package.mkdir(parents=True)
            (package / marker).write_text('{"synthetic": true}')
            (package / 'CARD.json').write_text('{"first_order":{}}')
        addendum=self.root/c.ADDENDUM_PATH; addendum.parent.mkdir(parents=True);addendum.write_text('frozen addendum')
        inventory=c.protected_inventory(self.root)
        self.assertEqual(len(inventory),7)
        c.export_contract({}, self.root)
        self.assertEqual(c.verify_protected(self.root, inventory), [])
        (base/'finished/RESULT.json').write_text('tampered')
        self.assertEqual(c.verify_protected(self.root, inventory), ['data/l2_pipeline/jobs/finished/RESULT.json: bytes changed'])
        (base/'sealed/CARD.json').unlink()
        self.assertEqual(len(c.verify_protected(self.root, inventory)),2)
    def test_migration_versions_only_unfrozen_and_is_immutable(self):
        reg=self.root/'data/l2_pipeline/registry';reg.mkdir(parents=True)
        work=self.root/'data/l2_pipeline/work'; rows=[]
        for identity in ['open','frozen']:
            package=work/identity/'v1';package.mkdir(parents=True)
            card=package/'CARD.json';card.write_text(json.dumps(dict(design_id=identity,version='v1',first_order={})))
            rows.append(dict(design_id=identity,card=str(card),work_dir=str(work)))
        (work/'frozen/v1/FREEZE_PREP.json').write_text('{}')
        (reg/'ACTIVE_DESIGNS.json').write_text(json.dumps(dict(designs=rows)))
        before={r['card']:c.sha(r['card']) for r in rows}
        output=self.root/'data/l2_pipeline/autopilot/c64/migration'
        report=c.plan_next_versions(self.root,output)
        self.assertEqual([r['status'] for r in report['designs']],['NEXT_VERSION_PLANNED','addendum fields unavailable'])
        next_card=json.loads((output/'open/NEXT_CARD_C64.json').read_text())
        self.assertIsNone(next_card['first_order']['purpose_direction'])
        self.assertEqual(next_card['l3_export_v2']['schema'],c.SCHEMA)
        self.assertFalse((output/'frozen/NEXT_CARD_C64.json').exists())
        self.assertEqual(before,{r['card']:c.sha(r['card']) for r in rows})
        self.assertEqual(report,c.plan_next_versions(self.root,output))
        with self.assertRaises(ValueError):c._write_once(output/'open/NEXT_CARD_C64.json',{})


class GeographyTests(unittest.TestCase):
    def setUp(self):
        self.card = dict(outcome=dict(years=[2000,2010],frequency='annual'),
            treatment=dict(universe=['A','N','F','B','R','X','C'],excluded_units=['X'],
                excluded_conflict_states=['C'],window=dict(start_year=2000,end_year=2010)))
        self.treatment = dict(audit=dict(passed=True),coverage=dict(start_year=1990,end_year=2020), records=[
            dict(unit='A',status='treated',start_period=2005,operative_intervals=[dict(start_year=2005,end_year=2020)]),
            dict(unit='N',status='never_treated',start_period=None),
            dict(unit='F',status='not_yet_treated',start_period=2015),
            dict(unit='B',status='always_treated'),
            dict(unit='R',status='treated',start_period=2004,operative_intervals=[dict(start_period=2004,end_period=2007)])])
    def test_never_future_always_and_reverted_annual_records(self):
        result=c.geography(self.card,self.treatment)
        self.assertEqual(result['status'],'AVAILABLE')
        self.assertEqual(result['U'],['A','F','N','R'])
        self.assertEqual(result['R'],['F','N','R'])
        self.assertEqual(result['always_treated_units'],['B'])
        self.assertEqual(result['treatment_records'],self.treatment['records'])
        self.assertEqual(result['treatment_audit'],dict(passed=True))
        result['treatment_records'][0]['start_period']=9999
        self.assertEqual(self.treatment['records'][0]['start_period'],2005)
    def test_ended_spell_without_end_coverage_does_not_guess_absence(self):
        self.treatment.pop('coverage')
        result=c.geography(self.card,self.treatment)
        self.assertEqual(result['status'],'FIELD_MISSING')
        self.assertIsNone(result['R'])
        self.assertIn('R',result['unresolved_last_year_units'])
    def test_unknown_and_missing_records_do_not_become_never_treated(self):
        self.treatment['records']=[r for r in self.treatment['records'] if r['unit']!='N']
        self.treatment['records'][0]['status']='unknown'
        result=c.geography(self.card,self.treatment)
        self.assertIsNone(result['U']); self.assertIsNone(result['R'])
        self.assertEqual(result['unresolved_baseline_units'],['A','N'])
    def test_baseline_interval_excludes_always_and_reentry_is_active(self):
        self.treatment['records'][0]['operative_intervals'][0]['start_year']=1999
        self.treatment['records'][-1]['operative_intervals'].append(dict(start_period=2009,end_period=2012))
        result=c.geography(self.card,self.treatment)
        self.assertEqual(result['always_treated_units'],['A','B'])
        self.assertEqual(result['R'],['F','N'])
    def test_no_audit_nonannual_and_ambiguous_intervals_stay_missing(self):
        self.treatment['audit']['passed']=False
        self.assertIsNone(c.geography(self.card,self.treatment)['R'])
        self.card['outcome']['frequency']='monthly'
        self.assertEqual(c.geography(self.card,self.treatment)['status'],'FIELD_MISSING')
        self.card['outcome']['frequency']='annual'
        self.treatment['audit']['passed']=True
        self.treatment['records'][0]['operative_intervals']=[dict(start_date='2005-07-01',end_date=None)]
        self.assertIsNone(c.geography(self.card,self.treatment)['R'])

if __name__ == "__main__": unittest.main()
