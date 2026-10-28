"""Home Assistant REST client and fuzzy device-command matcher.

This module is intentionally self-contained: it reads its config from the
environment (via ``.env``), uses a single shared :class:`requests.Session`
for connection reuse, and returns ``(success, payload)`` tuples so callers
can handle failures without try/except at every call site.
"""

from __future__ import annotations

import os
import json
import math
import re
import threading
from typing import Any
from urllib.parse import urlparse, urlunparse

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


def get_services() -> tuple[bool, Any]:
    """Return Home Assistant's live service/action catalogue."""
    return _request("GET", "/api/services")


def list_automations() -> tuple[bool, list[dict[str, Any]] | str]:
    """List automation entities with the stable IDs used by HA's editor API."""
    success, result = get_states()
    if not success:
        return False, str(result)
    if not isinstance(result, list):
        return False, "Unexpected Home Assistant states response."
    automations: list[dict[str, Any]] = []
    for entity in result:
        entity_id = str(entity.get("entity_id", ""))
        if not entity_id.startswith("automation."):
            continue
        attributes = entity.get("attributes", {})
        automations.append({
            "entity_id": entity_id,
            "id": str(attributes.get("id", "")),
            "alias": str(attributes.get("friendly_name", entity_id)),
            "state": str(entity.get("state", "unknown")),
            "last_triggered": attributes.get("last_triggered"),
        })
    automations.sort(key=lambda item: item["alias"].lower())
    return True, automations


def get_automation_config(automation_id: str) -> tuple[bool, Any]:
    automation_id = (automation_id or "").strip()
    if not automation_id:
        return False, "Missing automation id."
    return _request("GET", f"/api/config/automation/config/{automation_id}")


def save_automation_config(automation_id: str, config: dict[str, Any]) -> tuple[bool, Any]:
    automation_id = (automation_id or "").strip()
    if not automation_id:
        return False, "Missing automation id."
    return _request(
        "POST",
        f"/api/config/automation/config/{automation_id}",
        json=config,
    )


def delete_automation_config(automation_id: str) -> tuple[bool, Any]:
    automation_id = (automation_id or "").strip()
    if not automation_id:
        return False, "Missing automation id."
    return _request("DELETE", f"/api/config/automation/config/{automation_id}")


def list_entities(domain: str | None = None) -> tuple[bool, list[str] | str]:
    success, result = get_states()
    if not success:
        return False, str(result)
    if not isinstance(result, list):
        return False, "Unexpected Home Assistant states response."

    entities: list[str] = []
    for entity in result:
        entity_id = entity.get("entity_id", "")
        if not entity_id:
            continue
        if domain is None or entity_id.startswith(f"{domain}."):
            entities.append(entity_id)

    entities.sort()
    return True, entities


_STATUS_DOMAINS = {
    "light", "switch", "fan", "climate", "media_player", "binary_sensor",
    "person", "device_tracker", "lock", "cover",
}
_STATUS_DOMAIN_WORDS = {
    "light": {"light", "lights", "lamp", "lamps", "led", "leds"},
    "switch": {"switch", "switches", "plug", "plugs", "outlet", "outlets"},
    "fan": {"fan", "fans"},
    "climate": {"climate", "thermostat", "thermostats", "temperature"},
    "media_player": {"media", "speaker", "speakers", "tv", "television"},
    "binary_sensor": {"sensor", "sensors"},
    "person": {"person", "people"},
    "device_tracker": {"tracker", "trackers"},
    "lock": {"lock", "locks"},
    "cover": {"cover", "covers", "blind", "blinds", "shade", "shades"},
}
_STATUS_STATE_WORDS = {
    "on", "off", "open", "closed", "locked", "unlocked", "playing", "paused",
    "idle", "home", "not_home", "unavailable",
}
_STATUS_STOP_WORDS = {
    "what", "which", "show", "tell", "give", "list", "are", "is", "the", "a",
    "an", "my", "me", "all", "device", "devices", "currently", "right", "now",
    "status", "state", "running", "active",
}


def describe_home_status(query: str = "") -> str:
    """Describe a bounded, read-only slice of live Home Assistant state."""
    success, result = get_states()
    if not success:
        return str(result)
    if not isinstance(result, list):
        return "Unexpected Home Assistant states response."

    normalized = _normalize_text(query)
    query_words = set(normalized.split())
    domains = {
        domain for domain, words in _STATUS_DOMAIN_WORDS.items()
        if query_words.intersection(words)
    }
    desired_states = query_words.intersection(_STATUS_STATE_WORDS)
    if "running" in query_words or "active" in query_words:
        desired_states.add("on")
    name_words = query_words - _STATUS_STOP_WORDS - _STATUS_STATE_WORDS
    for words in _STATUS_DOMAIN_WORDS.values():
        name_words -= words

    matches: list[tuple[str, str, str]] = []
    for entity in result:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("entity_id") or "")
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domain not in _STATUS_DOMAINS or (domains and domain not in domains):
            continue
        state = str(entity.get("state") or "unknown").lower()
        if desired_states and state not in desired_states:
            continue
        attributes = entity.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        friendly = str(attributes.get("friendly_name") or _friendly_name_from_entity(entity_id))
        searchable = set(_normalize_text(f"{friendly} {entity_id}").split())
        if name_words and not name_words.issubset(searchable):
            continue
        detail = state.replace("_", " ")
        if domain == "climate":
            temperature = attributes.get("current_temperature")
            unit = attributes.get("temperature_unit") or "°"
            if temperature is not None:
                detail = f"{detail}, {temperature}{unit}"
        matches.append((friendly, detail, entity_id))

    matches.sort(key=lambda item: item[0].lower())
    if not matches:
        return "No matching Home Assistant entities found."
    shown = matches[:30]
    lines = [f"{name} is {state}." for name, state, _ in shown]
    if len(matches) > len(shown):
        lines.append(f"And {len(matches) - len(shown)} more matched.")
    return " ".join(lines)


