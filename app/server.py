"""Authenticated HTTP bridge for the JARVIS iOS companion."""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from brain.history import (
    delete_session,
    list_sessions,
    load_session_messages,
    rename_session,
    session_exists,
)
from brain.runtime import JarvisRuntime

app = FastAPI(title="JARVIS Core", version="1.0")
_runtime: JarvisRuntime | None = None


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


class ChatResponse(BaseModel):
    reply: str
    session_id: str


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.get("/health")
def health(_: None = Depends(authorize)) -> dict[str, object]:
    core = runtime()
    return {"status": "ok", "brain_online": core.brain_online, "brain": core.brain_label}


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
    if not delete_session(session_id):
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
    reply = runtime().respond_in_session(session_id, request.message.strip())
    return ChatResponse(reply=reply, session_id=session_id)


@app.post("/chat/stream")
def chat_stream(request: ChatRequest, _: None = Depends(authorize)) -> StreamingResponse:
    session_id = _resolve_session(request)

    def chunks() -> Iterator[bytes]:
        for fragment in runtime().respond_streaming_in_session(session_id, request.message.strip()):
            yield fragment.encode()

    return StreamingResponse(
        chunks(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Jarvis-Session-ID": session_id},
    )


def run_server() -> None:
    """Run the companion API using environment-configured bind settings."""
    import uvicorn

    uvicorn.run(
        "app.server:app",
        host=os.getenv("JARVIS_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("JARVIS_SERVER_PORT", "8000")),
        reload=False,
    )
