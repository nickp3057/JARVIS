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

def _geocode_query(query: str, count: int = 5) -> list[dict[str, Any]]:
    """Single API call to Open-Meteo geocoding. Returns the raw results list."""
    try:
        response = requests.get(
            _GEOCODE_URL,
            params={"name": query, "count": count, "language": "en", "format": "json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        log.warning("Geocoding request failed for %r: %s", query, exc)
        return []

    if response.status_code != 200:
        log.warning("Geocoding returned %s: %s", response.status_code, response.text[:200])
        return []

    try:
        data = response.json()
    except ValueError:
        return []

    return data.get("results") or []


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


def _geocode(location: str) -> tuple[float, float, str] | None:
    """Resolve a free-form location string to (lat, lon, display_name)."""
    location = (location or "").strip()
    if not location:
        return None

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

def _fetch_forecast(lat: float, lon: float) -> dict[str, Any] | None:
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
    try:
        response = requests.get(_FORECAST_URL, params=params, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        log.warning("Forecast request failed: %s", exc)
        return None

    if response.status_code != 200:
        log.warning("Forecast returned %s: %s", response.status_code, response.text[:200])
        return None

    try:
        return response.json()
    except ValueError:
        return None


def get_weather(location: str | None = None) -> str:
    """Return a short spoken weather summary for ``location`` (or the default)."""
    target = (location or "").strip() or os.getenv("DEFAULT_LOCATION", "").strip()
    if not target:
        return (
            "I don't have a default location set. "
            "Set DEFAULT_LOCATION in the env file or tell me where to check."
        )

    geocoded = _geocode(target)
    if geocoded is None:
        return f"I couldn't find a place called {target}."

    lat, lon, display_name = geocoded

    forecast = _fetch_forecast(lat, lon)
    if forecast is None:
        return f"I couldn't reach the weather service for {display_name}."

    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}

    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    wind = current.get("wind_speed_10m")
    humidity = current.get("relative_humidity_2m")

    daily_max = (daily.get("temperature_2m_max") or [None])[0]
    daily_min = (daily.get("temperature_2m_min") or [None])[0]

    if temp is None or code is None:
        return f"The weather data for {display_name} came back empty."

    description = _describe_code(int(code))

    parts = [
        f"In {display_name} it's currently {round(float(temp))} degrees and {description}."
    ]
    if daily_max is not None and daily_min is not None:
        parts.append(
            f"Today's high is {round(float(daily_max))}, low {round(float(daily_min))}."
        )
    if humidity is not None:
        parts.append(f"Humidity {round(float(humidity))} percent.")
    if wind is not None:
        parts.append(f"Winds around {round(float(wind))} miles per hour.")

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
