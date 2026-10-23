import csv
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.connectors.common import ConnectorError,ConnectorNotReady,ConnectorRequest
from scripts.l2_pipeline.connectors.natality import cdc_natality,natality_download_plan

class NatalityPublicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'cells.csv'
        self.rows=[['2019','Alabama','15-19 years','12.5','123','999','9.9','per 1,000'],
                   ['2019','Alabama','15-17 years','5.0','23','333','3.3','per 1,000'],
                   ['2019','Alabama','18-19 years','20.0','100','666','6.6','per 1,000'],
                   ['2019','United States','15-19 years','9.9','999','999','9.9','per 1,000']]
        self.write()
    def tearDown(self):self.temp.cleanup()
    def write(self):
        with self.path.open('w',newline='')as h:
            w=csv.writer(h);w.writerow(['year','state','age_years','state_rate','state_births','u_s_births','u_s_birth_rate','unit']);w.writerows(self.rows)
    def req(self,measure='birth_rate',age='15-19',years=(2019,),units=('AL',),**opts):
        return ConnectorRequest({'connector':'cdc_natality','paths':['cells.csv'],'measure_id':measure,**opts},
                                {'age_group':age},years,units,'annual',self.root)
    def test_counts_rates_and_national_totals_are_distinct(self):
        counts=cdc_natality(self.req('births_count'));rates=cdc_natality(self.req())
        self.assertEqual(123,counts.rows[0]['outcome_value'])
        self.assertEqual(12.5,rates.rows[0]['outcome_value'])
        self.assertEqual(1,len(counts.rows));self.assertEqual('maternal_residence_state',counts.rows[0]['geography'])
        self.assertEqual('state_births',counts.details['value_field'])
        self.assertEqual(5.0,cdc_natality(self.req(age='15-17')).rows[0]['outcome_value'])
        with self.assertRaisesRegex(ConnectorError,'counts and rates'):
            cdc_natality(self.req('births_count',value_field='state_rate'))
    def test_suppression_missing_state_and_unpublished_year_never_zero(self):
        self.rows[0][3]='*';self.write();p=cdc_natality(self.req(years=(2019,2023),units=('AL','AK')))
        self.assertTrue(all(r['outcome_value']=='' for r in p.rows))
        self.assertTrue(all(r['available']==0 for r in p.metadata))
        with self.assertRaisesRegex(ConnectorNotReady,'1990–2019'):
            natality_download_plan(self.req(years=(2023,)))
    def test_duplicate_bin_units_and_numeric_fail_closed(self):
        self.rows.append(self.rows[0][:]);self.write()
        with self.assertRaisesRegex(ConnectorError,'Duplicate'):cdc_natality(self.req())
        self.rows.pop();self.rows[0][7]='percent';self.write()
        with self.assertRaisesRegex(ConnectorError,'denominator'):cdc_natality(self.req())
        self.rows[0][7]='per 1,000';self.rows[0][4]='123.5';self.write()
        with self.assertRaisesRegex(ConnectorError,'integer'):cdc_natality(self.req('births_count'))
    def test_unsupported_ages_and_credential_free_public_plan(self):
        with self.assertRaisesRegex(ConnectorNotReady,'first-order outcome unavailable'):
            cdc_natality(self.req(age='18-44'))
        with self.assertRaisesRegex(ConnectorNotReady,'first-order outcome unavailable'):
            cdc_natality(self.req(age=['15-17','18-19']))
        plan=natality_download_plan(self.req())[0]
        self.assertIn('https://data.cdc.gov/resource/y268-sna3.csv?',plan['url'])
        self.assertNotIn('key=',plan['url']);self.assertEqual('USGOV_WORKS',plan['license'].split('(')[1].split(')')[0].split()[-1])
    def test_real_source_cell_availability_without_association(self):
        real=Path(__file__).resolve().parents[3]/'data/l2_pipeline/first_order_connectors/natality/nchs_state_teen_births_1990_2019.csv'
        if not real.exists():self.skipTest('Actual free download absent')
        req=ConnectorRequest({'connector':'cdc_natality','paths':[str(real)],'measure_id':'birth_rate'},
                             {'age_group':'15-19'},(1990,2019),('AL','AK','CA','DC'),'annual',real.parent)
        panel=cdc_natality(req)
        self.assertEqual(8,len(panel.rows));self.assertTrue(all(m['available']for m in panel.metadata))
        self.assertEqual(list(range(1990,2020)),panel.details['published_years'])
        self.assertEqual(0,panel.details['outcome_association_calls'])

if __name__=='__main__':unittest.main()
