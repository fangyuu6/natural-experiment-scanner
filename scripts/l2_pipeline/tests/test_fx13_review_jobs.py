"""Draft review binds secondary support without exposing outcome values."""
import tempfile
import unittest
from pathlib import Path

from scripts.l2_pipeline.agents.jobs import emit_job, load_job
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.storage import write_once
from scripts.l2_pipeline.tests.test_pipeline import card, REPO


class SecondaryReviewJobs(unittest.TestCase):
    def test_secondary_inventory_and_support_are_bound_and_values_forbidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ctx = StageContext(load_card(write_once(root/'card.json', card())), root/'work', REPO)
            write_once(ctx.design_dir/'SPEC.json', {'secondary_outcomes': ['invented_secondary']})
            inventory = write_once(ctx.design_dir/'SECONDARY_PANELS.json', {'secondaries': []})
            secondary = ctx.design_dir/'secondary_panels'/'invented_secondary'
            support = write_once(secondary/'SUPPORT.json', {'support_pass': False, 'reason': '4<6'})
            construction = write_once(secondary/'CONSTRUCTION.json', {'invented': True})
            metadata = secondary/'metadata.csv'
            metadata.write_text('unit,year,available\nAA,2000,0\n')
            values = secondary/'panel.csv'
            values.write_text('DO NOT READ INVENTED OUTCOME VALUES\n')
            job = emit_job(ctx, 'S6', 'reviewer', allow_draft=True,
                           jobs_root=root/'jobs', attempt_id='fx13_test')
            bound = {p['path'] for p in job.inputs}
            for path in (inventory, support, construction):
                self.assertIn(str(path), bound)
                self.assertIn(str(path), job.contract['permitted_metadata_paths'])
            self.assertIn(str(metadata), job.contract['permitted_metadata_paths'])
            self.assertIn(str(values), job.contract['forbidden_outcome_paths'])
            self.assertNotIn(str(values), bound)
            self.assertNotIn('DO NOT READ INVENTED OUTCOME VALUES', Path(job.job_path).read_text())
            self.assertEqual(load_job(Path(job.job_path)), job)
            support.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Agent input changed'):
                load_job(Path(job.job_path))
