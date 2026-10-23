"""Synthetic screening checks: units, tracks, provenance, secrets and timing."""
import importlib.util
import unittest
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

spec=importlib.util.spec_from_file_location('queue_build',Path(__file__).with_name('build.py'))
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)

class QueueTests(unittest.TestCase):
    def test_wave_distinct_states_not_texts(self):
        self.assertEqual(q.wave_windows([{'state':'CA','year':2020}]*4+[{'state':'NY','year':2021}]),[])
        w=q.wave_windows([{'state':'CA','year':2019},{'state':'NY','year':2020},{'state':'MA','year':2021}])
        self.assertEqual(w,[{'center_year':2020,'states':['CA','MA','NY']}])

    def test_waves_preserve_widely_separated_events(self):
        s=[{'state':x,'year':y} for y in (2000,2020) for x in ('CA','NY','MA')]
        self.assertEqual([w['center_year'] for w in q.wave_windows(s)],[1999,2000,2001,2019,2020,2021])

    def test_wave_can_have_unobserved_middle_year(self):
        s=[{'state':'CA','year':2016},{'state':'NY','year':2016},{'state':'MA','year':2018}]
        self.assertEqual(q.wave_windows(s),[{'center_year':2017,'states':['CA','MA','NY']}])

    def test_measurable_numeric_threshold_not_binary_category(self):
        self.assertTrue(q.measurable_threshold({'running_variable':'number of employees','cutoff':'50'}))
        self.assertFalse(q.measurable_threshold({'running_variable':'union membership','cutoff':'yes'}))
        self.assertFalse(q.measurable_threshold({'running_variable':'income','cutoff':''}))

    def test_new_json_encoded_threshold_and_legacy_field(self):
        self.assertEqual(len(q.thresholds({'all_rd_thresholds':'[{"running_variable":"age","cutoff":65}]'})),1)
        old=q.thresholds({'rd_info':'Variable: annual_income | Cutoff: 20000 | Direction: below_cutoff','rd_quote':'Under $20,000'})[0]
        self.assertEqual(old['running_variable'],'annual_income')
        self.assertEqual(old['cutoff'],'20000')

    def test_rct_does_not_promote_suggested_randomization(self):
        self.assertEqual(q.lexical_evidence({'title':'Tax law'},{'red_flags':'Use a randomized trial','research_angles':'random assignment'}),[])
        hit=q.lexical_evidence({'title':'H-1B registration'},{'did_quote':'USCIS randomly selects registrations.'})
        self.assertEqual(len(hit),1);self.assertFalse(hit[0]['gambling_context'])
        hit=q.lexical_evidence({'title':'Lottery commission'},{'did_quote':'The lottery commission shall sell lottery tickets.'})
        self.assertTrue(hit[0]['gambling_context'])

    def test_secret_queries_and_untrusted_urls(self):
        self.assertEqual(q.safe_url('https://example.org/file?api_key=SECRET#anchor'),'https://example.org/file')
        self.assertEqual(q.safe_url('https://user:SECRET@example.org/file'),'')
        self.assertEqual(q.safe_url('file:///private/secrets'),'')
        self.assertEqual(q.sanitize({'quote':'Read HTTPS://example.org/file?api_key=SECRET'}),{'quote':'Read https://example.org/file'})

    def test_stable_id_and_states(self):
        self.assertEqual(q.stable_id('DiD','a'),q.stable_id('DiD','a'))
        self.assertNotEqual(q.stable_id('DiD','a'),q.stable_id('RDD','a'))
        self.assertEqual(q.state_of('New York State'),'NY')
        self.assertEqual(q.state_of('US Federal'),'')
        self.assertIsNone(q.year_of({'year':'','date':''}))

    def test_mixed_restricted_alias_is_not_public_credit(self):
        base={'domain':'labor_market','evidence_strength':4,'design_id':'a','states':['CA']}
        restricted={**base,'data_alias_verdict':'mixed','source_records':[{'suggested_datasets':'IRS microdata, bespoke data'}]}
        public={**base,'data_alias_verdict':'mixed','source_records':[{'suggested_datasets':'CPS, bespoke data'}]}
        self.assertLess(q.ranking_key(public),q.ranking_key(restricted))

    def test_builder_integration_preserves_unsignaled_wave_and_new_cohort(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            p=Path(tmp);csvpath=p/'input.csv';der=p/'derived.csv';db=p/'extract.db';out=p/'output'
            with csvpath.open('w') as f:
                w=csv.DictWriter(f,fieldnames=['policy_id','jurisdiction','year','date','title','policy_topic','policy_family','did','rd','detail_json']);w.writeheader()
                for st in ('CA','NY','MA'):
                    w.writerow({'policy_id':st,'jurisdiction':st+' State','year':2020,'policy_topic':'labor_market','policy_family':'licensing','did':'0','rd':'0','detail_json':json.dumps({'did_quote':'Licenses shall be recognized.','treatment':'licensed workers','control_group':'other workers','treatment_timing':'2020'})})
            with der.open('w') as f:
                w=csv.DictWriter(f,fieldnames=['policy_id','strict_enacted','data_verdict']);w.writeheader()
                for st in ('CA','NY','MA'):w.writerow({'policy_id':st,'strict_enacted':'1','data_verdict':'public_ok'})
            c=sqlite3.connect(db);c.execute('create table results(policy_id text,chapter_id text,result_json text,policy_topic text,policy_family text,error text,analyzed_at text)')
            d={'title':'Benefit lottery','jurisdiction':'US Federal','intuition_pitch':'Benefits are allocated by lottery.','all_rd_thresholds':[{'running_variable':'age','cutoff':65,'quote':'Eligible at age 65.'}]}
            c.execute('insert into results values(?,?,?,?,?,?,?)',('EXTRA','',json.dumps(d),'public_health','benefit',None,'2026'));c.commit();c.close()
            subprocess.run([sys.executable,str(Path(__file__).with_name('build.py')),'--csv',str(csvpath),'--derived',str(der),'--db',str(db),'--output',str(out)],check=True,stdout=subprocess.PIPE,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            summary=json.loads((out/'summary.json').read_text())
            self.assertEqual(summary['did_family_designs'],1)
            self.assertEqual(summary['unique_source_texts_all_cohorts'],4)
            self.assertEqual(summary['counts']['RDD']['05_queued_source_records']['total'],1)
            self.assertEqual(summary['counts']['RCT']['02_non_gambling_allocation_mentions']['total'],1)
            wave=json.loads((out/'did_designs.jsonl').read_text())
            self.assertEqual(wave['source_record_count'],3)

if __name__=='__main__':unittest.main()
