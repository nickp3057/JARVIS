"""Who's talking to whom, live: a tiny in-memory log of every signal between
Core and the rest of the house, for the companion's link map and the iPad's
neural core (each real request fires a thought).

Nodes are fixed names: ``core`` (this Optiplex), ``nas`` (HA, Plex, the arr
stack), ``tv``, ``iphone``, ``ipad``, ``mac`` and ``internet`` (Anthropic,
APNs, weather...). Signals carry a short label (a path, a service name),
never a query string or a body.

Recorded from three places: the server's HTTP middleware (clients -> Core),
the SSE streams as they push (Core -> clients), and a wrapper around
``requests`` / ``httpx`` installed once by ``install()`` (Core -> NAS or the
internet). Home Assistant's websocket pushes count as NAS -> Core
(``app/status_hub.home_changed``).

Nothing here is persisted or pushed anywhere; it's a 400-entry ring buffer.
"""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

NODES = ("core", "nas", "tv", "iphone", "ipad", "mac", "internet")

_lock = threading.Lock()
_log: deque[dict[str, Any]] = deque(maxlen=400)
_seq = 0
_installed = False

# The NAS's well-known service ports, for hosts not named in .env.
_NAS_PORTS = {8123, 32400, 7878, 8989, 8080, 9696}
_NAS_URL_VARS = ("HOME_ASSISTANT_URL", "HA_URL", "PLEX_URL", "RADARR_URL", "SONARR_URL",
                 "QBITTORRENT_URL", "PROWLARR_URL")


def record(source: str, target: str, label: str = "") -> None:
    """Log one signal. Unknown node names are kept (the map ignores them)."""
    global _seq
    if not source or not target or source == target:
        return
    with _lock:
        _seq += 1
        _log.append({"seq": _seq, "at": time.time(), "from": source, "to": target, "label": label[:60]})


def seq() -> int:
    return _seq


def since(after: int) -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _log if item["seq"] > after]


def client_node(name: str | None, device: str | None = None) -> str:
    """Which node a request came from: the companion says so in
    ``X-Jarvis-Device`` (ipad / iphone / mac); a client token's name says the
    rest (a token named for the TV is the TV)."""
    for hint in (device, name):
        text = (hint or "").strip().lower()
        if not text:
            continue
        if "ipad" in text:
            return "ipad"
        if "phone" in text:
            return "iphone"
        if "mac" in text:
            return "mac"
        if "tv" in text:
            return "tv"
        return text[:24]
    return "iphone"  # the admin token without a device header: older phone builds


def _nas_hosts() -> set[str]:
    hosts = {h.strip().lower() for h in os.getenv("TRAFFIC_NAS_HOSTS", "").split(",") if h.strip()}
    for var in _NAS_URL_VARS:
        host = urlsplit(os.getenv(var, "")).hostname
        if host:
            hosts.add(host.lower())
    return hosts


def host_node(url: str) -> tuple[str, str]:
    """(node, label) for an outbound URL."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "internet", ""
    if host in _nas_hosts():
        return "nas", _service(port, host)
    tv = os.getenv("TV_HOST", "").split(":")[0].strip().lower()
    if tv and host == tv:
        return "tv", "tv"
    try:
        address = ipaddress.ip_address(host)
        # Tailscale's 100.64/10 counts: the NAS is reached over it from away.
        private = address.is_private or address in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        private = host.endswith(".local") or host == "localhost"
    if private:
        if host in {"127.0.0.1", "localhost", "::1"}:
            return "core", "local"
        if port in _NAS_PORTS:
            return "nas", _service(port, host)
        return "nas", host  # the LAN beyond the Optiplex is, in practice, the NAS
    return "internet", host


def _service(port: int | None, host: str) -> str:
    return {8123: "home assistant", 32400: "plex", 7878: "radarr", 8989: "sonarr",
            8080: "qbittorrent", 9696: "prowlarr"}.get(port or 0, host)


def install() -> None:
    """Wrap ``requests`` and ``httpx`` so every outbound call is logged.
    Idempotent; a failure to wrap never breaks the call itself."""
    global _installed
    if _installed:
        return
    _installed = True
    try:
        import requests.sessions

        original = requests.sessions.Session.request

        def request(self, method, url, *args, **kwargs):
            _outbound(url)
            return original(self, method, url, *args, **kwargs)

        requests.sessions.Session.request = request  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001 - requests missing: nothing to tap
        pass
    try:
        import httpx

        original_send = httpx.Client.send

        def send(self, request, *args, **kwargs):
            _outbound(request.url)
            return original_send(self, request, *args, **kwargs)

        httpx.Client.send = send  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001
        pass


def _outbound(url: Any) -> None:
    try:
        node, label = host_node(str(url))
        record("core", node, label)
    except Exception:  # noqa: BLE001 - logging must never break a call
        pass
