"""Offline public-client behavior checks; never contact a public endpoint."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorNotReady
from scripts.l2_pipeline.connectors.outcome_v2_core import PublicClient, check_url


class Response(io.BytesIO):
    def __init__(self, body=b'[{"year":2023}]', content_type='application/json'):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}

    def geturl(self):
        return 'https://data.cdc.gov/resource/test.json'


class Opener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class PublicClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.json'
        self.url = 'https://data.cdc.gov/resource/test.json'

    def tearDown(self):
        self.temp.cleanup()

    def client(self, opener, **kwargs):
        return PublicClient(opener=opener, sleep=lambda _: None, clock=lambda: 1000, **kwargs)

    def test_receipt_cache_and_mutation(self):
        opener = Opener(Response())
        client = self.client(opener)
        receipt = client.fetch(self.url, self.path)
        self.assertEqual(receipt, client.fetch(self.url, self.path))
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(len(receipt['sha256']), 64)
        self.assertEqual(receipt['size_bytes'], self.path.stat().st_size)
        self.assertNotIn('X-app-token', opener.requests[0].headers)
        self.path.write_bytes(b'changed')
        with self.assertRaises(ConnectorError):
            client.fetch(self.url, self.path)

    def test_login_html_never_saved(self):
        client = self.client(Opener(Response(b'<html>Missing Key</html>', 'text/html')))
        with self.assertRaises(ConnectorNotReady):
            client.fetch(self.url, self.path)
        self.assertFalse(self.path.exists())

    def test_retry_then_receipt(self):
        err = urllib.error.HTTPError(self.url, 429, 'rate limit', {'Retry-After': '2'}, None)
        opener = Opener(err, Response())
        self.client(opener).fetch(self.url, self.path)
        self.assertEqual(len(opener.requests), 2)

    def test_long_retry_and_forbidden_never_bypassed(self):
        for code, headers in [(429, {'Retry-After': '61'}), (403, {})]:
            opener = Opener(urllib.error.HTTPError(self.url, code, 'blocked', headers, None))
            with self.assertRaises(ConnectorNotReady):
                self.client(opener).fetch(self.url, self.path)
            self.assertEqual(len(opener.requests), 1)

    def test_size_cap_and_orphan_cache(self):
        with self.assertRaises(ConnectorError):
            self.client(Opener(Response(b'12345')), max_bytes=4).fetch(self.url, self.path)
        self.path.write_bytes(b'unknown')
        with self.assertRaises(ConnectorError):
            self.client(Opener()).fetch(self.url, self.path)

    def test_url_credentials_and_nonofficial_sources(self):
        for url in ['http://data.cdc.gov/test', 'https://data.cdc.gov.evil.test/a',
                    'https://user:password@data.cdc.gov/a',
                    'https://api.census.gov/data?key=anything',
                    'https://data.cdc.gov/a?app_token=anything']:
            with self.assertRaises(ConnectorError):
                check_url(url)
        check_url('https://data.cdc.gov/resource/test.json?$limit=10&$where=year%3D2023')

    def test_bls_post_cache_binds_request_and_never_accepts_keys(self):
        url = 'https://api.bls.gov/publicAPI/v1/timeseries/data/'
        request = {'seriesid': ['LAUST060000000000005'], 'startyear': '2023', 'endyear': '2024'}
        opener = Opener(Response())
        client = self.client(opener)
        receipt = client.fetch(url, self.path, json_body=request)
        self.assertEqual(receipt['request_method'], 'POST')
        self.assertEqual(json.loads(opener.requests[0].data), request)
        self.assertEqual(receipt, client.fetch(url, self.path, json_body=request))
        with self.assertRaises(ConnectorError):
            client.fetch(url, self.path, json_body=dict(request, endyear='2025'))
        with self.assertRaises(ConnectorError):
            client.fetch(url, self.path, json_body=dict(request, registrationkey='example'))
        with self.assertRaises(ConnectorError):
            client.fetch(url, self.path, json_body=dict(request, startyear='2000'))

    def test_symlink_receipt_refused_before_read(self):
        target = self.path.with_name('arbitrary.txt')
        target.write_text('This file must not be parsed as a receipt')
        self.path.write_bytes(b'public bytes')
        self.path.with_name(self.path.name + '.receipt.json').symlink_to(target)
        with self.assertRaisesRegex(ConnectorError, 'symlinks'):
            self.client(Opener()).fetch(self.url, self.path)


if __name__ == '__main__':
    unittest.main()
