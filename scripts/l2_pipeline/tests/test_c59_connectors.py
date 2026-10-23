"""C59 arithmetic, geography, missingness and additive catalog contracts."""
import csv,json,tempfile,unittest
from pathlib import Path
from scripts.l2_pipeline.connectors import c59_fiscal,c59_bps,c59_ui
from scripts.l2_pipeline.connectors.common import ConnectorError

class C59Connectors(unittest.TestCase):
    def test_per_capita_uses_thousand_dollars_and_no_imputation(self):
        self.assertEqual(c59_fiscal.per_capita({('AL',2000):12,('AK',2000):8},{('AL',2000):3000}),{('AL',2000):4})
    def test_permits_units_not_buildings_or_reported_subset(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'st2000a.txt';r=['0099','01','3','6','ALABAMA']+['0']*24
            for i,v in zip((6,9,12,15),(100,20,30,40)):r[i]=str(v)
            r[5]='999';r[18]='9999'
            p.write_text(','.join(r)+'\n')
            self.assertEqual(c59_bps.permit_values([p]),{('AL',2000):190})
    def test_qcew_suppression_and_exact_scope(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'q.csv';fields=['area_fips','own_code','industry_code','size_code','qtr','year','disclosure_code','annual_avg_emplvl']
            with p.open('w') as f:
                w=csv.writer(f);w.writerow(fields);w.writerows([
                    ['01000','5','6244','0','A','2001','','125'],
                    ['02000','5','6244','0','A','2001','N','0'],
                    ['04001','5','6244','0','A','2001','','999'],
                    ['04000','0','6244','0','A','2001','','999']])
            values,flags=c59_bps.qcew_values([p]);self.assertEqual(values,{('AL',2001):125});self.assertTrue(flags['AK',2001]['suppressed'])
    def test_ui_complete_months_and_liable_state_sum(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ui.csv'
            with p.open('w') as f:
                w=csv.writer(f);w.writerow(['st','rptdate','c21','c24']);w.writerows([['AL',f'2000-{m:02}-28',10,2] for m in range(1,13)]+[['AK','2000-01-31',999,999]])
            values,flags=c59_ui.annual_values(p,['c21','c24']);self.assertEqual(values,{('AL',2000):144});self.assertTrue(flags['AK',2000]['partial_year'])
    def test_ui_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'ui.csv';p.write_text('st,rptdate,c1\nAL,2000-01-31,1\nAL,2000-01-31,2\n')
            with self.assertRaises(ConnectorError):c59_ui.annual_values(p,['c1'])
    def test_local_fiscal_geography_and_population_units(self):
        codes,values,_,_=c59_fiscal.stc()
        # Census government code 03 is Arizona, not FIPS 04. Label governs.
        self.assertIn(('AZ',2024),values['C105']);self.assertNotIn(('US',2024),values['C105'])
        pop=c59_fiscal.population();self.assertGreater(pop['AL',2020],4_000_000);self.assertNotIn(('AL',2025),pop)
    def test_v12_construct_reads_exact_pinned_permit_contract(self):
        from scripts.l2_pipeline.connectors import outcome_v12
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        from scripts.l2_pipeline.connectors.c59_core import REPO
        outcome_v12.register();data=outcome_v12.catalog(verify_sources=False)
        sid='census_bps_housing_units_authorized_per_1000';spec=data['series'][sid]
        request=ConnectorRequest(options=dict(connector='outcome_v12_'+sid,series_id=sid,measure_id=sid,
            population_filters=spec['population_filters'],frequency='annual'),filters=spec['population_filters'],
            years=(2020,),units=('AL',),frequency='annual',base_dir=REPO)
        panel=outcome_v12._construct(request,data)
        self.assertEqual(len(panel.rows),1);self.assertGreater(panel.rows[0]['outcome_value'],0)
        self.assertEqual(panel.rows[0]['outcome_unit'],'housing units per 1,000 residents')
    def test_catalog_additive_and_value_free_support(self):
        from scripts.l2_pipeline.connectors import outcome_v11,outcome_v12,population_v63
        from scripts.l2_pipeline.connectors.c59_core import REPO
        old=outcome_v11.catalog(verify_sources=False);new=outcome_v12.catalog(verify_sources=True)
        self.assertLessEqual(set(old['series']),set(new['series']))
        for sid,spec in old['series'].items():
            inherited=dict(spec,registered_connector='outcome_v12_'+sid)
            self.assertEqual(inherited,new['series'][sid])
        meta=Path(outcome_v12.__file__).with_name('OUTCOME_POPULATIONS_V12.json')
        population_v63.catalog(source=new,path=meta)
        for sid,spec in new['series'].items():
            if spec['adapter']!='c59_artifact':continue
            support=json.loads((REPO/spec['support_artifact']['path']).read_text())
            self.assertFalse(support['outcome_values_in_sidecar'])
            self.assertTrue(all('outcome_value' not in row and 'numerator' not in row and 'denominator' not in row for row in support['metadata']))

if __name__=='__main__':unittest.main()
