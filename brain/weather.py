"""Weather via Open-Meteo.

Open-Meteo is a free, no-API-key weather service. We hit two endpoints:
- Geocoding: turns a city name into latitude/longitude.
- Forecast: returns current conditions plus today's high/low.

Geocoding is *forgiving*: we try the query as-is, then fall back to
splitting on commas, then to just the first word. This handles all of
"Herriman, Utah", "Herriman Utah", and "Herriman" cleanly.

Results are formatted for speech (no degree symbols, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import requests

from jarvis_logger import get_logger

log = get_logger(__name__)

_TIMEOUT_SECONDS = 8

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

_WEATHER_CODES: dict[int, str] = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "severe thunderstorms with hail",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    online: bool
    location: str = ""
    temperature_f: int | None = None
    description: str = ""
    high_f: int | None = None
    low_f: int | None = None
    humidity_percent: int | None = None
    wind_mph: int | None = None
    error: str = ""


def _describe_code(code: int) -> str:
    return _WEATHER_CODES.get(code, "unsettled weather")


# --- Geocoding (lenient) ----------------------------------------------------

def _get_json(url: str, params: dict[str, Any], what: str) -> dict[str, Any] | None:
    """One Open-Meteo call; None (logged) on a network error, non-200 or bad JSON."""
    try:
        response = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.warning("%s request failed: %s", what, exc)
        return None
    if response.status_code != 200:
        log.warning("%s returned %s: %s", what, response.status_code, response.text[:200])
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _geocode_query(query: str, count: int = 5) -> list[dict[str, Any]]:
    """Single API call to Open-Meteo geocoding. Returns the raw results list."""
    data = _get_json(_GEOCODE_URL, {"name": query, "count": count, "language": "en", "format": "json"},
                     f"Geocoding {query!r}")
    return (data or {}).get("results") or []


def _admin_match_score(result: dict[str, Any], hint: str) -> int:
    """Score how well a geocoder result matches a state/region hint string."""
    if not hint:
        return 0
    hint = hint.strip().lower()
    fields = (
        result.get("admin1", "") or "",
        result.get("admin2", "") or "",
        result.get("country", "") or "",
        result.get("country_code", "") or "",
    )
    score = 0
    for field in fields:
        if not field:
            continue
        f = field.lower()
        if hint == f:
            score += 5
        elif hint in f or f in hint:
            score += 2
    return score


def _format_result(result: dict[str, Any]) -> tuple[float, float, str] | None:
    lat = result.get("latitude")
    lon = result.get("longitude")
    if lat is None or lon is None:
        return None
    parts = [result.get("name")]
    if result.get("admin1"):
        parts.append(result["admin1"])
    if result.get("country_code"):
        parts.append(result["country_code"])
    display = ", ".join(p for p in parts if p)
    return float(lat), float(lon), display


# Places don't move: remember each resolved location for the process's life
# (the weather monitor refreshes often). Misses aren't kept, so a typo or an
# outage can resolve later.
_geocoded: dict[str, tuple[float, float, str]] = {}


def _geocode(location: str) -> tuple[float, float, str] | None:
    """Resolve a free-form location string to (lat, lon, display_name)."""
    location = (location or "").strip()
    if not location:
        return None
    key = location.lower()
    if key not in _geocoded:
        found = _geocode_uncached(location)
        if found is None:
            return None
        _geocoded[key] = found
    return _geocoded[key]


def _geocode_uncached(location: str) -> tuple[float, float, str] | None:
    # Build candidate queries in priority order.
    candidates: list[tuple[str, str]] = []  # (query, region_hint)
    candidates.append((location, ""))

    if "," in location:
        head, _, tail = location.partition(",")
        candidates.append((head.strip(), tail.strip()))
    else:
        words = location.split()
        if len(words) >= 2:
            # First word as the city, the rest as region hint (e.g. "Herriman Utah").
            candidates.append((words[0], " ".join(words[1:])))

    # First word alone, as last resort.
    first_word = location.split()[0] if location.split() else ""
    if first_word and first_word != location:
        candidates.append((first_word, ""))

    seen_queries: set[str] = set()
    for query, hint in candidates:
        if not query or query.lower() in seen_queries:
            continue
        seen_queries.add(query.lower())

        results = _geocode_query(query, count=5)
        if not results:
            continue

        # Rank: prefer matches whose admin region also matches the hint.
        if hint:
            results.sort(key=lambda r: -_admin_match_score(r, hint))

        best = results[0]
        formatted = _format_result(best)
        if formatted is not None:
            return formatted

    return None


# --- Forecast --------------------------------------------------------------

def _fetch_forecast(lat: float, lon: float, *, hourly: bool = False) -> dict[str, Any] | None:
    """Current conditions and today's high/low; ``hourly`` adds the hour-by-hour
    temperature and rain chance (two days, so late evening still has hours
    ahead), sunrise/sunset and whether it's daytime, for the answer screen."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "timezone": "auto",
        "forecast_days": 1,
    }
    if hourly:
        params.update({
            "current": params["current"] + ",is_day,wind_gusts_10m",
            "daily": params["daily"] + ",sunrise,sunset,precipitation_probability_max",
            "hourly": "temperature_2m,precipitation_probability,weather_code,is_day",
            "forecast_days": 2,
        })
    return _get_json(_FORECAST_URL, params, "Forecast")


