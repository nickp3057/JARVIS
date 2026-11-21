"""Email filing rules: Nick's "put this kind of mail in that folder" instructions.

A rule names a Gmail folder (label, nested with ``/`` like
``Kinda Important/Amazon``), the senders it covers (address or domain
fragments, e.g. ``amazon.com``) and optionally subject words (matched at a
word start, so ``return`` also catches "Returned" and "Returns"). A rule
can also colour its folder's label (Gmail colours labels, not single
messages, so "mark them red" means a red folder label) and choose whether
filed mail is marked read (default: left unread, so Nick sees it).

The email sorter (``brain/email_triage.py``) applies rules before the model
on every pass, including mail it already kept in an earlier pass. Rules are
deterministic: no model decides what a rule files.

State: ``memory/email_rules.json``. CLI for setup without a conversation:

    python -m brain.email_rules list
    python -m brain.email_rules add "Kinda Important/Amazon" --from amazon.com \
        --subject shipped delivered delivery arriving return refund --color red
    python -m brain.email_rules remove <id>
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "email_rules.json"
_lock = threading.RLock()
_MAX_RULES = 50
_MAX_TERMS = 20

# Gmail only accepts colours from its fixed label palette.
COLORS: dict[str, tuple[str, str]] = {
    "red": ("#fb4c2f", "#ffffff"),
    "orange": ("#ffad47", "#ffffff"),
    "yellow": ("#fad165", "#000000"),
    "green": ("#16a766", "#ffffff"),
    "blue": ("#4a86e8", "#ffffff"),
    "purple": ("#a479e2", "#ffffff"),
    "pink": ("#f691b3", "#ffffff"),
    "gray": ("#999999", "#ffffff"),
}


def _path() -> Path:
    return _FILE


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    return [r for r in rules if isinstance(r, dict) and r.get("folder")] if isinstance(rules, list) else []


def _save(rules: list[dict[str, Any]]) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"rules": rules}, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.error("Could not save email rules: %s", exc)


def _terms(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        term = " ".join(str(item).split()).strip().lower()
        if term and term not in out:
            out.append(term[:80])
    return out[:_MAX_TERMS]


def _color(value: Any) -> str:
    name = str(value or "").strip().lower()
    return {"grey": "gray"}.get(name, name)


def all_rules() -> list[dict[str, Any]]:
    with _lock:
        return _load()


def describe(rule: dict[str, Any]) -> str:
    who = ", ".join(rule.get("from") or []) or "any sender"
    what = (" with " + " / ".join(rule["subject"]) + " in the subject") if rule.get("subject") else ""
    extras = []
    if rule.get("color"):
        extras.append(f"{rule['color']} label")
    extras.append("marked read" if rule.get("mark_read") else "left unread")
    return f"{rule['id']}: mail from {who}{what} -> {rule['folder']} ({', '.join(extras)})"


def add_rule(folder: Any, senders: Any = None, subjects: Any = None, color: Any = "",
             mark_read: bool = False) -> tuple[dict[str, Any] | None, str]:
    """Add (or replace the same folder+senders) rule. Returns (rule, message)."""
    name = "/".join(p.strip() for p in str(folder or "").strip().strip("/").split("/") if p.strip())
    from_terms, subject_terms = _terms(senders), _terms(subjects)
    color_name = _color(color)
    if not name:
        return None, "Which folder should that mail go to, sir?"
    if not from_terms and not subject_terms:
        return None, "A rule needs at least a sender or some subject words, sir, or it would file everything."
    if color_name and color_name not in COLORS:
        return None, f"Gmail labels can be {', '.join(COLORS)}; '{color}' isn't one of them."
    with _lock:
        rules = _load()
        replaced = [r for r in rules if r["folder"].lower() == name.lower() and r.get("from") == from_terms]
        rules = [r for r in rules if r not in replaced]
        if len(rules) >= _MAX_RULES:
            return None, f"That's {_MAX_RULES} rules already, sir; remove one first."
        rule = {"id": replaced[0]["id"] if replaced else uuid.uuid4().hex[:6],
                "folder": replaced[0]["folder"] if replaced else name,
                "from": from_terms, "subject": subject_terms, "color": color_name,
                "mark_read": bool(mark_read), "created_at": time.time()}
        rules.append(rule)
        _save(rules)
    log.info("Email rule %s %s", "updated" if replaced else "added", describe(rule))
    return rule, ("Updated" if replaced else "Added") + " email rule " + describe(rule)


def remove_rule(identifier: Any) -> str:
    needle = str(identifier or "").strip().lower()
    if not needle:
        return "Say which email rule to remove, by id or folder."
    with _lock:
        rules = _load()
        hits = [r for r in rules if r["id"] == needle] or [
            r for r in rules if needle in r["folder"].lower() or any(needle in s for s in r.get("from", []))]
        if not hits:
            return f"No email rule matches '{identifier}'."
        if len(hits) > 1:
            return "Several rules match; use the id: " + "; ".join(describe(r) for r in hits)
        _save([r for r in rules if r is not hits[0]])
    return f"Removed email rule {describe(hits[0])}. Mail already filed stays where it is."


def list_rules() -> str:
    rules = all_rules()
    if not rules:
        return "No email filing rules yet, sir."
    return "Email filing rules:\n" + "\n".join(describe(r) for r in rules)


# --- Matching -------------------------------------------------------------------

def _sender_hit(term: str, address: str, name: str) -> bool:
    term = term[1:] if term.startswith("@") else term
    if "@" in term:  # a full address, or a mailbox like shipment-tracking@
        return term in address
    if "." in term:  # a domain: amazon.com covers shipment-tracking@amazon.com and @mail.amazon.com
        domain = address.rsplit("@", 1)[-1]
        return domain == term or domain.endswith("." + term)
    return term in address or term in name


def matches(rule: dict[str, Any], msg: dict[str, Any]) -> bool:
    address = str(msg.get("from", "")).lower()
    name = str(msg.get("from_name", "")).lower()
    subject = str(msg.get("subject", "")).lower()
    senders = rule.get("from") or []
    if senders and not any(_sender_hit(t, address, name) for t in senders):
        return False
    words = rule.get("subject") or []
    if words and not any(re.search(r"(?<![a-z0-9])" + re.escape(w), subject) for w in words):
        return False
    return True


def gmail_query(rule: dict[str, Any], days: int) -> str:
    """A Gmail search that's a superset of the rule (verified locally with ``matches``)."""
    parts = ["in:inbox", f"newer_than:{int(days)}d"]
    senders = [s.lstrip("@") for s in rule.get("from") or [] if "." in s or "@" in s]
    if senders and len(senders) == len(rule.get("from") or []):
        parts.append("from:(" + " OR ".join(senders) + ")")
    elif rule.get("from"):
        parts.append("(" + " OR ".join(f'"{s}"' for s in rule["from"]) + ")")
    return " ".join(parts)


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m brain.email_rules")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("folder")
    add.add_argument("--from", dest="senders", nargs="*", default=[])
    add.add_argument("--subject", nargs="*", default=[])
    add.add_argument("--color", default="")
    add.add_argument("--mark-read", action="store_true")
    rm = sub.add_parser("remove")
    rm.add_argument("rule")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        print(list_rules())
    elif args.cmd == "add":
        rule, message = add_rule(args.folder, args.senders, args.subject, args.color, args.mark_read)
        print(message)
        return 0 if rule else 1
    else:
        print(remove_rule(args.rule))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
