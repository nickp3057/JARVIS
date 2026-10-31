"""Central runtime spine for the JARVIS assistant.

The runtime is the single coordination layer shared by text mode, GUI mode,
voice mode, and future proactive monitors. It owns the conversation agent and
provides small, stable methods for common app operations instead of letting UI
widgets reach directly into lower-level modules.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import os
from threading import RLock
import threading
from typing import Any, Iterator

from brain import event_bus, memory_extractor, tool_health
from brain.agent import JarvisAgent
from brain.history import load_session_messages, session_exists, request_context, begin_companion_request, delete_session, start_new_session
from brain.memory import list_facts
from brain.timers import Timer, active_timers, pop_expired
from brain.reminders import active_reminders, pop_due_reminders as _pop_due_reminders
from brain.weather import WeatherSnapshot, get_weather_snapshot
from integrations.home_assistant import get_states, listen_state_changes
from jarvis_logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RuntimeStatus:
    """Small status snapshot suitable for UI panels and logs."""

    brain_online: bool
    brain_label: str
    memory_count: int
    active_timer_count: int
    alarming_timer_count: int
    last_used_tools: bool
    event_count: int
    active_task_count: int


@dataclass(frozen=True)
class RuntimeEvent:
    """A proactive observation or notable runtime action."""

    id: int
    kind: str
    source: str
    title: str
    detail: str
    created_at: float
    speakable: bool = False


@dataclass(frozen=True)
class SpokenAnnouncement:
    """A free-form line to be spoken proactively (e.g. a calendar alert).

    Exposes ``id`` and ``message`` so it rides the same announcement path as
    timers and reminders in the voice runner and GUI.
    """

    id: str
    message: str


@dataclass(frozen=True)
class RuntimeTask:
    """A short-lived unit of runtime work shown in the cockpit."""

    id: int
    title: str
    state: str
    detail: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class HomeDomainState:
    """Summary for one Home Assistant domain."""

    domain: str
    total: int = 0
    on_count: int = 0
    off_count: int = 0
    active_count: int = 0
    unavailable_count: int = 0


@dataclass(frozen=True)
class HomeSnapshot:
    """Latest Home Assistant state summary for cockpit display."""

    online: bool
    updated_at: float | None
    entity_count: int
    domains: dict[str, HomeDomainState]
    error: str = ""



def _invalidate_app_home_cache(entity_id: str) -> None:
    try:
        from app import status_hub
        if entity_id.startswith("scene."):  # a scene ran: its state is when
            status_hub.home_changed(scenes=True)
        elif entity_id in status_hub._dashboard_allowlist():  # noqa: SLF001 - only what the apps show
            status_hub.home_changed()
    except Exception:  # noqa: BLE001 - the apps' cache is a nicety, never break the monitor
        pass


def _monitor_on(name: str) -> bool:
    """A monitor's on/off switch in .env; monitors are on unless switched off."""
    return os.getenv(name, "true").strip().lower() in ("1", "true", "yes", "on")

