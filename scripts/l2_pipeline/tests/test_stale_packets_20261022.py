"""T226(d): stale hub packet expiry. Synthetic temporary queue only; nothing live is read or written."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.agents import stale_packets as sp

W3 = sp.W3_LIVE
W3_TS = sp._ts(W3)
PRE = W3_TS - 4 * 3600
POST = W3_TS + 4 * 3600


def jid(n):
    return ('%064x' % n)


class Fake:
    """Resolver stand-in: designs = {design_id: (attempt_id, state)}; ended = {(design, attempt): 'stopped'|'parked'}."""

    def __init__(self, base, designs, ended=None, job_files=None):
        self.base, self.designs, self.ended, self.job_files = Path(base), designs, ended or {}, job_files or {}

    def current(self, design_id):
        if design_id not in self.designs:
            return None
        return dict(design_id=design_id, attempt_id=self.designs[design_id][0])

    def design_state(self, row):
        return self.designs[row['design_id']][1]

    def attempt_end(self, row):
        return self.ended.get((row['design_id'], row['attempt_id']))

    def case_dir(self, row):
        d = self.base / 'cases' / row['design_id'] / row['attempt_id']
        (d / 'jobs').mkdir(parents=True, exist_ok=True)
        (d / 'actions').mkdir(exist_ok=True)
        return d


def put(root, state_dir, n, design, attempt, entered=PRE, kind='expired', persona=False, completion=False):
    """One packet directory in root/state_dir with the files the queue writes."""
    d = Path(root) / state_dir / jid(n)
    d.mkdir(parents=True)
    job = str(Path(root).parent / 'jobs' / design / attempt / ('A%d.md' % n))
    Path(job).parent.mkdir(parents=True, exist_ok=True)
    Path(job).write_text('job ' + str(n))
    Path(job).with_suffix('.job.json').write_text(json.dumps(dict(design_id=design, role='A', stage='S5' if persona else 'S1')))
    (d / 'PACKET.json').write_text(json.dumps(dict(job_id=jid(n), job_path=job, row=dict(design_id=design, attempt_id=attempt))))
    state = dict(enqueued_at=entered - 7200, expiries=2)
    (d / 'STATE.json').write_text(json.dumps(state))
    (d / 'PROMPT.md').write_text('p')
    if kind == 'expired':
        for i in (1, 2):
            (d / ('EXPIRED_%d.json' % i)).write_text(json.dumps(dict(lease=dict(worker='w'), time=entered - (2 - i) * 100)))
    else:
        state['releases'] = 3
        (d / 'STATE.json').write_text(json.dumps(state))
        for i in (1, 2, 3):
            (d / ('RELEASED_%d.json' % i)).write_text(json.dumps(dict(lease=dict(worker='w'), reason='x', time=entered - (3 - i) * 100)))
    if completion:
        (d / 'COMPLETION.json').write_text('{}')
    return d


class StalePacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'data/l2_pipeline/registry/SONNET_QUEUE'
        for s in (*q.STATES, 'retired_stopped'):
            (self.root / s).mkdir(parents=True)

    def snapshot(self, *dirs):
        out = {}
        for s in dirs:
            for p in sorted((self.root / s).rglob('*')):
                out[str(p.relative_to(self.root))] = p.read_bytes() if p.is_file() else None
        return out

    def fake(self, **kw):
        designs = dict(old=('v2', 'in_flight'), live=('v1', 'in_flight'), done=('v1', 'park'), fin=('v3', 'finished'))
        return Fake(self.base, kw.pop('designs', designs), **kw)

    # --- classification -------------------------------------------------------------------------------------------
    def test_categories_and_pre_post_w3(self):
        put(self.root, 'hub', 1, 'old', 'v1')  # superseded, pre W3
        put(self.root, 'hub', 2, 'old', 'v1', entered=POST, kind='released')  # superseded, post W3
        put(self.root, 'hub', 3, 'live', 'v1')  # current, in flight
        put(self.root, 'hub', 4, 'done', 'v1')  # current, terminal
        put(self.root, 'hub', 5, 'ghost', 'v1')  # design not active
        rows = {r['job_id']: r for r in sp.scan(self.root, self.fake())}
        self.assertEqual(rows[jid(1)]['category'], 'superseded')
        self.assertEqual(rows[jid(1)]['pre_or_post_w3'], 'pre_w3')
        self.assertEqual(rows[jid(2)]['pre_or_post_w3'], 'post_w3')
        self.assertEqual(rows[jid(2)]['hub_cause'], 'released_repeatedly')
        self.assertFalse(rows[jid(3)]['eligible'])
        self.assertEqual(rows[jid(3)]['depends_on_it'], 'yes')
        self.assertEqual(rows[jid(4)]['category'], 'current_terminal')
        self.assertEqual(rows[jid(5)]['attempt_vs_design'], 'design_not_active')
        self.assertEqual(sp.summary(list(rows.values()))['hub_total'], 5)

    # --- dry run ----------------------------------------------------------------------------------------------------
    def test_default_is_dry_run(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        before = self.snapshot(*q.STATES, 'retired_stopped')
        value = sp.expire(self.root, self.fake(), reason='r')
        self.assertFalse(value['applied'])
        self.assertEqual(len(value['moved']), 1)  # the plan
        self.assertEqual(before, self.snapshot(*q.STATES, 'retired_stopped'))
        self.assertFalse((self.root / sp.RETIRED).exists())

    # --- apply ------------------------------------------------------------------------------------------------------
    def test_apply_moves_by_rename_and_records_evidence(self):
        d = put(self.root, 'hub', 1, 'old', 'v1')
        files = {p.name: p.read_bytes() for p in d.iterdir()}
        inode = d.stat().st_ino
        value = sp.expire(self.root, self.fake(), reason='stale test', apply=True, now=1000.0)
        target = self.root / sp.RETIRED / jid(1)
        self.assertTrue(value['applied'] and target.is_dir() and not d.exists())
        self.assertEqual(target.stat().st_ino, inode)  # a rename, not a copy
        for name, data in files.items():
            self.assertEqual((target / name).read_bytes(), data)  # every byte retained
        rec = json.loads((target / 'RETIRED_STALE.json').read_text())
        self.assertEqual(rec['reason'], 'stale test')
        self.assertEqual(rec['retired_at'], 1000.0)
        self.assertEqual(rec['evidence']['attempt_vs_design'], 'superseded')
        self.assertEqual(rec['evidence']['pre_or_post_w3'], 'pre_w3')
        self.assertTrue(rec['bytes_retained'])

    def test_refuses_current_attempt_of_a_design_in_flight(self):
        put(self.root, 'hub', 1, 'live', 'v1')
        before = self.snapshot('hub')
        for flag in (False, True):
            value = sp.expire(self.root, self.fake(), reason='r', apply=True, include_current_terminal=flag)
            self.assertEqual(value['moved'], [])
            self.assertIn('current and design is in flight', value['skipped'][0]['why'])
        self.assertEqual(before, self.snapshot('hub'))

    def test_current_terminal_needs_explicit_flag(self):
        put(self.root, 'hub', 1, 'done', 'v1')
        self.assertEqual(sp.expire(self.root, self.fake(), reason='r', apply=True)['moved'], [])
        self.assertTrue((self.root / 'hub' / jid(1)).exists())
        value = sp.expire(self.root, self.fake(), reason='r', apply=True, include_current_terminal=True)
        self.assertEqual(len(value['moved']), 1)
        self.assertTrue((self.root / sp.RETIRED / jid(1) / 'RETIRED_STALE.json').exists())

    def test_never_touches_other_states(self):
        for i, s in enumerate(('pending', 'in_progress', 'done', 'chunked', 'retired_stopped'), 10):
            put(self.root, s, i, 'old', 'v1')
        put(self.root, 'hub', 1, 'old', 'v1')
        before = self.snapshot('pending', 'in_progress', 'done', 'chunked', 'retired_stopped')
        sp.expire(self.root, self.fake(), reason='r', apply=True)
        self.assertEqual(before, self.snapshot('pending', 'in_progress', 'done', 'chunked', 'retired_stopped'))
        self.assertEqual(len(list((self.root / sp.RETIRED).iterdir())), 1)
        # job_id restricted to a non-hub packet: nothing happens
        value = sp.expire(self.root, self.fake(), reason='r', apply=True, job_id=jid(10))
        self.assertEqual(value['moved'], [])

    def test_idempotent(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        put(self.root, 'hub', 2, 'live', 'v1')
        first = sp.expire(self.root, self.fake(), reason='r', apply=True, now=5.0)
        snap = self.snapshot('hub', sp.RETIRED)
        second = sp.expire(self.root, self.fake(), reason='again', apply=True, now=9.0)
        self.assertEqual(len(first['moved']), 1)
        self.assertEqual(second['moved'], [])
        self.assertEqual(snap, self.snapshot('hub', sp.RETIRED))

    def test_replay_after_crash_keeps_first_record(self):
        d = put(self.root, 'hub', 1, 'old', 'v1')
        (d / 'RETIRED_STALE.json').write_text('{"reason": "first", "retired_at": 1}')  # record written, rename not done
        sp.expire(self.root, self.fake(), reason='second', apply=True)
        rec = json.loads((self.root / sp.RETIRED / jid(1) / 'RETIRED_STALE.json').read_text())
        self.assertEqual(rec['reason'], 'first')

    def test_blockers(self):
        live_job = put(self.root, 'hub', 1, 'old', 'v1')  # the new attempt still lists this job
        job_path = json.loads((live_job / 'PACKET.json').read_text())['job_path']
        fake = self.fake()
        (fake.case_dir(dict(design_id='old', attempt_id='v2')) / 'jobs' / 'history.json').write_text(json.dumps([job_path]))
        put(self.root, 'hub', 2, 'old', 'v1', completion=True)
        put(self.root, 'hub', 3, 'old', 'v1', persona=True)
        put(self.root, 'hub', 4, 'old', 'v1')
        (self.root / 'hub' / jid(4) / 'PACKET.json').write_text('{broken')
        value = sp.expire(self.root, fake, reason='r', apply=True)
        self.assertEqual(value['moved'], [])
        why = {s['job_id']: s['why'] for s in value['skipped']}
        self.assertIn('still lists the job', why[jid(1)])
        self.assertIn('COMPLETION', why[jid(2)])
        self.assertIn('persona', why[jid(3)])
        self.assertIn('unreadable', why[jid(4)])
        self.assertEqual(len(list((self.root / 'hub').iterdir())), 4)

    def test_pre_w3_only_filter(self):
        put(self.root, 'hub', 1, 'old', 'v1', entered=PRE)
        put(self.root, 'hub', 2, 'old', 'v1', entered=POST)
        value = sp.expire(self.root, self.fake(), reason='r', apply=True, pre_w3_only=True)
        self.assertEqual([m['job_id'] for m in value['moved']], [jid(1)])
        self.assertTrue((self.root / 'hub' / jid(2)).exists())

    def test_name_collision_gets_suffix_and_nothing_overwritten(self):
        (self.root / sp.RETIRED / jid(1)).mkdir(parents=True)
        (self.root / sp.RETIRED / jid(1) / 'old.txt').write_text('keep')
        put(self.root, 'hub', 1, 'old', 'v1')
        sp.expire(self.root, self.fake(), reason='r', apply=True)
        self.assertEqual((self.root / sp.RETIRED / jid(1) / 'old.txt').read_text(), 'keep')
        self.assertTrue((self.root / sp.RETIRED / (jid(1) + '.2') / 'RETIRED_STALE.json').exists())

    def test_runs_under_queue_lock(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        calls = []
        real = q.locked

        @contextlib.contextmanager
        def spy(root):
            calls.append(Path(root))
            with real(root) as r:
                yield r
        with mock.patch.object(q, 'locked', spy):
            sp.expire(self.root, self.fake(), reason='r', apply=True)
        self.assertEqual(calls, [self.root])

    def test_reason_required_to_apply(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        with self.assertRaises(ValueError):
            sp.expire(self.root, self.fake(), reason='', apply=True)
        with self.assertRaises(ValueError):
            sp.expire(self.root, self.fake(), reason='x' * 1001, apply=True)
        self.assertTrue((self.root / 'hub' / jid(1)).exists())

    # --- queue interplay ---------------------------------------------------------------------------------------------
    def test_retired_packet_is_invisible_to_queue_lookup(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        self.assertIsNotNone(q.locate(self.root, jid(1)))
        sp.expire(self.root, self.fake(), reason='r', apply=True)
        self.assertIsNone(q.locate(self.root, jid(1)))  # like retired_stopped: enqueue would build a fresh packet
        self.assertNotIn(sp.RETIRED, q.STATES)

    # --- CLI ---------------------------------------------------------------------------------------------------------
    def run_cli(self, *argv, env=None):
        out = io.StringIO()
        with mock.patch.object(sp, 'LiveResolver', lambda repo: self.fake()), \
                mock.patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stdout(out):
            code = sp.main(['--root', str(self.root), *argv])
        return code, out.getvalue()

    def test_cli_list_and_csv(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        put(self.root, 'hub', 2, 'live', 'v1')
        csv_path = self.base / 'o.csv'
        code, out = self.run_cli('list', '--csv', str(csv_path))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['summary']['hub_total'], 2)
        lines = csv_path.read_text().splitlines()
        self.assertEqual(lines[0].split(',')[0], 'job_id')
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(list((self.root / 'hub').iterdir())), 2)

    def test_cli_apply_needs_hub_env_and_reason(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        env = {k: v for k, v in os.environ.items() if k != 'L2_HUB'}
        with mock.patch.dict(os.environ, env, clear=True):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli('expire', '--apply', '--reason', 'r')
        with mock.patch.dict(os.environ, {'L2_HUB': '1'}):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.run_cli('expire', '--apply')
            self.assertTrue((self.root / 'hub' / jid(1)).exists())
            code, out = self.run_cli('expire', '--apply', '--reason', 'r')
        self.assertEqual(code, 0)
        self.assertTrue((self.root / sp.RETIRED / jid(1)).is_dir())

    def test_cli_expire_without_apply_is_dry_run(self):
        put(self.root, 'hub', 1, 'old', 'v1')
        code, out = self.run_cli('expire')
        self.assertFalse(json.loads(out)['applied'])
        self.assertTrue((self.root / 'hub' / jid(1)).exists())


if __name__ == '__main__':
    unittest.main()
