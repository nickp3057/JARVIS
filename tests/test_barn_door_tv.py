"""Barn Door Protocol puts the TV back the way it found it."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brain import protocols


class BarnDoorTvTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(protocols, "_TV_FILE", Path(tmp.name) / "barn_door_tv.json")
        p.start()
        self.addCleanup(p.stop)
        self.power = patch("integrations.tv_remote.power", return_value=(True, "ok")).start()
        self.addCleanup(patch.stopall)

    def _engage_with_tv(self, state: str) -> None:
        with patch.object(protocols.home_assistant, "_request", return_value=(True, {"state": state})):
            protocols._tv_on(first=True)

    def test_tv_that_was_off_goes_back_off(self) -> None:
        self._engage_with_tv("off")
        self.power.assert_called_once_with(True)
        self.assertTrue(protocols._tv_back())
        self.power.assert_called_with(False)

    def test_tv_that_was_on_stays_on(self) -> None:
        self._engage_with_tv("on")
        self.assertFalse(protocols._tv_back())
        self.assertNotIn(((False,),), self.power.call_args_list)

    def test_second_engage_keeps_the_first_reading(self) -> None:
        self._engage_with_tv("off")
        with patch.object(protocols.home_assistant, "_request", return_value=(True, {"state": "on"})):
            protocols._tv_on(first=False)  # already red: the TV is on because of us
        self.assertTrue(protocols._tv_back())

    def test_stand_down_without_a_record_leaves_the_tv(self) -> None:
        self.assertFalse(protocols._tv_back())
        self.power.assert_not_called()


if __name__ == "__main__":
    unittest.main()
