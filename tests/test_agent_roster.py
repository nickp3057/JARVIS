import unittest
from unittest.mock import patch

from brain import agent_roster, subagents
from integrations import gmail, schwab


class RosterTests(unittest.TestCase):
    def setUp(self):
        subagents.reset_for_tests()

    def tearDown(self):
        subagents.reset_for_tests()

    def test_jarvis_is_the_head_and_jobs_show_on_their_agent(self):
        subagents.run_job("email_triage", "Sorting your inbox", lambda: "Sorted 3 emails.")
        subagents.run_job("investing_drop_NVDA", "Checking NVDA's drop", lambda: "Holding.")
        with patch.object(gmail, "is_linked", return_value=True), \
                patch.object(schwab, "configured", return_value=True), \
                patch("brain.investing.pending_proposals", return_value=[{"id": "P1"}]), \
                patch.dict("os.environ", {"EMAIL_TRIAGE_ENABLED": "true", "INVESTING_ENABLED": "true"}):
            data = agent_roster.roster()
        self.assertEqual(data["head"]["name"], "JARVIS")
        agents = {a["id"]: a for a in data["agents"]}
        self.assertTrue(all(a["boss"] == "jarvis" for a in agents.values()))
        self.assertEqual(agents["email"]["recent"][0]["summary"], "Sorted 3 emails.")
        self.assertEqual(agents["crash"]["recent"][0]["summary"], "Holding.")
        self.assertEqual(agents["advisor"]["needs_you"], 1)
        self.assertEqual(agents["advisor"]["recent"], [])

    def test_off_agents_say_why(self):
        with patch.object(gmail, "is_linked", return_value=False), \
                patch.dict("os.environ", {"EMAIL_TRIAGE_ENABLED": "true"}):
            email = next(a for a in agent_roster.roster()["agents"] if a["id"] == "email")
        self.assertEqual(email["state"], "off")
        self.assertIn("Gmail", email["note"])


if __name__ == "__main__":
    unittest.main()
