"""Weather: forgiving geocoding (cached), snapshot and spoken summary, failures."""
import os
import unittest
from unittest.mock import Mock, patch

import requests

from brain import weather

HERRIMAN = {"name": "Herriman", "admin1": "Utah", "country_code": "US", "latitude": 40.5, "longitude": -112.0}
OTHER = {"name": "Herriman", "admin1": "Nowhere", "country_code": "US", "latitude": 1.0, "longitude": 2.0}
FORECAST = {"current": {"temperature_2m": 53.6, "weather_code": 0, "wind_speed_10m": 7.4,
                        "relative_humidity_2m": 31},
            "daily": {"temperature_2m_max": [61.2], "temperature_2m_min": [38.9]}}


def reply(status=200, body=None):  # noqa: ANN001
    response = Mock(status_code=status, text="err")
    response.json.return_value = body
    return response


class WeatherTests(unittest.TestCase):
    def setUp(self) -> None:
        weather._geocoded.clear()
        self.addCleanup(weather._geocoded.clear)

    def routed(self, geocode=None, forecast=FORECAST):  # noqa: ANN001
        def get(url, params, timeout):  # noqa: ANN001
            if url == weather._GEOCODE_URL:
                return reply(body={"results": (geocode or {}).get(params["name"], [])})
            return reply(body=forecast)
        return patch.object(weather.requests, "get", side_effect=get)

    def test_spoken_summary(self) -> None:
        with self.routed({"Herriman, Utah": [HERRIMAN]}):
            text = weather.get_weather("Herriman, Utah")
        self.assertEqual(text, f"In Herriman, Utah, US it's currently 54 degrees and {weather._describe_code(0)}. "
                               "Today's high is 61, low 39. Humidity 31 percent. Winds around 7 miles per hour.")

    def test_forgiving_geocoding_prefers_the_hinted_region(self) -> None:
        with self.routed({"Herriman": [OTHER, HERRIMAN]}):
            lat, lon, name = weather._geocode("Herriman Utah")
        self.assertEqual((lat, name), (40.5, "Herriman, Utah, US"))

    def test_geocodes_are_cached_but_misses_are_not(self) -> None:
        with self.routed({"Herriman": [HERRIMAN]}) as get:
            weather.get_weather_snapshot("Herriman")
            weather.get_weather_snapshot("herriman")
        geocode_calls = [c for c in get.call_args_list if c.args[0] == weather._GEOCODE_URL]
        self.assertEqual(len(geocode_calls), 1)
        with self.routed({}) as get:
            self.assertIn("couldn't find", weather.get_weather("Atlantis"))
            weather.get_weather("Atlantis")
        self.assertEqual(len([c for c in get.call_args_list if c.args[0] == weather._GEOCODE_URL]), 2)

    def test_failures_read_as_sentences(self) -> None:
        with patch.dict(os.environ, {"DEFAULT_LOCATION": ""}):
            self.assertIn("default location", weather.get_weather())
            self.assertFalse(weather.get_weather_snapshot().online)
        with self.routed({"Herriman": [HERRIMAN]}, forecast=None):
            self.assertIn("couldn't reach the weather service", weather.get_weather("Herriman"))
        weather._geocoded.clear()
        with self.routed({"Herriman": [HERRIMAN]}, forecast={"current": {}}):
            snap = weather.get_weather_snapshot("Herriman")
        self.assertEqual((snap.online, snap.location), (False, "Herriman, Utah, US"))
        self.assertIn("came back empty", snap.error)

    def test_http_trouble_is_none_not_an_exception(self) -> None:
        with patch.object(weather.requests, "get", side_effect=requests.ConnectionError("down")), \
                self.assertLogs("jarvis.brain.weather", level="WARNING"):
            self.assertIsNone(weather._get_json("u", {}, "Forecast"))
        with patch.object(weather.requests, "get", return_value=reply(500)), \
                self.assertLogs("jarvis.brain.weather", level="WARNING"):
            self.assertIsNone(weather._get_json("u", {}, "Forecast"))
        bad = reply()
        bad.json.side_effect = ValueError
        with patch.object(weather.requests, "get", return_value=bad):
            self.assertIsNone(weather._get_json("u", {}, "Forecast"))

    def test_unknown_codes(self) -> None:
        self.assertEqual(weather._describe_code(12345), "unsettled weather")


if __name__ == "__main__":
    unittest.main()
