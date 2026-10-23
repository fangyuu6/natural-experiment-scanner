"""Window 2026-10-17 pure-engineering fixes F1-F6 (YIELD_DIAGNOSTIC_20261017). Synthetic data only; no outcome values."""
import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import packet_scope as scope
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_agents as fixtures


READABLE = 20000  # no packet file may have a line longer than this (a one-line 100-466 KB JSON was unreadable)


def longest_line(path):
    return max(len(line) for line in Path(path).read_bytes().split(b'\n'))


# --- F1: S6 review packets a Claude worker can read ----------------------------------------------------------------

class F1PageablePacketTests(unittest.TestCase):
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown
    emit = fixtures.AgentTests.emit

    def big_job(self, size=60000):
        job = self.emit('S5', 'Card')
        return replace(job, contract=dict(job.contract, filler=['x' * 50 for _ in range(size // 50)]))

    def enqueue_big(self):
        job = self.big_job()
        root = self.root / 'queue'
        with patch('scripts.l2_pipeline.agents.jobs.load_job', return_value=job), \
                patch('scripts.l2_pipeline.agents.contract_tests.check_job_types'):
            receipt = q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='invented'), root)
        return job, root, Path(receipt['packet'])

    def test_enqueued_packet_files_have_no_giant_line_and_hashes_bind_the_written_bytes(self):
        job, root, packet = self.enqueue_big()
        for name in ('PACKET.json', 'OUTPUT_SCHEMA.json', 'PROMPT.md', 'WORKER_README.md', 'STATE.json'):
            self.assertLessEqual(longest_line(packet / name), READABLE, name)
        binding = q.read(packet / 'PACKET.json')
        schema = q.read(packet / 'OUTPUT_SCHEMA.json')
        self.assertEqual(schema['contract']['filler'], job.contract['filler'])           # same content, readable form
        self.assertEqual(binding['output_schema_sha256'], sha(packet / 'OUTPUT_SCHEMA.json'))
        self.assertEqual(binding['prompt_sha256'], sha(packet / 'PROMPT.md'))
        self.assertGreater(len((packet / 'OUTPUT_SCHEMA.json').read_text().splitlines()), 1000)

    def test_a_pretty_packet_still_claims_validates_its_bytes_and_cannot_be_enqueued_twice(self):
        job, root, packet = self.enqueue_big()
        lease = q.claim(root, 'worker', now=100)
        self.assertIsNotNone(lease)
        self.assertTrue(Path(lease['read_scope']).is_file())
        self.assertLessEqual(longest_line(lease['read_scope']), READABLE)
        claimed = Path(lease['packet'])
        binding = q.read(claimed / 'PACKET.json')
        self.assertEqual(sha(claimed / 'OUTPUT_SCHEMA.json'), binding['output_schema_sha256'])   # what finish() re-checks
        with patch('scripts.l2_pipeline.agents.jobs.load_job', return_value=self.big_job()), \
                patch('scripts.l2_pipeline.agents.contract_tests.check_job_types'):
            again = q.enqueue(job.job_path, binding['row'], root)
        self.assertEqual(Path(again['packet']), claimed)

    def read_scope_packet(self, text):
        """A packet whose allowed read file is `text` (bytes written as given)."""
        packet = self.root / 'packet'
        source = self.root / 'review-inputs.json'
        source.write_bytes(text)
        packet.mkdir()
        ensure_once(packet / 'PACKET.json', dict(allowed_read_paths=[str(source)]))
        return packet, source

    def test_staged_json_with_a_giant_line_is_pretty_while_the_canonical_bytes_stay_bound(self):
        data = json.dumps(dict(items=[dict(i=i, text='y' * 40) for i in range(8000)], z=1), sort_keys=True, separators=(',', ':'))
        self.assertGreater(len(data), 300000)
        packet, source = self.read_scope_packet(data.encode())
        canonical = source.read_bytes()
        value = scope.stage(packet)
        row = value['files'][0]
        staged = packet / row['staged']
        self.assertEqual(source.read_bytes(), canonical)                                 # canonical bytes untouched
        self.assertEqual(row['source_sha256'], sha(source))                              # the hash the prompt binds
        self.assertEqual(row['sha256'], sha(staged))                                     # what read_allowed() checks
        self.assertEqual(row['staged_form'], 'pretty_json_v1')
        self.assertNotEqual(row['sha256'], row['source_sha256'])
        self.assertLessEqual(longest_line(staged), scope.PAGEABLE_LINE_LIMIT)
        self.assertEqual(json.loads(staged.read_text()), json.loads(canonical))          # identical JSON content
        self.assertEqual(scope.read_allowed(packet, row['staged']), staged.read_bytes())
        self.assertLessEqual(longest_line(packet / 'READ_SCOPE.json'), READABLE)
        self.assertEqual(scope.stage(packet), value)                                     # second call re-verifies the same manifest

    def test_short_json_other_files_and_unparseable_long_lines_are_staged_verbatim(self):
        for name, data in (('short.json', b'{"a":1}'), ('notes.txt', b'z' * 50000), ('broken.json', b'{' + b'q' * 50000)):
            with self.subTest(name=name):
                packet = self.root / ('p_' + name)
                source = self.root / name
                source.write_bytes(data)
                packet.mkdir()
                ensure_once(packet / 'PACKET.json', dict(allowed_read_paths=[str(source)]))
                row = scope.stage(packet)['files'][0]
                self.assertEqual(row['staged_form'], 'verbatim')
                self.assertEqual((packet / row['staged']).read_bytes(), data)
                self.assertEqual(row['sha256'], row['source_sha256'])

    def test_worker_text_tells_the_worker_how_to_page(self):
        self.assertIn('offset and limit', q.WORKER_INSTRUCTIONS)
        self.assertIn('source_sha256', q.WORKER_INSTRUCTIONS)


# --- F2: requeue(resume) keeps an accepted purpose ------------------------------------------------------------------

def window_lock(registry, task='WINDOW_20261017'):
    (registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task=task, time='2026-10-17T07:23:00+00:00')))


class F2ResumeKeepsPurposeTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.tests import test_c46
        test_c46.RequeueTests.setUp(self)  # fixture only: design stopped at purpose, history/reconcile/audit done
        self.value = dict(self.value, decision='resume')
        atomic(self.ruling, self.value)

    def accept_purpose(self, name='PURPOSE_HUB_RULING_V62.json', pointer=False):
        root = self.pilot.backend.root(self.row)
        ruling = dict(schema_version='6.2', purpose_rule='V6.2', decision='accept', design_id=self.row['design_id'],
                      card_sha256=sha(self.oldpath), execution_authorized=False, card_patch={})
        path = ensure_once(root / name, ruling)
        ensure_once(self.pilot.checkpoint(self.row, 'purpose'), ruling)
        if pointer:
            row = dict(self.row, purpose_ruling=dict(path=str(path), sha256=sha(path)))
            atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
            self.row = row
        return path

    def test_an_accepted_purpose_survives_resume_and_binds_to_the_new_card(self):
        from scripts.l2_pipeline.requeue import requeue
        old_ruling = self.accept_purpose()
        value = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(value['restart_action'], 'purpose-direction')
        self.assertEqual(value['retained_actions'], ['history', 'reconcile', 'audit', 'purpose'])
        self.assertEqual(value['purpose_kept_from'], 'old')
        row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(self.pilot.next_action(row), 'purpose-direction')
        pointer = row['purpose_ruling']
        self.assertEqual(sha(pointer['path']), pointer['sha256'])
        kept = read(pointer['path'])
        self.assertEqual((kept['card_sha256'], kept['decision'], kept['purpose_rule']), (sha(self.newpath), 'accept', 'V6.2'))
        self.assertEqual(kept['rebound_from'], dict(path=str(old_ruling), sha256=sha(old_ruling)))
        self.assertIs(kept['execution_authorized'], False)
        self.assertEqual(read(self.pilot.checkpoint(row, 'purpose')), kept)
        # the purpose step itself accepts it (autopilot.perform: decision, design, card hash and rule are all checked there)
        self.assertEqual(self.pilot.backend.perform(row, 'purpose', self.pilot), kept)
        self.assertEqual(sha(old_ruling), self.value_hash(old_ruling))                      # the old ruling is untouched

    def value_hash(self, path):
        return sha(path)

    def test_a_changed_target_or_population_or_outcome_still_re_verifies_the_purpose(self):
        from scripts.l2_pipeline.requeue import requeue
        self.accept_purpose()
        new = read(self.newpath)
        new['first_order'] = dict(new.get('first_order', {}), target_outcome='another_construct')
        atomic(self.newpath, new)
        atomic(self.ruling, dict(self.value, card_sha256=sha(self.newpath)))
        value = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(value['restart_action'], 'purpose')
        self.assertEqual(value['retained_actions'], ['history', 'reconcile', 'audit'])
        self.assertNotIn('purpose_kept_from', value)
        self.assertNotIn('purpose_ruling', value['new_row'])

    def test_a_ruling_whose_bytes_changed_or_that_is_not_an_acceptance_is_not_kept(self):
        from scripts.l2_pipeline.requeue import requeue, kept_purpose
        path = self.accept_purpose(pointer=True)
        path.chmod(0o644)
        path.write_text(path.read_text() + ' ')
        self.assertIsNone(kept_purpose(self.pilot, self.row, read(self.newpath)))
        value = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(value['restart_action'], 'purpose')

    def test_a_design_without_an_accepted_purpose_behaves_as_before(self):
        from scripts.l2_pipeline.requeue import requeue
        value = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual((value['restart_action'], value['retained_actions']), ('purpose', ['history', 'reconcile', 'audit']))

    def test_accept_and_park_decisions_are_unchanged(self):
        from scripts.l2_pipeline.requeue import requeue
        self.accept_purpose()
        atomic(self.ruling, dict(self.value, decision='park'))
        value = requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(value['restart_action'], 'PARK')
        self.assertNotIn('purpose_kept_from', value)

    def stopped_after_a_dropped_purpose(self):
        """The D8 history: v1 accepted its purpose, the old resume dropped it, the re-run STOPped at purpose."""
        from scripts.l2_pipeline import requeue as module
        self.accept_purpose()
        with patch.object(module, 'kept_purpose', return_value=None):                       # behaviour before the fix
            first = module.requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(first['restart_action'], 'purpose')
        row = first['new_row']
        self.pilot.stop(row, 'purpose', 'ValueError: NEEDS_RULING: V6.2 target/population requires new interpretation')
        self.third = self.repo / 'data/l2_pipeline/cards/new_v3.json'
        atomic(self.third, dict(read(self.newpath), version='v3'))
        return row

    def test_the_stopped_designs_get_their_purpose_back_from_the_earlier_attempt(self):
        from scripts.l2_pipeline.requeue import requeue
        row = self.stopped_after_a_dropped_purpose()
        ruling = self.repo / 'data/l2_pipeline/rulings/ruling_v3.json'
        atomic(ruling, dict(self.value, prior_card_sha256=sha(self.newpath), card_sha256=sha(self.third)))
        value = requeue(self.card['design_id'], self.third, ruling, self.pilot)
        self.assertEqual(value['purpose_kept_from'], 'old')
        self.assertEqual(value['restart_action'], 'purpose-direction')
        new_row = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual(new_row['attempt_id'], 'requeue_v3')
        self.assertEqual(read(new_row['purpose_ruling']['path'])['card_sha256'], sha(self.third))

    def test_window_step_reopens_only_the_class_designs_stopped_at_purpose_with_a_status_of_its_own(self):
        from scripts.l2_pipeline import purpose_kept_reopen as m
        from scripts.l2_pipeline.c85 import RULE
        self.stopped_after_a_dropped_purpose()
        (self.repo / RULE).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / RULE).write_text('fixture C85 ruling')
        window_lock(self.registry)
        self.assertEqual(len(m.CLASS), 5)
        self.assertEqual(m.apply(self.pilot), [])                                         # the fixture design is not in the class
        with patch.object(m, 'CLASS', (self.card['design_id'],)):
            done = m.apply(self.pilot)
            self.assertEqual([d['restart_action'] for d in done], ['purpose-direction'])
            self.assertEqual(done[0]['purpose_kept_from'], 'old')
            statuses = [e['status'] for e in read(self.registry / 'HUB_QUEUE.json')['entries']]
            self.assertIn(m.STATUS, statuses)
            self.assertEqual(m.apply(self.pilot), [])                                      # once: the design is no longer stopped
            window_lock(self.registry, 'OTHER')
            with self.assertRaisesRegex(ValueError, 'stopped code window'):
                m.apply(self.pilot)

    def test_the_new_status_is_whitelisted_and_nothing_else_was_added(self):
        text = (Path(__file__).parents[1] / 'requeue.py').read_text()
        self.assertIn("'SUPERSEDED_PURPOSE_KEPT'", text)
        atomic(self.ruling, dict(self.value, superseded_status='SUPERSEDED_MADE_UP'))
        from scripts.l2_pipeline.requeue import requeue
        with self.assertRaisesRegex(ValueError, 'Invalid superseded status'):
            requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)


