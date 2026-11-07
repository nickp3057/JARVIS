import unittest
from unittest.mock import patch

from brain import subagents


class _Resp:
    def __init__(self, content):
        self.content = content


class FakeClient:
    """Plays back scripted responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return _Resp(self.responses.pop(0))


def _use(tool_id, name, args):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def _spec(handler, **kw):
    tool = subagents.SubagentTool("act", "Do it.", {"type": "object", "properties": {}}, handler)
    return subagents.SubagentSpec(name="t", system="sys", tools=[tool], default_model="claude-test", **kw)


class RunTests(unittest.TestCase):
    def test_runs_tools_then_returns_text(self):
        seen = []
        client = FakeClient([[_use("1", "act", {"x": 1})], [{"type": "text", "text": "All done."}]])
        result = subagents.run(_spec(lambda a: seen.append(a) or "ok"), "go", client=client)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "All done.")
        self.assertEqual(seen, [{"x": 1}])
        self.assertEqual(result.tool_calls, [("act", {"x": 1})])
        # The tool result is fed back against the right tool_use id.
        replay = client.requests[1]["messages"][2]["content"][0]
        self.assertEqual((replay["tool_use_id"], replay["content"]), ("1", "ok"))
        # Only the spec's own tools are offered.
        self.assertEqual([t["name"] for t in client.requests[0]["tools"]], ["act"])

    def test_unknown_tool_and_handler_errors_are_reported_not_raised(self):
        def boom(_):
            raise RuntimeError("nope")
        client = FakeClient([[_use("1", "missing", {}), _use("2", "act", {})], [{"type": "text", "text": "x"}]])
        result = subagents.run(_spec(boom), "go", client=client)
        self.assertTrue(result.ok)
        results = client.requests[1]["messages"][2]["content"]
        self.assertTrue(all(r.get("is_error") for r in results))

    def test_round_cap(self):
        client = FakeClient([[_use(str(i), "act", {})] for i in range(3)])
        result = subagents.run(_spec(lambda a: "ok", max_rounds=3), "go", client=client)
        self.assertFalse(result.ok)
        self.assertIn("3 rounds", result.error)

    def test_no_client_is_a_clean_failure(self):
        with patch.object(subagents, "_client", return_value=None):
            result = subagents.run(_spec(lambda a: "ok"), "go")
        self.assertFalse(result.ok)


class JobTests(unittest.TestCase):
    def setUp(self):
        subagents.reset_for_tests()

    def tearDown(self):
        subagents.reset_for_tests()

    def test_job_reports_to_runtime_and_history(self):
        calls = []

        class Runtime:
            def start_task(self, title, detail=""):
                calls.append(("start", title))
                return 7

            def finish_task(self, task_id, *, state, detail=""):
                calls.append(("finish", task_id, state, detail))

            def announce(self, text, *, source, title):
                calls.append(("announce", text))

        subagents.attach(Runtime())
        job = subagents.run_job("mail", "Sorting", lambda: "Sorted 3.", announce=True)
        self.assertEqual(job.state, "done")
        self.assertEqual(calls, [("start", "Sorting"), ("finish", 7, "complete", "Sorted 3."), ("announce", "Sorted 3.")])
        self.assertIn("Sorted 3.", subagents.describe_jobs())

    def test_one_job_per_name(self):
        inner = []
        def work():
            inner.append(subagents.run_job("mail", "again", lambda: "x"))
            return "outer"
        subagents.run_job("mail", "Sorting", work)
        self.assertEqual(inner, [None])

    def test_failure_is_recorded(self):
        def work():
            raise ValueError("bad")
        job = subagents.run_job("mail", "Sorting", work)
        self.assertEqual(job.state, "failed")
        self.assertIn("bad", job.summary)


if __name__ == "__main__":
    unittest.main()
