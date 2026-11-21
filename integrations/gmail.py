"""Gmail integration for JARVIS: read the inbox, label, file and trash mail.

Used by the email triage subagent (``brain/email_triage.py``); nothing here
decides what to do with a message, it only does what it is told.

Configuration (.env):
- GOOGLE_CREDENTIALS_FILE : the same Desktop-app OAuth client as Calendar.
  Enable the Gmail API on that Google Cloud project.
- GMAIL_TOKEN_FILE        : cached token. Optional; defaults to
  memory/gmail_token.json. Kept apart from the calendar token so linking
  Gmail (a wider scope) never invalidates the calendar's read-only consent.

Linking is always explicit, never from inside the running process:

    .venv/bin/python -m integrations.gmail --auth        # Mac: opens a browser
    ssh -L 8765:localhost:8765 jarvis                     # the Surface: tunnel,
    cd ~/jarvis && .venv/bin/python -m integrations.gmail --auth   # then open the URL

Scope is ``gmail.modify``: read, label, archive and move to Trash. It cannot
permanently delete mail or change settings; Trash empties itself after 30
days, so everything JARVIS trashes can be restored until then.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from jarvis_logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_DEFAULT_TOKEN = _PROJECT_ROOT / "memory" / "gmail_token.json"
_AUTH_PORT = 8765

_service: Any | None = None
_hint_logged_at = 0.0
# googleapiclient/httplib2 are not thread-safe (see google_calendar.py).
_api_lock = threading.Lock()


def _credentials_file() -> str:
    return os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()


def _token_file() -> Path:
    raw = os.getenv("GMAIL_TOKEN_FILE", "").strip()
    return Path(raw).expanduser() if raw else _DEFAULT_TOKEN


def is_configured() -> bool:
    """Google credentials exist, so Gmail can be (or has been) linked."""
    return bool(_credentials_file()) or is_linked()


def is_linked() -> bool:
    """True once ``--auth`` has saved a token. Cheap; safe to poll."""
    return _token_file().exists()


def _log_link_hint() -> None:
    global _hint_logged_at
    now = time.monotonic()
    if _hint_logged_at and now - _hint_logged_at < 3600:
        return
    _hint_logged_at = now
    log.warning("Gmail token missing or revoked. Link it with: .venv/bin/python -m integrations.gmail --auth")


def _get_service() -> Any | None:
    """The Gmail API client, or None when unlinked/offline. Never prompts."""
    global _service
    if _service is not None:
        return _service
    if not is_linked():
        return None
    try:
        from google.auth.exceptions import TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("Google API libraries not installed; Gmail disabled.")
        return None
    token_path = _token_file()
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gmail token unreadable: %s", exc)
        _log_link_hint()
        return None
    if not creds.valid:
        if not (creds.expired and creds.refresh_token):
            _log_link_hint()
            return None
        try:
            creds.refresh(Request())
        except TransportError as exc:
            log.info("Gmail token refresh deferred, network unavailable: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - revoked / invalid grant
            log.warning("Gmail token refresh rejected: %s", exc)
            _log_link_hint()
            return None
        try:
            token_path.write_text(creds.to_json())
        except OSError:
            pass
    try:
        _service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gmail service build failed: %s", exc)
        return None
    return _service


def available() -> bool:
    return _get_service() is not None


def _call(request: Any) -> Any:
    with _api_lock:
        return request.execute()


# --- Reading -----------------------------------------------------------------

def list_message_ids(query: str, limit: int = 50) -> list[str] | None:
    """Ids matching a Gmail search (``in:inbox newer_than:3d``), newest first."""
    svc = _get_service()
    if svc is None:
        return None
    ids: list[str] = []
    page: str | None = None
    while len(ids) < limit:
        params: dict[str, Any] = {"userId": "me", "q": query, "maxResults": min(100, limit - len(ids))}
        if page:
            params["pageToken"] = page
        resp = _call(svc.users().messages().list(**params))
        ids.extend(str(m["id"]) for m in resp.get("messages", []) if m.get("id"))
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids[:limit]


def _header(headers: list[dict[str, str]], name: str) -> str:
    wanted = name.lower()
    for item in headers:
        if str(item.get("name", "")).lower() == wanted:
            return str(item.get("value", ""))
    return ""


def unsubscribe_link(raw: str) -> str:
    """The best target from a List-Unsubscribe header: https first, then mailto."""
    parts = [p.strip().strip("<>").strip() for p in raw.split(",") if p.strip()]
    for part in parts:
        if part.lower().startswith("http"):
            return part
    for part in parts:
        if part.lower().startswith("mailto:"):
            return part
    return ""


def summarize_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``format=metadata`` message into what triage needs."""
    headers = (raw.get("payload") or {}).get("headers") or []
    name, address = parseaddr(_header(headers, "From"))
    labels = list(raw.get("labelIds") or [])
    return {
        "id": str(raw.get("id", "")),
        "thread_id": str(raw.get("threadId", "")),
        "from_name": name,
        "from": address.lower(),
        "subject": _header(headers, "Subject"),
        "snippet": str(raw.get("snippet", "")),
        "received": int(raw.get("internalDate", 0) or 0) / 1000.0,
        "labels": labels,
        "starred": "STARRED" in labels,
        "unsubscribe": unsubscribe_link(_header(headers, "List-Unsubscribe")),
    }