# --- Public -----------------------------------------------------------------

def get_weather(location: str | None = None) -> str:
    """Return a short spoken weather summary for ``location`` (or the default)."""
    target = (location or "").strip() or os.getenv("DEFAULT_LOCATION", "").strip()
    if not target:
        return (
            "I don't have a default location set. "
            "Set DEFAULT_LOCATION in the env file or tell me where to check."
        )
    snap = get_weather_snapshot(target)
    if not snap.online:
        return snap.error
    parts = [f"In {snap.location} it's currently {snap.temperature_f} degrees and {snap.description}."]
    if snap.high_f is not None and snap.low_f is not None:
        parts.append(f"Today's high is {snap.high_f}, low {snap.low_f}.")
    if snap.humidity_percent is not None:
        parts.append(f"Humidity {snap.humidity_percent} percent.")
    if snap.wind_mph is not None:
        parts.append(f"Winds around {snap.wind_mph} miles per hour.")
    return " ".join(parts)


def get_weather_snapshot(location: str | None = None) -> WeatherSnapshot:
    """Return structured weather for cockpit panels and monitors."""
    target = (location or "").strip() or os.getenv("DEFAULT_LOCATION", "").strip()
    if not target:
        return WeatherSnapshot(
            online=False,
            error="DEFAULT_LOCATION is not set.",
        )

    geocoded = _geocode(target)
    if geocoded is None:
        return WeatherSnapshot(
            online=False,
            location=target,
            error=f"I couldn't find a place called {target}.",
        )

    lat, lon, display_name = geocoded
    forecast = _fetch_forecast(lat, lon)
    if forecast is None:
        return WeatherSnapshot(
            online=False,
            location=display_name,
            error=f"I couldn't reach the weather service for {display_name}.",
        )

    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    if temp is None or code is None:
        return WeatherSnapshot(
            online=False,
            location=display_name,
            error=f"The weather data for {display_name} came back empty.",
        )

    daily_max = (daily.get("temperature_2m_max") or [None])[0]
    daily_min = (daily.get("temperature_2m_min") or [None])[0]
    humidity = current.get("relative_humidity_2m")
    wind = current.get("wind_speed_10m")

    return WeatherSnapshot(
        online=True,
        location=display_name,
        temperature_f=round(float(temp)),
        description=_describe_code(int(code)),
        high_f=round(float(daily_max)) if daily_max is not None else None,
        low_f=round(float(daily_min)) if daily_min is not None else None,
        humidity_percent=round(float(humidity)) if humidity is not None else None,
        wind_mph=round(float(wind)) if wind is not None else None,
    )


# --- Answer screen ------------------------------------------------------------

