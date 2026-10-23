"""C56 synthetic contracts and metadata checks; no real outcome fits."""
import copy,csv,json,tempfile,unittest
from pathlib import Path
from scripts.l2_pipeline.contract_card import registered_contract_card
from scripts.l2_pipeline.connectors import outcome_v10,outcome_v11,population_v63,c56_qcew
from scripts.l2_pipeline.connectors.c56_natality import state_rows,validate_ocr,numeric
from scripts.l2_pipeline.connectors.common import STATES,ConnectorRequest
from scripts.l2_pipeline.agents.jobs import emit_job
from scripts.l2_pipeline.agents.replacement import emit as replacement
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import ensure_once,sha
from scripts.l2_pipeline.tests.test_pipeline import card
ROOT=Path(__file__).resolve().parents[3]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        c=card();c['analysis'].update(controls='never_treated',screen_expected_support={'treated_n':12})
        cp=ensure_once(self.root/'card.json',c);self.ctx=StageContext(load_card(cp),self.root/'work',ROOT)
        spec={k:copy.deepcopy(c[k]) for k in ['design_id','version','track','mechanism','analysis','outcome','population']}
        spec['analysis']['controls']='not_yet_treated';spec['comparison_group']='not_yet_treated'
        ensure_once(self.ctx.design_dir/'SPEC.json',spec)
        ensure_once(self.ctx.design_dir/'SUPPORT.json',dict(primary_passed=True,variants=[dict(variant='primary',
            comparison_group='not_yet_treated',retained_treated=['u'+str(i) for i in range(18)],passed=True)]))

    def test_forecast_draft_and_sealed_use_registered_sources(self):
        for stage,role,sealed in [('S5','Card',False),('S6','reviewer',False),('S6','reviewer',True)]:
            if sealed:ensure_once(self.ctx.design_dir/'FREEZE_FINAL.json',{'invented_candidate':True})
            job=emit_job(self.ctx,stage,role,jobs_root=self.root/('sealed' if sealed else stage),allow_draft=True)
            c=job.contract['card'];self.assertEqual(c['analysis']['controls'],'not_yet_treated')
            self.assertNotIn('screen_expected_support',c['analysis'])
            self.assertEqual(len(c['support']['variants'][0]['retained_treated']),18)
            self.assertEqual(job.contract['card_sources']['SPEC.json'],sha(self.ctx.design_dir/'SPEC.json'))
            # Above REVIEW_PROMPT_CHAR_LIMIT (S6) jobs.emit_job moves the contract into the hash-bound .review-inputs.json; read both.
            text=Path(job.job_path).read_text();transport=Path(job.job_path).with_suffix('.review-inputs.json')
            if transport.exists():text+=transport.read_text()
            self.assertIn('not_yet_treated',text)

    def test_replacement_derives_card_and_checks_original_registered_hash(self):
        job=emit_job(self.ctx,'S5','Card',jobs_root=self.root/'jobs')
        new=replacement(job.job_path,self.root/'replacement','c56')
        self.assertEqual(new.contract['card'],registered_contract_card(self.ctx.design_dir))
        (self.ctx.design_dir/'SUPPORT.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed'):replacement(job.job_path,self.root/'replacement2','c56b')

    def test_spec_support_mismatch_fails_closed(self):
        path=self.ctx.design_dir/'SUPPORT.json';s=json.loads(path.read_text());s['variants'][0]['comparison_group']='never_treated';path.write_text(json.dumps(s))
        with self.assertRaisesRegex(ValueError,'SPEC/SUPPORT'):registered_contract_card(self.ctx.design_dir)


