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

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, status
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
from app import auth_guard, client_tokens
from brain import event_bus, traffic, voice_presence
from integrations import media_doctor, media_requests
from brain import incident_reports, maintenance, self_repair
from jarvis_logger import get_logger

log = get_logger(__name__)
_SSE_KEEPALIVE_S = 15.0

_runtime: JarvisRuntime | None = None
_runtime_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start scheduled monitoring even when no companion has connected yet."""
    from integrations.nas_stack import enabled
    from speech import room_voice
    if enabled("ARR_STACK_MONITOR_ENABLED") or os.getenv("JARVIS_HEALTHCHECKS_URL", "").strip():
        await asyncio.to_thread(runtime)
    if room_voice.enabled():  # "Hey JARVIS" on the Surface's mic, same runtime as the apps
        room_voice.start(await asyncio.to_thread(runtime))
    # Under systemd with WatchdogSec (deploy/surface-setup.sh): ping only while
    # our own /health answers, so a wedged Core gets restarted. Inert elsewhere.
    from brain.watchdog import SystemdWatchdog
    systemd_watchdog = SystemdWatchdog(_self_health)
    systemd_watchdog.start()
    try:
        yield
    finally:
        systemd_watchdog.stop()
        room_voice.stop()
        if _runtime is not None:
            await asyncio.to_thread(_runtime.stop_monitors)


def _self_health() -> bool:
    """Core asking itself over loopback: does /health answer within 10 s?"""
    import requests
    port = int(os.getenv("JARVIS_SERVER_PORT", "8000"))
    token = os.getenv("JARVIS_API_TOKEN", "")
    try:
        response = requests.get(f"http://127.0.0.1:{port}/health", timeout=10,
                                headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException:
        return False
    return response.status_code == 200


app = FastAPI(title="JARVIS Core", version="1.0", lifespan=lifespan)
traffic.install()  # Core -> NAS / internet calls feed the companion's link map


def _caller(authorization: str | None, device: str | None) -> str:
    """The traffic node a request came from (brain/traffic.py)."""
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    client = client_tokens.scopes_for(supplied) if supplied else None
    return traffic.client_node(client[0] if client else None, device)


def _pushed(request: Request, label: str) -> None:
    """A stream just pushed to its client: a Core -> client signal."""
    try:
        traffic.record("core", _caller(request.headers.get("authorization"),
                                       request.headers.get("x-jarvis-device")), label)
    except Exception:  # noqa: BLE001 - the map must never break a stream
        pass


def _is_phone(headers: dict[str, str]) -> bool:
    """Nick's iPhone on the admin token (not an iPad, the Mac, or a client token
    such as the website's, which also arrives through Lightsail)."""
    if (headers.get("x-jarvis-device") or "").strip().lower() != "iphone":
        return False
    supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
    expected = os.getenv("JARVIS_API_TOKEN", "").strip()
    return bool(expected) and hmac.compare_digest(supplied, expected)


def _note_phone(scope) -> None:
    """A presence sighting (brain/presence.py): the iPhone's real address,
    since uvicorn rewrites the client from X-Forwarded-For for Lightsail."""
    try:
        from brain import presence
        client = scope.get("client")
        presence.note_phone(client[0] if client else None)
    except Exception:  # noqa: BLE001 - presence must never break a request
        log.debug("Phone sighting failed", exc_info=True)


class _TrafficTap:
    """Every request is a signal from its client to Core, and its answer a
    signal back once the reply has gone out (a camera frame, a status
    snapshot): the link map and the iPad's neural core light up with both.
    Streams only count when they open; their pushes log themselves
    (``_pushed``). The traffic stream itself isn't logged. Plain ASGI, not
    ``@app.middleware``, so the SSE streams pass untouched."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.inner(scope, receive, send)
            return
        path = scope.get("path", "")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if path == "/api/traffic/stream" or not headers.get("authorization"):
            await self.inner(scope, receive, send)
            return
        label = path.removeprefix("/api/").removeprefix("/")
        try:
            caller = _caller(headers["authorization"], headers.get("x-jarvis-device"))
            traffic.record(caller, "core", label)
        except Exception:  # noqa: BLE001 - the map must never break a request
            caller = None
        answered = {"ok": False}
        phone = _is_phone(headers)

        async def reply(message):
            if message.get("type") == "http.response.start":
                answered["ok"] = int(message.get("status", 500)) < 400
                if phone and answered["ok"]:
                    _note_phone(scope)
            await send(message)
            if (caller and answered["ok"] and message.get("type") == "http.response.body"
                    and not message.get("more_body", False)):
                traffic.record("core", caller, label)

        await self.inner(scope, receive, reply)


app.add_middleware(_TrafficTap)

_COMPANION_API_VERSION = 1
_COMPANION_CAPABILITIES: dict[str, object] = {
    "chat": {
        "streaming": True,
        "request_receipts": True,
        "server_history": True,
        "debug_reports": True,
        "incident_reports": True,
        "voice_live": True,
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
        "shopping": True,
        "glance": True,
        "wake_schedule": True,
        "portfolio": True,
        "tv_remote": True,
        "shared_theme": True,
        "knowledge": True,
    },
    "actions": {
        "home_on_off": True,
        "torrent_pause_resume": True,
        "repairs": True,
        "alert_fix": True,
        "autopilot": True,
        "skill_requests": True,
        "movie_picker": True,
        "skills": True,
        "presence": True,
    },
}


def runtime() -> JarvisRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = JarvisRuntime()
        return _runtime


def _client_ip(connection: Request | WebSocket | None) -> str | None:
    """The caller's address: the phone's real one behind Lightsail, since
    uvicorn trusts X-Forwarded-For only from JARVIS_TRUSTED_PROXIES."""
    client = getattr(connection, "client", None)
    return client.host if client else None


def _refuse(ip: str | None) -> HTTPException:
    """A wrong token: count it, and refuse harder once the address is locked out."""
    auth_guard.record_failure(ip)
    if auth_guard.blocked(ip):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                             detail="Too many wrong tokens; try again later")
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _check_admin(authorization: str | None, ip: str | None) -> None:
    expected = os.getenv("JARVIS_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="JARVIS_API_TOKEN is not configured")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not hmac.compare_digest(supplied, expected):
        raise _refuse(ip)


def authorize(request: Request, authorization: str | None = Header(default=None)) -> None:
    """The companion's admin token. A valid token always gets in; wrong ones
    count toward the lockout (app/auth_guard.py)."""
    _check_admin(authorization, _client_ip(request))


def _authorize_socket(socket: WebSocket) -> None:
    _check_admin(socket.headers.get("authorization"), _client_ip(socket))


def require_scope(scope: str):
    """Admin token (JARVIS_API_TOKEN) or a client token holding ``scope``.

    Client tokens (app/client_tokens.py) let a TV or dashboard read alerts
    and status without ever holding the admin token. Returns the client's
    name, or None for the admin token."""
    def dependency(request: Request, authorization: str | None = Header(default=None)) -> str | None:
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        expected = os.getenv("JARVIS_API_TOKEN", "").strip()
        if expected and hmac.compare_digest(supplied, expected):
            return None
        client = client_tokens.scopes_for(supplied)
        if client is None:
            if not expected:
                raise HTTPException(status_code=503, detail="JARVIS_API_TOKEN is not configured")
            raise _refuse(_client_ip(request))
        if scope not in client[1]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Token lacks scope {scope}")
        return client[0]
    return dependency


def _lockout_alert(ip: str, count: int) -> None:
    runtime().raise_alert(
        "security", "Someone is guessing JARVIS's token",
        f"{count} wrong tokens from {ip}; that address is locked out for a while.",
        source="auth", severity="warning", dedupe_key=f"auth-lockout-{ip}", cooldown_s=3600)


auth_guard.on_lockout = _lockout_alert


_status_read = Depends(require_scope("status:read"))


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=100)


class ChatResponse(BaseModel):
    reply: str
    session_id: str


@app.websocket("/api/voice/live")
async def companion_voice(socket: WebSocket) -> None:
    try:
        _authorize_socket(socket)
    except HTTPException:
        await socket.close(code=1008)
        return
    from app.companion_voice import serve
    await serve(socket, runtime)


@app.websocket("/api/voice/satellite")
async def voice_satellite(socket: WebSocket) -> None:
    """An iPad lends the room voice its mic and speaker (speech/satellites.py).
    ``?name=bed`` (or ``door``). In: binary 16 kHz mono int16 PCM. Out: JSON
    control messages and the reply's PCM. Companion token only."""
    try:
        _authorize_socket(socket)
    except HTTPException:
        await socket.close(code=1008)
        return
    from speech import room_voice, satellites
    name = satellites.clean_name(socket.query_params.get("name"))
    if name is None:
        await socket.close(code=1008)
        return
    await socket.accept()
    if not room_voice.enabled():
        await socket.send_text(json.dumps({"type": "off"}))
        await socket.close(code=1000)
        return
    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue = asyncio.Queue()

    def send(item: str | bytes) -> None:  # from the room voice's threads
        try:
            loop.call_soon_threadsafe(outbox.put_nowait, item)
        except RuntimeError:  # the loop is gone: the socket is closing anyway
            pass

    async def writer() -> None:
        while (item := await outbox.get()) is not None:
            if isinstance(item, bytes):
                await socket.send_bytes(item)
            else:
                await socket.send_text(item)

    async def keepalive() -> None:  # Core is silent between replies; the iPad's idle timeout isn't
        while True:
            await asyncio.sleep(20)
            send(json.dumps({"type": "ping"}))

    from speech import room_volume
    satellites.link_room_volume()
    sat = satellites.register(name, send)
    task = asyncio.create_task(writer())
    pinger = asyncio.create_task(keepalive())
    try:
        level = await asyncio.to_thread(room_volume.level)
        sat.send_json({"type": "hello", "rate": 16000, **({"level": level} if level is not None else {})})
        while True:
            message = await socket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                sat.feed(message["bytes"])
            elif message.get("text"):
                try:
                    note = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(note, dict) and note.get("type") == "volume":
                    try:  # the iPad's volume buttons: the whole room's level (every iPad follows)
                        await asyncio.to_thread(room_volume.set_level, int(note.get("level")))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(note, dict) and note.get("type") == "bye":
                    log.info("Voice satellite %s hanging up: %s", name, str(note.get("reason", ""))[:200])
    except Exception as exc:  # noqa: BLE001 - a dropped socket ends the satellite, nothing else
        log.info("Voice satellite %s socket ended: %s", name, exc or type(exc).__name__)
    finally:
        satellites.unregister(sat)
        outbox.put_nowait(None)
        pinger.cancel()
        task.cancel()


