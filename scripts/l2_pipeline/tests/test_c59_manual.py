import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from scripts.l2_pipeline.manual_evidence import supplement,load
from scripts.l2_pipeline.storage import sha
from scripts.l2_pipeline.c42 import exclusion_scope
from scripts.l2_pipeline.stages.s1_treatment import reconcile


class ManualEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(unit='AL',status='treated',start_period=2016,best_evidence_start_year=2015,
            mechanism='predicate',locator='secondary',source_id='old',confidence='high',evidence_status='known_start',evidence_refs=[])
        self.value=dict(records=[self.row],sources=[dict(source_id='old',url='https://example.org',source_type='secondary')])
        self.entry=dict(state='AL',effective_date='2015-06-05',quote='immunity',effective_date_quote='immediately',
            statute='section 2',url='https://state.gov/act',official_complete=True,notes=['official summary says June 8'])
        self.packet=dict(entries={'AL':self.entry},review={},bindings=[],predicate='predicate')
    def test_support_is_additive_and_keeps_date_conflict(self):
        old=copy.deepcopy(self.value);value,report=supplement(self.value,self.packet)
        self.assertEqual(self.value,old);self.assertEqual(value['records'][0]['start_period'],2016)
        self.assertEqual(value['records'][0]['evidence_refs'][0]['effective_date'],'2015-06-05')
        self.assertIn('June 8',report['states'][0]['open_issues'][0]);self.assertTrue(report['fresh_v3_required'])
    def test_disagreement_excludes_both_arms(self):
        self.row['start_period']=2015
        a,_=supplement(self.value,self.packet);b=copy.deepcopy(self.value)
        c=reconcile(a['records'],b['records'],['AL']);cfg=exclusion_scope({'universe':['AL']},c,[a,b])
        self.assertEqual(cfg['excluded_units'],['AL'])
        self.assertIn('A:manual_evidence_clock_conflict',cfg['excluded_conflict_state_reasons']['AL'])
    def test_exact_date_disagreement_not_hidden_by_same_year(self):
        self.row['effective_date']='2015-06-08'
        v,_=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['manual_evidence_conflict'],'manual_evidence_clock_conflict')
    def test_unknown_cannot_be_promoted(self):
        self.row.update(status='unknown',start_period=None)
        v,r=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['status'],'unknown');self.assertEqual(r['states'][0]['action'],'retained_unknown')
    def test_official_gap_requires_independent_locators(self):
        self.entry['official_complete']=False
        v,_=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['manual_evidence_conflict'],'official_evidence_gap')
        self.value['sources'][0]['source_type']='official'
        self.row['evidence_refs']=[dict(source_id='old',locator='s2',quote='text')]
        v,_=supplement(self.value,self.packet);self.assertEqual(v['records'][0]['status'],'treated')
    def test_predicate_ruling_not_silently_decided(self):
        self.packet['review']={'states':{'AL':{'decision':'NEEDS_RULING','readings_verbatim':['reading1','reading2']}}}
        v,r=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['status'],'unknown');self.assertEqual(v['records'][0]['disposition'],'NEEDS_RULING')
        self.assertEqual(r['states'][0]['predicate_review']['readings_verbatim'],['reading1','reading2'])
    def test_excluded_treated_never_becomes_control(self):
        self.packet['review']={'states':{'AL':{'decision':'exclude'}}}
        v,_=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['manual_evidence_conflict'],'predicate_excluded');self.assertNotEqual(v['records'][0]['status'],'never_treated')
    def test_hash_and_predicate_bound(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)/'data/l2_pipeline/autopilot/test/manual_evidence';base.mkdir(parents=True)
            raw=base/'official.txt';raw.write_text('source')
            entry=dict(self.entry,sources=[dict(file='official.txt',sha256=sha(raw),official_host=True)])
            (base/'AL.json').write_text(json.dumps(entry));(base/'HANDOFF.json').write_text(json.dumps(dict(state_records=['AL.json'],completed_states=['AL'])))
            (base/'PREDICATE_REVIEW_C59.json').write_text(json.dumps(dict(predicate='predicate')))
            p=load(d,'test','predicate');self.assertEqual(len(p['bindings']),4)
            with self.assertRaisesRegex(ValueError,'LOCKED'):load(d,'test','different')
            raw.write_text('changed')
            with self.assertRaisesRegex(ValueError,'bytes changed'):load(d,'test','predicate')
    def test_no_packet_does_not_change_legacy_bytes(self):
        v,r=supplement(self.value,None);self.assertIs(v,self.value);self.assertIsNone(r)
    def test_official_adoption_does_not_silently_support_absence(self):
        self.row.update(status='never_treated',start_period=None)
        v,_=supplement(self.value,self.packet)
        self.assertEqual(v['records'][0]['manual_evidence_conflict'],'manual_evidence_status_conflict')

    def test_no_ab_override(self):
        a,_=supplement(self.value,self.packet)
        b=copy.deepcopy(self.value);b['records'][0].update(status='unknown',start_period=None)
        b,_=supplement(b,self.packet)
        c=reconcile(a['records'],b['records'],['AL']);self.assertEqual(c['accepted'],[])


class FiscalScaleTests(unittest.TestCase):
    def test_monetary_death_tax_is_not_a_death_count(self):
        from scripts.l2_pipeline.connectors.rates import count_kind
        self.assertIsNone(count_kind(dict(series_id='death_and_gift_tax',outcome_unit='USD per capita')))
        self.assertIsNone(count_kind(dict(series_id='building_permits_count',outcome_unit='permits per 1,000 population')))
        self.assertEqual(count_kind(dict(series_id='overdose_deaths',outcome_unit='deaths')),'deaths')

from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests
class ManualBridgeTests(OfficialBridgeTests):
    def test_lock_binds_manual_bytes_and_v3_still_required(self):
        from unittest.mock import patch
        packet=dict(entries={},review={},bindings=[dict(path=str(self.path),sha256=sha(self.path))],predicate='one')
        with patch('scripts.l2_pipeline.manual_evidence.load',return_value=packet):job=self.prepare()
        lock=json.loads((self.ctx.design_dir/'CODING_LOCK.json').read_text())
        self.assertEqual(lock['manual_evidence']['bindings'],packet['bindings'])
        self.assertEqual(len(lock['files']),2);self.assertEqual(len(job.contract['sample']),10)
        self.assertFalse((self.ctx.design_dir/'TREATMENT.json').exists())
        self.assertFalse(job.contract['s1_ready'])
for name in dir(OfficialBridgeTests):
    if name.startswith('test_') and name not in ManualBridgeTests.__dict__:setattr(ManualBridgeTests,name,None)
del OfficialBridgeTests,name

if __name__=='__main__':unittest.main()
