"""Tracker intake cannot silently fall back to duplicate blind coders."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents.jobs import emit_job, emit_stage_jobs
from scripts.l2_pipeline.cards import load_card
from scripts.l2_pipeline.contracts import StageContext
from scripts.l2_pipeline.stages.s1_treatment import run
from scripts.l2_pipeline.storage import ensure_once
from scripts.l2_pipeline.tests.test_pipeline import card


class TrackerRoutingTests(unittest.TestCase):
    def test_s1_and_job_entry_points_use_tracker_audit_route(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            value = card()
            value['treatment']['source_type'] = 'policy_tracker'
            ctx = StageContext(load_card(ensure_once(root / 'card.json', value)), root / 'work', root)
            sentinel = object()
            with patch('scripts.l2_pipeline.policy_tracker_intake.run', return_value=sentinel) as routed:
                self.assertIs(run(ctx), sentinel)
                routed.assert_called_once_with(ctx)
            with patch('scripts.l2_pipeline.policy_tracker_intake.emit_auditor', return_value=sentinel):
                self.assertEqual(emit_stage_jobs(ctx, 'S1'), [sentinel])
                self.assertIs(emit_job(ctx, 'S1', 'auditor'), sentinel)
            for role in ('A', 'B', 'third_reader'):
                with self.subTest(role=role), self.assertRaisesRegex(ValueError, 'A/B duplication'):
                    emit_job(ctx, 'S1', role)
                with self.subTest(role=role), self.assertRaisesRegex(ValueError, 'A/B duplication'):
                    emit_stage_jobs(ctx, 'S1', roles=[role])


if __name__ == '__main__':
    unittest.main()
