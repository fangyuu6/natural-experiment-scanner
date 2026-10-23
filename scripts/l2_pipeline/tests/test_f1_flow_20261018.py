"""F1-flow staging (HUB_RULING_20261018_F1_FLOW): faster dead-worker reaping with a six-lease cap, and the retrospective F1 path. Synthetic data only."""
import json
import tempfile
import unittest
from pathlib import Path
from scripts.l2_pipeline.agents import purpose_direction as d, purpose_direction_retro as retro, sonnet_queue as q
from scripts.l2_pipeline.tests import test_agents as fixtures
from scripts.l2_pipeline.agents.contract_tests.packets import _context
from scripts.l2_pipeline.storage import ensure_once


class DeadWorkerReaping(unittest.TestCase):
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown
    emit = fixtures.AgentTests.emit

    def packet(self):
        job = self.emit('S5', 'Card')
        root = self.root / 'queue'
        return job, root, q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='invented'), root)

    def test_constants(self):
        self.assertEqual((q.DEAD_SECONDS, q.DEAD_EXPIRY_CAP, q.LEASE_SECONDS), (20 * 60, 6, 90 * 60))

    def test_worker_that_heartbeated_once_then_died_is_reaped_after_20_minutes_not_90(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        q.heartbeat(root, r['job_id'], lease['token'], now=1000 + 180)   # the real pattern: one heartbeat a few minutes after the claim
        self.assertEqual(q.reap(root, now=1180 + q.DEAD_SECONDS - 1), [])
        self.assertEqual([e['status'] for e in q.reap(root, now=1180 + q.DEAD_SECONDS)], ['pending'])

    def test_a_live_worker_that_keeps_heartbeating_is_never_reaped(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        for k in range(1, 40):   # 200 minutes of a 5-minute heartbeat
            q.heartbeat(root, r['job_id'], lease['token'], now=1000 + k * 300)
            self.assertEqual(q.reap(root, now=1000 + k * 300 + 299), [])

    def test_five_lost_workers_keep_the_packet_in_the_queue_and_the_sixth_goes_to_the_hub(self):
        job, root, r = self.packet()
        statuses = []
        for k in range(6):
            lease = q.claim(root, 'w%d' % k, now=k * 10000)
            q.heartbeat(root, r['job_id'], lease['token'], now=k * 10000 + 120)
            statuses += [e['status'] for e in q.reap(root, now=k * 10000 + 120 + q.DEAD_SECONDS)]
        self.assertEqual(statuses, ['pending'] * 5 + ['hub'])
        self.assertEqual(q.hub_reason(root / 'hub' / r['job_id']), 'Sonnet lease expired 6 times')

    def test_the_hub_reason_text_stays_reopenable_by_release_recovery(self):
        from scripts.l2_pipeline import release_recovery
        self.assertTrue(release_recovery.REASON.search('Sonnet lease expired 6 times'))

    def test_a_packet_that_holds_a_submission_still_goes_to_the_hub_at_the_second_expiry(self):
        job, root, r = self.packet()
        q.claim(root, 'a', now=1000)
        self.assertEqual([e['status'] for e in q.reap(root, now=1000 + q.SILENT_SECONDS)], ['pending'])
        q.claim(root, 'b', now=20000)
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text('{}')
        self.assertEqual([e['status'] for e in q.reap(root, now=20000 + q.SILENT_SECONDS)], ['hub'])

    def test_worker_text_names_the_20_minute_rule(self):
        self.assertIn('Silence of 20 min ends a lease', q.WORKER_INSTRUCTIONS)
        self.assertNotIn('90 minutes', q.WORKER_INSTRUCTIONS)


class RetroDirection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ctx = _context(self.root, 'retro_direction')
        self.ctx.card.payload['first_order']['statute_refs'] = [dict(source_ref='https://example.invalid', locator='s1')]
        self.dir = retro.retro_root(self.root) / 'jobs'

    def snapshot(self):
        return {p: p.read_bytes() for p in self.ctx.design_dir.rglob('*') if p.is_file()}

    def test_refuses_a_design_without_an_estimate(self):
        self.assertFalse(retro.eligible(self.ctx))
        with self.assertRaisesRegex(ValueError, 'only for designs with an estimate'):
            retro.emit(self.ctx, d.ROLES[0], self.dir)

    def test_prospective_path_still_refuses_the_estimated_design(self):
        ensure_once(self.ctx.design_dir / 'RESULT.json', {})
        with self.assertRaisesRegex(ValueError, 'protected'):
            d.emit(self.ctx, d.ROLES[0], self.root / 'case')

    def test_emits_blind_jobs_for_an_estimated_design_without_touching_the_design_dir(self):
        ensure_once(self.ctx.design_dir / 'RESULT.json', {})
        before = self.snapshot()
        jobs = [retro.emit(self.ctx, r, self.dir) for r in d.ROLES[:2]]
        self.assertEqual(before, self.snapshot())
        for job in jobs:
            d.validate_job(job)   # the same blind-contract validator as the prospective path
            self.assertIn('retro_f1', Path(job.job_path).parts)
            self.assertEqual(set(job.contract), {'design_id', 'role', 'jobs_kind', 'target_outcome', 'citations', 'permitted_source_paths', 'execution_authorized'})
        self.assertEqual(retro.emit(self.ctx, d.ROLES[0], self.dir).job_sha256, jobs[0].job_sha256)   # idempotent

    def test_job_root_outside_retro_f1_is_refused(self):
        ensure_once(self.ctx.design_dir / 'RESULT.json', {})
        with self.assertRaisesRegex(ValueError, 'retro_f1'):
            retro.emit(self.ctx, d.ROLES[0], self.root / 'data/l2_pipeline/case')

    def test_prompt_carries_no_estimate_or_result_text(self):
        ensure_once(self.ctx.design_dir / 'RESULT.json', {'att': 123456.789, 'marker': 'SECRET_ESTIMATE'})
        job = retro.emit(self.ctx, d.ROLES[0], self.dir)
        text = Path(job.job_path).read_text()
        self.assertNotIn('SECRET_ESTIMATE', text)
        self.assertNotIn('123456', text)

    def test_queue_root_is_separate_from_the_live_queue(self):
        self.assertNotEqual(retro.queue_root(self.root), q.root_for(self.root))
        self.assertIn('retro', retro.queue_root(self.root).name.lower())


if __name__ == '__main__':
    unittest.main()
