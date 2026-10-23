"""NAMED_PANEL 2026-10-16 (hub ruling HUB_RULING_20261016_NAMED_PANEL.md): Q3 evidence fetching and dispatcher
fixes, Q2 LAUS employment-population ratio (catalog V17), Q1 structural exclusions on S3-stopped cards.
Synthetic data only; no network."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents import evidence_fetch as ef
from scripts.l2_pipeline.agents import sonnet_queue as q
from scripts.l2_pipeline.autopilot import atomic, read
from scripts.l2_pipeline.storage import ensure_once, sha
from scripts.l2_pipeline.tests.test_autopilot import OfficialBridgeTests


def page(*sentences):
    body = ''.join('<p>' + s + '</p>' for s in sentences)
    return ('<html><head><title>t</title><script>var hidden="Hidden script sentence for the legislature";</script></head>'
            '<body>' + body + '</body></html>').encode()


class FakeWeb:
    """Replaces evidence_fetch.http_get; maps URL -> bytes (or an error)."""

    def __init__(self, pages=None, wayback=None, blocked=()):
        self.pages, self.wayback, self.blocked, self.calls = dict(pages or {}), dict(wayback or {}), set(blocked), []

    def __call__(self, url, timeout=30, cap=0):
        self.calls.append(url)
        if url.startswith(ef.WAYBACK_API):
            target = url[len(ef.WAYBACK_API):]
            from urllib.parse import unquote
            target = unquote(target)
            if target not in self.wayback:
                return dict(status=200, final_url=url, content_type='application/json', charset='',
                            data=json.dumps(dict(archived_snapshots={})).encode())
            return dict(status=200, final_url=url, content_type='application/json', charset='',
                        data=json.dumps(dict(archived_snapshots=dict(closest=dict(
                            available=True, status='200', timestamp='20240102030405',
                            url='http://web.archive.org/web/20240102030405/' + target)))).encode())
        if url.startswith('https://web.archive.org/web/'):
            original = url.split('id_/', 1)[1]
            if url.startswith(ef.WAYBACK_RAW):   # T191: direct form; unknown page -> 404, answer carries the snapshot address
                if original not in self.wayback:
                    raise ef.FetchError('HTTP 404')
                url = 'https://web.archive.org/web/20240102030405id_/' + original
            return dict(status=200, final_url=url, content_type='text/html', charset='utf-8', data=self.wayback[original])
        if url in self.blocked or url not in self.pages:
            raise ef.FetchError('HTTP 403')
        return dict(status=200, final_url=url, content_type='text/html', charset='utf-8', data=self.pages[url])


class QuoteAndUrlTests(unittest.TestCase):
    def test_quote_is_verbatim_after_whitespace_quote_and_dash_normalisation(self):
        raw = page('The act takes   effect on\nJanuary 1, 2001, as “amended” – see section 5.')
        self.assertEqual(ef.quote_check(raw, 'The act takes effect on January 1, 2001, as "amended" - see section 5.', 'text/html'), 'verified')

    def test_invented_or_altered_quote_is_not_found(self):
        raw = page('The act takes effect on January 1, 2001 for all residents.')
        self.assertEqual(ef.quote_check(raw, 'The act takes effect on January 1, 2002 for all residents.', 'text/html'), 'not_found')

    def test_scripts_and_short_quotes_never_verify(self):
        raw = page('Visible sentence about the statute of limitations.')
        self.assertEqual(ef.quote_check(raw, 'Hidden script sentence for the legislature', 'text/html'), 'not_found')
        self.assertEqual(ef.quote_check(raw, 'statute', 'text/html'), 'too_short')

    def test_pdf_without_readable_text_is_unavailable_not_verified(self):
        self.assertEqual(ef.quote_check(b'%PDF-1.4 not really a pdf', 'The act takes effect on January 1, 2001.', 'application/pdf'), 'unavailable')

    def test_hyphenated_line_break_in_extracted_text_still_verifies(self):
        raw = b'The adminis- tration of the program begins in the year 2001 under this section.'
        self.assertEqual(ef.quote_check(raw, 'The administration of the program begins in the year 2001', 'text/plain'), 'verified')

    def test_url_rules_refuse_credentials_other_schemes_and_local_hosts(self):
        for bad in ('ftp://x.gov/a', 'https://user:pw@x.gov/a', 'file:///etc/passwd', 'http://localhost/a',
                    'http://127.0.0.1/a', 'http://10.0.0.5/a', 'https://x.gov:22/a', '', 'https://x.gov/a b'):
            with self.subTest(bad), self.assertRaises(ef.FetchError):
                ef.check_url(bad)
        self.assertEqual(ef.check_url('https://leg.example.gov/a?b=1#frag'), 'https://leg.example.gov/a?b=1')

    def test_canonical_url_ignores_scheme_www_fragment_and_trailing_slash(self):
        self.assertEqual(ef.canonical_url('https://www.leg.gov/a/b/#x'), ef.canonical_url('http://leg.gov/a/b'))
        self.assertNotEqual(ef.canonical_url('https://leg.gov/a'), ef.canonical_url('https://leg.gov/b'))


class QueueCase(OfficialBridgeTests):
    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline.intake.history_routing import emit_pair
        self.queue = self.root / 'queue'
        self.fresh = emit_pair(self.card, self.card['treatment']['universe'], root=self.root / 'route_q', attempt_id='queue_test')
        self.job = self.fresh[0]
        self.row = dict(design_id=self.job.design_id, attempt_id='t')
        # F7 splits a history job of more than 10 units into chunk packets; these tests exercise one ordinary packet.
        chunking = patch('scripts.l2_pipeline.agents.history_chunks.CHUNK_UNITS', 1000)
        chunking.start()
        self.addCleanup(chunking.stop)
        self.packet = q.enqueue(self.job.job_path, self.row, self.queue)
        patcher = patch.object(q, 'root_for', return_value=self.queue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def claim(self, name='worker', now=100):
        return q.claim(self.queue, name, self.packet['job_id'], now=now)


class DispatcherTests(QueueCase):
    """The three dispatcher issues of YIELD_FIXES section 2."""

    def second_packet(self):
        other = self.fresh[1]
        return q.enqueue(other.job_path, dict(design_id=other.design_id, attempt_id='t'), self.queue)

    def set_enqueued(self, packet, value):
        path = self.queue / 'pending' / packet['job_id'] / 'STATE.json'
        state = read(path)
        state['enqueued_at'] = value
        atomic(path, state)

    def test_claim_order_is_oldest_first_not_hash_order(self):
        second = self.second_packet()
        by_name = sorted([self.packet, second], key=lambda p: p['job_id'])
        older = by_name[-1]  # the one that hash order would serve last
        self.set_enqueued(older, 10.0)
        self.set_enqueued(by_name[0], 20.0)
        self.assertEqual(Path(q.claim(self.queue, 'w1', now=100)['packet']).name, older['job_id'])
        self.assertEqual(Path(q.claim(self.queue, 'w2', now=100)['packet']).name, by_name[0]['job_id'])

    def test_a_repeated_worker_name_gets_a_short_suffix_and_a_new_name_is_kept(self):
        second = self.second_packet()
        first = q.claim(self.queue, 'sched-0000-1', self.packet['job_id'], now=100)
        again = q.claim(self.queue, 'sched-0000-1', second['job_id'], now=101)
        self.assertEqual(first['worker'], 'sched-0000-1')
        self.assertTrue(again['worker'].startswith('sched-0000-1-') and len(again['worker']) == len('sched-0000-1-') + 6, again['worker'])
        self.assertEqual(again['requested'], 'sched-0000-1')
        self.assertEqual(read(Path(again['packet']) / 'LEASE.json')['worker'], again['worker'])
        self.assertNotIn('requested', first)

    def test_forecast_persona_rule_still_follows_the_requested_name(self):
        from scripts.l2_pipeline.agents.packet_scope import can_claim
        history = dict(claims=[dict(worker='w-ab12cd', base='w', design_id='d', persona='Roth')])
        self.assertFalse(can_claim(history, 'w-ffee00', ('d', 'Card'), 'w'))
        self.assertTrue(can_claim(history, 'x-000000', ('d', 'Card'), 'x'))
        self.assertTrue(can_claim(history, 'w-ffee00', ('d', 'Roth'), 'w'))
        self.assertFalse(can_claim(dict(claims=[dict(worker='w', design_id='d', persona='Roth')]), 'w', ('d', 'Card')))

    def test_release_returns_the_packet_to_pending_at_once_and_ends_the_lease(self):
        lease = self.claim()
        done = q.release(self.queue, self.packet['job_id'], lease['token'], 'official site blocks every fetch', now=150)
        self.assertEqual(done['status'], 'pending')
        packet = self.queue / 'pending' / self.packet['job_id']
        self.assertTrue(packet.is_dir())
        marker = read(packet / 'RELEASED_1.json')
        self.assertEqual(marker['reason'], 'official site blocks every fetch')
        self.assertEqual(marker['lease']['worker'], 'worker')
        self.assertEqual(read(packet / 'STATE.json')['expiries'], 0)
        with self.assertRaisesRegex(ValueError, 'not claimed'):
            q.heartbeat(self.queue, self.packet['job_id'], lease['token'], now=151)
        again = q.claim(self.queue, 'other', self.packet['job_id'], now=200)
        self.assertEqual(Path(again['packet']).name, self.packet['job_id'])
        self.assertNotEqual(again['token'], lease['token'])

    def test_release_needs_the_token_and_a_reason_and_keeps_a_written_draft_aside(self):
        lease = self.claim()
        with self.assertRaisesRegex(ValueError, 'Stale or expired lease'):
            q.release(self.queue, self.packet['job_id'], 'wrong', 'x', now=150)
        with self.assertRaisesRegex(ValueError, 'reason'):
            q.release(self.queue, self.packet['job_id'], lease['token'], '  ', now=150)
        output = Path(self.job.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"draft": true}')
        q.release(self.queue, self.packet['job_id'], lease['token'], 'cannot finish', now=150)
        self.assertFalse(output.exists())
        kept = self.queue / 'pending' / self.packet['job_id'] / 'RELEASED_1_submission.json'
        self.assertEqual(kept.read_text(), '{"draft": true}')

    def test_released_packet_goes_behind_older_waiting_packets_and_the_third_release_goes_to_the_hub(self):
        second = self.second_packet()
        self.set_enqueued(self.packet, 1.0)
        self.set_enqueued(second, 2.0)
        lease = self.claim(now=100)
        q.release(self.queue, self.packet['job_id'], lease['token'], 'one', now=120)
        self.assertEqual(Path(q.claim(self.queue, 'w2', now=130)['packet']).name, second['job_id'])
        for n, status in ((2, 'pending'), (3, 'hub')):
            lease = q.claim(self.queue, 'w' + str(n + 2), self.packet['job_id'], now=200 * n)
            self.assertEqual(q.release(self.queue, self.packet['job_id'], lease['token'], 'again', now=200 * n + 1)['status'], status)
        self.assertTrue((self.queue / 'hub' / self.packet['job_id']).is_dir())
        with self.assertRaisesRegex(ValueError, 'released by workers 3 times'):
            q.state(self.job.job_path, self.job.repo)

    def test_cli_exposes_the_new_commands(self):
        import io
        import contextlib
        lease = self.claim(now=time.time())
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            q.main(['release', '--root', str(self.queue), '--job-id', self.packet['job_id'], '--token', lease['token'], '--reason', 'cli'])
        self.assertEqual(json.loads(buffer.getvalue())['status'], 'pending')


class FetchEvidenceTests(QueueCase):
    URL = 'https://leg.example.gov/code/title-5'
    SENTENCE = 'Invented official effective year 2001 for this section of the code'

    def setUp(self):
        super().setUp()
        self.lease = self.claim(now=time.time() - 50)
        self.web = FakeWeb(pages={self.URL: page(self.SENTENCE)})

    def fetch(self, url=None, web=None, now=None):
        now = time.time() if now is None else now
        with patch.object(ef, 'http_get', web or self.web):
            return ef.fetch_evidence(self.queue, self.packet['job_id'], self.lease['token'], url or self.URL, now=now)

    def test_claim_marks_a_history_packet_with_the_evidence_rule(self):
        packet = self.queue / 'in_progress' / self.packet['job_id']
        self.assertEqual(read(packet / 'EVIDENCE_RULE.json')['rule'], 'NAMED_PANEL_Q3')

    def test_live_fetch_saves_the_bytes_hashes_them_and_logs_the_provenance(self):
        entry = self.fetch()
        raw = page(self.SENTENCE)
        digest = hashlib.sha256(raw).hexdigest()
        self.assertEqual((entry['sha256'], entry['via'], entry['url'], entry['final_url']), (digest, 'live', self.URL, self.URL))
        saved = Path(self.job.output_dir) / entry['archive_path']
        self.assertEqual(saved.read_bytes(), raw)
        self.assertEqual(entry['archive_path'], 'evidence/' + digest + '.html')
        self.assertEqual(entry['size_bytes'], len(raw))
        self.assertTrue(entry['fetched_at_utc'].endswith('Z'))
        log = read(self.queue / 'in_progress' / self.packet['job_id'] / 'EVIDENCE_LOG.json')
        self.assertEqual([e['sha256'] for e in log['entries']], [digest])

    def test_blocked_live_page_falls_back_to_the_wayback_snapshot_and_says_so(self):
        web = FakeWeb(wayback={self.URL: page(self.SENTENCE)}, blocked={self.URL})
        entry = self.fetch(web=web)
        self.assertEqual(entry['via'], 'wayback')
        self.assertEqual(entry['snapshot_timestamp'], '20240102030405')
        self.assertIn('403', entry['live_error'])
        self.assertEqual(entry['url'], self.URL)
        self.assertTrue(any(c.startswith('https://web.archive.org/web/2id_/') or c.startswith('https://web.archive.org/web/20240102030405id_/') for c in web.calls))

    def test_rate_limit_gets_one_retry_only_except_on_archive_org(self):
        calls = []

        def get(url, *args, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise ef.FetchError('HTTP 429')
            return dict(status=200, final_url=url, content_type='text/html', charset='utf-8', data=page(self.SENTENCE))
        with patch.object(ef, 'PAUSE', 0):
            entry = self.fetch(web=get)
        self.assertEqual((len(calls), entry['via']), (2, 'live'))

        def always(url, *args, **kwargs):
            calls.append(url)
            raise ef.FetchError('HTTP 429')
        calls.clear()
        with patch.object(ef, 'PAUSE', 0), patch.object(ef, 'WAYBACK_BACKOFF', (0, 0)), patch.object(ef, 'JITTER', 0), self.assertRaises(ef.FetchError):
            self.fetch(web=always)
        self.assertEqual(len(calls), 5)  # live twice, Wayback availability three times (worker-fix G3: archive.org gets three tries)

    def test_no_live_page_and_no_snapshot_is_a_plain_error_and_writes_nothing(self):
        with self.assertRaisesRegex(ef.FetchError, 'no Wayback snapshot'):
            self.fetch(web=FakeWeb(blocked={self.URL}))
        self.assertFalse((Path(self.job.output_dir) / 'evidence').exists())

    def test_command_is_bound_to_the_lease_and_the_url_rules(self):
        with self.assertRaises(ValueError):
            ef.fetch_evidence(self.queue, self.packet['job_id'], 'bad-token', self.URL, now=time.time())
        for bad in ('ftp://x.gov/a', 'https://u:p@x.gov/a', 'http://localhost/x'):
            with self.subTest(bad), self.assertRaises(ef.FetchError):
                self.fetch(url=bad)
        # lease expired: no fetch
        with patch.object(ef, 'http_get', self.web), self.assertRaisesRegex(ValueError, 'Stale or expired'):
            ef.fetch_evidence(self.queue, self.packet['job_id'], self.lease['token'], self.URL, now=time.time() + q.LEASE_SECONDS + 5)

    def test_fetch_refreshes_the_lease_and_cli_prints_hash_and_path(self):
        import io
        import contextlib
        buffer = io.StringIO()
        with patch.object(ef, 'http_get', self.web), contextlib.redirect_stdout(buffer):
            q.main(['fetch-evidence', '--root', str(self.queue), '--job-id', self.packet['job_id'], '--token', self.lease['token'], '--url', self.URL])
        out = json.loads(buffer.getvalue())
        self.assertEqual(len(out['sha256']), 64)
        self.assertTrue(out['archive_path'].startswith('evidence/'))
        lease = read(self.queue / 'in_progress' / self.packet['job_id'] / 'LEASE.json')
        self.assertGreater(lease['heartbeat_at'], time.time() - 30)

    def test_verify_quote_command_reports_verified_not_found_and_short(self):
        entry = self.fetch()
        base = (self.queue, self.packet['job_id'], self.lease['token'], entry['sha256'])
        now = time.time()
        self.assertEqual(ef.verify_quote(*base, self.SENTENCE, now=now)['quote_check'], 'verified')
        self.assertEqual(ef.verify_quote(*base, self.SENTENCE.replace('2001', '2002'), now=now)['quote_check'], 'not_found')
        self.assertEqual(ef.verify_quote(*base, 'effective', now=now)['quote_check'], 'too_short')
        with self.assertRaisesRegex(ValueError, 'No fetched file'):
            ef.verify_quote(self.queue, self.packet['job_id'], self.lease['token'], '0' * 64, self.SENTENCE, now=now)

    def test_verify_quote_notices_changed_archive_bytes(self):
        entry = self.fetch()
        saved = Path(self.job.output_dir) / entry['archive_path']
        saved.chmod(0o644)
        saved.write_bytes(page('Something else entirely, written by hand into the archive.'))
        with self.assertRaisesRegex(ef.FetchError, 'changed after fetch'):
            ef.verify_quote(self.queue, self.packet['job_id'], self.lease['token'], entry['sha256'], self.SENTENCE, now=time.time())


class EvidenceRuleTests(QueueCase):
    """A row is known only with a fetched-and-hashed source whose quote verifies."""
    URL_A, URL_B, URL_C = 'https://leg.example.gov/code/a', 'https://ncsl.example.org/table', 'https://other.example.gov/c'

    def setUp(self):
        super().setUp()
        from scripts.l2_pipeline.agents.contract_tests import golden_output
        self.lease = self.claim(now=time.time() - 50)
        self.out = golden_output(self.job)
        for record in self.out['records'][12:]:
            record.update(status='never_treated', start_period=None, evidence_status='absent (V2_LIST)',
                          absence_basis=dict(official_code_history_no_earlier_enactment=True,
                                             no_earlier_in_compilations_on_disk=True, no_search_log_contradiction=True))
            record['evidence_refs'][0]['supports'] = 'absence_code_history'
            record.pop('best_evidence_start_year', None)
        self.quotes = sorted({r['quote'] for row in self.out['records'] for r in row.get('evidence_refs', [])})
        self.web = FakeWeb(pages={self.URL_A: page(*self.quotes), self.URL_B: page(*self.quotes), self.URL_C: page('Unrelated text that quotes none of the rows.')})

    def fetch(self, url):
        with patch.object(ef, 'http_get', self.web):
            return ef.fetch_evidence(self.queue, self.packet['job_id'], self.lease['token'], url, now=time.time())

    def source(self, sid, url, entry=None, kind='official'):
        row = dict(source_id=sid, url=url, source_type=kind, locator='x')
        if entry:
            row.update(sha256=entry['sha256'], archive_path=entry['archive_path'], final_url=entry['final_url'])
        else:
            row.update(provenance='citation_only', accessed='2026-10-16')
        return row

    def rewire(self, source_id='fixture-official'):
        for row in self.out['records']:
            row['source_id'] = source_id
            for ref in row.get('evidence_refs', []):
                ref['source_id'] = source_id

    def apply(self):
        return ef.apply_evidence_rule(self.job, self.out)

    def statuses(self, value):
        return [r['status'] for r in value['records']]

    def test_packet_without_the_rule_is_unchanged(self):
        (self.queue / 'in_progress' / self.packet['job_id'] / 'EVIDENCE_RULE.json').rename(self.root / 'moved_rule.json')
        value, report = self.apply()
        self.assertIsNone(report)
        self.assertEqual(value, self.out)

    def test_fetched_official_source_with_verified_quote_keeps_every_row_known(self):
        self.out['sources'] = [self.source('fixture-official', self.URL_A, self.fetch(self.URL_A))]
        value, report = self.apply()
        self.assertEqual(report['rows_downgraded'], 0)
        self.assertEqual(self.statuses(value), self.statuses(self.out))
        self.assertEqual(value['records'][0]['evidence_verification']['basis'], 'fetched_official_source_quote_verified')

    def test_unfetched_source_downgrades_known_rows_and_keeps_the_original(self):
        self.out['sources'] = [self.source('fixture-official', self.URL_A)]
        value, report = self.apply()
        known = [r for r in self.out['records'] if r['status'] != 'unknown']
        self.assertEqual(report['rows_downgraded'], len(known))
        row = value['records'][0]
        self.assertEqual((row['status'], row['start_period']), ('unknown', None))
        self.assertIn('fetched-and-hashed', row['reason'])
        self.assertEqual(row['evidence_unverified_original']['status'], 'treated')
        self.assertEqual(row['evidence_unverified_original']['start_period'], self.out['records'][0]['start_period'])

    def test_quote_missing_from_the_saved_bytes_downgrades(self):
        self.out['sources'] = [self.source('fixture-official', self.URL_C, self.fetch(self.URL_C))]
        value, report = self.apply()
        self.assertGreater(report['rows_downgraded'], 0)
        self.assertEqual(value['records'][0]['status'], 'unknown')

    def test_hash_not_in_the_fetch_log_or_wrong_url_is_citation_only(self):
        entry = self.fetch(self.URL_A)
        for change in (dict(sha256='f' * 64), dict(url='https://different.example.gov/x', final_url='')):
            with self.subTest(change):
                src = self.source('fixture-official', self.URL_A, entry)
                src.update(change)
                self.out['sources'] = [src]
                _, report = self.apply()
                self.assertGreater(report['rows_downgraded'], 0)

    def test_archive_bytes_changed_after_the_fetch_downgrades(self):
        entry = self.fetch(self.URL_A)
        self.out['sources'] = [self.source('fixture-official', self.URL_A, entry)]
        saved = Path(self.job.output_dir) / entry['archive_path']
        saved.chmod(0o644)
        saved.write_bytes(page(*self.quotes) + b'<!-- edited -->')
        _, report = self.apply()
        self.assertGreater(report['rows_downgraded'], 0)

    def citation_with_second(self, second_url, second_year_shift=0, second_type='secondary', same_year=True):
        """First source citation-only official; second source fetched (and its refs agree on the year, or not)."""
        entry = self.fetch(second_url)
        self.out['sources'] = [self.source('official-cite', self.URL_C), self.source('second', second_url, entry, kind=second_type)]
        for row in self.out['records']:
            if row['status'] == 'treated':
                first = row['evidence_refs'][0]
                row['source_id'] = 'official-cite'
                first['source_id'] = 'official-cite'
                row['evidence_refs'] = [first, dict(first, source_id='second', year=first['year'] + second_year_shift,
                                                    supports='start_year', locator='table 3')]

    def test_citation_only_official_row_is_known_with_a_second_independent_fetched_source(self):
        self.citation_with_second(self.URL_B)
        value, report = self.apply()
        self.assertEqual(report['rows_downgraded'], len([r for r in self.out['records'] if r['status'] == 'never_treated']))
        treated = [r for r in value['records'] if r['status'] == 'treated']
        self.assertTrue(treated)
        self.assertEqual(treated[0]['evidence_flags'], ['citation_only'])
        self.assertEqual(treated[0]['evidence_verification']['basis'], 'citation_only_with_second_fetched_source')

    def test_second_source_that_disagrees_on_the_year_does_not_confirm(self):
        self.citation_with_second(self.URL_B, second_year_shift=1)
        value, report = self.apply()
        self.assertTrue(all(r['status'] != 'treated' for r in value['records']))

    def test_second_source_must_be_a_different_document(self):
        entry = self.fetch(self.URL_A)
        self.out['sources'] = [self.source('official-cite', self.URL_A), self.source('second', self.URL_A, entry, kind='secondary')]
        for row in self.out['records']:
            if row['status'] == 'treated':
                first = row['evidence_refs'][0]
                row['source_id'] = first['source_id'] = 'official-cite'
                row['evidence_refs'] = [first, dict(first, source_id='second', supports='start_year')]
        value, report = self.apply()
        self.assertTrue(all(r['status'] != 'treated' for r in value['records']))

    def test_absence_needs_a_fetched_verified_official_source(self):
        self.out['sources'] = [self.source('fixture-official', self.URL_A)]
        value, _ = self.apply()
        absent = [r for r in self.out['records'] if r['status'] == 'never_treated']
        self.assertTrue(absent)
        self.assertTrue(all(r['status'] == 'unknown' for r in value['records'] if r['unit'] in {a['unit'] for a in absent}))

    def test_self_check_offers_one_correction_when_rows_would_become_unknown(self):
        self.out['sources'] = [self.source('fixture-official', self.URL_A)]
        out = Path(self.job.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.out))
        first = q.self_check(self.queue, self.packet['job_id'], self.lease['token'], now=120)
        self.assertIs(first['validated'], True)
        self.assertIs(first['correction_allowed'], True)
        self.assertGreater(first['evidence_report']['rows_downgraded'], 0)
        self.assertIs(q.self_check(self.queue, self.packet['job_id'], self.lease['token'], now=121)['correction_allowed'], True)
        entry = self.fetch(self.URL_A)
        self.out['sources'] = [self.source('fixture-official', self.URL_A, entry)]
        out.write_text(json.dumps(self.out))
        second = q.self_check(self.queue, self.packet['job_id'], self.lease['token'], now=130)
        self.assertEqual(second['evidence_report']['rows_downgraded'], 0)
        self.assertIs(second['correction_allowed'], False)

    def test_second_unrepaired_draft_is_finished_and_the_collector_records_unknown_rows(self):
        from scripts.l2_pipeline.autopilot import Backend
        self.out['sources'] = [self.source('fixture-official', self.URL_A)]
        out = Path(self.job.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.out))
        self.assertEqual(q.finish(self.queue, self.packet['job_id'], self.lease['token'], now=120)['status'], 'correction_required')
        out.write_text(json.dumps(dict(self.out, time_spent_seconds=self.out['time_spent_seconds'] + 1)))
        done = q.finish(self.queue, self.packet['job_id'], self.lease['token'], now=121)
        self.assertTrue(Path(done).is_relative_to(self.queue / 'done'))
        with patch.object(q, 'root_for', return_value=self.queue):
            receipt = Backend(self.ctx.repo).collect(self.job.job_path)
        verification = read(receipt['evidence_verification']['path'])
        self.assertGreater(verification['rows_downgraded'], 0)
        normalized = read(receipt['promoted_path'])
        self.assertEqual([r['status'] for r in normalized['records']].count('treated'), 0)
        self.assertEqual(read(self.job.output_path)['records'][0]['status'], 'treated')  # the original submission is untouched


# --------------------------------------------------------------------------------------------------
# Q2: LAUS employment-population ratio (catalog V17)
# --------------------------------------------------------------------------------------------------

FIPS = {'AL': '01', 'AK': '02', 'AZ': '04'}


def api_file(path, measure, values, months=None):
    """values: {(unit, year): monthly value}; months: {(unit, year): number of months present} (default 12)."""
    series = {}
    for (unit, year), value in sorted(values.items()):
        present = (months or {}).get((unit, year), 12)
        data = series.setdefault(unit, [])
        for month in range(1, present + 1):
            data.append(dict(year=str(year), period='M%02d' % month, periodName='m', value=str(value), footnotes=[{}]))
    body = dict(status='REQUEST_SUCCEEDED', message=[], Results=dict(series=[
        dict(seriesID='LAUST%s00000000000%s' % (FIPS[u], measure), data=d) for u, d in sorted(series.items())]))
    Path(path).write_text(json.dumps(body))
    return path


class RatioFixture(unittest.TestCase):
    YEARS = (2000, 2001)
    UNITS = ('AL', 'AK')

    def setUp(self):
        from scripts.l2_pipeline.connectors import outcome_v17 as v17
        self.v17 = v17
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.emp = {(u, y): 1000 * (1 + i) for i, u in enumerate(self.UNITS) for y in self.YEARS}
        self.pop = {(u, y): 2000 * (1 + i) for i, u in enumerate(self.UNITS) for y in self.YEARS}

    def write(self, emp_months=None, pop_months=None, pop_values=None, fallback=None):
        api_file(self.base / 'emp.json', '05', self.emp, emp_months)
        api_file(self.base / 'pop.json', '09', pop_values or self.pop, pop_months)
        names = ['emp.json', 'pop.json']
        if fallback is not None:
            (self.base / 'fallback.csv').write_text('unit,year,population,source\n' + ''.join(
                '%s,%d,%s,census_test\n' % (u, y, v) for (u, y), v in fallback.items()))
            names.append('fallback.csv')
        from scripts.l2_pipeline.connectors.common import sha
        self.spec = dict(series_id=self.v17.RATIO_ID, years=list(self.YEARS), numerator_paths=['emp.json'],
                         denominator_paths=['pop.json'], fallback_paths=['fallback.csv'] if fallback is not None else [])
        data = dict(series={self.v17.RATIO_ID: self.spec})
        return data, names, {n: sha(self.base / n) for n in names}

    def request(self, names, hashes, years=None, **options):
        from scripts.l2_pipeline.connectors.common import ConnectorRequest
        opts = dict(connector='outcome_v17_' + self.v17.RATIO_ID, series_id=self.v17.RATIO_ID, measure_id=self.v17.RATIO_ID,
                    paths=names, source_sha256=hashes, population_filters={}, frequency='annual', license='public', **options)
        return ConnectorRequest(opts, {}, tuple(years or self.YEARS), self.UNITS, 'annual', self.base)

    def construct(self, data, request):
        from scripts.l2_pipeline import connectors
        entry = dict(series_id=self.v17.RATIO_ID, fixed_population_filters={}, required_filters=(), allowed_causes=(),
                     allowed_age_groups=(), frequencies=('annual',))
        with patch.dict(connectors.CONNECTOR_CATALOG, {'outcome_v17_' + self.v17.RATIO_ID: entry}):
            return self.v17._construct(request, data)

    def values(self, panel):
        return {(r['unit'], r['year']): r for r in panel.rows}


class RatioConstructionTests(RatioFixture):
    def test_ratio_is_laus_employment_over_laus_population_in_percent(self):
        data, names, hashes = self.write()
        panel = self.construct(data, self.request(names, hashes))
        rows = self.values(panel)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(rows[('AL', 2000)]['outcome_value'], 100 * 1000 / 2000)
        self.assertAlmostEqual(rows[('AK', 2001)]['outcome_value'], 100 * 2000 / 4000)
        self.assertEqual({r['outcome_unit'] for r in panel.rows}, {'percent'})
        self.assertEqual({r['population_source'] for r in panel.rows}, {'laus'})
        self.assertEqual(panel.details['fallback_row_count'], 0)
        self.assertTrue(all(m['available'] == 1 and m['population_fallback'] == 0 for m in panel.metadata))

    def test_annual_values_are_twelve_month_means_of_each_series(self):
        data, names, hashes = self.write()
        api_file(self.base / 'emp.json', '05', {k: 1200 for k in self.emp})
        hashes['emp.json'] = __import__('hashlib').sha256((self.base / 'emp.json').read_bytes()).hexdigest()
        self.assertAlmostEqual(self.values(self.construct(data, self.request(names, hashes)))[('AL', 2000)]['outcome_value'], 60.0)

    def test_census_fallback_only_where_laus_lacks_the_population_and_it_is_flagged(self):
        data, names, hashes = self.write(pop_months={('AK', 2001): 11}, fallback={('AK', 2001): 5000, ('AL', 2000): 123})
        panel = self.construct(data, self.request(names, hashes))
        rows = self.values(panel)
        self.assertEqual(rows[('AK', 2001)]['population_source'], 'census_population_age16plus_fallback')
        self.assertAlmostEqual(rows[('AK', 2001)]['outcome_value'], 100 * 2000 / 5000)
        # a Census row for a state-year that LAUS covers never replaces the LAUS value
        self.assertEqual(rows[('AL', 2000)]['population_source'], 'laus')
        self.assertAlmostEqual(rows[('AL', 2000)]['outcome_value'], 50.0)
        flags = {(m['unit'], m['year']): m['population_fallback'] for m in panel.metadata}
        self.assertEqual(sum(flags.values()), 1)
        self.assertEqual(panel.details['fallback_rows'], [dict(unit='AK', year=2001, population=5000.0, source='census_test')])

    def test_without_a_fallback_a_missing_laus_population_is_unavailable_not_filled(self):
        data, names, hashes = self.write(pop_months={('AK', 2001): 11})
        panel = self.construct(data, self.request(names, hashes))
        row = self.values(panel)[('AK', 2001)]
        self.assertEqual((row['outcome_value'], row['population_source']), ('', 'unavailable'))
        self.assertEqual([m['available'] for m in panel.metadata if (m['unit'], m['year']) == ('AK', 2001)], [0])

    def test_incomplete_employment_year_is_unavailable(self):
        data, names, hashes = self.write(emp_months={('AL', 2000): 11}, fallback={('AL', 2000): 3000})
        row = self.values(self.construct(data, self.request(names, hashes)))[('AL', 2000)]
        self.assertEqual(row['outcome_value'], '')

    def test_card_must_bind_exactly_the_catalog_files_and_years_inside_coverage(self):
        data, names, hashes = self.write()
        from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady
        with self.assertRaises(ConnectorError):
            self.construct(data, self.request(names[:1], {k: v for k, v in hashes.items() if k in names[:1]}))
        with self.assertRaises(ConnectorNotReady):
            self.construct(data, self.request(names, hashes, years=(2000, 2001, 2002)))

    def test_changed_source_bytes_are_refused_by_the_bound_hash(self):
        data, names, hashes = self.write()
        (self.base / 'pop.json').write_text((self.base / 'pop.json').read_text() + ' ')
        from scripts.l2_pipeline.connectors.common import ConnectorError
        with self.assertRaises(ConnectorError):
            self.construct(data, self.request(names, hashes))


class PopulationDownloadPlanTests(unittest.TestCase):
    def setUp(self):
        from scripts.l2_pipeline.connectors import yield17_build as y17
        self.y17 = y17
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name)
        patcher = patch.object(y17, 'RAW', self.raw)
        patcher.start()
        self.addCleanup(patcher.stop)

    def put(self, name, units, first, last, measure='09'):
        fips = {u: c for c, u in self.y17.UNITS.items()}
        series = [dict(seriesID='LAUST%s00000000000%s' % (fips[u], measure),
                       data=[dict(year=str(y), period='M%02d' % m, value='1') for y in range(first, last + 1) for m in range(1, 13)])
                  for u in units]
        (self.raw / name).write_text(json.dumps(dict(status='REQUEST_SUCCEEDED', Results=dict(series=series))))

    def test_empty_disk_needs_fifteen_queries_in_the_fixed_windows(self):
        plan = self.y17.plan()
        self.assertEqual(plan['queries_needed'], 15)
        self.assertEqual(sorted(plan['missing']), ['1976-1985', '1986-1995', '1996-2005', '2006-2015', '2016-2025'])
        self.assertEqual([len(b) for b in self.y17.batches(self.y17.ALL_UNITS)], [25, 25, 1])

    def test_what_is_on_disk_is_not_asked_for_again(self):
        units = self.y17.ALL_UNITS
        self.put('bls_v1_bls_laus_population_2016_2025_0_a.json', units[:25], 2016, 2025)
        self.put('bls_v1_bls_laus_population_2016_2025_0_b.json', units[25:50], 2016, 2025)
        self.put('bls_v1_bls_laus_population_2016_2025_0_c.json', units[50:], 2016, 2025)
        self.put('bls_v1_bls_laus_population_1976_1985_0_d.json', units[:25], 1976, 1985)
        plan = self.y17.plan()
        self.assertEqual(plan['complete_windows'], [(2016, 2025)])
        self.assertEqual(len(plan['missing']['1976-1985']), 26)
        self.assertEqual(plan['queries_needed'], 2 + 3 * 3)  # 1976-85 needs 25+1, three more windows need 25+25+1

    def test_other_measures_and_partial_months_do_not_count(self):
        units = self.y17.ALL_UNITS
        self.put('bls_v1_bls_laus_population_2016_2025_0_a.json', units[:25], 2016, 2025, measure='05')
        with self.assertRaises(Exception):
            self.y17.coverage(sorted(self.raw.glob('*.json')))

    def test_download_runs_only_missing_batches_and_stops_quietly_at_the_daily_limit(self):
        calls = []

        class Client:
            retries = 0

            def fetch(self, url, destination, license, license_url, *, json_body=None):
                calls.append(dict(json_body))
                destination.write_text(json.dumps(dict(status='REQUEST_SUCCEEDED', Results=dict(series=[
                    dict(seriesID=i, data=[dict(year=str(y), period='M%02d' % m, value='1')
                                           for y in range(int(json_body['startyear']), int(json_body['endyear']) + 1)
                                           for m in range(1, 13)]) for i in json_body['seriesid']]))))
                return dict(path=str(destination))
        quota = self.raw / '.bls_v1_quota.json'
        today = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).date().isoformat()
        quota.write_text(json.dumps(dict(utc_date=today, reserved_queries=24, external_daily_queries=0)))
        result = self.y17.download(Client())
        # one query is left: the first batch of the first window runs, the second is refused quietly
        self.assertEqual(result['downloaded'], [dict(window=[1976, 1985], units=25)])
        self.assertIn('25query', result['stopped_by_quota'].replace(' ', ''))
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(quota.read_text())['reserved_queries'], 25)
        calls.clear()
        quota.write_text(json.dumps(dict(utc_date=today, reserved_queries=13, external_daily_queries=0)))
        # the first batch is on disk now; 12 queries are left: 1976-85 needs 2 more, three windows of 3, then one of 2016-25
        result = self.y17.download(Client())
        self.assertEqual([(c['startyear'], len(c['seriesid'])) for c in calls],
                         [('1976', 25), ('1976', 1)] + [(y, n) for y in ('1986', '1996', '2006') for n in (25, 25, 1)] + [('2016', 25)])
        self.assertEqual(len(calls), 12)
        self.assertIsNotNone(result['stopped_by_quota'])
        self.assertEqual(sorted(result['remaining']['missing']), ['2016-2025'])
        self.assertEqual(result['remaining']['queries_needed'], 2)


class CatalogV17DerivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.l2_pipeline.connectors import yield17_build as y17, yield16_build as y16
        cls.y17 = y17
        cls.v16 = json.loads(y16.V16.read_text())
        cls.p16 = json.loads(y16.P16.read_text())
        pointer = dict(path='m.json', sha256='0' * 64)
        pop = ['pop_a.json', 'pop_b.json']
        cls.v17, cls.p17 = y17.derive_catalog(cls.v16, cls.p16, pop_rel=pop, pop_hash={p: '1' * 64 for p in pop},
            pop_years=list(range(1976, 2026)), ratio_years=list(range(1976, 2026)), fallback_rel=[], fallback_hash={},
            pointer=pointer, v16_ref=dict(path='v16', sha256='2' * 64), p16_ref=dict(path='p16', sha256='3' * 64),
            lineage=dict(previous='x', ruling='y'))

    def test_every_other_series_is_v16_byte_for_byte_apart_from_the_connector_name(self):
        new = {self.y17.POPULATION_ID, self.y17.RATIO_ID}
        self.assertEqual(set(self.v17['series']) - set(self.v16['series']), new)
        for sid, spec in self.v16['series'].items():
            self.assertEqual(dict(self.v17['series'][sid], registered_connector=None), dict(spec, registered_connector=None), sid)
            self.assertEqual(self.v17['series'][sid]['registered_connector'], 'outcome_v17_' + sid)

    def test_top_level_and_targets_change_only_as_declared(self):
        changed = {k for k in self.v17 if k != 'series' and self.v17[k] != self.v16.get(k)}
        self.assertEqual(changed, {'catalog_id', 'schema_version', 'inherited_catalog', 'registered_entrypoint', 'registration_entrypoint',
                                   'version_lineage', 'targets'})
        for name, target in self.v17['targets'].items():
            old = self.v16['targets'][name]
            if self.y17.RATIO_ID in target['series']:
                self.assertEqual(name, 'state_labor_market')
                self.assertEqual([s for s in target['series'] if s != self.y17.RATIO_ID], old['series'])
            else:
                self.assertEqual(target, old)

    def test_new_series_definitions(self):
        pop = self.v17['series'][self.y17.POPULATION_ID]
        ratio = self.v17['series'][self.y17.RATIO_ID]
        self.assertEqual((pop['bls_measure'], pop['units'], pop['adapter']), ('09', 'persons', 'labor'))
        self.assertEqual((ratio['units'], ratio['frequency'], ratio['numerator_series'], ratio['denominator_series']),
                         ('percent', ['annual'], 'bls_laus_employment', self.y17.POPULATION_ID))
        self.assertEqual(ratio['numerator_paths'], self.v16['series']['bls_laus_employment']['paths'])
        self.assertEqual(set(ratio['paths']), set(ratio['numerator_paths']) | set(ratio['denominator_paths']))
        self.assertEqual(set(ratio['paths']), set(ratio['source_sha256']))
        self.assertEqual(ratio['fallback_paths'], [])

    def test_population_metadata_changes_only_the_new_series_and_the_preferred_series(self):
        self.assertEqual(set(self.p17['series']) - set(self.p16['series']), {self.y17.RATIO_ID})
        for sid, meta in self.p16['series'].items():
            self.assertEqual(self.p17['series'][sid], meta)
        for old, new in zip(self.p16['targets'], self.p17['targets']):
            if old['construct'] == 'employment_status':
                self.assertEqual(new, dict(old, preferred_series=self.y17.RATIO_ID))
            else:
                self.assertEqual(new, old)

    def test_gate0_series_choice_moves_from_the_count_to_the_ratio(self):
        from scripts.l2_pipeline import population_resolution as resolution
        card = dict(policy_family='labor_market')
        purpose = dict(status='VERIFIED', target_outcome='employment_status', target_construct='employment_status')
        before = resolution.choose(card, purpose, self.v16, self.p16)
        after = resolution.choose(card, purpose, self.v17, self.p17)
        self.assertEqual(before['selected']['series_id'], 'bls_laus_employment')
        self.assertEqual(after['selected']['series_id'], self.y17.RATIO_ID)

    def test_build_refuses_while_laus_population_is_incomplete(self):
        from scripts.l2_pipeline.connectors.common import ConnectorError
        with patch.object(self.y17, 'population_files', return_value=[]), self.assertRaisesRegex(ConnectorError, 'LAUS population missing'):
            self.y17.build()

    def test_v19_is_the_current_catalog_after_the_window_2026_10_23_switch_v17_stays_registered(self):
        # Window 2026-10-17 made V17 current (all 51 jurisdictions of LAUS population bound); window 2026-10-23 moved the pointer to V19.
        from scripts.l2_pipeline.connectors import outcome_v17, population_v63
        self.assertTrue(outcome_v17.CATALOG_PATH.is_file())
        self.assertEqual(population_v63.CURRENT_PATH.name, 'OUTCOME_POPULATIONS_V19.json')
        self.assertEqual(json.loads(population_v63.CURRENT_PATH.read_text())['catalog_id'], 'first_order_outcomes_v19')
        population_v63.current_catalog()  # builds without error from the V19 files


# --------------------------------------------------------------------------------------------------
# Q1: structural exclusions on S3-stopped cards
# --------------------------------------------------------------------------------------------------

def q1_fixture():
    """Window 2000-2008, three cohorts of treated states, 6 never-treated states; one state each for the three reasons."""
    records = [dict(unit='B0', status='treated', start_period=1995),            # adopted before the window
               dict(unit='B1', status='treated', start_period=2001),            # one pre-period only
               dict(unit='B2', status='treated', start_period=2008)]            # one post-period only (operation-year drop)
    records += [dict(unit='T%d' % i, status='treated', start_period=[2004, 2005, 2006][i % 3]) for i in range(12)]
    records += [dict(unit='C%d' % i, status='never_treated', start_period=None) for i in range(7)]
    card = dict(design_id='q1_invented', version='v3', track='DiD',
                analysis=dict(min_pre_periods=3, min_controls=5, min_cohorts=2, min_treated=6, min_post_periods=2),
                treatment=dict(), outcome=dict(frequency='annual', years=[2000, 2008]), sensitivities=[],
                transition_year_drop=True)
    series = [r['unit'] for r in records if r['unit'] != 'C6']                      # C6 is not in the outcome series
    return records, card, series


def write_metadata(path, units, years, drop=(), flag=()):
    import csv
    with Path(path).open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['unit', 'year', 'available', 'annual_usable', 'linkage_certified', 'suppression_flag'])
        writer.writeheader()
        for unit in units:
            for year in years:
                if (unit, year) in drop:
                    continue
                writer.writerow(dict(unit=unit, year=year, available=1, annual_usable=1, linkage_certified=1,
                                     suppression_flag=1 if (unit, year) in flag else 0))
    return Path(path)


class StructuralExclusionTests(unittest.TestCase):
    def test_three_structural_reasons_and_nothing_else(self):
        from scripts.l2_pipeline import named_panel as n
        records, card, series = q1_fixture()
        excluded, details = n.structural_exclusions(card, records, series)
        self.assertEqual({u: v['reason'] for u, v in excluded.items()},
                         {'B0': 'adopted_before_window', 'B1': 'too_few_pre_periods', 'B2': 'too_few_post_periods',
                          'C6': 'absent_from_outcome_series'})
        self.assertEqual(details['calendar'], [2000, 2008])
        self.assertTrue(details['transition_year_drop'])

    def test_suppressed_unreliable_or_missing_cells_never_remove_a_state(self):
        from scripts.l2_pipeline import named_panel as n
        records, card, series = q1_fixture()
        # The structural list is computed with every cell present, so data flags cannot enter it ...
        base, _ = n.structural_exclusions(card, records, series)
        with tempfile.TemporaryDirectory() as tmp:
            years = list(range(2000, 2009))
            meta = write_metadata(Path(tmp) / 'metadata.csv', series, years, drop={('T1', 2005)}, flag={('T2', 2003), ('C1', 2007)})
            # ... and the unchanged S3 replay, which does see them, only reports them as issues.
            replay = n.s3_replay(card, records, sorted(base), meta)
        self.assertNotIn('T1', base)
        self.assertNotIn('T2', base)
        self.assertNotIn('C1', base)
        self.assertEqual(replay['support_gap_excluded_units'], [])

    def test_both_the_card_and_the_replay_apply_the_same_exclusion_list(self):
        from scripts.l2_pipeline import named_panel as n
        records, card, series = q1_fixture()
        excluded, _ = n.structural_exclusions(card, records, series)
        with tempfile.TemporaryDirectory() as tmp:
            meta = write_metadata(Path(tmp) / 'metadata.csv', series, list(range(2000, 2009)))
            before = n.s3_replay(card, records, [], meta)
            after = n.s3_replay(card, records, sorted(excluded), meta)
        self.assertFalse(before['passed'])
        self.assertTrue(after['passed'], after['issues'])
        self.assertEqual(len(after['retained_treated']), 12)


class FloorTests(unittest.TestCase):
    def roster(self, treated, cohorts=3, controls=5):
        rows = [dict(unit='T%d' % i, status='treated', start_period=2000 + i % cohorts) for i in range(treated)]
        return rows + [dict(unit='C%d' % i, status='never_treated', start_period=None) for i in range(controls)]

    def check(self, records, excluded, sample=None, controls=None):
        from scripts.l2_pipeline import named_panel as n
        sample = sample if sample is not None else ['T%d' % i for i in range(10)]
        return n.check_floors(records, set(excluded), sample, ['C%d' % i for i in range(5)] if controls is None else controls)

    def test_one_third_removed_is_allowed_and_one_more_is_not(self):
        records = self.roster(30)
        self.assertEqual(self.check(records, ['T%d' % i for i in range(10, 20)])[1], [])
        failures = self.check(records, ['T%d' % i for i in range(10, 21)])[1]
        self.assertEqual(failures, ['more than 1/3 of the registered treated states removed'])

    def test_ten_treated_three_cohorts_five_controls_and_five_audited(self):
        self.assertEqual(self.check(self.roster(10), [])[1], [])
        self.assertIn('fewer than 10 treated states retained', self.check(self.roster(9), [])[1])
        self.assertIn('fewer than 3 cohorts retained', self.check(self.roster(12, cohorts=2), [])[1])
        self.assertIn('fewer than 5 control states', self.check(self.roster(12), [], controls=['C0', 'C1', 'C2', 'C3'])[1])
        self.assertEqual(self.check(self.roster(30), ['T0', 'T1', 'T2', 'T3', 'T4'])[1], [])
        self.assertIn('fewer than 5 of the audited states retained', self.check(self.roster(30), ['T0', 'T1', 'T2', 'T3', 'T4', 'T5'])[1])


class ScriptedCardTests(unittest.TestCase):
    def test_card_changes_only_the_exclusion_list_the_sensitivities_and_the_record(self):
        from scripts.l2_pipeline import named_panel as n
        _, card, _ = q1_fixture()
        card['treatment'] = dict(excluded_units=['ZZ'], predicate='p')
        card['sensitivities'] = [dict(name='omit_T1', changes=dict(treatment=dict(excluded_units=['ZZ', 'T1']))),
                                 dict(name='other', changes=dict(analysis=dict(min_controls=6)))]
        plan = dict(excluded={'B0': dict(reason='adopted_before_window'), 'B1': dict(reason='too_few_pre_periods')},
                    figures=dict(removed_treated=2, registered_treated=20))
        new = n.build_card(card, plan, 'v4', dict(path='r', sha256='1'), dict(path='c', sha256='2'))
        self.assertEqual(new['version'], 'v4')
        self.assertEqual(new['treatment'], dict(excluded_units=['B0', 'B1', 'ZZ'], predicate='p'))
        self.assertEqual(new['sensitivities'][0]['changes']['treatment']['excluded_units'], ['B0', 'B1', 'T1', 'ZZ'])
        self.assertEqual(new['sensitivities'][1], card['sensitivities'][1])
        self.assertEqual(card['version'], 'v3')  # the input is untouched
        record = new['automation']['named_panel_q1']
        self.assertEqual(record['statement'], 'added after the S3 stop, before any forecast or outcome calculation')
        self.assertEqual((record['route'], record['k_of_n']), ('post_stop_structural', '2 of 20 registered treated states'))
        self.assertNotIn('pre-regist', json.dumps(record).lower())
        rest = {k: v for k, v in new.items() if k not in ('version', 'treatment', 'sensitivities', 'automation')}
        self.assertEqual(rest, {k: v for k, v in card.items() if k not in ('version', 'treatment', 'sensitivities', 'automation')})
        self.assertNotIn('support_gap_rule', new['analysis'])


class Q1RequeueTests(unittest.TestCase):
    """The existing requeue route with the new superseded status; no history restart for an exclusion-only change."""

    def setUp(self):
        from scripts.l2_pipeline import named_panel as n
        from scripts.l2_pipeline.autopilot import Autopilot, Backend
        self.n = n
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.registry = self.repo / 'data/l2_pipeline/registry'
        self.pilot = Autopilot(self.registry, Backend(self.repo))
        original = read(Path(__file__).parents[1] / 'examples/did_card.json')
        if original is None:  # same fallback as test_c46.RequeueTests
            original = read(Path(__file__).resolve().parents[3] / 'data/l2_pipeline/intake/bulk_20261010/health/cards/bulk10_health_medicaid_sud_imd_waiver.json')
        self.card = deepcopy(original)
        self.card.update(design_id='q1-fixture', version='v3')
        self.card['treatment'] = dict(self.card.get('treatment', {}), excluded_units=[])
        self.old = self.repo / 'data/l2_pipeline/cards/old.json'
        atomic(self.old, self.card)
        self.row = dict(design_id='q1-fixture', attempt_id='requeue_v3', card=str(self.old), card_sha256=sha(self.old),
                        work_dir=str(self.repo / 'data/l2_pipeline/work'), route='V9')
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[self.row]))
        (self.registry / 'AUTOPILOT_STOP').touch()
        self.pilot.ledger({}, 'loop', 'CLEAN_STOP', 'record')
        case = self.pilot.case_dir(self.row)
        ensure_once(case / 'PARKED.json', dict(design_id='q1-fixture', attempt_id='requeue_v3', action='S3', status='PARKED', reason=n.PARK_REASON))
        for action in ('history', 'reconcile', 'audit'):
            ensure_once(self.pilot.checkpoint(self.row, action), dict(action=action))
        root = self.pilot.backend.root(self.row)
        for name in ('RECONCILIATION.json', 'CODING_LOCK.json', 'AUDIT_SELECTION.json'):
            ensure_once(root / name, dict(fixture=name))
        docs = self.repo / 'docs/plans/l2-pipeline'
        docs.mkdir(parents=True)
        (docs / 'HUB_RULING_20261016_NAMED_PANEL.md').write_text('fixture ruling text')
        self.plan = dict(design_id='q1-fixture', attempt_id='requeue_v3', card_version='v3', decision='requeue', stay_stopped_because=[],
                         excluded={'AL': dict(reason='adopted_before_window', status='treated', start_period=1990)},
                         figures=dict(registered_treated=12, removed_treated=1, retained_treated=11, retained_cohorts=4,
                                      retained_controls=9, audited_retained=9, audited_total=10),
                         details={}, baseline_replay_reproduces_stored_s3=True, replay_passed=True, replay_issues={},
                         replay_retained_treated=11, outcome_values_read=0)
        patcher = patch.multiple(n, plan_design=lambda pilot, row: deepcopy(self.plan), plan_all=lambda pilot: [deepcopy(self.plan)])
        patcher.start()
        self.addCleanup(patcher.stop)

    def files(self):
        out = self.repo / 'data/l2_pipeline/autopilot/named_panel/q1'
        class_ref = ensure_once(out / 'CLASS_RULING.json', self.n.class_ruling([deepcopy(self.plan)]))
        authority = self.repo / self.n.RULING
        ruling_ref, class_ref = self.n.bind(authority), self.n.bind(class_ref)
        card = ensure_once(out / 'cards/q1-fixture/CARD.json', self.n.build_card_rebased(read(self.old), self.old, self.plan, 'v4', ruling_ref, class_ref))
        value = dict(schema='l2_requeue_ruling_v1', design_id='q1-fixture', prior_card_sha256=sha(self.old), card_sha256=sha(card),
                     decision='accept', purpose_rule='V6.2', implementation_rule='NAMED_PANEL_Q1',
                     superseded_status=self.n.SUPERSEDED, impact_tags=['s3_support'], authority=ruling_ref, class_ruling=class_ref,
                     reason='fixture', execution_authorized=False)
        ruling = ensure_once(out / 'rulings/q1-fixture.json', value)
        return card, ruling, value, out

    def test_exclusion_only_card_keeps_history_and_restarts_at_purpose_direction(self):
        from scripts.l2_pipeline.requeue import requeue
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            card, ruling, _, _ = self.files()
            receipt = requeue('q1-fixture', card, ruling, self.pilot)
        self.assertEqual(receipt['restart_action'], 'purpose-direction')
        self.assertEqual(receipt['retained_actions'], ['history', 'reconcile', 'audit'])
        new = read(self.registry / 'ACTIVE_DESIGNS.json')['designs'][0]
        self.assertEqual((new['attempt_id'], read(new['card'])['treatment']['excluded_units']), ('requeue_v4', ['AL']))
        self.assertEqual(self.pilot.next_action(new), 'purpose-direction')
        self.assertTrue((self.pilot.case_dir(self.row) / 'PARKED.json').exists())  # the old attempt's park is kept as evidence

    def test_the_same_card_change_without_the_rule_would_restart_history(self):
        from scripts.l2_pipeline.requeue import requeue
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            card, ruling, value, out = self.files()
        plain = dict(value, decision='resume', implementation_rule=None, superseded_status='SUPERSEDED_C85')
        plain.pop('impact_tags')
        plain['authority'] = self.n.bind(self.repo / self.n.RULING)
        plain_ruling = ensure_once(out / 'rulings/plain.json', plain)
        self.assertEqual(requeue('q1-fixture', card, plain_ruling, self.pilot)['restart_action'], 'history')

    def test_status_is_whitelisted_and_the_hub_queue_entry_gets_it(self):
        from scripts.l2_pipeline.requeue import requeue
        atomic(self.registry / 'HUB_QUEUE.json', dict(entries=[dict(design_id='q1-fixture', attempt_id='requeue_v3', status='NEEDS_RULING')]))
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            card, ruling, _, _ = self.files()
            requeue('q1-fixture', card, ruling, self.pilot)
        self.assertEqual(read(self.registry / 'HUB_QUEUE.json')['entries'][0]['status'], 'SUPERSEDED_NAMED_PANEL_Q1')

    def test_card_that_differs_from_the_scripted_card_is_refused(self):
        from scripts.l2_pipeline.requeue import requeue
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            card, ruling, value, out = self.files()
            tampered = read(card)
            tampered['analysis'] = dict(tampered['analysis'], min_controls=1)
            bad_card = ensure_once(out / 'cards/q1-fixture/TAMPERED.json', tampered)
            bad = ensure_once(out / 'rulings/tampered.json', dict(value, card_sha256=sha(bad_card)))
            with self.assertRaisesRegex(ValueError, 'differs from the scripted card'):
                requeue('q1-fixture', bad_card, bad, self.pilot)
            more = read(card)
            more['treatment'] = dict(more['treatment'], excluded_units=['AL', 'AK'])
            wider_card = ensure_once(out / 'cards/q1-fixture/WIDER.json', more)
            wider = ensure_once(out / 'rulings/wider.json', dict(value, card_sha256=sha(wider_card)))
            with self.assertRaisesRegex(ValueError, 'differs from the scripted card'):
                requeue('q1-fixture', wider_card, wider, self.pilot)
        self.assertEqual(read(self.registry / 'ACTIVE_DESIGNS.json')['designs'], [self.row])

    def test_design_whose_floors_or_replay_fail_is_refused(self):
        from scripts.l2_pipeline.requeue import requeue
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]):
            card, ruling, _, _ = self.files()
            self.plan.update(decision='stay_stopped', stay_stopped_because=['fewer than 5 control states'])
            with self.assertRaisesRegex(ValueError, 'floors or replay do not pass'):
                requeue('q1-fixture', card, ruling, self.pilot)

    def test_design_outside_the_class_is_refused(self):
        from scripts.l2_pipeline.requeue import requeue
        with patch.object(self.n, 's3_stopped_rows', return_value=[]):
            card, ruling, _, _ = self.files()
            with self.assertRaisesRegex(ValueError, 'not an unrescued S3-stopped card'):
                requeue('q1-fixture', card, ruling, self.pilot)

    def test_apply_records_the_ruling_in_the_ledger_before_any_requeue_and_is_idempotent(self):
        atomic(self.registry / 'CODE_TASK.lock', dict(task='NAMED_PANEL', token='t'))
        stay = dict(deepcopy(self.plan), design_id='other', decision='stay_stopped', stay_stopped_because=['no structural exclusion applies'],
                    excluded={}, figures=dict(self.plan['figures'], removed_treated=0))
        with patch.object(self.n, 's3_stopped_rows', return_value=[self.row]), \
                patch.object(self.n, 'plan_all', lambda pilot: [deepcopy(self.plan), deepcopy(stay)]), \
                patch('scripts.l2_pipeline.ops.watchdog.clean_stop', lambda repo: None), \
                patch('scripts.l2_pipeline.c70_l3.protected', lambda root: False):
            by_id = read(self.registry / 'ACTIVE_DESIGNS.json')
            by_id['designs'].append(dict(self.row, design_id='other'))
            atomic(self.registry / 'ACTIVE_DESIGNS.json', by_id)
            result = self.n.apply(self.pilot)
            again = self.n.apply(self.pilot)
        self.assertEqual(result, again)
        self.assertEqual([r['design_id'] for r in result['requeued']], ['q1-fixture'])
        self.assertEqual(result['stayed_stopped'], ['other'])
        events = [json.loads(line) for line in (self.registry / 'AUTOPILOT_LEDGER.jsonl').read_text().splitlines()]
        names = [(e['action'], e['decision']) for e in events]
        self.assertLess(names.index(('named_panel_q1', 'RULING_RECORDED')), names.index(('requeue', 'SUPERSEDED_NAMED_PANEL_Q1')))
        recorded = next(e for e in events if e['decision'] == 'RULING_RECORDED')
        self.assertEqual(recorded['ruling']['sha256'], sha(self.repo / self.n.RULING))
        out = self.repo / 'data/l2_pipeline/autopilot/named_panel/q1'
        self.assertEqual(recorded['class_ruling']['sha256'], sha(out / 'CLASS_RULING.json'))
        export = read(out / 'exports/q1-fixture.json')
        self.assertEqual((export['k_of_n'], export['requeued'], export['route']), ('1 of 12 registered treated states', True, 'post_stop_structural'))
        self.assertEqual(export['excluded_states'], [dict(unit='AL', reason='adopted_before_window', status='treated', start_period=1990)])
        self.assertFalse(read(out / 'exports/other.json')['requeued'])

    def test_recover_clears_only_the_exact_c42_stop_of_a_q1_attempt_and_is_idempotent(self):
        atomic(self.registry / 'CODE_TASK.lock', dict(task='NAMED_PANEL_FIX', token='t'))
        card = read(self.old)
        card['automation'] = dict(card.get('automation') or {}, named_panel_q1=dict(excluded_units={'AL': 'adopted_before_window'}))
        atomic(self.old, card)
        row = dict(self.row, card_sha256=sha(self.old))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        (self.pilot.case_dir(row) / 'PARKED.json').unlink()
        self.pilot.stop(row, 'inspect', self.n.C42_STOP)
        self.assertEqual(self.pilot.next_action(row), 'STOP')
        self.assertEqual(self.n.recover_c42_stops(self.pilot), ['q1-fixture'])
        self.assertIsNone(self.pilot.stop_record(row))
        self.assertEqual(self.n.recover_c42_stops(self.pilot), [])
        entry = read(self.registry / 'HUB_QUEUE.json')['entries'][0]
        self.assertEqual(entry['status'], 'SUPERSEDED_C62')

    def test_a_second_stop_of_the_same_attempt_is_cleared_through_the_next_slot(self):
        atomic(self.registry / 'CODE_TASK.lock', dict(task='NAMED_PANEL_FIX2', token='t'))
        card = read(self.old)
        card['automation'] = dict(card.get('automation') or {}, named_panel_q1=dict(excluded_units={'AL': 'adopted_before_window'}))
        atomic(self.old, card)
        row = dict(self.row, card_sha256=sha(self.old))
        atomic(self.registry / 'ACTIVE_DESIGNS.json', dict(designs=[row]))
        (self.pilot.case_dir(row) / 'PARKED.json').unlink()
        self.pilot.stop(row, 'inspect', self.n.C42_STOP)
        self.assertEqual(self.n.recover_c42_stops(self.pilot), ['q1-fixture'])
        self.pilot.stop(row, 'S4', self.n.C64_STOP)
        self.assertEqual(self.pilot.next_action(row), 'STOP')
        self.assertEqual(self.n.recover_c42_stops(self.pilot), ['q1-fixture'])
        self.assertIsNone(self.pilot.stop_record(row))
        self.assertEqual(self.n.recover_c42_stops(self.pilot), [])

    def test_recover_leaves_other_stops_alone(self):
        atomic(self.registry / 'CODE_TASK.lock', dict(task='NAMED_PANEL_FIX', token='t'))
        (self.pilot.case_dir(self.row) / 'PARKED.json').unlink()
        self.pilot.stop(self.row, 'inspect', 'ValueError: something else')
        self.assertEqual(self.n.recover_c42_stops(self.pilot), [])
        self.assertIsNotNone(self.pilot.stop_record(self.row))


class C42OverlayWithQ1Tests(unittest.TestCase):
    """Found at the first application: a retained C42 overlay does not list the added exclusions."""

    def fixture(self, authorized):
        from scripts.l2_pipeline import c42
        from scripts.l2_pipeline.contracts import DesignCard, StageContext
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        _, base, _ = q1_fixture()
        base.update(policy_family='f', mechanism='m', unit='state', outcome_family='o')
        base['treatment'] = dict(predicate='p', universe=['A', 'B', 'C'], excluded_units=['A'])
        old = dict(base)
        (root / 'card.json').write_text(json.dumps(dict(base, treatment=dict(base['treatment'], excluded_units=['A', 'B']),
                                                        **({'automation': {'named_panel_q1': {'excluded_units': {'B': 'x'}}}} if authorized else {}))))
        design = root / 'design'
        design.mkdir()
        overlay = dict(base['treatment'], excluded_units=['A'], excluded_conflict_states=['A'], excluded_conflict_state_reasons={'A': ['x']})
        evidence = []
        payload = json.loads((root / 'card.json').read_text())
        ctx = StageContext(DesignCard.from_mapping(payload, root / 'card.json'), design, Path(__file__).resolve().parents[3])
        c42.register_exclusions(ctx, overlay, evidence)
        return c42, ctx

    def test_added_exclusions_listed_by_the_q1_record_are_accepted(self):
        c42, ctx = self.fixture(True)
        # register_exclusions binds the card hash; the overlay lists only the old exclusions
        got = c42.effective_context(ctx)
        self.assertEqual(got.card.payload['treatment']['excluded_units'], ['A', 'B'])

    def test_the_same_card_without_the_q1_record_is_still_refused(self):
        c42, ctx = self.fixture(False)
        with self.assertRaisesRegex(ValueError, 'C42 overlay removed existing exclusions'):
            c42.effective_context(ctx)


class LegacyPurposeDirectionPlaceholderTests(unittest.TestCase):
    """Found when the first Q1 design reached S4: a card with an unconfirmed researcher placeholder (direction 0)."""

    def contract(self, first):
        from scripts.l2_pipeline import c64, c64_metadata
        with patch.multiple(c64_metadata, export_contract=lambda card, repo=None: dict(geography={}),
                            geography=lambda card, treatment: {}):
            return c64.contract(dict(first_order=first), Path('.'), None)

    def test_unconfirmed_researcher_placeholder_is_a_missing_coding_not_zero(self):
        value = self.contract(dict(purpose_direction=0, purpose_direction_evidence=None,
                                   purpose_direction_status='researcher_proposal_pending_two_blind_coders_at_S0; statute text only'))
        self.assertIsNone(value['first_order']['purpose_direction'])
        self.assertEqual(value['purpose_direction_status'], 'FIELD_MISSING')

    def test_any_other_direction_without_evidence_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, 'requires S0 evidence'):
            self.contract(dict(purpose_direction=0, purpose_direction_evidence=None, purpose_direction_status='coded'))
        with self.assertRaisesRegex(ValueError, 'requires S0 evidence'):
            self.contract(dict(purpose_direction=-1, purpose_direction_evidence=None))


for _cls in (DispatcherTests, FetchEvidenceTests, EvidenceRuleTests, QueueCase):
    for _name in dir(OfficialBridgeTests):
        if _name.startswith('test_') and _name not in _cls.__dict__:
            setattr(_cls, _name, None)
del OfficialBridgeTests, _cls, _name


if __name__ == '__main__':
    unittest.main()