class NatalityTests(unittest.TestCase):
    def fixture(self):
        r={u:dict(count=100,birth_rate=12.,fertility_rate=60.) for u in STATES.values()};r['US']=dict(count=5100)
        return r

    def test_count_reconciliation_and_neighbours_required_for_each_state(self):
        r=self.fixture();good,flags,check=validate_ocr(r,{1993:r,1995:r},1994)
        self.assertEqual(len(good),51);self.assertFalse(flags);self.assertTrue(check['state_count_sum_pass'])
        broken=copy.deepcopy(r);broken['US']['count']=5200
        good,flags,_=validate_ocr(broken,{1993:r},1994);self.assertFalse(good);self.assertEqual(len(flags),51)
        broken=copy.deepcopy(r);broken['AL']['fertility_rate']=84.6
        good,flags,_=validate_ocr(broken,{1993:r},1994);self.assertEqual(len(good),50);self.assertIn('AL',flags)
        good,flags,_=validate_ocr(r,{},1994);self.assertFalse(good)

    def test_layout_wrapping_footnote_and_ocr_digits(self):
        r=state_rows('United States 2 .... 4,000,000 15.2 66.7\nNewYork.... 12.3 60.1\nFlorida\n . . . 12.0 61.7')
        self.assertEqual(r['US'][0],'4,000,000');self.assertEqual(r['NY'][0],'12.3');self.assertEqual(r['FL'][0],'12.0')
        self.assertIsNone(numeric('4,15S,212',count=True))

    def test_real_coverage_flags_and_splice_remain_value_free(self):
        c=outcome_v11.catalog(verify_sources=False)
        for sid in ['nchs_state_birth_rate','nchs_summary_fertility_rate']:
            spec=c['series'][sid];self.assertEqual(spec['years'],list(range(1990,2025)))
            support=json.loads((ROOT/spec['support_artifact']['path']).read_text())
            self.assertFalse(support['outcome_values_in_sidecar'])
            self.assertTrue(all(m['available']==0 and m['missing_reason']=='image_only_no_installed_OCR' for m in support['metadata'] if m['year']==1995))
            self.assertEqual(support['coverage']['by_year']['2019']['available'],51)
            self.assertFalse(any('outcome_value' in m for m in support['metadata']))


class CatalogTests(unittest.TestCase):
    def test_v11_inherits_old_bytes_except_explicit_gfr_splice(self):
        old=outcome_v10.catalog(verify_sources=False);new=outcome_v11.catalog()
        self.assertTrue(set(old['series'])<=set(new['series']))
        for sid,s in old['series'].items():
            if sid!='nchs_summary_fertility_rate':self.assertEqual(dict(s,registered_connector='outcome_v11_'+sid),new['series'][sid])
        # C56's metadata remains pinned even after the current catalog advances.
        self.assertEqual(population_v63.catalog(source=new,path=outcome_v11.CATALOG_PATH.with_name('OUTCOME_POPULATIONS_V11.json'))[1]['catalog_id'],'first_order_outcomes_v11')

    def test_eia_measures_and_missing_2025_population(self):
        c=outcome_v11.catalog(verify_sources=False)
        for sid in ['eia_retail_price_all_sectors','eia_retail_price_residential','eia_nonhydro_renewable_generation_share']:
            s=c['series'][sid];self.assertEqual(s['years'],list(range(1990,2026)));self.assertEqual(s['observed_coverage']['usable_cells'],51*36)
        self.assertEqual(c['series']['eia_retail_sales_per_capita']['years'][-1],2024)
        self.assertEqual(c['series']['eia_electric_power_co2_per_capita']['years'][-1],2024)

    def test_wage_population_selection_for_both_new_cards(self):
        from scripts.l2_pipeline.population_resolution import choose
        for did in ['b1011tl_noncompete_lowwage_ban','b1011tl_pay_range_default_disclosure']:
            c=json.loads((ROOT/'data/l2_pipeline/intake/bulk_20261011/tax_ins_labor/cards'/(did+'.json')).read_text())
            p=dict(status='VERIFIED',target_outcome='wage_level',target_construct='private_sector_weekly_wage',
                target_population_metadata=dict(ages=[16,120],restrictions={}))
            resolution=choose(c,p);self.assertEqual(resolution['selected']['series_id'],'bls_qcew_private_weekly_wage')
            self.assertEqual(resolution['outcome_values_read'],0)

    def test_child_day_care_exact_industry_and_suppression(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'qcew_2024.csv'
            fields=['area_fips','own_code','industry_code','size_code','year','qtr','disclosure_code','annual_avg_emplvl']
            with p.open('w') as f:
                w=csv.writer(f);w.writerow(fields);w.writerows([['01000','5','6244','0','2024','A','','123'],['02000','5','6244','0','2024','A','N','999'],['01000','5','10','0','2024','A','','99999']])
            req=ConnectorRequest(dict(paths=[p.name],source_sha256={p.name:sha(p)},series_id=c56_qcew.SID,measure_id=c56_qcew.SID,
                population_filters={'ownership':'private','industry':'6244'},source_url='https://www.bls.gov/cew/'),
                {'ownership':'private','industry':'6244'},(2024,),('AL','AK'),'annual',root)
            panel=c56_qcew.construct(req);rows={r['unit']:r for r in panel.rows};meta={r['unit']:r for r in panel.metadata}
            self.assertEqual(float(rows['AL']['outcome_value']),123)
            self.assertFalse(meta['AK']['available'])

if __name__=='__main__':unittest.main()
