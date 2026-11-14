"""Answer screens: the weather_today data, card builders, the bus's card
channel and the SSE stream's card frames. No network: requests is mocked."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

import app.server as server
from brain import answer_cards, event_bus, timers, tools, weather

HERRIMAN = {"name": "Herriman", "admin1": "Utah", "country_code": "US", "latitude": 40.5, "longitude": -112.0}
# Local times (utc_offset -7 h): 2026-11-13, hourly 00:00 .. 2026-11-14 23:00.
OFFSET = -7 * 3600
DAY0 = datetime(2026, 11, 13)


def _hourly_forecast() -> dict:
    times = [(DAY0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(48)]
    return {
        "utc_offset_seconds": OFFSET,
        "current": {"temperature_2m": 53.6, "weather_code": 2, "wind_speed_10m": 7.4,
                    "relative_humidity_2m": 31, "is_day": 1, "wind_gusts_10m": 22.0},
        "daily": {"time": ["2026-11-13", "2026-11-14"], "temperature_2m_max": [61.2, 50],
                  "temperature_2m_min": [38.9, 30], "sunrise": ["2026-11-13T07:15", "x"],
                  "sunset": ["2026-11-13T17:10", "x"], "precipitation_probability_max": [60, 10]},
        "hourly": {"time": times, "temperature_2m": [40 + h % 24 for h in range(48)],
                   "precipitation_probability": [60 if h == 16 else 5 for h in range(48)],
                   "weather_code": [61 if h == 16 else 0 for h in range(48)],
                   "is_day": [1 if 7 <= h % 24 < 17 else 0 for h in range(48)]},
    }


def _epoch(local_hour: float) -> float:
    """Unix time of 2026-11-13 local_hour:00 at UTC-7."""
    return datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp() - OFFSET + local_hour * 3600


def _routed(forecast: dict):
    def get(url, params, timeout):  # noqa: ANN001
        response = Mock(status_code=200, text="")
        if url == weather._GEOCODE_URL:
            response.json.return_value = {"results": [HERRIMAN]}
        else:
            assert params.get("hourly"), "the answer screen needs the hourly fetch"
            response.json.return_value = forecast
        return response
    return patch.object(weather.requests, "get", side_effect=get)


class WeatherTodayTests(unittest.TestCase):
    def setUp(self) -> None:
        weather._geocoded.clear()
        self.addCleanup(weather._geocoded.clear)

    def test_rest_of_today_hour_by_hour(self) -> None:
        with _routed(_hourly_forecast()):
            data = weather.get_weather_today("Herriman, Utah", now=_epoch(14.5))
        self.assertTrue(data["online"])
        self.assertEqual((data["temp_f"], data["high_f"], data["low_f"]), (54, 61, 39))
        self.assertEqual((data["condition"], data["wind_mph"], data["gust_mph"], data["rain_chance"]),
                         ("partly", 7, 22, 60))
        self.assertAlmostEqual(data["sunset"], _epoch(17 + 10 / 60))
        hours = data["hours"]
        # 14:00 (still running) to 23:00: ten hours, the current one first.
        self.assertEqual(len(hours), 10)
        self.assertAlmostEqual(hours[0]["at"], _epoch(14))
        rainy = hours[2]
        self.assertEqual((rainy["rain_chance"], rainy["condition"], rainy["temp_f"]), (60, "rain", 56))
        self.assertFalse(hours[-1]["is_day"])

    def test_late_evening_runs_on_into_the_night(self) -> None:
        with _routed(_hourly_forecast()):
            data = weather.get_weather_today("Herriman", now=_epoch(22.2))
        self.assertEqual(len(data["hours"]), weather._MIN_HOURS)

    def test_unreachable_service_is_an_error_not_a_card(self) -> None:
        with _routed({"current": {}}):
            data = weather.get_weather_today("Herriman")
        self.assertFalse(data["online"])
        self.assertIn("couldn't reach", data["error"])

    def test_conditions(self) -> None:
        self.assertEqual([weather.condition(c) for c in (0, 2, 3, 45, 53, 63, 73, 95, 4)],
                         ["clear", "partly", "cloudy", "fog", "drizzle", "rain", "snow", "storm", "cloudy"])


class ShowScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.addCleanup(event_bus.reset_for_tests)
        self.shown = []
        event_bus.subscribe_cards("test", self.shown.append)

    def test_weather_card_published_and_facts_returned(self) -> None:
        data = {"online": True, "location": "Herriman, Utah, US", "temp_f": 54, "description": "partly cloudy",
                "high_f": 61, "low_f": 39, "wind_mph": 7, "gust_mph": 22, "sunset": None,
                "hours": [{"at": _epoch(16), "rain_chance": 60}, {"at": _epoch(17), "rain_chance": 5}]}
        with patch.object(weather, "get_weather_today", return_value=data) as fetch:
            reply = tools.execute_tool("show_screen", {"card": "weather_today", "location": "Tokyo"})
        fetch.assert_called_once_with("Tokyo")
        self.assertIn("54 degrees and partly cloudy", reply)
        self.assertIn("Rain chance peaks at 60 percent", reply)
        self.assertIn("gusting 22", reply)
        self.assertIn("On screen now", reply)
        (card,) = self.shown
        self.assertEqual((card.card, card.title, card.data["temp_f"]), ("weather_today", "Today · Herriman", 54))
        self.assertEqual(event_bus.live_cards(), [card])

    def test_errors_and_empty_answers_show_nothing(self) -> None:
        with patch.object(weather, "get_weather_today", return_value={"online": False, "error": "No place."}):
            self.assertEqual(answer_cards.show("weather_today"), "No place.")
        with patch.object(timers, "active_timers", return_value=[]):
            self.assertEqual(answer_cards.show("timers"), "You have no active timers.")
        self.assertIn("no screen called", answer_cards.show("tv_remote"))
        self.assertEqual(self.shown, [])

    def test_timers_card(self) -> None:
        running = timers.Timer("abc123", expires_at=9e12, message="Pasta")
        with patch.object(timers, "active_timers", return_value=[running]), \
             patch.object(timers, "list_timers", return_value="Active timers: pasta."):
            reply = answer_cards.show("timers")
        self.assertTrue(reply.startswith("Active timers: pasta."))
        row = self.shown[0].data["timers"][0]
        self.assertEqual((row["id"], row["label"], row["paused"]), ("abc123", "Pasta", False))
        self.assertGreater(row["remaining_s"], 0)

    def test_calendar_day_keeps_today_only(self) -> None:
        from integrations import google_calendar
        now = datetime.now().astimezone()
        later = now.replace(hour=23, minute=0, second=0, microsecond=0)
        events = [{"summary": "Quiz 4", "start": later, "all_day": False, "location": "B12", "calendar": "School"},
                  {"summary": "Tomorrow", "start": later + timedelta(days=1), "all_day": False}]
        with patch.object(google_calendar, "is_configured", return_value=True), \
             patch.object(google_calendar, "upcoming_events", return_value=events) as fetch:
            reply = answer_cards.show("calendar_day")
        self.assertFalse(fetch.call_args.kwargs["allow_interactive"])
        self.assertIn("Quiz 4 at 11 PM", reply)
        data = self.shown[0].data
        self.assertEqual([e["title"] for e in data["events"]], ["Quiz 4"])
        self.assertEqual(data["events"][0]["start_at"], later.timestamp())

    def test_calendar_unreachable_shows_nothing(self) -> None:
        from integrations import google_calendar
        with patch.object(google_calendar, "is_configured", return_value=True), \
             patch.object(google_calendar, "upcoming_events", return_value=None):
            self.assertIn("couldn't reach", answer_cards.show("calendar_day"))
        self.assertEqual(self.shown, [])

    def test_tool_is_advertised_with_every_card_type(self) -> None:
        spec = next(s for s in tools.get_tool_specs() if s["name"] == "show_screen")
        self.assertEqual(spec["input_schema"]["properties"]["card"]["enum"], list(answer_cards.CARD_TYPES))


class CardBusTests(unittest.TestCase):
    def setUp(self) -> None:
        event_bus.reset_for_tests()
        self.addCleanup(event_bus.reset_for_tests)

    def test_cards_never_touch_alert_history_or_the_phone(self) -> None:
        phone = Mock()
        event_bus.subscribe("phone", phone)
        event_bus.publish_card("timers", "Timers", {"timers": []}, source="t")
        phone.assert_not_called()
        self.assertEqual(event_bus.since(0), [])

    def test_expired_cards_are_not_replayed(self) -> None:
        short = event_bus.publish_card("timers", "Short", {}, source="t", ttl_s=60)
        longer = event_bus.publish_card("timers", "Longer", {}, source="t", ttl_s=120)
        self.assertEqual(event_bus.live_cards(), [short, longer])
        with patch.object(event_bus.time, "time", return_value=short.created_at + 61):
            self.assertEqual(event_bus.live_cards(), [longer])

    def _stream(self, only: str | None, publish) -> list[str]:  # noqa: ANN001
        async def scenario() -> list[str]:
            request = Mock(is_disconnected=AsyncMock(return_value=False))
            response = await server.client_events(request, since=0, only=only, last_event_id=None, _=None)
            frames = response.body_iterator
            try:
                threading.Timer(0.1, publish).start()
                first = await asyncio.wait_for(anext(frames), 5)
                second = await asyncio.wait_for(anext(frames), 5)
            finally:
                await frames.aclose()
            return [first, second]
        server._runtime = Mock()
        self.addCleanup(setattr, server, "_runtime", None)
        return asyncio.run(scenario())

    def test_stream_carries_cards_without_moving_last_event_id(self) -> None:
        event_bus.publish("x", "replayed", "m", source="s")
        def publish() -> None:
            event_bus.publish_card("weather_today", "Today", {"temp_f": 54}, source="t")
        first, second = self._stream(None, publish)
        self.assertTrue(first.startswith("id: 1\nevent: alert\n"))
        self.assertTrue(second.startswith("event: card\ndata: "))
        self.assertNotIn("id:", second.split("data: ")[0])
        body = json.loads(second.split("data: ", 1)[1])
        self.assertEqual((body["card"], body["data"]["temp_f"], body["ttl_s"]), ("weather_today", 54, 45.0))
        self.assertEqual(event_bus._card_subscribers, {})

    def test_cards_only_stream_skips_alerts_and_replays_live_cards(self) -> None:
        event_bus.publish("x", "alert", "m", source="s")
        event_bus.publish_card("timers", "On screen", {}, source="t")
        def publish() -> None:
            event_bus.publish("x", "skipped", "m", source="s")
            event_bus.publish_card("timers", "Live", {}, source="t")
        frames = self._stream("cards", publish)
        self.assertEqual([json.loads(f.split("data: ", 1)[1])["title"] for f in frames], ["On screen", "Live"])
        self.assertEqual([n for n in event_bus._subscribers if n.startswith("sse-")], [])


if __name__ == "__main__":
    unittest.main()
