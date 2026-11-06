"""Companion Media tab: search -> request (proposal) -> confirm (add)."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from brain import confirm_gate
from integrations import media_selections, radarr
from integrations import servarr_common as servarr

GOLD = {"title": "Gold", "year": 2016, "tmdbId": 339964, "overview": "Prospector.",
        "images": [{"coverType": "poster", "remoteUrl": "https://img/gold.jpg"}]}
OTHER = {"title": "Gold", "year": 2014, "tmdbId": 1}
OWNED = {"title": "Heat", "year": 1995, "tmdbId": 949, "id": 3, "hasFile": True}


class MediaRequestApiTests(unittest.TestCase):
    def setUp(self) -> None:
        media_selections.clear_for_tests()
        confirm_gate.clear_for_tests()
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin", "RADARR_URL": "http://nas:7878",
                                      "RADARR_API_KEY": "k", "RADARR_DEFAULT_QUALITY": "HD-720p"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(server.app)
        self.addCleanup(self.client.close)
        self.h = {"Authorization": "Bearer admin"}
        lookup = patch.object(radarr, "_lookup", side_effect=self.fake_lookup)
        lookup.start()
        self.addCleanup(lookup.stop)
        q = patch.object(radarr.servarr, "resolve_quality", return_value=(4, "HD-720p"))
        r = patch.object(radarr.servarr, "resolve_root_folder", return_value=("/m", "/m"))
        self.post = patch.object(radarr.servarr, "call", return_value=servarr.Result(True, {"id": 9}, "ok"))
        for x in (q, r):
            x.start()
            self.addCleanup(x.stop)
        self.call = self.post.start()
        self.addCleanup(self.post.stop)

    @staticmethod
    def fake_lookup(term):
        if term.startswith("tmdb:"):
            wanted = int(term[5:])
            return True, [m for m in (GOLD, OTHER, OWNED) if m["tmdbId"] == wanted]
        return True, [OWNED] if term == "Heat" else [GOLD, OTHER]

    def test_auth_required(self) -> None:
        self.assertEqual(self.client.get("/api/media/search?q=Gold").status_code, 401)

    def test_search_returns_structured_results(self) -> None:
        body = self.client.get("/api/media/search?q=Gold&kind=movie", headers=self.h).json()
        self.assertEqual([r["year"] for r in body["results"]], [2016, 2014])
        self.assertEqual(body["results"][0]["poster"], "https://img/gold.jpg")
        self.assertFalse(body["results"][0]["in_library"])

    def test_two_steps_and_only_one_write(self) -> None:
        self.client.get("/api/media/search?q=Gold&kind=movie", headers=self.h)
        proposal = self.client.post("/api/media/requests", headers=self.h, json={"kind": "movie", "id": 339964}).json()
        self.assertEqual(proposal["status"], "needs_confirmation")
        self.assertIn("Gold (2016)", proposal["description"])
        self.assertIn("HD-720p", proposal["description"])
        self.call.assert_not_called()
        done = self.client.post("/api/media/requests/confirm", headers=self.h, json={"code": proposal["code"]}).json()
        self.assertEqual(done["status"], "added")
        self.call.assert_called_once()
        again = self.client.post("/api/media/requests/confirm", headers=self.h, json={"code": proposal["code"]}).json()
        self.assertEqual(again["status"], "expired")
        self.call.assert_called_once()

    def test_wrong_code_and_unsearched_id_rejected(self) -> None:
        self.client.get("/api/media/search?q=Gold&kind=movie", headers=self.h)
        bad = self.client.post("/api/media/requests", headers=self.h, json={"kind": "movie", "id": 777}).json()
        self.assertEqual(bad["status"], "invalid")
        proposal = self.client.post("/api/media/requests", headers=self.h, json={"kind": "movie", "id": 339964}).json()
        wrong = "0000" if proposal["code"] != "0000" else "1111"
        self.assertEqual(self.client.post("/api/media/requests/confirm", headers=self.h, json={"code": wrong}).json()["status"], "invalid")
        self.call.assert_not_called()

    def test_owned_title_not_requested(self) -> None:
        self.client.get("/api/media/search?q=Heat&kind=movie", headers=self.h)
        self.assertEqual(self.client.post("/api/media/requests", headers=self.h, json={"kind": "movie", "id": 949}).json()["status"], "exists")

    def test_chat_yes_cannot_approve_app_request(self) -> None:
        self.client.get("/api/media/search?q=Gold&kind=movie", headers=self.h)
        self.client.post("/api/media/requests", headers=self.h, json={"kind": "movie", "id": 339964})
        self.assertIsNone(confirm_gate.conversation_action("yes", "some-chat-session"))
        self.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class DefaultProfileTests(unittest.TestCase):
    def test_movies_default_to_standard_and_shows_to_tv(self) -> None:
        from integrations import servarr_common
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RADARR_DEFAULT_QUALITY", None)
            os.environ.pop("SONARR_DEFAULT_QUALITY", None)
            self.assertEqual(servarr_common.default_quality("radarr"), "Standard")
            self.assertEqual(servarr_common.default_quality("sonarr"), "TV")
        with patch.dict(os.environ, {"RADARR_DEFAULT_QUALITY": "Favorite 4K"}):
            self.assertEqual(servarr_common.default_quality("radarr"), "Favorite 4K")


class ShowRequestTests(unittest.TestCase):
    """Media-tab show adds grab season 1 and search for it (not 'future')."""

    SILO = {"title": "Silo", "year": 2023, "tvdbId": 403245, "seasons": []}

    def setUp(self) -> None:
        media_selections.clear_for_tests()
        confirm_gate.clear_for_tests()
        env = patch.dict(os.environ, {"SONARR_URL": "http://nas:8989", "SONARR_API_KEY": "k",
                                      "SONARR_MONITOR_STRATEGY": "future"})
        env.start()
        self.addCleanup(env.stop)
        from integrations import sonarr
        patches = [
            patch.object(sonarr, "_lookup", return_value=(True, [self.SILO])),
            patch.object(sonarr, "_language_profile_id", return_value=None),
            patch.object(sonarr.servarr, "resolve_quality", return_value=(2, "TV")),
            patch.object(sonarr.servarr, "resolve_root_folder", return_value=("/tv", "/tv")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.call = patch.object(sonarr.servarr, "call", return_value=servarr.Result(True, {"id": 24}, "ok")).start()
        self.addCleanup(patch.stopall)

    def test_show_add_monitors_first_season_and_searches(self) -> None:
        from integrations import media_requests
        media_requests.search("Silo", "show")
        proposal = media_requests.request("show", 403245)
        self.assertIn("season 1", proposal["description"])
        done = media_requests.confirm(proposal["code"])
        self.assertEqual(done["status"], "added", done)
        self.assertIn("season 1", done["message"])
        body = self.call.call_args.kwargs["json"]
        self.assertEqual(body["addOptions"], {"searchForMissingEpisodes": True, "monitor": "firstSeason"})
