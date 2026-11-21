"""Pick the conversation model, following new Claude releases automatically.

``ANTHROPIC_MODEL`` (read live, like every setting):

- a concrete id (``claude-haiku-4-5-20251001``) pins that model, as before;
- empty, ``auto`` or ``auto:<family>`` follows the newest model in a family
  (``haiku`` by default, or ``ANTHROPIC_MODEL_FAMILY``). The newest id is
  looked up from the Models API at most once a day and cached in
  ``memory/model_cache.json``; any lookup failure keeps the last known id
  (or the built-in default), so a network blip never takes JARVIS dark.

Newer generations changed the request surface (Haiku 5.5 thinks by default,
which eats the 1024-token reply budget and adds voice latency), so
``request_kwargs`` adds per-generation settings: on 5.x+ models an
``effort`` level (``ANTHROPIC_EFFORT``, default ``low``) and, for the Haiku
family, thinking off (``ANTHROPIC_THINKING=off``, the default). Both go in
``extra_body`` so older SDK versions on Core's host accept them. If the API
rejects them for some future model, ``mark_plain`` remembers to send that
model a plain request instead.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_CACHE_FILE = Path(__file__).resolve().parents[1] / "memory" / "model_cache.json"
_REFRESH_S = 24 * 3600
_RETRY_AFTER_FAILURE_S = 3600
_LEGACY_PREFIXES = ("claude-instant", "claude-2", "claude-3", "claude-haiku-4", "claude-sonnet-4", "claude-opus-4")
_FAMILIES = {"haiku", "sonnet", "opus"}
_lock = threading.Lock()
_plain_models: set[str] = set()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_CACHE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _CACHE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2))
        temporary.replace(_CACHE_FILE)
    except OSError:
        log.warning("Could not write %s", _CACHE_FILE, exc_info=True)


def _family(setting: str) -> str | None:
    """The auto family for this setting, or None when the setting pins a model."""
    value = setting.strip().lower()
    if value and value != "auto" and not value.startswith("auto:"):
        return None
    family = value.split(":", 1)[1].strip() if ":" in value else ""
    family = family or os.getenv("ANTHROPIC_MODEL_FAMILY", "haiku").strip().lower() or "haiku"
    return family if family in _FAMILIES else "haiku"


def _newest_from_api(family: str) -> str | None:
    """Newest released model id in ``family`` from the Models API."""
    key = (os.getenv("ANTHROPIC_API_KEY", "") or "").strip()
    if not key:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=5.0, max_retries=0)
    candidates = [
        model for model in client.models.list(limit=100)
        if str(getattr(model, "id", "")).startswith(f"claude-{family}-")
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda m: str(getattr(m, "created_at", "")))
    return str(newest.id)


def resolve(setting: str, default: str) -> str:
    """The model id to use for ``setting`` (the raw ``ANTHROPIC_MODEL`` value)."""
    family = _family(setting)
    if family is None:
        return setting.strip()
    if not (os.getenv("ANTHROPIC_API_KEY", "") or "").strip():
        return default  # offline / tests: nothing to look up, nothing to cache
    now = time.time()
    with _lock:
        cache = _load()
        entry = cache.get(family) if isinstance(cache.get(family), dict) else {}
        known = str(entry.get("id") or "")
        due = now - float(entry.get("checked_at", 0) or 0) > (_REFRESH_S if known else _RETRY_AFTER_FAILURE_S)
        if due or not entry:
            try:
                newest = _newest_from_api(family)
            except Exception as exc:  # noqa: BLE001 - any failure keeps the old id
                log.warning("Model lookup for %s failed: %s", family, exc)
                newest = None
            if newest and newest != known:
                log.info("Conversation model for family %s: %s -> %s", family, known or default, newest)
            # A failed lookup on a known id waits a day, not an hour: the
            # known id is still fine, and an outage shouldn't add latency.
            cache[family] = {"id": newest or known, "checked_at": now}
            _save(cache)
            known = newest or known
    return known or default


def is_legacy(model: str) -> bool:
    return model.startswith(_LEGACY_PREFIXES)


def request_kwargs(model: str) -> dict[str, Any]:
    """Extra Messages API kwargs for this model's generation."""
    if is_legacy(model) or model in _plain_models:
        return {}
    body: dict[str, Any] = {}
    effort = os.getenv("ANTHROPIC_EFFORT", "low").strip().lower()
    if effort in {"low", "medium", "high"}:
        body["output_config"] = {"effort": effort}
    thinking_off = os.getenv("ANTHROPIC_THINKING", "off").strip().lower() in {"off", "false", "0", "no"}
    if thinking_off and model.startswith("claude-haiku-") and "output_config" in body:
        # Haiku 5.x accepts disabled thinking at effort high or below only.
        body["thinking"] = {"type": "disabled"}
    return {"extra_body": body} if body else {}


def mark_plain(model: str) -> None:
    """Stop sending generation settings to a model that rejected them."""
    log.warning("Model %s rejected effort/thinking settings; sending plain requests from now on", model)
    _plain_models.add(model)


def is_settings_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    return "thinking" in text or "effort" in text or "output_config" in text
