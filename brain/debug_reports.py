"""Durable, user-submitted companion debug reports.

Reports are explicit feedback artifacts, not long-term personality memory.  The
iOS companion sends a bounded transcript plus the user's description; JARVIS
can later list and inspect reports through read-only tools.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_REPORTS_FILE = Path(__file__).resolve().parent.parent / "memory" / "debug_reports.json"
_LOCK = RLock()


def _load() -> list[dict[str, Any]]:
    if not _REPORTS_FILE.exists():
        return []
    try:
        data = json.loads(_REPORTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not load debug reports: %s", exc)
    return []


def _save(reports: list[dict[str, Any]]) -> None:
    _REPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _REPORTS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(_REPORTS_FILE)


def create_debug_report(
    *,
    issue: str,
    messages: list[dict[str, str]],
    session_id: str | None = None,
    conversation_title: str | None = None,
    focus_message_id: str | None = None,
    app_context: dict[str, str] | None = None,
    source: str = "ios_companion",
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist one bounded report and return its public metadata.

    ``sections`` (``[{"title": str, "lines": [str]}]``) carry gathered
    evidence, e.g. an incident report's logs and health checks; a report
    with sections needs no transcript."""
    clean_issue = issue.strip()
    if not clean_issue:
        raise ValueError("Issue description is required.")

    clean_messages: list[dict[str, str]] = []
    for message in messages[-100:]:
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant", "error"} or not content:
            continue
        clean_messages.append({
            "id": str(message.get("id", ""))[:100],
            "role": role,
            "content": content[:20_000],
            "created_at": str(message.get("created_at", ""))[:100],
        })
    clean_sections = [
        {"title": str(section.get("title", ""))[:200],
         "lines": [str(line)[:2_000] for line in list(section.get("lines") or [])[:60]]}
        for section in (sections or [])[:20]
        if isinstance(section, dict) and section.get("title")
    ]
    if not clean_messages and not clean_sections:
        raise ValueError("At least one transcript message is required.")

    report = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "open",
        "source": source[:50],
        "session_id": (session_id or "")[:100] or None,
        "conversation_title": (conversation_title or "")[:200] or None,
        "focus_message_id": (focus_message_id or "")[:100] or None,
        "issue": clean_issue[:4_000],
        "messages": clean_messages,
        "sections": clean_sections,
        "app_context": {
            str(key)[:100]: str(value)[:500]
            for key, value in list((app_context or {}).items())[:20]
        },
    }
    with _LOCK:
        reports = _load()
        reports.append(report)
        _save(reports)
    log.info("Stored debug report %s with %d messages", report["id"], len(clean_messages))
    return {"id": report["id"], "created_at": report["created_at"], "status": "open"}


def list_debug_reports(limit: int = 20) -> list[dict[str, Any]]:
    with _LOCK:
        reports = _load()
    return list(reversed(reports[-max(1, min(limit, 100)):]))


def get_debug_report(report_id: str) -> dict[str, Any] | None:
    target = report_id.strip().lower()
    with _LOCK:
        for report in reversed(_load()):
            if str(report.get("id", "")).lower() == target:
                return report
    return None


def summarize_debug_reports() -> str:
    reports = list_debug_reports()
    if not reports:
        return "No companion debug reports have been submitted."
    lines = [f"{len(reports)} most recent companion debug report(s):"]
    for report in reports:
        issue = str(report.get("issue", "")).replace("\n", " ")
        lines.append(
            f"- {report.get('id')} | {report.get('created_at')} | "
            f"{report.get('status', 'open')} | {issue[:180]}"
        )
    return "\n".join(lines)


def describe_debug_report(report_id: str) -> str:
    report = get_debug_report(report_id)
    if report is None:
        return f"No debug report found with id {report_id}."
    lines = [
        f"Debug report {report['id']} ({report.get('created_at')})",
        f"Status: {report.get('status', 'open')}",
        f"Source: {report.get('source', 'unknown')}",
        f"Session: {report.get('session_id') or 'not bound'}",
        (f"Alert: {report.get('issue', '')}" if report.get("source") == "incident"
         else f"Issue reported by Nick: {report.get('issue', '')}"),
    ]
    context = report.get("app_context") or {}
    if context:
        lines.append("App context: " + ", ".join(f"{key}={value}" for key, value in context.items()))
    for section in report.get("sections") or []:
        lines.append(f"{section.get('title', '')}:")
        lines.extend(f"  {line}" for line in section.get("lines", []))
    if report.get("messages"):
        lines.append("Transcript:")
    for message in report.get("messages", []):
        marker = " [FOCUS]" if message.get("id") == report.get("focus_message_id") else ""
        lines.append(f"{message.get('role', 'unknown')}{marker}: {message.get('content', '')}")
    text = "\n".join(lines)
    return text[:30_000]