@app.get("/api/voice/satellites")
def voice_satellites(_: None = Depends(authorize)) -> dict[str, object]:
    """Which iPads are lending the room voice their mic and speaker, and the
    desk-vs-iPad offset (``sync_ms``: > 0 the desk waits)."""
    from speech import satellites
    return {"satellites": satellites.status(), "sync_ms": satellites.sync_ms()}


class SatelliteSyncRequest(BaseModel):
    nudge_ms: float | None = Field(default=None, ge=-500, le=500)
    reset: bool = False


@app.post("/api/voice/satellites/sync")
def voice_satellites_sync(request: SatelliteSyncRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Desk earlier / later from the iPad: ``nudge_ms`` > 0 holds the desk
    back more (the iPads trail), < 0 less; ``reset`` goes back to ``.env``."""
    from speech import satellites
    if request.reset:
        return {"sync_ms": satellites.set_sync_ms(None)}
    return {"sync_ms": satellites.set_sync_ms(satellites.sync_ms() + (request.nudge_ms or 0))}


class SpeakerTestRequest(BaseModel):
    target: str = Field(min_length=1, max_length=20)  # "desk" or a satellite's name
    text: str = Field(default="", max_length=200)


@app.post("/api/voice/satellites/test")
def voice_speaker_test(request: SpeakerTestRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Say a line on one speaker alone (the desk, or one iPad), to check
    each speaker by itself. Returns at once; the line plays in the background."""
    import threading as _threading
    from speech import remote_voice, satellites
    name = "the desk" if request.target == "desk" else f"the {request.target} iPad"
    text = request.text.strip() or f"Testing, sir. This is {name}."
    if request.target == "desk":
        from speech.tts import speak
        _threading.Thread(target=speak, args=(text,), daemon=True, name="speaker-test").start()
        return {"ok": True, "target": "desk"}
    sat = satellites.get(satellites.clean_name(request.target) or "")
    if sat is None:
        return {"ok": False, "detail": f"{request.target} isn't connected"}

    def play() -> None:
        stopped = _threading.Event()
        try:
            sat.play_pcm(remote_voice.pcm_stream(text, stopped), remote_voice.SAMPLE_RATE)
        finally:
            stopped.set()

    _threading.Thread(target=play, daemon=True, name="speaker-test").start()
    return {"ok": True, "target": sat.name}


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
def chat(request: ChatRequest, client: str | None = Depends(require_scope("chat"))) -> ChatResponse:
    """Nick's token speaks as Nick; a client token (the TV remote's mic) is
    whoever is holding it, so it can't use voice-locked tools
    (``brain/voice_trust.py``) unless listed in ``VOICE_TRUSTED_CLIENTS``."""
    from brain import voice_trust
    session_id = _resolve_session(request)
    speaker = (voice_trust.OWNER if client is None or client.lower() in voice_trust.trusted_clients()
               else voice_trust.guest(f"client token {client}"))
    try:
        kwargs = {"request_id": request.request_id} if request.request_id else {}
        with voice_trust.speaking(speaker):
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
    from brain import auto_heal
    cutoff = time.time() - max(0.0, hours) * 3600
    alerts: list[dict[str, object]] = []
    seen: dict[str, dict[str, object]] = {}
    first_seen: dict[str, float] = {}
    for a in reversed(event_bus.since(0, limit=200)):  # newest first
        if a.created_at < cutoff or event_bus.is_cleared(a):
            continue
        key = event_bus.condense_key(a.kind, a.title, a.message, a.context)
        first_seen[key] = a.created_at  # oldest copy wins: the walk is newest first
        if key in seen:  # the same problem again: one row, counted
            seen[key]["repeats"] = int(seen[key]["repeats"]) + 1
            continue
        seen[key] = {**a.to_dict(), "repeats": 1,
                     "fixable": auto_heal.fix_for(a.kind, a.context) is not None,
                     "_link": auto_heal.link_for(a.kind, a.context), "_key": key}
        alerts.append(seen[key])
    alerts = alerts[: max(1, min(50, limit))]
    for row in alerts:
        # What JARVIS is doing about it: the newest repair tied to this alert
        # since it first appeared (autopilot or a Fix it tap).
        link, key = row.pop("_link"), row.pop("_key")
        if link:
            row["repair"] = self_repair.latest_for_link(str(link), first_seen[str(key)] - 1800)
    return {"alerts": alerts}


@app.post("/api/alerts/refresh")
def refresh_alerts(_: None = Depends(authorize)) -> dict[str, object]:
    """The Needs attention Refresh button: re-check each alert against the
    live system; fixed ones disappear, the rest stay."""
    from brain import alert_checks
    return alert_checks.refresh()


class AlertActionRequest(BaseModel):
    created_at: float | None = None


@app.post("/api/alerts/{alert_id}/fix")
def alert_fix(alert_id: int, request: AlertActionRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """The alert sheet's Fix it button. The tap is Nick's approval; the fix
    runs in the background and its progress shows on the alert (``repair``)."""
    from brain import auto_heal
    runtime()  # the runtime wires the repair notifier
    alert = incident_reports.find_alert(alert_id, request.created_at)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found (the server may have restarted)")
    return auto_heal.fix_alert(alert)


@app.post("/api/alerts/{alert_id}/dismiss")
def alert_dismiss(alert_id: int, request: AlertActionRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Swipe away one alert (every repeat of the same problem goes with it)."""
    alert = incident_reports.find_alert(alert_id, request.created_at)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found (the server may have restarted)")
    key = event_bus.condense_key(alert.kind, alert.title, alert.message, alert.context)
    ids = [a.id for a in event_bus.since(0, limit=200)
           if event_bus.condense_key(a.kind, a.title, a.message, a.context) == key]
    event_bus.resolve(ids)
    return {"dismissed": len(ids)}


@app.get("/api/autopilot")
def autopilot(_: None = Depends(authorize)) -> dict[str, object]:
    """Whether JARVIS fixes the basic stuff himself, which fixes, and what he ran today."""
    from brain import auto_heal
    return {"enabled": auto_heal.enabled(), "fixes": sorted(auto_heal.allowed_fixes()),
            "never": sorted(auto_heal.NEVER_AUTO), "runs": auto_heal.recent_runs()}


class AutopilotRequest(BaseModel):
    enabled: bool


@app.post("/api/autopilot")
def autopilot_set(request: AutopilotRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import settings as settings_mod
    settings_mod.update_setting("AUTO_REPAIR_ENABLED", "true" if request.enabled else "false")
    return autopilot()


@app.post("/api/skills/requests/{request_id}/draft")
def skill_request_draft(request_id: str, _: None = Depends(authorize)) -> dict[str, object]:
    """Draft a skill tap: the skill drafter subagent works on it in the
    background; poll /api/skills/requests (``draft_skill_id`` or ``note``)."""
    from brain import skill_drafter, skill_requests
    if not any(r["id"] == request_id for r in skill_requests.listing("all")):
        raise HTTPException(status_code=404, detail="No such skill request")
    runtime()  # jobs show in the runtime's task lane
    started = skill_drafter.start(request_id)
    if started:
        skill_requests.annotate(request_id, drafting=True, note="")
    return {"started": started, "message": "Drafting a skill…" if started else "A skill is already being drafted."}


@app.get("/api/skills")
def skill_list(_: None = Depends(authorize)) -> dict[str, object]:
    """Learned skills: drafts waiting for approval, active and disabled ones."""
    from brain import skills
    return {"skills": [{**s, "summary": skills.describe(s)} for s in skills.listing()]}


class SkillUpdate(BaseModel):
    status: str = Field(pattern="^(active|disabled|deleted)$")


@app.post("/api/skills/{skill_id}")
def skill_update(skill_id: str, request: SkillUpdate, _: None = Depends(authorize)) -> dict[str, object]:
    """Approve (active), switch off (disabled) or throw away (deleted). The tap is Nick's approval."""
    from brain import skills
    try:
        record = skills.set_status(skill_id, request.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="No such skill")
    return {"skill": record}


@app.get("/api/presence")
def presence_status(_: None = _status_read) -> dict[str, object]:
    """Home or away (null when unknown): the newer of the iPhone's GPS through
    Home Assistant and where the phone last reached Core from."""
    from brain import presence
    from app import status_hub
    where, at = presence.phone_sighting()
    return {"location": presence.location(), "person": presence.person_entity(), "room_quiet": status_hub.room_away(),
            "phone": {"where": where, "at": at or None}}


class PhonePresence(BaseModel):
    where: str = Field(pattern="^(home|away)$")


@app.post("/api/presence/phone")
def presence_phone(body: PhonePresence, _: None = Depends(authorize)) -> dict[str, object]:
    """The iPhone's geofence crossed the home circle (iOS wakes the app for it)."""
    from brain import presence
    presence.report(body.where, source="geofence")
    return {"ok": True, "location": presence.location(fresh=True)}


@app.get("/api/presence/zone")
def presence_zone(_: None = Depends(authorize)) -> dict[str, object]:
    """The circle around home the iPhone's geofence watches (HA's zone.home)."""
    from brain import presence
    zone = presence.home_zone()
    if zone is None:
        raise HTTPException(status_code=503, detail="Home Assistant hasn't said where home is")
    return {"zone": zone}


@app.post("/api/presence/lights-off")
def presence_lights_off(_: None = Depends(authorize)) -> dict[str, object]:
    """The "You left lights on" push's Turn them off button."""
    from brain import presence
    done, failed = presence.turn_off_left_on()
    message = (f"Turned off {done} light{'s' if done != 1 else ''}." if done else "Nothing was still on.")
    if failed:
        message += f" Couldn't turn off {', '.join(failed)}."
    return {"turned_off": done, "failed": failed, "message": message}


# --- Movie picker (iPad movie mode) ------------------------------------------


@app.get("/api/plex/movies")
def plex_movies(query: str = "", sort: str = "recent", _: None = Depends(authorize)) -> dict[str, object]:
    """Plex films for the picker: title, year, poster path, resume point."""
    from integrations import plex
    if not plex.is_configured():
        raise HTTPException(status_code=503, detail="Plex is not configured on Core")
    ok, films = plex.library_movies(query[:100], sort if sort in ("recent", "title", "year", "unwatched") else "recent")
    if not ok:
        raise HTTPException(status_code=502, detail=str(films))
    return {"movies": films}


@app.get("/api/plex/libraries")
def plex_libraries(_: None = Depends(authorize)) -> dict[str, object]:
    """Movie and show libraries, and which ones the picker hides."""
    from integrations import plex
    ok, listing = plex.libraries()
    if not ok:
        raise HTTPException(status_code=502, detail=str(listing))
    return {"libraries": listing}


class PlexLibrariesRequest(BaseModel):
    hidden: list[str] = Field(default_factory=list, max_length=50)


@app.post("/api/plex/libraries")
def plex_libraries_set(request: PlexLibrariesRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Choose which libraries the picker hides (saved in PLEX_HIDDEN_LIBRARIES)."""
    from brain import settings as settings_mod
    keys = [k for k in request.hidden if k.isdigit()]
    settings_mod.update_setting("PLEX_HIDDEN_LIBRARIES", ",".join(keys))
    return plex_libraries()


@app.get("/api/plex/shows")
def plex_shows(query: str = "", sort: str = "recent", _: None = Depends(authorize)) -> dict[str, object]:
    """Plex shows for the picker: title, year, poster, episodes watched of total."""
    from integrations import plex
    if not plex.is_configured():
        raise HTTPException(status_code=503, detail="Plex is not configured on Core")
    ok, shows = plex.library_shows(query[:100], sort if sort in ("recent", "title", "year", "unwatched") else "recent")
    if not ok:
        raise HTTPException(status_code=502, detail=str(shows))
    return {"shows": shows}


@app.get("/api/plex/shows/{show_key}/episodes")
def plex_show_episodes(show_key: str, _: None = Depends(authorize)) -> dict[str, object]:
    """A show's seasons and episodes, and which one Continue plays."""
    from integrations import plex
    ok, result = plex.show_episodes(show_key)
    if not ok:
        raise HTTPException(status_code=404 if "Not a" in str(result) or "no episodes" in str(result) else 502,
                            detail=str(result))
    return result


@app.get("/api/plex/poster")
def plex_poster(path: str, width: int = 300, _: None = Depends(authorize)) -> Response:
    """A poster through Core (the Plex token stays here). Cached a day on the device."""
    from integrations import plex
    ok, body, content_type = plex.poster(path, width)
    if not ok:
        raise HTTPException(status_code=404 if "path" in str(body) else 502, detail=str(body))
    return Response(content=body, media_type=content_type, headers={"Cache-Control": "private, max-age=86400"})


class PlexPlayRequest(BaseModel):
    rating_key: str = Field(pattern=r"^\d{1,12}$")
    from_start: bool = False


@app.post("/api/plex/play")
def plex_play(request: PlexPlayRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Start a film, episode or show on the room TV. JARVIS TV opens it (closes
    Plex first, presses Watch); a show plays its next-up episode; resumes
    unless ``from_start``."""
    from integrations import plex, tv_remote
    found, item = plex.playable(request.rating_key)
    if not found or not isinstance(item, dict):
        raise HTTPException(status_code=404, detail=str(item))
    if request.from_start and item["kind"] != "show":
        cleared, detail = plex.play_from_start(request.rating_key)
        if not cleared:
            raise HTTPException(status_code=502, detail=f"Couldn't reset the resume point: {detail}")
    sent, detail = tv_remote.play_item(request.rating_key, item["kind"], item["title"])
    if not sent:
        raise HTTPException(status_code=502, detail=f"The TV didn't take it: {detail}")
    return {"ok": True, "message": f"Starting {item['label']} on the TV."}


@app.get("/api/skills/requests")
def skill_request_list(status: str = "open", _: None = Depends(authorize)) -> dict[str, object]:
    """What Nick asked for that JARVIS can't do yet, most asked first."""
    from brain import skill_requests
    return {"requests": skill_requests.listing(status if status in (*skill_requests.STATUSES, "all") else "open")}


class SkillRequestUpdate(BaseModel):
    status: str = Field(pattern="^(open|done|declined)$")


@app.post("/api/skills/requests/{request_id}")
def skill_request_update(request_id: str, request: SkillRequestUpdate, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import skill_requests
    record = skill_requests.set_status(request_id, request.status)
    if record is None:
        raise HTTPException(status_code=404, detail="No such skill request")
    return {"request": record}


@app.post("/api/alerts/clear")
def clear_alerts(_: None = Depends(authorize)) -> dict[str, object]:
    """Empty the Needs attention list (alerts raised later still show)."""
    return {"cleared": event_bus.clear_attention()}


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


@app.get("/api/approvals")
def approvals(session_id: str, _: None = Depends(authorize)) -> dict[str, object]:
    """What this chat session is waiting on Nick for (orders, fixes, scenes),
    for the Approve/Deny buttons under JARVIS's reply. A tap sends
    "approve <code>" / "deny <code>" through /chat/stream, the same
    human-only gate as typing it, so this route only lists."""
    from brain import confirm_gate
    return {"approvals": confirm_gate.pending(session_id)}


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


@app.put("/api/todos/{todo_id}")
def todo_rename(todo_id: str, request: TodoRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain.reminders import rename_reminder
    item = rename_reminder(todo_id, request.text)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown to-do")
    status_hub.invalidate("now")
    return {"todo": {"id": item.id, "text": item.text, "due_at": item.due_at, "is_due": item.is_due()}}


@app.post("/api/todos/{todo_id}/done")
def todo_done(todo_id: str, _: None = Depends(authorize)) -> Response:
    from brain.reminders import remove_reminder
    if not remove_reminder(todo_id):
        raise HTTPException(status_code=404, detail="Unknown to-do")
    status_hub.invalidate("now")
    return Response(status_code=204)


# --- Shopping list (saved items with photos; brain/shopping.py) ------------


class ShoppingItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=300)
    store: str = Field(default="", max_length=60)
    quantity: int = Field(default=1, ge=1, le=99)
    on_list: bool = True


class ShoppingListRequest(BaseModel):
    quantity: int | None = Field(default=None, ge=1, le=99)


def _shopping_item(item: dict[str, object] | None) -> dict[str, object]:
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown shopping item")
    return {"item": item}


@app.get("/api/shopping")
def shopping_overview(_: None = Depends(authorize)) -> dict[str, object]:
    """The list (what to buy) and the catalog (everything ever saved)."""
    from brain import shopping
    return {"list": shopping.current_list(), "catalog": shopping.catalog()}


@app.get("/api/shopping/suggest")
def shopping_suggest(q: str = "", _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return {"items": shopping.suggest(q[:120])}


@app.post("/api/shopping/items")
def shopping_create(request: ShoppingItemRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    try:
        return {"item": shopping.create_item(request.name, request.note, request.store,
                                             request.on_list, request.quantity)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.put("/api/shopping/items/{item_id}")
def shopping_update(item_id: int, request: ShoppingItemRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    try:
        return _shopping_item(shopping.update_item(item_id, request.model_dump(exclude={"on_list"})))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/shopping/items/{item_id}")
def shopping_delete(item_id: int, _: None = Depends(authorize)) -> Response:
    from brain import shopping
    if not shopping.delete_item(item_id):
        raise HTTPException(status_code=404, detail="Unknown shopping item")
    return Response(status_code=204)


@app.post("/api/shopping/items/{item_id}/list")
def shopping_add_to_list(item_id: int, request: ShoppingListRequest | None = None,
                         _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return _shopping_item(shopping.add_to_list(item_id, request.quantity if request else None))


@app.delete("/api/shopping/items/{item_id}/list")
def shopping_remove_from_list(item_id: int, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return _shopping_item(shopping.remove_from_list(item_id))


@app.post("/api/shopping/items/{item_id}/bought")
def shopping_bought(item_id: int, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return _shopping_item(shopping.mark_bought(item_id))


@app.delete("/api/shopping/items/{item_id}/bought")
def shopping_undo_bought(item_id: int, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return _shopping_item(shopping.undo_bought(item_id))


@app.put("/api/shopping/items/{item_id}/photo")
async def shopping_put_photo(item_id: int, request: Request, _: None = Depends(authorize)) -> dict[str, object]:
    """A product photo as a raw JPEG body (the app shrinks it first), or a PNG
    cut-out when the app removed its background."""
    from brain import shopping
    data = await request.body()
    try:
        return _shopping_item(shopping.set_photo(item_id, data))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/shopping/items/{item_id}/photo")
def shopping_clear_photo(item_id: int, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import shopping
    return _shopping_item(shopping.clear_photo(item_id))


@app.get("/api/shopping/items/{item_id}/photo")
def shopping_get_photo(item_id: int, _: None = Depends(authorize)) -> Response:
    from brain import shopping
    path = shopping.photo_path(item_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No photo")
    # The app adds ?v=<photo_version>, so a replaced photo is a new URL.
    return Response(content=path.read_bytes(), media_type=shopping.photo_media_type(path),
                    headers={"Cache-Control": "private, max-age=604800"})


# --- Donna (private counsellor; brain/donna.py) ------------------------------


class DonnaMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8_000)


class DonnaImportRequest(BaseModel):
    text: str = Field(min_length=50, max_length=2_000_000)


def _donna_state() -> dict[str, object]:
    from brain import donna
    return {"session": donna.current_session(), "checkins": donna.pending_checkins(),
            "has_case_file": bool(donna.case_file())}


@app.get("/api/donna")
def donna_state(_: None = Depends(authorize)) -> dict[str, object]:
    """The session in progress (if any) and pending check-ins."""
    from brain import donna
    donna.close_stale()  # writes any idle session's note first
    return _donna_state()


@app.post("/api/donna/message")
def donna_message(request: DonnaMessageRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import donna
    try:
        result = donna.reply(request.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {**result, "checkins": donna.pending_checkins()}


@app.post("/api/donna/end")
def donna_end(_: None = Depends(authorize)) -> dict[str, object]:
    """End the session now: Donna writes her note."""
    from brain import donna
    return {"ended": donna.end_session()}


@app.post("/api/donna/import")
def donna_import(request: DonnaImportRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """A pasted past conversation becomes (or updates) her case file."""
    from brain import donna
    try:
        return {"case_file": donna.import_history(request.text)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/donna/memory")
def donna_memory(_: None = Depends(authorize)) -> dict[str, object]:
    """Everything she keeps, for the "What Donna knows" page."""
    from brain import donna
    return {"case_file": donna.case_file(), "notes": donna.all_notes(),
            "followups": donna.open_followups(), "moods": donna.moods(30)}


@app.delete("/api/donna/memory")
def donna_forget(_: None = Depends(authorize)) -> Response:
    from brain import donna
    donna.forget_everything()
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


@app.get("/api/agents")
def agents(_: None = _status_read) -> dict[str, object]:
    """JARVIS and the agents that work for him, for the companion's Agents map."""
    from brain import agent_roster
    return agent_roster.roster()


@app.get("/api/agents/usage")
def agents_usage(days: float = 7, _: None = _status_read) -> dict[str, object]:
    """Estimated model spend per agent over the last ``days`` (0 = all time)."""
    from brain import agent_roster
    return agent_roster.usage_report(days if days > 0 else None)


@app.post("/api/schwab/login")
def schwab_login(_: None = Depends(authorize)) -> dict[str, object]:
    """Schwab's login page for the app's web view, and the callback address
    to catch (the app hands that redirect to /api/schwab/finish)."""
    from integrations import schwab
    try:
        if schwab.borrowing():
            # The login lives on the Schwab hub: the app opens its page in Safari.
            return {"url": schwab.login_url(), "callback": "", "external": True}
        return {"url": schwab.login_url(), "callback": os.getenv("SCHWAB_CALLBACK_URL", "").strip(),
                "external": False}
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


# --- Room camera (brain/camera.py) --------------------------------------------
# The iPad is the lens, Nick's phone the only viewer. The lens runs only while
# the phone heartbeats /view and there's no kill lockout. Frames live in
# memory, never on disk. "Jarvis, kill the camera" and /kill lock it off.


@app.post("/api/camera/view")
def camera_view(_: None = Depends(authorize)) -> dict[str, object]:
    """The phone, while the live view is open, every couple of seconds: keep
    the lens on. Refused silently during a kill (``wanted`` stays false)."""
    from brain import camera
    camera.view_heartbeat()
    return camera.status()


@app.get("/api/camera/status")
def camera_status(_: None = Depends(authorize)) -> dict[str, object]:
    """The iPad polls this to decide whether to capture (and show the green
    orb); the phone reads the kill countdown from it."""
    from brain import camera
    return camera.status()


@app.post("/api/camera/leave")
def camera_leave(_: None = Depends(authorize)) -> dict[str, object]:
    """The phone closed the live view: stop the lens (and the iPad's green
    orb) now instead of waiting for the heartbeat to lapse."""
    from brain import camera
    camera.leave()
    return camera.status()


class DeviceBatteryRequest(BaseModel):
    device: str = Field(default="iPad", min_length=1, max_length=40)
    level: float = Field(ge=0, le=1)
    charging: bool = False


IPAD_LOW_BATTERY = 0.20


@app.post("/api/device/battery")
def device_battery(payload: DeviceBatteryRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """The wall iPad reports its battery when it changes. Below 20% and not
    on the charger, JARVIS warns the phone (at most every few hours)."""
    from brain import alert_checks
    alert_checks.battery_reports[payload.device] = (payload.level, payload.charging)
    low = payload.level < IPAD_LOW_BATTERY and not payload.charging
    if low:
        pct = round(payload.level * 100)
        runtime().raise_alert("device_battery", f"{payload.device} battery low",
                              f"The {payload.device} is at {pct}% and not charging.",
                              source="device-battery", dedupe_key=f"battery:{payload.device}",
                              context={"device": payload.device},
                              cooldown_s=4 * 3600)
    return {"ok": True, "low": low}


@app.post("/api/camera/kill")
def camera_kill(_: None = Depends(authorize)) -> dict[str, object]:
    """Lock the lens off for five minutes, from anywhere."""
    from brain import camera
    camera.kill()
    return camera.status()


def _camera_lens(raw: str | None) -> str:
    from brain import camera
    name = camera.lens_name(raw)
    if name is None:
        raise HTTPException(status_code=422, detail="bad lens name")
    return name


@app.post("/api/camera/frame")
async def camera_put_frame(request: Request, lens: str | None = None,
                           _: None = Depends(authorize)) -> dict[str, object]:
    """An iPad's latest JPEG (raw body), under its lens name (``?lens=bed``
    for the nightstand mini; none = ``door``). Refused (409) during a kill."""
    from brain import camera
    name = _camera_lens(lens)
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty frame")
    if not camera.put_frame(data, lens=name):
        raise HTTPException(status_code=409, detail="camera is off")
    return {"ok": True}


@app.get("/api/camera/frame")
def camera_get_frame(lens: str | None = None, _: None = Depends(authorize)) -> Response:
    """The phone pulls one angle's newest frame (``?lens=door`` / ``bed``;
    none = the door's, else any); 404 when that lens isn't sending."""
    from brain import camera
    data = camera.get_frame(lens=_camera_lens(lens) if lens else None)
    if data is None:
        raise HTTPException(status_code=404, detail="no frame")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/tv/power")
def tv_power_state(_: None = Depends(authorize)) -> dict[str, object]:
    """Whether the room TV is on (the iPad's power square). Reads only the
    TV's remote entity in Home Assistant; ``on`` is false when it's off or
    unreachable."""
    from brain import wake_alarm
    try:
        return {"on": wake_alarm.tv_is_on()}
    except Exception:  # noqa: BLE001 - HA down: show it as off
        return {"on": False}


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


class WakeAwakeRequest(BaseModel):
    # False: "I'm awake, leave the lights": the alarm stops, the room stays as it is.
    lights: bool = True


@app.post("/api/wake/ack")
def wake_ack(_: None = Depends(require_scope("wake:write"))) -> dict[str, object]:
    """The TV took the alarm (its music is starting). Without this within
    WAKE_TV_ACK_S, Core resends the alarm, then rings the room speaker."""
    from brain import wake_alarm
    return {"ok": wake_alarm.tv_acknowledged()}


@app.post("/api/wake/awake")
def wake_awake(payload: WakeAwakeRequest | None = None,
               _: None = Depends(require_scope("wake:write"))) -> dict[str, object]:
    """I'm up (phone, iPad or TV): the TV's wake screen closes, its music
    stops, and the good-morning lights come on (``wake_alarm.good_morning``).
    ``{"lights": false}`` stops the alarm and touches nothing else."""
    from brain import wake_alarm
    lights = payload.lights if payload is not None else True
    wake_alarm.im_up(lights=lights)
    return {"ok": True, "lights": lights}


# --- What JARVIS knows: the Obsidian vault, read-only (brain/knowledge_vault.py)


@app.get("/api/knowledge")
def knowledge_list(_: None = Depends(authorize)) -> dict[str, object]:
    """The notes JARVIS may read (profile memory, journal, captured notes)."""
    from brain import knowledge_vault
    return {"notes": knowledge_vault.list_notes()}


@app.get("/api/knowledge/note")
def knowledge_note(path: str, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import knowledge_vault
    note = knowledge_vault.read_note(path)
    if note is None:
        raise HTTPException(status_code=404, detail="No such note.")
    return note


class KnowledgeImport(BaseModel):
    title: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=200_000)


@app.post("/api/knowledge/import")
def knowledge_import(request: KnowledgeImport, _: None = Depends(authorize)) -> dict[str, object]:
    """Paste-in memory: saves the text as a note under Jarvis Memory/Imported
    (knowledge_vault.import_note), searched section by section each turn, and
    rebuilds the always-injected core profile from it in the background."""
    from brain import knowledge_vault
    try:
        path = knowledge_vault.import_note(request.title, request.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from brain import memory_extractor
    memory_extractor.refresh_core_profile_async()  # boil it down into what he always knows
    return {"path": path}


@app.get("/api/knowledge/graph")
def knowledge_graph(_: None = Depends(authorize)) -> dict[str, object]:
    """The vault as a map: folders, notes, Profile topics and [[wikilinks]]
    (titles only). The iPad's Memory map draws it."""
    from brain import knowledge_vault
    return knowledge_vault.graph()


# --- Shared colour theme (phone and TV follow one pick; brain/theme.py) -----


class ThemeRequest(BaseModel):
    # Checked against brain/theme.THEMES (one list, not a second copy here).
    theme: str | None = Field(default=None, max_length=40)
    experiments: dict[str, bool] | None = None


@app.get("/api/theme")
def theme_get(_: None = _status_read) -> dict[str, object]:
    from brain import theme
    return theme.current()


@app.put("/api/theme")
def theme_put(request: ThemeRequest, _: None = Depends(require_scope("settings:write"))) -> dict[str, object]:
    """Pick the shared theme and/or flip Experimenting switches."""
    from brain import theme
    if request.theme is None and not request.experiments:
        raise HTTPException(status_code=422, detail="Send a theme or experiments.")
    try:
        result = theme.choose(request.theme) if request.theme is not None else theme.current()
        if request.experiments:
            result = theme.set_experiments(request.experiments)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


# --- Maintenance (recurring chores with a countdown and a log) -------------


class MaintenanceTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    schedule_type: str = Field(pattern="^(interval|fixed_calendar|log_only)$")
    # interval: how many units; fixed_calendar: months between dates (1/2/3/4/6/12).
    interval_value: int | None = Field(default=None, ge=1, le=3650)
    interval_unit: str | None = Field(default=None, pattern="^(days|months|years)$")
    fixed_month: int | None = Field(default=None, ge=1, le=12)
    fixed_day: int | None = Field(default=None, ge=1, le=31)
    # fixed_calendar only: skip dates outside these months (may wrap the year).
    season_start: int | None = Field(default=None, ge=1, le=12)
    season_end: int | None = Field(default=None, ge=1, le=12)
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


class ProtocolRequest(BaseModel):
    action: str = Field(default="engage", pattern="^(engage|stand_down)$")


@app.post("/api/protocols/barn-door")
def barn_door(request: ProtocolRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Barn Door Protocol from the apps (three quick taps on the reactor)."""
    from brain import protocols
    message = protocols.stand_down() if request.action == "stand_down" else protocols.engage()
    return {"message": message}


class Temp4kRequest(BaseModel):
    tmdb_id: int
    hours: float = Field(default=24, ge=1, le=336)


@app.get("/api/media/temp4k")
def media_temp4k(_: None = Depends(authorize)) -> dict[str, object]:
    """Temporary 4K films (integrations/temp_4k.py) for the Media countdown."""
    from integrations import temp_4k
    if not temp_4k.configured():
        return {"configured": False, "films": [], "keep_hours": temp_4k.keep_hours()}
    return {"configured": True, "films": temp_4k.listing(), "keep_hours": temp_4k.keep_hours()}


@app.post("/api/media/temp4k/extend")
def media_temp4k_extend(request: Temp4kRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from integrations import temp_4k
    try:
        return {"error": "", "film": temp_4k.extend(request.tmdb_id, request.hours)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="not a temporary 4K film") from exc


@app.post("/api/media/temp4k/remove")
def media_temp4k_remove(request: Temp4kRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from integrations import temp_4k
    try:
        done, detail = temp_4k.remove_id(request.tmdb_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="not a temporary 4K film") from exc
    return {"error": "" if done else detail}


@app.get("/api/media/profiles")
def media_profiles(kind: str, _: None = Depends(authorize)) -> dict[str, object]:
    """Quality profile names for a request's picker, and the default one."""
    from integrations import servarr_common
    kind = "tv" if kind == "show" else kind
    if kind not in ("movie", "tv"):
        raise HTTPException(status_code=422, detail="kind must be movie or tv")
    service = "radarr" if kind == "movie" else "sonarr"
    ok, profiles = servarr_common.get_quality_profiles(service)
    if not ok:
        return {"profiles": [], "default": servarr_common.default_quality(service), "error": str(profiles)}
    return {"profiles": [p.get("name", "") for p in profiles if p.get("name")],
            "default": servarr_common.default_quality(service), "error": ""}


class AnnounceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=400)


@app.post("/api/voice/announce")
def voice_announce(request: AnnounceRequest, _: None = Depends(require_scope("chat"))) -> dict[str, object]:
    """Broadcast: a chime, then ``text`` spoken on the room speaker at the
    next pause (speech/room_voice.py). 409 when the room voice is off."""
    from speech import room_voice
    if not room_voice.announce(request.text):
        raise HTTPException(status_code=409, detail="The room voice isn't running")
    return {"queued": True}


@app.post("/api/voice/hush")
def voice_hush(_: None = Depends(require_scope("chat"))) -> dict[str, object]:
    """A tap on the iPad: end the room voice's current exchange (stop talking,
    stop listening, drop a reply still being written) and wait for "Hey
    JARVIS" again. ``hushed`` is False when it was idle already."""
    from speech import room_voice
    return {"hushed": room_voice.hush()}


class VoiceEnrollRequest(BaseModel):
    action: str = "start"  # start | cancel
    mic: str = "desk"      # "desk" or a satellite's name (bed, door)


@app.post("/api/voice/enroll")
def voice_enroll(body: VoiceEnrollRequest, _: None = Depends(require_scope("chat"))) -> dict[str, object]:
    """Teach JARVIS my voice from the iPad: ``start`` arms voice ID enrolment
    for ``mic`` and the room voice listens there for the lines (no wake word);
    the iPads show each line from ``/api/voice/stream``'s ``enroll``.
    ``cancel`` stops it and leaves the old print alone."""
    from brain import speaker_id
    from speech import room_voice, satellites
    if body.action == "cancel":
        speaker_id.cancel_enrollment()
        room_voice.hush()
        return {"ok": True, "enroll": None}
    if body.action != "start":
        raise HTTPException(status_code=400, detail="action must be start or cancel")
    mic = "desk" if body.mic == "desk" else satellites.clean_name(body.mic)
    if mic is None:
        raise HTTPException(status_code=400, detail="unknown mic")
    if not speaker_id.available():
        return {"ok": False, "detail": "The voice model isn't installed on Core."}
    if mic != "desk" and satellites.get(mic) is None:
        return {"ok": False, "detail": "That iPad isn't connected as a mic. Turn on JARVIS listens here."}
    if not room_voice.enroll_from(mic):
        return {"ok": False, "detail": "The room voice isn't running on Core."}
    return {"ok": True, "enroll": speaker_id.enrollment_status()}


@app.get("/api/voice/state")
def voice_state(_: None = _status_read) -> dict[str, object]:
    """What the room voice is doing (brain/voice_presence.py)."""
    return voice_presence.current()


@app.get("/api/voice/stream")
async def voice_stream(request: Request, _: None = _status_read) -> StreamingResponse:
    """Server-sent events of the room voice's state: one ``voice`` event per
    change (phase, live transcript, reply), keep-alive every 15 s. The iPad
    shows JARVIS listening and the live transcript from this."""
    async def events():
        state = voice_presence.current()
        yield f"event: voice\ndata: {json.dumps(state)}\n\n"
        quiet_since = time.monotonic()
        # Polls the in-memory state (cheap) rather than parking a worker
        # thread per screen; 80 ms keeps captions live.
        while not await request.is_disconnected():
            await asyncio.sleep(0.08)
            latest = voice_presence.current()
            if latest["seq"] != state["seq"]:
                state, quiet_since = latest, time.monotonic()
                _pushed(request, "voice")
                yield f"event: voice\ndata: {json.dumps(state)}\n\n"
            elif time.monotonic() - quiet_since > _SSE_KEEPALIVE_S:
                quiet_since = time.monotonic()
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/home/stream")
async def home_stream(request: Request, _: None = _status_read) -> StreamingResponse:
    """Server-sent ``home`` events: one whenever a dashboard entity or a
    scene changes in Home Assistant (app/status_hub.py ``home_changed``), so
    the room screens refetch at once instead of on their next poll. A burst
    (a scene flipping eight lights) arrives as a few events, not eight.
    Each event also carries the shared ``theme``: the TV follows it here, so
    Barn Door reaches it at once instead of on its 5 s theme poll. ``look``
    changes with any theme pick or Experimenting switch, so the phone and
    iPad refetch /api/theme at once (their 10 s poll pauses while this
    stream is up). ``camera`` is whether the iPad's lens is wanted
    (brain/camera.py): the iPad follows it here instead of asking
    /api/camera/status every 1.5 s, and starts or stops within a beat.
    ``lightsync`` moves whenever light sync's state does (running, the film,
    intensity, a build's progress): the iPad's movie screen refetches
    /api/lightsync then."""
    await asyncio.to_thread(runtime)  # the HA live feed runs on the runtime
    from brain import camera, theme

    def pulse() -> dict[str, object]:
        from brain import light_sync
        shared = theme.current()
        return {"seq": status_hub.home_seq(), "theme": shared["theme"], "look": shared["revision"],
                "camera": camera.wanted(), "lightsync": light_sync.controller.revision()}

    async def events():
        state = pulse()
        yield f"event: home\ndata: {json.dumps(state)}\n\n"
        quiet_since = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(0.15)
            latest = pulse()
            if latest != state:
                state, quiet_since = latest, time.monotonic()
                _pushed(request, "home")
                yield f"event: home\ndata: {json.dumps(state)}\n\n"
            elif time.monotonic() - quiet_since > _SSE_KEEPALIVE_S:
                quiet_since = time.monotonic()
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/traffic/stream")
async def traffic_stream(request: Request, _: None = _status_read) -> StreamingResponse:
    """Server-sent ``traffic`` events: every signal between Core, the NAS,
    the TV, the phone, the iPad and the internet as it happens (batched every
    100 ms; brain/traffic.py). Starts from now, no replay."""
    async def events():
        last = traffic.seq()
        quiet_since = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(0.1)
            batch = traffic.since(last)
            if batch:
                last, quiet_since = batch[-1]["seq"], time.monotonic()
                yield f"event: traffic\ndata: {json.dumps(batch)}\n\n"
            elif time.monotonic() - quiet_since > _SSE_KEEPALIVE_S:
                quiet_since = time.monotonic()
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/clients/events")
async def client_events(
    request: Request,
    since: int = 0,
    only: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    client: str | None = Depends(require_scope("events:read")),
) -> StreamingResponse:
    """Server-sent events of every bus alert (brain/event_bus.py).

    Replays alerts newer than ``Last-Event-ID`` (or ``?since=``) so a client
    that reconnects misses nothing still in the 200-alert history, then
    streams live ones with a keep-alive comment every 15 s.

    Answer cards (``brain/answer_cards.py``) ride the same stream as
    ``event: card`` frames with no ``id:`` line, so they never move a
    client's Last-Event-ID; cards still on screen are replayed on connect.
    ``?only=cards`` streams just the cards (the companion app)."""
    await asyncio.to_thread(runtime)  # monitors are what publish alerts
    try:
        start = int(last_event_id) if last_event_id else since
    except ValueError:
        start = since
    alerts_wanted = only != "cards"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    name = f"sse-{uuid.uuid4()}"

    def offer(item: object) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass  # slow client: it can catch up from history on reconnect

    def on_event(item: object) -> None:
        try:
            loop.call_soon_threadsafe(offer, item)
        except RuntimeError:
            pass  # loop closed: the stream is gone

    # Subscribe before replaying so nothing published in between is lost;
    # ids dedupe the overlap.
    if alerts_wanted:
        event_bus.subscribe(name, on_event)
    event_bus.subscribe_cards(name, on_event)

    def frame(alert: event_bus.Alert) -> str:
        # A private alert (money) still moves the id, but only the companion's
        # own token sees what it says; client tokens (the TV) get a comment.
        if alert.private and client is not None:
            return f"id: {alert.id}\n: private\n\n"
        return f"id: {alert.id}\nevent: alert\ndata: {json.dumps(alert.to_dict())}\n\n"

    def card_frame(card: event_bus.Card) -> str:
        return f"event: card\ndata: {json.dumps(card.to_dict())}\n\n"

    async def events():
        last = start
        last_card = 0
        try:
            for alert in event_bus.since(start) if alerts_wanted else []:
                last = alert.id
                yield frame(alert)
            for card in event_bus.live_cards():
                last_card = card.id
                yield card_frame(card)
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if isinstance(item, event_bus.Card):
                    if item.id > last_card:
                        last_card = item.id
                        _pushed(request, "card")
                        yield card_frame(item)
                elif item.id > last:
                    last = item.id
                    _pushed(request, "alert")
                    yield frame(item)
        finally:
            event_bus.unsubscribe(name)
            event_bus.unsubscribe_cards(name)

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


class LightRequest(BaseModel):
    entity_id: str = Field(min_length=7, max_length=200)
    brightness_pct: int | None = Field(default=None, ge=1, le=100)
    rgb: list[int] | None = Field(default=None, min_length=3, max_length=3)
    kelvin: int | None = Field(default=None, ge=1500, le=9000)
    rainbow: bool = False


@app.post("/api/home/light")
def home_light(request: LightRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Brightness / colour for an allowlisted light (the iPad's light sheet)."""
    try:
        return status_hub.set_light(request.entity_id, request.brightness_pct, request.rgb, request.kelvin,
                                    request.rainbow)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FanRequest(BaseModel):
    entity_id: str = Field(min_length=5, max_length=200)
    percentage: int | None = Field(default=None, ge=0, le=100)
    preset_mode: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9 _-]+$")


@app.post("/api/home/fan")
def home_fan(request: FanRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Speed (0 = off) and/or mode for an allowlisted fan (the iPad's fan tile and sheet)."""
    try:
        return status_hub.set_fan(request.entity_id, request.percentage, request.preset_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RoomVolumeRequest(BaseModel):
    level: int = Field(ge=0, le=10)


def _room_volume_state() -> dict[str, object]:
    from speech import room_volume
    return {"level": room_volume.level(), "night": room_volume.quiet_now(),
            "night_level": room_volume.night_level()}


@app.get("/api/host/battery")
def host_battery(_: None = Depends(authorize)) -> dict[str, object]:
    """Battery of the laptop Core runs on (``app/host_battery.py``), for the
    companion's Settings › Laptop, which polls it while open."""
    from app import host_battery as battery
    return battery.status()


@app.get("/api/room/volume")
def room_volume_get(_: None = Depends(authorize)) -> dict[str, object]:
    """The room speaker's level (0-10, ``speech/room_volume.py``); ``night``
    is true inside ROOM_QUIET_HOURS, where the slider sets the night level."""
    return _room_volume_state()


@app.post("/api/room/volume")
def room_volume_set(request: RoomVolumeRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """The iPad's volume slider: this time of day's level, applied now, with
    a soft chime so Nick hears how loud it is."""
    from speech import room_sounds, room_volume
    room_volume.set_level(request.level)
    room_sounds.play("chime")
    return _room_volume_state()


@app.post("/api/room/volume/night")
def room_volume_night_set(request: RoomVolumeRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Settings' Night volume stepper: the quiet-hours level, editable at any
    hour. Applied (with a chime) only when it is night right now."""
    from speech import room_sounds, room_volume
    room_volume.set_night_level(request.level)
    if room_volume.quiet_now():
        room_sounds.play("chime")
    return _room_volume_state()


@app.get("/api/home/scenes")
def home_scenes(_: None = Depends(authorize)) -> dict[str, object]:
    """Saved Home Assistant scenes for the app's scene picker."""
    return status_hub.home_scenes()


class SceneRequest(BaseModel):
    entity_id: str = Field(min_length=7, max_length=200, pattern=r"^scene\.[a-z0-9_]+$")
    toggle: bool = False  # a tile press: the scene the room is in, untouched, leaves it


@app.post("/api/home/scenes/activate")
def home_scene_activate(request: SceneRequest, _: None = Depends(authorize)) -> dict[str, object]:
    try:
        return status_hub.activate_scene(request.entity_id, toggle=request.toggle)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/home/scenes/exit-movie")
def home_scene_exit_movie(_: None = Depends(authorize)) -> dict[str, object]:
    """The movie remote's Exit Movie: the room back to how it was before the
    Movie scene (brain/movie_exit.py), light sync let go."""
    from brain import movie_exit
    return movie_exit.exit_movie()


# --- Light sync (brain/light_sync.py, brain/light_tracks.py) ----------------


def _lightsync_state() -> dict[str, object]:
    from brain import light_sync
    sync = light_sync.controller
    if not sync.armed() and not sync.clock.tv_fresh() and sync.now() - sync.clock.plex_polled_at > 3:
        sync.poll_plex()  # the monitor isn't asking Plex: name the film for the menu now
    return sync.status()


@app.get("/api/lightsync")
def lightsync_get(_: None = Depends(authorize)) -> dict[str, object]:
    """The iPad's Light sync menu: auto, intensity, running, the film on the
    TV and whether it has a track, the build in progress, every track."""
    return _lightsync_state()


class LightSyncRequest(BaseModel):
    action: str | None = Field(default=None, pattern="^(start|stop)$")
    intensity: str | None = Field(default=None, pattern="^(subtle|medium|intense)$")
    auto: bool | None = None
    nudge_ms: int | None = Field(default=None, ge=-2000, le=2000)
    scope: str | None = Field(default=None, pattern="^(film|global|format)$")
    reset_offset: bool | None = None
    calibrate: bool | None = None
    lamp_nudge_ms: int | None = Field(default=None, ge=-1500, le=1500)
    light_ip: str | None = Field(default=None, max_length=64)
    light_nudge_ms: int | None = Field(default=None, ge=-2000, le=2000)


@app.post("/api/lightsync")
def lightsync_set(request: LightSyncRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Start / stop, intensity, the auto-in-movie-mode switch, timing nudges
    (``nudge_ms``, positive = lights earlier: the film syncing, or global
    while calibrating or with ``scope``), ``reset_offset`` and calibration
    (``calibrate: true`` plays the test clip, ``false`` is Done). ``message``
    is JARVIS's answer (why it didn't, when it didn't)."""
    from brain import light_sync
    sync = light_sync.controller
    if all(v is None for v in (request.action, request.intensity, request.auto, request.nudge_ms,
                               request.reset_offset, request.calibrate, request.lamp_nudge_ms, request.light_nudge_ms)):
        raise HTTPException(status_code=422, detail="Send an action, intensity, auto, nudge_ms, reset_offset or calibrate.")
    messages = []
    if request.light_nudge_ms is not None:
        try:
            sync.nudge_light(request.light_ip or "", request.light_nudge_ms)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.intensity is not None:
        sync.set_intensity(request.intensity)
    if request.auto is not None:
        sync.set_auto(request.auto)
    if request.calibrate is True:
        messages.append(sync.calibrate()[1])
    elif request.calibrate is False:
        messages.append(sync.end_calibration()[1])
    if request.reset_offset:
        messages.append(sync.reset_offset(request.scope)[1])
    if request.nudge_ms:
        messages.append(sync.nudge(request.nudge_ms, request.scope)[1])
    if request.lamp_nudge_ms:
        messages.append(sync.nudge_lamp(request.lamp_nudge_ms)[1])
    if request.action == "start":
        messages.append(sync.start()[1])
    elif request.action == "stop":
        messages.append(sync.stop()[1])
    return {**_lightsync_state(), "message": " ".join(m for m in messages if m)}


class LightSyncBuildRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    rating_key: str | None = Field(default=None, pattern=r"^\d{1,12}$")
    rebuild: bool = False
    cancel: str | None = Field(default=None, pattern=r"^(all|\d{1,12}(--[0-9a-f]{16})?)$")
    # The copy playing on the TV now (its own Plex Part), whatever rating_key says.
    playing: bool = False


@app.post("/api/lightsync/build")
def lightsync_build(request: LightSyncBuildRequest, _: None = Depends(authorize)) -> dict[str, object]:
    """Make one film's light track in the background, or queue it behind the
    one building (an alert says as each is ready). ``cancel``: a ratingKey
    (building or queued) or ``all``."""
    from brain import light_tracks
    from integrations import plex
    if request.cancel:
        cancelled = (light_tracks.cancel(everything=True) if request.cancel == "all"
                     else light_tracks.cancel(request.cancel))
        message = ("Cancelled " + ", ".join(cancelled) + ", sir.") if cancelled else "Nothing to cancel, sir."
        return {**_lightsync_state(), "started": False, "message": message}
    if request.playing:
        from brain import light_sync
        started, message = light_sync.controller.build_playing()
        return {**_lightsync_state(), "started": started, "message": message}
    if not (request.title or request.rating_key):
        raise HTTPException(status_code=422, detail="Send a title, rating_key, playing or cancel.")
    ok, film = plex.movie(request.rating_key) if request.rating_key else plex.find_movie(request.title or "")
    if not ok:
        return {**_lightsync_state(), "started": False, "message": str(film)}
    started, message = light_tracks.start_build(film, rebuild=request.rebuild)
    return {**_lightsync_state(), "started": started, "message": message}


class TVClockExchange(BaseModel):
    tv_send_ms: float = Field(ge=0, allow_inf_nan=False)
    core_receive_ms: float = Field(ge=0, allow_inf_nan=False)
    core_send_ms: float = Field(ge=0, allow_inf_nan=False)
    tv_receive_ms: float = Field(ge=0, allow_inf_nan=False)


@app.get("/api/tv/clock")
def tv_clock(_: None = Depends(require_scope("playback:write"))) -> dict[str, float]:
    """Monotonic clock exchange, never wall time. TV echoes all four stamps."""
    received = time.monotonic() * 1000
    return {"core_receive_ms": received, "core_send_ms": time.monotonic() * 1000}


class TVPlaybackReport(BaseModel):
    package: str = Field(default="", max_length=120)
    state: str = Field(pattern="^(playing|paused|buffering|stopped)$")
    position_ms: int = Field(ge=0, le=48 * 3600 * 1000)
    speed: float = Field(default=1.0, ge=0, le=4)
    title: str = Field(default="", max_length=300)
    rating_key: str | None = Field(default=None, max_length=40)
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    sample_elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    exchange: TVClockExchange | None = None
    stream_id: str | None = Field(default=None, max_length=40)
    report_id: int | None = Field(default=None, ge=0)


@app.post("/api/tv/playback")
def tv_playback(report: TVPlaybackReport, _: None = Depends(require_scope("playback:write"))) -> dict[str, object]:
    """The room TV's Plex position (JARVIS TV, every second while playing and on
    every change), advanced to its elapsedRealtime timestamp and clock-mapped. Synchronized reports carry the previous four-stamp exchange.
    Only Plex counts: another app's media session is ignored."""
    received = time.monotonic() * 1000
    if report.package and report.package != "com.plexapp.android":
        return {"ok": True, "used": False}
    from brain import light_sync
    key = report.rating_key if report.rating_key and report.rating_key.isdigit() else ""
    light_sync.controller.tv_report(state=report.state, position_ms=report.position_ms, speed=report.speed,
                                    title=report.title, rating_key=key, elapsed_ms=report.elapsed_ms,
                                    exchange=report.exchange.model_dump() if report.exchange else None,
                                    stream_id=report.stream_id, report_id=report.report_id, sample_elapsed_ms=report.sample_elapsed_ms)
    return {"ok": True, "used": True, "core_receive_ms": received, "core_send_ms": time.monotonic() * 1000}


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


# --- Money (brain/finance_advisor.py, REMOTE_AND_MONEY_PLAN.md Part 2) ---------

class ApplePayment(BaseModel):
    """What the iPhone's "Transaction" Shortcut automation sends on every Apple Pay payment."""
    merchant: str = Field(min_length=1, max_length=120)
    amount: str | float = Field(description='"$12.50", "12.50" or 12.5')
    card: str = Field(default="", max_length=60)
    id: str | None = Field(default=None, max_length=80)


def _money_amount(raw: str | float) -> float:
    import re
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = re.sub(r"[^0-9.,\-]", "", raw).replace(",", "")
        try:
            value = float(text)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Not an amount: {raw!r}") from None
    if not 0 < abs(value) < 100_000:
        raise HTTPException(status_code=422, detail="Amount out of range")
    return round(abs(value), 2)


@app.post("/api/money/transaction")
def money_transaction(payment: ApplePayment, _: str | None = Depends(require_scope("money:write"))) -> dict[str, object]:
    """One Apple Pay payment from the Shortcut, into Mooney's ledger (Apple
    Card can't be read through Plaid); the advisor checks it at once."""
    from brain import finance_advisor
    from integrations import mooney
    if not mooney.configured():
        raise HTTPException(status_code=503, detail="Mooney isn't linked (MOONEY_URL / MOONEY_JARVIS_TOKEN)")
    try:
        entry = finance_advisor.record_apple_pay(merchant=payment.merchant, amount=_money_amount(payment.amount),
                                                 card=payment.card or "Apple Pay", entry_id=payment.id or "")
    except mooney.MooneyError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if _runtime is not None:
        _runtime.wake_finance()
    return {"ok": True, "id": entry}


class MoneySettingsRequest(BaseModel):
    weekly_budget: float | None = Field(default=None, ge=0, le=100_000)
    big_purchase: float | None = Field(default=None, ge=0, le=100_000)
    quiet_start: str | None = Field(default=None, max_length=5)
    quiet_end: str | None = Field(default=None, max_length=5)
    dry_run: bool | None = None


@app.get("/api/money")
def money_status(_: None = Depends(authorize)) -> dict[str, object]:
    """Settings > Money in the companion: the advisor's settings and its recent notices."""
    from brain import finance_advisor
    return finance_advisor.status()


@app.put("/api/money/settings")
def money_settings(request: MoneySettingsRequest, _: None = Depends(authorize)) -> dict[str, object]:
    from brain import finance_advisor
    try:
        finance_advisor.update_settings(**request.model_dump(exclude_none=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return finance_advisor.status()


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
        # Behind Lightsail's Caddy (jarvis.nickolaspearson.com) the caller's
        # real address arrives in X-Forwarded-For; trust it only from there.
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("JARVIS_TRUSTED_PROXIES", "127.0.0.1").strip() or "127.0.0.1",
    )
