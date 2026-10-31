"""The room camera: only Nick's phone turns the lens on, and a kill locks it."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from brain import camera, tools


class CameraStateTests(unittest.TestCase):
    def setUp(self) -> None:
        camera.reset_for_tests()
        self.addCleanup(camera.reset_for_tests)

    def test_lens_wanted_only_with_a_live_viewer(self) -> None:
        self.assertFalse(camera.wanted(now=100))
        camera.view_heartbeat(now=100)
        self.assertTrue(camera.wanted(now=102))
        self.assertFalse(camera.wanted(now=100 + camera.VIEWER_TTL + 1))  # heartbeat lapsed

    def test_kill_locks_the_lens_off_regardless_of_viewers(self) -> None:
        camera.view_heartbeat(now=100)
        camera.kill(now=100)
        self.assertTrue(camera.killed_for(now=100) > 0)
        camera.view_heartbeat(now=150)  # someone still asking
        self.assertFalse(camera.wanted(now=150))  # but locked off
        self.assertFalse(camera.put_frame(b"jpeg", now=150))  # frames refused too
        self.assertIsNone(camera.get_frame(now=150))
        after = 100 + camera.KILL_SECONDS + 1  # once the lockout lifts, a viewer works again
        camera.view_heartbeat(now=after)
        self.assertTrue(camera.wanted(now=after))

    def test_frames_are_served_fresh_and_expire(self) -> None:
        camera.view_heartbeat(now=100)
        self.assertTrue(camera.put_frame(b"jpeg", now=100))
        self.assertEqual(camera.get_frame(now=101), b"jpeg")
        self.assertIsNone(camera.get_frame(now=100 + camera.FRAME_TTL + 1))  # stale


class CameraEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        camera.reset_for_tests()
        self.addCleanup(camera.reset_for_tests)
        env = patch.dict("os.environ", {"JARVIS_API_TOKEN": "t"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(server.app)
        self.headers = {"Authorization": "Bearer t"}

    def test_view_then_frame_roundtrip(self) -> None:
        self.assertEqual(self.client.post("/api/camera/view", headers=self.headers).json()["viewing"], True)
        up = self.client.post("/api/camera/frame", headers=self.headers, content=b"\xff\xd8jpeg")
        self.assertEqual(up.status_code, 200)
        got = self.client.get("/api/camera/frame", headers=self.headers)
        self.assertEqual((got.status_code, got.content), (200, b"\xff\xd8jpeg"))

    def test_kill_endpoint_and_tool_block_frames(self) -> None:
        self.client.post("/api/camera/view", headers=self.headers)
        killed = self.client.post("/api/camera/kill", headers=self.headers).json()
        self.assertTrue(killed["killed"] and killed["kill_seconds_left"] > 0)
        self.assertEqual(self.client.post("/api/camera/frame", headers=self.headers, content=b"x").status_code, 409)
        self.assertEqual(self.client.get("/api/camera/frame", headers=self.headers).status_code, 404)

    def test_frame_requires_auth(self) -> None:
        self.assertEqual(self.client.get("/api/camera/frame").status_code, 401)

    def test_kill_camera_tool(self) -> None:
        self.assertIn("locked off", tools.execute_tool("kill_camera", {}))
        self.assertTrue(camera.killed_for() > 0)


if __name__ == "__main__":
    unittest.main()
