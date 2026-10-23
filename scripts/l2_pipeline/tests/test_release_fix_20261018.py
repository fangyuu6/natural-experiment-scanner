"""Release-incident fixes F7-F11 (2026-10-18): history packets of at most 10 jurisdictions, PDF evidence text, bounded S6 review
pack, queue events PARK (never STOP), recovery requeue. Synthetic data only; no network, no outcome value."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import evidence_fetch as ef
from scripts.l2_pipeline.agents import sonnet_queue as q
try:
    from scripts.l2_pipeline.agents import history_chunks as hc
    from scripts.l2_pipeline.agents import review_pack
except ImportError:   # before F7/F9 these modules do not exist: the tests then fail one by one instead of the file failing as a whole
    hc = review_pack = None
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.autopilot import Autopilot, Backend, atomic, read
from scripts.l2_pipeline.c42 import Park
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests import test_autopilot
from scripts.l2_pipeline.tests.test_named_panel_20261016 import FakeWeb, page


# --- helpers ----------------------------------------------------------------------------------------------------------

def text_pdf(*pages):
    """A small valid PDF with one line of Helvetica text per page (a text layer)."""
    n = len(pages)
    font = 3 + 2 * n
    objs = {1: b'<< /Type /Catalog /Pages 2 0 R >>',
            2: ('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join('%d 0 R' % (3 + 2 * i) for i in range(n)), n)).encode()}
    for i, text in enumerate(pages):
        safe = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream = ('BT /F1 11 Tf 40 700 Td (%s) Tj ET' % safe).encode()
        objs[3 + 2 * i] = ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R /Resources << /Font << /F1 %d 0 R >> >> >>'
                           % (4 + 2 * i, font)).encode()
        objs[4 + 2 * i] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream'
    objs[font] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
    out, offsets = b'%PDF-1.4\n', {}
    for i in sorted(objs):
        offsets[i] = len(out)
        out += b'%d 0 obj\n' % i + objs[i] + b'\nendobj\n'
    start = len(out)
    out += b'xref\n0 %d\n' % (len(objs) + 1) + b'0000000000 65535 f \n' + b''.join(b'%010d 00000 n \n' % offsets[i] for i in sorted(objs))
    return out + b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, start)


def scanned_pdf():
    """A PDF that opens but has no text layer (what a scan looks like to a text reader)."""
    ef._site()
    from pypdf import PdfWriter
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(612, 792)
    writer.write(buffer)
    return buffer.getvalue()


class PdfWeb(FakeWeb):
    """FakeWeb that serves chosen URLs as PDF bytes."""

    def __init__(self, pdfs, **kw):
        super().__init__(**kw)
        self.pdfs = dict(pdfs)

    def __call__(self, url, timeout=30, cap=0):
        if url in self.pdfs:
            self.calls.append(url)
            return dict(status=200, final_url=url, content_type='application/pdf', charset='', data=self.pdfs[url])
        return super().__call__(url, timeout, cap)


class ReleaseCase(unittest.TestCase):
    """A 17-unit V9 history job pair (roles A and B) and a queue root; nothing is enqueued yet.

    The fixture is test_autopilot.OfficialBridgeTests.setUp called on this class (not inherited, not imported by name), so its own
    tests do not run again here."""

    def setUp(self):
        test_autopilot.OfficialBridgeTests.setUp(self)
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        self.queue = self.root / 'queue'
        self.fresh = emit_pair(self.card, self.card['treatment']['universe'], root=self.root / 'route_q', attempt_id='queue_test')
        self.job_a, self.job_b = self.fresh
        self.row = dict(design_id=self.job_a.design_id, attempt_id='t')
        patcher = patch.object(q, 'root_for', return_value=self.queue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def enqueue(self, job=None):
        job = job or self.job_a
        return q.enqueue(job.job_path, self.row, self.queue)

    def chunks(self, parent_id):
        return hc.group(self.queue / 'chunked' / parent_id)['chunks']

    def work_chunk(self, entry, worker, now, with_rule=False, mutate=None):
        """Claim one chunk packet, write the golden answer for its units and finish it. Returns finish()'s result."""
        job = load_job(Path(entry['job_path']))
        lease = q.claim(self.queue, worker, entry['job_id'], now=now)
        self.assertIsNotNone(lease, 'chunk packet not claimable')
        rule = Path(lease['packet']) / 'EVIDENCE_RULE.json'
        if not with_rule and rule.exists():
            rule.unlink()   # a packet without the fetch rule: these tests are about the transport, not the evidence rule
        value = golden_output(job)
        if mutate:
            mutate(job, value)
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text(json.dumps(value))
        return q.finish(self.queue, entry['job_id'], lease['token'], now=now + 1), lease, job


# --- F7 ---------------------------------------------------------------------------------------------------------------

class SplitTests(unittest.TestCase):
    def test_blocks_are_balanced_ordered_and_at_most_ten(self):
        universe = ['S%02d' % i for i in range(51)]
        blocks = hc.split(universe)
        self.assertEqual([len(b) for b in blocks], [9, 9, 9, 8, 8, 8])
        self.assertEqual([u for b in blocks for u in b], universe)
        self.assertEqual([len(b) for b in hc.split(universe[:10])], [10])
        self.assertEqual([len(b) for b in hc.split(universe[:11])], [6, 5])
        with self.assertRaises(ValueError):
            hc.split([])


