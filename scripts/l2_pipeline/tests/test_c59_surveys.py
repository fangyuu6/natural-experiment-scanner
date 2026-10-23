"""Survey interpretation and calendar/unit regressions; no policy associations."""
import json
import unittest

from scripts.l2_pipeline.connectors import c59_survey as survey
from scripts.l2_pipeline.connectors.common import ConnectorError


def person(**changes):
    p = {name:0 for name in survey.FIELDS}
    p.update(A_AGE=40,A_SEX=2,PRCITSHP=1,PEHSPNON=2,PERLIS=1,POV_UNIV=1,
             PEARNVAL=10000,WECLW=5,CLWK=1,PENPLAN=2,PENINCL=0,WKSWORK=52,HRSWK=40)
    p.update(changes)
    return p


class AsecInterpretationTests(unittest.TestCase):
    def test_weighted_median_is_not_unweighted_mean(self):
        self.assertEqual(survey.weighted_median([(10,9),(100,1)]),10)
        self.assertIsNone(survey.weighted_median([(10,0)]))

    def test_poverty_excludes_niu_and_preserves_child_non_citizen_scope(self):
        self.assertNotIn('poverty_pct_all',survey.measures(person(POV_UNIV=0)))
        self.assertEqual(survey.measures(person(A_AGE=10,PRCITSHP=5))['poverty_pct_noncitizen_children'],1)
        self.assertNotIn('poverty_pct_noncitizen_children',survey.measures(person(A_AGE=18,PRCITSHP=5)))

    def test_infant_coverage_niu_never_becomes_uninsured(self):
        self.assertNotIn('insured_pct_all',survey.measures(person(COV=0)))
        self.assertEqual(survey.measures(person(COV=2))['insured_pct_all'],0)

    def test_pension_participation_denominator_includes_no_plan_workers(self):
        m=survey.measures(person(PENPLAN=2,PENINCL=0))
        self.assertEqual(m['retirement_plan_participation_pct_private_workers_age18_64'],0)
        self.assertNotIn('retirement_plan_access_pct_private_workers_age18_64',survey.measures(person(WECLW=6)))
        self.assertNotIn('retirement_plan_access_pct_private_workers_age18_64',survey.measures(person(WECLW=1,CLWK=2)))

    def test_household_earnings_and_cognitive_disability_are_exact_subgroups(self):
        a=survey.measures(person(WECLW=4,PEDISREM=1))
        self.assertEqual(a['mean_annual_earnings_private_household_workers_age18_64'],10000)
        self.assertEqual(a['mean_annual_earnings_cognitive_difficulty_age18_64'],10000)
        self.assertNotIn('mean_annual_earnings_cognitive_difficulty_age18_64',survey.measures(person(PEDISEAR=1)))

    def test_offsets_are_one_based_and_conflicts_fail(self):
        self.assertEqual(survey.dictionary_fields('D A_AGE 2 19 (00:85)'),{'A_AGE':(18,2)})
        self.assertEqual(survey.field(b'00123',(2,3)),123)
        with self.assertRaises(ConnectorError):
            survey.dictionary_fields('A_AGE 2 19 (00:85)\nA_AGE 2 20 (00:85)')
        with self.assertRaises(ConnectorError):
            survey.field(b'1',(0,3))

    def test_orphan_person_is_rejected(self):
        with self.assertRaises(ConnectorError):
            list(survey.iter_people([b'3'],{}))

    def test_state_fips_is_reset_at_each_household(self):
        definitions={name:(1,1) for name in survey.FIELDS}
        definitions['GESTFIPS']=(1,2)
        records=list(survey.iter_people([b'106',b'31',b'148',b'31'],definitions))
        self.assertEqual([state for state,_ in records],['CA','TX'])
        with self.assertRaises(ConnectorError):
            list(survey.iter_people([b'106',b'31',b'199',b'31'],definitions))


class BrfssTests(unittest.TestCase):
    def record(self,**changes):
        r=dict(questionid='_RFSMOK3',response='Yes',locationabbr='CA',year='2010',break_out='Overall',data_value_type='Crude Prevalence',data_value_unit='%',data_value='10.5',data_value_footnote='')
        r.update(changes)
        return r

    def test_subgroups_not_mixed_and_missing_not_zero(self):
        records=[self.record(),self.record(break_out='Male'),self.record(locationabbr='TX',data_value='',data_value_footnote='Suppressed')]
        v,f,_=survey.brfss_rows(records,{'_RFSMOK3'})
        self.assertEqual(len(v),2)
        self.assertEqual(v['CA',2010],10.5)
        self.assertIsNone(v['TX',2010])
        self.assertTrue(f['TX',2010]['suppressed'])

    def test_duplicate_and_impossible_prevalence_rejected(self):
        with self.assertRaises(ConnectorError):
            survey.brfss_rows([self.record(),self.record()],{'_RFSMOK3'})
        with self.assertRaises(ConnectorError):
            survey.brfss_rows([self.record(data_value='101')],{'_RFSMOK3'})


class DurableArtifactTests(unittest.TestCase):
    def artifact(self,sid):
        p=survey.BASE/'parsed'/(sid+'.json')
        self.assertTrue(p.exists(),'Run offline survey build before integration tests')
        return json.loads(p.read_text())

    def test_income_clock_and_demographic_clock_documented(self):
        d=self.artifact('cps_asec_poverty_pct_all')
        self.assertEqual(d['years'],list(range(2015,2024)))
        self.assertEqual(d['details']['reference_year'],'survey_year - 1')
        self.assertFalse(d['details']['replicate_weights_used'])
        self.assertEqual(d['details']['outcome_association_calls'],0)
        self.assertTrue(all(m.get('sampling_error') for m in d['metadata']))

    def test_brfss_splice_preserves_modern_values_and_explicit_breaks(self):
        d=self.artifact('brfss_binge_drinking_pct_legacy_spliced')
        modern=json.loads((survey.RAW/'c46_20261010/parsed/brfss_binge_drinking_pct.json').read_text())
        values={(r['unit'],r['year']):r['outcome_value'] for r in d['rows']}
        for r in modern['rows']:
            self.assertEqual(values[r['unit'],r['year']],r['outcome_value'])
        flags={(r['unit'],r['year']):r for r in d['metadata']}
        self.assertIn('cellphone',flags['CA',2011]['series_break'])
        self.assertIn('5_to_4',flags['CA',2006]['series_break'])

    def test_support_contains_no_outcome_values_or_sample_counts(self):
        for p in (survey.BASE/'support').glob('*.json'):
            d=json.loads(p.read_text())
            self.assertFalse(d['outcome_values_in_sidecar'])
            for m in d['metadata']:
                self.assertNotIn('outcome_value',m)
                self.assertNotIn('unweighted_n',m)

    def test_pension_offering_is_not_individual_access(self):
        specs=json.loads((survey.BASE/'SERIES_SPECS.json').read_text())
        offered=specs['cps_asec_retirement_plan_access_pct_private_workers_age18_64']
        participation=specs['cps_asec_retirement_plan_participation_pct_private_workers_age18_64']
        self.assertEqual(offered['construct'],'employer_retirement_plan_offering')
        self.assertEqual(offered['population_metadata']['restrictions']['plan_measure'],'employer_offers_plan_to_any_employees')
        self.assertEqual(participation['construct'],'retirement_plan_participation')
        artifact=json.loads((survey.REPO/offered['artifact']['path']).read_text())
        self.assertIn('does not establish respondent eligibility',artifact['details']['measurement_definition'])
        self.assertEqual(artifact['population_filters'],offered['population_filters'])


if __name__=='__main__':
    unittest.main()
