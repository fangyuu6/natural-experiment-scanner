"""C70 outcome contract checks; synthetic cells and metadata only."""
import copy,json,tempfile,unittest
from pathlib import Path
from scripts.l2_pipeline.connectors import c70_build as c70
from scripts.l2_pipeline.connectors.common import ConnectorError

class C70ConnectorContracts(unittest.TestCase):
    def test_build_globals_restore_on_failure(self):
        from unittest.mock import patch
        before=(c70.REPO,c70.BASE,c70.core.REPO,c70.core.BASE,c70.public.REPO,c70.rates.ROOT,c70.core.SUPPORT_FIELDS.copy())
        with patch.object(c70,'_assemble',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):c70.assemble('/tmp/c70-fixture-unused')
        self.assertEqual(before,(c70.REPO,c70.BASE,c70.core.REPO,c70.core.BASE,c70.public.REPO,c70.rates.ROOT,c70.core.SUPPORT_FIELDS))
        with self.assertRaises(RuntimeError):
            with c70.settings(c70.core,SUPPORT_FIELDS=c70.core.SUPPORT_FIELDS|c70.EXTRA_SUPPORT):raise RuntimeError('fixture failure')
        self.assertEqual(before[-1],c70.core.SUPPORT_FIELDS)

    def test_ipeds_threshold_boundaries(self):
        self.assertTrue(c70.ipeds_coverage(100,94)['unreliable'])
        self.assertFalse(c70.ipeds_coverage(100,95)['unreliable'])
        self.assertFalse(c70.ipeds_coverage(100,98)['institution_coverage_sensitivity_99'])
        self.assertTrue(c70.ipeds_coverage(100,99)['institution_coverage_sensitivity_99'])
        self.assertFalse(c70.ipeds_coverage(100,95,'HD_institutions_without_component')['unreliable'])
        self.assertTrue(c70.ipeds_coverage(0,0)['unreliable'])
        self.assertTrue(c70.ipeds_coverage(100,100,'published_total_missing')['unreliable'])
        with self.assertRaises(ConnectorError):c70.ipeds_coverage(10,11)

    def test_college_age18_24_denominator(self):
        cells={5:10,10:5,15:35,21:20,26:5,31:25,1:99999}
        self.assertEqual(c70.college_formula(cells),40)
        self.assertEqual(c70.college_formula(cells,True),30)
        cells[15]=None;self.assertIsNone(c70.college_formula(cells))

    def test_ucr_definitions_not_spliced(self):
        from scripts.l2_pipeline.connectors.c49_ucr import parse
        records=[dict(year=1979,state_abbr='AL',state_name='Alabama',population=1000,rape_legacy=2,rape_revised='',caveats='')]
        legacy,_=parse(records,'rape_legacy');revised,_=parse(records,'rape_revised')
        self.assertEqual(legacy['AL',1979]['value'],200)
        self.assertIsNone(revised['AL',1979]['value'])
        with self.assertRaises(ConnectorError):parse(records*2,'rape_legacy')

    def test_brfss_exact_age_question_and_break(self):
        r=dict(locationabbr='AL',year='2011',questionid='_HCVU651',response='Yes',break_out='Overall',data_value_type='Crude Prevalence',data_value_unit='%',question='Adults aged 18-64 who have any kind of health care coverage',data_value='81.2',data_value_footnote='')
        values,flags=c70.parse_brfss([r]);self.assertEqual(values['AL',2011],81.2)
        self.assertIn('raking',flags['AL',2011]['series_break'])
        with self.assertRaises(ConnectorError):c70.parse_brfss([r,r])
        with self.assertRaises(ConnectorError):c70.parse_brfss([r|dict(question='Adults18+')])
        values,_=c70.parse_brfss([r|dict(questionid='HLTHPLN1')]);self.assertEqual(values,{})
        values,flags=c70.parse_brfss([r|dict(data_value='',data_value_footnote='Suppressed')]);self.assertIsNone(values['AL',2011]);self.assertTrue(flags['AL',2011]['suppressed'])

    def test_v15_catalog_contracts(self):
        from scripts.l2_pipeline.connectors import outcome_v15,population_v63
        data=outcome_v15.catalog(verify_sources=False)
        self.assertEqual(data['schema_version'],15)
        for field in c70.UCR_FIELDS:self.assertIn('fbi_ucr_'+field+'_rate',data['series'])
        acs=data['series']['acs1_college_enrollment_share_age18_24'];self.assertEqual(acs['years'],[y for y in range(2005,2025) if y!=2020])
        ftc=data['series']['ftc_csn_idtheft_reports_per_100k'];self.assertEqual(ftc['construct_label'],'reported complaints');self.assertEqual(ftc['years'],list(range(2002,2025)))
        self.assertIn(2010,[x['year'] for x in ftc['series_breaks']])
        for kind in ('enrollment','completions'):
            self.assertEqual(data['series']['ipeds_'+kind+'_per_population']['required_sensitivities'][0]['threshold'],.99)
        source,meta=population_v63.current_catalog();self.assertEqual(source['catalog_id'],'first_order_outcomes_v19');  # window 2026-10-23: V19 is current (V16 on 2026-10-16, V17 on 2026-10-17); V15 contracts above are unchanged
        self.assertEqual(meta['series']['acs1_college_enrollment_share_age18_24']['ages'],[18,24])

    def test_ipeds_registered_99_masks_only_ineligible_cells(self):
        from unittest.mock import patch
        from scripts.l2_pipeline.connectors import outcome_v15
        from scripts.l2_pipeline.connectors.common import ConnectorRequest,PanelData
        sid='ipeds_enrollment_per_population';data=outcome_v15.catalog(verify_sources=False);spec=data['series'][sid]
        options=dict(connector='outcome_v15_'+sid,series_id=sid,measure_id=sid,population_filters=spec['population_filters'],frequency='annual',institution_coverage_min=.99)
        request=ConnectorRequest(options,spec['population_filters'],(2019,),('AL','AK'),'annual',Path('.'))
        panel=PanelData((dict(unit='AL',year=2019,outcome_value=5),dict(unit='AK',year=2019,outcome_value=7)),(dict(unit='AL',year=2019,institution_coverage=.97,annual_usable=1,linkage_certified=1),dict(unit='AK',year=2019,institution_coverage=.99,annual_usable=1,linkage_certified=1)))
        with patch.object(outcome_v15.c46_core,'construct',return_value=panel):
            result=outcome_v15._construct(request,data)
        self.assertIsNone(result.rows[0]['outcome_value']);self.assertEqual(result.rows[1]['outcome_value'],7)
        self.assertEqual(result.metadata[0]['annual_usable'],0);self.assertEqual(result.metadata[0]['institution_coverage'],.97)
        from dataclasses import replace
        with self.assertRaises(ConnectorError):outcome_v15._construct(replace(request,options=options|dict(institution_coverage_min=.90)),data)

    def test_v15_rejects_wrong_binding_and_associations(self):
        from scripts.l2_pipeline.connectors import outcome_v15
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        data=outcome_v15.catalog(verify_sources=False);sid='ftc_csn_idtheft_reports_per_100k';spec=data['series'][sid]
        options=dict(connector='outcome_v15_'+sid,series_id=sid,measure_id=sid,population_filters=spec['population_filters'],frequency='annual',party='forbidden')
        request=ConnectorRequest(options,spec['population_filters'],(2002,),('AL',),'annual',Path('.'))
        with self.assertRaises(ConnectorError):outcome_v15._construct(request,data)
        request=ConnectorRequest(options|dict(series_id='unknown'),spec['population_filters'],(2002,),('AL',),'annual',Path('.'))
        with self.assertRaises(ConnectorError):outcome_v15._construct(request,data)

if __name__=='__main__':unittest.main()