class HistoryChunkTests(ReleaseCase):
    def test_a_17_unit_history_job_becomes_chunk_packets_and_an_inert_parent(self):
        result = self.enqueue()
        self.assertEqual(result['chunks'], 2)
        parent_id = result['job_id']
        self.assertTrue((self.queue / 'chunked' / parent_id).is_dir())
        self.assertEqual(len(list((self.queue / 'pending').iterdir())), 2)
        entries = self.chunks(parent_id)
        self.assertEqual([len(e['units']) for e in entries], [9, 8])
        self.assertEqual([u for e in entries for u in e['units']], self.card['treatment']['universe'])
        for entry in entries:
            binding = read(self.queue / 'pending' / entry['job_id'] / 'PACKET.json')
            self.assertEqual(binding['chunk']['parent_job_id'], parent_id)
            self.assertLessEqual(len(binding['chunk']['units']), hc.CHUNK_UNITS)
        # the parent is never claimable; only the two chunks are
        first, second = q.claim(self.queue, 'w1', now=100), q.claim(self.queue, 'w2', now=100)
        self.assertEqual({Path(first['packet']).name, Path(second['packet']).name}, {e['job_id'] for e in entries})
        self.assertIsNone(q.claim(self.queue, 'w3', now=100))
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'running')
        self.assertEqual(self.enqueue()['job_id'], parent_id)   # idempotent: nothing new
        self.assertEqual(len(list((self.queue / 'in_progress').iterdir())), 2)

    def test_the_scheduler_enqueues_a_big_history_job_as_chunks_and_does_not_enqueue_it_again(self):
        pilot = Autopilot(self.root / 'data/l2_pipeline/registry', Backend(self.root))
        pilot.pending = [(self.row, self.job_a.job_path)]
        with patch.dict(os.environ, {'L2_CLAUDE_ONLY': '1'}), patch.object(pilot, 'executor', return_value='sonnet_queue'), \
                patch.object(pilot, 'queue_reconciliations'), patch.object(pilot.backend, 'global_running', return_value=0):
            pilot.schedule()
            launched = [json.loads(line) for line in (pilot.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
            launched = [r for r in launched if r.get('decision') == 'LAUNCHED']
            self.assertEqual([r['chunks'] for r in launched], [2])
            parent_id = launched[0]['job_id']
            self.assertEqual(len(list((self.queue / 'pending').iterdir())), 2)
            self.assertTrue((self.queue / 'chunked' / parent_id).is_dir())
            # next tick: the autopilot asks for the state of the job (running), it is not queued again
            self.assertEqual(pilot.backend.job_state(self.job_a.job_path), 'running')
            pilot.pending = []
            pilot.schedule()
        self.assertEqual(len(list((self.queue / 'pending').iterdir())), 2)

    def test_chunk_packets_are_never_rerouted_to_codex_and_the_parent_stays_running(self):
        with patch.dict(os.environ):
            os.environ.pop('L2_CLAUDE_ONLY', None)          # mixed mode: A and B history packets would otherwise resume through Codex
            parent_id = self.enqueue()['job_id']
            self.assertEqual(q.reroute_pending(self.queue), [])
            self.assertEqual(q.reap(self.queue, now=time.time()), [])
        self.assertEqual(len(list((self.queue / 'pending').iterdir())), 2)
        self.assertFalse(list((self.queue / 'rerouted_codex').glob('*')) if (self.queue / 'rerouted_codex').exists() else False)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'running')
        self.assertTrue((self.queue / 'chunked' / parent_id).is_dir())

    def test_a_job_of_ten_or_fewer_units_is_not_split(self):
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        small = emit_pair(self.card, self.card['treatment']['universe'][:10], root=self.root / 'route_small', attempt_id='small')[0]
        result = q.enqueue(small.job_path, dict(self.row), self.queue)
        self.assertNotIn('chunks', result)
        self.assertEqual(len(list((self.queue / 'pending').iterdir())), 1)
        self.assertEqual(list((self.queue / 'chunked').iterdir()), [])

    def test_chunk_jobs_keep_the_blindness_contract_and_only_their_own_plan(self):
        parent_id = self.enqueue()['job_id']
        for entry in self.chunks(parent_id):
            job = load_job(Path(entry['job_path']))
            c = job.contract
            self.assertEqual(c['manual']['universe'], entry['units'])
            self.assertEqual(c['history_chunk']['units'], entry['units'])
            self.assertIs(c['peer_read_forbidden'], True)
            self.assertEqual(c['blindness_scope'], 'our_outcomes_and_estimates_v7')
            self.assertEqual(c['evidence_standard'], 'HUB_RULING_V9_HISTORY_EVIDENCE')
            self.assertEqual(c['previous_packet_hashes'][self.job_a.job_path], self.job_a.job_sha256)
            plan = json.loads(Path(next(i['path'] for i in job.inputs if Path(i['path']).name == 'COLLECTION_PLAN.json')).read_text())
            self.assertEqual(plan['universe'], entry['units'])
            self.assertIn('CHUNK', Path(job.job_path).read_text())
            self.assertNotIn(self.job_b.job_path, json.dumps(c))   # never the peer role's packet
            self.assertTrue(job.network_access)

    def test_history_is_complete_only_when_all_chunks_are_in_and_the_merge_passes_the_ordinary_collector(self):
        from scripts.l2_pipeline.agents import collect_job
        parent_id = self.enqueue()['job_id']
        entries = self.chunks(parent_id)
        done, _, _ = self.work_chunk(entries[0], 'w1', 100)
        self.assertTrue(str(done).endswith(entries[0]['job_id']))
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'running')   # one chunk of two: not complete
        self.assertFalse(Path(self.job_a.output_path).exists())
        self.assertTrue((self.queue / 'chunked' / parent_id).is_dir())
        self.work_chunk(entries[1], 'w2', 300)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'done')
        self.assertTrue((self.queue / 'done' / parent_id).is_dir())
        self.assertFalse((self.queue / 'chunked' / parent_id).exists())
        merged = json.loads(Path(self.job_a.output_path).read_text())
        universe = self.card['treatment']['universe']
        self.assertEqual([r['unit'] for r in merged['records']], universe)
        self.assertEqual(merged['source_pack_sha256'], self.job_a.contract['source_sha256'])
        self.assertEqual(len({s['source_id'] for s in merged['sources']}), len(merged['sources']))
        self.assertEqual([c['units'] for c in merged['chunk_merge']['chunks']], [e['units'] for e in entries])
        self.assertNotIn('collection_complete', merged)
        receipt = collect_job(self.job_a.job_path)          # the ordinary collector, unchanged
        self.assertIs(receipt['validated'], True)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'done')   # and again: stable
        before = Path(self.job_a.output_path).read_bytes()
        hc.merge(self.queue, self.queue / 'done' / parent_id)                       # idempotent
        self.assertEqual(Path(self.job_a.output_path).read_bytes(), before)

    def test_fetch_evidence_of_every_chunk_is_combined_under_the_parent_and_the_rule_still_verifies(self):
        parent_id = self.enqueue()['job_id']
        entries = self.chunks(parent_id)
        url = 'https://leg.example.gov/code/title-5'
        probe = golden_output(load_job(Path(entries[0]['job_path'])))
        quotes = sorted({r['quote'] for row in probe['records'] for r in row.get('evidence_refs', [])})
        web = FakeWeb(pages={url: page(*quotes)})
        fetched = {}

        def with_evidence(job, value):
            lease_packet = self.queue / 'in_progress' / q.identity(job.job_path)
            lease = read(lease_packet / 'LEASE.json')
            with patch.object(ef, 'http_get', web):
                entry = ef.fetch_evidence(self.queue, q.identity(job.job_path), lease['token'], url, now=time.time())
            fetched[job.job_path] = entry
            value['sources'] = [dict(source_id='fixture-official', url=url, source_type='official', locator='x', sha256=entry['sha256'],
                                     archive_path=entry['archive_path'], final_url=entry['final_url'])]
        for n, entry in enumerate(entries):
            result, _, _ = self.work_chunk(entry, 'w%d' % (n + 1), time.time() - 60, with_rule=True, mutate=with_evidence)
            self.assertTrue(str(result).endswith(entry['job_id']), result)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'done')
        done = self.queue / 'done' / parent_id
        log = read(done / 'EVIDENCE_LOG.json')
        self.assertEqual([e['chunk'] for e in log['entries']], [1, 2])
        saved = Path(self.job_a.output_dir) / log['entries'][0]['archive_path']
        self.assertTrue(saved.is_file())
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), log['entries'][0]['sha256'])
        self.assertEqual(read(done / 'EVIDENCE_RULE.json')['rule'], ef.RULE)
        merged = json.loads(Path(self.job_a.output_path).read_text())
        value, report = ef.apply_evidence_rule(self.job_a, merged)   # the parent is judged on the combined fetch log
        self.assertEqual(report['rows_downgraded'], 0)
        self.assertEqual(report['rows_checked'], 17)

    def test_source_ids_that_collide_between_chunks_are_namespaced_and_rows_follow(self):
        parent = self.job_a
        jobs = hc.chunk_jobs(parent)
        values = []
        for index, job in enumerate(jobs, 1):
            value = golden_output(job)
            value['sources'][0]['url'] = 'https://leg.example.gov/chunk' + str(index)   # same id, different content
            values.append(dict(index=index, job=job, value=value, output_sha256='%064d' % index))
        merged = hc.merge_values(parent, values)
        ids = [s['source_id'] for s in merged['sources']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('fixture-official', ids)
        self.assertIn('k02_fixture-official', ids)
        second_units = set(jobs[1].contract['manual']['universe'])
        for row in merged['records']:
            expected = 'k02_fixture-official' if row['unit'] in second_units else 'fixture-official'
            self.assertEqual(row['source_id'], expected)
            self.assertTrue(all(ref['source_id'] == expected for ref in row['evidence_refs']))
        self.assertEqual(merged['chunk_merge']['renamed_source_ids'], {'2': ['fixture-official']})

    def test_a_chunk_that_leaves_a_unit_out_makes_the_merged_history_incomplete_not_invalid(self):
        from scripts.l2_pipeline.c59_queue import require_complete_history
        jobs = hc.chunk_jobs(self.job_a)
        values = []
        for index, job in enumerate(jobs, 1):
            value = golden_output(job)
            if index == 2:
                value['records'] = value['records'][:-1]
                value['collection_complete'] = False
            values.append(dict(index=index, job=job, value=value, output_sha256='%064d' % index))
        merged = hc.merge_values(self.job_a, values)
        self.assertIs(merged['collection_complete'], False)
        self.assertEqual(len(merged['records']), 16)
        with self.assertRaisesRegex(Park, 'INSUFFICIENT_WORK'):
            require_complete_history(self.job_a, merged)

    def test_the_merge_never_hides_a_defect_the_ordinary_collector_must_see(self):
        from scripts.l2_pipeline.agents.collect import validate_blindness
        jobs = hc.chunk_jobs(self.job_a)
        good = [dict(index=i, job=j, value=golden_output(j), output_sha256='%064d' % i) for i, j in enumerate(jobs, 1)]
        clean = hc.merge_values(self.job_a, deepcopy(good))
        self.assertEqual(clean['chunk_merge']['problems'], [])
        validate_blindness(self.job_a, clean)                                    # the clean merge passes the V7 attestation check
        dirty = deepcopy(good)
        dirty[1]['value']['blindness_attestation']['our_outcome_data_read'] = True
        merged = hc.merge_values(self.job_a, dirty)
        self.assertIs(merged['blindness_attestation']['our_outcome_data_read'], True)   # carried through, not cleaned
        with self.assertRaisesRegex(ValueError, '[Cc]ontaminated'):
            validate_blindness(self.job_a, merged)
        foreign = deepcopy(good)
        foreign[0]['value']['records'][0]['unit'] = foreign[1]['value']['records'][0]['unit']
        merged = hc.merge_values(self.job_a, foreign)
        self.assertEqual(len(merged['records']), 17)                                    # nothing dropped: the repeat stays visible
        self.assertEqual(len({r['unit'] for r in merged['records']}), 16)
        self.assertEqual({p['problem'] for p in merged['chunk_merge']['problems']}, {'record outside the chunk units', 'unit repeated'})
        self.assertIs(merged['collection_complete'], False)
        wrong = deepcopy(good)
        wrong[0]['value']['design_id'] = 'some-other-design'
        self.assertEqual(hc.merge_values(self.job_a, wrong)['design_id'], 'some-other-design')   # the parent then fails its identity check
        wrong = deepcopy(good)
        wrong[1]['value']['outcome_associations_computed'] = True
        self.assertIs(hc.merge_values(self.job_a, wrong)['outcome_associations_computed'], True)
        broken = deepcopy(good)
        broken[0]['value'] = None
        tombstone = hc.merge_values(self.job_a, broken)
        self.assertNotIn('design_id', tombstone)
        self.assertEqual(tombstone['chunk_merge']['problems'][0]['problem'], 'submission is not a JSON object')

    def test_merge_survives_a_crash_after_the_output_was_written(self):
        parent_id = self.enqueue()['job_id']
        entries = self.chunks(parent_id)
        for n, entry in enumerate(entries):
            self.work_chunk(entry, 'w%d' % (n + 1), 100 + 300 * n)
        real_rename = os.rename

        def crash_on_the_parent_move(source, target):
            if Path(source).name == parent_id:
                raise OSError('invented crash')
            return real_rename(source, target)
        with patch('scripts.l2_pipeline.agents.history_chunks.os.rename', side_effect=crash_on_the_parent_move):
            with self.assertRaises(OSError):
                q.state(self.job_a.job_path, self.job_a.repo)
        self.assertTrue(Path(self.job_a.output_path).is_file())               # the merged submission is whole on disk ...
        self.assertTrue((self.queue / 'chunked' / parent_id).is_dir())        # ... and the parent has not moved
        before = Path(self.job_a.output_path).read_bytes()
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'done')   # the next tick completes it, same bytes
        self.assertEqual(Path(self.job_a.output_path).read_bytes(), before)
        self.assertTrue((self.queue / 'done' / parent_id / 'COMPLETION.json').is_file())

    def test_a_chunk_packet_that_left_the_queue_is_made_again_from_its_canonical_job(self):
        parent_id = self.enqueue()['job_id']
        entries = self.chunks(parent_id)
        lost = self.queue / 'pending' / entries[1]['job_id']
        (self.root / 'retired').mkdir()
        os.rename(lost, self.root / 'retired' / lost.name)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'pending')     # the autopilot then calls enqueue
        self.assertEqual(self.enqueue()['job_id'], parent_id)
        self.assertTrue(lost.is_dir())
        self.assertEqual(read(lost / 'PACKET.json')['chunk']['index'], 2)
        self.assertEqual(q.state(self.job_a.job_path, self.job_a.repo), 'running')

    def test_one_worker_identity_never_codes_both_roles_of_a_design(self):
        a_id, b_id = self.enqueue(self.job_a)['job_id'], self.enqueue(self.job_b)['job_id']
        a_chunks, b_chunks = self.chunks(a_id), self.chunks(b_id)
        self.assertIsNotNone(q.claim(self.queue, 'w1', a_chunks[0]['job_id'], now=100))
        self.assertIsNone(q.claim(self.queue, 'w1', b_chunks[0]['job_id'], now=101))          # role B: refused for w1
        self.assertIsNotNone(q.claim(self.queue, 'w2', b_chunks[0]['job_id'], now=102))
        self.assertIsNone(q.claim(self.queue, 'w2', a_chunks[1]['job_id'], now=103))          # role A: refused for w2
        self.assertIsNotNone(q.claim(self.queue, 'w1', a_chunks[1]['job_id'], now=104))       # the same role again is fine
        self.assertIsNone(q.claim(self.queue, 'w1', b_chunks[1]['job_id'], now=105))
        self.assertIsNone(q.claim(self.queue, 'w1'))                                          # nothing else waits for w1: B-1 only
        self.assertIsNotNone(q.claim(self.queue, 'w3', b_chunks[1]['job_id'], now=106))

    def test_a_structural_release_of_a_chunk_parks_the_history_via_the_parent_state(self):
        parent_id = self.enqueue()['job_id']
        entry = self.chunks(parent_id)[0]
        lease = q.claim(self.queue, 'w1', entry['job_id'], now=100)
        done = q.release(self.queue, entry['job_id'], lease['token'], 'needs 100 official pages', now=150, kind='too_large')
        self.assertEqual(done['status'], 'hub')
        with self.assertRaises(Park) as caught:
            q.state(self.job_a.job_path, self.job_a.repo)
        self.assertIn('QUEUE_PARK', str(caught.exception))
        self.assertEqual(caught.exception.metrics['kind'], 'QUEUE_PARK')
        self.assertEqual(caught.exception.metrics['release_kind'], 'too_large')


