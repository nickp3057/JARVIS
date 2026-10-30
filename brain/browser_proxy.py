"""Public-only egress for Chromium, including redirects and subresources.

Resolve and connect to the same checked IP. Chromium must use this proxy with
its implicit loopback bypass disabled. Each HTTP connection serves one request;
HTTPS uses a tunnel to a checked public host. No credentials are logged.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import select
import socket
import threading
from urllib.parse import urlsplit


def _connect(host: str, port: int) -> socket.socket:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0].split('%', 1)[0]).is_global for a in addresses):
        raise ValueError('Private network address refused')
    last = None
    for family, kind, protocol, _, address in addresses:
        sock = socket.socket(family, kind, protocol)
        sock.settimeout(20)
        try:
            sock.connect(address)  # pinned IP; no second DNS resolution
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise OSError('Public host unreachable') from last


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    while True:
        ready, _, _ = select.select([client, upstream], [], [], 20)
        if not ready:
            return
        for source in ready:
            data = source.recv(65536)
            if not data:
                return
            (upstream if source is client else client).sendall(data)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    rbufsize = 0  # no buffered body bytes stranded before socket relay

    def log_message(self, *_args) -> None:
        pass

    def do_CONNECT(self) -> None:
        self._forward(tunnel=True)

    def _forward(self, tunnel: bool = False) -> None:
        self.close_connection = True
        target = urlsplit('//' + self.path if tunnel else self.path)
        started = False
        try:
            if not target.hostname or target.username or target.password or (not tunnel and target.scheme not in {'http', 'ws'}):
                raise ValueError('Unsupported proxy target')
            with _connect(target.hostname, target.port or (443 if tunnel else 80)) as upstream:
                self.connection.settimeout(20)
                if tunnel:
                    self.send_response(200, 'Connection Established')
                    self.end_headers()
                else:
                    path = target.path or '/'
                    if target.query:
                        path += '?' + target.query
                    # Close after this response: a second absolute request on
                    # the same socket must pass address validation again.
                    headers = [(k, v) for k, v in self.headers.items()
                               if k.lower() not in {'host', 'connection', 'proxy-connection', 'proxy-authorization'}]
                    authority = target.netloc
                    head = f'{self.command} {path} HTTP/1.1\r\nHost: {authority}\r\n'
                    head += ''.join(f'{k}: {v}\r\n' for k, v in headers)
                    head += ('Connection: Upgrade\r\n\r\n' if self.headers.get('Upgrade') else 'Connection: close\r\n\r\n')
                    upstream.sendall(head.encode('latin-1'))
                started = True
                _relay(self.connection, upstream)
        except (OSError, ValueError):
            if not started:
                self.send_error(403, 'Public network access only')

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward


class PublicProxy:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='browser-egress')
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
