import os
import unittest
from unittest.mock import patch

from brain import traffic


class TrafficTests(unittest.TestCase):
    def test_client_nodes(self) -> None:
        self.assertEqual(traffic.client_node(None, "ipad"), "ipad")
        self.assertEqual(traffic.client_node(None, "ipad-nightstand"), "nightstand")
        self.assertEqual(traffic.client_node(None, "iphone"), "iphone")
        self.assertEqual(traffic.client_node("jarvis-tv", None), "tv")
        self.assertEqual(traffic.client_node(None, None), "iphone")

    def test_host_nodes(self) -> None:
        with patch.dict(os.environ, {"HOME_ASSISTANT_URL": "http://nas.lan:8123", "PLEX_URL": ""}):
            self.assertEqual(traffic.host_node("http://nas.lan:8123/api/states?x=secret"),
                             ("nas", "home assistant /api/states"))
            self.assertEqual(traffic.host_node("http://100.97.1.2:7878/api"), ("nas", "radarr /api"))
            self.assertEqual(traffic.host_node("https://api.anthropic.com/v1/messages")[0], "internet")
            self.assertEqual(traffic.host_node("https://api.push.apple.com/3/device/" + "ab" * 32),
                             ("internet", "api.push.apple.com /3/device/…"))
            self.assertEqual(traffic.host_node("http://127.0.0.1:11434/x")[0], "core")

    def test_ring_buffer_and_since(self) -> None:
        start = traffic.seq()
        traffic.record("core", "nas", "plex")
        traffic.record("core", "core", "ignored")
        got = traffic.since(start)
        self.assertEqual([(g["from"], g["to"], g["label"]) for g in got], [("core", "nas", "plex")])


if __name__ == "__main__":
    unittest.main()