# --- F8 ---------------------------------------------------------------------------------------------------------------

class EvidenceTextTests(ReleaseCase):
    URL = 'https://leg.example.gov/code/title-5.pdf'
    SENTENCE = 'The program takes effect on January 1, 2001 for every resident of the State'

    def setUp(self):
        super().setUp()
        parent_id = self.enqueue()['job_id']
        self.entry = self.chunks(parent_id)[0]
        self.lease = q.claim(self.queue, 'w1', self.entry['job_id'], now=time.time() - 50)
        self.id, self.token = self.entry['job_id'], self.lease['token']

    def fetch(self, web, url=None):
        with patch.object(ef, 'http_get', web):
            return ef.fetch_evidence(self.queue, self.id, self.token, url or self.URL, now=time.time())

    def test_pdf_text_layer_is_returned_page_by_page_and_the_same_text_verifies(self):
        web = PdfWeb({self.URL: text_pdf('Cover page of the compilation', self.SENTENCE, 'Third page, history note 1999')})
        entry = self.fetch(web)
        self.assertEqual((entry['text_status'], entry['pages_total']), ('ok', 3))
        got = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], page=2, now=time.time())
        self.assertEqual((got['text_status'], got['page'], got['pages_total']), ('ok', 2, 3))
        self.assertIn(self.SENTENCE, got['text'])
        self.assertLessEqual(abs(got['page_chars'][0] - len('Cover page of the compilation')), 2)
        self.assertEqual(ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], now=time.time())['page'], 1)   # default page 1
        verdict = ef.verify_quote(self.queue, self.id, self.token, entry['sha256'], self.SENTENCE, now=time.time())
        self.assertEqual(verdict['quote_check'], 'verified')

    def test_a_scan_has_no_text_layer_and_says_not_to_release(self):
        entry = self.fetch(PdfWeb({self.URL: scanned_pdf()}))
        self.assertEqual(entry['text_status'], 'no_text_layer')
        self.assertFalse(entry['quote_check_available'])
        got = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], now=time.time())
        self.assertEqual((got['text_status'], got['text'], got['page']), ('no_text_layer', '', None))
        self.assertIn('Do not release', got['note'])
        verdict = ef.verify_quote(self.queue, self.id, self.token, entry['sha256'], self.SENTENCE, now=time.time())
        self.assertEqual(verdict['quote_check'], 'no_text_layer')
        self.assertIn('Do not release', verdict['note'])

    def test_bytes_no_reader_opens_are_unavailable_not_verified(self):
        entry = self.fetch(PdfWeb({self.URL: b'%PDF-1.4 this is not really a pdf file'}))
        got = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], now=time.time())
        self.assertEqual(got['text_status'], 'unavailable')
        self.assertEqual(ef.quote_check(b'%PDF-1.4 not really a pdf', self.SENTENCE, 'application/pdf'), 'unavailable')

    def test_long_html_is_cut_into_pages_and_a_long_page_continues_by_offset(self):
        words = ' '.join('word%05d' % i for i in range(9000))                      # about 90,000 characters
        html = page(words)
        entry = self.fetch(FakeWeb(pages={self.URL: html}))
        first = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], now=time.time())
        self.assertGreater(first['pages_total'], 3)
        self.assertLessEqual(len(first['text']), ef.MAX_TEXT_CHARS)
        last = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], page=first['pages_total'], now=time.time())
        self.assertIn('word08999', last['text'])
        with patch.object(ef, 'MAX_TEXT_CHARS', 100):
            part = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], page=1, now=time.time())
            self.assertTrue(part['truncated'])
            rest = ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], page=1, offset=part['next_offset'], now=time.time())
            self.assertTrue(rest['text'].startswith(first['text'][100:110]))
        with self.assertRaisesRegex(ValueError, 'page must be'):
            ef.evidence_text(self.queue, self.id, self.token, entry['sha256'], page=99, now=time.time())
        with self.assertRaisesRegex(ValueError, 'No fetched file'):
            ef.evidence_text(self.queue, self.id, self.token, '0' * 64, now=time.time())
        with self.assertRaises(ValueError):
            ef.evidence_text(self.queue, self.id, 'bad-token', entry['sha256'], now=time.time())

    def test_the_command_line_has_evidence_text(self):
        entry = self.fetch(PdfWeb({self.URL: text_pdf(self.SENTENCE)}))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            q.main(['evidence-text', '--root', str(self.queue), '--job-id', self.id, '--token', self.token, '--sha256', entry['sha256'], '--page', '1'])
        out = json.loads(buffer.getvalue())
        self.assertEqual((out['text_status'], out['page']), ('ok', 1))
        self.assertIn(self.SENTENCE, out['text'])
        self.assertIn('evidence-text', q.WORKER_INSTRUCTIONS)
        self.assertIn('no_text_layer', q.WORKER_INSTRUCTIONS)


