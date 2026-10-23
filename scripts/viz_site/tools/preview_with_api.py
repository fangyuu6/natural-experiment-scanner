#!/usr/bin/env python3
"""Serve a built V5 preview folder and forward only GET /api/* to the live site; stdlib only (plus the system curl).

The static preview has no /api, so Screening can show only the fallback card for an opened candidate. This server serves the
preview files and passes GET /api/* to the public site, read-only, so the real colour-coded panel loads.

    python3 scripts/viz_site/tools/preview_with_api.py /private/tmp/nes-site-v5-r30 --port 8780

The proxy is the one of scripts/release/serve_candidate_v5.py without its release pins (no manifest, hash or binding is read or
checked): fixed HTTPS origin, GET only, redirects not followed, no curl config file, connect timeout 5 s, total 45 s, process
timeout 50 s. Every other method on /api/* is refused with 405 and never reaches the origin. Bound to 127.0.0.1 only. No work
happens on import; only main() opens a socket; only a GET /api/* runs curl. Each forwarded request is printed with a running count.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit


ORIGIN = 'https://natural-experiment-scanner.com'
HOST = '127.0.0.1'
DEFAULT_PORT = 8780
CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8', '.svg': 'image/svg+xml',
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict
    body: bytes


def error(message: str, status: int) -> Response:
    return Response(status, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
                            'X-Robots-Tag': 'noindex'},
                    json.dumps({'error': message, 'code': status}, separators=(',', ':')).encode())


def proxy_api(target: str) -> Response:
    """Fixed HTTPS origin, GET only, no redirects/config/UA override or shell."""
    try:
        result = subprocess.run(
            ['curl', '-q', '--silent', '--show-error', '--globoff', '--path-as-is',
             '--proto', '=https', '--request', 'GET', '--connect-timeout', '5',
             '--max-time', '45', '--write-out', '\n%{http_code}\n%{content_type}',
             '--url', ORIGIN + target],
            capture_output=True, timeout=50, check=False)
        if result.returncode:
            return error('upstream timeout' if result.returncode == 28 else 'upstream unavailable',
                         504 if result.returncode == 28 else 502)
        body, raw_status, raw_type = result.stdout.rsplit(b'\n', 2)
        status = int(raw_status)
        require(200 <= status <= 599, 'invalid upstream status')
        content_type = raw_type.decode('latin-1')
        require(not any(ord(c) < 32 or ord(c) == 127 for c in content_type), 'invalid upstream content type')
        return Response(status, {'Content-Type': content_type, 'Cache-Control': 'no-store'} if content_type
                        else {'Cache-Control': 'no-store'}, body)
    except subprocess.TimeoutExpired:
        return error('upstream timeout', 504)
    except (OSError, ValueError):
        return error('upstream unavailable', 502)


def request_target(target: str):
    """(path, path plus query): encoded names stay encoded, dot segments collapse, so /api/../x is /x and is never forwarded."""
    require(target.startswith('/') and not target.startswith('//')
            and not any(ord(c) < 32 or ord(c) == 127 for c in target), 'invalid request target')
    path_part, separator, query_part = target.partition('?')
    parsed = urlsplit(path_part.replace('\\', '/') + separator + query_part)
    require(not parsed.netloc, 'invalid request authority')
    segments = []
    parts = parsed.path.split('/')[1:]
    for index, part in enumerate(parts):
        dot = part.lower().replace('%2e', '.')
        if dot in ('.', '..'):
            if dot == '..' and segments:
                segments.pop()
            if index == len(parts) - 1:
                segments.append('')
        else:
            segments.append(part)
    path = '/' + '/'.join(segments)
    return path, path + ('?' + parsed.query if parsed.query else '')


class PreviewApp:
    def __init__(self, root: Path, proxy=proxy_api, log=None):
        self.root = Path(root).resolve()
        self.proxy = proxy
        self.log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self.api_requests = 0
        self._lock = threading.Lock()

    def static(self, path: str) -> Response:
        relative = unquote(path).lstrip('/')
        if '\x00' in relative:
            return error('invalid request target', 400)
        try:
            found = (self.root / relative).resolve()
        except (OSError, RuntimeError):
            return error('not found', 404)
        # A name that resolves outside the preview folder (a symlink, an encoded slash and dots) is not served.
        if found != self.root and self.root not in found.parents:
            return error('not found', 404)
        if found.is_dir():
            if not path.endswith('/'):
                return Response(301, {'Location': path + '/', 'Cache-Control': 'no-store'}, b'')
            found = found / 'index.html'
        if not found.is_file():
            return error('not found', 404)
        content_type = CONTENT_TYPES.get(found.suffix.lower()) or mimetypes.guess_type(found.name)[0] or 'application/octet-stream'
        # No caching, so a rebuilt preview shows at once.
        return Response(200, {'Content-Type': content_type, 'Cache-Control': 'no-store'}, found.read_bytes())

    def handle(self, method: str, target: str) -> Response:
        try:
            path, upstream_target = request_target(target)
        except ValueError:
            return error('invalid request target', 400)
        if path.startswith('/api/'):
            if method != 'GET':
                response = error('method not allowed', 405)
                return Response(405, {**response.headers, 'Allow': 'GET'}, response.body)
            with self._lock:
                self.api_requests += 1
                number = self.api_requests
            response = self.proxy(upstream_target)
            self.log(f'api #{number} GET {upstream_target} -> {response.status} ({len(response.body)} bytes)')
            return response
        if method not in ('GET', 'HEAD'):
            response = error('method not allowed', 405)
            return Response(405, {**response.headers, 'Allow': 'GET, HEAD'}, response.body)
        return self.static(path)


def make_handler(app: PreviewApp):
    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            response = app.handle(self.command, self.path)
            try:
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', str(len(response.body)))
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(response.body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser closed the connection

        def log_message(self, format, *args):
            pass  # only the forwarded API requests are logged

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = respond
        do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = respond

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('directory', type=Path, help='built preview folder, e.g. /private/tmp/nes-site-v5-r30')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.directory.is_symlink() or not args.directory.is_dir():
        print('STOP: not a preview folder: ' + str(args.directory), file=sys.stderr)
        return 1
    app = PreviewApp(args.directory)
    with ThreadingHTTPServer((HOST, args.port), make_handler(app)) as server:
        print(f'http://{HOST}:{server.server_port}/  serving {app.root}  (GET /api/* -> {ORIGIN})', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
