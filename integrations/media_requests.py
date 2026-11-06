"""Chat-free media requests for the companion app's Media tab.

Same safety model as chat, without the model in the loop:

1. ``search`` runs the Radarr/Sonarr lookup and records the session receipt
   (``media_selections``), so only titles actually shown can be requested.
2. ``request`` takes the exact tapped id, narrows the receipt to it and opens
   a ``brain.confirm_gate`` proposal. Nothing is written yet.
3. ``confirm`` approves with the proposal's code and runs ``add_movie`` /
   ``add_series`` (which keep their own validation and timeout handling).

The app has one owner, so the flow uses one fixed session namespace, separate
from chat sessions so a chat "yes" can never approve an app request.
"""
from __future__ import annotations

from typing import Any

from integrations import media_selections, radarr, sonarr
from integrations import servarr_common as servarr

SESSION = "companion-media"
_KINDS = {
    "movie": ("radarr", radarr, "tmdbId"),
    "show": ("sonarr", sonarr, "tvdbId"),
}
_MAX_RESULTS = 10
# Shows added from the Media tab grab season 1 now (and nothing else), so
# the tap actually downloads something. Sonarr's ``future`` default would
# monitor only unaired episodes and the add-time search would find nothing.
SHOW_MONITOR = "firstSeason"


def _poster(item: dict[str, Any]) -> str | None:
    for image in item.get("images") or []:
        if image.get("coverType") == "poster":
            return image.get("remoteUrl") or None
    return None


def _kind(kind: str) -> tuple[str, Any, str]:
    entry = _KINDS.get((kind or "").strip().lower())
    if entry is None:
        raise ValueError("kind must be 'movie' or 'show'")
    return entry


def search(query: str, kind: str) -> dict[str, Any]:
    service, module, id_key = _kind(kind)
    query = (query or "").strip()
    if not query:
        return {"results": [], "error": "Enter a title."}
    if not servarr.is_configured(service):
        return {"results": [], "error": f"{service.title()} isn't configured."}
    ok, results = module.candidates(query)
    if not ok:
        return {"results": [], "error": str(results)}
    shown = results[:_MAX_RESULTS]
    media_selections.record(service, SESSION, shown, id_key)
    return {"results": [
        {
            "kind": kind,
            "id": int(item[id_key]),
            "title": item.get("title", "Unknown"),
            "year": item.get("year"),
            "in_library": module._in_library(item),
            "overview": (item.get("overview") or "")[:300],
            "poster": _poster(item),
        }
        for item in shown
    ]}


def request(kind: str, media_id: int, quality: str | None = None) -> dict[str, Any]:
    """Open a confirm-gate proposal for one searched title; writes nothing."""
    from brain import confirm_gate

    service, module, _ = _kind(kind)
    chosen = media_selections.narrow(service, SESSION, int(media_id))
    if chosen is None:
        return {"status": "invalid", "message": "That title wasn't in your latest search; search again."}
    if module._in_library(chosen):
        return {"status": "exists", "message": f"{chosen.get('title', 'It')} is already in your library."}
    quality = (quality or "").strip() or None
    year = chosen.get("year")
    label = f"{chosen.get('title', 'it')}{f' ({year})' if year else ''}"
    profile = quality or servarr.default_quality(service)

    def run() -> str:
        if service == "radarr":
            return radarr.add_movie(media_id, quality=quality, confirmed=True, session_id=SESSION)
        return sonarr.add_series(
            media_id, quality=quality, monitor=SHOW_MONITOR, confirmed=True, session_id=SESSION
        )

    scope = " (season 1)" if service == "sonarr" else ""
    confirm_gate.propose(SESSION, f"add {label}{scope} to {service.title()} at {profile}", run)
    return {
        "status": "needs_confirmation",
        "code": confirm_gate.pending_code(SESSION),
        "description": confirm_gate.pending_description(SESSION),
    }


def confirm(code: str) -> dict[str, Any]:
    """Run the open proposal if ``code`` matches it."""
    from brain import confirm_gate

    code = (code or "").strip()
    expected = confirm_gate.pending_code(SESSION)
    if expected is None:
        return {"status": "expired", "message": "That request expired; tap the title again."}
    if code != expected:
        return {"status": "invalid", "message": "That confirmation code doesn't match."}
    message = confirm_gate.conversation_action(f"approve {code}", SESSION)
    added = bool(message) and message.startswith("Added ")
    return {"status": "added" if added else "failed", "message": message or "Nothing happened."}