def condition(code: int) -> str:
    """One word a screen can draw an icon from: clear, partly, cloudy, fog,
    drizzle, rain, snow or storm."""
    if code in (0, 1):
        return "clear"
    if code == 2:
        return "partly"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    return "cloudy"


def _local_epoch(text: Any, offset_s: float) -> float | None:
    """Open-Meteo's local ISO time (``timezone=auto``, no offset) as Unix time."""
    from datetime import datetime, timezone
    try:
        naive = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).timestamp() - offset_s


def _num(value: Any) -> int | None:
    try:
        return round(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


# The screen shows the rest of today hour by hour; late in the evening it
# runs on into the night so there is always something to read.
_MIN_HOURS, _MAX_HOURS = 8, 18


def get_weather_today(location: str | None = None, *, now: float | None = None) -> dict[str, Any]:
    """Structured "today" weather for the answer screen (``weather_today``).

    Keys: online, location, temp_f, description, condition, is_day, high_f,
    low_f, humidity_percent, wind_mph, gust_mph, rain_chance (today's max),
    sunrise / sunset (Unix time), hours: [{at, temp_f, rain_chance,
    condition, is_day}] from this hour to midnight (8 to 18 entries); or
    online False with an error."""
    import time as _time
    target = (location or "").strip() or os.getenv("DEFAULT_LOCATION", "").strip()
    if not target:
        return {"online": False, "error": "DEFAULT_LOCATION is not set."}
    geocoded = _geocode(target)
    if geocoded is None:
        return {"online": False, "location": target, "error": f"I couldn't find a place called {target}."}
    lat, lon, display_name = geocoded
    forecast = _fetch_forecast(lat, lon, hourly=True)
    current = (forecast or {}).get("current") or {}
    if forecast is None or current.get("temperature_2m") is None or current.get("weather_code") is None:
        return {"online": False, "location": display_name,
                "error": f"I couldn't reach the weather service for {display_name}."}
    daily = forecast.get("daily") or {}
    offset = float(forecast.get("utc_offset_seconds") or 0)
    now = _time.time() if now is None else now

    def first(key: str) -> Any:
        values = daily.get(key) or [None]
        return values[0]

    hourly = forecast.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    rain = hourly.get("precipitation_probability") or []
    codes = hourly.get("weather_code") or []
    day_flags = hourly.get("is_day") or []
    midnight = None
    if daily.get("time"):
        midnight = _local_epoch(f"{daily['time'][0]}T00:00", offset)
    hours: list[dict[str, Any]] = []
    for index, stamp in enumerate(times):
        at = _local_epoch(stamp, offset)
        if at is None or at + 3600 <= now:  # this hour still counts until it ends
            continue
        if len(hours) >= _MAX_HOURS:
            break
        if len(hours) >= _MIN_HOURS and midnight is not None and at >= midnight + 86400:
            break
        code = codes[index] if index < len(codes) and codes[index] is not None else 0
        hours.append({
            "at": at,
            "temp_f": _num(temps[index] if index < len(temps) else None),
            "rain_chance": _num(rain[index] if index < len(rain) else None),
            "condition": condition(int(code)),
            "is_day": bool(day_flags[index]) if index < len(day_flags) else True,
        })
    code = int(current["weather_code"])
    return {
        "online": True,
        "location": display_name,
        "temp_f": _num(current.get("temperature_2m")),
        "description": _describe_code(code),
        "condition": condition(code),
        "is_day": bool(current.get("is_day", 1)),
        "high_f": _num(first("temperature_2m_max")),
        "low_f": _num(first("temperature_2m_min")),
        "humidity_percent": _num(current.get("relative_humidity_2m")),
        "wind_mph": _num(current.get("wind_speed_10m")),
        "gust_mph": _num(current.get("wind_gusts_10m")),
        "rain_chance": _num(first("precipitation_probability_max")),
        "sunrise": _local_epoch(first("sunrise"), offset) if first("sunrise") else None,
        "sunset": _local_epoch(first("sunset"), offset) if first("sunset") else None,
        "hours": hours,
    }
