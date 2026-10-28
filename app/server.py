"""Authenticated HTTP bridge for the JARVIS iOS companion."""

from __future__ import annotations

import asyncio
import threading
import time
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from brain.history import (
    list_sessions,
    load_session_messages,
    rename_session,
    session_exists,
)
from app import status_hub
from brain.debug_reports import create_debug_report
from brain.agent import stream_round_breaks
from brain.runtime import JarvisRuntime
from app import client_tokens
from brain import event_bus
from integrations import media_doctor, media_requests
from brain import incident_reports, maintenance, self_repair

_SSE_KEEPALIVE_S = 15.0

_runtime: JarvisRuntime | None = None
_runtime_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start scheduled monitoring even when no companion has connected yet."""
    from integrations.nas_stack import enabled
    if enabled("ARR_STACK_MONITOR_ENABLED") or os.getenv("JARVIS_HEALTHCHECKS_URL", "").strip():
        await asyncio.to_thread(runtime)
    try:
        yield
    finally:
        if _runtime is not None:
            await asyncio.to_thread(_runtime.stop_monitors)


app = FastAPI(title="JARVIS Core", version="1.0", lifespan=lifespan)

_COMPANION_API_VERSION = 1
_COMPANION_CAPABILITIES: dict[str, object] = {
    "chat": {
        "streaming": True,
        "request_receipts": True,
        "server_history": True,
        "debug_reports": True,
        "incident_reports": True,
    },
    "dashboard": {
        "now": True,
        "academic_countdowns": True,
        "study_brief": True,
        "media_storage": True,
        "service_health": True,
        "transfers": True,
        "media": True,
        "home_entities": True,
        "service_links": True,
        "maintenance": True,
        "todos": True,
        "glance": True,
        "wake_schedule": True,
        "portfolio": True,
        "tv_remote": True,
        "shared_theme": True,
    },
    "actions": {
        "home_on_off": True,
        "torrent_pause_resume": True,
        "repairs": True,
    },
}


def runtime() -> JarvisRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = JarvisRuntime()
        return _runtime


def authorize(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("JARVIS_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="JARVIS_API_TOKEN is not configured")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_scope(scope: str):
    """Admin token (JARVIS_API_TOKEN) or a client token holding ``scope``.

    Client tokens (app/client_tokens.py) let a TV or dashboard read alerts
    and status without ever holding the admin token."""
    def dependency(authorization: str | None = Header(default=None)) -> None:
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        expected = os.getenv("JARVIS_API_TOKEN", "").strip()
        if expected and hmac.compare_digest(supplied, expected):
            return
        client = client_tokens.scopes_for(supplied)
        if client is None:
            if not expected:
                raise HTTPException(status_code=503, detail="JARVIS_API_TOKEN is not configured")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        if scope not in client[1]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Token lacks scope {scope}")
    return dependency


_status_read = Depends(require_scope("status:read"))


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=100)


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class DebugMessage(BaseModel):
    id: str = Field(default="", max_length=100)
    role: str = Field(pattern="^(user|assistant|error)$")
    content: str = Field(min_length=1, max_length=20_000)
    created_at: str = Field(default="", max_length=100)


class DebugReportRequest(BaseModel):
    issue: str = Field(min_length=1, max_length=4_000)
    messages: list[DebugMessage] = Field(min_length=1, max_length=100)
    session_id: str | None = Field(default=None, max_length=100)
    conversation_title: str | None = Field(default=None, max_length=200)
    focus_message_id: str | None = Field(default=None, max_length=100)
    app_context: dict[str, str] = Field(default_factory=dict)


@app.get("/health")
def health(_: None = Depends(authorize)) -> dict[str, object]:
    core = runtime()
    return {"status": "ok", "brain_online": core.brain_online, "brain": core.brain_label}


@app.get("/api/capabilities")
def capabilities(_: None = Depends(authorize)) -> dict[str, object]:
    """Stable feature negotiation for rolling Core/companion upgrades."""
    return {
        "api_version": _COMPANION_API_VERSION,
        "capabilities": _COMPANION_CAPABILITIES,
    }


@app.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(_: None = Depends(authorize)) -> dict[str, str]:
    return {"session_id": runtime().start_session()}


@app.get("/sessions")
def sessions(_: None = Depends(authorize)) -> dict[str, object]:
    return {"sessions": list_sessions(limit=100)}


@app.get("/sessions/{session_id}")
def session(session_id: str, _: None = Depends(authorize)) -> dict[str, object]:
    if not session_exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "messages": load_session_messages(session_id, include_timestamps=True),
    }


@app.patch("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def rename(session_id: str, body: RenameRequest, _: None = Depends(authorize)) -> Response:
    if not rename_session(session_id, body.title):
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(session_id: str, _: None = Depends(authorize)) -> Response:
    if not runtime().delete_companion_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _resolve_session(request: ChatRequest) -> str:
    if request.session_id:
        if not session_exists(request.session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return request.session_id
    return runtime().start_session()


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, _: None = Depends(require_scope("chat"))) -> ChatResponse:
    session_id = _resolve_session(request)
    try:
        kwargs = {"request_id": request.request_id} if request.request_id else {}
        reply = runtime().respond_in_session(session_id, request.message.strip(), **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChatResponse(reply=reply, session_id=session_id)


@app.post("/chat/stream")
def chat_stream(
    request: ChatRequest,
    _: None = Depends(authorize),
    stream_segments: str | None = Header(default=None, alias="X-Jarvis-Stream-Segments"),
) -> StreamingResponse:
    session_id = _resolve_session(request)
    # Opt-in: separate model rounds with agent.ROUND_BREAK so the app can
    # show "checking..." and the result as separate bubbles.
    segments = isinstance(stream_segments, str) and stream_segments.strip() == "1"

    async def chunks():
        # A synchronous StreamingResponse can resume a generator on different
        # pool threads. Keep the runtime's RLock/context on one worker, and
        # finish persistence even when the phone stops reading or disconnects.
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        disconnected = threading.Event()

        def publish(value):
            if not disconnected.is_set():
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, value)
                except RuntimeError:
                    pass  # Application shutdown; worker still finishes history.

        def produce():
            stream_round_breaks.set(segments)
            try:
                kwargs = {"request_id": request.request_id} if request.request_id else {}
                for fragment in runtime().respond_streaming_in_session(session_id, request.message.strip(), **kwargs):
                    publish(fragment.encode())
            except Exception as exc:
                publish(exc)
            finally:
                publish(None)

        threading.Thread(target=produce, name="companion-turn", daemon=True).start()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            disconnected.set()

    return StreamingResponse(
        chunks(),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Jarvis-Session-ID": session_id,
            "X-Jarvis-Stream-Segments": "1" if segments else "0",
            # URLSession sniffs up to 512 bytes of text/plain before handing
            # any to the app, so most replies arrived all at once. nosniff
            # turns that off; the others stop proxies from buffering.
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/debug-reports", status_code=status.HTTP_201_CREATED)
def submit_debug_report(
    request: DebugReportRequest,
    _: None = Depends(authorize),
) -> dict[str, object]:
    """Store user-approved companion context for later JARVIS diagnosis."""
    try:
        return create_debug_report(
            issue=request.issue,
            messages=[message.model_dump() for message in request.messages],
            session_id=request.session_id,
            conversation_title=request.conversation_title,
            focus_message_id=request.focus_message_id,
            app_context=request.app_context,
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not store debug report: {exc}") from exc


# --- Read-only dashboard endpoints -----------------------------------------
# Aggregated in app.status_hub over the existing integrations. Every route
# returns 200 with an ``error`` field when a backing service is down — the
# companion app must never see a hard failure because the NAS is unreachable.


@app.get("/api/status/torrents")
def status_torrents(_: None = _status_read) -> dict[str, object]:
    return status_hub.torrents_status()


@app.get("/api/now")
def now_status(_: None = _status_read) -> dict[str, object]:
    return status_hub.now_status()


@app.get("/api/glance")
def glance_status(_: None = _status_read) -> dict[str, object]:
    """Ambient-screen snapshot (the TV launcher): greeting, theme, weather,
    Now feed, downloads and the last day's alerts in one call."""
    return status_hub.glance_status()


