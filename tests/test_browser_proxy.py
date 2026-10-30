"""No external hosts: exercise the proxy with fake DNS and loopback HTTP."""
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests
from brain import browser_proxy

class BrowserProxyTests(unittest.TestCase):
    def test_private_and_mixed_dns_answers_never_connect(self):
        for ips in [('127.0.0.1',), ('10.0.0.147',), ('::1',), ('8.8.8.8', '192.168.1.1')]:
            addresses = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', (ip, 80)) for ip in ips]
            with patch.object(socket, 'getaddrinfo', return_value=addresses), patch.object(socket, 'socket') as connect:
                with self.assertRaises(ValueError):
                    browser_proxy._connect('example.test', 80)
                connect.assert_not_called()

    def test_connect_uses_checked_ip_without_resolving_again(self):
        address = ('8.8.8.8', 443)
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', address)]), \
                patch.object(socket, 'socket', return_value=Mock()) as factory:
            sock = browser_proxy._connect('public.test', 443)
        sock.connect.assert_called_once_with(address)

    def test_redirect_and_direct_private_subrequests_are_blocked(self):
        received = []
        class Site(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                received.append(self.path)
                self.send_response(302 if self.path == '/redirect' else 200)
                self.send_header('Location', 'http://127.0.0.1/private')
                self.end_headers()
                self.wfile.write(b'public')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        proxy = browser_proxy.PublicProxy()
        self.addCleanup(proxy.close)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        original = browser_proxy._connect
        def connect(host, port):
            if host == 'public.test':
                return socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
            return original(host, port)
        session = requests.Session()
        session.trust_env = False
        self.addCleanup(session.close)
        session.proxies = {'http': proxy.url, 'https': proxy.url}
        with patch.object(browser_proxy, '_connect', side_effect=connect):
            self.assertEqual(session.get('http://public.test/', timeout=3).status_code, 200)
            self.assertEqual(session.get('http://public.test/redirect', timeout=3).status_code, 403)
            self.assertEqual(session.get('http://127.0.0.1/private', timeout=3).status_code, 403)
            with self.assertRaises(requests.exceptions.ProxyError):
                session.get('https://127.0.0.1/private', timeout=3)
        self.assertEqual(received, ['/', '/redirect'])
