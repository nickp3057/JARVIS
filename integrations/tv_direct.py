"""Nick's room TV straight from Core: the Android TV Remote protocol (the one
the Google TV phone app and Home Assistant's Android TV Remote integration
speak), without Home Assistant in the middle.

The companion's remote used to go iPad -> Core -> Home Assistant (on the NAS)
-> TV, so a busy NAS made every button press lag (2026-11-24, a 36 GB download
pushed the NAS's load past 30). Core now keeps its own connection to the TV
open and sends keys over it; ``integrations/tv_remote.py`` tries this first and
falls back to Home Assistant when it can't.

Setup, once: ``TV_HOST`` (the TV's IP) in .env, then pair from a terminal on
the Surface (a code appears on the TV):

    .venv/bin/python -m integrations.tv_direct pair

That writes a client certificate to ``memory/tv_remote/`` (the TV trusts it
from then on); a Core restart picks it up. ``TV_REMOTE_DIRECT=0`` turns the
direct path off (everything goes through Home Assistant again).
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Callable

from jarvis_logger import get_logger

log = get_logger(__name__)

_DIR = Path(__file__).resolve().parent.parent / "memory" / "tv_remote"
CERT = _DIR / "cert.pem"
KEY = _DIR / "key.pem"
CLIENT_NAME = "JARVIS"
# A button press that can't get through this fast goes to Home Assistant instead.
_SEND_TIMEOUT_S = 4.0

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_remote: Any = None


def _host() -> str:
    return os.getenv("TV_HOST", "").strip()


def _library_present() -> bool:
    try:
        import androidtvremote2  # noqa: F401
    except ImportError:
        return False
    return True


def configured() -> bool:
    """Direct control is on: enabled, a TV address, a paired certificate and the library."""
    if os.getenv("TV_REMOTE_DIRECT", "1").strip().lower() in ("0", "false", "off", "no"):
        return False
    return bool(_host()) and CERT.exists() and KEY.exists() and _library_present()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, daemon=True, name="tv-direct").start()
            _loop = loop
        return _loop


async def _connected() -> Any:
    """The open connection, opening it (and its auto-reconnect) on first use."""
    global _remote
    from androidtvremote2 import AndroidTVRemote

    if _remote is not None and getattr(_remote, "_remote_message_protocol", None) is not None:
        return _remote
    if _remote is None:
        _remote = AndroidTVRemote(CLIENT_NAME, str(CERT), str(KEY), _host(),
                                  loop=asyncio.get_running_loop(), enable_ime=False)
    await asyncio.wait_for(_remote.async_connect(), timeout=_SEND_TIMEOUT_S - 0.5)
    _remote.keep_reconnecting(lambda: log.warning("TV direct remote: the TV no longer trusts JARVIS; pair again"))
    log.info("TV direct remote connected to %s", _host())
    return _remote


def _run(action: Callable[[Any], Any]) -> tuple[bool, str]:
    """Run ``action(remote)`` on the connection's loop; (ok, detail)."""
    if not configured():
        return False, "direct TV remote isn't set up"

    async def job() -> Any:
        remote = await _connected()
        outcome = action(remote)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        return outcome

    future = asyncio.run_coroutine_threadsafe(job(), _ensure_loop())
    try:
        future.result(timeout=_SEND_TIMEOUT_S)
    except FutureTimeout:
        future.cancel()
        return False, "the TV didn't answer in time"
    except Exception as exc:  # noqa: BLE001 - any failure means "use Home Assistant instead"
        return False, f"{type(exc).__name__}: {exc}"
    return True, "ok"


def press_key(code: str) -> tuple[bool, str]:
    """One remote key (``DPAD_UP``, ``MEDIA_PLAY_PAUSE``, ...)."""
    return _run(lambda remote: remote.send_key_command(code))


async def _set_power(remote: Any, on: bool) -> None:
    state = remote.is_on
    if state is None:
        remote.send_key_command("WAKEUP" if on else "SLEEP")
    elif state != on:
        remote.send_key_command("POWER")


def power(on: bool) -> tuple[bool, str]:
    return _run(lambda remote: _set_power(remote, on))


def launch(link_or_package: str) -> tuple[bool, str]:
    """Wake the TV if needed, then open an app (package id) or a deep link,
    like Home Assistant's ``remote.turn_on`` with an ``activity``."""
    async def go(remote: Any) -> None:
        if remote.is_on is False:
            remote.send_key_command("POWER")
            await asyncio.sleep(1.0)
        remote.send_launch_app_command(link_or_package)
    return _run(go)


# --- Pairing (run once from a terminal on the Surface) ---------------------------

async def _pair() -> int:
    from androidtvremote2 import AndroidTVRemote

    host = _host()
    if not host:
        print("Set TV_HOST (the TV's IP) in .env first.")
        return 1
    _DIR.mkdir(parents=True, exist_ok=True)
    remote = AndroidTVRemote(CLIENT_NAME, str(CERT), str(KEY), host, enable_ime=False)
    await remote.async_generate_cert_if_missing()
    name, mac = await remote.async_get_name_and_mac()
    print(f"Pairing with {name} ({host}, {mac}). A code should appear on the TV.")
    await remote.async_start_pairing()
    code = input("Code on the TV: ").strip()
    await remote.async_finish_pairing(code)
    await remote.async_connect()
    remote.disconnect()
    print("Paired. Restart Core (sudo systemctl restart jarvis-api) and the remote goes direct.")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] != ["pair"]:
        print("usage: python -m integrations.tv_direct pair")
        return 2
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    return asyncio.run(_pair())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
