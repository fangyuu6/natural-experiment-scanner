"""Draft review must be able to inspect locked legal evidence while blind to outcomes."""
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.tests.test_pipeline import card, REPO

class V4ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        c=card();c['outcome']['paths']=[str(self.root/'real_outcomes.csv')]
        p=write_once(self.root/'card.json',c);self.ctx=StageContext(load_card(p),self.root/'work',REPO)
        write_once(self.ctx.design_dir/'SPEC.json',{'draft':'synthetic fixture'})
        write_once(self.ctx.design_dir/'SUPPORT.json',{'metadata_only':True})
        self.names=('RECONCILIATION.json','HUB_RULING.json','CODING_LOCK.json','THIRD_READ.json')
        for name in self.names:write_once(self.ctx.design_dir/name,{'synthetic_legal_metadata':name})
        self.job=emit_job(self.ctx,'S6','reviewer',jobs_root=self.root/'jobs',allow_draft=True)
    def tearDown(self):self.tmp.cleanup()
    def test_locked_legal_material_permitted_and_outcomes_forbidden(self):
        paths=set(self.job.contract['permitted_metadata_paths'])
        for name in self.names:self.assertIn(str(self.ctx.design_dir/name),paths)
        self.assertIn(str(self.root/'real_outcomes.csv'),self.job.contract['forbidden_outcome_paths'])
        self.assertNotIn(str(self.root/'real_outcomes.csv'),paths)
        self.assertFalse(self.job.network_access)
        self.assertFalse(self.job.contract['execution_authorized'])
    def test_changed_ruling_rejects_job_even_when_spec_unchanged(self):
        (self.ctx.design_dir/'HUB_RULING.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'input changed'):load_job(Path(self.job.job_path))

if __name__=='__main__':unittest.main()
