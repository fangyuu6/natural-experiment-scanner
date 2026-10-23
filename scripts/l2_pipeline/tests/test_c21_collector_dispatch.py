"""C20 representation and intake-install ordering regressions, without models."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents.collect import _locator_text, collect_job
from scripts.l2_pipeline.intake.dispatch import main
from scripts.l2_pipeline.storage import sha


class LocatorTests(unittest.TestCase):
    def test_native_structures_collect_losslessly_without_promoting_s1(self):
        for locator in ([{'url': 'https://official.example/法', 'section': '§ 1'}],
                        {'pages': [1, 2], 'url': 'https://official.example/law'},
                        {'page': 230}, [12, 13]):
            with self.subTest(locator=locator), tempfile.TemporaryDirectory() as folder:
                root=Path(folder); original=root/'audit.json'; promoted=root/'pending.json'
                sample=['U'+str(i) for i in range(8)]
                answer=dict(design_id='fixture', role='auditor', source_sha256='source',
                    selection_sha256='selection', import_sha256='import',
                    blindness_attestation=dict(other_submissions_read=False, outcome_associations_read=False),
                    checks=[dict(unit=u, status='match', official_locator=copy.deepcopy(locator),
                        panel_year=2000, official_operational_calendar_year=2000,
                        official_operational_date='2000-01-01') for u in sample])
                original.write_text(json.dumps(answer, ensure_ascii=False))
                before=sha(original)
                job=SimpleNamespace(stage='S1', design_id='fixture', role='auditor',
                    output_path=str(original), promote_path=str(promoted), job_path='fixture.md',
                    job_sha256='packet', contract=dict(source_sha256='source', selection_sha256='selection',
                        jobs_kind='v8b_calendar_year', audit_target='official_operational_calendar_year',
                        import_sha256='import', sample=sample,
                        sampled_tuples=[dict(unit=u, status='treated', start_period=2000) for u in sample]))
                with patch('scripts.l2_pipeline.agents.collect.load_job', return_value=job):
                    receipt=collect_job('fixture.md')
                self.assertTrue(receipt['validated']); self.assertEqual(sha(original),before)
                value=json.loads(promoted.read_text())
                self.assertFalse(value['s1_ready']); self.assertFalse(value['execution_authorized'])
                for row in value['checks']:
                    self.assertEqual(row['official_locator'],locator)
                    self.assertEqual(json.loads(row['official_locator_canonical_json']),locator)

    def test_empty_and_non_json_locators_are_refused(self):
        for value in (None, '', ' ', [], {}, [False, True], {'url': ' '},
                      {'url': 'law', 'number': float('nan')}, {1: 'law'}, ['law', ('tuple',)]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _locator_text(value)


class DispatchOrderTests(unittest.TestCase):
    def test_install_precedes_gate_and_hub(self):
        root=Path('/private/tmp/c21-order-fixture'); config=SimpleNamespace(root=root)
        for extra in ([], ['--preflight-only'], ['--dry-run']):
            with self.subTest(extra=extra):
                order=[]
                with patch('scripts.l2_pipeline.intake.dispatch.IntakeConfig.from_existing', return_value=config), \
                     patch('scripts.l2_pipeline.intake.dispatch.v8b_calendar.configure', side_effect=lambda _: order.append('configure')), \
                     patch('scripts.l2_pipeline.intake.dispatch.v8b_calendar.install', side_effect=lambda: order.append('install')), \
                     patch('scripts.l2_pipeline.intake.dispatch.fresh_gate', side_effect=lambda *a,**k: order.append('gate')), \
                     patch('scripts.l2_pipeline.hub.main', side_effect=lambda _: order.append('hub') or 0):
                    result=main(['--intake-root',str(root),'dispatch','S1','--jobs-root',
                                 str(root/'fresh_audit_jobs'),'--hub-outside-sandbox','--max-concurrency','4',*extra])
                self.assertEqual(result,0)
                self.assertEqual(order,['configure','install',*(['gate'] if not extra else []),'hub'])

    def test_gate_failure_stops_hub_after_install(self):
        root=Path('/private/tmp/c21-order-fixture'); config=SimpleNamespace(root=root)
        with patch('scripts.l2_pipeline.intake.dispatch.IntakeConfig.from_existing', return_value=config), \
             patch('scripts.l2_pipeline.intake.dispatch.v8b_calendar.configure'), \
             patch('scripts.l2_pipeline.intake.dispatch.v8b_calendar.install') as install, \
             patch('scripts.l2_pipeline.intake.dispatch.fresh_gate', side_effect=ValueError('unconfirmed')), \
             patch('scripts.l2_pipeline.hub.main') as hub:
            with self.assertRaisesRegex(ValueError,'unconfirmed'):
                main(['--intake-root',str(root),'dispatch','S1','--jobs-root',str(root/'fresh_audit_jobs')])
            install.assert_called_once();hub.assert_not_called()


if __name__ == '__main__': unittest.main()
