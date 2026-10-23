"""Offline CDC/NCHS schema, population, time and receipt integrity checks."""
import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import types
import sys

from .common import ConnectorError, ConnectorNotReady, ConnectorRequest, NAMES
from .outcome_v2_cdc import acquire, build, _pdf_tables, SERIES


class CDCOutcomesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def request(self, series, data, *, years=(2024,), units=('CA','NY'), filters=None, frequency='annual'):
        path = self.root / 'source.csv'
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
        return ConnectorRequest({'series_id':series,'paths':[path.name]}, filters or {}, years,units,frequency,self.root)

    def vsrr(self, **changes):
        return dict(state='CA',state_name='California',year='2024',month='December',period='12 month-ending',
                    indicator='Number of Drug Overdose Deaths',data_value='100',predicted_value='110',footnote='Provisional',**changes)

    def test_vsrr_december_and_new_york_components(self):
        data=[self.vsrr(),dict(self.vsrr(),month='January',data_value='900'),
              dict(self.vsrr(),state='NY',state_name='New York',data_value='20'),
              dict(self.vsrr(),state='YC',state_name='New York City',data_value='30')]
        panel=build(self.request('cdc_vsrr_overdose_deaths_reported',data))
        self.assertEqual([r['outcome_value'] for r in panel.rows],[100,50])
        self.assertEqual(panel.rows[1]['nyc_combined'],1)
        self.assertEqual(panel.rows[0]['geography'],'state_of_occurrence')
        self.assertEqual(panel.inventory[0]['size_bytes'],(self.root/'source.csv').stat().st_size)
        self.assertEqual(len(panel.inventory[0]['sha256']),64)

    def test_vsrr_monthly_is_rolling_not_monthly_flow(self):
        panel=build(self.request('cdc_vsrr_overdose_deaths_reported',[self.vsrr()],units=('CA',),frequency='monthly'))
        self.assertEqual(len(panel.rows),12)
        self.assertIsNone(panel.rows[0]['outcome_value'])
        self.assertEqual(panel.rows[-1]['outcome_value'],100)
        self.assertEqual(panel.metadata[-1]['annual_usable'],0)
        self.assertEqual(panel.rows[-1]['period_semantics'],'12 month-ending')

    def test_vsrr_missing_nyc_component_is_not_remainder_of_state(self):
        panel=build(self.request('cdc_vsrr_overdose_deaths_reported',
                                [dict(self.vsrr(),state='NY',state_name='New York')],units=('NY',)))
        self.assertIsNone(panel.rows[0]['outcome_value'])
        self.assertEqual(panel.rows[0]['published_cell_present'],0)

    def test_vsrr_suppressed_component_and_predicted_binding(self):
        panel=build(self.request('cdc_vsrr_overdose_deaths_predicted',
                                [dict(self.vsrr(),footnote='Data suppressed (<10).')],units=('CA',)))
        self.assertIsNone(panel.rows[0]['outcome_value'])
        request=self.request('cdc_vsrr_overdose_deaths_predicted',[self.vsrr()],units=('CA',))
        self.assertEqual(build(request).rows[0]['outcome_value'],110)
        with self.assertRaises(ConnectorError):
            build(replace(request,options=dict(request.options,value_field='data_value')))
        request=self.request('cdc_vsrr_overdose_deaths_predicted',[dict(self.vsrr(),predicted_value='110.5')],units=('CA',))
        self.assertEqual(build(request).rows[0]['outcome_value'],110.5)

    def test_duplicate_and_period_type_rejected(self):
        with self.assertRaises(ConnectorError):
            build(self.request('cdc_vsrr_overdose_deaths_reported',[self.vsrr(),self.vsrr()]))
        with self.assertRaises(ConnectorError):
            build(self.request('cdc_vsrr_overdose_deaths_reported',[dict(self.vsrr(),period='Month')]))

    def test_receipt_symlink_rejected_before_read(self):
        request=self.request('cdc_vsrr_overdose_deaths_reported',[self.vsrr()],units=('CA',))
        target=self.root/'harmless_fixture.json'
        target.write_text('{"fixture":"must remain unread"}')
        (self.root/'source.csv.receipt.json').symlink_to(target)
        with patch.object(Path,'read_text',side_effect=AssertionError('No receipt read allowed')):
            with self.assertRaisesRegex(ConnectorError,'cannot be symlinks'):
                build(request)

    def test_firearm_ttm_never_becomes_annual_and_missing_not_zero(self):
        data=[dict(geoid='06',name='California',intent='FA_Deaths',period='2024',count_sup='1-9',rate=''),
              dict(geoid='06',name='California',intent='FA_Deaths',period='TTM',count_sup='99',rate='1.5')]
        panel=build(self.request('cdc_firearm_deaths',data))
        self.assertTrue(all(r['outcome_value'] is None for r in panel.rows))
        self.assertEqual(panel.metadata[0]['published_cell_present'],1)
        self.assertEqual(panel.metadata[1]['published_cell_present'],0)
        data[0]['name']='New York'
        with self.assertRaises(ConnectorError): build(self.request('cdc_firearm_deaths',data))
        data[0].update(name='California',rate='-999')
        self.assertIsNone(build(self.request('cdc_firearm_crude_rate',data,units=('CA',))).rows[0]['outcome_value'])

    def test_leading_cause_exact_selection_and_legacy_coverage(self):
        data=[dict(state='California',year='2017',cause_name='Suicide',deaths='10',aadr='3.1'),
              dict(state='California',year='2017',cause_name='All causes',deaths='20',aadr='9.0')]
        panel=build(self.request('cdc_leading_cause_age_adjusted_rate',data,years=(2017,2018),units=('CA',),filters={'cause_name':'Suicide'}))
        self.assertEqual([r['outcome_value'] for r in panel.rows],[3.1,None])
        with self.assertRaises(ConnectorNotReady):
            build(self.request('cdc_leading_cause_deaths',data,filters={'cause_name':'Firearm'}))

    def test_teen_births_exact_age_and_rate_units(self):
        data=[dict(state='California',year='2019',age_years='15-19 years',state_births='100',state_rate='4.5',unit='per 1,000'),
              dict(state='California',year='2019',age_years='15-17 years',state_births='50',state_rate='2.0',unit='per 1,000')]
        panel=build(self.request('cdc_teen_birth_rate',data,years=(2019,),units=('CA',),filters={'age_group':'15-19'}))
        self.assertEqual(panel.rows[0]['outcome_value'],4.5)
        with self.assertRaises(ConnectorNotReady):
            build(self.request('cdc_teen_birth_rate',data,filters={'age_group':'18-24'}))

    def fake_pdf(self, texts):
        pages=[types.SimpleNamespace(extract_text=lambda t=t:t) for t in texts]
        return patch.dict(sys.modules,{'pypdf':types.SimpleNamespace(PdfReader=lambda _: types.SimpleNamespace(pages=pages))})

    def table(self, *, bad_columns=False):
        header=('Table 8. Birth rates, by age of mother: United States, each state and territory, 2023\n'
                '[By place of residence] Fertility rate\n')
        lines=[n+' . . . '+('10.7 54.5 1,621.0 * 13.1 5.5 24.6 57.7 91.0 94.3 54.3 12.5 1.1') for n in NAMES]
        if bad_columns:lines[0]=lines[0].rsplit(' ',1)[0]
        return header+'\n'.join(lines)

    def test_pdf_state_table_and_suppression_validated(self):
        with self.fake_pdf([self.table()]):
            table=_pdf_tables(self.root/'source.pdf')
        self.assertEqual(len(table),51)
        self.assertEqual(table['CA',2023]['numbers'][7],57.7)
        self.assertIsNone(table['CA',2023]['numbers'][3])
        self.assertEqual(table['CA',2023]['source_pdf_page'],1)

    def test_pdf_wrapped_state_label_and_alaska_native_header(self):
        text=self.table().replace('Nebraska . . . ', 'Nebraska\n . . . ')
        text=text.replace('[By place of residence] Fertility rate', '[By place of residence] Fertility rate\nAlaska Native Asian Black')
        with self.fake_pdf([text]):
            self.assertEqual(len(_pdf_tables(self.root/'source.pdf')),51)

    def test_pdf_missing_state_column_or_national_only_rejected(self):
        for text in [self.table(bad_columns=True), self.table().replace('Wyoming','Not a state'),
                     'Table 2. Birth rates, by age of mother: United States, 2023']:
            with self.fake_pdf([text]),self.assertRaises(ConnectorError):_pdf_tables(self.root/'source.pdf')

    def test_acquire_reproducible_keyless_plan_and_receipt(self):
        class Client:
            def __init__(self):self.calls=[]
            def fetch(self,url,path,license,license_url):
                self.calls.append((url,path,license,license_url));path.parent.mkdir(exist_ok=True)
                path.write_text('year,state\n2024,California\n');return {'path':str(path),'url':url}
        client=Client(); receipts=acquire('cdc_firearm_deaths',[2024],self.root,client)
        self.assertEqual(len(receipts),1)
        self.assertIn('FA_Deaths',client.calls[0][0]);self.assertNotIn('app_token',client.calls[0][0])
        self.assertEqual(client.calls[0][2],SERIES['cdc_firearm_deaths']['license'])
        with self.assertRaises(ConnectorNotReady):acquire('cdc_firearm_deaths',[2018],self.root,client)


if __name__=='__main__':unittest.main()
