"""YIELD_FIXES 2026-10-16: D8 census V3.1, Gate0 catalog requeue, queue retirement,
V9 evidence-row correction and certified-history reuse. Synthetic data only."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_agents as fixtures
from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests
from scripts.l2_pipeline.tests.test_c46 import RequeueTests


class D8CensusV31Tests(OfficialBridgeTests):
    """Bug (a): c48_audit.bound_comparison omitted remainder_census."""

    def census(self, n=9):
        self.policy['excluded_units'] = ['U' + str(i) for i in range(12 - n)]
        return self.prepare()

    def evidence(self, job):
        from scripts.l2_pipeline.autopilot_history import bind
        return [bind(job.output_path), bind(job.promote_path),
                bind(Path(job.output_dir) / 'COLLECTION.json'), bind(job.contract['reconciliation_path'])]

    def audit_with(self, job, status, count):
        from scripts.l2_pipeline.agents.contract_tests import golden_output, check_job_types
        from scripts.l2_pipeline.agents import collect_job
        check_job_types([job])
        output = golden_output(job)
        for check in output['checks'][:count]:
            check.update(status=status)
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text(json.dumps(output))
        collect_job(job.job_path)

    def test_prepare_s1_accepts_a_passing_census_audit(self):
        from scripts.l2_pipeline.c48_audit import prepare_s1
        job = self.census(9)
        self.audit(job)
        self.assertEqual(len(job.contract['sample']), 9)
        # Before the fix this raised Park('V3.1 fresh audit did not pass').
        self.assertIs(prepare_s1(self.ctx, job.job_path), self.ctx)

    def test_census_with_unresolved_dates_registers_the_overlay_and_certifies(self):
        from scripts.l2_pipeline.c48_audit import prepare_s1, effective_context
        from scripts.l2_pipeline.autopilot_history import accept
        job = self.census(6)
        self.audit_with(job, 'unresolved', 2)
        self.assertEqual(read(job.promote_path)['audit']['unresolved'], 2)
        ctx = prepare_s1(self.ctx, job.job_path)
        preparation = read(self.ctx.design_dir / 'C48_AUDIT_PREPARATION.json')
        self.assertEqual(len(preparation['unresolved_units']), 2)
        self.assertEqual(accept(ctx, job.job_path).disposition, 'ready')
        self.assertEqual(read(self.ctx.design_dir / 'TREATMENT.json')['audit']['sample_size'], 6)
        # Backend.ctx recomputes the overlay from the bound evidence on every later tick.
        self.assertEqual(effective_context(self.ctx).card.payload['sensitivities'], ctx.card.payload['sensitivities'])

    def test_bound_comparison_needs_the_census_receipt(self):
        from scripts.l2_pipeline.c48_audit import bound_comparison
        from scripts.l2_pipeline.c42 import Park
        job = self.census(9)
        self.audit(job)
        evidence = self.evidence(job)
        self.assertTrue(bound_comparison(evidence)['accepted'])
        selection = Path(job.contract['selection_path'])
        original = selection.read_text()
        try:
            value = json.loads(original)
            value.pop('remainder_census')
            selection.write_text(json.dumps(value))
            with self.assertRaisesRegex(Park, 'V3.1 fresh audit did not pass'):
                bound_comparison(evidence)
            value = json.loads(original)
            value['remainder_census']['owner_approval_date'] = '2020-01-01'
            selection.write_text(json.dumps(value))
            with self.assertRaisesRegex(Park, 'V3.1 fresh audit did not pass'):
                bound_comparison(evidence)
            value = json.loads(original)
            value['sample'] = value['sample'][:-1]
            selection.write_text(json.dumps(value))
            with self.assertRaisesRegex(Park, 'V3.1 fresh audit did not pass'):
                bound_comparison(evidence)
        finally:
            selection.write_text(original)

    def test_census_failing_the_error_threshold_still_parks(self):
        from scripts.l2_pipeline.c48_audit import bound_comparison
        from scripts.l2_pipeline.c42 import Park
        job = self.census(9)
        self.audit(job, errors=3)
        with self.assertRaisesRegex(Park, 'V3.1 fresh audit did not pass'):
            bound_comparison(self.evidence(job))

    def test_ten_state_sample_is_unaffected(self):
        from scripts.l2_pipeline.c48_audit import prepare_s1
        job = self.prepare()
        self.audit(job)
        self.assertEqual(len(job.contract['sample']), 10)
        self.assertIs(prepare_s1(self.ctx, job.job_path), self.ctx)


class RequeueGate0CatalogTests(RequeueTests):
    """Bug (b): requeue.py rejected superseded_status SUPERSEDED_GATE0_CATALOG."""

    def test_gate0_catalog_status_is_accepted_and_recorded(self):
        from scripts.l2_pipeline.requeue import requeue
        atomic(self.ruling, dict(self.value, decision='resume', superseded_status='SUPERSEDED_GATE0_CATALOG'))
        result = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(result['retained_actions'], ['history', 'reconcile', 'audit'])
        queue = read(self.registry / 'HUB_QUEUE.json')
        self.assertEqual(queue['entries'][0]['status'], 'SUPERSEDED_GATE0_CATALOG')

    def test_unknown_status_is_still_refused(self):
        from scripts.l2_pipeline.requeue import requeue
        atomic(self.ruling, dict(self.value, decision='resume', superseded_status='SUPERSEDED_NOPE'))
        with self.assertRaisesRegex(ValueError, 'Invalid superseded status'):
            requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)


class Gate0StaleMarkerTests(RequeueTests):
    """Bug (b), second half: a MECHANICAL_REQUEUE marker left by a failed attempt blocked the next catalog."""

    def apply_with(self, status='SUPERSEDED_GATE0_CATALOG'):
        from scripts.l2_pipeline import population_resolution as pr
        new = deepcopy(self.card)
        new['version'] = 'v2'
        resolution = dict(decision='accept', reason='fixture', selected=dict(series_id='fixture'),
                          equivalence_citation=None, candidates=[])
        with patch.object(pr, 'purpose_evidence', return_value=(self.authority, dict(status='VERIFIED'))), \
                patch.object(pr, 'choose', return_value=resolution), \
                patch.object(pr, 'revised_card', return_value=new), \
                patch.object(pr, 'rebase_sources', side_effect=lambda card, path: card), \
                patch('scripts.l2_pipeline.requeue.requeue', return_value={'new_row': 'fixture'}) as requeue:
            value = pr.apply(self.pilot, self.row, scheduler_owned=True, output=self.repo / 'out' / status,
                             superseded_status=status)
        return value, requeue

    def test_parked_design_keeps_a_stale_marker_and_is_requeued(self):
        case = self.pilot.case_dir(self.row)
        ensure_once(case / 'PARKED.json', dict(action='S2', reason='no local public files'))
        marker = ensure_once(case / 'MECHANICAL_REQUEUE.json', dict(card='stale-v15', ruling='stale-v15'))
        before = sha(marker)
        value, requeue = self.apply_with()
        self.assertTrue(value['requeued'])
        self.assertEqual(requeue.call_count, 1)
        self.assertEqual(sha(marker), before)
        ruling = read(self.repo / 'out' / 'SUPERSEDED_GATE0_CATALOG' / 'RULING.json')
        self.assertEqual(ruling['superseded_status'], 'SUPERSEDED_GATE0_CATALOG')

    def test_parked_design_without_a_marker_still_gets_one(self):
        case = self.pilot.case_dir(self.row)
        ensure_once(case / 'PARKED.json', dict(action='S2', reason='no local public files'))
        self.apply_with()
        self.assertTrue((case / 'MECHANICAL_REQUEUE.json').exists())

    def test_live_design_still_refuses_a_changed_marker(self):
        case = self.pilot.case_dir(self.row)
        ensure_once(case / 'MECHANICAL_REQUEUE.json', dict(card='stale', ruling='stale'))
        with self.assertRaisesRegex(ValueError, 'Existing preparation artifact changed'):
            self.apply_with()


class RetireStoppedTests(unittest.TestCase):
    """Bug (c): pending packets of STOPped designs stayed claimable."""
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown
    emit = fixtures.AgentTests.emit

    def packet(self, role='Card', attempt='invented'):
        job = self.emit('S5', role)
        root = self.root / 'queue'
        return job, root, q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id=attempt), root)

    def tree(self, root):
        return {str(p.relative_to(root)): sha(p) for p in Path(root).rglob('*') if p.is_file()}

    def test_stopped_packet_is_archived_with_bytes_kept_and_cannot_be_claimed(self):
        job, root, receipt = self.packet()
        before = {k: v for k, v in self.tree(root).items() if k.startswith('pending/')}
        events = q.retire_stopped(root, lambda row, job: True)
        self.assertEqual([e['status'] for e in events], ['retired_stopped'])
        self.assertEqual(events[0]['row']['design_id'], job.design_id)
        self.assertEqual(list((root / 'pending').iterdir()), [])
        archived = root / 'retired_stopped' / receipt['job_id']
        after = self.tree(root)
        for name, digest in before.items():
            self.assertEqual(after[name.replace('pending/', 'retired_stopped/', 1)], digest)
        self.assertTrue((archived / 'RETIRED_STOPPED.json').is_file())
        self.assertIsNone(q.claim(root, 'worker'))
        self.assertIsNone(q.locate(root, receipt['job_id']))

    def test_claimed_and_live_packets_are_left_alone(self):
        _, root, claimed = self.packet('Card')
        lease = q.claim(root, 'worker', claimed['job_id'], now=100)
        _, _, live = self.packet('Roth')
        q.retire_stopped(root, lambda row, job: False)
        self.assertTrue((root / 'pending' / live['job_id']).exists())
        q.retire_stopped(root, lambda row, job: True)
        self.assertTrue((root / 'in_progress' / claimed['job_id']).exists())
        self.assertFalse((root / 'pending' / live['job_id']).exists())
        self.assertTrue(q.heartbeat(root, claimed['job_id'], lease['token'], now=101))

    def test_requeued_design_gets_a_fresh_packet_and_a_second_retirement_keeps_both(self):
        job, root, receipt = self.packet()
        q.retire_stopped(root, lambda row, job: True)
        again = q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='requeue_v2'), root)
        self.assertEqual(again['job_id'], receipt['job_id'])
        self.assertTrue((root / 'pending' / receipt['job_id']).exists())
        q.retire_stopped(root, lambda row, job: True)
        archive = sorted(p.name for p in (root / 'retired_stopped').iterdir())
        self.assertEqual(archive, [receipt['job_id'], receipt['job_id'] + '.2'])

    def test_a_failing_stop_predicate_never_retires(self):
        _, root, receipt = self.packet()

        def broken(row, job):
            raise ValueError('bytes changed')
        self.assertEqual(q.retire_stopped(root, broken), [])
        self.assertTrue((root / 'pending' / receipt['job_id']).exists())

    def test_scheduler_retires_only_the_current_stopped_attempt_and_records_it(self):
        from scripts.l2_pipeline.autopilot import Autopilot, Backend
        job, root, receipt = self.packet(attempt='attempt_a')
        repo = self.root.resolve()
        registry = repo / 'data/l2_pipeline/registry'
        pilot = Autopilot(registry, Backend(repo))
        row = dict(design_id=job.design_id, attempt_id='attempt_a', card='x', card_sha256='x')
        atomic(registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        with patch.object(q, 'root_for', return_value=root):
            pilot.schedule()
            self.assertTrue((root / 'pending' / receipt['job_id']).exists())  # not stopped yet
            pilot.stop(row, 'history', 'invented hub stop')
            pilot.schedule()
        self.assertFalse((root / 'pending' / receipt['job_id']).exists())
        self.assertTrue((root / 'retired_stopped' / receipt['job_id']).exists())
        ledger = [json.loads(line) for line in (registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        retired = [e for e in ledger if e.get('decision') == 'RETIRED_STOPPED']
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0]['design_id'], job.design_id)
        self.assertEqual(retired[0]['job'], job.job_path)

    def superseded(self, lists_job):
        from scripts.l2_pipeline.autopilot import Autopilot, Backend
        job, root, receipt = self.packet(attempt='attempt_a')
        repo = self.root.resolve()
        registry = repo / 'data/l2_pipeline/registry'
        pilot = Autopilot(registry, Backend(repo))
        old = dict(design_id=job.design_id, attempt_id='attempt_a', card='x', card_sha256='x')
        pilot.stop(old, 'history', 'invented hub stop')
        new = dict(old, attempt_id='requeue_v2')
        atomic(registry / 'ACTIVE_DESIGNS.json', dict(designs=[new]))
        if lists_job:  # a requeue that retained the job keeps it in the new attempt's inventory
            ensure_once(pilot.case_dir(new) / 'jobs/history.json', dict(jobs=[job.job_path]))
        with patch.object(q, 'root_for', return_value=root):
            pilot.schedule()
        return root, receipt

    def test_packet_of_a_superseded_stopped_attempt_is_retired_unless_the_new_attempt_keeps_the_job(self):
        root, receipt = self.superseded(lists_job=False)
        self.assertFalse((root / 'pending' / receipt['job_id']).exists())
        self.assertTrue((root / 'retired_stopped' / receipt['job_id']).exists())

    def test_packet_whose_job_the_new_attempt_retains_is_kept(self):
        root, receipt = self.superseded(lists_job=True)
        self.assertTrue((root / 'pending' / receipt['job_id']).exists())

    def test_packet_of_a_superseded_attempt_that_never_stopped_is_kept(self):
        from scripts.l2_pipeline.autopilot import Autopilot, Backend
        job, root, receipt = self.packet(attempt='attempt_a')
        repo = self.root.resolve()
        registry = repo / 'data/l2_pipeline/registry'
        pilot = Autopilot(registry, Backend(repo))
        atomic(registry / 'ACTIVE_DESIGNS.json', dict(designs=[dict(design_id=job.design_id, attempt_id='requeue_v2', card='x', card_sha256='x')]))
        with patch.object(q, 'root_for', return_value=root):
            pilot.schedule()
        self.assertTrue((root / 'pending' / receipt['job_id']).exists())


class V9RowCorrectionTests(OfficialBridgeTests):
    """Ruling (i): one self-check correction for V9 evidence-row errors."""

    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        self.queue = self.root / 'queue'
        self.fresh = emit_pair(self.card, self.card['treatment']['universe'],
                               root=self.root / 'route_q', attempt_id='queue_test')
        self.job = self.fresh[0]
        # F7 splits a history job of more than 10 units into chunk packets; this class exercises one ordinary packet.
        chunking = patch('scripts.l2_pipeline.agents.history_chunks.CHUNK_UNITS', 1000)
        chunking.start()
        self.addCleanup(chunking.stop)
        self.packet = q.enqueue(self.job.job_path, dict(design_id=self.job.design_id, attempt_id='t'), self.queue)
        self.lease = q.claim(self.queue, 'worker', self.packet['job_id'], now=100)

    def write(self, value):
        path = Path(self.job.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def good(self):
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        out = golden_output(self.job)
        for record in out['records'][12:]:
            record.update(status='never_treated', start_period=None, evidence_status='absent (V2_LIST)',
                          absence_basis=dict(official_code_history_no_earlier_enactment=True,
                                             no_earlier_in_compilations_on_disk=True,
                                             no_search_log_contradiction=True))
            record['evidence_refs'][0]['supports'] = 'absence_code_history'
        return out

    def broken(self, kind):
        out = self.good()
        row = out['records'][0]
        if kind == 'E1':
            row['evidence_refs'] = []
        elif kind == 'E2':
            for ref in row['evidence_refs']:
                ref['year'] = ref['year'] + 1
        elif kind == 'E3':
            sid = row['source_id']
            row.update(status='unknown', start_period=None, reason='sources differ', disposition=None,
                       named_conflict=dict(kind='sources_conflict', detail='x', binary_relevant_fact='y',
                                           source_ids=[sid]))
            row.pop('best_evidence_start_year', None)
            row.pop('evidence_status', None)
        return out

    def check(self, now=101):
        return q.self_check(self.queue, self.packet['job_id'], self.lease['token'], now=now)

    def test_each_row_error_gets_exactly_one_correction(self):
        for kind, message in (('E1', 'V9 known status requires a located quoted official evidence ref'),
                              ('E2', 'V9 start year needs a dated official confirmation in the same row'),
                              ('E3', 'V9 source conflict requires two distinct sources')):
            with self.subTest(kind):
                packet = self.queue / 'in_progress' / self.packet['job_id']
                for name in ('CORRECTION.json', 'REJECTED_SUBMISSION.txt', 'CORRECTED_SUBMISSION.json'):
                    if (packet / name).exists():
                        (packet / name).rename(packet / (name + '.' + kind))
                if Path(self.job.output_path).exists():
                    Path(self.job.output_path).rename(Path(self.job.output_path).with_suffix('.' + kind))
                out_dir = Path(self.job.output_dir)
                for stale in out_dir.glob('SELF_CHECK_*.json'):
                    stale.rename(stale.with_suffix('.' + kind))
                self.write(self.broken(kind))
                first = self.check()
                self.assertIs(first['validated'], False)
                self.assertTrue(first['error'].endswith(message), first['error'])
                self.assertIs(first['correction_allowed'], True)
                self.assertEqual(self.check()['correction_allowed'], True)  # idempotent while unchanged
                self.write(self.good())
                self.assertIs(self.check(102)['validated'], True)

    def test_second_failed_draft_gets_no_further_correction_and_follows_the_old_path(self):
        from scripts.l2_pipeline.autopilot import Backend
        self.write(self.broken('E1'))
        self.assertIs(self.check()['correction_allowed'], True)
        redraft = self.broken('E2')
        self.write(redraft)
        second = self.check(102)
        self.assertIs(second['validated'], False)
        self.assertIs(second['correction_allowed'], False)
        done = q.finish(self.queue, self.packet['job_id'], self.lease['token'], now=103)
        self.assertTrue(Path(done).is_relative_to(self.queue / 'done'))
        # Unchanged: the collector raises a plain ValueError (hub STOP), not a replacement.
        with patch.object(q, 'root_for', return_value=self.queue):
            with self.assertRaisesRegex(ValueError, 'V9 start year needs a dated official confirmation'):
                Backend(self.ctx.repo).collect(self.job.job_path)

    def test_correction_is_never_offered_without_the_blindness_bindings(self):
        from scripts.l2_pipeline.agents.replacement import row_correctable
        binding = q.read(self.queue / 'in_progress' / self.packet['job_id'] / 'PACKET.json')
        message = 'V9 known status requires a located quoted official evidence ref'
        self.assertTrue(row_correctable(self.job, binding, message, {}))
        for key, value in (('peer_read_forbidden', False), ('blindness_scope', 'legacy'),
                           ('evidence_standard', 'other'), ('jobs_kind', 'other')):
            changed = replace(self.job, contract=dict(self.job.contract, **{key: value}))
            self.assertFalse(row_correctable(changed, binding, message, {}), key)
        wider = dict(binding, allowed_read_paths=binding['allowed_read_paths'] + ['/tmp/other-coder/submission.json'])
        self.assertFalse(row_correctable(self.job, wider, message, {}))
        self.assertFalse(row_correctable(replace(self.job, role='auditor'), binding, message, {}))
        self.assertFalse(row_correctable(self.job, binding, 'V9 absence requires all three V2_LIST checks '
                                         'is not in this set', {}))
        self.assertFalse(row_correctable(self.job, binding, message, {'verdict': 'STOP'}))

    def test_evidence_that_is_not_a_row_error_is_not_correctable(self):
        out = self.good()
        out['blindness_attestation']['other_submissions_read'] = True
        self.write(out)
        result = self.check()
        self.assertIs(result['validated'], False)
        self.assertIs(result['correction_allowed'], False)


class FakePilot:
    """The part of Autopilot that history_reuse touches, over synthetic S1 products."""

    def __init__(self, test, rows, contexts, checkpoints):
        from types import SimpleNamespace
        self.registry = test.root / 'data/l2_pipeline/registry'
        self.repo = Path(__file__).resolve().parents[3]
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=rows))
        self.contexts, self.checkpoints = contexts, checkpoints
        self.backend = SimpleNamespace(root=lambda row: self.contexts[row['design_id']].design_dir, ctx=self.ctx)

    def ctx(self, row):
        from scripts.l2_pipeline.c42 import effective_context as c42_context
        from scripts.l2_pipeline.c48_audit import effective_context as c48_context
        return c48_context(c42_context(self.contexts[row['design_id']]))

    def done(self, row, action):
        return self.checkpoints.get((row['design_id'], action))


class HistoryReuseTests(OfficialBridgeTests):
    """Ruling (ii): an already-certified history reused for a new design on the same policy."""

    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline.agents import collect_job
        from scripts.l2_pipeline.autopilot_history import accept
        # A/B disagree on U0's operation year: a conflict exclusion the reusing design must inherit.
        out = Path(self.jobs[1].output_path)
        value = read(out)
        value['records'][0]['best_evidence_start_year'] += 1
        value['records'][0]['evidence_refs'][0]['year'] += 1
        out.write_text(json.dumps(value))
        for name in ('COLLECTION.json', 'NORMALIZATION.json', 'NORMALIZED_HISTORY.json'):
            (out.parent / name).unlink()
        collect_job(self.jobs[1].job_path)
        self.source_job = self.prepare()
        self.audit(self.source_job)
        accept(self.ctx, self.source_job.job_path)
        self.source_id = self.card['design_id']
        self.source_row = dict(design_id=self.source_id, attempt_id='v9', card=str(self.path), card_sha256=sha(self.path))
        self.checkpoints = {
            (self.source_id, 'S1'): dict(disposition='ready'),
            (self.source_id, 'audit'): dict(jobs=[self.source_job.job_path]),
            (self.source_id, 'history'): dict(jobs=[j.job_path for j in self.jobs])}
        self.target_row, self.target_ctx = self.target()
        self.pilot = FakePilot(self, [self.source_row, self.target_row],
                               {self.source_id: self.ctx, 'reuse_target': self.target_ctx}, self.checkpoints)

    def target(self, name='reuse_target', edit=None, link=None):
        from scripts.l2_pipeline.contracts import StageContext, DesignCard
        from scripts.l2_pipeline import history_reuse
        card = deepcopy(self.card)
        card['design_id'] = name
        card.setdefault('automation', {})['history_reuse'] = link or dict(
            source_design_id=self.source_id, rule=history_reuse.RULING)
        if edit:
            edit(card)
        path = ensure_once(self.root / (name + '.json'), card)
        ctx = StageContext(DesignCard.from_mapping(card, path), self.root / 'work', Path(__file__).resolve().parents[3])
        return dict(design_id=name, attempt_id='requeue_v2', card=str(path), card_sha256=sha(path), route='V9'), ctx

    def rebuild(self, row, ctx):
        self.pilot.contexts[row['design_id']] = ctx
        atomic(self.pilot.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.source_row, row]))

    def test_reuse_materialises_the_certified_history_and_s1_accepts_it(self):
        from scripts.l2_pipeline import history_reuse
        from scripts.l2_pipeline.autopilot_history import accept
        before = {n: sha(self.ctx.design_dir / n) for n in history_reuse.REQUIRED}
        value = history_reuse.reuse(self.pilot, self.target_row)
        record = read(value['record'])
        self.assertEqual(set(record['source_artifacts']), set(history_reuse.REQUIRED))
        root = self.target_ctx.design_dir
        self.assertEqual(record['source_design_id'], self.source_id)
        self.assertEqual(record['audit'], read(self.ctx.design_dir / 'TREATMENT.json')['audit'])
        self.assertEqual(record['excluded_conflict_states'], ['U0'])
        for name in history_reuse.COPIED:
            self.assertEqual(sha(root / name), sha(self.ctx.design_dir / name))
        ctx = self.pilot.backend.ctx(self.target_row)
        self.assertIn('U0', ctx.card.payload['treatment']['excluded_units'])
        self.assertEqual(value['checkpoints']['audit']['jobs'], [self.source_job.job_path])
        self.assertEqual(value['checkpoints']['history']['jobs'], [j.job_path for j in self.jobs])
        self.assertEqual(accept(ctx, self.source_job.job_path).disposition, 'ready')
        mine, theirs = read(root / 'TREATMENT.json'), read(self.ctx.design_dir / 'TREATMENT.json')
        for key in ('records', 'audit', 'sample', 'excluded_primary_units', 'excluded_conflict_states'):
            self.assertEqual(mine[key], theirs[key], key)
        proof = read(root / 'S1_IMPORT_PROVENANCE.json')
        self.assertEqual(proof['source_type'], 'reused_certified_history')
        self.assertIn(sha(root / 'HISTORY_REUSE.json'), [e['sha256'] for e in proof['evidence']])
        self.assertEqual(before, {n: sha(self.ctx.design_dir / n) for n in history_reuse.REQUIRED})
        self.assertEqual(read(self.ctx.design_dir / 'S1_IMPORT_PROVENANCE.json')['source_type'], 'autopilot_official_v9')

    def test_requeued_source_keeps_its_audit_answer_in_the_attempt_that_ran_the_audit(self):
        from types import SimpleNamespace
        import shutil
        from scripts.l2_pipeline import history_reuse
        # A requeued source (its current root holds copies of reconciliation, lock and selection,
        # the S1 products and no OFFICIAL_AUDIT.json) resolves the answer through the audit job.
        later = self.root / 'work_later'
        later.mkdir()
        for name in history_reuse.IN_ROOT + ('C42_PREPARATION.json',):
            shutil.copyfile(self.ctx.design_dir / name, later / name)
        self.pilot.contexts[self.source_id] = SimpleNamespace(design_dir=later)
        value = history_reuse.reuse(self.pilot, self.target_row)
        record = read(value['record'])
        self.assertEqual(Path(record['source_artifacts']['OFFICIAL_AUDIT.json']['path']),
                         (self.ctx.design_dir / 'OFFICIAL_AUDIT.json').resolve())
        self.assertEqual(sha(self.target_ctx.design_dir / 'OFFICIAL_AUDIT.json'), sha(self.ctx.design_dir / 'OFFICIAL_AUDIT.json'))
        self.assertEqual(Path(record['source_root']), later.resolve())

    def test_reuse_is_idempotent_repairs_a_crash_and_detects_tampering(self):
        from scripts.l2_pipeline import history_reuse
        first = history_reuse.reuse(self.pilot, self.target_row)
        digest = sha(first['record'])
        self.assertEqual(history_reuse.reuse(self.pilot, self.target_row), first)
        (self.target_ctx.design_dir / 'C42_PREPARATION.json').unlink()
        (self.target_ctx.design_dir / 'RECONCILIATION.json').unlink()
        self.assertEqual(history_reuse.reuse(self.pilot, self.target_row), first)
        self.assertTrue((self.target_ctx.design_dir / 'C42_PREPARATION.json').exists())
        self.assertEqual(sha(first['record']), digest)
        # Later source requeues cannot disturb a design that already reuses its history.
        self.pilot.checkpoints.clear()
        atomic(self.pilot.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.target_row]))
        self.assertEqual(history_reuse.reuse(self.pilot, self.target_row), first)
        treatment = self.ctx.design_dir / 'TREATMENT.json'
        original = treatment.read_text()
        treatment.write_text(original.replace('"passed"', '"passed" '))
        try:
            with self.assertRaisesRegex(ValueError, 'source artifact changed'):
                history_reuse.reuse(self.pilot, self.target_row)
        finally:
            treatment.write_text(original)

    def test_same_policy_means_exactly_the_same_definition_and_states(self):
        from scripts.l2_pipeline import history_reuse
        cases = {
            'treatment.predicate': lambda c: c['treatment'].update(predicate='a different intervention'),
            'mechanism': lambda c: c.update(mechanism='a different mechanism'),
            'policy_family': lambda c: c.update(policy_family='other_family'),
            'treatment.universe': lambda c: c['treatment'].update(universe=c['treatment']['universe'][:-1]),
            'treatment.coverage_end': lambda c: c['treatment'].update(coverage_end='2030-12-31'),
            'transition_year_drop': lambda c: c.update(transition_year_drop=False),
            'treatment.start_period_kind': lambda c: c['treatment'].update(start_period_kind='operative_year'),
        }
        for field, edit in cases.items():
            with self.subTest(field):
                row, ctx = self.target('t_' + field.replace('.', '_'), edit)
                self.rebuild(row, ctx)
                with self.assertRaisesRegex(ValueError, 'different policy definition: .*' + field.replace('.', r'\.')):
                    history_reuse.reuse(self.pilot, row)
                self.assertFalse((ctx.design_dir / 'HISTORY_REUSE.json').exists())

    def test_wording_whitespace_and_a_shorter_window_are_not_differences(self):
        from scripts.l2_pipeline import history_reuse
        def edit(card):
            card['treatment']['predicate'] = '  ' + card['treatment']['predicate'].replace(' ', '  ') + '\n'
            card['treatment']['coverage_end'] = '2019-12-31'
            card['treatment']['seed'] = 1
        row, ctx = self.target('t_whitespace', edit)
        self.rebuild(row, ctx)
        self.assertEqual(read(history_reuse.reuse(self.pilot, row)['record'])['source_design_id'], self.source_id)

    def test_unusable_sources_and_links_are_refused(self):
        from scripts.l2_pipeline import history_reuse
        row, ctx = self.target('t_unknown', link=dict(source_design_id='nobody', rule=history_reuse.RULING))
        self.rebuild(row, ctx)
        with self.assertRaisesRegex(ValueError, 'not another active design'):
            history_reuse.reuse(self.pilot, row)
        row, ctx = self.target('t_self', link=dict(source_design_id='t_self', rule=history_reuse.RULING))
        self.rebuild(row, ctx)
        with self.assertRaisesRegex(ValueError, 'not another active design'):
            history_reuse.reuse(self.pilot, row)
        row, ctx = self.target('t_rule', link=dict(source_design_id=self.source_id, rule='docs/other.md'))
        self.rebuild(row, ctx)
        with self.assertRaisesRegex(ValueError, 'Invalid history_reuse declaration'):
            history_reuse.reuse(self.pilot, row)
        row, ctx = self.target('t_extra', link=dict(source_design_id=self.source_id, rule=history_reuse.RULING, extra=1))
        self.rebuild(row, ctx)
        with self.assertRaisesRegex(ValueError, 'Invalid history_reuse declaration'):
            history_reuse.reuse(self.pilot, row)
        row, ctx = self.target()
        self.rebuild(row, ctx)
        del self.checkpoints[(self.source_id, 'S1')]
        with self.assertRaisesRegex(ValueError, 'S1 is not READY'):
            history_reuse.reuse(self.pilot, row)
        self.checkpoints[(self.source_id, 'S1')] = dict(disposition='ready')
        marker = self.ctx.design_dir / 'HISTORY_REUSE.json'
        marker.write_text('{}')
        try:
            with self.assertRaisesRegex(ValueError, 'originally certified design'):
                history_reuse.reuse(self.pilot, row)
        finally:
            marker.unlink()

    def test_source_that_never_passed_fresh_v3_is_refused(self):
        from scripts.l2_pipeline import history_reuse
        treatment = self.ctx.design_dir / 'TREATMENT.json'
        original = treatment.read_text()
        value = json.loads(original)
        value['audit']['passed'] = False
        treatment.write_text(json.dumps(value))
        try:
            with self.assertRaisesRegex(ValueError, 'not a certified fresh-V3 history'):
                history_reuse.reuse(self.pilot, self.target_row)
        finally:
            treatment.write_text(original)

    def test_declaration_helpers(self):
        from scripts.l2_pipeline import history_reuse
        self.assertTrue(history_reuse.declared(self.target_row))
        self.assertFalse(history_reuse.declared(self.source_row))
        self.assertEqual(history_reuse.candidates(self.pilot, self.target_row), [self.source_id])


class HistoryReuseApplyTests(RequeueTests):
    """Ruling (ii) rule 7: reopen a history-stage STOP through the existing card-bound requeue."""

    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline import history_reuse
        case = self.pilot.case_dir(self.row)
        (case / 'STOP.json').unlink()
        for action in ('history', 'reconcile', 'audit'):
            self.pilot.checkpoint(self.row, action).unlink()
        self.pilot.stop(self.row, 'history', 'ValueError: V9 known status requires a located quoted official evidence ref')
        atomic(self.registry / 'CODE_TASK.lock', dict(task='YIELD_FIXES'))
        ruling = self.repo / history_reuse.RULING
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text('synthetic ruling (ii)')
        self.patch = patch.object(history_reuse, 'candidates', return_value=['certified_source'])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_stopped_history_design_is_requeued_with_only_the_link_added(self):
        from scripts.l2_pipeline import history_reuse
        before = sha(self.oldpath)
        result = history_reuse.apply(self.pilot)
        self.assertEqual(len(result['records']), 1)
        receipt = result['records'][0]
        self.assertEqual(receipt['restart_action'], 'history')
        self.assertEqual(receipt['retained_actions'], [])
        new_row = receipt['new_row']
        new_card = read(new_row['card'])
        old_card = read(self.oldpath)
        self.assertEqual(new_card['automation']['history_reuse'],
                         dict(source_design_id='certified_source', rule=history_reuse.RULING))
        changed = {k for k in set(old_card) | set(new_card) if old_card.get(k) != new_card.get(k)}
        self.assertEqual(changed, {'version', 'automation'})
        self.assertEqual(sha(self.oldpath), before)
        self.assertEqual(self.pilot.next_action(new_row), 'history')
        self.assertTrue(history_reuse.declared(new_row))
        queue = read(self.registry / 'HUB_QUEUE.json')
        self.assertIn('SUPERSEDED_HISTORY_REUSE', {e.get('status') for e in queue['entries']})
        self.assertEqual(history_reuse.apply(self.pilot), result)

    def test_only_history_stage_stops_without_a_history_checkpoint_are_reopened(self):
        from scripts.l2_pipeline import history_reuse
        case = self.pilot.case_dir(self.row)
        # A stop at another stage is not this ruling's business.
        (case / 'STOP.json').rename(case / 'STOP.json.moved')
        self.pilot.stop(self.row, 'purpose', 'ValueError: something else')
        self.assertEqual(history_reuse.apply(self.pilot)['records'], [])

    def test_no_certified_source_or_a_declared_link_means_no_change(self):
        from scripts.l2_pipeline import history_reuse
        self.patch.stop()
        with patch.object(history_reuse, 'candidates', return_value=[]):
            self.assertEqual(history_reuse.apply(self.pilot)['records'], [])
        self.patch.start()

    def test_a_source_between_attempts_makes_the_design_wait_not_stop(self):
        from scripts.l2_pipeline import history_reuse
        with patch.object(history_reuse, 'reuse', side_effect=history_reuse.SourceNotReady('source between attempts')):
            self.assertIsNone(self.pilot.reuse_action(self.row, 'history'))
        ledger = [json.loads(line) for line in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        self.assertIn('WAIT_HISTORY_REUSE_SOURCE', {e.get('decision') for e in ledger})
        with patch.object(history_reuse, 'reuse', side_effect=ValueError('different policy definition')):
            with self.assertRaisesRegex(ValueError, 'different policy'):
                self.pilot.reuse_action(self.row, 'history')

    def test_requires_the_yield_fixes_stopped_window(self):
        from scripts.l2_pipeline import history_reuse
        atomic(self.registry / 'CODE_TASK.lock', dict(task='OTHER'))
        with self.assertRaisesRegex(ValueError, 'YIELD_FIXES stopped code window required'):
            history_reuse.apply(self.pilot)
        atomic(self.registry / 'CODE_TASK.lock', dict(task='YIELD_FIXES'))
        (self.registry / 'AUTOPILOT_STOP').unlink()
        with self.assertRaisesRegex(ValueError, 'YIELD_FIXES stopped code window required'):
            history_reuse.apply(self.pilot)


class D8AuditReplayTests(D8CensusV31Tests):
    """audit_passes_now replays the V3.1 recomputation on the bound audit only."""

    def pilot(self, job):
        from types import SimpleNamespace
        return SimpleNamespace(done=lambda row, action: dict(jobs=[job.job_path]) if action == 'audit' else None)

    def test_passing_census_audit_replays_true_and_failing_or_unbound_false(self):
        from scripts.l2_pipeline import c85
        job = self.census(9)
        self.audit(job)
        self.assertTrue(c85.audit_passes_now(self.pilot(job), {}))
        selection = Path(job.contract['selection_path'])
        original = selection.read_text()
        try:
            value = json.loads(original)
            value.pop('remainder_census')
            selection.write_text(json.dumps(value))
            self.assertFalse(c85.audit_passes_now(self.pilot(job), {}))
        finally:
            selection.write_text(original)
        self.assertFalse(c85.audit_passes_now(SimpleNamespaceDone(None), {}))

    def test_real_failure_replays_false(self):
        from scripts.l2_pipeline import c85
        job = self.census(9)
        self.audit(job, errors=3)
        self.assertFalse(c85.audit_passes_now(self.pilot(job), {}))


class SimpleNamespaceDone:
    def __init__(self, value):
        self.value = value

    def done(self, row, action):
        return self.value


class D8ReopenTests(RequeueTests):
    """Bug (a) reopen: the C85 requeue route, only for designs whose audit now passes."""

    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline import c85
        case = self.pilot.case_dir(self.row)
        (case / 'STOP.json').unlink()
        atomic(case / 'PARKED.json', dict(action='S1', reason=c85.V31_PARK_REASON, status='PARKED'))
        atomic(self.registry / 'CODE_TASK.lock', dict(task='YIELD_FIXES'))
        ruling = self.repo / c85.RULE
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text('synthetic C85 ruling')

    def test_passing_census_design_is_requeued_by_resume_and_originals_stay(self):
        from scripts.l2_pipeline import c85
        case = self.pilot.case_dir(self.row)
        before = {str(p): sha(p) for p in [self.oldpath, case / 'PARKED.json']}
        with patch.object(c85, 'audit_passes_now', return_value=True):
            result = c85.reopen_v31(self.pilot)
            self.assertEqual(len(result['records']), 1)
            receipt = result['records'][0]
            self.assertEqual(receipt['retained_actions'], ['history', 'reconcile', 'audit'])
            self.assertEqual(receipt['restart_action'], 'purpose')
            self.assertEqual(result['kept_parked'], [])
            self.assertEqual(before, {p: sha(p) for p in before})
            self.assertEqual(self.pilot.next_action(receipt['new_row']), 'purpose')
            self.assertEqual(c85.reopen_v31(self.pilot), result)

    def test_failing_audit_stays_parked(self):
        from scripts.l2_pipeline import c85
        with patch.object(c85, 'audit_passes_now', return_value=False):
            result = c85.reopen_v31(self.pilot)
        self.assertEqual(result['records'], [])
        self.assertEqual(result['kept_parked'], [self.row['design_id']])
        self.assertEqual(self.pilot.next_action(self.row), 'PARK')

    def test_other_reasons_and_actions_are_not_touched(self):
        from scripts.l2_pipeline import c85
        case = self.pilot.case_dir(self.row)
        (case / 'PARKED.json').unlink()
        atomic(case / 'PARKED.json', dict(action='S3', reason='Registered support insufficient'))
        with patch.object(c85, 'audit_passes_now', return_value=True):
            self.assertEqual(c85.reopen_v31(self.pilot)['records'], [])
        (case / 'PARKED.json').unlink()
        atomic(case / 'PARKED.json', dict(action='S2', reason=c85.V31_PARK_REASON))
        (self.registry.parent / 'autopilot/yield_fixes/d8_v31/APPLICATION.json').unlink()
        with patch.object(c85, 'audit_passes_now', return_value=True):
            self.assertEqual(c85.reopen_v31(self.pilot)['records'], [])

    def test_frozen_design_and_wrong_window_are_refused(self):
        from scripts.l2_pipeline import c85
        ensure_once(self.pilot.backend.root(self.row) / 'SEALED_REVIEW.json', dict(synthetic=True))
        with patch.object(c85, 'audit_passes_now', return_value=True):
            self.assertEqual(c85.reopen_v31(self.pilot)['records'], [])
        atomic(self.registry / 'CODE_TASK.lock', dict(task='C85'))
        with self.assertRaisesRegex(ValueError, 'YIELD_FIXES stopped code window required'):
            c85.reopen_v31(self.pilot)


class CatalogV16Tests(unittest.TestCase):
    """Catalog V16 = V15 plus local bindings for the three series that parked at S2."""
    ROOT = Path(__file__).resolve().parents[3]

    def versions(self):
        from scripts.l2_pipeline.connectors import outcome_v15, outcome_v16
        return outcome_v15.catalog(verify_sources=False), outcome_v16.catalog(verify_sources=False), outcome_v16

    def test_v16_equals_v15_except_the_registered_name_and_three_local_bindings(self):
        a, b, module = self.versions()
        self.assertEqual(set(a['series']), set(b['series']))
        changed = set()
        for sid in a['series']:
            x, y = dict(a['series'][sid]), dict(b['series'][sid])
            self.assertEqual(y.pop('registered_connector'), 'outcome_v16_' + sid)
            x.pop('registered_connector')
            if x != y:
                changed.add(sid)
                delta = {k for k in set(x) | set(y) if x.get(k) != y.get(k)}
                allowed = {'paths', 'source_sha256', 'locally_downloaded_years', 'local_source_manifest', 'local_source_note'}
                if sid == 'acs1_snap_household_receipt_rate':
                    allowed.add('years')
                self.assertLessEqual(delta, allowed, sid)
        self.assertEqual(changed, set(module.LOCAL_SERIES))
        self.assertEqual(a['targets'], b['targets'])
        self.assertEqual(b['schema_version'], 16)
        self.assertEqual(b['catalog_id'], 'first_order_outcomes_v16')
        self.assertEqual(b['inherited_catalog']['sha256'], sha(module.CATALOG_PATH.with_name('OUTCOME_CATALOG_V15.json')))

    def test_populations_v16_only_renames_the_catalog(self):
        base = Path(__file__).resolve().parents[1] / 'connectors'
        a = json.loads((base / 'OUTCOME_POPULATIONS_V15.json').read_text())
        b = json.loads((base / 'OUTCOME_POPULATIONS_V16.json').read_text())
        self.assertEqual(b.pop('catalog_id'), 'first_order_outcomes_v16')
        a.pop('catalog_id')
        self.assertEqual(b.pop('inherited_population_metadata')['sha256'], sha(base / 'OUTCOME_POPULATIONS_V15.json'))
        a.pop('inherited_population_metadata')
        self.assertEqual(a, b)

    def test_current_catalog_is_v19_after_the_2026_10_23_switch_v16_stays_registered(self):
        from scripts.l2_pipeline import gate0
        from scripts.l2_pipeline.connectors import population_v63, CONNECTOR_CATALOG
        source, meta = population_v63.current_catalog()
        self.assertEqual(source['catalog_id'], 'first_order_outcomes_v19')  # window 2026-10-17: current moved V16 -> V17; window 2026-10-23: V17 -> V19
        self.assertEqual(meta['catalog_id'], 'first_order_outcomes_v19')
        for sid in ('bls_laus_employment', 'bls_ces_private_weekly_earnings', 'acs1_snap_household_receipt_rate'):
            self.assertIn('outcome_v16_' + sid, CONNECTOR_CATALOG)
        identity = gate0.catalog_identity(self.ROOT)
        self.assertEqual(identity['catalog_id'], 'first_order_outcomes_v19')
        self.assertTrue(identity['path'].endswith('OUTCOME_POPULATIONS_V19.json'))

    @unittest.skipUnless((ROOT / 'data/l2_pipeline/connectors_v2/yield_fixes_20261016/SOURCE_MANIFEST.json').is_file(), 'local public downloads absent')
    def test_bound_local_files_exist_and_hash(self):
        a, b, module = self.versions()
        data = module.catalog(verify_sources=True)
        for sid in module.LOCAL_SERIES:
            spec = data['series'][sid]
            self.assertTrue(spec['paths'])
            self.assertEqual(set(spec['paths']), set(spec['source_sha256']))
            self.assertTrue(spec['locally_downloaded_years'])
            manifest = spec['local_source_manifest']
            self.assertEqual(sha(self.ROOT / manifest['path']), manifest['sha256'])
        acs = data['series']['acs1_snap_household_receipt_rate']
        self.assertEqual(acs['years'], acs['locally_downloaded_years'])
        self.assertNotIn(2020, acs['years'])

    @unittest.skipUnless((ROOT / 'data/l2_pipeline/connectors_v2/yield_fixes_20261016/SOURCE_MANIFEST.json').is_file(), 'local public downloads absent')
    def test_build_is_repeatable_and_reads_no_outcome_values(self):
        from scripts.l2_pipeline.connectors import yield16_build
        from scripts.l2_pipeline.connectors.common import STATES
        self.assertEqual(yield16_build.STATES, STATES)  # the builder carries a literal copy
        first = yield16_build.build()
        self.assertEqual(yield16_build.build(), first)
        self.assertEqual((first['outcome_values_read'], first['outcome_association_calls'], first['actual_data_estimation_calls']), (0, 0, 0))
        manifest = json.loads((self.ROOT / first['manifest']['path']).read_text())
        self.assertEqual(manifest['outcome_values_read'], 0)
        self.assertEqual({b['url'] for b in manifest['blocked_urls']}, {
            'https://download.bls.gov/pub/time.series/la/la.data.2.AllStatesU',
            'https://download.bls.gov/pub/time.series/sm/sm.data.56.TotalPrivate.Current'})
        self.assertTrue(all(row['url'] for rows in manifest['sources'].values() for row in rows if row.get('artifact_role') != 'derived_panel_input'))

    @unittest.skipUnless((ROOT / 'data/l2_pipeline/connectors_v2/yield_fixes_20261016/SOURCE_MANIFEST.json').is_file(), 'local public downloads absent')
    def test_card_outcome_bound_to_v16_resolves_the_local_files(self):
        from scripts.l2_pipeline.prescreen import bind_outcome
        from scripts.l2_pipeline.connectors import outcome_v16
        a, b, module = self.versions()
        for sid in module.LOCAL_SERIES:
            outcome = dict(connector='outcome_v16_' + sid, series_id=sid, catalog_path=str(outcome_v16.CATALOG_PATH.relative_to(self.ROOT)),
                           catalog_sha256=sha(outcome_v16.CATALOG_PATH))
            bound = bind_outcome(outcome)
            self.assertEqual(len(bound['paths']), len(b['series'][sid]['paths']))
            self.assertTrue(all(Path(p).is_file() for p in bound['paths']))
            self.assertEqual(set(bound['source_sha256']), set(bound['paths']))
        old = dict(connector='outcome_v15_bls_laus_employment', series_id='bls_laus_employment',
                   catalog_path='scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json',
                   catalog_sha256=sha(self.ROOT / 'scripts/l2_pipeline/connectors/OUTCOME_CATALOG_V15.json'))
        self.assertNotIn('paths', bind_outcome(old))  # V15 cards keep their bytes and meaning

    @unittest.skipUnless((ROOT / 'data/l2_pipeline/connectors_v2/yield_fixes_20261016/SOURCE_MANIFEST.json').is_file(), 'local public downloads absent')
    def test_connectors_build_a_panel_from_each_bound_series(self):
        from scripts.l2_pipeline.connectors import construct, ConnectorRequest, outcome_v16
        a, b, module = self.versions()
        cases = {'bls_laus_employment': ([2005, 2006], 'persons'), 'bls_ces_private_weekly_earnings': ([2015, 2016], 'nominal USD per week'),
                 'acs1_snap_household_receipt_rate': ([max(b['series']['acs1_snap_household_receipt_rate']['years']) - 1,
                                                       max(b['series']['acs1_snap_household_receipt_rate']['years'])], 'percent')}
        for sid, (years, unit) in cases.items():
            with self.subTest(sid):
                spec = b['series'][sid]
                card = dict(population=dict(filters={}), outcome=dict(
                    connector='outcome_v16_' + sid, series_id=sid, measure_id=sid, catalog_path=str(outcome_v16.CATALOG_PATH.relative_to(self.ROOT)),
                    catalog_sha256=sha(outcome_v16.CATALOG_PATH), years=[years[0], years[-1]], period_years=years, units=['AL', 'CA', 'DC'],
                    frequency='annual', population_filters={}, source_population_filters={}, outcome_unit=unit, source_outcome_unit=unit,
                    source_validation_required=True))
                panel = construct(ConnectorRequest.from_card(card, self.ROOT))
                self.assertEqual(len(panel.rows), 3 * len(years))
                self.assertTrue(all(r['outcome_value'] != '' for r in panel.rows), sid)
                self.assertEqual({r['unit'] for r in panel.rows}, {'AL', 'CA', 'DC'})
                self.assertTrue(panel.inventory)


class PrescreenReuseTests(unittest.TestCase):
    """Ruling (ii): the fresh-pool pre-screen check is for fresh audits only."""

    def test_reuse_declared_card_needs_no_fresh_pool_and_others_still_do(self):
        from scripts.l2_pipeline.tests.test_gate0 import fixture
        from scripts.l2_pipeline.prescreen import evaluate
        from scripts.l2_pipeline import history_reuse
        with tempfile.TemporaryDirectory() as tmp, patch('scripts.l2_pipeline.population_resolution.choose', side_effect=ValueError('new construct')):
            root = Path(tmp)
            card = fixture(root)
            units = list(card['treatment']['screening_adoption']['adopters'])
            plain = evaluate(card, root, excluded_units=units)
            self.assertEqual(plain['status'], 'PRESCREEN_PARK')
            self.assertTrue(all(not v['fresh_pass'] for v in plain['variants']))
            reused = deepcopy(card)
            reused.setdefault('automation', {})['history_reuse'] = dict(source_design_id='source', rule=history_reuse.RULING)
            result = evaluate(reused, root, excluded_units=units)
            self.assertEqual(result['status'], 'PROCEED')
            self.assertTrue(all(v['fresh_pass'] and v['fresh_pool_n'] == 0 for v in result['variants']))


class TickReuseTests(unittest.TestCase):
    """The tick routes a declared row through the reuse checkpoints, never through A/B history jobs."""

    def setUp(self):
        from scripts.l2_pipeline.autopilot import Autopilot
        from scripts.l2_pipeline.tests.test_autopilot import MockAgents
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.backend = MockAgents(self.repo)
        self.pilot = Autopilot(self.registry, self.backend)

    def row(self, declared=True):
        from scripts.l2_pipeline import history_reuse
        card = dict(automation=dict(history_reuse=dict(source_design_id='src', rule=history_reuse.RULING)) if declared else {})
        path = ensure_once(self.repo / ('card_%s.json' % declared), card)
        row = dict(design_id='d%s' % declared, attempt_id='test', work_dir=str(self.repo / 'data/l2_pipeline/work'),
                   card=str(path), card_sha256=sha(path), route='V9', managed_execution=True)
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        return row

    def canned(self):
        return dict(source_design_id='src', record='record.json', checkpoints=dict(
            history=dict(jobs=['A', 'B'], reused_from='src'),
            reconcile=dict(jobs=[], reused_from='src', counts={'x': 1}, excluded_conflict_states=['U0'],
                           excluded_conflict_state_reasons={'U0': ['conflict']}),
            audit=dict(jobs=['J'], reused_from='src')))

    def test_declared_row_takes_the_reuse_checkpoints_and_emits_no_history_job(self):
        from scripts.l2_pipeline import history_reuse
        row = self.row(True)
        with patch.object(history_reuse, 'reuse', return_value=self.canned()):
            self.pilot.tick()
        for action in ('history', 'reconcile', 'audit'):
            self.assertEqual(self.pilot.done(row, action), self.canned()['checkpoints'][action])
        self.assertFalse((self.pilot.case_dir(row) / 'jobs/history.json').exists())
        self.assertNotIn((row['design_id'], 'reconcile'), self.backend.actions)
        ledger = [json.loads(line) for line in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        decisions = {e.get('decision') for e in ledger}
        self.assertLessEqual({'HISTORY_REUSED', 'EXCLUDE'}, decisions)
        self.assertEqual(self.pilot.next_action(row), 'purpose')

    def test_an_undeclared_row_still_codes_its_own_history(self):
        from scripts.l2_pipeline import history_reuse
        row = self.row(False)
        with patch.object(history_reuse, 'reuse', side_effect=AssertionError('must not be called')):
            self.pilot.tick()
        self.assertIsNone(self.pilot.done(row, 'history'))
        self.assertTrue((self.pilot.case_dir(row) / 'jobs/history.json').exists())

    def test_refused_reuse_stops_the_design_for_the_hub(self):
        from scripts.l2_pipeline import history_reuse
        row = self.row(True)
        with patch.object(history_reuse, 'reuse', side_effect=ValueError('History reuse refused; different policy definition: treatment.universe')):
            self.pilot.tick()
        self.assertIn('different policy definition', self.pilot.stop_record(row)['reason'])


class ReadmeRefreshTests(unittest.TestCase):
    """Bug (d): the registry copy of WORKER_README.md stayed at the 2026-10-10 text."""

    def test_stale_readme_is_replaced_once_with_the_old_text_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'WORKER_README.md').write_text('old 2026-10-10 text: exclusive creation; never overwrite')
            self.assertTrue(q.refresh_readme(root))
            self.assertEqual((root / 'WORKER_README.md').read_text(), q.WORKER_INSTRUCTIONS)
            self.assertEqual((root / 'WORKER_README.before-20261016.md').read_text(), 'old 2026-10-10 text: exclusive creation; never overwrite')
            self.assertFalse(q.refresh_readme(root))
            # A second stale version never overwrites the first saved copy.
            (root / 'WORKER_README.md').write_text('another version')
            self.assertTrue(q.refresh_readme(root))
            self.assertIn('never overwrite', (root / 'WORKER_README.before-20261016.md').read_text())

    def test_current_text_names_citation_only_sources_and_the_row_correction(self):
        # NAMED_PANEL Q3 replaced the 'browser-only provenance' allowance by fetch-evidence / citation-only rows.
        text = q.WORKER_INSTRUCTIONS
        self.assertIn('citation-only', text)
        self.assertNotIn('browser-only', text)
        self.assertIn('Never invent a hash, quote', text)
        self.assertIn('same single correction', text)
        self.assertIn('self-check --job-id', text)


for _cls, _parent in ((D8CensusV31Tests, OfficialBridgeTests), (RequeueGate0CatalogTests, RequeueTests), (Gate0StaleMarkerTests, RequeueTests), (V9RowCorrectionTests, OfficialBridgeTests), (HistoryReuseTests, OfficialBridgeTests), (HistoryReuseApplyTests, RequeueTests), (D8ReopenTests, RequeueTests), (D8AuditReplayTests, OfficialBridgeTests)):
    for _name in dir(_parent):
        if _name.startswith('test_') and _name not in _cls.__dict__:
            setattr(_cls, _name, None)
del OfficialBridgeTests, RequeueTests, _cls, _parent, _name


if __name__ == '__main__':
    unittest.main()
