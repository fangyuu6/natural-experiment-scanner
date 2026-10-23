"""Staged 2026-10-19: hub import of normal-browser downloads into a history packet's evidence archive.
Synthetic data only; no network; temporary directories only."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents import browser_evidence as be
from scripts.l2_pipeline.agents import evidence_fetch as ef
from scripts.l2_pipeline.autopilot import read
from scripts.l2_pipeline.tests import test_named_panel_20261016 as base

URL = 'https://docs.legis.example.gov/statutes/statutes/961/iii/34'
SENTENCE = 'Invented official provision effective on the day after publication in the state journal'


def delivery_item(delivery, name, raw, **overrides):
    (delivery / 'files').mkdir(exist_ok=True)
    (delivery / 'files' / name).write_bytes(raw)
    item = dict(file='files/' + name, url=URL, final_url=URL, fetched_at_utc='2026-10-19T09:15:30Z', bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), browser='TestBrowser 1.0 (synthetic)')
    item.update(overrides)
    return item


def write_handoff(delivery, items, **extra):
    value = dict(schema=be.SCHEMA, design_id=None, items=items)
    value.update(extra)
    (delivery / 'HANDOFF.json').write_text(json.dumps(value))


class BrowserImportCase(base.QueueCase):
    def setUp(self):
        super().setUp()
        self.job_id = self.packet['job_id']
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.delivery = Path(self.tmp.name)
        self.raw = base.page(SENTENCE)

    def deliver(self, **overrides):
        write_handoff(self.delivery, [delivery_item(self.delivery, 'p.html', self.raw, **overrides)], design_id=self.job.design_id)

    def log(self, state='pending'):
        path = self.queue / state / self.job_id / 'EVIDENCE_LOG.json'
        return read(path)['entries'] if path.exists() else []

    def run_import(self, apply=True):
        return be.import_delivery(self.queue, self.job_id, self.delivery, apply=apply)


class ImportTests(BrowserImportCase):
    def test_plan_writes_nothing_and_apply_saves_bytes_and_logs_provenance(self):
        self.deliver()
        plan = self.run_import(apply=False)
        self.assertEqual([i['action'] for i in plan['items']], ['would_import'])
        self.assertEqual(self.log(), [])
        self.assertFalse((Path(self.job.output_dir) / 'evidence').exists())
        result = self.run_import()
        digest = hashlib.sha256(self.raw).hexdigest()
        self.assertEqual([i['action'] for i in result['items']], ['import'])
        saved = Path(self.job.output_dir) / 'evidence' / (digest + '.html')
        self.assertEqual(saved.read_bytes(), self.raw)
        self.assertEqual(saved.stat().st_mode & 0o222, 0)   # read-only, like a fetched file
        (entry,) = self.log()
        self.assertEqual((entry['via'], entry['url'], entry['final_url'], entry['sha256'], entry['size_bytes']),
                         ('browser', URL, URL, digest, len(self.raw)))
        self.assertEqual(entry['fetched_at_utc'], '2026-10-19T09:15:30Z')
        self.assertEqual((entry['browser'], entry['origin'], entry['archive_path']),
                         ('TestBrowser 1.0 (synthetic)', 'live', 'evidence/' + digest + '.html'))

    def test_second_apply_is_a_no_op(self):
        self.deliver()
        self.run_import()
        again = self.run_import()
        self.assertEqual([i['action'] for i in again['items']], ['already_imported'])
        self.assertEqual(len(self.log()), 1)

    def test_worker_fetch_evidence_returns_the_staged_entry_without_network_and_the_quote_verifies(self):
        self.deliver()
        self.run_import()
        lease = self.claim(now=time.time() - 50)

        def no_network(*args, **kwargs):
            raise AssertionError('network used')
        with patch.object(ef, 'http_get', no_network):
            entry = ef.fetch_evidence(self.queue, self.job_id, lease['token'], URL.replace('https://', 'http://'), now=time.time())
            self.assertEqual((entry['via'], entry['sha256'], entry['text_status']), ('browser', hashlib.sha256(self.raw).hexdigest(), 'ok'))
            check = ef.verify_quote(self.queue, self.job_id, lease['token'], entry['sha256'], SENTENCE, now=time.time())
            self.assertEqual(check['quote_check'], 'verified')
            text = ef.evidence_text(self.queue, self.job_id, lease['token'], entry['sha256'], now=time.time())
            self.assertIn(SENTENCE, text['text'])
        self.assertEqual(len(self.log('in_progress')), 1)   # no second log entry for the staged answer

    def test_imported_file_counts_as_fetched_for_the_evidence_rule(self):
        self.deliver()
        self.run_import()
        digest = hashlib.sha256(self.raw).hexdigest()
        value = dict(
            sources=[dict(source_id='s1', source_type='official', url=URL, final_url=URL, sha256=digest, archive_path='evidence/' + digest + '.html')],
            records=[dict(unit='ZZ', status='never_treated', evidence_refs=[
                dict(source_id='s1', locator='sec 34', quote=SENTENCE, supports='absence_code_history')])])
        decisions, _ = ef.evaluate_rows(self.job, value, ef._entries(self.queue / 'pending' / self.job_id))
        self.assertTrue(decisions[0]['known'])
        self.assertEqual(decisions[0]['basis'], 'fetched_official_source_quote_verified')

    def test_wayback_copy_keeps_original_url_and_snapshot_fields(self):
        snap = 'https://web.archive.org/web/20260610095213id_/' + URL
        self.deliver(origin='wayback', final_url=snap, snapshot_timestamp='20260610095213', live_error='timed out')
        self.run_import()
        (entry,) = self.log()
        self.assertEqual((entry['url'], entry['final_url'], entry['snapshot_timestamp'], entry['snapshot_url'], entry['live_error']),
                         (URL, snap, '20260610095213', snap, 'timed out'))
        self.assertEqual(entry['via'], 'browser')
        self.assertIsNotNone(ef.staged_entry(self.queue / 'pending' / self.job_id, URL))

    def test_pdf_is_saved_with_pdf_extension(self):
        raw = b'%PDF-1.4 synthetic bytes, not a real document'
        write_handoff(self.delivery, [delivery_item(self.delivery, 'a.pdf', raw, url='https://docs.legis.example.gov/a.pdf',
                                                    final_url='https://docs.legis.example.gov/a.pdf')], design_id=self.job.design_id)
        result = self.run_import()
        self.assertTrue(result['items'][0]['archive_path'].endswith('.pdf'))


class RefusalTests(BrowserImportCase):
    def refused(self, pattern):
        with self.assertRaisesRegex(be.ImportRefused, pattern):
            self.run_import()
        self.assertEqual(self.log(), [])
        self.assertFalse((Path(self.job.output_dir) / 'evidence').exists())

    def test_hash_or_size_mismatch_is_refused(self):
        self.deliver(sha256='0' * 64)
        self.refused('SHA256 differs')
        self.deliver(bytes=3)
        self.refused('size differs')

    def test_missing_receipt_fields_are_refused(self):
        self.deliver(browser='')
        self.refused('lacks browser')

    def test_path_outside_the_delivery_and_symlinks_are_refused(self):
        outside = Path(self.tmp.name).parent / 'outside_secret.html'
        outside.write_bytes(self.raw)
        self.addCleanup(lambda: outside.unlink())
        item = delivery_item(self.delivery, 'p.html', self.raw, file='../outside_secret.html')
        write_handoff(self.delivery, [item], design_id=self.job.design_id)
        self.refused('outside the delivery')
        link = self.delivery / 'files' / 'link.html'
        link.symlink_to(outside)
        write_handoff(self.delivery, [dict(item, file='files/link.html')], design_id=self.job.design_id)
        self.refused('symlink')

    def test_bad_url_scheme_and_credentials_are_refused(self):
        self.deliver(url='ftp://docs.legis.example.gov/a')
        self.refused('http/https')
        self.deliver(final_url='https://user:pw@docs.legis.example.gov/a')
        self.refused('credentials')

    def test_bad_timestamp_content_type_and_wayback_fields_are_refused(self):
        self.deliver(fetched_at_utc='2026-10-19 09:15')
        self.refused('fetched_at_utc')
        self.deliver(content_type='application/zip')
        self.refused('only PDF, HTML')
        self.deliver(origin='wayback')
        self.refused('Wayback copy needs')

    def test_other_design_and_unknown_packet_are_refused(self):
        write_handoff(self.delivery, [delivery_item(self.delivery, 'p.html', self.raw)], design_id='some_other_design')
        self.refused('delivery is for')
        self.deliver()
        with self.assertRaisesRegex(be.ImportRefused, 'none'):
            be.import_delivery(self.queue, 'a' * 64, self.delivery, apply=True)

    def test_a_bad_second_item_writes_nothing_for_the_first(self):
        good = delivery_item(self.delivery, 'p.html', self.raw)
        bad = delivery_item(self.delivery, 'q.html', base.page('another invented sentence of enough length'), sha256='1' * 64)
        write_handoff(self.delivery, [good, bad], design_id=self.job.design_id)
        self.refused('SHA256 differs')

    def test_finished_packet_state_is_refused(self):
        self.deliver()
        packet = self.queue / 'pending' / self.job_id
        os.replace(packet, self.queue / 'hub' / self.job_id)
        with self.assertRaisesRegex(be.ImportRefused, 'pending or in_progress'):
            self.run_import()
        os.replace(self.queue / 'hub' / self.job_id, packet)


class CommandTests(BrowserImportCase):
    def test_main_defaults_to_a_plan_and_apply_writes(self):
        self.deliver()
        args = ['--root', str(self.queue), '--job-id', self.job_id, '--delivery', str(self.delivery)]
        with patch('builtins.print') as shown:
            self.assertEqual(be.main(args), 0)
        self.assertEqual(json.loads(shown.call_args[0][0])['items'][0]['action'], 'would_import')
        self.assertEqual(self.log(), [])
        with patch('builtins.print') as shown:
            self.assertEqual(be.main(args + ['--apply']), 0)
        self.assertEqual(len(self.log()), 1)

    def test_main_returns_one_with_the_reason_on_a_refusal(self):
        self.deliver(sha256='0' * 64)
        with patch('builtins.print') as shown:
            code = be.main(['--root', str(self.queue), '--job-id', self.job_id, '--delivery', str(self.delivery), '--apply'])
        self.assertEqual(code, 1)
        self.assertIn('SHA256 differs', json.loads(shown.call_args[0][0])['refused'])


if __name__ == '__main__':
    unittest.main()
