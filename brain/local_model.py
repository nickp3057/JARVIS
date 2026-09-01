"""Optional OpenAI-compatible local conversational fallback.

This first routing slice is intentionally read-only and tool-free. Deterministic
keyword intents run before it, so an unavailable or incapable local model can
never claim it changed Home Assistant or another external system.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

from jarvis_logger import get_logger

log = get_logger(__name__)

_READ_ONLY_INTENTS = {
    "time",
    "ha_test",
    "list_lights",
    "list_switches",
    "list_fans",
    "list_devices",
    "home_status",
}
_BLOCKED_HOME_TARGET_TERMS = {
    "alarm", "door", "garage", "lock", "shade", "shades", "cover", "window"
}
_GENERIC_CLIMATE_TARGET_TERMS = {
    "climate", "heater", "heating", "thermostat", "temperature"
}
_GENERIC_LIGHT_TARGET_TERMS = {"bulb", "lamp", "light", "lights"}
_LIGHT_COLORS = {
    "blue", "cyan", "green", "magenta", "orange", "pink", "purple", "red",
    "white", "yellow",
}
_unavailable_until = 0.0


def _can_attempt() -> bool:
    return time.monotonic() >= _unavailable_until


def _record_success() -> None:
    global _unavailable_until
    _unavailable_until = 0.0


def _record_failure() -> None:
    global _unavailable_until
    try:
        cooldown = max(
            1.0,
            min(300.0, float(os.getenv("LOCAL_MODEL_FAILURE_COOLDOWN_S", "30"))),
        )
    except (TypeError, ValueError):
        cooldown = 30.0
    _unavailable_until = time.monotonic() + cooldown


def is_configured() -> bool:
    enabled = os.getenv("LOCAL_MODEL_ENABLED", "false").strip().lower()
    return enabled in {"1", "true", "yes", "on"} and bool(
        os.getenv("LOCAL_MODEL_URL", "").strip()
        and os.getenv("LOCAL_MODEL_NAME", "").strip()
    )


def _endpoint() -> str | None:
    base = os.getenv("LOCAL_MODEL_URL", "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _timeout() -> float:
    try:
        return max(1.0, min(60.0, float(os.getenv("LOCAL_MODEL_TIMEOUT_S", "8"))))
    except (TypeError, ValueError):
        return 8.0


def _system_prompt() -> str:
    address = (os.getenv("ADDRESS") or "sir").strip() or "sir"
    return (
        "You are JARVIS, Nick's concise local assistant. Address him as "
        f"{address}. Reply in one or two direct sentences with dry confidence. "
        "You have no tools in this fallback mode: never claim you changed a device, "
        "looked up live information, or completed an external action. Say plainly "
        "when the full reasoning system is required."
    )


def generate_reply(history: list[dict[str, Any]], user_input: str) -> str | None:
    """Return a validated local reply, or ``None`` for transparent fallback."""
    if not is_configured() or not _can_attempt():
        return None
    endpoint = _endpoint()
    if endpoint is None:
        log.warning("LOCAL_MODEL_URL is invalid; local fallback disabled for this turn.")
        return None

    messages: list[dict[str, str]] = [{"role": "system", "content": _system_prompt()}]
    for item in history[-12:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()[:4_000]})
    messages.append({"role": "user", "content": user_input.strip()[:4_000]})

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("LOCAL_MODEL_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={
                "model": os.getenv("LOCAL_MODEL_NAME", "").strip(),
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 300,
                "stream": False,
            },
            timeout=_timeout(),
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty local model content")
        _record_success()
        return content.strip()[:4_000]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        _record_failure()
        log.warning("Local model unavailable; using keyword fallback: %s", exc)
        return None


def generate_summary(messages: list[dict[str, str]]) -> str | None:
    """Return an opt-in bounded session digest, or ``None`` for extractive fallback."""
    enabled = os.getenv("LOCAL_MODEL_SUMMARIES", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"} or not is_configured() or not _can_attempt():
        return None
    endpoint = _endpoint()
    if endpoint is None:
        return None
    transcript = []
    for item in messages[-10:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            transcript.append(f"{role}: {' '.join(content.split())[:500]}")
    if not transcript:
        return None
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("LOCAL_MODEL_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={
                "model": os.getenv("LOCAL_MODEL_NAME", "").strip(),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Summarize this conversation in at most 120 words. Preserve concrete "
                            "decisions, tasks, names, and unresolved next steps. Do not add facts."
                        ),
                    },
                    {"role": "user", "content": "\n".join(transcript)[:5_000]},
                ],
                "temperature": 0,
                "max_tokens": 180,
                "stream": False,
            },
            timeout=_timeout(),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty local summary")
        _record_success()
        return " ".join(content.split())[:1_600]
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        _record_failure()
        log.warning("Local summary unavailable; retaining extractive digest: %s", exc)
        return None


def classify_semantic_route(user_input: str) -> dict[str, Any] | None:
    """Return one validated read intent or reversible home on/off command."""
    if not is_configured() or not _can_attempt():
        return None
    semantic_enabled = os.getenv("LOCAL_MODEL_SEMANTIC_ROUTING", "true").strip().lower()
    if semantic_enabled not in {"1", "true", "yes", "on"}:
        return None
    endpoint = _endpoint()
    if endpoint is None:
        return None

    allowed = ", ".join(sorted(_READ_ONLY_INTENTS))
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("LOCAL_MODEL_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.post(
            endpoint,
            headers=headers,
            json={
                "model": os.getenv("LOCAL_MODEL_NAME", "").strip(),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Classify the request. Return only JSON in one of these forms: "
                            f'{{"kind":"intent","intent":"name"}} where name is one of {allowed}; '
                            'or {"kind":"home_control","action":"turn_on|turn_off",'
                            '"target":"device name","confidence":0.0}; or '
                            '{"kind":"light_brightness","target":"light name",'
                            '"brightness_pct":0,"confidence":0.0}; or '
                            '{"kind":"light_color","target":"light name",'
                            '"color":"supported color","confidence":0.0}; or '
                            '{"kind":"climate_temperature","target":"thermostat name",'
                            '"temperature":72,"unit":"F|C","confidence":0.0}; or '
                            '{"kind":"unknown"}. Home control is only for reversible on/off '
                            "requests targeting lights, switches, plugs, or fans. Brightness is only "
                            "for an explicit percentage from 0 to 100 on a named light. Color is "
                            f"only for a named light and one of {', '.join(sorted(_LIGHT_COLORS))}. Climate "
                            "temperature requires an explicit number, unit, and named thermostat. Never classify "
                            "locks, doors, garages, covers, alarms, playback, deletion, settings, "
                            "automations, scenes, scripts, or other actions."
                        ),
                    },
                    {"role": "user", "content": user_input.strip()[:1_000]},
                ],
                "temperature": 0,
                "max_tokens": 60,
                "stream": False,
            },
            timeout=_timeout(),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            return None
        # Tolerate common fenced output, but never fuzzy-match an intent name.
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        result = json.loads(cleaned)
        _record_success()
        if not isinstance(result, dict):
            return None
        if result.get("kind") == "intent" and result.get("intent") in _READ_ONLY_INTENTS:
            return {"kind": "intent", "intent": result["intent"]}
        if result.get("kind") == "light_brightness":
            target = result.get("target")
            try:
                confidence = float(result.get("confidence", 0))
                brightness = int(result.get("brightness_pct"))
                threshold = float(os.getenv("LOCAL_MODEL_HOME_CONTROL_CONFIDENCE", "0.85"))
            except (TypeError, ValueError):
                return None
            if not isinstance(target, str) or not 0 <= brightness <= 100:
                return None
            target = " ".join(target.strip().split())[:120]
            target_tokens = {
                token for token in re.findall(r"[a-z0-9]+", target.lower()) if len(token) > 2
            }
            specific_target_tokens = target_tokens - _GENERIC_LIGHT_TARGET_TERMS
            input_tokens = {
                token for token in re.findall(r"[a-z0-9]+", user_input.lower()) if len(token) > 2
            }
            if (
                not target
                or confidence < max(0.5, min(1.0, threshold))
                or not specific_target_tokens
                or not specific_target_tokens.intersection(input_tokens)
                or target_tokens.intersection(_BLOCKED_HOME_TARGET_TERMS)
            ):
                return None
            return {
                "kind": "light_brightness",
                "target": target,
                "brightness_pct": brightness,
            }
        if result.get("kind") == "light_color":
            target = result.get("target")
            color = str(result.get("color") or "").strip().lower()
            try:
                confidence = float(result.get("confidence", 0))
                threshold = float(os.getenv("LOCAL_MODEL_HOME_CONTROL_CONFIDENCE", "0.85"))
            except (TypeError, ValueError):
                return None
            if not isinstance(target, str) or color not in _LIGHT_COLORS:
                return None
            target = " ".join(target.strip().split())[:120]
            target_tokens = {
                token for token in re.findall(r"[a-z0-9]+", target.lower()) if len(token) > 2
            }
            specific_target_tokens = target_tokens - _GENERIC_LIGHT_TARGET_TERMS
            input_tokens = {
                token for token in re.findall(r"[a-z0-9]+", user_input.lower()) if len(token) > 2
            }
            if (
                not target
                or confidence < max(0.5, min(1.0, threshold))
                or not specific_target_tokens
                or not specific_target_tokens.intersection(input_tokens)
                or color not in input_tokens
            ):
                return None
            return {"kind": "light_color", "target": target, "color": color}
        if result.get("kind") == "climate_temperature":
            target = result.get("target")
            unit = str(result.get("unit") or "").strip().upper().replace("°", "")
            try:
                temperature = float(result.get("temperature"))
                confidence = float(result.get("confidence", 0))
                threshold = float(os.getenv("LOCAL_MODEL_HOME_CONTROL_CONFIDENCE", "0.85"))
            except (TypeError, ValueError):
                return None
            if not isinstance(target, str) or unit not in {"F", "C"}:
                return None
            target = " ".join(target.strip().split())[:120]
            limits = (45.0, 90.0) if unit == "F" else (7.0, 32.0)
            target_tokens = {
                token for token in re.findall(r"[a-z0-9]+", target.lower()) if len(token) > 2
            }
            specific_target_tokens = target_tokens - _GENERIC_CLIMATE_TARGET_TERMS
            input_tokens = {
                token for token in re.findall(r"[a-z0-9]+", user_input.lower()) if len(token) > 2
            }
            if (
                not limits[0] <= temperature <= limits[1]
                or not target
                or confidence < max(0.5, min(1.0, threshold))
                or not specific_target_tokens
                or not specific_target_tokens.intersection(input_tokens)
            ):
                return None
            return {
                "kind": "climate_temperature",
                "target": target,
                "temperature": temperature,
                "unit": unit,
            }
        if result.get("kind") != "home_control":
            return None
        action = result.get("action")
        target = result.get("target")
        try:
            confidence = float(result.get("confidence", 0))
            threshold = float(os.getenv("LOCAL_MODEL_HOME_CONTROL_CONFIDENCE", "0.85"))
        except (TypeError, ValueError):
            return None
        if action not in {"turn_on", "turn_off"} or not isinstance(target, str):
            return None
        target = " ".join(target.strip().split())[:120]
        if not target or confidence < max(0.5, min(1.0, threshold)):
            return None
        target_tokens = {token for token in re.findall(r"[a-z0-9]+", target.lower()) if len(token) > 2}
        input_tokens = {
            token for token in re.findall(r"[a-z0-9]+", user_input.lower()) if len(token) > 2
        }
        if not target_tokens.intersection(input_tokens):
            return None
        if target_tokens.intersection(_BLOCKED_HOME_TARGET_TERMS):
            return None
        return {"kind": "home_control", "action": action, "target": target}
    except (
        requests.RequestException,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        _record_failure()
        log.warning("Local semantic routing failed closed: %s", exc)
        return None


def classify_read_only_intent(user_input: str) -> str | None:
    """Compatibility wrapper returning only safe read-only classifications."""
    route = classify_semantic_route(user_input)
    if route and route.get("kind") == "intent":
        return route.get("intent")
    return None