class JarvisRuntime:
    """Coordinates JARVIS subsystems behind one stable interface."""

    def __init__(self, agent: JarvisAgent | None = None) -> None:
        self._lock = RLock()
        # Serializes conversation turns and session switching only. Never hold
        # _lock across a model call: it guards tasks, events, and snapshots that
        # monitors and UI panels read while a turn is in flight.
        self._turn_lock = RLock()
        self._events: deque[RuntimeEvent] = deque(maxlen=100)
        self._tasks: dict[int, RuntimeTask] = {}
        self._reminder_event_ids: set[str] = set()
        self._pending_announcements: deque[str] = deque(maxlen=50)
        self._next_announce_id = 1
        self._calendar_monitor_started = False
        self._calendar_monitor_failed = False
        self._calendar_alert_keys: set[str] = set()
        self._next_event_id = 1
        self._next_task_id = 1
        self._launch_briefing_claimed = False
        self._stop_monitors = threading.Event()
        self._started_at = time.monotonic()
        self._home_monitor_started = False
        self._home_monitor_failed = False
        self._weather_monitor_started = False
        self._weather_monitor_failed = False
        self._download_monitor_started = False
        self._download_monitor_failed = False
        self._device_watch_started = False
        self._nas_host_started = False
        # The phone is one subscriber of the alert bus (companion push, HA
        # fallback); client event streams subscribe in app/server.py.
        event_bus.subscribe("phone", self._deliver_alert_to_phone, background=True)
        self._arr_monitor_thread = None
        self._arr_last_cycle = 0.0
        self._deadman_started = False
        self._price_monitor_started = False
        self._wake_monitor_started = False
        self._home_flaps: dict[str, list[float]] = {}
        self._home_flap_muted: dict[str, float] = {}
        self._schwab_monitor_started = False
        self._price_monitor_failed = False
        self._home_snapshot = HomeSnapshot(
            online=False,
            updated_at=None,
            entity_count=0,
            domains={},
            error="Waiting for Home Assistant",
        )
        self._home_event_last_seen: dict[str, float] = {}
        self._proactive_notice_keys: set[str] = set()
        self._weather_snapshot = WeatherSnapshot(
            online=False,
            error="Waiting for weather monitor",
        )
        self.agent = agent or JarvisAgent()
        self.room_session_id = self.agent.session_id
        memory_extractor.add_listener(self._on_memory_change)
        from brain import arr_stack
        arr_stack.set_recovery_notifier(self._report_arr_recovery)
        from brain import self_repair
        self_repair.set_notifier(lambda title, detail, severity: self.raise_alert(
            "repair", title, detail, source="self-repair", severity=severity))
        log.info("Runtime online. brain=%s", self.brain_label)
        self.record_event(
            kind="system",
            source="runtime",
            title="Runtime online",
            detail=f"Brain: {self.brain_label}",
        )
        self.start_monitors()

    @property
    def brain_online(self) -> bool:
        return self.agent.is_available()

    @property
    def brain_label(self) -> str:
        return self.agent.brain_label

    @property
    def last_used_tools(self) -> bool:
        return self.agent.last_used_tools

    @property
    def last_turn_tools(self) -> list[tuple[str, dict]]:
        """(tool, input) for each tool the last finished turn ran."""
        return list(getattr(self.agent, "turn_tools", ()) or ())

    def mark_no_tools_used(self) -> None:
        """Record that the latest runtime action was conversational only."""
        self.agent.last_used_tools = False

    def start_monitors(self) -> None:  # noqa: C901 (one block per monitor, flat on purpose)
        """Start background monitors that feed observations into the inbox."""
        if _monitor_on("HOME_MONITOR_ENABLED") and not self._home_monitor_started:
            self._home_monitor_started = True
            threading.Thread(target=self._home_monitor_entry, daemon=True).start()

        if _monitor_on("WEATHER_MONITOR_ENABLED") and not self._weather_monitor_started:
            self._weather_monitor_started = True
            threading.Thread(target=self._weather_monitor_loop, daemon=True).start()

        if _monitor_on("CALENDAR_MONITOR_ENABLED") and not self._calendar_monitor_started:
            try:
                from integrations.google_calendar import is_configured
                configured = is_configured()
            except Exception:  # noqa: BLE001
                configured = False
            if configured:
                self._calendar_monitor_started = True
                threading.Thread(target=self._calendar_monitor_loop, daemon=True).start()

        if _monitor_on("QBITTORRENT_MONITOR_ENABLED") and not self._download_monitor_started:
            try:
                from integrations.qbittorrent import is_configured
                configured = is_configured()
            except Exception:  # noqa: BLE001
                configured = False
            if configured:
                self._download_monitor_started = True
                threading.Thread(target=self._download_monitor_loop, daemon=True).start()

        if _watched_devices() and not self._device_watch_started:
            self._device_watch_started = True
            threading.Thread(target=self._device_watch_loop, daemon=True).start()

        if not self._wake_monitor_started:
            self._wake_monitor_started = True
            threading.Thread(target=self._wake_monitor_loop, daemon=True).start()

        from integrations import schwab
        if schwab.configured() and not self._schwab_monitor_started:
            self._schwab_monitor_started = True
            threading.Thread(target=self._schwab_monitor_loop, daemon=True).start()
            threading.Thread(target=schwab.stream_forever, args=(self._stop_monitors,), daemon=True).start()

        if _monitor_on("PRICE_WATCH_MONITOR_ENABLED") and not self._price_monitor_started:
            self._price_monitor_started = True
            threading.Thread(target=self._price_monitor_loop, daemon=True).start()

        from integrations import nas_stack
        if nas_stack.enabled("ARR_STACK_MONITOR_ENABLED") and self._arr_monitor_thread is None:
            self._arr_monitor_thread = threading.Thread(target=self._arr_stack_monitor_loop, daemon=True)
            self._arr_monitor_thread.start()
        from integrations import nas_host
        if nas_host.monitor_enabled() and not self._nas_host_started:
            self._nas_host_started = True
            threading.Thread(target=self._nas_host_monitor_loop, daemon=True).start()
        if os.getenv("JARVIS_HEALTHCHECKS_URL", "").strip() and not self._deadman_started:
            self._deadman_started = True
            threading.Thread(target=self._deadman_loop, daemon=True).start()

    def _in_startup_grace(self) -> bool:
        """True during the first ``STARTUP_NETWORK_GRACE_S`` (120) seconds.

        After a power cut the Optiplex's Wi-Fi can still be associating when
        the process starts, so every integration fails its first attempt with
        "Network is unreachable" or a DNS error. Monitors keep retrying during
        this window but don't record those failures as outages."""
        try:
            grace = float(os.getenv("STARTUP_NETWORK_GRACE_S", "120"))
        except ValueError:
            grace = 120.0
        started = getattr(self, "_started_at", None)
        return started is not None and time.monotonic() - started < grace

    def _arr_stack_monitor_loop(self) -> None:
        from brain.arr_incidents import IncidentPolicy, run_cycle
        from brain.arr_stack import AlertTracker, recovery_in_progress
        from integrations import nas_stack
        tracker = AlertTracker()
        policy = IncidentPolicy()
        while not self._stop_monitors.is_set():
            try:
                snapshot = nas_stack.poll_health()
                # Only spend the SSH round trip when something looks wrong and
                # the read-only key exists; the result feeds the maintenance
                # hold, the watchdog-recovery hold and the watchdog note.
                status = None
                if (snapshot["issues"] or tracker.has_unconfirmed() or snapshot.get("dht_nodes") == 0) and nas_stack.status_configured():
                    status = nas_stack.watchdog_status()
                snapshot["maintenance"] = recovery_in_progress() or nas_stack.recovery_active(status)
                # brain/arr_incidents.py decides what reaches the phone: holds
                # during watchdog recoveries, minimum downtime, one grouped
                # notification per incident, daily digest for long backoff.
                pushes, advisory = run_cycle(tracker, policy, snapshot, status)
                for push in pushes:
                    log.info("Arr stack push (%s, %s): %s", push.kind, push.thread_id, push.message)
                    message = push.message
                    if push.kind in ("alert", "urgent") and status is not None:
                        message += f" {nas_stack.watchdog_note(status)}"
                    if push.passive:
                        self.record_event(kind="notice", source="arr-stack", title="Arr stack update", detail=message[:500])
                    else:
                        self.announce(message, source="arr-stack", title="Arr stack health alert")
                    self.push_to_phone(push.title, message, source="arr-stack", thread_id=push.thread_id,
                                       collapse_id=push.collapse_id, passive=push.passive)
                if advisory:
                    # Inbox/event feed only -- no announce, no phone push.
                    self.record_event(kind="notice", source="arr-stack", title="Arr stack advisory", detail="; ".join(advisory))
                self._arr_last_cycle = time.monotonic()
            except Exception:
                # Leave the heartbeat stale if the worker cannot complete a
                # polling cycle; do not keep reporting the monitor as healthy.
                log.exception("Arr stack monitor cycle failed")
            if self._stop_monitors.wait(nas_stack.seconds("ARR_STACK_MONITOR_INTERVAL_S", 60)):
                break

    def _report_arr_recovery(self, title: str, message: str) -> None:
        """Deliver the asynchronous outcome of a confirmed arr restart."""
        self.announce(message, source="arr-stack", title="Arr recovery result")
        self.push_to_phone(title, message, source="arr-stack")

    def push_to_phone(self, title: str, message: str, *, source: str, **grouping) -> bool:
        """Publish an arr-stack alert on the bus (phone + client streams).

        ``grouping`` (thread_id / collapse_id / passive) keeps one incident in
        one phone notification."""
        severity = "info" if grouping.get("passive") else "critical"
        return event_bus.publish("arr_stack", title, message, source=source, severity=severity, **grouping) is not None

    def raise_alert(self, kind: str, title: str, detail: str, *, source: str, severity: str = "warning",
                    push: bool = True, dedupe_key: str | None = None, cooldown_s: float = 0.0,
                    context: dict | None = None, **grouping) -> None:
        """Inbox event plus bus alert: the one call for a new proactive alert.
        ``grouping`` (thread_id, collapse_id, passive, category) goes to the phone push."""
        self.record_event(kind="alert", source=source, title=title, detail=detail)
        event_bus.publish(kind, title, detail, source=source, severity=severity, push=push,
                          dedupe_key=dedupe_key, cooldown_s=cooldown_s, context=context, **grouping)

    def _deliver_alert_to_phone(self, alert: "event_bus.Alert") -> None:
        """Bus subscriber: companion push first, Home Assistant as fallback.

        A failed delivery is recorded in the inbox (not re-published, which
        would loop) so the alert is never silently lost."""
        if not alert.push:
            return
        from brain import notifications
        if not notifications.configured():
            return
        delivery = notifications.send(alert.title, alert.message, **alert.grouping)
        if not delivery.delivered:
            self.record_event(kind="alert", source=alert.source, title="Phone alert delivery failed",
                              detail=f"Alert remains in the runtime inbox. {delivery.detail}"[:500])

    def _device_watch_loop(self) -> None:
        """Alert when a watched device stays unavailable in Home Assistant.

        A device must be unavailable/unknown for DEVICE_OFFLINE_ALERT_MIN
        (default 10) before it alerts, once, then a single info alert when it
        returns. If Home Assistant itself is down nothing alerts here; the HA
        monitor reports that.
        """
        down_since: dict[str, float] = {}
        alerted: set[str] = set()
        while not self._stop_monitors.is_set():
            watched = _watched_devices()
            grace = float(os.getenv("DEVICE_OFFLINE_ALERT_MIN", "10")) * 60
            success, payload = get_states()
            if success and isinstance(payload, list):
                states = {str(e.get("entity_id")): e for e in payload if isinstance(e, dict)}
                now = time.time()
                for entity_id in watched:
                    entity = states.get(entity_id)
                    name = str(((entity or {}).get("attributes") or {}).get("friendly_name") or _friendly_entity_name(entity_id))
                    offline = entity is None or str(entity.get("state", "")).lower() in {"unavailable", "unknown"}
                    if offline:
                        down_since.setdefault(entity_id, now)
                        if entity_id not in alerted and now - down_since[entity_id] >= grace:
                            alerted.add(entity_id)
                            minutes = int((now - down_since[entity_id]) // 60)
                            self.raise_alert("device_offline", f"{name} offline",
                                             f"{name} has been unavailable in Home Assistant for {minutes} minutes.",
                                             source="home")
                    else:
                        down_since.pop(entity_id, None)
                        if entity_id in alerted:
                            alerted.discard(entity_id)
                            self.raise_alert("device_online", f"{name} back online", f"{name} is reachable again.",
                                             source="home", severity="info", push=False)
            if self._stop_monitors.wait(float(os.getenv("DEVICE_WATCH_INTERVAL_S", "60"))):
                break

    def _nas_host_monitor_loop(self) -> None:
        """Containers, RAID, SMART, volumes and temperatures from the NAS's
        host-health snapshot, read through the read-only status key.

        Debounce and recovery live in ``integrations.nas_host.HostWatch``. An
        unreachable NAS is not judged here; the arr monitor reports that.
        """
        from integrations import nas_host, nas_stack
        watch = nas_host.HostWatch()
        while not self._stop_monitors.is_set():
            # step() persists delivery state. Do not consume transitions until
            # startup grace ends and we can actually publish their alerts.
            if self._in_startup_grace():
                if self._stop_monitors.wait(5):
                    break
                continue
            try:
                alerts = watch.step(nas_stack.watchdog_status())
            except Exception:  # noqa: BLE001 - a bad snapshot must not kill the monitor
                log.exception("NAS host monitor poll failed")
                alerts = []
            for alert in alerts:
                self.raise_alert("nas_host", alert.title, alert.message, source="nas-host",
                                 severity=alert.severity, push=alert.push)
            if self._stop_monitors.wait(nas_stack.seconds("NAS_HOST_MONITOR_INTERVAL_S", 300, 60)):
                break

    def _deadman_loop(self) -> None:
        """Ping Healthchecks while the arr monitor is alive.

        Healthchecks.io is what pages Nick when pings stop, so a missed ping is
        only an inbox line here, never a phone/app alert (it used to raise
        one on every jarvis-api start: this loop checked before the arr
        monitor's first poll had finished). Until that first poll lands,
        nothing is judged and no ping is sent."""
        from integrations import nas_stack
        failed = False
        started = time.monotonic()
        while not self._stop_monitors.is_set():
            stale_after = max(180, nas_stack.seconds("ARR_STACK_MONITOR_INTERVAL_S", 60) * 3)
            arr_enabled = nas_stack.enabled("ARR_STACK_MONITOR_ENABLED")
            alive = self._arr_monitor_thread is not None and self._arr_monitor_thread.is_alive()
            if arr_enabled and alive and self._arr_last_cycle <= 0 and time.monotonic() - started < stale_after:
                if self._stop_monitors.wait(15):
                    break
                continue
            monitor_ok = not arr_enabled or (
                alive and self._arr_last_cycle > 0 and time.monotonic() - self._arr_last_cycle < stale_after
            )
            ok = monitor_ok and nas_stack.ping_deadman()
            if not ok and not failed:
                why = "arr monitor stalled" if not monitor_ok else "Healthchecks ping failed"
                log.warning("Dead-man heartbeat not delivered: %s", why)
                self.record_event(kind="system", source="deadman", title="Dead-man heartbeat not delivered",
                                  detail=f"{why}; Healthchecks.io will page if it persists.")
            failed = not ok
            if self._stop_monitors.wait(nas_stack.seconds("JARVIS_HEALTHCHECKS_INTERVAL_S", 300, 60)):
                break

    def stop_monitors(self) -> None:
        """Ask background monitors to stop."""
        self._stop_monitors.set()
        from integrations import schwab
        schwab.linked_event.set()  # its monitor waits on this, not on _stop_monitors
        memory_extractor.remove_listener(self._on_memory_change)
        memory_extractor.stop()

    def _on_memory_change(self, title: str, detail: str) -> None:
        """Surface background memory writes in the event inbox."""
        self.record_event(kind="memory", source="memory", title=title, detail=detail)

    def respond(self, user_input: str, *, spoken: bool = False) -> str:
        """Run one conversational turn through the best available brain.
        ``spoken``: the reply goes to a speaker (the room voice), so short."""
        task_id = self.start_task("Reasoning", user_input.strip())
        with self._turn_lock:
            if spoken:
                if not session_exists(self.room_session_id):
                    self.room_session_id = start_new_session()
                self.agent.switch_session(self.room_session_id)
            self.agent.spoken_turn = spoken
            try:
                reply = self.agent.respond(user_input)
            except Exception:
                self.finish_task(task_id, state="failed", detail="Conversation failed")
                raise
            finally:
                self.agent.spoken_turn = False
        task_state = "action" if self.last_used_tools else "complete"
        self.finish_task(task_id, state=task_state, detail="Reply ready")
        if self.last_used_tools:
            self.record_event(
                kind="action",
                source="agent",
                title="Tool action completed",
                detail=user_input.strip(),
            )
        return reply

    def respond_streaming(self, user_input: str) -> Iterator[str]:
        """Streaming twin of respond() that yields reply fragments for TTS."""
        task_id = self.start_task("Reasoning", user_input.strip())
        try:
            with self._turn_lock:
                for chunk in self.agent.respond_streaming(user_input):
                    yield chunk
        except Exception:
            self.finish_task(task_id, state="failed", detail="Conversation failed")
            raise
        task_state = "action" if self.last_used_tools else "complete"
        self.finish_task(task_id, state=task_state, detail="Reply ready")
        if self.last_used_tools:
            self.record_event(
                kind="action",
                source="agent",
                title="Tool action completed",
                detail=user_input.strip(),
            )

    def respond_in_session(self, session_id: str, user_input: str, request_id: str | None = None) -> str:
        """Atomically select and answer a remote turn; retries reuse its receipt."""
        with self._turn_lock:
            if not session_exists(session_id):
                raise KeyError(session_id)
            if request_id:
                previous = begin_companion_request(request_id, session_id, user_input)
                if previous is not None:
                    return previous
            token = request_context.set(request_id)
            try:
                self.agent.switch_session(session_id)
                return self.respond(user_input)
            finally:
                request_context.reset(token)

    def respond_streaming_in_session(self, session_id: str, user_input: str, request_id: str | None = None) -> Iterator[str]:
        """Consume on one worker thread: the lock and request context are thread-bound."""
        with self._turn_lock:
            if not session_exists(session_id):
                raise KeyError(session_id)
            if request_id:
                previous = begin_companion_request(request_id, session_id, user_input)
                if previous is not None:
                    yield previous
                    return
            token = request_context.set(request_id)
            try:
                self.agent.switch_session(session_id)
                yield from self.respond_streaming(user_input)
            finally:
                request_context.reset(token)

    def delete_companion_session(self, session_id: str) -> bool:
        """Serialize deletion with turns that may still be saving history."""
        with self._turn_lock:
            return delete_session(session_id)

    def compose_briefing(self) -> str:
        """Build a short situational briefing and log it as a runtime event."""
        from brain.briefing import compose_briefing as _compose
        text = _compose()
        self.record_event(
            kind="briefing",
            source="runtime",
            title="Briefing",
            detail=text,
        )
        return text

    def launch_briefing(self) -> str:
        """Claim and compose the optional once-per-runtime launch briefing."""
        enabled = os.getenv("BRIEFING_ON_LAUNCH", "false").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if not enabled:
            return ""
        with self._lock:
            if self._launch_briefing_claimed:
                return ""
            self._launch_briefing_claimed = True
        try:
            return self.compose_briefing()
        except Exception:  # noqa: BLE001
            # A transient weather/calendar failure should not permanently
            # consume the launch briefing if another front end can retry.
            with self._lock:
                self._launch_briefing_claimed = False
            log.exception("Launch briefing failed")
            return ""

    def start_task(self, title: str, detail: str = "") -> int:
        """Create an active runtime task and return its id."""
        with self._lock:
            task_id = self._next_task_id
            self._next_task_id += 1
            now = time.time()
            self._tasks[task_id] = RuntimeTask(
                id=task_id,
                title=title,
                state="active",
                detail=detail,
                created_at=now,
                updated_at=now,
            )
        self.record_event(kind="task", source="runtime", title=f"Started: {title}", detail=detail)
        return task_id

    def finish_task(self, task_id: int, *, state: str = "complete", detail: str = "") -> None:
        """Mark a task complete and keep it briefly visible in the task lane."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            self._tasks[task_id] = RuntimeTask(
                id=task.id,
                title=task.title,
                state=state,
                detail=detail or task.detail,
                created_at=task.created_at,
                updated_at=time.time(),
            )
        self.record_event(kind="task", source="runtime", title=f"Finished: {task.title}", detail=detail)

    def recent_tasks(self, limit: int = 10) -> list[RuntimeTask]:
        """Return active and recently finished tasks, newest first."""
        with self._lock:
            now = time.time()
            stale_ids = [
                task_id
                for task_id, task in self._tasks.items()
                if task.state != "active" and now - task.updated_at > 90
            ]
            for task_id in stale_ids:
                self._tasks.pop(task_id, None)
            tasks = sorted(self._tasks.values(), key=lambda task: task.updated_at, reverse=True)
        return tasks[:limit]

    def start_session(self) -> str:
        """Start a fresh conversation session."""
        with self._turn_lock:
            session_id = self.agent.start_session()
        self.record_event(
            kind="session",
            source="history",
            title="New session",
            detail=session_id,
        )
        return session_id

    def switch_session(self, session_id: str) -> list[dict[str, Any]]:
        """Switch active context and return messages for transcript loading."""
        with self._turn_lock:
            self.agent.switch_session(session_id)
            messages = load_session_messages(session_id)
        self.record_event(
            kind="session",
            source="history",
            title="Session resumed",
            detail=session_id,
        )
        return messages

    def active_timers(self) -> list[Timer]:
        return active_timers()

    def pop_due_reminders(self) -> list:
        """Mark and return newly due timers and reminders for announcement.

        Both Timer and Reminder expose ``.id`` and ``.message`` so they share
        the same announcement path in the voice runner.
        """
        due: list = list(pop_expired())
        due.extend(_pop_due_reminders())
        with self._lock:
            while self._pending_announcements:
                text = self._pending_announcements.popleft()
                ann_id = f"ann{self._next_announce_id}"
                self._next_announce_id += 1
                due.append(SpokenAnnouncement(id=ann_id, message=text))
        for item in due:
            self._record_reminder_event(item)
        return due

    def announce(self, text: str, *, source: str = "runtime", title: str = "Announcement") -> None:
        """Queue a free-form line to be spoken proactively and log it."""
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._pending_announcements.append(text)
        self.record_event(kind="notice", source=source, title=title, detail=text, speakable=True)

    def _record_reminder_event(self, timer: Timer) -> None:
        with self._lock:
            if timer.id in self._reminder_event_ids:
                return
            self._reminder_event_ids.add(timer.id)
        self.record_event(
            kind="reminder",
            source="timers",
            title="Reminder due",
            detail=timer.message,
            speakable=True,
        )

    def record_event(
        self,
        *,
        kind: str,
        source: str,
        title: str,
        detail: str = "",
        speakable: bool = False,
    ) -> RuntimeEvent:
        """Add a runtime event to the shared inbox."""
        with self._lock:
            event = RuntimeEvent(
                id=self._next_event_id,
                kind=kind,
                source=source,
                title=title,
                detail=detail,
                created_at=time.time(),
                speakable=speakable,
            )
            self._next_event_id += 1
            self._events.appendleft(event)
        log.info("Runtime event: %s/%s %s", source, kind, title)
        return event

    def recent_events(self, limit: int = 20) -> list[RuntimeEvent]:
        with self._lock:
            return list(self._events)[:limit]

    def home_snapshot(self) -> HomeSnapshot:
        with self._lock:
            return self._home_snapshot

    def weather_snapshot(self) -> WeatherSnapshot:
        with self._lock:
            return self._weather_snapshot

    def observe_timers(self) -> None:
        """Update timer state and record alarm events without consuming speech."""
        for timer in self.active_timers():
            if timer.alarming:
                self._record_reminder_event(timer)

    def status_snapshot(self) -> RuntimeStatus:
        timers = self.active_timers()
        tasks = self.recent_tasks(limit=20)
        return RuntimeStatus(
            brain_online=self.brain_online,
            brain_label=self.brain_label,
            memory_count=len(list_facts()),
            active_timer_count=len(timers),
            alarming_timer_count=sum(1 for timer in timers if timer.alarming),
            last_used_tools=self.last_used_tools,
            event_count=len(self.recent_events(limit=100)),
            active_task_count=sum(1 for task in tasks if task.state == "active"),
        )

    def _home_monitor_loop(self) -> None:
        interval = float(os.getenv("HOME_MONITOR_INTERVAL_S", "20.0"))
        watched_domains = {
            domain.strip()
            for domain in os.getenv(
                "HOME_MONITOR_DOMAINS",
                "light,switch,fan,media_player,binary_sensor,person,device_tracker,lock,cover,climate",
            ).split(",")
            if domain.strip()
        }
        previous: dict[str, str] | None = None

        while not self._stop_monitors.is_set():
            success, payload = get_states()
            if not success:
                with self._lock:
                    self._home_snapshot = HomeSnapshot(
                        online=False,
                        updated_at=time.time(),
                        entity_count=0,
                        domains={},
                        error=str(payload),
                    )
                if not self._home_monitor_failed:
                    self._home_monitor_failed = True
                    self.record_event(
                        kind="monitor",
                        source="home",
                        title="Home monitor offline",
                        detail=str(payload),
                    )
                if self._stop_monitors.wait(interval):
                    break
                continue

            self._home_monitor_failed = False
            self._mark_home_monitor_healthy()
            current: dict[str, str] = {}
            entities_by_domain: dict[str, list[dict[str, Any]]] = {}
            for entity in payload:
                entity_id = entity.get("entity_id", "")
                domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
                if domain not in watched_domains:
                    continue
                state = str(entity.get("state", "unknown"))
                current[entity_id] = state
                entities_by_domain.setdefault(domain, []).append(entity)

            snapshot = _build_home_snapshot(entities_by_domain)
            with self._lock:
                self._home_snapshot = snapshot

            if previous is None:
                previous = current
                self.record_event(
                    kind="monitor",
                    source="home",
                    title="Home monitor online",
                    detail=f"Watching {len(current)} entities",
                )
                self._evaluate_home_proactive_rules(snapshot)
                if self._stop_monitors.wait(interval):
                    break
                continue

            for entity_id, state in current.items():
                old_state = previous.get(entity_id)
                if old_state is None or old_state == state:
                    continue
                if not self._should_record_home_change(entity_id, old_state, state):
                    continue
                self.record_event(
                    kind="home",
                    source="home",
                    title=_friendly_entity_name(entity_id),
                    detail=f"{old_state} -> {state}",
                )
            self._evaluate_home_proactive_rules(snapshot)
            previous = current
            if self._stop_monitors.wait(interval):
                break

    def _home_monitor_entry(self) -> None:
        transport = os.getenv("HOME_MONITOR_TRANSPORT", "websocket").strip().lower()
        if transport in ("poll", "polling", "rest"):
            self._home_monitor_loop()
            return
        self._home_websocket_monitor_loop()

    def _home_websocket_monitor_loop(self) -> None:
        """Maintain a live HA state subscription, reconnecting after outages."""
        retry_s = float(os.getenv("HOME_MONITOR_RECONNECT_S", "5.0"))
        watched_domains = {
            domain.strip()
            for domain in os.getenv(
                "HOME_MONITOR_DOMAINS",
                "light,switch,fan,media_player,binary_sensor,person,device_tracker,lock,cover,climate",
            ).split(",")
            if domain.strip()
        }
        entities: dict[str, dict[str, Any]] = {}
        ever_connected = False

        while not self._stop_monitors.is_set():
            success, payload = get_states()
            if not success:
                self._set_home_monitor_offline(str(payload))
                if self._stop_monitors.wait(retry_s):
                    break
                continue

            entities = {
                str(entity.get("entity_id")): entity
                for entity in payload
                if str(entity.get("entity_id", "")).split(".", 1)[0] in watched_domains
            }
            self._update_home_snapshot_from_entities(entities)

            def _connected() -> None:
                nonlocal ever_connected
                was_offline = self._home_monitor_failed
                self._home_monitor_failed = False
                self._mark_home_monitor_healthy()
                if not ever_connected or was_offline:
                    self.record_event(
                        kind="monitor",
                        source="home",
                        title="Home live monitor online",
                        detail=f"Watching {len(entities)} entities via WebSocket",
                    )
                ever_connected = True

            def _changed(data: dict[str, Any]) -> None:
                entity_id = str(data.get("entity_id", ""))
                if entity_id.startswith("scene."):
                    _invalidate_app_home_cache(entity_id)  # the apps light the scene that ran
                if not entity_id or entity_id.split(".", 1)[0] not in watched_domains:
                    return
                old_entity = data.get("old_state") or entities.get(entity_id) or {}
                new_entity = data.get("new_state")
                old_state = str(old_entity.get("state", "unknown"))
                if new_entity is None:
                    entities.pop(entity_id, None)
                    self._update_home_snapshot_from_entities(entities)
                    return
                known = entity_id in entities
                entities[entity_id] = new_entity
                _invalidate_app_home_cache(entity_id)  # the apps see a wall switch flip now, not in 5 s
                new_state = str(new_entity.get("state", "unknown"))
                if known and old_state == new_state:
                    # Attribute-only update (media position, brightness,
                    # sensor attributes). The snapshot only counts states,
                    # so skip the full rebuild these frequent events used
                    # to trigger.
                    return
                self._update_home_snapshot_from_entities(entities)
                if old_state == new_state or not self._should_record_home_change(
                    entity_id, old_state, new_state,
                ):
                    return
                self.record_event(
                    kind="home",
                    source="home",
                    title=_friendly_entity_name(entity_id),
                    detail=f"{old_state} -> {new_state}",
                )

            try:
                listen_state_changes(
                    self._stop_monitors,
                    _changed,
                    on_connected=_connected,
                )
            except Exception as exc:  # noqa: BLE001
                if self._stop_monitors.is_set():
                    break
                log.warning("Home Assistant live monitor disconnected: %s", exc)
                self._set_home_monitor_offline(str(exc))
                if self._stop_monitors.wait(retry_s):
                    break

    def _update_home_snapshot_from_entities(
        self,
        entities: dict[str, dict[str, Any]],
    ) -> None:
        entities_by_domain: dict[str, list[dict[str, Any]]] = {}
        for entity_id, entity in entities.items():
            domain = entity_id.split(".", 1)[0]
            entities_by_domain.setdefault(domain, []).append(entity)
        snapshot = _build_home_snapshot(entities_by_domain)
        with self._lock:
            self._home_snapshot = snapshot
        self._evaluate_home_proactive_rules(snapshot)

    def _mark_home_monitor_healthy(self) -> None:
        """Record a live-link success so the self-diagnosis flag clears."""
        try:
            tool_health.record("home_assistant_monitor", ok=True)
        except Exception:  # noqa: BLE001
            log.debug("tool_health.record failed for home monitor", exc_info=True)

    def _set_home_monitor_offline(self, error: str) -> None:
        with self._lock:
            previous = self._home_snapshot
            self._home_snapshot = HomeSnapshot(
                online=False,
                updated_at=time.time(),
                entity_count=previous.entity_count,
                domains=previous.domains,
                error=error,
            )
        if not self._home_monitor_failed and not self._in_startup_grace():
            self._home_monitor_failed = True
            self.record_event(
                kind="monitor",
                source="home",
                title="Home live monitor offline",
                detail=error,
            )
            try:
                # Two strikes so the self-diagnosis block flags the link as degraded.
                tool_health.record("home_assistant_monitor", ok=False, error=error)
                tool_health.record("home_assistant_monitor", ok=False, error=error)
            except Exception:  # noqa: BLE001
                log.debug("tool_health.record failed for home monitor", exc_info=True)

    def _should_record_home_change(self, entity_id: str, old_state: str, state: str) -> bool:
        noisy_states = {"unknown", "unavailable", "none", ""}
        if old_state.lower() in noisy_states or state.lower() in noisy_states:
            return False

        cooldown_s = float(os.getenv("HOME_EVENT_COOLDOWN_S", "5.0"))
        key = f"{entity_id}:{old_state}->{state}"
        now = time.time()
        # A flapping device (the TV dropping on and off Wi-Fi in standby,
        # 2026-10-31: 276 events in 90 min) would push every real event out
        # of the inbox: more than 4 changes in 10 minutes mutes it for 30.
        flaps = self.__dict__.setdefault("_home_flaps", {})
        muted = self.__dict__.setdefault("_home_flap_muted", {})
        if now < muted.get(entity_id, 0.0):
            return False
        recent = [t for t in flaps.get(entity_id, []) if now - t < 600] + [now]
        flaps[entity_id] = recent
        if len(recent) > 4:
            muted[entity_id] = now + 1800
            flaps[entity_id] = []
            log.info("Home events from %s muted for 30 min (flapping)", entity_id)
            return False
        last_seen = self._home_event_last_seen.get(key, 0)
        if now - last_seen < cooldown_s:
            return False
        self._home_event_last_seen[key] = now
        return True

    def _evaluate_home_proactive_rules(self, snapshot: HomeSnapshot) -> None:
        lights = snapshot.domains.get("light")
        if lights is None:
            return

        late_hour = int(os.getenv("HOME_LATE_LIGHTS_HOUR", "23"))
        threshold = int(os.getenv("HOME_LATE_LIGHTS_THRESHOLD", "4"))
        now = datetime.now()
        if now.hour < late_hour or lights.on_count < threshold:
            return

        key = f"late_lights:{now.date().isoformat()}"
        if key in self._proactive_notice_keys:
            return
        self._proactive_notice_keys.add(key)
        self.record_event(
            kind="notice",
            source="home",
            title="Late lights still on",
            detail=f"{lights.on_count} lights are on.",
        )

    def _calendar_monitor_loop(self) -> None:
        interval = float(os.getenv("CALENDAR_MONITOR_INTERVAL_S", "120.0"))
        lead = float(os.getenv("CALENDAR_ALERT_LEAD_MIN", "10.0"))
        try:
            from integrations.google_calendar import upcoming_within
        except Exception:  # noqa: BLE001
            return

        while not self._stop_monitors.is_set():
            try:
                soon = upcoming_within(lead)
            except Exception:  # noqa: BLE001
                soon = []
            for event in soon:
                key = event.get("id") or f"{event.get('summary')}:{event.get('start')}"
                if key in self._calendar_alert_keys:
                    continue
                self._calendar_alert_keys.add(key)
                mins = int(event.get("minutes_until", 0))
                if mins <= 0:
                    when = "right now"
                elif mins == 1:
                    when = "in one minute"
                else:
                    when = f"in {mins} minutes"
                self.announce(
                    f"{event.get('summary', 'a meeting')} {when}",
                    source="calendar",
                    title="Upcoming meeting",
                )
            if self._stop_monitors.wait(interval):
                break

    def _wake_monitor_loop(self) -> None:
        """At the wake-up alarm's time, tell the TV (brain/wake_alarm.py).

        A bus event, not a phone push: the TV follows /api/clients/events,
        and the packet itself wakes it from network standby."""
        from brain import wake_alarm
        while not self._stop_monitors.is_set():
            try:
                alarm = wake_alarm.due()
            except Exception:  # noqa: BLE001
                log.exception("Wake-up monitor failed")
                alarm = None
            if alarm:
                wake_alarm.alarm_fired()
                # The bus event can't reach a TV that dropped off Wi-Fi in standby:
                # power it on through HA, and ring the room speaker if it stays off.
                threading.Thread(target=wake_alarm.wake_tv_with_backup, args=(self._stop_monitors,),
                                 daemon=True, name="wake-tv").start()
                music = alarm["label"] or ("your music" if alarm["music"] else "whatever Spotify last played")
                # Info for the TV (no banner); the phone gets it with I'm up / Snooze buttons.
                self.raise_alert("wake_up", "Good morning, sir", f"{music} is on the TV.",
                                 source="wake-up", severity="info", push=True, category="WAKE_UP",
                                 collapse_id="wake-up",
                                 context={"music": alarm["music"], "volume": alarm["volume"],
                                          "label": alarm.get("label", "")})
            if self._stop_monitors.wait(20):
                break

    def _schwab_monitor_loop(self) -> None:
        """Schwab holdings and cash (integrations/schwab.py): every
        SCHWAB_POLL_SECONDS (30) while the market is open, 10 minutes when
        it isn't; prices stream separately. Asks Nick to log in again a day
        before Schwab's 7-day login runs out, and once more if it does."""
        from integrations import schwab
        warned: set[str] = set()
        while not self._stop_monitors.is_set():
            warning = schwab.login_warning()
            expires = schwab.login_expires_at()
            if warning and f"{warning}@{expires}" not in warned:
                warned.add(f"{warning}@{expires}")
                detail = ("Schwab's weekly login runs out " + schwab.expires_text(expires) + "."
                          if warning == "soon" else "Schwab's weekly login ran out; the portfolio is frozen.")
                self.raise_alert("schwab_login", "Schwab needs you",
                                 detail + " Open JARVIS > Settings > Charles Schwab > Log in.",
                                 source="schwab", severity="warning")
            if schwab.linked():
                try:
                    schwab.refresh_accounts()
                    schwab.refresh_yesterday()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Schwab refresh failed: %s", exc)
                    schwab.set_error(str(exc)[:200])
            try:
                interval = max(10.0, float(os.getenv("SCHWAB_POLL_SECONDS", "30")))
            except ValueError:
                interval = 30.0
            # Slow down only once there's something to show; a fresh login
            # (linked_event) cuts the wait short either way.
            if not schwab.market_open() and schwab.has_accounts():
                interval = 600.0
            schwab.linked_event.wait(interval)
            schwab.linked_event.clear()
            if self._stop_monitors.is_set():
                break

    def _price_monitor_loop(self) -> None:
        """Periodically refresh explicitly saved public product pages."""
        interval = max(300.0, float(os.getenv("PRICE_WATCH_INTERVAL_S", "21600") or 21600))
        try:
            from brain.price_watch import check_price_watches
        except Exception:  # noqa: BLE001
            self._price_monitor_failed = True
            return

        while not self._stop_monitors.is_set():
            try:
                notices = check_price_watches()
                self._price_monitor_failed = False
            except Exception:  # noqa: BLE001
                log.exception("Price watch monitor failed")
                self._price_monitor_failed = True
                notices = []
            for notice in notices:
                currency = f"{notice.get('currency')} " if notice.get("currency") else ""
                reason = "reached your target" if notice.get("reason") == "target" else "dropped"
                self.announce(
                    f"{notice.get('label', 'A watched item')} {reason} at "
                    f"{currency}{float(notice.get('price', 0)):,.2f}",
                    source="price-watch",
                    title="Price alert",
                )
            if self._stop_monitors.wait(interval):
                break

    def _weather_monitor_loop(self) -> None:
        interval = float(os.getenv("WEATHER_MONITOR_INTERVAL_S", "1800.0"))
        previous_temp: int | None = None
        failures = 0

        while not self._stop_monitors.is_set():
            snapshot = get_weather_snapshot()
            with self._lock:
                self._weather_snapshot = snapshot

            if not snapshot.online:
                failures += 1
                # Retry with backoff (30 s, 60 s, ... up to the interval)
                # rather than leaving weather empty for a full interval
                # after a boot-time network race.
                if not self._weather_monitor_failed and not self._in_startup_grace():
                    self._weather_monitor_failed = True
                    self.record_event(
                        kind="monitor",
                        source="weather",
                        title="Weather monitor offline",
                        detail=snapshot.error,
                    )
            else:
                failures = 0
                if self._weather_monitor_failed:
                    self.record_event(
                        kind="monitor",
                        source="weather",
                        title="Weather monitor online",
                        detail=snapshot.location,
                    )
                self._weather_monitor_failed = False
                if previous_temp is None:
                    self.record_event(
                        kind="weather",
                        source="weather",
                        title="Weather snapshot",
                        detail=_format_weather_event(snapshot),
                    )
                elif snapshot.temperature_f is not None and abs(snapshot.temperature_f - previous_temp) >= 10:
                    self.record_event(
                        kind="notice",
                        source="weather",
                        title="Weather shifted",
                        detail=f"{previous_temp}F -> {snapshot.temperature_f}F in {snapshot.location}",
                    )
                previous_temp = snapshot.temperature_f

            delay = min(interval, 30.0 * 2 ** (failures - 1)) if failures else interval
            if self._stop_monitors.wait(delay):
                break

    def _download_monitor_loop(self) -> None:
        """Watch qBittorrent for completions and download errors."""
        from integrations.qbittorrent import download_snapshot

        interval = float(os.getenv("QBITTORRENT_MONITOR_INTERVAL_S", "30.0"))
        announce_completions = os.getenv(
            "QBITTORRENT_ANNOUNCE_COMPLETIONS", "true",
        ).strip().lower() in ("1", "true", "yes", "on")
        from integrations.media_doctor import no_progress_stall

        previous: dict[str, dict[str, Any]] | None = None
        # Hashes already alerted as stalled; cleared when they move again, so
        # each stall alerts once instead of every poll.
        stalled: set[str] = set()

        while not self._stop_monitors.is_set():
            success, result = download_snapshot()
            if not success:
                if not self._download_monitor_failed and not self._in_startup_grace():
                    self._download_monitor_failed = True
                    self.record_event(
                        kind="monitor",
                        source="downloads",
                        title="Download monitor offline",
                        detail=str(result),
                    )
                if self._stop_monitors.wait(interval):
                    break
                continue

            current = {
                str(item.get("hash", "")): item
                for item in result
                if item.get("hash")
            }
            if previous is None or self._download_monitor_failed:
                self.record_event(
                    kind="monitor",
                    source="downloads",
                    title="Download monitor online",
                    detail=f"Watching {len(current)} torrents",
                )
            self._download_monitor_failed = False

            if previous is not None:
                for torrent_hash, item in current.items():
                    old = previous.get(torrent_hash)
                    if old is None:
                        continue
                    old_progress = float(old.get("progress", 0) or 0)
                    progress = float(item.get("progress", 0) or 0)
                    name = str(item.get("name", "Download"))
                    if old_progress < 1 <= progress:
                        if announce_completions:
                            self.announce(
                                f"Download complete: {name}",
                                source="downloads",
                                title="Download complete",
                            )
                        else:
                            self.record_event(
                                kind="download",
                                source="downloads",
                                title="Download complete",
                                detail=name,
                            )
                    state = str(item.get("state", "")).lower()
                    old_state = str(old.get("state", "")).lower()
                    if "error" in state and "error" not in old_state:
                        self.raise_alert("download_error", "Download error", name, source="downloads")
            now = time.time()
            for torrent_hash, item in current.items():
                if float(item.get("progress", 0) or 0) >= 1:
                    stalled.discard(torrent_hash)
                    continue
                why = no_progress_stall(item, now)
                if why is None:
                    stalled.discard(torrent_hash)
                elif torrent_hash not in stalled:
                    stalled.add(torrent_hash)
                    staged = self._stage_download_retry(torrent_hash, why)
                    self.raise_alert("download_stalled", "Download stalled",
                                     f"{item.get('name', 'Download')}: {why} "
                                     + ("A retry is ready in Repairs." if staged else "Ask me to diagnose it."),
                                     source="downloads",
                                     context={"torrent_hash": torrent_hash, "name": str(item.get("name", ""))[:200]})
            stalled &= set(current)
            previous = current
            if self._stop_monitors.wait(interval):
                break


    def _stage_download_retry(self, torrent_hash: str, why: str) -> bool:
        """Stage (never run) a one-tap retry for the arr item behind a stalled
        torrent. False when no Radarr/Sonarr queue item owns it."""
        try:
            from integrations import media_doctor
            from brain import self_repair
            found = media_doctor.find_queue_record(torrent_hash)
            if found is None:
                return False
            params = media_doctor.retry_params(*found)
            self_repair.propose_stack_fix("retry_download", f"{found[2]}: {why}", params=params,
                                          key=torrent_hash.lower()[:16])
            return True
        except Exception:  # noqa: BLE001 - a lookup failure must not stop the alert
            log.exception("Could not stage a download retry")
            return False


def _watched_devices() -> list[str]:
    """Home Assistant entity ids from JARVIS_WATCH_DEVICES (comma-separated)."""
    return [e.strip() for e in os.getenv("JARVIS_WATCH_DEVICES", "").split(",") if "." in e]


def _friendly_entity_name(entity_id: str) -> str:
    if "." not in entity_id:
        return entity_id
    _, name = entity_id.split(".", 1)
    return name.replace("_", " ").title()


def _format_weather_event(snapshot: WeatherSnapshot) -> str:
    if not snapshot.online:
        return snapshot.error
    parts = [snapshot.location]
    if snapshot.temperature_f is not None:
        parts.append(f"{snapshot.temperature_f}F")
    if snapshot.description:
        parts.append(snapshot.description)
    return " - ".join(parts)


def _build_home_snapshot(entities_by_domain: dict[str, list[dict[str, Any]]]) -> HomeSnapshot:
    domains: dict[str, HomeDomainState] = {}
    total_entities = 0
    for domain, entities in entities_by_domain.items():
        total = len(entities)
        total_entities += total
        on_count = 0
        off_count = 0
        active_count = 0
        unavailable_count = 0

        for entity in entities:
            state = str(entity.get("state", "unknown")).lower()
            if state in ("unavailable", "unknown"):
                unavailable_count += 1
            elif state == "on":
                on_count += 1
                active_count += 1
            elif state == "off":
                off_count += 1
            elif state in ("playing", "paused", "idle", "home", "open"):
                active_count += 1

        domains[domain] = HomeDomainState(
            domain=domain,
            total=total,
            on_count=on_count,
            off_count=off_count,
            active_count=active_count,
            unavailable_count=unavailable_count,
        )

    return HomeSnapshot(
        online=True,
        updated_at=time.time(),
        entity_count=total_entities,
        domains=domains,
    )
