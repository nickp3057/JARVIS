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


    def test_kinds(self):
        kinds = {a["id"]: a["kind"] for a in agent_roster.roster()["agents"]}
        self.assertEqual(kinds["memory"], "helper")
        self.assertEqual(kinds["study"], "helper")
        self.assertEqual(kinds["email"], "subagent")
        self.assertEqual(kinds["crash"], "subagent")

    def test_a_new_subagent_shows_up_by_itself(self):
        subagents.run_job("weather_watch", "Watching the storm", lambda: "Storm passed.")
        spec = subagents.SubagentSpec(name="gift_finder", system="s", tools=[], default_model="claude-test")
        subagents.run(spec, "go", client=None)  # no key: still noticed
        agents = {a["id"]: a for a in agent_roster.roster()["agents"]}
        self.assertEqual(agents["new:weather_watch"]["name"], "Watching the storm")
        self.assertEqual(agents["new:weather_watch"]["recent"][0]["summary"], "Storm passed.")
        self.assertEqual(agents["new:gift_finder"]["kind"], "subagent")

    def test_every_subagent_in_the_code_has_a_map_entry(self):
        """Adding a subagent? Give it an _AGENTS entry in brain/agent_roster.py."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        names = set()
        for path in list((root / "brain").glob("*.py")) + list((root / "integrations").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            names |= set(re.findall(r'(?:run_job|subagents\.start|_spec)\(\s*f?"([a-z_]+)', text))
            names |= set(re.findall(r'SubagentSpec\(\s*name="([a-z_]+)"', text))
        names |= {"investing_" + k for k in ("suggest", "review", "playground")}  # investing.start(kind)
        known = "|".join(a["jobs"] for a in agent_roster._AGENTS if a["jobs"])
        patterns = known.split("|")
        # f"investing_{kind}" leaves the prefix "investing_": covered if a known name starts with it.
        missing = sorted(n for n in names if not agent_roster._matches(n, known)
                         and not (n.endswith("_") and any(p.startswith(n) for p in patterns)))
        self.assertEqual(missing, [], "add these to _AGENTS in brain/agent_roster.py")
        self.assertIn("email_triage", names)


if __name__ == "__main__":
    unittest.main()
