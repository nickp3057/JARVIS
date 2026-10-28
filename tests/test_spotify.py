"""Spotify commands against a fake Spotify Web API client."""
import os
import unittest
from unittest.mock import MagicMock, patch

from integrations import spotify

PHONE = {"id": "p1", "name": "Nick's iPhone", "type": "Smartphone", "is_active": False}
TV = {"id": "t1", "name": "Nick's Room TV", "type": "TV", "is_active": True}
TRACK = {"uri": "spotify:track:1", "name": "Back in Black", "artists": [{"name": "AC/DC"}],
         "album": {"name": "Back in Black", "images": [{"url": "http://art"}]}}


class SpotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sp = MagicMock()
        self.sp.devices.return_value = {"devices": [PHONE, TV]}
        self.sp.search.side_effect = lambda q, type, limit: (
            {"tracks": {"items": [TRACK]}} if type == "track" and q == "back in black" else
            {"playlists": {"items": [{"uri": "spotify:playlist:9", "name": "Chill"}]}} if type == "playlist"
            and q == "chill" else {type + "s": {"items": []}})
        patch.object(spotify, "_get_client", return_value=self.sp).start()
        patch.dict(os.environ, {"SPOTIFY_PREFERRED_DEVICE": ""}).start()
        self.addCleanup(patch.stopall)

    def test_play_a_track_on_the_active_device(self) -> None:
        self.assertEqual(spotify.play_music("back in black"), "Playing Back in Black by AC/DC on Nick's Room TV.")
        self.sp.start_playback.assert_called_once_with(device_id="t1", uris=["spotify:track:1"])

    def test_preferred_device_is_read_live(self) -> None:
        os.environ["SPOTIFY_PREFERRED_DEVICE"] = "iphone"
        spotify.play_music("back in black")
        self.assertEqual(self.sp.start_playback.call_args.kwargs["device_id"], "p1")
        os.environ["SPOTIFY_PREFERRED_DEVICE"] = "kitchen"  # not around: the active one
        spotify.play_music("back in black")
        self.assertEqual(self.sp.start_playback.call_args.kwargs["device_id"], "t1")

    def test_playlist_fallback_and_nothing_found(self) -> None:
        self.assertEqual(spotify.play_music("chill"), "Playing the playlist 'Chill' on Nick's Room TV.")
        self.sp.start_playback.assert_called_with(device_id="t1", context_uri="spotify:playlist:9")
        self.assertIn("couldn't find anything", spotify.play_music("zzz"))
        self.assertIn("something to play", spotify.play_music("  "))

    def test_no_devices(self) -> None:
        self.sp.devices.return_value = {"devices": []}
        for reply in (spotify.play_music("back in black"), spotify.resume_music(),
                      spotify.transfer_playback("tv")):
            self.assertIn("No Spotify device is available", reply)
        self.assertIn("No Spotify devices are visible", spotify.list_speakers())

    def test_transport_and_volume(self) -> None:
        self.assertEqual(spotify.pause_music(), "Paused.")
        self.assertEqual(spotify.resume_music(), "Playing.")
        self.assertEqual(spotify.skip_track(), "Skipped.")
        self.assertEqual(spotify.previous_track(), "Going back.")
        self.assertEqual(spotify.set_volume(140), "Volume 100.")
        self.sp.volume.assert_called_with(100)
        self.assertEqual(spotify.set_volume(-3), "Volume 0.")

    def test_now_playing(self) -> None:
        self.sp.current_playback.return_value = None
        self.assertEqual(spotify.current_track(), "Nothing is playing, sir.")
        self.assertIsNone(spotify.current_track_structured())
        self.sp.current_playback.return_value = {"item": TRACK, "is_playing": True}
        self.assertEqual(spotify.current_track(), "Back in Black by AC/DC.")
        self.assertEqual(spotify.current_track_structured(), {
            "track": "Back in Black", "artist": "AC/DC", "album": "Back in Black",
            "album_art_url": "http://art", "is_playing": True})

    def test_speakers_and_transfer(self) -> None:
        self.assertEqual(spotify.list_speakers(), "Available Spotify devices: Nick's iPhone, a smartphone; "
                                                  "Nick's Room TV (active), a tv.")
        self.assertEqual(spotify.transfer_playback("iphone"), "Playback moved to Nick's iPhone.")
        self.sp.transfer_playback.assert_called_once_with("p1", force_play=True)
        self.assertIn("Available: Nick's iPhone, Nick's Room TV", spotify.transfer_playback("kitchen"))
        self.assertIn("Which device", spotify.transfer_playback(""))

    def test_api_errors_become_sentences(self) -> None:
        self.sp.pause_playback.side_effect = RuntimeError("401 token expired")
        with self.assertLogs("jarvis.integrations.spotify", level="ERROR"):
            self.assertEqual(spotify.pause_music(), "Spotify error: 401 token expired")
        self.sp.devices.side_effect = RuntimeError("down")
        with self.assertLogs("jarvis.integrations.spotify", level="ERROR"):
            self.assertIn("No Spotify device", spotify.resume_music())


class NotConfiguredTests(unittest.TestCase):
    def test_every_command_says_so(self) -> None:
        with patch.object(spotify, "_get_client", return_value=None):
            for reply in (spotify.play_music("x"), spotify.pause_music(), spotify.resume_music(),
                          spotify.skip_track(), spotify.previous_track(), spotify.current_track(),
                          spotify.set_volume(5), spotify.list_speakers(), spotify.transfer_playback("x")):
                self.assertEqual(reply, "Spotify isn't configured, sir.")
            self.assertFalse(spotify.is_available())

    def test_client_init_is_tried_once(self) -> None:
        with patch.object(spotify, "_client", None), patch.object(spotify, "_client_init_attempted", False), \
                patch.object(spotify, "CLIENT_ID", ""), self.assertLogs("jarvis.integrations.spotify", "WARNING"):
            self.assertIsNone(spotify._get_client())
            self.assertIsNone(spotify._get_client())  # no second attempt, no second warning


if __name__ == "__main__":
    unittest.main()
