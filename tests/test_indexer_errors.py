"""Quoting Prowlarr's real indexer errors, and the switch_indexer_mirror fix.

Found 2026-10-28: 1337x.to answered ``403 Forbidden, error code: 1006`` (a
Cloudflare IP ban on the VPN exit) and JARVIS called it a 429 rate limit."""
import os
import unittest
from unittest.mock import MagicMock, patch

from brain import stack_fixes
from integrations import nas_stack

BANNED = ('HTTP Error - Res: HTTP/2.0 [GET] https://1337x.to/cat/Movies/1/: 403.Forbidden (17 bytes)\n'
          'error code: 1006')
ENV = {'PROWLARR_URL': 'http://p:9696', 'PROWLARR_API_KEY': 'k'}


def _indexer():
    return {'id': 6, 'name': '1337x', 'fields': [{'name': 'baseUrl', 'value': None}],
            'indexerUrls': ['https://1337x.to/', 'https://1337x.st/', 'https://x1337x.ws/'],
            'legacyUrls': ['https://1337x.is/']}


def _response(status, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body if body is not None else {}
    response.text = ''
    return response


class ExplainTests(unittest.TestCase):
    def test_cloudflare_1006_is_an_ip_ban_not_a_rate_limit(self):
        text = nas_stack.explain_http_error(BANNED)
        self.assertIn('HTTP 403 Forbidden', text)
        self.assertIn('Cloudflare error 1006', text)
        self.assertIn('banned this IP', text)
        self.assertIn('not a rate limit', text)

    def test_429_is_called_a_rate_limit(self):
        text = nas_stack.explain_http_error('[GET] https://x.example/: 429.TooManyRequests (0 bytes)')
        self.assertIn('rate limit', text)

    def test_no_status_means_none(self):
        self.assertIsNone(nas_stack.explain_http_error('Searching indexer(s): [1337x] for Term: [] 2006'))

    def test_newest_error_is_matched_to_the_indexer_by_host(self):
        records = [{'time': '2026-10-28T17:18:56Z', 'message': 'Searching indexer(s): [1337x]'},
                   {'time': '2026-10-28T17:18:38Z', 'message': BANNED},
                   {'time': '2026-10-28T02:00:00Z', 'message': '[GET] https://other.example/: 500.InternalServerError'}]
        with patch.object(nas_stack.requests, 'get', return_value=_response(200, [_indexer()])):
            found = nas_stack.indexer_errors('http://p:9696', 'k', records, [6])
        self.assertEqual(list(found), ['indexer:6'])
        self.assertTrue(found['indexer:6'].startswith('1337x: HTTP 403 Forbidden, Cloudflare error 1006'))
        self.assertIn('at 2026-10-28 17:18 UTC', found['indexer:6'])


class SwitchMirrorTests(unittest.TestCase):
    def _run(self, tests, outages=None):
        put = MagicMock(return_value=_response(202))
        answers = iter(tests)
        self.tested = []

        def post(url, json, **_):
            self.tested.append(json['fields'][0]['value'])  # the dict is reused, so copy at call time
            return next(answers)
        with patch.dict(os.environ, ENV), \
             patch.object(stack_fixes.requests, 'get', return_value=_response(200, _indexer())), \
             patch.object(stack_fixes.requests, 'post', side_effect=post), \
             patch.object(stack_fixes.requests, 'put', put), \
             patch.object(stack_fixes.nas_stack, 'indexer_outages', return_value=outages or {}), \
             patch.object(stack_fixes, '_sleep'):
            ok, outcome = stack_fixes.run('switch_indexer_mirror', {'indexer_id': 6, 'label': '1337x'})
        return ok, outcome, put

    def test_saves_the_first_mirror_that_passes(self):
        refused = _response(400, [{'errorMessage': BANNED}])
        ok, outcome, put = self._run([refused, _response(200)])
        self.assertTrue(ok)
        self.assertIn('now uses https://x1337x.ws/', outcome)
        self.assertIn('https://1337x.st/: HTTP 403 Forbidden, Cloudflare error 1006', outcome)
        self.assertEqual(self.tested, ['https://1337x.st/', 'https://x1337x.ws/'])  # never the banned default
        self.assertEqual(put.call_args.kwargs['json']['fields'][0]['value'], 'https://x1337x.ws/')

    def test_nothing_is_saved_when_every_mirror_fails(self):
        refused = _response(400, [{'errorMessage': BANNED}])
        ok, outcome, put = self._run([refused, refused])
        self.assertFalse(ok)
        put.assert_not_called()
        self.assertIn('nothing was saved', outcome)
        self.assertIn('new gluetun server', outcome)

    def test_still_backed_off_after_saving_is_reported(self):
        ok, outcome, _ = self._run([_response(200)], outages={'indexer:6': 'Indexer 1337x is down'})
        self.assertFalse(ok)
        self.assertIn('still shows as backed off', outcome)

    def test_describe_names_the_indexer(self):
        self.assertIn('Switch 1337x', stack_fixes.describe('switch_indexer_mirror', {'indexer_id': 6, 'label': '1337x'}))


if __name__ == '__main__':
    unittest.main()
