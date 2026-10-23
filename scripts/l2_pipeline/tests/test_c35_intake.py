"""C35 value-blind support and compatibility checks; synthetic inputs only."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts.l2_pipeline.intake import c35_intake as intake
from scripts.l2_pipeline.connectors import c35_core

class C35IntakeTests(unittest.TestCase):
    def setUp(self):
        self.years=list(range(1980,2001,2));self.units=['T'+str(i) for i in range(6)]+['C'+str(i) for i in range(5)]
        self.records=[dict(unit=u,start_period=1987 if i<3 else 1991 if i<6 else None,
            status='treated' if i<6 else 'never_treated',primary_excluded=False,never_control_eligible=i>=6,reversal_years=[])
            for i,u in enumerate(self.units)]
        self.imported=dict(coverage=dict(start_year=1980,end_year=2000),records=self.records)
        self.mapping=dict(series_id='synthetic',years=self.years)
        self.flags={(u,y):dict(available=1,annual_usable=1,linkage_certified=1,provisional=0) for u in self.units for y in self.years}
    def geometry(self,flags=None):
        with patch.dict(intake.SUPPORT,{'synthetic':flags or self.flags},clear=True):
            return intake.supported_geometry(self.imported,self.mapping)
    def test_odd_year_onsets_use_even_release_grid(self):
        g=self.geometry();self.assertTrue(g['passed']);self.assertEqual(g['treated_n'],6)
        self.assertEqual(g['supported_cohorts_n'],2);self.assertEqual(g['outcome_values_read'],0)
        self.assertTrue(all(y%2==0 for c in g['cohort_controls'].values() for y in c['pre_post_years']))
    def test_suppressed_control_horizon_parks(self):
        flags=copy.deepcopy(self.flags)
        for key,f in flags.items():
            if key[0].startswith('C'):f.update(available=0,suppressed=True)
        self.assertFalse(self.geometry(flags)['passed'])
    def test_provisional_and_unknown_age_linkage_do_not_support(self):
        flags=copy.deepcopy(self.flags)
        for (u,y),f in flags.items():
            if u.startswith('T') and y>1991:f['provisional']=1
        self.assertFalse(self.geometry(flags)['passed'])
        for f in flags.values():f.update(provisional=0,linkage_certified=0)
        self.assertFalse(self.geometry(flags)['passed'])
    def test_support_sidecar_rejects_outcome_values(self):
        with tempfile.TemporaryDirectory(dir=c35_core.REPO) as d:
            p=Path(d)/'support.json'
            p.write_text(json.dumps(dict(outcome_values_in_sidecar=False,series_id='synthetic',metadata=[dict(unit='AL',year=1980,outcome_value=0)])))
            spec=dict(series_id='synthetic',support_artifact=dict(path=str(p.relative_to(c35_core.REPO)),sha256=c35_core.sha(p)))
            with self.assertRaises(ValueError):intake.load_support(spec)
    def test_value_free_flag_allowlist(self):
        for forbidden in ('fatality_count','source_count','outcome_value','population_denominator','occupant','all_road'):
            self.assertNotIn(forbidden,c35_core.SUPPORT_FIELDS)

if __name__=='__main__':unittest.main()