# --- F9 ---------------------------------------------------------------------------------------------------------------

class ReviewPackTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.cards import load_card
        from scripts.l2_pipeline.contracts import StageContext
        from scripts.l2_pipeline.storage import write_once
        from scripts.l2_pipeline.tests.test_pipeline import card, REPO
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.queue = self.root / 'queue'
        self.ctx = StageContext(load_card(write_once(self.root / 'card.json', card())), self.root / 'work', REPO)
        d = self.ctx.design_dir
        write_once(d / 'SPEC.json', {'registered_estimand': 'Invented hours ATT', 'rows': [{'n': i, 'label': 'row ' + str(i)} for i in range(30000)]})
        write_once(d / 'FREEZE_FINAL.json', {'invented_sealed_candidate': True})
        write_once(d / 'RECONCILIATION.json', {'registered_legal_evidence': 'Invented evidence; ' * 120_000})
        write_once(d / 'SUPPORT.json', {'support_pass': True, 'variants': [{'variant': 'primary', 'support_pass': True, 'cells': list(range(5000))}]})
        write_once(d / 'DRY_LOAD.json', {'passed': True, 'schema': 'l2_dry_load_v1', 'loaded': [{'variant': 'primary', 'rows': 12}]})
        write_once(d / 'TEST_EVIDENCE.json', {'schema': 'l2_pipeline_test_evidence_v1', 'passed': True, 'no_archival_fits': True,
                                              'code_inventory': {'/x/a.py': 'a' * 64},
                                              'runs': [{'command': ['/v/bin/python', '-B', '-m', 'regression'], 'exit_code': 0,
                                                        'transcript': 'noise\n' * 200_000 + 'Ran 1509 tests in 700.1s\n\nOK\n'}]})
        panels = d / 'panels' / 'primary'
        panels.mkdir(parents=True)
        (panels / 'panel.csv').write_text('unit,year,outcome\nSENTINEL_OUTCOME_VALUE_8675309,2001,1\n')
        with patch('scripts.l2_pipeline.agents.jobs.REVIEW_PROMPT_CHAR_LIMIT', 10 ** 9):    # inline mode, the case of a small design
            self.job = self.emit()

    def emit(self, attempt='new'):
        from scripts.l2_pipeline.agents.jobs import emit_job
        return emit_job(self.ctx, 'S6', 'reviewer', jobs_root=self.root / 'jobs', attempt_id=attempt)

    def test_the_pack_is_bounded_deterministic_and_keeps_every_pipeline_verdict(self):
        pack = review_pack.build(self.job)
        text = json.dumps(pack, indent=1, sort_keys=True)
        self.assertLessEqual(len(text), review_pack.TOKEN_BUDGET * review_pack.CHARS_PER_TOKEN)
        self.assertEqual(json.dumps(review_pack.build(self.job), indent=1, sort_keys=True), text)
        self.assertEqual({'identity', 'card', 'artifacts', 'code', 'pipeline_checks', 'limits', 'schema'}, set(pack) - {'card_sources'})
        names = {a['name']: a for a in pack['artifacts']}
        for name in ('SPEC.json', 'SUPPORT.json', 'DRY_LOAD.json', 'TEST_EVIDENCE.json', 'RECONCILIATION.json'):
            self.assertIn(name, names)
            self.assertEqual(names[name]['sha256'], sha(self.ctx.design_dir / name))
        self.assertIs(names['SUPPORT.json']['pass_flags']['support_pass'], True)
        self.assertIs(names['DRY_LOAD.json']['pass_flags']['passed'], True)
        regression = pack['pipeline_checks']['regression']
        self.assertEqual((regression['passed'], regression['runs'][0]['exit_code'], regression['runs'][0]['ran']), (True, 0, 'Ran 1509 tests in 700.1s'))
        self.assertEqual(regression['runs'][0]['verdict'], 'OK')
        self.assertIn('_list_len', json.dumps(names['SPEC.json']['content']))              # the 30,000-row spec was cut, and says so
        self.assertIn('chars; sha256', json.dumps(names['RECONCILIATION.json']['content']))
        self.assertEqual(pack['identity']['design_id'], self.job.design_id)
        self.assertEqual(pack['identity']['candidate_sha256'], self.job.contract['candidate_sha256'])
        self.assertIn('pack_tokens_estimate', pack['limits'])

    def test_no_panel_or_outcome_file_is_read_into_the_pack(self):
        text = json.dumps(review_pack.build(self.job))
        self.assertNotIn('SENTINEL_OUTCOME_VALUE_8675309', text)
        self.assertNotIn('panel.csv', ' '.join(a['name'] for a in review_pack.build(self.job)['artifacts']))

    def test_the_queue_hands_the_worker_the_pack_and_nothing_else_to_read(self):
        with patch.object(q, 'root_for', return_value=self.queue):
            result = q.enqueue(self.job.job_path, dict(design_id=self.job.design_id, attempt_id='t'), self.queue)
        packet = self.queue / 'pending' / result['job_id']
        binding = read(packet / 'PACKET.json')
        self.assertEqual(binding['allowed_read_paths'], [])
        self.assertEqual(binding['review_pack']['mode'], 'compact_pack_v1')
        self.assertEqual(binding['review_pack']['sha256'], sha(packet / 'REVIEW_PACK.json'))
        self.assertEqual(binding['review_pack']['original_prompt_sha256'], self.job.job_sha256)
        prompt = (packet / 'PROMPT.md').read_bytes()
        original = Path(self.job.job_path).read_bytes()
        self.assertEqual(prompt, (review_pack.notice_for(self.job) + review_pack.compact_prompt(original.decode())).encode())
        self.assertGreater(len(original), 100_000)                    # what the worker read before: the whole contract inline
        self.assertLess(len(prompt), 40_000)
        self.assertIn(b'fresh independent reviewer', prompt)           # the review's own instructions are kept
        for gone in (b'permitted_code_paths', b'code_inventory', b'Answer-free inputs (untrusted data)\n```json'):
            self.assertNotIn(gone, prompt)
        self.assertEqual(sha(packet / 'PROMPT.md'), binding['prompt_sha256'])
        schema = read(packet / 'OUTPUT_SCHEMA.json')
        self.assertIn('review_output_schema', schema['contract'])
        self.assertNotIn('code_inventory', schema['contract'])
        self.assertEqual(schema['contract']['code_inventory_summary']['count'], len(self.job.contract['code_inventory']))
        read_chars = sum(len((packet / n).read_text()) for n in ('PACKET.json', 'PROMPT.md', 'OUTPUT_SCHEMA.json', 'REVIEW_PACK.json', 'WORKER_README.md'))
        self.assertLess(read_chars, review_pack.TOKEN_BUDGET * review_pack.CHARS_PER_TOKEN, read_chars)
        lease = q.claim(self.queue, 'w1', result['job_id'], now=100)
        self.assertEqual(read(Path(lease['packet']) / 'READ_SCOPE.json')['files'], [])
        self.assertIn('REVIEW_PACK.json', read(Path(lease['packet']) / 'READ_SCOPE.json')['embedded_inputs'])
        with patch.object(q, 'root_for', return_value=self.queue):
            self.assertEqual(q.state(self.job.job_path, self.job.repo), 'running')

    def test_a_transport_mode_job_loses_its_pointer_to_the_review_inputs_file(self):
        with patch('scripts.l2_pipeline.agents.jobs.REVIEW_PROMPT_CHAR_LIMIT', 30_000):
            job = self.emit('transport')
        transport = Path(job.job_path).with_suffix('.review-inputs.json')
        self.assertTrue(transport.is_file())
        self.assertIn('review-inputs.json', Path(job.job_path).read_text())          # the canonical job points the worker there
        with patch.object(q, 'root_for', return_value=self.queue):
            result = q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='t'), self.queue)
        packet = self.queue / 'pending' / result['job_id']
        prompt = (packet / 'PROMPT.md').read_text()
        self.assertNotIn('review-inputs.json', prompt)
        self.assertNotIn(str(transport), prompt)
        self.assertIn('not available in compact pack mode', prompt)
        self.assertEqual(read(packet / 'PACKET.json')['allowed_read_paths'], [])
        self.assertEqual(sha(transport), read(packet / 'PACKET.json')['review_pack'].get('transport_sha256', sha(transport)))

    def test_code_section_counts_identical_lists_changed_with_a_diff_and_parses_the_snapshot(self):
        tree, live = self.root / 'tree', self.root / 'live'
        for base in (tree, live):
            (base / 'pkg').mkdir(parents=True)
        files = {'pkg/same.py': 'x = 1\n', 'pkg/edited.py': 'def f():\n    return 1\n', 'pkg/gone.py': 'y = 2\n', 'pkg/broken.py': 'def (:\n'}
        for rel, body in files.items():
            (tree / rel).write_text(body)
        (live / 'pkg/same.py').write_text(files['pkg/same.py'])
        (live / 'pkg/edited.py').write_text('def f():\n    return 2\n')
        (live / 'pkg/broken.py').write_text(files['pkg/broken.py'])
        inventory = {str(live / rel): hashlib.sha256(body.encode()).hexdigest() for rel, body in files.items()}
        section = review_pack.code_section(tree, inventory, {rel: 'h' for rel in files})
        self.assertEqual((section['identical'], section['changed_count'], section['removed_count']), (2, 1, 1))
        self.assertEqual(section['changed'][0]['path'], str(live / 'pkg/edited.py'))
        diff = '\n'.join(section['diffs'][0]['diff'])
        self.assertIn('-    return 1', diff)
        self.assertIn('+    return 2', diff)
        self.assertEqual([e['path'] for e in section['python_parse_errors']], ['pkg/broken.py'])
        self.assertEqual(section['python_files_parsed'], 3)

    def test_shrink_marks_every_cut_and_pass_flags_survive_it(self):
        value = {'ok_pass': True, 'nested': {'deep': {'deeper': {'support_pass': False}}}, 'rows': list(range(1000)), 'text': 'z' * 5000}
        small = review_pack.shrink(value, 2, 3, 50, 5)
        self.assertEqual(small['rows']['_list_len'], 1000)
        self.assertIn('[+4950 chars', small['text'])
        flags = review_pack.pass_flags(value)
        self.assertEqual(flags, {'ok_pass': True, 'nested.deep.deeper.support_pass': False})
        fitted, tier = review_pack.fit(value, 300)
        self.assertLessEqual(len(json.dumps(fitted, indent=1, sort_keys=True)), 300)
        self.assertGreater(tier, 0)


