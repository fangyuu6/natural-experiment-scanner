"""T191 (2026-10-20): evidence fetch fixes. TLS-1.3-only hosts via curl, plain-agent retry on HTTP 403, Wayback by direct address
(no availability API when it is rate-limited). Synthetic data; the network function is replaced."""
import unittest
from unittest.mock import patch

from scripts.l2_pipeline.agents import evidence_fetch as ef

OK = dict(status=200, final_url='https://x.example.gov/a', content_type='text/html', charset='', data=b'<html>ok</html>')


class TransportTests(unittest.TestCase):
    def test_tls_version_alert_is_retried_through_curl(self):
        err = ef.FetchError('URLError: [SSL: TLSV1_ALERT_PROTOCOL_VERSION] tlsv1 alert protocol version (_ssl.c:1129)')
        with patch.object(ef, '_urllib_get', side_effect=err), patch.object(ef, '_curl_get', return_value=OK) as curl:
            self.assertEqual(ef.http_get('https://x.example.gov/a')['status'], 200)
        curl.assert_called_once()

    def test_curl_failure_keeps_the_original_error_text(self):
        err = ef.FetchError('URLError: [SSL: TLSV1_ALERT_PROTOCOL_VERSION] x')
        with patch.object(ef, '_urllib_get', side_effect=err), \
                patch.object(ef, '_curl_get', side_effect=ef.FetchError('URLError: timed out (curl)')):
            with self.assertRaisesRegex(ef.FetchError, 'TLSV1_ALERT_PROTOCOL_VERSION.*curl: URLError: timed out'):
                ef.http_get('https://x.example.gov/a')

    def test_other_errors_do_not_go_through_curl(self):
        with patch.object(ef, '_urllib_get', side_effect=ef.FetchError('HTTP 404')), patch.object(ef, '_curl_get') as curl:
            with self.assertRaisesRegex(ef.FetchError, 'HTTP 404'):
                ef.http_get('https://x.example.gov/a')
        curl.assert_not_called()

    def test_403_is_not_retried_with_another_agent(self):
        # hub ruling 2026-10-20 (T191): no browser-like or alternate agent after a refusal
        with patch.object(ef, '_urllib_get', side_effect=ef.FetchError('HTTP 403')) as get:
            with self.assertRaisesRegex(ef.FetchError, 'HTTP 403'):
                ef.http_get('https://x.example.gov/a')
            self.assertEqual(get.call_count, 1)

    def test_curl_path_refuses_private_redirect_targets(self):
        # check_url runs on every hop; a literal private address is refused before any process starts
        with patch('subprocess.run') as run:
            with self.assertRaises(ef.FetchError):
                ef._curl_get('http://10.0.0.1/x', 5, 1000, ef.USER_AGENT)
        run.assert_not_called()


class WaybackTests(unittest.TestCase):
    URL = 'https://x.example.gov/code/1'

    def test_direct_address_gives_bytes_and_timestamp_without_the_availability_api(self):
        calls = []

        def get(url, *a, **k):
            calls.append(url)
            return dict(status=200, final_url='https://web.archive.org/web/20260610095213id_/' + self.URL,
                        content_type='text/html', charset='', data=b'<html>snap</html>')
        out = ef.wayback_snapshot(self.URL, get)
        self.assertEqual((out['snapshot_timestamp'], len(calls)), ('20260610095213', 1))
        self.assertTrue(calls[0].startswith(ef.WAYBACK_RAW))

    def test_404_means_no_snapshot_and_429_does_not_call_the_api(self):
        for code, text in (('HTTP 404', 'No Wayback snapshot listed'), ('HTTP 429', 'rate-limited')):
            calls = []

            def get(url, *a, **k):
                calls.append(url)
                raise ef.FetchError(code)
            with self.assertRaisesRegex(ef.FetchError, text):
                ef.wayback_snapshot(self.URL, get)
            self.assertEqual(len(calls), 1)

    def test_other_direct_failure_falls_back_to_the_availability_api(self):
        calls = []

        def get(url, *a, **k):
            calls.append(url)
            if url.startswith(ef.WAYBACK_RAW):
                raise ef.FetchError('URLError: timed out')
            if url.startswith(ef.WAYBACK_API):
                return dict(status=200, final_url=url, content_type='application/json', charset='', data=(
                    b'{"archived_snapshots":{"closest":{"available":true,"status":"200","timestamp":"20240102030405"}}}'))
            return dict(status=200, final_url=url, content_type='text/html', charset='', data=b'<html>s</html>')
        out = ef.wayback_snapshot(self.URL, get)
        self.assertEqual(out['snapshot_timestamp'], '20240102030405')
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()


class TemplateTests(unittest.TestCase):
    def test_current_history_template_adds_only_the_pre_window_item(self):
        from pathlib import Path
        from scripts.l2_pipeline.agents import history
        base = Path(history.__file__).parent / 'templates/v9_n12/official_history_coder_fx10.md'
        new = history.CURRENT_TEMPLATE
        self.assertTrue(new.is_file())
        old_lines, new_lines = base.read_text().split('\n'), new.read_text().split('\n')
        added = [l for l in new_lines if l not in old_lines]
        self.assertEqual(len(added), 1)
        self.assertTrue(added[0].startswith('6. Pre-window states'))
        self.assertIn('pre_window_bound=true', added[0])
        self.assertEqual([l for l in new_lines if l in old_lines], old_lines)   # nothing removed or reordered
