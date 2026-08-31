"""Home Assistant REST client and fuzzy device-command matcher.

This module is intentionally self-contained: it reads its config from the
environment (via ``.env``), uses a single shared :class:`requests.Session`
for connection reuse, and returns ``(success, payload)`` tuples so callers
can handle failures without try/except at every call site.
"""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

from jarvis_logger import get_logger

load_dotenv()

log = get_logger(__name__)

_TIMEOUT_SECONDS = 10

# Single pooled session reused for every HA call.
_session = requests.Session()


# --- Core API wrappers ------------------------------------------------------

def _config() -> tuple[str, str]:
    """Return live HA config, accepting the old short aliases for migration."""
    url = (
        os.getenv("HOME_ASSISTANT_URL", "").strip()
        or os.getenv("HA_URL", "").strip()
    ).rstrip("/")
    token = (
        os.getenv("HOME_ASSISTANT_TOKEN", "").strip()
        or os.getenv("HA_TOKEN", "").strip()
    )
    return url, token


def _is_configured() -> bool:
    url, token = _config()
    return bool(url and token)


def _unconfigured() -> tuple[bool, str]:
    return False, "Home Assistant is not configured yet."


def _request(method: str, path: str, **kwargs: Any) -> tuple[bool, Any]:
    """Perform a REST call against Home Assistant."""
    base_url, token = _config()
    if not base_url or not token:
        return _unconfigured()

    url = f"{base_url}{path}"
    headers = dict(kwargs.pop("headers", {}))
    headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    try:
        response = _session.request(
            method,
            url,
            timeout=_TIMEOUT_SECONDS,
            headers=headers,
            **kwargs,
        )
    except requests.RequestException as exc:
        log.warning("HA request failed: %s %s -> %s", method, path, exc)
        return False, f"Could not reach Home Assistant: {exc}"

    if response.status_code in (200, 201):
        try:
            return True, response.json()
        except ValueError:
            return True, response.text

    log.warning("HA %s %s returned %s: %s", method, path, response.status_code, response.text)
    return False, f"Home Assistant error {response.status_code}: {response.text}"


def test_connection() -> tuple[bool, str]:
    success, payload = _request("GET", "/api/")
    if success:
        return True, "Connected to Home Assistant successfully."
    return False, str(payload)


def get_states() -> tuple[bool, Any]:
    return _request("GET", "/api/states")


def list_entities(domain: str | None = None) -> tuple[bool, list[str] | str]:
    success, result = get_states()
    if not success:
        return False, str(result)

    entities: list[str] = []
    for entity in result:
        entity_id = entity.get("entity_id", "")
        if not entity_id:
            continue
        if domain is None or entity_id.startswith(f"{domain}."):
            entities.append(entity_id)

    entities.sort()
    return True, entities


def call_service(domain: str, service: str, entity_id: str) -> tuple[bool, str]:
    success, payload = _request(
        "POST",
        f"/api/services/{domain}/{service}",
        json={"entity_id": entity_id},
    )
    if success:
        pretty_service = service.replace("_", " ").title()
        return True, f"{pretty_service} command sent to {entity_id}."
    return False, str(payload)


# --- Fuzzy device command matching ------------------------------------------

_SYNONYM_GROUPS: dict[str, tuple[str, ...]] = {
    "strip": ("strip", "strips"),
    "desk": ("desk",),
    "bedroom": ("bedroom",),
    "fan": ("fan",),
    "scentsy": ("scentsy",),
    "avengers": ("avengers",),
    "tower": ("tower",),
    "wii": ("wii",),
    "lights": ("light", "lights"),
    "led": ("led",),
}


def _normalize_text(text: str) -> str:
    text = text.lower().strip()
    for char in ("'", "-", ".", ","):
        text = text.replace(char, " ")
    return " ".join(text.split())


def _extract_action(command: str) -> str | None:
    command = _normalize_text(command)

    # Explicit verb phrases and a trailing state describe the requested
    # action. Check OFF first so a location phrase such as "the light on my
    # desk" cannot override "turn off".
    if "turn off" in command or "switch off" in command or command.endswith(" off"):
        return "turn_off"
    if "turn on" in command or "switch on" in command or command.endswith(" on"):
        return "turn_on"
    return None


def _friendly_name_from_entity(entity_id: str) -> str:
    _, object_id = entity_id.split(".", 1)
    return object_id.replace("_", " ")


def _score_entity(command: str, entity_id: str) -> int:
    command_text = _normalize_text(command)
    entity_text = _normalize_text(_friendly_name_from_entity(entity_id))

    command_words = set(command_text.split())
    entity_words = set(entity_text.split())

    score = len(command_words & entity_words) * 10

    if entity_text in command_text:
        score += 25

    for entity_key, variants in _SYNONYM_GROUPS.items():
        if entity_key in entity_words and any(word in command_words for word in variants):
            score += 12

    return score


def _find_best_entity(command: str, domains: list[str]) -> tuple[bool, str]:
    candidates: list[str] = []
    for domain in domains:
        success, result = list_entities(domain)
        if not success:
            return False, str(result)
        candidates.extend(result)  # type: ignore[arg-type]

    scored: list[tuple[int, str]] = []
    for entity_id in candidates:
        score = _score_entity(command, entity_id)
        if score > 0:
            scored.append((score, entity_id))

    if not scored:
        return False, "I could not find a matching device."

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_entity = scored[0]

    if len(scored) > 1 and scored[1][0] == best_score:
        return False, "I found multiple possible matches. Be more specific."

    return True, best_entity


def handle_device_command(command: str) -> str:
    """Interpret a freeform on/off command and send it to Home Assistant."""
    action = _extract_action(command)
    if action is None:
        return "I need you to say whether to turn something on or off."

    normalized = _normalize_text(command)

    if "fan" in normalized:
        domains = ["fan"]
    elif any(word in normalized for word in ("light", "lights", "strip", "desk", "bedroom")):
        domains = ["light"]
    else:
        domains = ["switch", "light", "fan"]

    success, entity_or_message = _find_best_entity(command, domains)
    if not success:
        return entity_or_message

    domain = entity_or_message.split(".", 1)[0]
    success, message = call_service(domain, action, entity_or_message)
    return message if success else f"Command failed. {message}"


_ACTION_SERVICES = {
    "scene": "turn_on",
    "script": "turn_on",
    "automation": "trigger",
}


def activate_home_action(kind: str, name: str) -> str:
    """Activate one HA scene, script, or automation by fuzzy entity name."""
    kind = _normalize_text(kind).removesuffix("s")
    if kind not in _ACTION_SERVICES:
        return "I can activate Home Assistant scenes, scripts, or automations."

    name = (name or "").strip()
    if not name:
        return f"I need the name of the {kind} to activate."

    success, entity_or_message = _find_best_entity(name, [kind])
    if not success:
        return entity_or_message

    success, message = call_service(
        kind,
        _ACTION_SERVICES[kind],
        entity_or_message,
    )
    return message if success else f"Command failed. {message}"