# --- F10 --------------------------------------------------------------------------------------------------------------

INCIDENT_REASONS = {
    '51-jurisdiction V9 history needs ~100 fetched, hashed, quote-verified official pages; not completable in one worker session': 'too_large',
    'All-51-jurisdiction keg-registration history needs ~100+ fetched, hashed, quote-verified official sources; not completable inside one 90-minute lease': 'too_large',
    'fetched PDF compilation is unreadable here (no PDF text tool), so rows could not be completed': 'tool_missing',
    'Cannot finish 51-jurisdiction evidence packet: PDFs unreadable (no pdftoppm), WebFetch 403': 'tool_missing',
    'S6 sealed-candidate reviewer needs synthetic numeric tests with .venv_did python, which this worker\'s allowed shell commands exclude': 'tool_missing',
    'Sealed S6 review needs a complete sweep over a 330k-token review-inputs file; cannot be done honestly with Read-only tools in one worker session': 'tool_missing',
    'ERS policy database is a binary download with no verifiable quote text': 'input_unreadable',
    'official site blocks every fetch': 'worker',
    'cannot finish': 'worker',
    'again': 'worker',
    'one': 'worker',
}


class ReleasePolicyTests(ReleaseCase):
    def test_the_incident_reasons_are_read_the_way_the_queue_will_act_on_them(self):
        from scripts.l2_pipeline import release_policy
        for reason, kind in INCIDENT_REASONS.items():
            with self.subTest(reason[:50]):
                self.assertEqual(release_policy.classify(reason), kind)
        self.assertEqual(release_policy.normalise_kind('structural_too_large'), 'too_large')
        self.assertEqual(release_policy.normalise_kind('worker', 'not completable in one worker session'), 'worker')   # the worker's word wins
        with self.assertRaisesRegex(ValueError, 'Unknown release kind'):
            release_policy.normalise_kind('whatever')

    def single(self):
        job = self.job_a
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        small = emit_pair(self.card, self.card['treatment']['universe'][:10], root=self.root / 'route_one', attempt_id='one')[0]
        self.small = small
        return q.enqueue(small.job_path, dict(self.row), self.queue)

    def test_a_structural_release_goes_to_the_hub_at_once_with_its_reason(self):
        packet = self.single()
        lease = q.claim(self.queue, 'w1', packet['job_id'], now=100)
        done = q.release(self.queue, packet['job_id'], lease['token'], 'needs about 100 fetched official pages', now=150, kind='too_large')
        self.assertEqual((done['status'], done['kind'], done['releases']), ('hub', 'too_large', 1))
        hub = self.queue / 'hub' / packet['job_id']
        marker = read(hub / 'RELEASED_1.json')
        self.assertEqual((marker['kind'], marker['structural'], marker['reason']), ('too_large', True, 'needs about 100 fetched official pages'))
        self.assertEqual(read(hub / 'STATE.json')['structural'], 'too_large')
        self.assertIn('structurally unfinishable (too_large): needs about 100', q.hub_reason(hub))
        with self.assertRaises(ValueError) as caught:
            q.state(self.small.job_path, self.small.repo)
        self.assertIsInstance(caught.exception, Park)
        self.assertRegex(str(caught.exception), r'^QUEUE_PARK: .*structurally unfinishable \(too_large\).*HUB_QUEUE required')

    def test_an_old_style_release_with_a_structural_reason_is_sorted_by_its_text(self):
        packet = self.single()
        lease = q.claim(self.queue, 'w1', packet['job_id'], now=100)
        self.assertEqual(q.release(self.queue, packet['job_id'], lease['token'], 'not completable in one worker session', now=150)['status'], 'hub')

    def test_a_worker_release_goes_back_to_pending_and_only_the_third_reaches_the_hub(self):
        packet = self.single()
        for n, status in ((1, 'pending'), (2, 'pending'), (3, 'hub')):
            lease = q.claim(self.queue, 'w%d' % n, packet['job_id'], now=100 * n)
            done = q.release(self.queue, packet['job_id'], lease['token'], 'context ran out', now=100 * n + 1, kind='worker')
            self.assertEqual(done['status'], status)
        with self.assertRaisesRegex(Park, 'released by workers 3 times'):
            q.state(self.small.job_path, self.small.repo)

    def test_the_command_line_takes_kind(self):
        packet = self.single()
        lease = q.claim(self.queue, 'w1', packet['job_id'], now=time.time())
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            q.main(['release', '--root', str(self.queue), '--job-id', packet['job_id'], '--token', lease['token'],
                    '--kind', 'tool_missing', '--reason', 'no pdf reader'])
        self.assertEqual(json.loads(buffer.getvalue())['status'], 'hub')
        self.assertIn('--kind', q.WORKER_INSTRUCTIONS)