def call_service(
    domain: str,
    service: str,
    entity_id: str | None = None,
    service_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    from integrations import tv_remote
    if domain == "media_player" and service in ("turn_on", "turn_off") and tv_remote.is_tv_player(entity_id):
        # Cast "turn on" opens Home Assistant's cast screen on the TV; the
        # TV's own remote protocol wakes it properly.
        return tv_remote.power(service == "turn_on")
    payload = dict(service_data or {})
    if entity_id:
        payload["entity_id"] = entity_id
    success, payload = _request(
        "POST",
        f"/api/services/{domain}/{service}",
        json=payload,
    )
    if success:
        pretty_service = service.replace("_", " ").title()
        target = f" to {entity_id}" if entity_id else ""
        return True, f"{pretty_service} command sent{target}."
    return False, str(payload)


def list_services(domain: str | None = None) -> tuple[bool, list[str] | str]:
    """Return discoverable ``domain.service`` names, optionally filtered."""
    success, result = get_services()
    if not success:
        return False, str(result)
    if not isinstance(result, list):
        return False, "Unexpected Home Assistant services response."

    requested = (domain or "").strip().lower()
    names: list[str] = []
    for item in result:
        item_domain = str(item.get("domain", "")).strip().lower()
        if not item_domain or (requested and item_domain != requested):
            continue
        services = item.get("services", {})
        if not isinstance(services, dict):
            continue
        names.extend(f"{item_domain}.{name}" for name in services)
    names.sort()
    return True, names


def describe_services(domain: str | None = None) -> tuple[bool, list[str] | str]:
    """Return service names plus the fields advertised by Home Assistant."""
    success, result = get_services()
    if not success:
        return False, str(result)
    if not isinstance(result, list):
        return False, "Unexpected Home Assistant services response."

    requested = (domain or "").strip().lower()
    descriptions: list[str] = []
    for item in result:
        item_domain = str(item.get("domain", "")).strip().lower()
        if not item_domain or (requested and item_domain != requested):
            continue
        services = item.get("services", {})
        if not isinstance(services, dict):
            continue
        for name, spec in services.items():
            spec = spec if isinstance(spec, dict) else {}
            fields = spec.get("fields", {})
            field_names = sorted(fields) if isinstance(fields, dict) else []
            field_text = f"; fields: {', '.join(field_names)}" if field_names else ""
            descriptions.append(f"{item_domain}.{name}{field_text}")
    descriptions.sort()
    return True, descriptions


_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")
_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_BLOCKED_DOMAINS = {"hassio", "shell_command", "python_script", "update"}
_BLOCKED_CALLS = {
    ("homeassistant", "restart"),
    ("homeassistant", "stop"),
    ("homeassistant", "reload_all"),
}
_CONFIRM_DOMAINS = {"alarm_control_panel", "button", "cover", "lock", "notify"}
_CONFIRM_TERMS = ("disarm", "open", "press", "unlock")


def service_requires_confirmation(domain: str, service: str) -> bool:
    domain = domain.strip().lower()
    service = service.strip().lower()
    return domain in _CONFIRM_DOMAINS or any(term in service for term in _CONFIRM_TERMS)


def call_discovered_service(
    domain: str,
    service: str,
    *,
    entity_id: str | None = None,
    service_data: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> str:
    """Safely call a discovered HA service with guardrails around risky actions."""
    domain = (domain or "").strip().lower()
    service = (service or "").strip().lower()
    entity_id = (entity_id or "").strip().lower() or None
    if not _IDENTIFIER.fullmatch(domain) or not _IDENTIFIER.fullmatch(service):
        return "Invalid Home Assistant domain or service name."
    if entity_id and not _ENTITY_ID.fullmatch(entity_id):
        return "Invalid Home Assistant entity id."
    if domain in _BLOCKED_DOMAINS or (domain, service) in _BLOCKED_CALLS:
        return "That administrative Home Assistant action is blocked from general service control."
    if service_data is not None and not isinstance(service_data, dict):
        return "Home Assistant service data must be an object."
    if service_requires_confirmation(domain, service) and not confirmed:
        target = f" for {entity_id}" if entity_id else ""
        return (
            f"Confirmation required before calling {domain}.{service}{target}. "
            "Ask Nick to explicitly confirm the exact action."
        )

    success, available = list_services(domain)
    if not success:
        return f"Could not discover Home Assistant services: {available}"
    full_name = f"{domain}.{service}"
    if full_name not in available:
        return f"Home Assistant does not advertise the service {full_name}."

    success, message = call_service(domain, service, entity_id, service_data)
    return message if success else f"Command failed. {message}"


def _websocket_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"{parsed.path.rstrip('/')}/api/websocket"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def listen_state_changes(
    stop_event: threading.Event,
    on_change,
    *,
    on_connected=None,
) -> None:
    """Subscribe to HA ``state_changed`` events until stopped or disconnected."""
    base_url, token = _config()
    if not base_url or not token:
        raise RuntimeError("Home Assistant is not configured yet.")

    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is required for live Home Assistant events.") from exc

    ws = websocket.create_connection(_websocket_url(base_url), timeout=5)
    try:
        hello = json.loads(ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError("Unexpected Home Assistant WebSocket greeting.")
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant WebSocket authentication failed: {auth.get('message', '')}")

        subscription_id = 1
        ws.send(json.dumps({
            "id": subscription_id,
            "type": "subscribe_events",
            "event_type": "state_changed",
        }))
        subscribed = json.loads(ws.recv())
        if not subscribed.get("success"):
            raise RuntimeError("Home Assistant rejected the state event subscription.")
        if on_connected is not None:
            on_connected()

        ws.settimeout(1)
        while not stop_event.is_set():
            try:
                message = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if message.get("type") != "event" or message.get("id") != subscription_id:
                continue
            event = message.get("event", {})
            data = event.get("data", {})
            on_change(data)
    finally:
        ws.close()


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


def set_light_brightness(target: str, brightness_pct: int) -> str:
    """Set one uniquely matched light to a validated percentage."""
    target = _normalize_text(target)
    if not target:
        return "I need the name of the light."
    if isinstance(brightness_pct, bool) or not isinstance(brightness_pct, int):
        return "Brightness must be a whole percentage from 0 to 100."
    if not 0 <= brightness_pct <= 100:
        return "Brightness must be between 0 and 100 percent."

    success, entity_or_message = _find_best_entity(target, ["light"])
    if not success:
        return entity_or_message
    service = "turn_off" if brightness_pct == 0 else "turn_on"
    data = {} if brightness_pct == 0 else {"brightness_pct": brightness_pct}
    success, message = call_service("light", service, entity_or_message, data)
    return message if success else f"Command failed. {message}"


_LIGHT_COLORS = {
    "blue", "cyan", "green", "magenta", "orange", "pink", "purple", "red",
    "white", "yellow",
}


def set_light_color(target: str, color: str) -> str:
    """Set one uniquely matched light to a small, explicit named-color allowlist."""
    target = _normalize_text(target)
    color = _normalize_text(color)
    if not target:
        return "I need the name of the light."
    if color not in _LIGHT_COLORS:
        return "That color is not in the supported safe color list."
    success, entity_or_message = _find_best_entity(target, ["light"])
    if not success:
        return entity_or_message
    success, message = call_service(
        "light", "turn_on", entity_or_message, {"color_name": color},
    )
    return message if success else f"Command failed. {message}"


def set_climate_temperature(target: str, temperature: float, unit: str) -> str:
    """Set one thermostat within conservative residential temperature bounds."""
    target = _normalize_text(target)
    unit = str(unit or "").strip().upper().replace("°", "")
    if not target:
        return "I need the name of the thermostat."
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return "Temperature must be a number."
    temperature = float(temperature)
    if not math.isfinite(temperature) or unit not in {"F", "C"}:
        return "Temperature must include a valid Fahrenheit or Celsius unit."
    limits = (45.0, 90.0) if unit == "F" else (7.0, 32.0)
    if not limits[0] <= temperature <= limits[1]:
        return f"Temperature must be between {limits[0]:g} and {limits[1]:g} degrees {unit}."

    success, entity_or_message = _find_best_entity(target, ["climate"])
    if not success:
        return entity_or_message
    success, states = get_states()
    if not success:
        return str(states)
    if not isinstance(states, list):
        return "Unexpected Home Assistant states response."
    entity = next(
        (item for item in states if isinstance(item, dict) and item.get("entity_id") == entity_or_message),
        None,
    )
    attributes = entity.get("attributes", {}) if isinstance(entity, dict) else {}
    configured_unit = str(attributes.get("temperature_unit") or "").upper().replace("°", "")
    if configured_unit not in {"F", "C"}:
        return "I could not determine that thermostat's temperature unit safely."
    converted = temperature
    if unit == "F" and configured_unit == "C":
        converted = (temperature - 32) * 5 / 9
    elif unit == "C" and configured_unit == "F":
        converted = temperature * 9 / 5 + 32
    converted = round(converted, 1)
    success, message = call_service(
        "climate", "set_temperature", entity_or_message, {"temperature": converted},
    )
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