# --- F3: an honest UNRESOLVED purpose is a documented PARK, never a STOP --------------------------------------------

class F3UnresolvedParkTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.tests import test_v6_purpose_jobs as fixture
        fixture.PurposeTests.setUp(self)
        self.addCleanup(self.tearDown)
        self.backend = Backend(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, **changes):
        from scripts.l2_pipeline.tests import test_v6_purpose_jobs as fixture
        value = fixture.PurposeTests.output(self)
        value.update(changes)
        out = Path(self.job.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(value))
        return value

    def unresolved(self, **changes):
        changes.setdefault('unresolved_reason', 'Only the abstract names the aggregate, not this outcome')
        return self.draft(status='UNRESOLVED', **changes)

    def paper(self, ref='https://papers.test/paper1', title='Invented primary paper', supports=('target_outcome',), **extra):
        return dict(source_type='paper', source_ref=ref, title=title, locator='Section 2', supports=list(supports), **extra)

    def collect(self):
        from scripts.l2_pipeline.c42 import Park
        try:
            self.backend.collect(self.job.job_path)
        except Park as exc:
            return exc
        return None

    def test_every_evidence_shortfall_of_an_unresolved_answer_is_a_park_with_the_workers_reason(self):
        from scripts.l2_pipeline.c42 import Park
        one_paper = [self.paper(supports=('target_outcome', 'target_population'))]
        two_papers = one_paper + [self.paper(ref='https://papers.test/paper2', title='Second study')]
        cases = {
            'V6.2 target requires': dict(citations=one_paper),
            'located target and population support': dict(citations=[self.paper()]),
            'identify supported fields': dict(citations=[self.paper(supports=('Establishes the outcome', 'target_population'))]),
            'lacks type/title/locator': dict(citations=[self.paper(title='')]),
            'not search links': dict(citations=[self.paper(ref='https://papers.test/search?q=outcome')]),
            '1–3 citations': dict(citations=[]),
            'cite every reference': dict(citations=two_papers, confirmation='No marker here'),
            'one-line target_population': dict(target_population=''),
            'population metadata invalid': dict(citations=two_papers, confirmation='Both [1] [2]', target_population_metadata=dict(ages=[1])),
        }
        for label, changes in cases.items():
            with self.subTest(label):
                self.unresolved(**changes)
                exc = self.collect()
                self.assertIsInstance(exc, Park, label)
                self.assertIn(label, exc.metrics['collector_error'])
                self.assertEqual(str(exc), 'S0 purpose unresolved: Only the abstract names the aggregate, not this outcome')
                self.assertEqual((exc.metrics['kind'], exc.metrics['outcome_values_read']), ('PURPOSE_UNRESOLVED', 0))

    def test_the_same_defects_in_a_verified_answer_are_not_parked(self):
        from scripts.l2_pipeline.c42 import Park
        for changes in (dict(citations=[self.paper(supports=('target_outcome', 'target_population'))]), dict(citations=[self.paper()])):
            self.draft(**changes)
            with self.assertRaises(ValueError) as caught:
                self.backend.collect(self.job.job_path)
            self.assertNotIsInstance(caught.exception, Park)

    def test_an_unresolved_answer_that_is_malformed_in_other_ways_stays_an_error(self):
        from scripts.l2_pipeline.c42 import Park
        for changes in (dict(unresolved_reason=''), dict(card_sha256='0' * 64), dict(internal_persona_lens='Someone')):
            self.unresolved(**dict(changes, citations=[self.paper()]))
            with self.assertRaises(ValueError) as caught:
                self.backend.collect(self.job.job_path)
            self.assertNotIsInstance(caught.exception, Park, changes)

    def test_a_clean_unresolved_answer_is_collected_and_the_purpose_step_parks_it(self):
        from types import SimpleNamespace
        from scripts.l2_pipeline.c42 import Park
        value = self.unresolved(citations=[self.paper(supports=('target_outcome', 'target_population')),
                                           self.paper(ref='https://papers.test/paper2', title='Second study', supports=('target_outcome',))])
        self.assertIsNone(self.collect())
        ctx = SimpleNamespace(design_dir=self.root / 'design', read=lambda name: dict(value, status='UNRESOLVED'))
        (self.root / 'design').mkdir()
        with patch.object(Backend, 'ctx', return_value=ctx):
            with self.assertRaises(Park) as caught:
                self.backend.perform(dict(design_id='d', attempt_id='a'), 'purpose', None)
        self.assertTrue(str(caught.exception).startswith('S0 purpose unresolved: Only the abstract'))
        self.assertEqual(caught.exception.metrics['kind'], 'PURPOSE_UNRESOLVED')

    def test_unresolved_and_new_construct_questions_are_parks_that_remain_value_errors(self):
        from scripts.l2_pipeline.c42 import Park
        from scripts.l2_pipeline import population_resolution as pr
        card = dict(policy_family='f')
        with self.assertRaises(Park) as caught:
            pr.choose(card, dict(status='UNRESOLVED'), source={}, metadata={})
        self.assertEqual(str(caught.exception), 'NEEDS_RULING: unresolved construct question')
        self.assertIsInstance(caught.exception, ValueError)
        with patch.object(pr.population_v63, 'target_metadata', return_value=None):
            with self.assertRaises(Park) as caught:
                pr.choose(card, dict(status='VERIFIED', target_outcome='brand_new_outcome'), source={}, metadata={})
        self.assertEqual(str(caught.exception), 'NEEDS_RULING: genuinely new construct question: brand_new_outcome')
        self.assertEqual(caught.exception.metrics['kind'], 'PURPOSE_NEW_CONSTRUCT')


