import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.server as server
from brain import skill_requests, tools


class SkillRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(skill_requests, '_FILE', Path(self.tmp.name) / 'skills.json')
        p.start()
        self.addCleanup(p.stop)

    def test_asking_again_counts_instead_of_duplicating(self):
        first = skill_requests.file('Control the bedroom blinds', 'close my blinds', 'no blinds integration')
        again = skill_requests.file('Control bedroom blinds', 'tilt the blinds shut')
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(again['count'], 2)
        self.assertEqual(again['asks'], ['close my blinds', 'tilt the blinds shut'])
        other = skill_requests.file('Order groceries from Smiths')
        self.assertNotEqual(other['id'], first['id'])
        self.assertEqual([r['id'] for r in skill_requests.listing()], [first['id'], other['id']])  # most asked first

    def test_closed_requests_leave_the_open_list_and_a_new_ask_starts_fresh(self):
        record = skill_requests.file('Read my Letterboxd diary')
        skill_requests.set_status(record['id'], 'done')
        self.assertEqual(skill_requests.listing(), [])
        self.assertEqual(skill_requests.listing('done')[0]['id'], record['id'])
        fresh = skill_requests.file('Read my Letterboxd diary')
        self.assertNotEqual(fresh['id'], record['id'])
        with self.assertRaises(ValueError):
            skill_requests.set_status(record['id'], 'bogus')

    def test_tools_file_and_list(self):
        reply = tools.execute_tool('request_skill', {'summary': 'Book a restaurant', 'ask': 'get me a table at 7'})
        self.assertIn('Skill requests', reply)
        self.assertIn('Book a restaurant', tools.execute_tool('list_skill_requests', {}))
        self.assertIn("Couldn't", tools.execute_tool('request_skill', {'summary': '  '}))

    def test_endpoints(self):
        with patch.dict(os.environ, {'JARVIS_API_TOKEN': 'test-token'}):
            server._runtime = MagicMock()
            self.addCleanup(setattr, server, '_runtime', None)
            client = TestClient(server.app)
            self.addCleanup(client.close)
            headers = {'Authorization': 'Bearer test-token'}
            record = skill_requests.file('Wake the gaming PC')
            [row] = client.get('/api/skills/requests', headers=headers).json()['requests']
            self.assertEqual(row['id'], record['id'])
            body = client.post(f"/api/skills/requests/{record['id']}", json={'status': 'declined'}, headers=headers)
            self.assertEqual(body.json()['request']['status'], 'declined')
            self.assertEqual(client.post('/api/skills/requests/nope', json={'status': 'done'}, headers=headers).status_code, 404)
            self.assertEqual(client.post(f"/api/skills/requests/{record['id']}", json={'status': 'x'}, headers=headers).status_code, 422)
            self.assertEqual(client.get('/api/skills/requests', headers=headers).json()['requests'], [])


if __name__ == '__main__':
    unittest.main()
