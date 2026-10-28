"""JARVIS's browser and web tools: guardrails, the plain-HTTP reader, the
server tool versions, and the agent's pause_turn / rejected-tool handling.
No network or Chromium: a fake engine and fake HTTP stand in."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx

import brain.agent as agent_mod
from brain import browser, tools
from brain.agent import JarvisAgent


def element(ref, tag="a", **extra):  # noqa: ANN001, ANN003, ANN201
    base = {"ref": ref, "tag": tag, "type": "", "label": f"thing {ref}", "href": "", "name": "",
            "autocomplete": "", "in_form": False}
    base.update(extra)
    return base


class FakeEngine:
    interactive = True

    def __init__(self) -> None:
        self.page = browser._Page("https://shop.example/", "Shop", "Welcome to the shop.", [
            element(1, href="https://shop.example/deals"),
            element(2, "input", type="text", name="q", label="Message", in_form=True, form_method="post"),
            element(7, "input", type="search", name="q", label="Search", in_form=True, form_method="get"),
            element(3, "input", type="password", name="pw", label="Password", in_form=True),
            element(4, "button", type="", label="Sign in", in_form=True),
            element(5, "input", type="text", name="cc-number", label="Card number", in_form=True),
            element(6, "select", label="Size", options=["S", "M", "L"]),
        ])
        self.calls: list[tuple] = []

    def open(self, url):  # noqa: ANN001, ANN201
        self.calls.append(("open", url))
        return self.page

    def click(self, el):  # noqa: ANN001, ANN201
        self.calls.append(("click", el["ref"]))
        return self.page

    def type(self, el, text, enter):  # noqa: ANN001, ANN201
        self.calls.append(("type", el["ref"], text, enter))
        return self.page

    def select(self, el, option):  # noqa: ANN001, ANN201
        self.calls.append(("select", el["ref"], option))
        return self.page

    def scroll(self):  # noqa: ANN201
        return self.page

    def back(self):  # noqa: ANN201
        return self.page

    def close(self) -> None:
        self.calls.append(("close",))


class BrowserGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()
        self.browser = browser.Browser(engine_factory=lambda: self.engine)
        self.addCleanup(self.browser.close)
        self.browser.open("https://shop.example/")

    def test_page_lists_numbered_elements_and_flags_untrusted_text(self) -> None:
        text = self.browser.read()
        self.assertIn("untrusted", text)
        self.assertIn('[1] a "thing 1" -> https://shop.example/deals', text)
        self.assertIn("options: S | M | L", text)
        self.assertIn("[3] input[password]", text)
        self.assertIn("sensitive", text.split("[3]")[1].splitlines()[0])

    def test_links_and_fields_work(self) -> None:
        self.browser.act("click", 1)
        self.browser.act("type", 2, "rtx 5090")
        self.browser.act("select", 6, "M")
        self.assertEqual(self.engine.calls[1:], [("click", 1), ("type", 2, "rtx 5090", False), ("select", 6, "M")])

    def test_never_types_secrets(self) -> None:
        for ref in (3, 5):
            reply = self.browser.act("type", ref, "hunter2")
            self.assertIn("don't type", reply)
        self.assertFalse(any(call[0] == "type" for call in self.engine.calls))

    def test_submitting_a_form_needs_confirmation(self) -> None:
        self.assertIn("ask him", self.browser.act("click", 4))
        self.assertIn("Ask Nick", self.browser.act("type_and_enter", 2, "hi"))
        self.assertEqual(self.engine.calls, [("open", "https://shop.example/")])
        self.browser.act("click", 4, confirmed=True)
        self.assertEqual(self.engine.calls[-1], ("click", 4))

    def test_search_boxes_submit_without_asking(self) -> None:
        self.browser.act("type_and_enter", 7, "gpus")
        self.assertEqual(self.engine.calls[-1], ("type", 7, "gpus", True))

    def test_bad_refs_and_actions_are_explained(self) -> None:
        with self.assertRaisesRegex(ValueError, r"no element \[99\]"):
            self.browser.act("click", 99)
        self.assertIn("Unknown browser action", self.browser.act("dance", 1))


class UrlGuardTests(unittest.TestCase):
    def test_private_and_local_addresses_are_refused(self) -> None:
        for url in ("http://localhost:8123", "http://10.0.0.147:7878", "file:///etc/passwd",
                    "http://user:pw@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                browser._check_url(url)

    def test_bare_domains_get_https(self) -> None:
        with patch("brain.price_watch._validate_public_url"):
            self.assertEqual(browser._check_url("example.com/a"), "https://example.com/a")

    def test_module_helpers_never_raise(self) -> None:
        self.assertIn("won't open", browser.open_page("http://127.0.0.1/"))


class HttpReaderTests(unittest.TestCase):
    HTML = """<html><head><title> Deals | Shop </title><style>.x{}</style><script>var a=1</script></head>
    <body><h1>Today's deals</h1><p>RTX 5090 &amp; more</p><a href="/gpu">Graphics cards</a>
    <a href="#top">Top</a><a href="javascript:void(0)">JS</a><nav><a href="https://other.example/x">Other</a></nav>
    </body></html>"""

    def test_text_links_and_title(self) -> None:
        engine = browser._HttpEngine()
        with patch.object(browser, "_fetch_html", return_value=("https://shop.example/deals", self.HTML)):
            page = engine.open("https://shop.example/deals")
        self.assertEqual(page.title, "Deals | Shop")
        self.assertIn("Today's deals", page.text)
        self.assertIn("RTX 5090 & more", page.text)
        self.assertNotIn("var a", page.text)
        self.assertEqual([(e["label"], e["href"]) for e in page.elements],
                         [("Graphics cards", "https://shop.example/gpu"), ("Other", "https://other.example/x")])

    def test_plain_engine_follows_links_but_explains_the_rest(self) -> None:
        fake = browser._HttpEngine()
        fake.page = browser._Page("https://a.example/", "A", "hi", [element(1, "button", label="Go")])
        b = browser.Browser(engine_factory=lambda: fake)
        self.addCleanup(b.close)
        b._engine = fake
        self.assertIn("playwright", b.act("click", 1))
        self.assertIn("playwright", b.act("type", 1, "x"))

    def test_redirect_to_a_private_address_is_refused(self) -> None:
        hop = Mock(status_code=302, headers={"Location": "http://192.168.1.1/admin"})
        with patch("brain.price_watch._validate_public_url",
                   side_effect=[None, ValueError("private")]), \
                patch.object(browser.requests, "get", return_value=hop), self.assertRaises(ValueError):
            browser._fetch_html("https://shop.example/")


class WebToolVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        patch.object(tools, "_REJECTED_SERVER_TOOLS", set()).start()
        self.addCleanup(patch.stopall)

    def _server(self, model):  # noqa: ANN001, ANN202
        return {s["name"]: s["type"] for s in tools.get_tool_specs(model) if "type" in s}

    def test_dynamic_filtering_versions_only_where_supported(self) -> None:
        with patch.dict(os.environ, {"WEB_SEARCH_ENABLED": "1", "WEB_FETCH_ENABLED": "1"}):
            self.assertEqual(self._server("claude-sonnet-5-5"),
                             {"web_search": "web_search_20260209", "web_fetch": "web_fetch_20260209"})
            self.assertEqual(self._server("claude-opus-4-6")["web_fetch"], "web_fetch_20260209")
            for basic in ("claude-haiku-5-5", "claude-sonnet-4-5-20250929", "claude-opus-4-20250514", None):
                self.assertEqual(self._server(basic),
                                 {"web_search": "web_search_20250305", "web_fetch": "web_fetch_20250910"}, basic)

    def test_fetch_can_be_turned_off_and_rejected_tools_drop(self) -> None:
        with patch.dict(os.environ, {"WEB_FETCH_ENABLED": "0", "WEB_SEARCH_ENABLED": "1"}):
            self.assertEqual(set(self._server("claude-haiku-5-5")), {"web_search"})
        with patch.dict(os.environ, {"WEB_FETCH_ENABLED": "1", "WEB_SEARCH_ENABLED": "1"}):
            tools.reject_server_tool("web_fetch")
            self.assertEqual(set(self._server("claude-haiku-5-5")), {"web_search"})


def _agent(client):  # noqa: ANN001, ANN202
    agent = object.__new__(JarvisAgent)
    agent._client = client
    agent._session_id = "s"
    agent._recent_tool_context = {}
    agent._last_turn_tools = []
    agent.spoken_turn = False
    return agent


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        for target, value in ((agent_mod, "_model"), ):
            patch.object(target, value, return_value="claude-haiku-5-5").start()
        patch.object(JarvisAgent, "_prepare_turn", return_value=([], [{"role": "user", "content": "hi"}], 0)).start()
        patch.object(JarvisAgent, "_save_tool_context").start()
        patch.object(agent_mod.model_select, "request_kwargs", return_value={}).start()
        patch.object(tools, "_REJECTED_SERVER_TOOLS", set()).start()
        self.addCleanup(patch.stopall)

    def test_pause_turn_resumes_without_a_new_user_message(self) -> None:
        paused = SimpleNamespace(stop_reason="pause_turn", usage=None, content=[
            SimpleNamespace(type="server_tool_use", id="s1", name="web_search", input={"query": "x"})])
        done = SimpleNamespace(stop_reason="end_turn", usage=None, content=[SimpleNamespace(type="text", text="Found it, sir.")])
        client = Mock()
        client.messages.create.side_effect = [paused, done]
        with patch.object(agent_mod, "_serialize_content_block", side_effect=lambda b: vars(b)):
            self.assertEqual(_agent(client)._run_tool_loop(), "Found it, sir.")
        second = client.messages.create.call_args_list[1].kwargs["messages"]
        self.assertEqual(second[-1]["role"], "assistant")
        self.assertEqual(second[-1]["content"][0]["type"], "server_tool_use")

    def test_a_rejected_web_tool_is_dropped_and_the_turn_still_answers(self) -> None:
        response = httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
        error = anthropic.BadRequestError("tools.12: web_fetch is not available", response=response, body=None)
        done = SimpleNamespace(stop_reason="end_turn", usage=None, content=[SimpleNamespace(type="text", text="Evening.")])
        client = Mock()
        client.messages.create.side_effect = [error, done]
        with patch.dict(os.environ, {"WEB_FETCH_ENABLED": "1", "WEB_SEARCH_ENABLED": "1"}), \
                self.assertLogs("jarvis.brain.tools", level="WARNING"):
            self.assertEqual(_agent(client)._run_tool_loop(), "Evening.")
        retried = {t["name"] for t in client.messages.create.call_args_list[1].kwargs["tools"]}
        self.assertNotIn("web_fetch", retried)
        self.assertIn("web_search", retried)


class ToolDispatchTests(unittest.TestCase):
    def test_tools_route_to_the_browser(self) -> None:
        with patch.object(browser, "open_page", return_value="page") as opened, \
                patch.object(browser, "act", return_value="acted") as acted:
            self.assertEqual(tools.execute_tool("browser_open", {"url": "https://a.example"}), "page")
            self.assertEqual(tools.execute_tool("browser_act", {"action": "click", "ref": 2}), "acted")
        opened.assert_called_once_with("https://a.example")
        acted.assert_called_once_with("click", 2, "", False)


if __name__ == "__main__":
    unittest.main()