def get_message(message_id: str) -> dict[str, Any] | None:
    svc = _get_service()
    if svc is None:
        return None
    raw = _call(svc.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe"],
    ))
    return summarize_message(raw)


def user_labels() -> dict[str, str] | None:
    """User-created labels (Gmail's "folders"), name -> id."""
    svc = _get_service()
    if svc is None:
        return None
    resp = _call(svc.users().labels().list(userId="me"))
    return {str(lb["name"]): str(lb["id"]) for lb in resp.get("labels", [])
            if lb.get("type") == "user" and lb.get("name") and lb.get("id")}


# --- Acting ------------------------------------------------------------------

def _modify(message_id: str, add: list[str], remove: list[str]) -> bool:
    svc = _get_service()
    if svc is None:
        return False
    _call(svc.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": add, "removeLabelIds": remove}))
    return True


def file_to_label(message_id: str, label_id: str, mark_read: bool = True) -> bool:
    """Move into a folder: add the label, take it out of the inbox, mark it read (unless told not to)."""
    return _modify(message_id, [label_id], ["INBOX", "UNREAD"] if mark_read else ["INBOX"])


def create_label(name: str) -> str | None:
    """Create a user label (``Parent/Child`` nests it). Returns its id."""
    svc = _get_service()
    if svc is None:
        return None
    resp = _call(svc.users().labels().create(userId="me", body={
        "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}))
    return str(resp.get("id", "")) or None


def set_label_color(label_id: str, background: str, text: str) -> bool:
    """Colour a label. Gmail only accepts its fixed palette (see brain.email_rules.COLORS)."""
    svc = _get_service()
    if svc is None:
        return False
    _call(svc.users().labels().patch(userId="me", id=label_id, body={
        "color": {"backgroundColor": background, "textColor": text}}))
    return True


def trash(message_id: str) -> bool:
    """Move to Trash (recoverable for 30 days). Never a permanent delete."""
    svc = _get_service()
    if svc is None:
        return False
    _call(svc.users().messages().trash(userId="me", id=message_id))
    return True


def untrash(message_id: str) -> bool:
    svc = _get_service()
    if svc is None:
        return False
    _call(svc.users().messages().untrash(userId="me", id=message_id))
    return _modify(message_id, ["INBOX"], [])


# --- One-time consent --------------------------------------------------------

def _cli_auth(port: int = _AUTH_PORT) -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred_file = _credentials_file()
    if not cred_file or not Path(cred_file).exists():
        print("GOOGLE_CREDENTIALS_FILE is not set or missing; see JARVIS.md 'Gmail setup'.")
        return 1
    headless = os.getenv("GOOGLE_OAUTH_HEADLESS", "").strip().lower() in {"1", "true", "yes", "on"} or (
        sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")))
    flow = InstalledAppFlow.from_client_secrets_file(cred_file, _SCOPES)
    creds = flow.run_local_server(
        host="localhost", bind_addr="127.0.0.1", port=port, open_browser=not headless,
        authorization_prompt_message=(
            "If this is a remote host, this session must be: ssh -L {port}:localhost:{port} <host>\n"
            "Open this URL in your browser:\n{url}"
        ).replace("{port}", str(port)),
        success_message="JARVIS Gmail access granted. You can close this tab.",
    )
    token_path = _token_file()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Token saved to {token_path}. The running JARVIS starts sorting mail on its next pass.")
    return 0


if __name__ == "__main__":
    if "--auth" in sys.argv[1:]:
        raise SystemExit(_cli_auth())
    print("Usage: python -m integrations.gmail --auth")
