"""Authenticated HTTP bridge for the JARVIS iOS companion."""

from __future__ import annotations

import asyncio
import threading
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain.history import (
    list_sessions,
    load_session_messages,
    rename_session,
    session_exists,
)
from app import status_hub
from brain.debug_reports import create_debug_report
from brain.runtime import JarvisRuntime

app = FastAPI(title="JARVIS Core", version="1.0")
_runtime: JarvisRuntime | None = None

_COMPANION_API_VERSION = 1
_COMPANION_CAPABILITIES: dict[str, object] = {
    "chat": {
        "streaming": True,
        "request_receipts": True,
        "server_history": True,
        "debug_reports": True,
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
    },
    "actions": {
        "home_on_off": True,
        "torrent_pause_resume": True,
    },
}


def runtime() -> JarvisRuntime:
    global _runtime
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
def chat(request: ChatRequest, _: None = Depends(authorize)) -> ChatResponse:
    session_id = _resolve_session(request)
    try:
        kwargs = {"request_id": request.request_id} if request.request_id else {}
        reply = runtime().respond_in_session(session_id, request.message.strip(), **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ChatResponse(reply=reply, session_id=session_id)


@app.post("/chat/stream")
def chat_stream(request: ChatRequest, _: None = Depends(authorize)) -> StreamingResponse:
    session_id = _resolve_session(request)

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
        headers={"X-Jarvis-Session-ID": session_id},
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
def status_torrents(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.torrents_status()


@app.get("/api/now")
def now_status(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.now_status()


@app.get("/api/study/brief")
def study_brief_status(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.study_brief_status()


@app.get("/api/status/media")
def status_media(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.media_status()


@app.get("/api/status/storage")
def status_storage(_: None = Depends(authorize)) -> dict[str, object]:
    return status_hub.media_storage_status()


@app.get("/api/status/services")
def status_services(_: None = Depends(authorize)) -> dict[str, object]:
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


def run_server() -> None:
    """Run the companion API using environment-configured bind settings."""
    import uvicorn

    uvicorn.run(
        "app.server:app",
        host=os.getenv("JARVIS_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("JARVIS_SERVER_PORT", "8000")),
        reload=False,
    )