# --- F4: the prompt and the validator agree on `supports` -----------------------------------------------------------

class F4SupportsTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.tests import test_c64_purpose as fixture
        fixture.C64PurposeTests.setUp(self)
        self.addCleanup(self.tmp.cleanup)
        self.fixture = fixture

    def draft(self, supports):
        value = self.fixture.C64PurposeTests.output(self)
        value['citations'][0]['supports'] = supports
        out = Path(self.job.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(value))
        return value

    def test_the_c64_route_uses_a_prompt_that_asks_for_exactly_what_the_validator_accepts(self):
        from scripts.l2_pipeline.agents import purpose
        self.assertTrue(self.job.template_path.endswith('purpose_verifier_c86.md'))
        self.assertEqual(Path(purpose.C64_TEMPLATE).name, 'purpose_verifier_c86.md')
        text = Path(self.job.job_path).read_text()
        self.assertNotIn('must describe', text)
        self.assertIn('only allowed items are the exact strings "target_outcome" and "target_population"', text)
        self.assertIn('never in supports', text)
        block = text.split('"supports": {', 1)[1].split('}', 2)[0]
        self.assertIn('"enum"', block)
        self.assertIn('"minItems": 1', block)
        old = (Path(purpose.__file__).parent / 'templates/v6/purpose_verifier_c64.md').read_text()
        self.assertIn('Each supports entry must describe', old)                          # old jobs stay bound to the old bytes

    def test_the_validator_accepts_exactly_the_two_words_and_the_common_descriptive_slip(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        for supports in (['target_outcome', 'target_population'],
                         ['target_outcome: names burglary as the dependent variable (Table 3)', 'target_population - eligibility clause'],
                         ['target_outcome (Table 3)', 'target_population;']):
            with self.subTest(supports=supports):
                value = self.draft(supports)
                normalized = validate_purpose_output(self.job, value)
                self.assertEqual(normalized['citations'][0]['supports'], ['target_outcome', 'target_population'])
        value = self.draft(['target_outcome: a', 'target_outcome: b', 'target_population'])
        self.assertEqual(validate_purpose_output(self.job, value)['citations'][0]['supports'], ['target_outcome', 'target_population'])

    def test_free_text_and_unknown_words_are_still_refused(self):
        from scripts.l2_pipeline.agents.purpose import validate_purpose_output
        for supports in (['Establishes that the statute targets the outcome'], [], ['target_outcomes'], ['outcome', 'target_population'],
                         'target_outcome', [1], ['target_outcome: ok', 'free text']):
            with self.subTest(supports=supports):
                with self.assertRaisesRegex(ValueError, 'must identify supported fields'):
                    validate_purpose_output(self.job, self.draft(supports))

    def test_a_collected_slip_is_promoted_with_the_clean_words_and_the_original_is_kept_as_written(self):
        from scripts.l2_pipeline.agents import collect_job
        self.draft(['target_outcome: names the outcome', 'target_population: scope clause'])
        original = Path(self.job.output_path).read_bytes()
        self.assertTrue(collect_job(self.job.job_path)['validated'])
        promoted = json.loads(Path(self.job.promote_path).read_text())
        self.assertEqual(promoted['citations'][0]['supports'], ['target_outcome', 'target_population'])
        self.assertEqual(Path(self.job.output_path).read_bytes(), original)

    def test_a_worker_whose_supports_is_free_text_is_told_the_allowed_words_in_the_one_correction(self):
        root = self.root / 'queue'
        receipt = q.enqueue(self.job.job_path, dict(design_id=self.job.design_id, attempt_id='invented'), root)
        packet = Path(receipt['packet'])
        self.draft(['Establishes that the statute targets the outcome', 'target_population'])
        result = q.validate_submission(packet)
        self.assertTrue(result['error'].endswith(q.SUPPORTS_HINT))
        self.assertIn('exactly "target_outcome" or "target_population"', result['error'])
        self.assertTrue(result['correction_allowed'])
        self.assertTrue(q.read(packet / 'CORRECTION.json')['error'].endswith(q.SUPPORTS_HINT))
        self.assertIn('exact words "target_outcome" and/or', q.WORKER_INSTRUCTIONS.replace('\n', ' '))

    def test_other_collector_errors_get_no_hint(self):
        root = self.root / 'queue'
        receipt = q.enqueue(self.job.job_path, dict(design_id=self.job.design_id, attempt_id='invented'), root)
        value = self.draft(['target_outcome', 'target_population'])
        value['confirmation'] = 'two\nlines'
        Path(self.job.output_path).write_text(json.dumps(value))
        result = q.validate_submission(Path(receipt['packet']))
        self.assertNotIn('supports items', result['error'])


# --- F5: a worker that never heartbeated is dead, not slow ---------------------------------------------------------

class F5DeadWorkerTests(unittest.TestCase):
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown
    emit = fixtures.AgentTests.emit

    def packet(self):
        job = self.emit('S5', 'Card')
        root = self.root / 'queue'
        return job, root, q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='invented'), root)

    def write_output(self, job):
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text('{}')

    def test_a_lease_that_was_never_renewed_expires_after_15_minutes_not_90(self):
        job, root, r = self.packet()
        q.claim(root, 'worker', now=1000)
        self.assertEqual(q.SILENT_SECONDS, 15 * 60)
        self.assertEqual(q.reap(root, now=1000 + q.SILENT_SECONDS - 1), [])
        events = q.reap(root, now=1000 + q.SILENT_SECONDS)
        self.assertEqual([e['status'] for e in events], ['pending'])
        self.assertEqual(q.read(root / 'pending' / r['job_id'] / 'STATE.json')['expiries'], 1)

    def test_a_worker_that_renewed_its_lease_is_reaped_after_20_minutes_of_silence(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        q.heartbeat(root, r['job_id'], lease['token'], now=1000 + 120)
        self.assertEqual(q.reap(root, now=1000 + 120 + q.SILENT_SECONDS + 240), [])   # past the 15-minute silent rule, short of the 20-minute dead rule
        self.assertEqual(q.DEAD_SECONDS, 20 * 60)
        self.assertEqual(q.reap(root, now=1000 + 120 + q.DEAD_SECONDS - 1), [])
        self.assertEqual([e['status'] for e in q.reap(root, now=1000 + 120 + q.DEAD_SECONDS)], ['pending'])

    def test_a_renewal_inside_the_first_minute_does_not_count_as_a_sign_of_life(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        q.heartbeat(root, r['job_id'], lease['token'], now=1030)
        self.assertEqual([e['status'] for e in q.reap(root, now=1030 + q.SILENT_SECONDS)], ['pending'])

    def test_a_packet_with_no_submission_and_no_release_gets_six_leases_before_the_hub(self):
        job, root, r = self.packet()
        statuses = []
        for round_ in range(6):
            q.claim(root, 'worker', now=1000 + round_ * 10000)
            statuses += [e['status'] for e in q.reap(root, now=1000 + round_ * 10000 + q.SILENT_SECONDS)]
        self.assertEqual(statuses, ['pending'] * 5 + ['hub'])
        packet = root / 'hub' / r['job_id']
        self.assertTrue((packet / 'EXPIRED_6.json').is_file())
        self.assertEqual(q.hub_reason(packet), 'Sonnet lease expired 6 times')
        with patch.object(q, 'root_for', return_value=root), self.assertRaisesRegex(ValueError, 'expired 6 times; HUB_QUEUE required'):
            q.state(job.job_path, self.ctx.repo)

    def test_a_packet_that_has_a_submission_goes_to_the_hub_at_the_second_expiry(self):
        job, root, r = self.packet()
        q.claim(root, 'worker', now=1000)
        self.assertEqual([e['status'] for e in q.reap(root, now=1000 + q.SILENT_SECONDS)], ['pending'])
        q.claim(root, 'worker', now=20000)
        self.write_output(job)
        self.assertEqual([e['status'] for e in q.reap(root, now=20000 + q.SILENT_SECONDS)], ['hub'])
        self.assertEqual(q.hub_reason(root / 'hub' / r['job_id']), 'Sonnet lease expired twice')

    def test_a_released_packet_is_not_dead_and_keeps_the_two_lease_rule(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        q.release(root, r['job_id'], lease['token'], 'cannot finish', now=1100)
        q.claim(root, 'worker', now=2000)
        self.assertEqual([e['status'] for e in q.reap(root, now=2000 + q.SILENT_SECONDS)], ['pending'])
        q.claim(root, 'worker', now=9000)
        self.assertEqual([e['status'] for e in q.reap(root, now=9000 + q.SILENT_SECONDS)], ['hub'])

    def test_the_park_reason_names_the_real_cause(self):
        # F10 (2026-10-18): the hub queue PARKs the design (reopenable) instead of STOPping it; the reason still names the cause.
        job, root, r = self.packet()
        for round_ in range(6):
            q.claim(root, 'worker', now=1000 + round_ * 10000)
            q.reap(root, now=1000 + round_ * 10000 + q.SILENT_SECONDS)
        row = dict(design_id=job.design_id, attempt_id='invented')
        pilot = Autopilot(self.root.resolve() / 'data/l2_pipeline/registry', backend=Backend(self.root.resolve()))
        atomic(pilot.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        with patch.object(q, 'root_for', return_value=root), patch.object(pilot, 'stop') as stop, patch.object(pilot, 'queue_reconciliations'), \
                patch.object(pilot.backend, 'global_running', return_value=0), patch.object(pilot, 'executor', return_value='codex'):
            pilot.schedule()
        stop.assert_not_called()
        parked = read(pilot.case_dir(row) / 'PARKED.json')
        self.assertIn('expired 6 times', parked['reason'])
        self.assertEqual((parked['status'], parked['metrics']['kind'], parked['metrics']['cause']), ('PARKED', 'QUEUE_PARK', 'lease_expired'))


# --- F6: intake S3 replay on the certified roster ------------------------------------------------------------------

class F6IntakeReplayTests(unittest.TestCase):
    """A card that reuses a certified history is screened on that history's roster (no year tolerance); a design whose
    support failure the Q1 structural list cannot cure is parked at intake as S3_SIBLING. Synthetic metadata only."""

    UNITS = ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN',
             'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT']

    def setUp(self):
        from scripts.l2_pipeline import prescreen
        self.prescreen = prescreen
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for target, value in (('scripts.l2_pipeline.population_resolution.choose', dict(side_effect=ValueError('new construct'))),
                              ('scripts.l2_pipeline.prescreen.screen_ri', dict(return_value=dict(passed=True, checks={})))):
            patcher = patch(target, **value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def card(self, absent=()):
        from scripts.l2_pipeline.tests.test_gate0 import fixture
        c = fixture(self.root)
        units = self.UNITS
        c['outcome'].update(series_id='invented', units=units)
        side = ensure_once(self.root / ('support_%d.json' % len(absent)), dict(series_id='invented', outcome_values_in_sidecar=False,
            metadata=[dict(unit=u, year=y) for u in units if u not in absent for y in range(2000, 2019)]))
        c['outcome'].update(support_path=str(side), support_sha256=sha(side))
        c['treatment'].update(universe=units)
        # The screening map is deliberately cheerful: it must not matter once the certified roster is used.
        c['treatment']['screening_adoption'] = dict(adopters={u: 2005 + 3 * (i // 6) for i, u in enumerate(units[:12])}, never_through_coverage_end=units[12:])
        c.setdefault('automation', {})['history_reuse'] = dict(source_design_id='source', rule='docs/plans/l2-pipeline/HUB_RULING_20261016_HISTORY_REUSE.md')
        return c

    def certified(self, early=0, extra_early_cohort=1998):
        """14 treated states in 3 cohorts plus `early` of them registered before the window; 12 never-treated controls."""
        starts = [2005] * 5 + [2008] * 5 + [2011] * 4
        for i in range(early):
            starts[13 - i] = extra_early_cohort
        records = [dict(unit=u, status='treated', start_period=s) for u, s in zip(self.UNITS, starts)]
        records += [dict(unit=u, status='never_treated', start_period=None) for u in self.UNITS[14:]]
        return dict(source_design_id='source', records=records, sample=self.UNITS[:10], excluded_units=[])

    def test_a_roster_that_passes_s3_proceeds_with_the_reused_basis_and_no_tolerance(self):
        result = self.prescreen.evaluate(self.card(), self.root, certified=self.certified())
        self.assertEqual(result['status'], 'PROCEED')
        self.assertEqual(result['adoption_basis'], 'certified_reused_history')
        self.assertEqual(result['year_tolerance'], [0])
        self.assertEqual({v['year_shift'] for v in result['variants']}, {0})
        self.assertNotIn('structural_rescue', result)
        self.assertEqual(result['outcome_values_read'], 0)

    def test_a_support_failure_the_structural_list_cures_proceeds_and_records_the_list(self):
        result = self.prescreen.evaluate(self.card(), self.root, certified=self.certified(early=1))
        self.assertEqual(result['status'], 'PROCEED')
        rescue = result['structural_rescue']
        self.assertTrue(rescue['rescuable'])
        self.assertEqual(rescue['excluded'], {'IN': dict(reason='adopted_before_window', status='treated', start_period=1998)})
        self.assertEqual(rescue['figures']['retained_treated'], 13)

    def test_a_treated_state_missing_from_the_outcome_series_is_cured_the_same_way(self):
        result = self.prescreen.evaluate(self.card(absent=('IL',)), self.root, certified=self.certified())
        self.assertEqual(result['status'], 'PROCEED')
        self.assertEqual(result['structural_rescue']['excluded']['IL']['reason'], 'absent_from_outcome_series')

    def test_a_failure_that_would_remove_more_than_a_third_is_parked_as_s3_sibling(self):
        result = self.prescreen.evaluate(self.card(), self.root, certified=self.certified(early=5))
        self.assertEqual(result['status'], 'PRESCREEN_PARK')
        self.assertTrue(result['reasons'][0].startswith('S3_SIBLING: '), result['reasons'])
        self.assertIn('more than 1/3', result['reasons'][0])
        self.assertFalse(result['structural_rescue']['rescuable'])
        self.assertEqual(result['outcome_values_read'], 0)

    def test_a_failure_with_no_structural_cause_is_parked(self):
        certified = self.certified()
        certified['records'] = [r for r in certified['records'] if r['status'] == 'treated'][:4] + [
            dict(unit=u, status='never_treated', start_period=None) for u in self.UNITS[14:]]  # 4 treated states: below the registered minimum of 6
        result = self.prescreen.evaluate(self.card(), self.root, certified=certified)
        self.assertEqual(result['status'], 'PRESCREEN_PARK')
        self.assertIn('no structural exclusion applies', result['reasons'][0])

    def test_without_a_certified_roster_the_screening_map_decides_as_before(self):
        card = self.card()
        plain = self.prescreen.evaluate(card, self.root)
        self.assertEqual(plain['status'], 'PROCEED')
        self.assertEqual(plain['adoption_basis'], 'screening_only')
        self.assertEqual(plain['year_tolerance'], [-1, 0, 1])
        self.assertEqual(self.prescreen.evaluate(card, self.root, certified=self.certified(early=5))['status'], 'PRESCREEN_PARK')
        self.assertNotIn('structural_rescue', plain)

    def test_the_full_year_adapter_ignores_the_roster(self):
        card = self.card()
        card['analysis'].update(support_adapter='annual_full_year_v1', full_year_variant='primary')
        card['treatment']['full_year_declaration'] = dict(key='invented', period_years=list(range(2000, 2019)), window=[2000, 2018],
            operative={u: 2005 + 3 * (i // 6) for i, u in enumerate(self.UNITS[:12])}, never_controls=self.UNITS[12:], source_ready=True)
        result = self.prescreen.evaluate(card, self.root, certified=self.certified(early=5))
        self.assertEqual(result['adoption_basis'], 'screening_only')

    def test_the_roster_comes_from_the_verified_source_with_its_exclusion_overlay(self):
        from scripts.l2_pipeline import history_reuse
        card = self.card()
        card['treatment']['excluded_units'] = ['MT']
        path = ensure_once(self.root / 'reuse_card.json', card)
        row = dict(design_id='new', attempt_id='a', card=str(path))
        source = dict(treatment=dict(records=self.certified()['records'], sample=self.UNITS[:10]),
                      overlay=dict(prepared_payload=dict(treatment=dict(excluded_conflict_states=['MO']))))
        with patch.object(history_reuse, 'verify_source', return_value=source) as verify:
            value = history_reuse.certified_roster(object(), row)
        self.assertEqual(verify.call_args.args[3], 'source')
        self.assertEqual(value['source_design_id'], 'source')
        self.assertEqual(value['records'], self.certified()['records'])
        self.assertEqual(value['sample'], self.UNITS[:10])
        self.assertEqual(value['excluded_units'], ['MO', 'MT'])

    def test_an_unusable_source_or_an_undeclared_card_gives_no_roster_and_never_raises(self):
        from scripts.l2_pipeline import history_reuse
        row = dict(design_id='new', attempt_id='a', card=str(ensure_once(self.root / 'plain_card.json', {'automation': {}})))
        self.assertIsNone(history_reuse.certified_roster(object(), row))
        row = dict(row, card=str(ensure_once(self.root / 'declared_card.json', self.card())))
        for error in (history_reuse.SourceNotReady('not ready'), ValueError('refused'), OSError('gone')):
            with patch.object(history_reuse, 'verify_source', side_effect=error):
                self.assertIsNone(history_reuse.certified_roster(object(), row))

    def test_gate0_and_the_prescreen_hand_the_roster_to_the_evaluation(self):
        from scripts.l2_pipeline import gate0, history_reuse
        roster = self.certified()
        for module in (gate0, self.prescreen):
            with self.subTest(module.__name__), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp).resolve()
                from scripts.l2_pipeline.tests.test_autopilot import MockAgents
                pilot = Autopilot(repo / 'data/l2_pipeline/registry', MockAgents(repo))
                path = ensure_once(repo / 'card.json', self.card())
                row = dict(design_id='new', attempt_id='a', route='V9', card=str(path), card_sha256=sha(path),
                           work_dir=str(repo / 'data/l2_pipeline/work'), prescreen_enabled=True)
                answer = dict(status='PROCEED', reasons=[], variants=[], card_sha256=sha(path))
                with patch.object(history_reuse, 'certified_roster', return_value=roster) as find, \
                        patch.object(self.prescreen, 'evaluate', return_value=dict(answer)) as evaluate:
                    module.persist(pilot, row)
                find.assert_called_once_with(pilot, row)
                self.assertIs(evaluate.call_args.kwargs['certified'], roster)
