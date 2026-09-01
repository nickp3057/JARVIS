"""Maps free-form user text to a canonical intent name.

The router is deliberately keyword-based and dependency-free so it stays easy
to read and extend. Each rule is a ``(predicate, intent)`` pair checked in
order; the first match wins. When you add a new intent, add the rule here and
the handler in ``brain.intents``.
"""

from __future__ import annotations

from typing import Callable

Predicate = Callable[[str], bool]
Rule = tuple[Predicate, str]


def _contains_all(*needles: str) -> Predicate:
    return lambda command: all(needle in command for needle in needles)


def _contains_any(*needles: str) -> Predicate:
    return lambda command: any(needle in command for needle in needles)


def _is_device_control(command: str) -> bool:
    return (
        "turn on" in command
        or "turn off" in command
        or command.endswith(" on")
        or command.endswith(" off")
    )


def _is_home_status(command: str) -> bool:
    """Recognize common read-only state questions before on/off commands."""
    question = command.startswith((
        "is ", "are ", "what ", "what's ", "which ", "show ", "tell me ",
    ))
    home_words = (
        "light", "lamp", "switch", "plug", "outlet", "fan", "thermostat",
        "temperature", "speaker", "media", "sensor", "lock", "blind", "shade",
    )
    state_words = (
        " on", " off", "running", "active", "open", "closed", "locked",
        "unlocked", "playing", "paused", "status", "state",
    )
    return question and any(word in command for word in home_words) and any(
        word in command for word in state_words
    )


_RULES: tuple[Rule, ...] = (
    (_contains_any("exit", "quit", "bye"), "exit"),
    (_contains_any("time"), "time"),
    (_contains_any("test home assistant", "ping home assistant"), "ha_test"),
    (_contains_all("list", "lights"), "list_lights"),
    (_contains_all("list", "switches"), "list_switches"),
    (_contains_all("list", "fans"), "list_fans"),
    (_contains_all("list", "devices"), "list_devices"),
    (_is_home_status, "home_status"),
    (_is_device_control, "device_control"),
)


def route_command(command: str) -> str:
    """Return the intent name that best matches ``command``."""
    normalized = command.lower().strip()

    # The exit rule needs exact-word matching so we don't bail out on phrases
    # like "what time is it goodbye." Keep this list in sync with the rule.
    if normalized in {"exit", "quit", "bye"}:
        return "exit"

    for predicate, intent in _RULES:
        if intent == "exit":
            continue
        if predicate(normalized):
            return intent

    return "unknown"
