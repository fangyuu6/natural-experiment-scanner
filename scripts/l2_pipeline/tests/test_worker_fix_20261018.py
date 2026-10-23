"""Worker fix (POST_W3_DIAGNOSTIC_20261018): G1 a lease with no heartbeat for 20 minutes expires, G2 the role guard knows real worker names,
G3 several pages in one fetch-evidence call with polite pacing and archive.org back-off, G4 a hub-to-pending route for packets lost to
dead workers. Synthetic data only; no network, no outcome value."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.l2_pipeline.agents import evidence_fetch as ef
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.agents.contract_tests import golden_output
from scripts.l2_pipeline.agents.jobs import load_job
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.tests import test_agents as fixtures
from scripts.l2_pipeline.tests.test_named_panel_20261016 import FakeWeb, page
from scripts.l2_pipeline.tests import test_release_fix_20261018 as rf   # the module, not its classes: their own tests do not run again here


# --- G1 ---------------------------------------------------------------------------------------------------------------

class LeaseSilenceTests(unittest.TestCase):
    setUp = fixtures.AgentTests.setUp
    tearDown = fixtures.AgentTests.tearDown
    emit = fixtures.AgentTests.emit

    def packet(self):
        job = self.emit('S5', 'Card')
        root = self.root / 'queue'
        return job, root, q.enqueue(job.job_path, dict(design_id=job.design_id, attempt_id='invented'), root)

    def test_the_rule_is_twenty_minutes_and_the_lease_stays_ninety(self):
        self.assertEqual((q.DEAD_SECONDS, q.LEASE_SECONDS, q.SILENT_SECONDS), (20 * 60, 90 * 60, 15 * 60))

    def test_a_claimed_packet_with_no_heartbeat_for_20_minutes_goes_back_to_pending(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        q.heartbeat(root, r['job_id'], lease['token'], now=1000 + 200)
        self.assertEqual(q.reap(root, now=1200 + 20 * 60 - 1), [])
        self.assertEqual([e['status'] for e in q.reap(root, now=1200 + 20 * 60)], ['pending'])
        self.assertIsNotNone(q.claim(root, 'second', r['job_id'], now=1200 + 20 * 60 + 1))
        with self.assertRaisesRegex(ValueError, 'not claimed|Stale'):   # the dead worker's token no longer works
            q.heartbeat(root, r['job_id'], lease['token'], now=1200 + 20 * 60 + 2)

    def test_a_worker_that_heartbeats_keeps_the_packet_for_the_whole_ninety_minute_lease_and_longer(self):
        job, root, r = self.packet()
        lease = q.claim(root, 'worker', now=1000)
        for k in range(1, 18):   # a heartbeat every 5 minutes for 85 minutes
            q.heartbeat(root, r['job_id'], lease['token'], now=1000 + k * 300)
            self.assertEqual(q.reap(root, now=1000 + k * 300 + 20 * 60 - 1), [])
        self.assertEqual(q.owned(root, r['job_id'], lease['token'], 1000 + 17 * 300 + 60)[1]['worker'], 'worker')   # still owned at minute 85
        self.assertEqual([e['status'] for e in q.reap(root, now=1000 + 17 * 300 + 20 * 60)], ['pending'])

    def test_the_text_a_worker_reads_names_the_20_minute_rule(self):
        self.assertIn('Silence of 20 min ends a lease', q.WORKER_INSTRUCTIONS)
        self.assertNotIn('25 min', q.WORKER_INSTRUCTIONS)


# --- G2 ---------------------------------------------------------------------------------------------------------------

class RoleGuardRealNameTests(rf.ReleaseCase):
    def test_a_reused_base_name_no_longer_starves_the_other_role(self):
        """The 2026-10-18 pattern: the dispatcher asked for the same base name again and again; every suffixed worker counted for the
        base, so after A chunks had been worked nobody could claim the B chunks of that design."""
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        small = emit_pair(self.card, self.card['treatment']['universe'][:10], root=self.root / 'route_small', attempt_id='small')[0]
        first = q.enqueue(small.job_path, dict(self.row), self.queue)               # an ordinary packet: base name 'w' is used for real once
        self.assertEqual(q.claim(self.queue, 'w', first['job_id'], now=100)['worker'], 'w')
        a_id, b_id = self.enqueue(self.job_a)['job_id'], self.enqueue(self.job_b)['job_id']
        a_chunks, b_chunks = self.chunks(a_id), self.chunks(b_id)
        one = q.claim(self.queue, 'w', a_chunks[0]['job_id'], now=101)
        two = q.claim(self.queue, 'w', a_chunks[1]['job_id'], now=102)
        self.assertTrue(one['worker'].startswith('w-') and two['worker'].startswith('w-') and one['worker'] != two['worker'])
        third = q.claim(self.queue, 'w', b_chunks[0]['job_id'], now=103)             # before the fix: None (base name 'w' held role A)
        self.assertIsNotNone(third)
        self.assertEqual({one['requested'], two['requested'], third['requested']}, {'w'})
        self.assertIsNotNone(q.claim(self.queue, 'w', b_chunks[1]['job_id'], now=104))

    def test_a_session_that_presents_the_name_its_claim_returned_still_cannot_code_the_other_role(self):
        a_id, b_id = self.enqueue(self.job_a)['job_id'], self.enqueue(self.job_b)['job_id']
        a_chunks, b_chunks = self.chunks(a_id), self.chunks(b_id)
        q.claim(self.queue, 'w', a_chunks[0]['job_id'], now=100)
        second = q.claim(self.queue, 'w', a_chunks[1]['job_id'], now=101)
        self.assertNotEqual(second['worker'], 'w')
        self.assertIsNone(q.claim(self.queue, second['worker'], b_chunks[0]['job_id'], now=102))   # the returned name is the identity
        self.assertIsNone(q.claim(self.queue, 'w', b_chunks[0]['job_id'], now=103))                # the first holder kept the plain name
        self.assertIsNotNone(q.claim(self.queue, 'someone-else', b_chunks[0]['job_id'], now=104))

    def test_holders_are_the_names_the_claim_returned(self):
        from scripts.l2_pipeline.agents import history_chunks as hc
        a_id = self.enqueue(self.job_a)['job_id']
        entries = self.chunks(a_id)
        q.claim(self.queue, 'w', entries[0]['job_id'], now=100)
        lease = q.claim(self.queue, 'w', entries[1]['job_id'], now=101)
        self.assertEqual(hc._holders(self.queue / 'in_progress' / entries[1]['job_id']), {lease['worker']})


# --- G3 ---------------------------------------------------------------------------------------------------------------

class FetchManyTests(rf.ReleaseCase):
    URLS = ['https://leg.example.gov/code/title-%d' % n for n in (1, 2, 3)]

    def setUp(self):
        super().setUp()
        self.parent_id = self.enqueue()['job_id']
        self.entry = self.chunks(self.parent_id)[0]
        self.job = load_job(Path(self.entry['job_path']))
        self.lease = q.claim(self.queue, 'w1', self.entry['job_id'], now=time.time() - 50)
        self.pages = {u: page('Section %d of the code says the act takes effect in the year 200%d for everybody' % (n, n))
                      for n, u in enumerate(self.URLS, 1)}
        self.web = FakeWeb(pages=self.pages)

    def many(self, urls, web=None, budget=None):
        with patch.object(ef, 'http_get', web or self.web):
            return ef.fetch_evidence_many(self.queue, self.entry['job_id'], self.lease['token'], urls, budget=budget)

    def evidence_files(self):
        folder = Path(self.job.output_dir) / 'evidence'
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_three_pages_in_one_call_are_each_saved_hashed_and_logged_as_before(self):
        out = self.many(self.URLS)
        self.assertEqual((out['requested'], out['fetched'], out['failed']), (3, 3, 0))
        for url, result in zip(self.URLS, out['results']):
            raw = self.pages[url]
            self.assertTrue(result['ok'])
            self.assertEqual((result['url'], result['via'], result['sha256']), (url, 'live', hashlib.sha256(raw).hexdigest()))
            self.assertEqual((Path(self.job.output_dir) / result['archive_path']).read_bytes(), raw)
        log = read(self.queue / 'in_progress' / self.entry['job_id'] / 'EVIDENCE_LOG.json')
        self.assertEqual([e['url'] for e in log['entries']], self.URLS)
        self.assertEqual(len(self.evidence_files()), 3)

    def test_one_dead_page_is_reported_and_the_others_still_come(self):
        bad = 'https://gone.example.gov/missing'
        out = self.many([self.URLS[0], bad, self.URLS[1]])
        self.assertEqual((out['fetched'], out['failed']), (2, 1))
        self.assertEqual([r['ok'] for r in out['results']], [True, False, True])
        self.assertEqual(out['results'][1]['url'], bad)
        self.assertIn('no Wayback snapshot', out['results'][1]['error'])
        self.assertEqual(len(self.evidence_files()), 2)                                   # nothing is written for the dead page
        self.assertEqual(len(read(self.queue / 'in_progress' / self.entry['job_id'] / 'EVIDENCE_LOG.json')['entries']), 2)

    def test_a_malformed_url_is_one_failed_result_not_a_failed_call(self):
        out = self.many([self.URLS[0], 'ftp://x.gov/a', 'http://localhost/x'])
        self.assertEqual([r['ok'] for r in out['results']], [True, False, False])

    def test_a_repeated_url_is_fetched_once(self):
        out = self.many([self.URLS[0], self.URLS[0], self.URLS[1]])
        self.assertEqual(self.web.calls, [self.URLS[0], self.URLS[1]])
        self.assertEqual(out['results'][1], dict(ok=True, url=self.URLS[0], duplicate_of=0))

    def test_a_call_that_runs_out_of_time_stops_starting_pages_and_says_which_were_not_attempted(self):
        out = self.many(self.URLS, budget=0)                                              # the first page is always tried
        self.assertEqual((out['fetched'], out['failed'], out['not_attempted']), (1, 2, 2))
        self.assertEqual([r['ok'] for r in out['results']], [True, False, False])
        self.assertEqual([r.get('not_attempted') for r in out['results']], [None, True, True])
        self.assertIn('fetch this page in a new call', out['results'][1]['error'])
        self.assertEqual(self.web.calls, [self.URLS[0]])
        again = self.many(self.URLS[1:])                                                  # the worker repeats the rest
        self.assertEqual((again['fetched'], again['not_attempted']), (2, 0))
        self.assertEqual(ef.CALL_BUDGET_SECONDS, 100)

    def test_more_than_the_limit_is_refused_before_anything_is_fetched(self):
        urls = ['https://leg.example.gov/p%d' % n for n in range(ef.MAX_URLS_PER_CALL + 1)]
        with self.assertRaisesRegex(ValueError, 'At most %d' % ef.MAX_URLS_PER_CALL):
            self.many(urls)
        self.assertEqual(self.web.calls, [])
        with self.assertRaisesRegex(ValueError, 'No URL'):
            self.many([])

    def test_the_lease_is_checked_and_a_stale_token_stops_the_call(self):
        with patch.object(ef, 'http_get', self.web), self.assertRaisesRegex(ValueError, 'Stale|not claimed'):
            ef.fetch_evidence_many(self.queue, self.entry['job_id'], 'bad-token', self.URLS)
        self.assertEqual(self.web.calls, [])

    def write_list(self, text, name='urls_1.txt'):
        path = Path(self.job.output_dir) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def read_list(self, path):
        return ef.read_urls_file(self.queue, self.entry['job_id'], self.lease['token'], str(path))

    def test_a_url_list_file_gives_one_url_per_line_and_skips_blank_and_comment_lines(self):
        path = self.write_list('# the official pages\n\n' + '\n'.join('  ' + u + '  ' for u in self.URLS) + '\n')
        self.assertEqual(self.read_list(path), self.URLS)

    def test_a_url_list_file_must_be_a_small_txt_file_inside_the_packet_output_directory(self):
        outside = self.root / 'urls_outside.txt'
        outside.write_text(self.URLS[0] + '\n')
        for bad in (outside, Path(self.job.output_dir) / 'nope.txt', self.write_list('x\n', 'urls_2.json')):
            with self.subTest(str(bad)), self.assertRaisesRegex(ValueError, 'inside the packet output directory'):
                self.read_list(bad)
        link = Path(self.job.output_dir) / 'urls_link.txt'
        link.symlink_to(outside)                                                          # a symlink out of the directory is refused too
        with self.assertRaisesRegex(ValueError, 'inside the packet output directory'):
            self.read_list(link)
        with self.assertRaisesRegex(ValueError, 'larger than'):
            self.read_list(self.write_list(self.URLS[0] * 3000, 'urls_big.txt'))
        with self.assertRaisesRegex(ValueError, 'lists no URL'):
            self.read_list(self.write_list('# nothing\n\n', 'urls_empty.txt'))
        with self.assertRaisesRegex(ValueError, 'At most %d' % ef.MAX_URLS_PER_CALL):
            self.read_list(self.write_list('\n'.join('https://leg.example.gov/p%d' % n for n in range(ef.MAX_URLS_PER_CALL + 1)), 'urls_many.txt'))

    def test_errors_never_echo_what_a_file_or_a_line_contained(self):
        secret = 'TOPSECRET-line-from-a-file'
        self.write_list(secret + '\n', 'urls_secret.json')
        with self.assertRaises(ValueError) as caught:
            self.read_list(Path(self.job.output_dir) / 'urls_secret.json')               # wrong suffix: refused before the file is read
        self.assertNotIn(secret, str(caught.exception))
        out = self.many([secret])                                                         # a line that is no URL
        self.assertIs(out['results'][0]['ok'], False)
        self.assertNotIn(secret, out['results'][0]['error'])

    def run_cli(self, *argv, web=None):
        buffer = io.StringIO()
        with patch.object(ef, 'http_get', web or self.web), contextlib.redirect_stdout(buffer):
            q.main(['fetch-evidence', '--root', str(self.queue), '--job-id', self.entry['job_id'], '--token', self.lease['token'], *argv])
        return json.loads(buffer.getvalue())

    def test_the_command_takes_repeated_url_and_a_list_file_and_one_url_prints_what_it_always_did(self):
        one = self.run_cli('--url', self.URLS[0])
        self.assertEqual((one['url'], one['via'], len(one['sha256'])), (self.URLS[0], 'live', 64))   # no 'results' wrapper for a single URL
        many = self.run_cli('--url', self.URLS[1], '--url', self.URLS[2])
        self.assertEqual((many['requested'], many['fetched']), (2, 2))
        path = self.write_list(self.URLS[0] + '\n' + self.URLS[2] + '\n')
        listed = self.run_cli('--urls-file', str(path), '--url', self.URLS[1])
        self.assertEqual([r['url'] for r in listed['results']], [self.URLS[1], self.URLS[0], self.URLS[2]])

    def test_one_dead_url_prints_a_json_error_and_no_traceback(self):
        out = self.run_cli('--url', 'https://gone.example.gov/missing')
        self.assertIs(out['ok'], False)
        self.assertIn('Wayback', out['error'])

    def test_the_command_needs_a_url(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            q.main(['fetch-evidence', '--root', str(self.queue), '--job-id', self.entry['job_id'], '--token', self.lease['token']])

    def test_a_packet_with_a_list_file_still_finishes_through_the_ordinary_collector(self):
        parent_id = self.parent_id

        def fetch_then_write(job, value):
            lease = read(self.queue / 'in_progress' / q.identity(job.job_path) / 'LEASE.json')
            path = Path(job.output_dir) / 'urls_1.txt'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.URLS[0] + '\n' + self.URLS[1] + '\n')
            with patch.object(ef, 'http_get', self.web):
                urls = ef.read_urls_file(self.queue, q.identity(job.job_path), lease['token'], str(path))
                ef.fetch_evidence_many(self.queue, q.identity(job.job_path), lease['token'], urls)
        second = self.chunks(parent_id)[1]
        result, _, _ = self.work_chunk(second, 'w2', time.time() - 60, with_rule=False, mutate=fetch_then_write)
        self.assertTrue(str(result).endswith(second['job_id']), result)                  # finish() accepted it


class ArchiveBackoffTests(unittest.TestCase):
    URL = 'https://leg.example.gov/code/title-5'

    def sleeps(self):
        slept = []
        patcher = patch.object(ef.time, 'sleep', side_effect=slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        return slept

    def test_archive_org_gets_three_tries_with_15_then_30_seconds_plus_jitter(self):
        slept, calls = self.sleeps(), []

        def always_429(url, *a, **k):
            calls.append(url)
            raise ef.FetchError('HTTP 429')
        with self.assertRaisesRegex(ef.FetchError, '429'):
            ef.polite(always_429)('https://web.archive.org/web/20240102030405id_/' + self.URL)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(slept), 2)
        self.assertTrue(15 <= slept[0] <= 15 + ef.JITTER and 30 <= slept[1] <= 30 + ef.JITTER, slept)

    def test_archive_org_recovers_when_the_third_try_works(self):
        slept, calls = self.sleeps(), []

        def third_time(url, *a, **k):
            calls.append(url)
            if len(calls) < 3:
                raise ef.FetchError('HTTP 429')
            return dict(status=200, data=b'ok')
        self.assertEqual(ef.polite(third_time)(ef.WAYBACK_API + 'x')['data'], b'ok')
        self.assertEqual(len(slept), 2)

    def test_a_retry_after_header_is_honoured_up_to_a_minute(self):
        slept, calls = self.sleeps(), []

        def asked_to_wait(url, *a, **k):
            calls.append(url)
            if len(calls) == 1:
                error = ef.FetchError('HTTP 429')
                error.retry_after = 45
                raise error
            return dict(status=200, data=b'ok')
        ef.polite(asked_to_wait)(ef.WAYBACK_API + 'x')
        self.assertTrue(45 <= slept[0] <= 45 + ef.JITTER, slept)
        calls.clear()
        slept.clear()

        def far_too_long(url, *a, **k):
            calls.append(url)
            if len(calls) == 1:
                error = ef.FetchError('HTTP 429')
                error.retry_after = 3600
                raise error
            return dict(status=200, data=b'ok')
        ef.polite(far_too_long)(ef.WAYBACK_API + 'x')
        self.assertTrue(ef.RETRY_AFTER_CAP <= slept[0] <= ef.RETRY_AFTER_CAP + ef.JITTER, slept)

    def test_any_other_host_keeps_one_retry_after_the_short_pause(self):
        slept, calls = self.sleeps(), []

        def always_429(url, *a, **k):
            calls.append(url)
            raise ef.FetchError('HTTP 429')
        with self.assertRaises(ef.FetchError):
            ef.polite(always_429)(self.URL)
        self.assertEqual((len(calls), slept), (2, [ef.PAUSE]))

    def test_other_errors_are_not_retried(self):
        slept, calls = self.sleeps(), []

        def forbidden(url, *a, **k):
            calls.append(url)
            raise ef.FetchError('HTTP 403')
        with self.assertRaises(ef.FetchError):
            ef.polite(forbidden)(ef.WAYBACK_API + 'x')
        self.assertEqual((len(calls), slept), (1, []))

    def test_http_get_keeps_the_retry_after_the_server_sent(self):
        import urllib.error

        class Opener:
            def open(self, request, timeout=0):
                raise urllib.error.HTTPError(request.full_url, 429, 'Too Many Requests', {'Retry-After': '17'}, None)
        with patch('urllib.request.build_opener', return_value=Opener()), patch.object(ef, '_public_host'):
            with self.assertRaises(ef.FetchError) as caught:
                ef.http_get('https://leg.example.gov/a')
        self.assertEqual((str(caught.exception), caught.exception.retry_after), ('HTTP 429', 17.0))


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / 'PACE'
        self.slept = []
        patcher = patch.object(ef.time, 'sleep', side_effect=self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_second_request_to_the_same_host_waits_for_the_gap(self):
        ef.pace(self.dir, 'https://leg.example.gov/a')
        self.assertEqual(self.slept, [])
        ef.pace(self.dir, 'https://leg.example.gov/b')
        self.assertEqual(len(self.slept), 1)
        self.assertTrue(0 < self.slept[0] <= ef.HOST_GAP)

    def test_hosts_do_not_wait_for_each_other_and_archive_org_waits_longer(self):
        ef.pace(self.dir, 'https://leg.example.gov/a')
        ef.pace(self.dir, 'https://other.example.gov/a')
        self.assertEqual(self.slept, [])
        ef.pace(self.dir, 'https://web.archive.org/web/1')
        ef.pace(self.dir, 'https://web.archive.org/web/2')
        self.assertTrue(ef.HOST_GAP < self.slept[0] <= ef.ARCHIVE_GAP, self.slept)

    def test_a_pace_directory_that_cannot_be_used_never_fails_a_fetch(self):
        blocker = Path(self.tmp.name) / 'file'
        blocker.write_text('x')
        ef.pace(blocker / 'PACE', 'https://leg.example.gov/a')   # a directory below a file: OSError inside, swallowed
        self.assertEqual(self.slept, [])

    def test_only_the_real_network_function_is_paced(self):
        fake_calls = []

        def fake(url, *a, **k):
            fake_calls.append(url)
            return dict(status=200, data=b'x')
        ef.polite(fake, pace_dir=self.dir)('https://leg.example.gov/a')
        ef.polite(fake, pace_dir=self.dir)('https://leg.example.gov/a')
        self.assertFalse(self.dir.exists())                                              # tests (and any replaced function) are not slowed
        self.assertEqual(self.slept, [])
        with patch.object(ef, '_REAL_HTTP_GET', fake):
            ef.polite(fake, pace_dir=self.dir)('https://leg.example.gov/a')
        self.assertTrue(self.dir.exists())

    def test_fetch_for_packet_paces_into_the_queue_pace_directory(self):
        packet = Path(self.tmp.name) / 'queue' / 'in_progress' / ('0' * 64)
        packet.mkdir(parents=True)
        calls = []

        def real(url, *a, **k):
            calls.append(url)
            return dict(status=200, final_url=url, content_type='text/html', charset='', data=b'<html>ok</html>')
        with patch.object(ef, '_REAL_HTTP_GET', real), patch.object(ef, 'http_get', real):
            for _ in range(2):
                ef.fetch_for_packet(packet, None, 'https://leg.example.gov/a', now=1.0)
        self.assertTrue((packet.parents[1] / 'PACE' / 'leg.example.gov').is_file())
        self.assertEqual(len(self.slept), 1)


# --- G4 ---------------------------------------------------------------------------------------------------------------

class ReturnHubTests(rf.ReleaseCase):
    def setUp(self):
        super().setUp()
        self.parent_id = self.enqueue()['job_id']
        self.entry = self.chunks(self.parent_id)[0]
        self.id = self.entry['job_id']
        self.names = 0

    def lose(self, times):
        """``times`` workers claim the packet and vanish; the scheduler reaps each lease."""
        statuses = []
        for _ in range(times):
            self.names += 1
            now = 1000 + self.names * 10000
            q.claim(self.queue, 'x%d' % self.names, self.id, now=now)
            statuses += [e['status'] for e in q.reap(self.queue, now=now + q.SILENT_SECONDS)]
        return statuses

    def test_six_lost_workers_put_the_packet_in_the_hub_and_the_plan_lists_it_without_writing(self):
        self.assertEqual(self.lose(6), ['pending'] * 5 + ['hub'])
        before = sorted(p.name for p in (self.queue / 'hub' / self.id).iterdir())
        out = q.return_hub(self.queue)
        self.assertEqual((out['applied'], [r['job_id'] for r in out['returned']], out['skipped']), (False, [self.id], []))
        self.assertEqual(out['returned'][0]['role'], 'A')
        self.assertEqual(sorted(p.name for p in (self.queue / 'hub' / self.id).iterdir()), before)
        self.assertFalse((self.queue / 'pending' / self.id).exists())

    def test_apply_returns_it_with_a_fresh_budget_keeping_every_record(self):
        self.lose(6)
        hub = self.queue / 'hub' / self.id
        atomic(hub / 'HUB_DISPOSITION.json', dict(disposition='parked'))
        expired = {p.name: p.read_bytes() for p in hub.glob('EXPIRED_*.json')}
        later = time.time() + 10
        out = q.return_hub(self.queue, reason='workers were lost to the shell-command stall, fixed', apply=True, now=later)
        self.assertEqual([r['returns'] for r in out['returned']], [1])
        pending = self.queue / 'pending' / self.id
        self.assertTrue(pending.is_dir() and not hub.exists())
        self.assertEqual({p.name: p.read_bytes() for p in pending.glob('EXPIRED_*.json')}, expired)               # lease history untouched
        marker = read(pending / 'HUB_RETURNED_1.json')
        self.assertEqual((marker['returns'], marker['expiries'], marker['hub_reason']), (1, 6, 'Sonnet lease expired 6 times'))
        self.assertIn('shell-command stall', marker['reason'])
        self.assertFalse((pending / 'HUB_DISPOSITION.json').exists())                                          # the old verdict is kept under a new name
        self.assertEqual(read(pending / 'HUB_DISPOSITION_1.json'), dict(disposition='parked'))
        state = read(pending / 'STATE.json')
        self.assertEqual((state['expiries'], state['returns'], state['budget_base'], state['requeued_at']), (6, 1, 6, later))
        self.assertNotIn('expiry_transition', state)
        self.assertEqual(q.pending_order(self.queue)[-1].name, self.id)                                         # back of the queue

    def test_the_returned_packet_gets_six_more_leases_and_the_expiry_files_keep_counting(self):
        self.lose(6)
        q.return_hub(self.queue, reason='again', apply=True)
        self.assertEqual(self.lose(6), ['pending'] * 5 + ['hub'])
        hub = self.queue / 'hub' / self.id
        self.assertEqual(sorted(int(p.name[8:-5]) for p in hub.glob('EXPIRED_*.json')), list(range(1, 13)))
        self.assertEqual(q.hub_reason(hub), 'Sonnet lease expired 12 times')

    def test_a_packet_is_returned_at_most_twice(self):
        for n in (1, 2):
            self.lose(6)
            self.assertEqual(len(q.return_hub(self.queue, reason='again', apply=True)['returned']), 1, n)
        self.lose(6)
        out = q.return_hub(self.queue, reason='again', apply=True)
        self.assertEqual(out['returned'], [])
        self.assertIn('already returned 2 times', out['skipped'][0]['why'])
        self.assertTrue((self.queue / 'hub' / self.id).is_dir())

    def test_a_structural_release_a_worker_release_and_a_completion_are_not_returned(self):
        other = self.chunks(self.parent_id)[1]
        lease = q.claim(self.queue, 'r1', other['job_id'], now=100)
        q.release(self.queue, other['job_id'], lease['token'], 'needs about 100 fetched official pages', now=110, kind='too_large')
        self.lose(6)
        out = q.return_hub(self.queue, reason='x', apply=True)
        self.assertEqual([r['job_id'] for r in out['returned']], [self.id])
        self.assertEqual([s['job_id'] for s in out['skipped']], [other['job_id']])
        self.assertIn('not worker-side only', out['skipped'][0]['why'])
        self.assertTrue((self.queue / 'hub' / other['job_id']).is_dir())
        self.assertFalse(q.worker_side_only(self.queue / 'hub' / other['job_id']))

    def test_a_completed_packet_is_never_worker_side_only(self):
        self.lose(6)
        hub = self.queue / 'hub' / self.id
        self.assertTrue(q.worker_side_only(hub))
        atomic(hub / 'COMPLETION.json', dict(output_sha256='0' * 64))
        self.assertFalse(q.worker_side_only(hub))
        self.assertEqual(q.return_hub(self.queue)['returned'], [])

    def test_three_worker_releases_are_a_workers_verdict_on_the_packet_and_stay_in_the_hub(self):
        other = self.chunks(self.parent_id)[1]
        for n in (1, 2, 3):
            lease = q.claim(self.queue, 'rel%d' % n, other['job_id'], now=100 * n)
            q.release(self.queue, other['job_id'], lease['token'], 'context ran out', now=100 * n + 1, kind='worker')
        self.assertTrue((self.queue / 'hub' / other['job_id']).is_dir())
        self.assertFalse(q.worker_side_only(self.queue / 'hub' / other['job_id']))
        self.assertEqual(q.return_hub(self.queue)['returned'], [])

    def test_a_draft_submission_is_kept_beside_the_packet_so_the_next_worker_can_write(self):
        job = load_job(Path(self.entry['job_path']))
        self.lose(1)
        self.names += 1
        now = 1000 + self.names * 10000
        q.claim(self.queue, 'x%d' % self.names, self.id, now=now)
        Path(job.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(job.output_path).write_text('{"draft": true}')                              # a worker wrote something, then vanished
        self.assertEqual([e['status'] for e in q.reap(self.queue, now=now + q.SILENT_SECONDS)], ['hub'])   # a packet holding a draft: second expiry
        out = q.return_hub(self.queue, reason='draft kept', apply=True)
        pending = self.queue / 'pending' / self.id
        self.assertEqual([r['job_id'] for r in out['returned']], [self.id])
        self.assertFalse(Path(job.output_path).exists())
        self.assertEqual((pending / 'HUB_RETURNED_1_submission.json').read_text(), '{"draft": true}')
        self.assertTrue(read(pending / 'HUB_RETURNED_1.json')['submission_kept'].endswith('HUB_RETURNED_1_submission.json'))

    def test_filters_and_the_reason_rule(self):
        self.lose(6)
        self.assertEqual(q.return_hub(self.queue, design_id='another-design')['returned'], [])
        self.assertEqual(q.return_hub(self.queue, attempt_id='another-attempt')['returned'], [])
        self.assertEqual(len(q.return_hub(self.queue, design_id=self.row['design_id'], attempt_id=self.row['attempt_id'])['returned']), 1)
        self.assertEqual(q.return_hub(self.queue, job_id='f' * 64)['returned'], [])
        with self.assertRaisesRegex(ValueError, 'reason'):
            q.return_hub(self.queue, apply=True)
        with self.assertRaisesRegex(ValueError, 'Invalid packet ID'):
            q.return_hub(self.queue, job_id='../x')
        self.assertTrue((self.queue / 'hub' / self.id).is_dir())

    def test_the_command_is_a_hub_operation_and_lists_unless_told_to_apply(self):
        self.lose(6)
        with patch.dict(os.environ):
            os.environ.pop('L2_HUB', None)
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                q.main(['return-hub', '--root', str(self.queue)])
        with patch.dict(os.environ, {'L2_HUB': '1'}):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                q.main(['return-hub', '--root', str(self.queue)])
            self.assertEqual(json.loads(buffer.getvalue())['applied'], False)
            self.assertTrue((self.queue / 'hub' / self.id).is_dir())
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                q.main(['return-hub', '--root', str(self.queue), '--apply', '--reason', 'fixed', '--design', self.row['design_id']])
            self.assertEqual(len(json.loads(buffer.getvalue())['returned']), 1)
        self.assertTrue((self.queue / 'pending' / self.id).is_dir())

    def test_a_returned_packet_can_be_claimed_and_finished_like_any_other(self):
        self.lose(6)
        q.return_hub(self.queue, reason='fixed', apply=True)
        result, lease, _ = self.work_chunk(self.entry, 'fresh', time.time() - 60)
        self.assertTrue(str(result).endswith(self.id), result)
        self.assertTrue((self.queue / 'done' / self.id).is_dir())


# --- G4: reopening only worker-side parks -------------------------------------------------------------------------------

class WorkerSideReopenTests(unittest.TestCase):
    setUp = rf.RecoveryTests.setUp
    CAUSE = rf.RecoveryTests.CAUSE

    def park(self, name, **metrics):
        from scripts.l2_pipeline.autopilot import read as read_
        from scripts.l2_pipeline.storage import ensure_once, sha
        design = 'rr-' + name
        card = dict(self.card, design_id=design)
        path = self.repo / 'data/l2_pipeline/cards' / (design + '.json')
        atomic(path, card)
        row = dict(self.row, design_id=design, card=str(path), card_sha256=sha(path))
        ensure_once(self.pilot.case_dir(row) / 'PARKED.json', dict(
            design_id=design, attempt_id='old', action='dispatch', status='PARKED', reason='QUEUE_PARK: INSUFFICIENT_WORK: ' + name,
            metrics=dict(kind='QUEUE_PARK', **metrics)))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=read_(self.registry / 'ACTIVE_DESIGNS.json')['designs'] + [row]))
        return design

    def test_only_worker_side_queue_parks_are_reopened_with_the_flag_and_structural_ones_wait(self):
        from scripts.l2_pipeline import release_recovery as rr
        lost = self.park('lost', cause='lease_expired', release_kind=None)
        again = self.park('workers', cause='released_repeatedly', release_kind='worker')
        big = self.park('big', cause='structural_release', release_kind='too_large')
        old = self.park('old-style')                                                      # a park without the new fields: not provably worker-side
        names = lambda items: sorted(c['row']['design_id'] for c in items)
        everything, _ = rr.plan(self.pilot, include_parks=True)
        self.assertEqual(names(everything), sorted([lost, again, big, old, 'rr-expired', 'rr-hit']))
        candidates, kept = rr.plan(self.pilot, include_parks=True, worker_side_only=True)
        self.assertEqual(names(candidates), sorted([lost, again, 'rr-expired', 'rr-hit']))                   # STOP records are not filtered
        held = {k['design_id']: k for k in kept}
        self.assertEqual(sorted(held), sorted([big, old, 'rr-sealed']))
        self.assertIn('structural queue park', held[big]['why'])
        done = rr.apply(self.pilot, include_parks=True, worker_side_only=True)
        self.assertEqual(sorted(r['design_id'] for r in done['records']), sorted([lost, again, 'rr-expired', 'rr-hit']))
        self.assertIs(read(done['receipt'])['worker_side_only'], True)

    def test_the_command_line_has_the_flag(self):
        from scripts.l2_pipeline import release_recovery as rr
        self.park('big', cause='structural_release', release_kind='too_large')
        with patch('scripts.l2_pipeline.autopilot.Autopilot', return_value=self.pilot):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rr.main(['plan', '--include-parks', '--worker-side-only'])
        plan = json.loads(buffer.getvalue())
        self.assertNotIn('rr-big', [c['design_id'] for c in plan['candidates']])
        self.assertIn('rr-big', [k['design_id'] for k in plan['kept']])


if __name__ == '__main__':
    unittest.main()
