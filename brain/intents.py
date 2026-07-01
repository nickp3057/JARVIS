"""Intent handlers for the JARVIS assistant.

Each handler takes the raw user input and returns a :class:`IntentResult`
describing what should happen next. A dispatch table maps intent names to
handlers, so adding behavior is just adding a function and an entry.

Handlers never raise. A handler that blows up is caught by
:func:`handle_intent`, logged, and surfaced to the user as a friendly error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from integrations.home_assistant import (
    handle_device_command,
    list_entities,
    test_connection,
)
from jarvis_logger import get_logger
from speech.tts import speak

log = get_logger(__name__)


def _address() -> str:
    """Honor the live ADDRESS setting so the fallback matches the main brain."""
    return (os.getenv("ADDRESS") or "sir").strip() or "sir"


@dataclass(frozen=True)
class IntentResult:
    """Result of handling one user intent."""

    keep_running: bool = True
    message: str = ""


IntentHandler = Callable[[str], IntentResult]


def _say(message: str) -> None:
    """Print ``message`` with a Jarvis prefix and speak it aloud."""
    print(f"Jarvis: {message}")
    speak(message)


def _print_entity_list(title: str, entities: list[str]) -> None:
    print(f"Jarvis: {title}")
    if not entities:
        print("Jarvis:   None found.")
        return
    for entity in entities:
        print(f"Jarvis:   {entity}")


def _list_domain(title: str, domain: str) -> IntentResult:
    success, result = list_entities(domain)
    if success and isinstance(result, list):
        _print_entity_list(title, result)
        return IntentResult(message=_format_entity_list(title, result))
    else:
        _say(str(result))
        return IntentResult(message=str(result))


def _format_entity_list(title: str, entities: list[str]) -> str:
    if not entities:
        return f"{title} None found."
    return f"{title} " + ", ".join(entities)


# --- Handlers ---------------------------------------------------------------

def _handle_exit(_: str) -> IntentResult:
    message = f"Shutting down. See you, {_address()}."
    _say(message)
    return IntentResult(keep_running=False, message=message)


def _handle_time(_: str) -> IntentResult:
    current_time = datetime.now().strftime("%I:%M %p")
    message = f"The time is {current_time}."
    _say(message)
    return IntentResult(message=message)


def _handle_ha_test(_: str) -> IntentResult:
    _, message = test_connection()
    _say(message)
    return IntentResult(message=message)


def _handle_list_lights(_: str) -> IntentResult:
    return _list_domain("Here are your lights:", "light")


def _handle_list_switches(_: str) -> IntentResult:
    return _list_domain("Here are your switches:", "switch")


def _handle_list_fans(_: str) -> IntentResult:
    return _list_domain("Here are your fans:", "fan")


def _handle_list_devices(_: str) -> IntentResult:
    success_lights, lights = list_entities("light")
    success_switches, switches = list_entities("switch")
    success_fans, fans = list_entities("fan")

    if success_lights and success_switches and success_fans:
        _print_entity_list("Lights:", lights)  # type: ignore[arg-type]
        _print_entity_list("Switches:", switches)  # type: ignore[arg-type]
        _print_entity_list("Fans:", fans)  # type: ignore[arg-type]
        return IntentResult(
            message=" ".join(
                (
                    _format_entity_list("Lights:", lights),  # type: ignore[arg-type]
                    _format_entity_list("Switches:", switches),  # type: ignore[arg-type]
                    _format_entity_list("Fans:", fans),  # type: ignore[arg-type]
                )
            )
        )
    else:
        message = "I hit an error while listing devices."
        _say(message)
        return IntentResult(message=message)


def _handle_device_control(user_input: str) -> IntentResult:
    response = handle_device_command(user_input)
    _say(response)
    return IntentResult(message=response)


def _handle_unknown(_: str) -> IntentResult:
    message = "I did not understand that command yet."
    _say(message)
    return IntentResult(message=message)


_DISPATCH: dict[str, IntentHandler] = {
    "exit": _handle_exit,
    "time": _handle_time,
    "ha_test": _handle_ha_test,
    "list_lights": _handle_list_lights,
    "list_switches": _handle_list_switches,
    "list_fans": _handle_list_fans,
    "list_devices": _handle_list_devices,
    "device_control": _handle_device_control,
    "unknown": _handle_unknown,
}


def handle_intent(intent: str, user_input: str) -> bool:
    """Dispatch ``intent`` and return whether the REPL should keep running."""
    handler = _DISPATCH.get(intent, _handle_unknown)
    log.info("Dispatch intent=%s input=%r", intent, user_input)

    try:
        result = handler(user_input)
    except Exception:  # noqa: BLE001 - we never want handlers to crash the REPL
        log.exception("Handler for intent=%s raised", intent)
        _say("Something went wrong handling that. I logged the details.")
        return True

    return result.keep_running


def fallback_reply(user_input: str) -> str:
    """Return a text-only reply for the offline keyword router.

    This is used by the GUI and agent fallback path, so it deliberately does
    not print or speak. The older ``handle_intent`` function keeps those side
    effects for text mode.
    """
    from brain.router import route_command

    intent = route_command(user_input)
    log.info("Fallback reply intent=%s input=%r", intent, user_input)

    try:
        if intent == "exit":
            return "I can close down from text mode, but the window stays open here."
        if intent == "time":
            current_time = datetime.now().strftime("%I:%M %p")
            return f"The time is {current_time}."
        if intent == "ha_test":
            _, message = test_connection()
            return message
        if intent == "list_lights":
            success, result = list_entities("light")
            return _format_entity_list("Lights:", result) if success else str(result)
        if intent == "list_switches":
            success, result = list_entities("switch")
            return _format_entity_list("Switches:", result) if success else str(result)
        if intent == "list_fans":
            success, result = list_entities("fan")
            return _format_entity_list("Fans:", result) if success else str(result)
        if intent == "list_devices":
            parts: list[str] = []
            for title, domain in (("Lights:", "light"), ("Switches:", "switch"), ("Fans:", "fan")):
                success, result = list_entities(domain)
                if not success:
                    return str(result)
                parts.append(_format_entity_list(title, result))
            return " ".join(parts)
        if intent == "device_control":
            return handle_device_command(user_input)
    except Exception:  # noqa: BLE001
        log.exception("Fallback reply failed")
        return "Something went wrong handling that. I logged the details."

    return (
        f"My higher reasoning is offline, {_address()}, so I only know "
        "basic commands right now."
    )
