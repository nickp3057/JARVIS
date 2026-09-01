"""Tool definitions exposed to the Claude agent.

Each custom tool is a thin wrapper around an existing function in the
codebase, plus an Anthropic-shaped schema describing its inputs. The single
``TOOL_SPECS`` list is what we send to the API; the ``execute_tool``
dispatcher is what we call when Claude returns a tool_use block.

The list also contains Anthropic's built-in *server tools* (like
``web_search``) - those are executed on Anthropic's side, so we just declare
them and don't dispatch them locally.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable

from brain.apps import close_app, focus_app, list_open_apps, open_app
from brain.automation_workshop import (
    apply_automation_draft,
    draft_automation,
    inspect_automation,
    inventory_automations,
    rollback_automation_change,
)
from brain.clipboard_tool import read_clipboard, write_clipboard
from brain.debug_reports import describe_debug_report, summarize_debug_reports
from brain.history import clear_history, history_stats
from brain.memory import (
    describe_facts,
    describe_contradiction_candidates,
    adjust_fact_confidence,
    forget_fact,
    remember_fact,
    remember_profile_record,
    update_fact,
)
from brain.memory_proposals import (
    apply_memory_proposal,
    describe_memory_proposals,
    propose_memory_fact,
    reject_memory_proposal,
)
from brain.history import describe_session_summaries
from brain.knowledge_vault import describe_search
from brain.timers import cancel_timer, dismiss_timer, list_timers, pause_timer, resume_timer, set_timer
from brain.reminders import add_reminder, complete_reminder, delete_reminder, list_reminders
from brain.reminders import _format_due as _format_reminder_due
from brain.settings import get_setting, list_settings, update_setting
from brain.vision import describe_screen
from brain.weather import get_weather
from integrations import radarr as radarr
from integrations import qbittorrent as qbittorrent
from integrations import servarr_common as servarr
from integrations import sonarr as sonarr
from integrations import spotify as spotify
from integrations.home_assistant import (
    activate_home_action,
    call_discovered_service,
    describe_home_status,
    describe_services,
    handle_device_command,
    list_entities,
    test_connection,
)
from jarvis_logger import get_logger

log = get_logger(__name__)


def _web_search_enabled() -> bool:
    return os.getenv("WEB_SEARCH_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


# --- Tool implementations ---------------------------------------------------

def _tool_get_time(_: dict[str, Any]) -> str:
    now = datetime.now()
    hour_minute = now.strftime("%I:%M").lstrip("0")
    am_pm = now.strftime("%p")
    weekday = now.strftime("%A")
    month = now.strftime("%B")
    day = now.day
    return f"It is {hour_minute} {am_pm} on {weekday}, {month} {day}."


def _tool_control_device(args: dict[str, Any]) -> str:
    command = args.get("command", "")
    if not command:
        return "Tool error: missing 'command'."
    return handle_device_command(command)


def _tool_list_devices(args: dict[str, Any]) -> str:
    domain = args.get("domain")
    success, result = list_entities(domain)
    if not success:
        return f"Tool error: {result}"
    if not result:
        return f"No {domain or 'matching'} entities found."
    return ", ".join(result)  # type: ignore[arg-type]


def _tool_home_status(args: dict[str, Any]) -> str:
    return describe_home_status(str(args.get("query") or ""))


def _tool_test_home_assistant(_: dict[str, Any]) -> str:
    _, message = test_connection()
    return message


def _tool_activate_home_action(args: dict[str, Any]) -> str:
    return activate_home_action(args.get("kind", ""), args.get("name", ""))


def _tool_list_home_services(args: dict[str, Any]) -> str:
    domain = args.get("domain")
    success, result = describe_services(domain)
    if not success:
        return f"Tool error: {result}"
    if not result:
        return f"No Home Assistant services found for {domain or 'any domain'}."
    return ", ".join(result)  # type: ignore[arg-type]


def _tool_call_home_service(args: dict[str, Any]) -> str:
    return call_discovered_service(
        args.get("domain", ""),
        args.get("service", ""),
        entity_id=args.get("entity_id"),
        service_data=args.get("service_data"),
        confirmed=bool(args.get("confirmed", False)),
    )


def _tool_list_automations(_: dict[str, Any]) -> str:
    return inventory_automations()


def _tool_inspect_automation(args: dict[str, Any]) -> str:
    return inspect_automation(args.get("identifier", ""))


def _tool_draft_automation(args: dict[str, Any]) -> str:
    return draft_automation(
        args.get("operation", ""),
        identifier=args.get("identifier"),
        config=args.get("config"),
    )


def _tool_apply_automation_draft(args: dict[str, Any]) -> str:
    return apply_automation_draft(
        args.get("draft_id", ""),
        confirmed=bool(args.get("confirmed", False)),
    )


def _tool_rollback_automation_change(args: dict[str, Any]) -> str:
    return rollback_automation_change(
        args.get("change_id", ""),
        confirmed=bool(args.get("confirmed", False)),
    )


def _tool_remember_fact(args: dict[str, Any]) -> str:
    return remember_fact(
        args.get("category", "general"),
        args.get("content", ""),
        key=args.get("key", ""),
        confidence=args.get("confidence"),
        source=args.get("source", "conversation"),
    )


def _tool_remember_profile_record(args: dict[str, Any]) -> str:
    return remember_profile_record(
        args.get("record_type", ""),
        args.get("name", ""),
        args.get("detail", ""),
        confidence=args.get("confidence"),
        source=args.get("source", "conversation"),
    )


def _tool_forget_fact(args: dict[str, Any]) -> str:
    return forget_fact(args.get("fact_id", ""))


def _tool_update_fact(args: dict[str, Any]) -> str:
    return update_fact(
        args.get("fact_id", ""),
        args.get("content", ""),
        args.get("category"),
        key=args.get("key"),
        confidence=args.get("confidence"),
        source=args.get("source"),
    )


def _tool_adjust_fact_confidence(args: dict[str, Any]) -> str:
    return adjust_fact_confidence(
        args.get("fact_id", ""), args.get("signal", ""), args.get("amount"),
    )


def _tool_list_memory_facts(args: dict[str, Any]) -> str:
    return describe_facts(args.get("category", ""), args.get("query", ""))


def _tool_find_memory_contradictions(args: dict[str, Any]) -> str:
    return describe_contradiction_candidates(
        args.get("category", "general"), args.get("content", ""),
    )


def _tool_propose_memory_fact(args: dict[str, Any]) -> str:
    return propose_memory_fact(
        args.get("category", "general"),
        args.get("content", ""),
        key=args.get("key", ""),
        confidence=args.get("confidence", 0.7),
        evidence=args.get("evidence", ""),
    )


def _tool_list_memory_proposals(args: dict[str, Any]) -> str:
    return describe_memory_proposals(args.get("status", "pending"))


def _tool_apply_memory_proposal(args: dict[str, Any]) -> str:
    return apply_memory_proposal(
        args.get("proposal_id", ""), confirmed=bool(args.get("confirmed", False)),
    )


def _tool_reject_memory_proposal(args: dict[str, Any]) -> str:
    return reject_memory_proposal(args.get("proposal_id", ""))


def _tool_search_memory(args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "Tool error: missing 'query'."
    return describe_search(query)


def _tool_search_session_summaries(args: dict[str, Any]) -> str:
    try:
        limit = int(args.get("limit", 5) or 5)
    except (TypeError, ValueError):
        limit = 5
    return describe_session_summaries(str(args.get("query", "")), limit)


def _tool_list_debug_reports(_: dict[str, Any]) -> str:
    return summarize_debug_reports()


def _tool_inspect_debug_report(args: dict[str, Any]) -> str:
    return describe_debug_report(str(args.get("report_id", "")))


def _tool_get_weather(args: dict[str, Any]) -> str:
    return get_weather(args.get("location"))


def _tool_set_timer(args: dict[str, Any]) -> str:
    duration_seconds = float(args.get("duration_seconds", 0) or 0)
    duration_minutes = float(args.get("duration_minutes", 0) or 0)
    duration_hours = float(args.get("duration_hours", 0) or 0)
    total = duration_seconds + duration_minutes * 60 + duration_hours * 3600
    if total <= 0:
        return "Tool error: timer duration must be positive."
    return set_timer(total, args.get("message", "Timer up"))


def _tool_cancel_timer(args: dict[str, Any]) -> str:
    return cancel_timer(args.get("timer_id", ""))

def _tool_pause_timer(args: dict[str, Any]) -> str:
    return pause_timer(args.get("timer_id", ""))

def _tool_resume_timer(args: dict[str, Any]) -> str:
    return resume_timer(args.get("timer_id", ""))

def _tool_dismiss_timer(args: dict[str, Any]) -> str:
    return dismiss_timer(args.get("timer_id", ""))


def _tool_list_timers(_: dict[str, Any]) -> str:
    return list_timers()


def _tool_play_music(args: dict[str, Any]) -> str:
    return spotify.play_music(args.get("query", ""))


def _tool_pause_music(_: dict[str, Any]) -> str:
    return spotify.pause_music()


def _tool_resume_music(_: dict[str, Any]) -> str:
    return spotify.resume_music()


def _tool_skip_track(_: dict[str, Any]) -> str:
    return spotify.skip_track()


def _tool_previous_track(_: dict[str, Any]) -> str:
    return spotify.previous_track()


def _tool_current_track(_: dict[str, Any]) -> str:
    return spotify.current_track()


def _tool_set_volume(args: dict[str, Any]) -> str:
    percent = args.get("percent")
    if percent is None:
        return "Tool error: missing 'percent' (0-100)."
    return spotify.set_volume(int(percent))


def _tool_list_speakers(_: dict[str, Any]) -> str:
    return spotify.list_speakers()


def _tool_transfer_playback(args: dict[str, Any]) -> str:
    return spotify.transfer_playback(args.get("device_name", ""))




def _tool_open_app(args: dict[str, Any]) -> str:
    return open_app(args.get("name", ""))

def _tool_close_app(args: dict[str, Any]) -> str:
    return close_app(args.get("name", ""))

def _tool_focus_app(args: dict[str, Any]) -> str:
    return focus_app(args.get("name", ""))

def _tool_list_open_apps(_: dict[str, Any]) -> str:
    return list_open_apps()

def _tool_read_clipboard(_: dict[str, Any]) -> str:
    return read_clipboard()

def _tool_write_clipboard(args: dict[str, Any]) -> str:
    return write_clipboard(args.get("text", ""))

def _tool_look_at_screen(args: dict[str, Any]) -> str:
    question = args.get("question", "What's on the screen right now?")
    return describe_screen(question)

def _tool_clear_conversation(_: dict[str, Any]) -> str:
    return clear_history()


def _tool_conversation_stats(_: dict[str, Any]) -> str:
    return history_stats()


def _tool_update_setting(args: dict[str, Any]) -> str:
    return update_setting(args.get("key", ""), args.get("value", ""))


def _tool_get_setting(args: dict[str, Any]) -> str:
    return get_setting(args.get("key", ""))


def _tool_list_settings(_: dict[str, Any]) -> str:
    return list_settings()


def _tool_morning_briefing(_: dict[str, Any]) -> str:
    from brain.briefing import compose_briefing
    return compose_briefing()


def _tool_add_reminder(args: dict[str, Any]) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return "Tool error: missing reminder text."
    due_at = None
    due_iso = args.get("due_iso")
    in_minutes = args.get("in_minutes")
    in_hours = args.get("in_hours")
    if due_iso:
        try:
            due_at = datetime.fromisoformat(str(due_iso)).timestamp()
        except ValueError:
            return "Tool error: due_iso must look like 2026-06-16T15:00."
    elif in_minutes or in_hours:
        secs = float(in_minutes or 0) * 60 + float(in_hours or 0) * 3600
        if secs > 0:
            due_at = datetime.now().timestamp() + secs
    reminder = add_reminder(text, due_at)
    if due_at is not None:
        return f"Reminder set for {_format_reminder_due(due_at)}: {text}. (id {reminder.id})"
    return f"Added to your list: {text}. (id {reminder.id})"


def _tool_list_reminders(_: dict[str, Any]) -> str:
    return list_reminders()


def _tool_complete_reminder(args: dict[str, Any]) -> str:
    return complete_reminder(args.get("reminder_id", ""))


def _tool_delete_reminder(args: dict[str, Any]) -> str:
    return delete_reminder(args.get("reminder_id", ""))


def _tool_find_movie(args: dict[str, Any]) -> str:
    return radarr.find_movie(args.get("title", ""), session_id=args.get("_jarvis_session_id"))


def _tool_add_movie(args: dict[str, Any]) -> str:
    return radarr.add_movie(
        args.get("tmdb_id", ""),
        quality=args.get("quality"),
        root_folder=args.get("root_folder"),
        confirmed=bool(args.get("confirmed", False)),
        session_id=args.get("_jarvis_session_id"),
    )


def _tool_find_series(args: dict[str, Any]) -> str:
    return sonarr.find_series(args.get("title", ""), session_id=args.get("_jarvis_session_id"))


def _tool_add_series(args: dict[str, Any]) -> str:
    return sonarr.add_series(
        args.get("tvdb_id", ""),
        quality=args.get("quality"),
        root_folder=args.get("root_folder"),
        monitor=args.get("monitor"),
        season_folder=bool(args.get("season_folder", True)),
        confirmed=bool(args.get("confirmed", False)),
        session_id=args.get("_jarvis_session_id"),
    )


def _tool_list_quality_profiles(args: dict[str, Any]) -> str:
    service = (args.get("service") or "").strip().lower()
    services = [service] if service in ("radarr", "sonarr") else ["radarr", "sonarr"]
    lines: list[str] = []
    for svc in services:
        if not servarr.is_configured(svc):
            lines.append(f"{svc.title()} isn't configured.")
            continue
        names = servarr.list_profile_names(svc)
        if names:
            lines.append(f"{svc.title()} quality profiles: {', '.join(names)}.")
        else:
            lines.append(f"{svc.title()}: I couldn't read its quality profiles.")
    return " ".join(lines)


def _tool_download_status(args: dict[str, Any]) -> str:
    return qbittorrent.download_status(args.get("scope", "downloading"))


def _tool_torrent_inventory(args: dict[str, Any]) -> str:
    return qbittorrent.torrent_inventory(args.get("limit", 20))


def _tool_manage_torrent(args: dict[str, Any]) -> str:
    return qbittorrent.manage_torrent(
        args.get("action", ""),
        args.get("torrent_hash", ""),
        confirmed=bool(args.get("confirmed", False)),
        delete_files=bool(args.get("delete_files", False)),
    )


def _tool_test_qbittorrent(_: dict[str, Any]) -> str:
    return qbittorrent.test_connection()


def _tool_media_queue(args: dict[str, Any]) -> str:
    return servarr.queue_status(args.get("service"))


def _tool_media_health(args: dict[str, Any]) -> str:
    return servarr.health_status(args.get("service"))


def _media_service(args: dict[str, Any]):
    service = (args.get("service") or "").strip().lower()
    return {"radarr": radarr, "sonarr": sonarr}.get(service)


def _tool_media_history(args: dict[str, Any]) -> str:
    service = _media_service(args)
    return service.history(args.get("limit", 10)) if service else "Choose radarr or sonarr."


def _tool_missing_media(args: dict[str, Any]) -> str:
    service = _media_service(args)
    return service.missing(args.get("limit", 10)) if service else "Choose radarr or sonarr."


def _tool_upcoming_media(args: dict[str, Any]) -> str:
    service = _media_service(args)
    return service.upcoming(args.get("days", 14)) if service else "Choose radarr or sonarr."


def _tool_todays_agenda(_: dict[str, Any]) -> str:
    from integrations.google_calendar import todays_agenda
    return todays_agenda()


def _tool_next_event(_: dict[str, Any]) -> str:
    from integrations.google_calendar import next_event_spoken
    return next_event_spoken()


# --- Anthropic tool schemas -------------------------------------------------

_CUSTOM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_time",
        "description": "Get the current local time and date. Use this any time the user asks what time or day it is.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "control_device",
        "description": (
            "Turn a device on or off via Home Assistant. Pass the user's "
            "natural-language phrasing (e.g. 'turn on the desk fan') as the "
            "command - the integration does fuzzy matching against known "
            "entities. Use this for any lights, fans, switches, plugs, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The user's device command in natural language."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_devices",
        "description": "List entities of a given domain (light, switch, fan) from Home Assistant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Entity domain to filter by, e.g. 'light', 'switch', 'fan'. Omit to list everything."}
            },
        },
    },
    {
        "name": "home_status",
        "description": (
            "Answer a read-only question about current Home Assistant state using "
            "friendly device names. Use this for questions such as which lights are "
            "on, whether a named device is off, which fans are running, or a thermostat's "
            "current state. Pass the user's concise state question as query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "State, domain, or device-name filter in natural language.",
                }
            },
        },
    },
    {
        "name": "test_home_assistant",
        "description": "Ping Home Assistant to confirm the connection is healthy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "activate_home_action",
        "description": (
            "Activate a Home Assistant scene, script, or automation by name. "
            "Use this when Nick asks to run, trigger, start, or activate one "
            "of those Home Assistant actions. This does not create or edit "
            "automations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["scene", "script", "automation"],
                    "description": "The Home Assistant entity domain.",
                },
                "name": {
                    "type": "string",
                    "description": "Natural-language name of the scene, script, or automation.",
                },
            },
            "required": ["kind", "name"],
        },
    },
    {
        "name": "list_home_services",
        "description": (
            "Discover the live Home Assistant service/action catalogue. Use "
            "this when a request needs more than basic on/off control, then "
            "choose an advertised domain.service name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter, such as light, climate, media_player, or vacuum.",
                },
            },
        },
    },
    {
        "name": "call_home_service",
        "description": (
            "Call a service discovered with list_home_services. Use this for "
            "brightness, color, thermostat, media-player, vacuum, and other "
            "Home Assistant capabilities beyond basic on/off. Administrative "
            "services are blocked. Locks, alarms, covers, buttons, notifications, "
            "and actions containing unlock/disarm/open/press require Nick's "
            "explicit confirmation of the exact action before confirmed=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain, e.g. light or climate."},
                "service": {"type": "string", "description": "Advertised service name, e.g. turn_on or set_temperature."},
                "entity_id": {"type": "string", "description": "Optional exact target entity id."},
                "service_data": {
                    "type": "object",
                    "description": "Optional service fields such as brightness_pct, rgb_color, or temperature.",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "True only after Nick explicitly confirms a guarded action. False by default.",
                },
            },
            "required": ["domain", "service"],
        },
    },
    {
        "name": "list_automations",
        "description": "List Home Assistant automations with their names, IDs, entity IDs, and enabled states.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_automation",
        "description": (
            "Read the complete current configuration of one Home Assistant automation "
            "by exact ID, entity ID, or unambiguous name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Automation ID, entity ID, or name."},
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "draft_automation",
        "description": (
            "Stage and validate a Home Assistant automation create, update, or delete. "
            "This only creates a local draft and returns a diff; it does not change the "
            "house. For update/delete, inspect the automation first and pass its name or "
            "ID. For create/update, provide the complete configuration object. After "
            "drafting, explain the behavior and diff, then wait for Nick's explicit approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["create", "update", "delete"]},
                "identifier": {"type": "string", "description": "Required for update/delete."},
                "config": {"type": "object", "description": "Complete proposed automation config for create/update."},
            },
            "required": ["operation"],
        },
    },
    {
        "name": "apply_automation_draft",
        "description": (
            "Apply one previously reviewed automation draft. ONLY call after Nick explicitly "
            "approves that exact draft ID in a later message. The tool checks for concurrent "
            "changes, verifies the result, and automatically attempts rollback on failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "Must be true after explicit approval."},
            },
            "required": ["draft_id", "confirmed"],
        },
    },
    {
        "name": "rollback_automation_change",
        "description": (
            "Restore the backup from an applied automation change. ONLY call after Nick "
            "explicitly confirms rollback of that exact change ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "change_id": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "Must be true after explicit approval."},
            },
            "required": ["change_id", "confirmed"],
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a durable fact about Nick to long-term memory. Use this any "
            "time you learn something worth knowing across future conversations: "
            "people in his life, interests, preferences, projects, plans. Be "
            "selective - skip ephemera. Don't ask permission, just remember."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Short category label."},
                "content": {"type": "string", "description": "The fact, in a concise sentence."},
                "key": {
                    "type": "string",
                    "description": (
                        "Stable semantic attribute key such as preferences.favorite_color. "
                        "Reuse the same key when the value changes so memory is revised in place."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "Certainty from 0 to 1; use 1 for Nick's explicit statements.",
                },
                "source": {
                    "type": "string",
                    "description": "Short provenance label such as conversation, inferred, or imported.",
                },
            },
            "required": ["category", "content"],
        },
    },
    {
        "name": "forget_fact",
        "description": "Remove a fact from memory by its id.",
        "input_schema": {
            "type": "object",
            "properties": {"fact_id": {"type": "string", "description": "The 8-character id."}},
            "required": ["fact_id"],
        },
    },
    {
        "name": "remember_profile_record",
        "description": (
            "Save or revise one structured person, preference, project, routine, or goal. "
            "The type and name form a stable semantic key, so later details replace the "
            "same record with revision history instead of creating duplicates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "record_type": {"type": "string", "enum": ["person", "preference", "project", "routine", "goal"]},
                "name": {"type": "string", "description": "Stable subject or attribute name."},
                "detail": {"type": "string", "description": "Current concise detail."},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source": {"type": "string"},
            },
            "required": ["record_type", "name", "detail"],
        },
    },
    {
        "name": "update_fact",
        "description": (
            "Correct or replace an existing durable fact by id. Prefer this over "
            "forgetting and re-adding when new information contradicts a memory; "
            "it preserves a bounded revision trail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string", "description": "The existing 8-character id."},
                "content": {"type": "string", "description": "The corrected fact."},
                "category": {"type": "string", "description": "Optional corrected category."},
                "key": {"type": "string", "description": "Optional stable semantic attribute key."},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "source": {"type": "string", "description": "Optional provenance label."},
            },
            "required": ["fact_id", "content"],
        },
    },
    {
        "name": "list_memory_facts",
        "description": (
            "List durable profile memories with ids and revision metadata. Use when Nick "
            "asks what you remember or when you need an id to correct or forget a fact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Optional exact category filter."},
                "query": {"type": "string", "description": "Optional text filter."},
            },
        },
    },
    {
        "name": "adjust_fact_confidence",
        "description": (
            "Explicitly reinforce or decay one remembered fact's confidence by ID. "
            "Use only when evidence was confirmed or weakened; this records an audit timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                "signal": {"type": "string", "enum": ["reinforce", "decay"]},
                "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            },
            "required": ["fact_id", "signal"],
        },
    },
    {
        "name": "propose_memory_fact",
        "description": (
            "Stage an inferred or extracted long-term memory for review without writing it "
            "to the durable profile. Use for model-inferred facts; explicit statements may "
            "still use remember_fact directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["person", "preference", "routine", "project", "goal", "general"]},
                "content": {"type": "string"},
                "key": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence": {"type": "string", "description": "Short source excerpt supporting the proposal."},
            },
            "required": ["category", "content"],
        },
    },
    {
        "name": "find_memory_contradictions",
        "description": (
            "Find legacy unkeyed memories that may conflict with a proposed fact. "
            "This is advisory only and never changes memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["category", "content"],
        },
    },
    {
        "name": "list_memory_proposals",
        "description": "List reviewable memory proposals and their stable IDs.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["pending", "applied", "rejected", "all"]}},
        },
    },
    {
        "name": "apply_memory_proposal",
        "description": (
            "Apply one exact pending memory proposal. Only call after Nick explicitly "
            "approves that proposal ID in a later message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
            "required": ["proposal_id", "confirmed"],
        },
    },
    {
        "name": "reject_memory_proposal",
        "description": "Reject one pending memory proposal by exact ID without changing durable memory.",
        "input_schema": {
            "type": "object",
            "properties": {"proposal_id": {"type": "string"}},
            "required": ["proposal_id"],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Search Nick's explicitly authorized Obsidian Markdown notes. Use when he asks "
            "you to recall details that may live in his notes. Results include source paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_session_summaries",
        "description": (
            "Deliberately search compact summaries of prior conversation sessions. Use "
            "when Nick asks what you discussed or accomplished before. Do not call for "
            "ordinary turns; current companion sessions remain isolated by default."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional topic to find."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        },
    },
    {
        "name": "list_debug_reports",
        "description": (
            "List recent issue reports Nick explicitly submitted from the companion app. "
            "Use when he asks what went wrong, what needs debugging, or to review feedback."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_debug_report",
        "description": (
            "Inspect one companion debug report, including Nick's issue description, "
            "app context, focused response, and submitted conversation transcript."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"report_id": {"type": "string"}},
            "required": ["report_id"],
        },
    },
    {
        "name": "clear_conversation",
        "description": (
            "Wipe the persistent conversation history database. Use when Nick "
            "says 'forget our conversation', 'clear your memory of our chats', "
            "'start fresh', etc. Does NOT affect the long-term memory profile "
            "(profile.json) - use forget_fact for individual memory items."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "conversation_history_stats",
        "description": "Report how many conversation turns are stored in long-term history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_weather",
        "description": (
            "Get current weather and today's forecast for a place. If no "
            "location is given, the default location from configuration is "
            "used. Format the location as 'City, State' or 'City, Country' "
            "when known (e.g. 'Herriman, Utah', 'Tokyo, Japan') - this gives "
            "the best geocoding hits. The lookup is forgiving though, so "
            "just 'Herriman' or 'Herriman Utah' (no comma) will also work. "
            "Don't ask the user to clarify - try the lookup first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name, optionally with state/country (e.g. 'Herriman, Utah'). Optional - defaults to DEFAULT_LOCATION.",
                }
            },
        },
    },
    {
        "name": "set_timer",
        "description": "Set a timer or reminder. JARVIS speaks the message when it expires.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_seconds": {"type": "number"},
                "duration_minutes": {"type": "number"},
                "duration_hours": {"type": "number"},
                "message": {"type": "string", "description": "What JARVIS should say when the timer fires."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "cancel_timer",
        "description": "Cancel an active timer by its 6-character id.",
        "input_schema": {
            "type": "object",
            "properties": {"timer_id": {"type": "string"}},
            "required": ["timer_id"],
        },
    },
    {
        "name": "pause_timer",
        "description": "Pause an active timer, freezing its countdown.",
        "input_schema": {
            "type": "object",
            "properties": {"timer_id": {"type": "string"}},
            "required": ["timer_id"],
        },
    },
    {
        "name": "resume_timer",
        "description": "Resume a previously paused timer from where it left off.",
        "input_schema": {
            "type": "object",
            "properties": {"timer_id": {"type": "string"}},
            "required": ["timer_id"],
        },
    },
    {
        "name": "dismiss_timer",
        "description": "Dismiss an alarm that has already gone off (removes the alarming timer from the display).",
        "input_schema": {
            "type": "object",
            "properties": {"timer_id": {"type": "string"}},
            "required": ["timer_id"],
        },
    },
    {
        "name": "list_timers",
        "description": "List all currently active timers and their remaining time.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "play_music",
        "description": (
            "Play music on Spotify. Pass any natural-language request - song, "
            "artist, album, genre, or vibe (e.g. 'Bohemian Rhapsody', 'play "
            "some jazz', 'lofi study music'). Plays on the preferred Spotify "
            "device if configured, else the active device, else the first "
            "available. Use for any 'play' request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to play."}},
            "required": ["query"],
        },
    },
    {
        "name": "pause_music",
        "description": "Pause Spotify playback.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resume_music",
        "description": "Resume Spotify playback after a pause.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "skip_track",
        "description": "Skip to the next track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "previous_track",
        "description": "Go back to the previous track on Spotify.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "current_track",
        "description": "Get the title and artist of the currently playing Spotify track.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_volume",
        "description": "Set Spotify playback volume from 0 to 100.",
        "input_schema": {
            "type": "object",
            "properties": {"percent": {"type": "integer", "description": "0 to 100."}},
            "required": ["percent"],
        },
    },
    {
        "name": "list_speakers",
        "description": (
            "List all currently available Spotify Connect devices (computers, "
            "phones, speakers, TVs). Use this when Nick asks 'what speakers do "
            "I have' or 'where can I play music'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "transfer_playback",
        "description": (
            "Move active Spotify playback to a different device by name. The "
            "match is case-insensitive substring (e.g. 'kitchen' matches "
            "'Kitchen Echo'). Use when Nick says 'play this on the X' or "
            "'move it to the Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"device_name": {"type": "string", "description": "Substring of the target device's name."}},
            "required": ["device_name"],
        },
    },
    # --- Computer control --------------------------------------------------
    {
        "name": "open_app",
        "description": (
            "Open an application on Nick's computer by name. Works for Chrome, "
            "Spotify, Discord, Cursor, VS Code, Terminal, File Explorer, etc. "
            "Use any natural-language name — 'browser', 'files', 'terminal'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "App name to open."}},
            "required": ["name"],
        },
    },
    {
        "name": "close_app",
        "description": "Close / quit an application on Nick's computer by name.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "focus_app",
        "description": "Bring an already-open application window to the foreground.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_open_apps",
        "description": "List all currently running applications that have a visible window.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Clipboard ---------------------------------------------------------
    {
        "name": "read_clipboard",
        "description": (
            "Read the current text content of Nick's clipboard. Use this when "
            "he says 'summarize what I copied', 'translate that', 'fix this', "
            "'what did I just copy', etc."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "write_clipboard",
        "description": "Write text to Nick's clipboard so he can paste it anywhere.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to copy to clipboard."}},
            "required": ["text"],
        },
    },
    # --- Vision ------------------------------------------------------------
    {
        "name": "look_at_screen",
        "description": (
            "Take a screenshot and use vision to answer a question about what's "
            "currently on Nick's screen. Use this whenever he says 'what's on my "
            "screen', 'what am I looking at', 'can you see this', 'look at my screen', "
            "or asks about something that would require seeing his display."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "What to look for or answer about the screen. Default: 'What's on the screen right now?'",
                }
            },
        },
    },

    {
        "name": "list_settings",
        "description": "List all editable runtime settings JARVIS can change (voice speed, humor level, default location, what to call Nick, etc.).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_setting",
        "description": "Read the current value of a single runtime setting by its key (e.g. HUMOR_PERCENT, ADDRESS, ELEVENLABS_SPEED, DEFAULT_LOCATION).",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "The setting key to read."}},
            "required": ["key"],
        },
    },
    {
        "name": "update_setting",
        "description": (
            "Change a runtime setting live, without a restart. Use for any "
            "'set X to Y' request and any time you infer a setting should "
            "change: ELEVENLABS_SPEED (0.7-1.2), HUMOR_PERCENT (0-100), "
            "ADDRESS (what to call Nick), DEFAULT_LOCATION, etc. Don't ask "
            "permission - just update and confirm in one sentence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "The setting key to change."},
                "value": {"type": "string", "description": "The new value."},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "morning_briefing",
        "description": (
            "Give Nick a short spoken briefing: greeting, the current time and "
            "date, today's weather, and any running timers. Use this whenever he "
            "says 'good morning', 'brief me', 'what's my day look like', 'give me "
            "the rundown', or asks for a status update on the day."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_reminder",
        "description": (
            "Add a reminder or to-do. For a timed reminder ('remind me at 3pm "
            "to call mom', 'in 20 minutes'), work out the time using the "
            "current time and pass either due_iso (local ISO 8601 like "
            "2026-06-16T15:00) or in_minutes / in_hours. For an open to-do "
            "with no time ('add milk to my list'), pass just the text. JARVIS "
            "speaks timed reminders aloud when they come due."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "What to be reminded of."},
                "due_iso": {"type": "string", "description": "Local ISO 8601 due time, e.g. 2026-06-16T15:00. Optional."},
                "in_minutes": {"type": "number", "description": "Minutes from now until due. Optional."},
                "in_hours": {"type": "number", "description": "Hours from now until due. Optional."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List all open reminders and to-dos with their ids and due times.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_reminder",
        "description": "Mark a reminder or to-do as done by its id (e.g. 'r1a2b3c4').",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "delete_reminder",
        "description": "Delete a reminder or to-do entirely by its id.",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "todays_agenda",
        "description": (
            "Read Nick's Google Calendar agenda for the rest of today. Use for "
            "'what's on my calendar', 'what's my day look like', 'am I free this "
            "afternoon'. Returns events with their times."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "next_event",
        "description": "Get Nick's next upcoming calendar event and when it starts.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # --- Media library (Radarr / Sonarr) ---------------------------------
    {
        "name": "find_movie",
        "description": (
            "Search Radarr for a movie by title. Use this FIRST whenever Nick "
            "asks to add / download / get a movie. It only searches - it never "
            "adds anything. Returns the best match(es) with year and TMDB id, "
            "or says no match / lists several. Show Nick the result(s) and wait "
            "for him to confirm which one before calling add_movie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Movie title, optionally with a year."}
            },
            "required": ["title"],
        },
    },
    {
        "name": "add_movie",
        "description": (
            "Add a movie to Radarr and start searching for it. ONLY call this "
            "after find_movie has returned a specific match AND Nick has "
            "explicitly confirmed that exact title in a prior turn. Never call "
            "it on an ambiguous or unconfirmed result. 'quality' is a natural "
            "phrase like '4k efficient' or '1080p' - it's fuzzy-matched to a "
            "configured quality profile; omit it to use the default. The runtime "
            "will reject any ID not returned by this session's latest lookup."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tmdb_id": {"type": "integer", "description": "TMDB id from a find_movie result."},
                "quality": {"type": "string", "description": "Natural-language quality phrase, e.g. '4k efficient', '1080p'. Optional."},
                "root_folder": {"type": "string", "description": "Root folder path override. Optional - defaults to the configured / first folder."},
                "confirmed": {"type": "boolean", "description": "Must be true, and only set it once Nick has explicitly confirmed this exact movie."},
            },
            "required": ["tmdb_id", "confirmed"],
        },
    },
    {
        "name": "find_series",
        "description": (
            "Search Sonarr for a TV show by title. Use this FIRST whenever Nick "
            "asks to add / download / get a show or series. It only searches - "
            "it never adds anything. Returns the best match(es) with year and "
            "TVDB id, or says no match / lists several. Show Nick the result(s) "
            "and wait for him to confirm which one before calling add_series."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Show title, optionally with a year."}
            },
            "required": ["title"],
        },
    },
    {
        "name": "add_series",
        "description": (
            "Add a TV show to Sonarr and start searching. ONLY call this after "
            "find_series has returned a specific match AND Nick has explicitly "
            "confirmed that exact title in a prior turn. Never call it on an "
            "ambiguous or unconfirmed result. 'quality' is a natural phrase "
            "fuzzy-matched to a quality profile. 'monitor' controls which "
            "episodes to watch for: default 'future' (new episodes only); use "
            "'all' for the whole back catalogue, or 'firstSeason' / 'pilot' / "
            "'latestSeason' / 'none' - accepts natural phrasing too. Omit optional "
            "choices to use defaults; never ask about them unless Nick requests them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tvdb_id": {"type": "integer", "description": "TVDB id from a find_series result."},
                "quality": {"type": "string", "description": "Natural-language quality phrase, e.g. '4k efficient', '1080p'. Optional."},
                "root_folder": {"type": "string", "description": "Root folder path override. Optional."},
                "monitor": {"type": "string", "description": "Monitor strategy: 'future' (default), 'all', 'firstSeason', 'latestSeason', 'lastSeason', 'pilot', 'none', or natural phrasing like 'whole series'."},
                "season_folder": {"type": "boolean", "description": "Organise episodes into per-season folders. Default true."},
                "confirmed": {"type": "boolean", "description": "Must be true, and only set it once Nick has explicitly confirmed this exact show."},
            },
            "required": ["tvdb_id", "confirmed"],
        },
    },
    {
        "name": "list_quality_profiles",
        "description": (
            "List the quality profiles defined in Radarr and/or Sonarr. Use "
            "when Nick asks what quality options he has, or when a quality "
            "phrase didn't match and you need to offer him the real names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "'radarr' or 'sonarr'. Omit for both."}
            },
        },
    },
    {
        "name": "download_status",
        "description": (
            "Get live qBittorrent transfer speeds and torrent progress, ETA, state, "
            "and remaining downloads. Use when Nick asks what is downloading, how "
            "long something has left, whether a torrent is stalled, or current speed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["downloading", "all", "completed", "seeding", "stalled", "errored", "active", "inactive"],
                    "description": "Defaults to downloading/incomplete torrents.",
                },
            },
        },
    },
    {
        "name": "test_qbittorrent",
        "description": "Test qBittorrent WebUI connectivity and report its version.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "torrent_inventory",
        "description": "List current torrents with exact hashes for a later confirmed operation.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    },
    {
        "name": "manage_torrent",
        "description": (
            "Pause, resume, recheck, reprioritize, or delete one exact live torrent. "
            "Only call after listing torrents and receiving explicit approval for the exact "
            "hash and action. Deleting downloaded files requires delete_files=true explicitly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["pause", "resume", "recheck", "priority_top", "priority_bottom", "priority_increase", "priority_decrease", "delete"]},
                "torrent_hash": {"type": "string", "description": "Full 40-character hash from torrent_inventory."},
                "confirmed": {"type": "boolean"},
                "delete_files": {"type": "boolean", "description": "Only meaningful for delete; defaults false."},
            },
            "required": ["action", "torrent_hash", "confirmed"],
        },
    },
    {
        "name": "media_queue",
        "description": (
            "Show Radarr and Sonarr activity queues with title, progress, remaining "
            "size, ETA, tracked status, and download errors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["radarr", "sonarr"]},
            },
        },
    },
    {
        "name": "media_health",
        "description": "Show current Radarr and Sonarr health warnings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["radarr", "sonarr"]},
            },
        },
    },
    {
        "name": "media_history",
        "description": "Show recent read-only Radarr or Sonarr import/download activity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["radarr", "sonarr"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["service"],
        },
    },
    {
        "name": "missing_media",
        "description": "List monitored movies or episodes that are currently missing files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["radarr", "sonarr"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["service"],
        },
    },
    {
        "name": "upcoming_media",
        "description": "Show monitored movie releases or TV episodes coming up soon.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "enum": ["radarr", "sonarr"]},
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
            },
            "required": ["service"],
        },
    },
]


# Map each tool name to the local function that executes it. Server tools
# (like web_search) are intentionally absent - Anthropic runs those.
_DISPATCH: dict[str, Callable[[dict[str, Any]], str]] = {
    "get_time": _tool_get_time,
    "control_device": _tool_control_device,
    "list_devices": _tool_list_devices,
    "home_status": _tool_home_status,
    "test_home_assistant": _tool_test_home_assistant,
    "activate_home_action": _tool_activate_home_action,
    "list_home_services": _tool_list_home_services,
    "call_home_service": _tool_call_home_service,
    "list_automations": _tool_list_automations,
    "inspect_automation": _tool_inspect_automation,
    "draft_automation": _tool_draft_automation,
    "apply_automation_draft": _tool_apply_automation_draft,
    "rollback_automation_change": _tool_rollback_automation_change,
    "remember_fact": _tool_remember_fact,
    "remember_profile_record": _tool_remember_profile_record,
    "forget_fact": _tool_forget_fact,
    "update_fact": _tool_update_fact,
    "adjust_fact_confidence": _tool_adjust_fact_confidence,
    "list_memory_facts": _tool_list_memory_facts,
    "find_memory_contradictions": _tool_find_memory_contradictions,
    "propose_memory_fact": _tool_propose_memory_fact,
    "list_memory_proposals": _tool_list_memory_proposals,
    "apply_memory_proposal": _tool_apply_memory_proposal,
    "reject_memory_proposal": _tool_reject_memory_proposal,
    "search_memory": _tool_search_memory,
    "search_session_summaries": _tool_search_session_summaries,
    "list_debug_reports": _tool_list_debug_reports,
    "inspect_debug_report": _tool_inspect_debug_report,
    "clear_conversation": _tool_clear_conversation,
    "conversation_history_stats": _tool_conversation_stats,
    "get_weather": _tool_get_weather,
    "set_timer": _tool_set_timer,
    "cancel_timer": _tool_cancel_timer,
    "pause_timer": _tool_pause_timer,
    "resume_timer": _tool_resume_timer,
    "dismiss_timer": _tool_dismiss_timer,
    "list_timers": _tool_list_timers,
    "play_music": _tool_play_music,
    "pause_music": _tool_pause_music,
    "resume_music": _tool_resume_music,
    "skip_track": _tool_skip_track,
    "previous_track": _tool_previous_track,
    "current_track": _tool_current_track,
    "set_volume": _tool_set_volume,
    "list_speakers": _tool_list_speakers,
    "transfer_playback": _tool_transfer_playback,
    "open_app": _tool_open_app,
    "close_app": _tool_close_app,
    "focus_app": _tool_focus_app,
    "list_open_apps": _tool_list_open_apps,
    "read_clipboard": _tool_read_clipboard,
    "write_clipboard": _tool_write_clipboard,
    "look_at_screen": _tool_look_at_screen,
    "list_settings": _tool_list_settings,
    "get_setting": _tool_get_setting,
    "update_setting": _tool_update_setting,
    "morning_briefing": _tool_morning_briefing,
    "add_reminder": _tool_add_reminder,
    "list_reminders": _tool_list_reminders,
    "complete_reminder": _tool_complete_reminder,
    "delete_reminder": _tool_delete_reminder,
    "todays_agenda": _tool_todays_agenda,
    "next_event": _tool_next_event,
    "find_movie": _tool_find_movie,
    "add_movie": _tool_add_movie,
    "find_series": _tool_find_series,
    "add_series": _tool_add_series,
    "list_quality_profiles": _tool_list_quality_profiles,
    "download_status": _tool_download_status,
    "torrent_inventory": _tool_torrent_inventory,
    "manage_torrent": _tool_manage_torrent,
    "test_qbittorrent": _tool_test_qbittorrent,
    "media_queue": _tool_media_queue,
    "media_health": _tool_media_health,
    "media_history": _tool_media_history,
    "missing_media": _tool_missing_media,
    "upcoming_media": _tool_upcoming_media,
}


def _web_search_server_tool() -> dict[str, Any]:
    """Anthropic-hosted web search server tool spec."""
    max_uses = int(os.getenv("WEB_SEARCH_MAX_USES", "5") or 5)
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}


def get_tool_specs() -> list[dict[str, Any]]:
    """Return the full tool list sent to the Anthropic API each turn.

    Includes every custom tool plus, when enabled, Anthropic's hosted
    web_search server tool.
    """
    specs: list[dict[str, Any]] = list(_CUSTOM_TOOLS)
    if _web_search_enabled():
        specs.append(_web_search_server_tool())
    return specs


# Backwards-compatible alias; some call sites import TOOL_SPECS directly.
TOOL_SPECS = _CUSTOM_TOOLS


def execute_tool(name: str, args: dict[str, Any], session_id: str | None = None) -> str:
    """Dispatch a tool_use block to its handler and return spoken-friendly text."""
    handler = _DISPATCH.get(name)
    if handler is None:
        log.warning("Unknown tool requested: %s", name)
        return f"Tool error: no such tool '{name}'."
    try:
        safe_args = dict(args or {})
        if session_id:
            safe_args["_jarvis_session_id"] = session_id
        return handler(safe_args)
    except Exception as exc:  # noqa: BLE001
        log.exception("Tool %s raised", name)
        return f"Tool error while running {name}: {exc}"
