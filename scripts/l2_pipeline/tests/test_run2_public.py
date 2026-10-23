"""Run2 source construction contracts, without live requests or outcome fits."""
import copy
import json
import tempfile
import unittest
import io
import struct
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady, ConnectorRequest, sha
from scripts.l2_pipeline.connectors.run2_public import apply_outcome_plan, eia_state_sales, qcew_year
from scripts.l2_pipeline.contracts import DesignCard, StageContext


class EiaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        for name in ('sales_annual.xlsx','customers_annual.xlsx'):
            (self.base/name).write_bytes(b'public fixture')
        self.request = ConnectorRequest({'paths':['sales_annual.xlsx','customers_annual.xlsx'],
            'value_field':'residential_electricity_kwh_per_customer'}, {'published_target':'buildings'},
            (2000,2001), ('AL','TX'), 'annual', self.base)
        self.headers = ['Year','State','Industry Sector Category','Residential','Commercial','Industrial','Transportation','Other','Total']

    def tearDown(self):
        self.tmp.cleanup()

    def source(self,path):
        value = 200 if path.name == 'sales_annual.xlsx' else 10
        return 'Official', [['Title'], self.headers, [2000,'AL','Total Electric Industry',value,0,0,0,0,value],
                            [2000,'AL','Investor-Owned',9999,0,0,0,0,9999]]

    def test_explicit_mwh_conversion_and_missing_retention(self):
        with patch('scripts.l2_pipeline.connectors.run2_public.published_sheet',self.source):
            panel=eia_state_sales(self.request)
        self.assertEqual(panel.rows[0]['outcome_value'],20000)
        self.assertEqual(panel.rows[1]['outcome_value'],'')
        self.assertEqual(sum(r['available'] for r in panel.metadata),1)
        self.assertEqual(panel.details['sector'],'Residential')

    def test_zero_denominator_is_missing(self):
        def source(path):
            _,table=self.source(path)
            if path.name == 'customers_annual.xlsx':table[2][3]=0
            return '',table
        with patch('scripts.l2_pipeline.connectors.run2_public.published_sheet',source):
            panel=eia_state_sales(self.request)
        self.assertEqual(panel.rows[0]['outcome_value'],'')

    def test_no_sector_or_solar_proxy(self):
        request=replace(self.request,filters={'published_target':'solar'},options=dict(self.request.options,value_field='small_scale_solar_generation_kwh'))
        with self.assertRaises(ConnectorNotReady):eia_state_sales(request)
        with self.assertRaises(ConnectorNotReady):eia_state_sales(replace(self.request,filters={'published_target':'electricity'}))

    def test_duplicate_provider_total_refused(self):
        def source(path):
            title,table=self.source(path);table.append(copy.deepcopy(table[2]));return title,table
        with patch('scripts.l2_pipeline.connectors.run2_public.published_sheet',source):
            with self.assertRaises(ConnectorError):eia_state_sales(self.request)

    def test_changed_source_hash_refused(self):
        request=replace(self.request,options=dict(self.request.options,source_sha256={'sales_annual.xlsx':'0'*64}))
        with self.assertRaises(ConnectorError):eia_state_sales(request)


class HistoricalArchiveTests(unittest.TestCase):
    def test_public_ranges_reconstruct_exact_crc_checked_industry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            buffer=io.BytesIO()
            raw=b'area_fips,annual_avg_emplvl\n01000,123\n'
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('2001 annual 621111 complete.csv',raw)
            archive=buffer.getvalue()
            class Response:
                headers={'Content-Length':str(len(archive))}
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class Opener:
                def open(self,*args,**kwargs):return Response()
            def acquire(url,path,byte_range=None,**kwargs):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(archive[byte_range[0]:byte_range[1]+1])
                return {'path':str(path),'sha256':sha(path)}
            with patch('scripts.l2_pipeline.connectors.run2_public.OUT',root),patch('scripts.l2_pipeline.connectors.run2_public.acquire',acquire),patch('urllib.request.build_opener',return_value=Opener()):
                _,paths=qcew_year(2001,('621111',))
            self.assertEqual(paths['621111'].read_bytes(),raw)
            proof=json.loads((root/'qcew/2001/621111.extraction.json').read_text())
            self.assertFalse(proof['whole_archive_downloaded'])
            self.assertEqual(proof['derived_sha256'],sha(paths['621111']))

    def test_archive_crc_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_STORED) as z:z.writestr('2001 annual 621111 complete.csv',b'actualdata')
            archive=bytearray(buffer.getvalue())
            central=archive.find(b'PK\x01\x02')
            struct.pack_into('<I',archive,central+16,0)
            class Response:
                headers={'Content-Length':str(len(archive))}
                def __enter__(self):return self
                def __exit__(self,*args):pass
            class Opener:
                def open(self,*args,**kwargs):return Response()
            def acquire(url,path,byte_range=None,**kwargs):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive[byte_range[0]:byte_range[1]+1]);return {'path':str(path)}
            with patch('scripts.l2_pipeline.connectors.run2_public.OUT',root),patch('scripts.l2_pipeline.connectors.run2_public.acquire',acquire),patch('urllib.request.build_opener',return_value=Opener()):
                with self.assertRaisesRegex(ConnectorError,'CRC'):qcew_year(2001,('621111',))


class PlanTests(unittest.TestCase):
    def test_offline_hook_preserves_card_identity_and_binds_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);card_path=repo/'card.json';card_path.write_text('{}')
            payload=dict(design_id='test_design',version='v1',policy_family='test',mechanism='test',
                track='DiD',unit='state',outcome_family='test',outcome={'connector':'eia','value_field':'electricity_sales_kwh_per_customer'})
            ctx=StageContext(DesignCard.from_mapping(payload,card_path),repo/'work',repo)
            root=repo/'data/l2_pipeline/jobs/run2/outcomes/test_design';root.mkdir(parents=True)
            source=repo/'source.csv';source.write_text('data')
            plan={'design_id':'test_design','card_sha256':sha(card_path),'outcome_overrides':{'paths':[str(source)]},'source_files':[{'path':str(source),'sha256':sha(source)}]}
            (root/'OUTCOME_PLAN.json').write_text(json.dumps(plan))
            with patch('urllib.request.urlopen',side_effect=AssertionError('network called')):
                prepared=apply_outcome_plan(ctx)
            self.assertEqual(prepared.card.path,ctx.card.path)
            self.assertEqual(prepared.card.payload['outcome']['value_field'],payload['outcome']['value_field'])
            self.assertNotIn('paths',ctx.card.payload['outcome'])
            self.assertEqual(prepared.card.payload['outcome']['run2_plan_sha256'],sha(root/'OUTCOME_PLAN.json'))
            source.write_text('changed')
            with self.assertRaises(ConnectorError):apply_outcome_plan(ctx)

if __name__=='__main__':unittest.main()
