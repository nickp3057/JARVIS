"""The tool catalogue Claude sees must match what JARVIS can run."""
import re
import unittest
from unittest.mock import patch

from brain import tools

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_JSON_TYPES = {"string", "integer", "number", "boolean", "array", "object"}


class ToolCatalogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = [s for s in tools.get_tool_specs() if s.get("type") != "web_search_20250305"
                      and s["name"] != "web_search"]

    def test_every_advertised_tool_runs_and_every_handler_is_advertised(self) -> None:
        names = [s["name"] for s in self.specs]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        self.assertEqual(set(names), set(tools._DISPATCH))

    def test_schemas_are_well_formed(self) -> None:
        for spec in self.specs:
            with self.subTest(tool=spec["name"]):
                self.assertRegex(spec["name"], _NAME)
                self.assertGreater(len(spec.get("description", "").strip()), 20)
                schema = spec["input_schema"]
                self.assertEqual(schema["type"], "object")
                props = schema.get("properties", {})
                for required in schema.get("required", []):
                    self.assertIn(required, props)
                for prop, body in props.items():
                    kind = body.get("type")
                    kinds = kind if isinstance(kind, list) else [kind]
                    if kind is not None:
                        self.assertTrue(set(kinds) <= _JSON_TYPES | {"null"}, f"{prop}: {kind}")
                    if "enum" in body:
                        self.assertTrue(body["enum"], f"{prop}: empty enum")

    def test_only_the_last_tool_carries_the_cache_marker(self) -> None:
        specs = tools.get_tool_specs()
        marked = [s["name"] for s in specs if "cache_control" in s]
        self.assertEqual(len(marked), 1)
        self.assertNotIn("cache_control", tools._CUSTOM_TOOLS[-1])  # the shared list isn't mutated


class ExecuteToolTests(unittest.TestCase):
    def test_unknown_tool(self) -> None:
        with self.assertLogs("jarvis.brain.tools", level="WARNING"):
            self.assertEqual(tools.execute_tool("nope", {}), "Tool error: no such tool 'nope'.")

    def test_a_crashing_tool_is_reported_and_scored(self) -> None:
        def boom(_args):  # noqa: ANN001
            raise ValueError("bad input")
        with patch.dict(tools._DISPATCH, {"boom": boom}), patch.object(tools.tool_health, "record") as record, \
                self.assertLogs("jarvis.brain.tools", level="ERROR"):
            reply = tools.execute_tool("boom", {"x": 1})
        self.assertEqual(reply, "Tool error while running boom: bad input")
        self.assertFalse(record.call_args.kwargs["ok"])

    def test_session_id_is_passed_without_touching_the_callers_args(self) -> None:
        seen = {}
        args = {"a": 1}
        with patch.dict(tools._DISPATCH, {"echo": lambda a: seen.update(a) or "ok"}), \
                patch.object(tools.tool_health, "record"):
            self.assertEqual(tools.execute_tool("echo", args, session_id="s1"), "ok")
        self.assertEqual(seen, {"a": 1, "_jarvis_session_id": "s1"})
        self.assertEqual(args, {"a": 1})

    def test_bookkeeping_failure_never_breaks_a_tool(self) -> None:
        with patch.dict(tools._DISPATCH, {"echo": lambda a: "ok"}), \
                patch.object(tools.tool_health, "record", side_effect=RuntimeError("ledger down")):
            self.assertEqual(tools.execute_tool("echo", {}), "ok")


if __name__ == "__main__":
    unittest.main()
