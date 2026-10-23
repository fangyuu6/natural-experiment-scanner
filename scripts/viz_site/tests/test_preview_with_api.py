"""The preview server forwards only GET /api/* to the fixed origin and serves nothing outside the preview folder.

No network and no socket: the proxy is a fake, or curl is patched. The folder served in these tests is this tests folder.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.viz_site.tools import preview_with_api as server

HERE = Path(__file__).resolve().parent


class FakeProxy:
    def __init__(self):
        self.targets = []

    def __call__(self, target):
        self.targets.append(target)
        return server.Response(200, {'Content-Type': 'application/json'}, b'{"ok":true}')


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.proxy = FakeProxy()
        self.lines = []
        self.app = server.PreviewApp(HERE, proxy=self.proxy, log=self.lines.append)

    def test_only_loopback_and_the_public_origin(self):
        self.assertEqual(server.HOST, '127.0.0.1')
        self.assertEqual(server.ORIGIN, 'https://natural-experiment-scanner.com')
        self.assertEqual(server.DEFAULT_PORT, 8780)

    def test_get_api_is_forwarded_with_its_query_and_counted(self):
        response = self.app.handle('GET', '/api/policy-groups/raise_the_age?page=1&size=100')
        self.assertEqual((response.status, response.body), (200, b'{"ok":true}'))
        self.assertEqual(self.proxy.targets, ['/api/policy-groups/raise_the_age?page=1&size=100'])
        self.app.handle('GET', '/api/policies/12/detail')
        self.assertEqual(self.lines, ['api #1 GET /api/policy-groups/raise_the_age?page=1&size=100 -> 200 (11 bytes)',
                                      'api #2 GET /api/policies/12/detail -> 200 (11 bytes)'])
        self.assertEqual(self.app.api_requests, 2)

    def test_every_other_method_on_api_is_refused_before_the_proxy(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT', 'HEAD'):
            response = self.app.handle(method, '/api/policies/12/detail')
            self.assertEqual(response.status, 405, method)
            self.assertEqual(response.headers['Allow'], 'GET')
        self.assertEqual(self.proxy.targets, [])
        self.assertEqual(self.app.api_requests, 0)

    def test_dot_segments_cannot_turn_a_static_name_into_an_api_call_or_the_reverse(self):
        # /api/../__init__.py is the static file /__init__.py; neither form reaches the origin.
        for target in ('/api/../__init__.py', '/api/%2e%2e/__init__.py', '/api/./../__init__.py'):
            response = self.app.handle('GET', target)
            self.assertEqual(response.status, 200, target)
            self.assertEqual(response.body, (HERE / '__init__.py').read_bytes(), target)
        self.assertEqual(self.proxy.targets, [])
        # The collapsed path is what is forwarded.
        self.app.handle('GET', '/x/../api/policies/1/detail')
        self.assertEqual(self.proxy.targets, ['/api/policies/1/detail'])
        # "/api" alone and "/apix/" are not API paths.
        for target in ('/api', '/apix/y'):
            self.assertEqual(self.app.handle('GET', target).status, 404, target)
        self.assertEqual(len(self.proxy.targets), 1)

    def test_other_origins_and_malformed_targets_are_refused(self):
        for target in ('//evil.example/api/x', 'http://evil.example/api/x', 'api/x', '/api/x\x00', '/api/x\r\nHost: evil', '/api/\x7fx'):
            self.assertEqual(self.app.handle('GET', target).status, 400, repr(target))
        self.assertEqual(self.proxy.targets, [])

    def test_static_files_come_from_the_folder_only(self):
        response = self.app.handle('GET', '/__init__.py')
        self.assertEqual((response.status, response.body), (200, (HERE / '__init__.py').read_bytes()))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.app.handle('HEAD', '/__init__.py').status, 200)
        # A name that leaves the folder is not served, whether written with dots, an encoded slash or a backslash.
        outside = HERE.parent / 'check_v5.py'
        self.assertTrue(outside.is_file())
        for target in ('/..%2fcheck_v5.py', '/%2e%2e%2fcheck_v5.py', '/..%5ccheck_v5.py', '/sub/..%2f..%2fcheck_v5.py'):
            self.assertEqual(self.app.handle('GET', target).status, 404, target)
        self.assertEqual(self.app.handle('GET', '/no_such_file.html').status, 404)
        for method in ('POST', 'PUT', 'DELETE', 'OPTIONS'):
            response = self.app.handle(method, '/__init__.py')
            self.assertEqual((response.status, response.headers['Allow']), (405, 'GET, HEAD'), method)
        self.assertEqual(self.proxy.targets, [])

    def test_directory_without_a_slash_goes_to_the_slash_and_without_an_index_is_not_found(self):
        parent = server.PreviewApp(HERE.parent, proxy=self.proxy, log=self.lines.append)
        response = parent.handle('GET', '/tests')
        self.assertEqual((response.status, response.headers['Location']), (301, '/tests/'))
        self.assertEqual(parent.handle('GET', '/tests/').status, 404)

    def test_curl_call_is_fixed_origin_get_only_without_redirects_and_with_the_three_timeouts(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b'{"a":1}\n200\napplication/json; charset=utf-8', stderr=b'')
        with patch.object(server.subprocess, 'run', return_value=completed) as run:
            response = server.proxy_api('/api/policies/7/detail?x=1')
        command, options = run.call_args[0][0], run.call_args[1]
        self.assertEqual(command[0], 'curl')
        self.assertIn('-q', command)  # no curl config file
        self.assertEqual(command[command.index('--request') + 1], 'GET')
        self.assertEqual(command[command.index('--url') + 1], 'https://natural-experiment-scanner.com/api/policies/7/detail?x=1')
        self.assertEqual(command[command.index('--proto') + 1], '=https')
        self.assertEqual((command[command.index('--connect-timeout') + 1], command[command.index('--max-time') + 1], options['timeout']), ('5', '45', 50))
        for flag in ('-L', '--location', '-d', '--data', '-F', '--form', '-T', '--upload-file', '-x', '--proxy', '-K', '--config', '-H', '--header', '-A', '--user-agent'):
            self.assertNotIn(flag, command)
        self.assertFalse(options.get('shell', False))
        self.assertEqual((response.status, response.body, response.headers['Content-Type']), (200, b'{"a":1}', 'application/json; charset=utf-8'))

    def test_upstream_failures_become_502_and_504_and_an_upstream_redirect_is_not_followed(self):
        for code, status in ((28, 504), (6, 502), (35, 502)):
            with patch.object(server.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, stdout=b'', stderr=b'')):
                self.assertEqual(server.proxy_api('/api/x').status, status, code)
        with patch.object(server.subprocess, 'run', side_effect=subprocess.TimeoutExpired('curl', 50)):
            self.assertEqual(server.proxy_api('/api/x').status, 504)
        with patch.object(server.subprocess, 'run', side_effect=OSError('no curl')):
            self.assertEqual(server.proxy_api('/api/x').status, 502)
        # A 301 from the origin is handed back as a 301 without a Location header: the page sees it fail and curl never followed it.
        redirect = subprocess.CompletedProcess([], 0, stdout=b'\n301\ntext/html', stderr=b'')
        with patch.object(server.subprocess, 'run', return_value=redirect):
            response = server.proxy_api('/api/x')
        self.assertEqual(response.status, 301)
        self.assertNotIn('Location', response.headers)

    def test_a_missing_folder_stops_before_any_socket(self):
        with patch.object(server, 'ThreadingHTTPServer') as listener, patch.object(server.sys, 'stderr'):
            self.assertEqual(server.main([str(HERE / 'no_such_preview_folder')]), 1)
        listener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
