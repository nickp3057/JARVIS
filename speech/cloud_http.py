"""One kept-alive HTTPS session for the speech clouds (ElevenLabs, OpenAI).

A bare ``requests.post`` opens a new connection every call: about 90 ms of
DNS, TCP and TLS from the Optiplex to api.elevenlabs.io (measured 2026-11-09),
paid once for the transcript and again for the spoken reply. Here both share
a pooled connection, and ``warm()`` opens it while Nick is still talking, so
neither waits for a handshake.
"""
from __future__ import annotations

import threading

import requests

from jarvis_logger import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            _session = requests.Session()
        return _session


def post(url: str, **kwargs) -> requests.Response:  # noqa: ANN003
    """``requests.post`` on the shared session. A kept-alive connection the
    far end has already closed fails at once; that is retried one time on a
    fresh connection, so reuse never costs a reply."""
    try:
        return session().post(url, **kwargs)
    except requests.ConnectionError:
        log.debug("Pooled connection to %s was stale; retrying once", url.split("/")[2])
        return session().post(url, **kwargs)


def warm(url: str) -> None:
    """Open (or keep open) the connection to ``url``'s host in the
    background. Never raises; a failure only means the next call connects
    for itself."""
    def run() -> None:
        try:
            session().head(url, timeout=3)
        except requests.RequestException:
            log.debug("Could not warm %s", url, exc_info=True)

    threading.Thread(target=run, name="speech-warm", daemon=True).start()