class SchedulerParkTests(unittest.TestCase):
    """The scheduler turns a packet in hub/ into a PARK of the design's current attempt, never a STOP."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.pilot = Autopilot(self.registry, Backend(self.repo))
        atomic(self.repo / 'job.job.json', dict(role='A', contract={}, output_dir=str(self.repo / 'output')))
        atomic(self.repo / 'other-job.job.json', dict(design_id='D1', role='A', contract={}, output_dir=str(self.repo / 'output')))
        self.row = dict(design_id='D1', attempt_id='v1', card=str(self.repo / 'card.json'), card_sha256='0' * 64,
                        work_dir=str(self.repo / 'data/l2_pipeline/work'), route='V9')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row]))
        self.queue = q.root_for(self.repo)

    def hub_packet(self, name, row=None, kind='too_large', releases=1):
        packet = self.queue / 'hub' / name
        packet.mkdir(parents=True)
        atomic(packet / 'PACKET.json', dict(row=row or self.row, job_path=str(self.repo / 'job.md')))
        atomic(packet / 'STATE.json', dict(expiries=0, releases=releases, **({'structural': kind} if kind else {})))
        for n in range(1, releases + 1):
            atomic(packet / ('RELEASED_%d.json' % n), dict(lease=dict(worker='w'), reason='not completable in one lease', time=1.0, release=n,
                                                           kind=kind or 'worker', structural=bool(kind)))
        return packet

    def schedule(self):
        with patch.object(self.pilot, 'queue_reconciliations'), patch.object(self.pilot.backend, 'global_running', return_value=0):
            self.pilot.schedule()

    def ledger(self):
        path = self.registry / 'AUTOPILOT_LEDGER.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_a_structural_hub_packet_parks_the_current_attempt_and_never_stops_it(self):
        packet = self.hub_packet('p1')
        self.schedule()
        case = self.pilot.case_dir(self.row)
        parked = read(case / 'PARKED.json')
        self.assertFalse((case / 'STOP.json').exists())
        self.assertEqual((parked['status'], parked['metrics']['kind'], parked['metrics']['release_kind']), ('PARKED', 'QUEUE_PARK', 'too_large'))
        self.assertTrue(parked['reason'].startswith('QUEUE_PARK: INSUFFICIENT_WORK (queue event, reopenable): Sonnet packet released as structurally unfinishable (too_large)'))
        entries = read(self.registry / 'HUB_QUEUE.json')['entries']
        self.assertEqual([(e['status'], e['reopen']) for e in entries], [('PARKED_QUEUE', 'release_recovery')])
        self.assertEqual(read(packet / 'HUB_DISPOSITION.json')['disposition'], 'parked')
        self.assertEqual([r['decision'] for r in self.ledger() if r['decision'] in ('PARK', 'STOP')], ['PARK'])
        self.schedule()                                             # the next tick: nothing new
        self.assertEqual(len(read(self.registry / 'HUB_QUEUE.json')['entries']), 1)
        self.assertEqual([r['decision'] for r in self.ledger() if r['decision'] in ('PARK', 'STOP')], ['PARK'])

    def test_three_worker_releases_and_lease_expiry_also_park(self):
        self.hub_packet('p1', kind=None, releases=3)
        self.schedule()
        parked = read(self.pilot.case_dir(self.row) / 'PARKED.json')
        self.assertEqual(parked['metrics']['cause'], 'released_repeatedly')
        self.assertIn('released by workers 3 times', parked['reason'])
        self.assertFalse((self.pilot.case_dir(self.row) / 'STOP.json').exists())
        other = dict(self.row, design_id='D2')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row, other]))
        packet = self.hub_packet('p2', row=other, kind=None, releases=0)
        atomic(packet / 'STATE.json', dict(expiries=2))
        atomic(packet / 'EXPIRED_2.json', dict(lease=dict(worker='w', claimed_at=1.0, heartbeat_at=1.0), time=1.0))
        self.schedule()
        parked = read(self.pilot.case_dir(other) / 'PARKED.json')
        self.assertEqual(parked['metrics']['cause'], 'lease_expired')
        self.assertIn('lease expired twice', parked['reason'])

    def test_a_packet_of_an_old_attempt_or_of_a_design_that_already_ended_changes_nothing(self):
        newer = dict(self.row, attempt_id='v2')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[newer]))
        old = self.hub_packet('old')                                    # packet row is attempt v1, the design runs v2
        self.schedule()
        self.assertEqual(read(old / 'HUB_DISPOSITION.json')['disposition'], 'superseded')
        self.assertFalse((self.pilot.case_dir(newer) / 'PARKED.json').exists())
        self.assertFalse((self.registry / 'HUB_QUEUE.json').exists())
        stopped = dict(newer, design_id='D3')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[newer, stopped]))
        ensure_once(self.pilot.case_dir(stopped) / 'STOP.json', dict(design_id='D3', attempt_id='v2', action='draft', status='NEEDS_RULING',
                                                                      reason='science', time='2026-10-18T03:00:00+00:00', rule='x'))
        late = self.hub_packet('late', row=stopped)
        self.schedule()
        self.assertEqual(read(late / 'HUB_DISPOSITION.json')['disposition'], 'already_stopped')
        self.assertFalse((self.pilot.case_dir(stopped) / 'PARKED.json').exists())

    def test_l3_reads_a_queue_park_as_excluded_work_not_as_an_unclassified_design(self):
        import importlib.util
        import sys
        path = Path(__file__).resolve().parents[2] / 'l3_meta' / 'build_live_set.py'
        if not path.is_file():
            self.skipTest('L3 builder not present')
        sys.path.insert(0, str(path.parent))
        try:
            spec = importlib.util.spec_from_file_location('build_live_set_queue_park_test', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(path.parent))
        self.hub_packet('p1')
        self.schedule()
        reason = read(self.pilot.case_dir(self.row) / 'PARKED.json')['reason']
        self.assertEqual(module.classify_reason(reason), 'insufficient_work')

    def test_c81_requeue_current_leaves_queue_parks_to_release_recovery(self):
        from scripts.l2_pipeline import c83_history
        ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(
            design_id='D1', attempt_id='v1', action='dispatch', status='PARKED', metrics=dict(kind='QUEUE_PARK'),
            reason='QUEUE_PARK: INSUFFICIENT_WORK (queue event, reopenable): Sonnet lease expired twice; HUB_QUEUE required'))
        with patch('scripts.l2_pipeline.c70_l3.protected', return_value=False):
            self.assertEqual(c83_history.requeue_current(self.pilot), [])      # the C81 completion route is not for queue parks
        self.assertFalse((self.pilot.case_dir(self.row) / 'HISTORY_COMPLETION_REQUEUE.json').exists())
        other = dict(self.row, design_id='D4')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row, other]))
        ensure_once(self.pilot.case_dir(other) / 'PARKED.json', dict(
            design_id='D4', attempt_id='v1', action='history', status='PARKED', reason='INSUFFICIENT_WORK: official history is an incomplete research checkpoint'))
        with patch('scripts.l2_pipeline.c70_l3.protected', return_value=False), patch.object(self.pilot.backend, 'root', return_value=self.repo / 'x'):
            self.assertEqual([v['design_id'] for v in c83_history.requeue_current(self.pilot)], ['D4'])   # a real incomplete history still is

    def test_pending_packets_of_a_final_park_are_retired_like_those_of_a_stopped_design(self):
        ensure_once(self.pilot.case_dir(self.row) / 'PARKED.json', dict(
            design_id='D1', attempt_id='v1', action='S3', status='PARKED', reason='Registered support insufficient'))
        pending = self.queue / 'pending' / ('a' * 64)
        pending.mkdir(parents=True)
        atomic(pending / 'PACKET.json', dict(row=self.row, job_path=str(self.repo / 'other-job.md')))
        atomic(pending / 'STATE.json', dict(expiries=0, enqueued_at=1.0))
        with patch.dict(os.environ, {'L2_CLAUDE_ONLY': '1'}):
            self.schedule()
        self.assertFalse(pending.exists())
        self.assertTrue((self.queue / 'retired_stopped' / pending.name).is_dir())
        # a PARK that the C81 completion reopens keeps its packets
        other = dict(self.row, design_id='D5')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row, other]))
        ensure_once(self.pilot.case_dir(other) / 'PARKED.json', dict(
            design_id='D5', attempt_id='v1', action='history', status='PARKED', reason='INSUFFICIENT_WORK: incomplete'))
        ensure_once(self.pilot.case_dir(other) / 'HISTORY_COMPLETION_REQUEUE.json', dict(
            park=dict(path=str(self.pilot.case_dir(other) / 'PARKED.json'), sha256=sha(self.pilot.case_dir(other) / 'PARKED.json'))))
        kept = self.queue / 'pending' / ('b' * 64)
        kept.mkdir(parents=True)
        atomic(kept / 'PACKET.json', dict(row=other, job_path=str(self.repo / 'other-job.md')))
        atomic(kept / 'STATE.json', dict(expiries=0, enqueued_at=1.0))
        with patch.dict(os.environ, {'L2_CLAUDE_ONLY': '1'}):
            self.schedule()
        self.assertTrue(kept.exists())


# --- F11 --------------------------------------------------------------------------------------------------------------

class RecoveryTests(unittest.TestCase):
    CAUSE = 'Sonnet packet released by workers 3 times: /x/job.md'

    def setUp(self):
        from scripts.l2_pipeline.tests import test_c46
        from scripts.l2_pipeline import release_recovery as rr
        self.rr = rr
        test_c46.RequeueTests.setUp(self)      # one design stopped at 'purpose' with another reason (it must stay untouched)
        rows = read(self.registry / 'ACTIVE_DESIGNS.json')['designs']
        self.rows = {}
        for name, reason, when in (('hit', self.CAUSE, '2026-10-18T03:30:00+00:00'),
                                   ('expired', 'ValueError: Sonnet lease expired twice; HUB_QUEUE required', '2026-10-18T04:00:00+00:00'),
                                   ('early', self.CAUSE, '2026-10-18T01:00:00+00:00'),
                                   ('science', 'ValueError: NEEDS_RULING: NOVEL/science finding requires explicit validated hub closure', '2026-10-18T03:40:00+00:00'),
                                   ('sealed', self.CAUSE, '2026-10-18T03:50:00+00:00')):
            design = 'rr-' + name
            card = dict(self.card, design_id=design)
            path = self.repo / 'data/l2_pipeline/cards' / (design + '.json')
            atomic(path, card)
            row = dict(self.row, design_id=design, card=str(path), card_sha256=sha(path))
            ensure_once(self.pilot.case_dir(row) / 'STOP.json', dict(design_id=design, attempt_id='old', action='dispatch', status='NEEDS_RULING',
                                                                      reason=reason, time=when, rule='fixture'))
            self.rows[name] = row
            rows.append(row)
        queue = read(self.registry / 'HUB_QUEUE.json', dict(schema='l2_hub_queue_v1', entries=[]))
        for name, row in self.rows.items():      # what stop() would have recorded for each
            stop = read(self.pilot.case_dir(row) / 'STOP.json')
            queue['entries'].append(stop)
        atomic(self.registry / 'HUB_QUEUE.json', queue)
        ensure_once(self.pilot.backend.root(self.rows['sealed']) / 'FREEZE_FINAL.json', dict(fixture=True))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=rows))
        (self.repo / rr.RULING).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / rr.RULING).write_text('fixture hub order')
        (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task='WINDOW_REDESIGN_3', time='2026-10-18T07:00:00+00:00')))

    def test_plan_lists_only_queue_stops_since_the_incident_and_keeps_the_sealed_one_aside(self):
        candidates, kept = self.rr.plan(self.pilot)
        self.assertEqual(sorted(c['row']['design_id'] for c in candidates), ['rr-expired', 'rr-hit'])
        self.assertEqual([k['design_id'] for k in kept], ['rr-sealed'])
        self.assertIn('frozen or sealed', kept[0]['why'])

    def test_apply_requeues_each_once_with_the_new_status_and_touches_nothing_else(self):
        stop_before = {n: sha(self.pilot.case_dir(r) / 'STOP.json') for n, r in self.rows.items()}
        done = self.rr.apply(self.pilot)
        self.assertEqual(sorted(r['design_id'] for r in done['records']), ['rr-expired', 'rr-hit'])
        self.assertEqual({r['restart_action'] for r in done['records']}, {'history'})
        self.assertEqual([k['design_id'] for k in done['kept']], ['rr-sealed'])
        self.assertEqual(done['refused'], [])
        active = {r['design_id']: r for r in read(self.registry / 'ACTIVE_DESIGNS.json')['designs']}
        self.assertEqual(active['rr-hit']['attempt_id'], 'requeue_v2')
        self.assertEqual(active['rr-expired']['attempt_id'], 'requeue_v2')
        for stays in ('rr-early', 'rr-science', 'rr-sealed', self.card['design_id']):
            self.assertEqual(active[stays]['attempt_id'], 'old')
        statuses = {e['design_id']: e['status'] for e in read(self.registry / 'HUB_QUEUE.json')['entries']}
        self.assertEqual(statuses['rr-hit'], 'SUPERSEDED_RELEASE_RECOVERY')
        self.assertEqual(statuses['rr-expired'], 'SUPERSEDED_RELEASE_RECOVERY')
        self.assertEqual({n: sha(self.pilot.case_dir(r) / 'STOP.json') for n, r in self.rows.items()}, stop_before)   # no STOP file edited
        new_card = read(Path(active['rr-hit']['card']))
        old_card = read(self.rows['hit']['card'])
        self.assertEqual({k for k in new_card if new_card[k] != old_card.get(k)}, {'version'})                       # only the version
        self.assertEqual(self.rr.apply(self.pilot)['records'], [])                                                  # idempotent
        self.assertEqual(read(done['receipt'])['kept'][0]['design_id'], 'rr-sealed')

    def test_include_parks_reopens_a_queue_park_and_nothing_else(self):
        design = 'rr-park'
        card = dict(self.card, design_id=design)
        path = self.repo / 'data/l2_pipeline/cards' / (design + '.json')
        atomic(path, card)
        row = dict(self.row, design_id=design, card=str(path), card_sha256=sha(path))
        ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(design_id=design, attempt_id='old', action='dispatch', status='PARKED',
                                                                   reason='QUEUE_PARK: Sonnet lease expired twice; HUB_QUEUE required',
                                                                   metrics=dict(kind='QUEUE_PARK')))
        rows = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'] + [row]
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=rows))
        self.assertNotIn(design, [c['row']['design_id'] for c in self.rr.plan(self.pilot)[0]])
        self.assertIn(design, [c['row']['design_id'] for c in self.rr.plan(self.pilot, include_parks=True)[0]])
        done = self.rr.apply(self.pilot, include_parks=True)
        self.assertIn(design, [r['design_id'] for r in done['records']])

    def test_it_needs_its_window_and_the_f7_to_f10_code(self):
        (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task='SOMETHING_ELSE')))
        with self.assertRaisesRegex(ValueError, 'WINDOW_'):
            self.rr.apply(self.pilot)
        (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task='WINDOW_REDESIGN_3')))
        with patch.object(q, 'STATES', ('pending', 'in_progress', 'done', 'hub')), self.assertRaisesRegex(ValueError, 'F7-F10 are not live: F7'):
            self.rr.apply(self.pilot)
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]['attempt_id'], 'old')

    def test_command_line_prints_the_plan_and_applies_inside_the_window(self):
        with patch('scripts.l2_pipeline.autopilot.Autopilot', return_value=self.pilot):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.rr.main(['plan'])
            plan = json.loads(buffer.getvalue())
            self.assertEqual(sorted(c['design_id'] for c in plan['candidates']), ['rr-expired', 'rr-hit'])
            self.assertEqual([k['design_id'] for k in plan['kept']], ['rr-sealed'])
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.rr.main(['apply', '--task', 'WINDOW_REDESIGN_3'])
            applied = json.loads(buffer.getvalue())
        self.assertEqual(sorted(r['design_id'] for r in applied['records']), ['rr-expired', 'rr-hit'])
        self.assertTrue(applied['receipt'].endswith('APPLICATION_001.json'))
        with patch('scripts.l2_pipeline.autopilot.Autopilot', return_value=self.pilot), self.assertRaisesRegex(ValueError, 'WINDOW_'):
            (self.registry / 'CODE_TASK.lock').write_text(json.dumps(dict(task='WINDOW_OTHER')))
            self.rr.main(['apply', '--task', 'WINDOW_REDESIGN_3'])

    def test_the_status_is_in_the_requeue_whitelist(self):
        from scripts.l2_pipeline.requeue import requeue
        atomic(self.ruling, dict(self.value, decision='resume', superseded_status='SUPERSEDED_RELEASE_RECOVERY'))
        requeue(self.card['design_id'], self.newpath, self.ruling, self.pilot)
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json')['entries'][0]['status'], 'SUPERSEDED_RELEASE_RECOVERY')


if __name__ == '__main__':
    unittest.main()