@app.get("/api/study/brief")
def study_brief_status(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.study_brief_status()


@app.get("/api/status/media")
def status_media(_: None = _status_read) -> dict[str, object]:
    return status_hub.media_status()


@app.get("/api/status/media/diagnose")
def status_media_diagnose(
    title: str | None = None, kind: str | None = None, _: None = _status_read,
) -> dict[str, object]:
    """Read-only trace of one title (or every queued download). Fixes are
    listed for display; applying one goes through chat and the confirm gate."""
    return media_doctor.diagnose(title, kind)


class MediaRequest(BaseModel):
    kind: str = Field(pattern="^(movie|show)$")
    id: int = Field(gt=0)
    quality: str | None = Field(default=None, max_length=80)


class MediaConfirm(BaseModel):
    code: str = Field(min_length=4, max_length=4)


@app.get("/api/media/search")
def media_search(q: str, kind: str = "movie", _: None = Depends(authorize)) -> dict[str, object]:
    """Lookup only; records which titles were shown so only they can be requested."""
    try:
        return media_requests.search(q, kind)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/media/requests")
def media_request(request: MediaRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Step one of two: opens a confirm-gate proposal; nothing is added yet."""
    return media_requests.request(request.kind, request.id, request.quality)


@app.post("/api/media/requests/confirm")
def media_request_confirm(request: MediaConfirm, _: None = Depends(authorize)) -> dict[str, object]:
    """Step two: approve the open proposal with its code."""
    return media_requests.confirm(request.code)


@app.get("/api/alerts/recent")
def recent_alerts(limit: int = 10, hours: float = 24, _: None = _status_read) -> dict[str, object]:
    """Newest-first bus alerts for polling clients (the Hub's Needs attention
    card). Info-level alerts (e.g. "back online") are included so a client
    can tell a problem cleared."""
    cutoff = time.time() - max(0.0, hours) * 3600
    alerts = [a.to_dict() for a in reversed(event_bus.since(0, limit=200)) if a.created_at >= cutoff]
    return {"alerts": alerts[: max(1, min(50, limit))]}


class IncidentReportRequest(BaseModel):
    created_at: float | None = None
    session_id: str | None = Field(default=None, max_length=100)


@app.post("/api/alerts/{alert_id}/report")
def alert_report(alert_id: int, request: IncidentReportRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Tap-to-chat: gather logs, health and recent changes for one alert,
    store them as an incident debug report, and return the chat prompt.
    Read-only; fixes are proposed in the chat that follows."""
    alert = incident_reports.find_alert(alert_id, request.created_at)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found (the server may have restarted)")
    try:
        return incident_reports.build(alert, session_id=request.session_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not build incident report: {exc}") from exc


@app.get("/api/repairs")
def repairs(_: None = Depends(authorize)) -> dict[str, object]:
    """Pending and recent self-repair proposals for the Home tab's Repairs card."""
    return {"repairs": self_repair.public_proposals()}


@app.post("/api/repairs/{proposal_id}/apply")
def repair_apply(proposal_id: str, _: None = Depends(authorize)) -> dict[str, object]:
    """Nick's Approve tap is the approval. Stack fixes run in the background
    and report their verified outcome as an alert; poll /api/repairs."""
    runtime()  # the runtime wires the repair notifier
    message = self_repair.apply(proposal_id, confirmed=True, approved_by="app")
    current = next((r for r in self_repair.public_proposals() if r["id"] == proposal_id), None)
    return {"message": message, "status": current["status"] if current else "unknown"}


@app.post("/api/repairs/{proposal_id}/reject")
def repair_reject(proposal_id: str, _: None = Depends(authorize)) -> dict[str, object]:
    return {"message": self_repair.reject(proposal_id)}


# --- To-dos (open reminders; checking one off deletes it) -------------------


class TodoRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)


@app.post("/api/todos")
def todo_create(request: TodoRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain.reminders import add_reminder
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="A to-do needs some text")
    item = add_reminder(text)
    status_hub.invalidate("now")
    return {"todo": {"id": item.id, "text": item.text, "due_at": None, "is_due": False}}


@app.post("/api/todos/{todo_id}/done")
def todo_done(todo_id: str, _: None = Depends(authorize)) -> Response:
    from brain.reminders import remove_reminder
    if not remove_reminder(todo_id):
        raise HTTPException(status_code=404, detail="Unknown to-do")
    status_hub.invalidate("now")
    return Response(status_code=204)


# --- Wake-up schedule (music on the TV; brain/wake_alarm.py) ---------------


class WakeScheduleRequest(BaseModel):
    times: list[str | None] = Field(min_length=7, max_length=7)
    music: str = Field(default="", max_length=300)
    label: str = Field(default="", max_length=80)
    volume: int = Field(default=30, ge=5, le=100)
    enabled: bool = True


@app.get("/api/wake")
def wake_get(_: None = Depends(authorize)) -> dict[str, object]:
    from brain import wake_alarm
    return wake_alarm.editor()


@app.put("/api/wake")
def wake_put(request: WakeScheduleRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import wake_alarm
    try:
        wake_alarm.set_schedule(request.times, request.music, request.label, request.volume, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return wake_alarm.editor()


# --- Charles Schwab, read-only (integrations/schwab.py) ----------------------


@app.get("/api/portfolio")
def portfolio(_: None = _status_read) -> dict[str, object]:
    from integrations import schwab
    return schwab.snapshot()


@app.post("/api/schwab/login")
def schwab_login(_: None = Depends(authorize)) -> dict[str, object]:
    """Schwab's login page for the app's web view, and the callback address
    to catch (the app hands that redirect to /api/schwab/finish)."""
    from integrations import schwab
    try:
        return {"url": schwab.login_url(), "callback": os.getenv("SCHWAB_CALLBACK_URL", "").strip()}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class SchwabFinishRequest(BaseModel):
    redirect_url: str = Field(min_length=1, max_length=4_000)


@app.post("/api/schwab/finish")
def schwab_finish(request: SchwabFinishRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """The redirect the app caught after Schwab's login (code + state)."""
    from integrations import schwab
    try:
        schwab.finish_from_redirect(request.redirect_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Schwab refused the code
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    threading.Thread(target=runtime, daemon=True).start()  # monitors pick it up
    return {"linked": True}


def _schwab_page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(status_code=status_code, content=(
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<body style='font:17px -apple-system,sans-serif;background:#06101c;color:#e8f6fa;"
        "text-align:center;padding:64px 24px'>"
        f"<h2 style='color:#40d9f2;font-weight:600'>{title}</h2><p>{body}</p></body>"))


@app.get("/api/schwab/callback", include_in_schema=False)
def schwab_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    """Where Schwab sends Safari after Nick logs in (SCHWAB_CALLBACK_URL, via
    Tailscale HTTPS). No bearer token: the one-time state from
    /api/schwab/login is what proves the login came from JARVIS."""
    from integrations import schwab
    if error or not code:
        return _schwab_page("Not linked", "Schwab didn't finish the login. Try again from the app.", 400)
    try:
        schwab.finish_login(code, state)
    except PermissionError as exc:
        return _schwab_page("Not linked", str(exc), 403)
    except Exception as exc:  # noqa: BLE001 - show Nick, don't 500
        return _schwab_page("Not linked", f"Schwab said no: {exc}", 502)
    threading.Thread(target=runtime, daemon=True).start()  # monitors pick it up
    return _schwab_page("Linked, sir.", "Your portfolio will be on JARVIS in a moment. You can close this tab.")


# --- TV remote (integrations/tv_remote.py) ------------------------------------


class TvRemoteRequest(BaseModel):
    key: str = Field(min_length=1, max_length=20)


@app.post("/api/tv/remote")
def tv_remote_press(request: TvRemoteRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """One button on the companion's TV remote."""
    from integrations import tv_remote
    try:
        ok, detail = tv_remote.press(request.key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    return {"ok": True}


@app.post("/api/wake/snooze")
def wake_snooze(_: None = Depends(authorize)) -> dict[str, object]:
    """Snooze from the phone: the TV pauses now, Core rings again in 9 min."""
    from brain import wake_alarm
    at = wake_alarm.snooze()
    event_bus.publish("wake_snooze", "Snoozed", f"Back at {at.strftime('%-I:%M')}.", source="wake-up",
                      severity="info", push=False, context={"until": at.timestamp()})
    return {"until": at.timestamp()}


@app.post("/api/wake/awake")
def wake_awake(_: None = Depends(authorize)) -> dict[str, object]:
    """I'm up from the phone: the TV's wake screen closes, music plays on."""
    from brain import wake_alarm
    wake_alarm.awake()
    event_bus.publish("wake_dismiss", "Up and at 'em", "", source="wake-up", severity="info", push=False)
    return {"ok": True}


# --- Shared colour theme (phone and TV follow one pick; brain/theme.py) -----


class ThemeRequest(BaseModel):
    theme: str = Field(pattern="^(arc|hotRod|malibu|crimson|mark85|spiderMan|mcqueen)$")


@app.get("/api/theme")
def theme_get(_: None = _status_read) -> dict[str, object]:
    from brain import theme
    return theme.current()


@app.put("/api/theme")
def theme_put(request: ThemeRequest, _: None = Depends(require_scope("settings:write"))) -> dict[str, object]:
    from brain import theme
    return theme.choose(request.theme)


# --- Maintenance (recurring chores with a countdown and a log) -------------


class MaintenanceTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schedule_type: str = Field(pattern="^(interval|fixed_calendar|log_only)$")
    interval_value: int | None = Field(default=None, ge=1, le=3650)
    interval_unit: str | None = Field(default=None, pattern="^(days|months|years)$")
    fixed_month: int | None = Field(default=None, ge=1, le=12)
    fixed_day: int | None = Field(default=None, ge=1, le=31)
    notes: str = Field(default="", max_length=2_000)
    last_done: str | None = Field(default=None, max_length=10)


class MaintenanceCompleteRequest(BaseModel):
    note: str = Field(default="", max_length=1_000)
    completed_on: str | None = Field(default=None, max_length=10)


def _maintenance_changed() -> None:
    # The Now feed lists due tasks; show a completion on the next poll.
    status_hub.invalidate("now")


@app.get("/api/maintenance")
def maintenance_list(_: None = Depends(authorize)) -> dict[str, object]:
    return {"tasks": maintenance.list_tasks()}


@app.post("/api/maintenance")
def maintenance_create(request: MaintenanceTaskRequest, _: None = Depends(authorize)) -> dict[str, object]:
    try:
        task = maintenance.create_task(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _maintenance_changed()
    return {"task": task}


@app.put("/api/maintenance/{task_id}")
def maintenance_update(task_id: int, request: MaintenanceTaskRequest, _: None = Depends(authorize)) -> dict[str, object]:
    try:
        task = maintenance.update_task(task_id, request.model_dump(exclude={"last_done"}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown maintenance task")
    _maintenance_changed()
    return {"task": task}


@app.delete("/api/maintenance/{task_id}")
def maintenance_delete(task_id: int, _: None = Depends(authorize)) -> Response:
    if not maintenance.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Unknown maintenance task")
    _maintenance_changed()
    return Response(status_code=204)


@app.post("/api/maintenance/{task_id}/complete")
def maintenance_complete(
    task_id: int, request: MaintenanceCompleteRequest | None = None, _: None = Depends(authorize)
) -> dict[str, object]:
    body = request or MaintenanceCompleteRequest()
    try:
        task = maintenance.complete_task(task_id, body.note, body.completed_on)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Unknown maintenance task")
    _maintenance_changed()
    return {"task": task}


@app.get("/api/maintenance/{task_id}/history")
def maintenance_history(task_id: int, _: None = Depends(authorize)) -> dict[str, object]:
    entries = maintenance.history(task_id)
    if entries is None:
        raise HTTPException(status_code=404, detail="Unknown maintenance task")
    return {"task": maintenance.get_task(task_id), "history": entries}


@app.delete("/api/maintenance/{task_id}/history/{entry_id}")
def maintenance_delete_entry(task_id: int, entry_id: int, _: None = Depends(authorize)) -> Response:
    if not maintenance.delete_log_entry(task_id, entry_id):
        raise HTTPException(status_code=404, detail="Unknown log entry")
    _maintenance_changed()
    return Response(status_code=204)


@app.get("/api/clients/events")
async def client_events(
    request: Request,
    since: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _: None = Depends(require_scope("events:read")),
) -> StreamingResponse:
    """Server-sent events of every bus alert (brain/event_bus.py).

    Replays alerts newer than ``Last-Event-ID`` (or ``?since=``) so a client
    that reconnects misses nothing still in the 200-alert history, then
    streams live ones with a keep-alive comment every 15 s."""
    await asyncio.to_thread(runtime)  # monitors are what publish alerts
    try:
        start = int(last_event_id) if last_event_id else since
    except ValueError:
        start = since
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    name = f"sse-{uuid.uuid4()}"

    def offer(alert: event_bus.Alert) -> None:
        try:
            queue.put_nowait(alert)
        except asyncio.QueueFull:
            pass  # slow client: it can catch up from history on reconnect

    def on_alert(alert: event_bus.Alert) -> None:
        try:
            loop.call_soon_threadsafe(offer, alert)
        except RuntimeError:
            pass  # loop closed: the stream is gone

    # Subscribe before replaying so nothing published in between is lost;
    # ids dedupe the overlap.
    event_bus.subscribe(name, on_alert)

    def frame(alert: event_bus.Alert) -> str:
        return f"id: {alert.id}\nevent: alert\ndata: {json.dumps(alert.to_dict())}\n\n"

    async def events():
        last = start
        try:
            for alert in event_bus.since(start):
                last = alert.id
                yield frame(alert)
            while not await request.is_disconnected():
                try:
                    alert = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if alert.id > last:
                    last = alert.id
                    yield frame(alert)
        finally:
            event_bus.unsubscribe(name)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
    })


@app.get("/api/status/storage")
def status_storage(_: None = _status_read) -> dict[str, object]:
    return status_hub.media_storage_status()


@app.get("/api/status/services")
def status_services(_: None = _status_read) -> dict[str, object]:
    return status_hub.services_status()


@app.get("/api/links")
def service_links(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.links()


@app.get("/api/home/entities")
def home_entities(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.home_entities()


# --- Quick actions ----------------------------------------------------------
# Guarded writes for the companion hub. Invalid input (bad action, entity not
# on the HOME_DASHBOARD_ENTITIES allowlist) is a 400; a failed or refused
# service call still returns 200 with an ``error`` field.


class HomeControlRequest(BaseModel):
    entity_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=32)


class TorrentControlRequest(BaseModel):
    torrent_hash: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=32)
    confirmed: bool = False


@app.post("/api/home/control")
def home_control(request: HomeControlRequest, _: None = Depends(authorize)) -> dict[str, object]:
    try:
        return status_hub.home_control(request.entity_id, request.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/torrents/control")
def torrents_control(
    request: TorrentControlRequest, _: None = Depends(authorize)
) -> dict[str, object]:
    try:
        return status_hub.torrent_control(
            request.torrent_hash, request.action, request.confirmed
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Push notification registration -----------------------------------------
# Server half of companion push. Tokens are stored now so the app can be
# wired up independently; delivery stays on Home Assistant until APNs
# credentials exist (see brain/notifications.py and OVERNIGHT_HANDOFF.md).


class PushRegisterRequest(BaseModel):
    device_token: str = Field(min_length=64, max_length=200)
    environment: str = Field(default="production", pattern="^(production|sandbox)$")
    device_name: str = Field(default="", max_length=80)


@app.post("/api/push/register")
def push_register(request: PushRegisterRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import notifications
    ok, detail = notifications.register_device(request.device_token, request.environment, request.device_name)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"registered": True, **notifications.status()}


@app.delete("/api/push/register/{device_token}")
def push_unregister(device_token: str, _: None = Depends(authorize)) -> Response:
    from brain import notifications
    notifications.unregister_device(device_token)
    return Response(status_code=204)


@app.get("/api/push/status")
def push_status(_: None = Depends(authorize)) -> dict[str, object]:
    from brain import notifications
    return notifications.status()


def run_server() -> None:
    """Run the companion API using environment-configured bind settings."""
    import uvicorn

    uvicorn.run(
        "app.server:app",
        host=os.getenv("JARVIS_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("JARVIS_SERVER_PORT", "8000")),
        reload=False,
        # SSE clients (the TV) never close their stream, so without a cap a
        # restart sat in "Waiting for connections to close" until systemd's
        # 90 s stop timeout killed it. They reconnect and replay on their own.
        timeout_graceful_shutdown=5,
    )
