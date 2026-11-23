"""The money advisor: JARVIS watching Nick's spending (REMOTE_AND_MONEY_PLAN.md, Part 2).

Spending comes from Mooney's money hub (``integrations/mooney.py``): Plaid
transactions for MACU checking and Chase, plus Apple Pay payments the iOS
Shortcut posts to ``POST /api/money/transaction`` (Apple Card can't be read
through Plaid). The triggers are plain rules; the model only writes the words.

**Nick's budget (2026-11-23):**

- *Weekly:* $125 (``weekly_budget``) of everyday spending on MACU debit, for a
  budget week of Monday 00:00 to Sunday 23:59 local time.
- *Apple Card:* outside the weekly budget, watched on its own (heads-up and a
  monthly total) so it doesn't quietly become the real budget.
- *Chase:* the Amazon card; a monthly total and the heads-up.
- Transfers between his accounts and card payments are never spending.

**What he hears:**

- *Big-purchase heads-up:* any charge over ``big_purchase`` ($50), any card, at once.
- *Same-day nudge:* the week running ahead of its $125 pace, or one merchant
  three times in seven days. At most one a day.
- *Weekly review:* Sunday 12:00, before the new $125 lands. A week over
  budget gets a proper scolding, in character, never cruel.
- *Monthly check:* the 1st: last month by card, plus one suggestion.

Nothing is sent during Nick's quiet hours (set in the companion: Settings >
Money); a heads-up waits for them to end. While ``dry_run`` is on (the first
week), notices only land in the log (``finance_settings`` shows it).

**Arguing back:** ``explain_transaction`` finds the charge and writes the
annotation to Mooney (both apps show it): ``reimbursed``/``split`` net the
money out, ``excused`` keeps it counted but noted, ``mistake`` drops it.
A remembered rule ("golf is usually split") is applied to later charges.
The same excuse three times in a week gets noticed.

Privacy: finance alerts are ``private`` (never on the TV's stream or glance);
the tools are locked to Nick (``voice_trust.FINANCE_TOOLS``); spending never
becomes a profile fact (``memory_extractor``). State: ``memory/finance.json``.
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from jarvis_logger import get_logger

log = get_logger(__name__)

_STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "finance.json"
_lock = threading.RLock()
_NOT_SPENDING = {"TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS", "INCOME"}
ROLES = ("weekly", "apple_card", "chase", "other")
ROLE_NAMES = {"weekly": "weekly budget (MACU debit)", "apple_card": "Apple Card", "chase": "Chase (Amazon)",
              "other": "other cards"}
DEFAULTS: dict[str, Any] = {
    "weekly_budget": 125.0, "big_purchase": 50.0, "quiet_start": "22:00", "quiet_end": "08:00",
    "dry_run": True, "weekly_review_hour": 12,
}


# --- State ---------------------------------------------------------------------

def _load() -> dict[str, Any]:
    try:
        data = json.loads(_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: dict[str, Any]) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.chmod(0o600)
    tmp.replace(_STATE_FILE)


def settings() -> dict[str, Any]:
    with _lock:
        return {**DEFAULTS, **_load().get("settings", {})}


def update_settings(**changes: Any) -> dict[str, Any]:
    """Change the weekly budget, the heads-up line, quiet hours or dry run."""
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None or key not in DEFAULTS:
            continue
        if key in ("weekly_budget", "big_purchase"):
            clean[key] = max(0.0, round(float(value), 2))
        elif key in ("quiet_start", "quiet_end"):
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(value).strip()):
                raise ValueError(f"{key} must look like 22:00")
            clean[key] = str(value).strip().zfill(5)
        elif key == "dry_run":
            clean[key] = bool(value)
        elif key == "weekly_review_hour":
            clean[key] = max(0, min(23, int(value)))
    with _lock:
        state = _load()
        state["settings"] = {**state.get("settings", {}), **clean}
        _save(state)
    return settings()


def enabled() -> bool:
    from integrations import mooney
    return os.getenv("FINANCE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"} and mooney.configured()


def quiet(now: datetime, conf: dict[str, Any] | None = None) -> bool:
    conf = conf or settings()
    start = datetime.strptime(conf["quiet_start"], "%H:%M").time()
    end = datetime.strptime(conf["quiet_end"], "%H:%M").time()
    t = now.time()
    if start == end:
        return False
    return (start <= t or t < end) if start > end else (start <= t < end)


# --- Ledger --------------------------------------------------------------------

def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())  # Monday


def role_for(txn: dict[str, Any], accounts: dict[str, dict[str, Any]], overrides: dict[str, str]) -> str:
    """Which budget a charge belongs to: an account Nick assigned, else by name."""
    if txn.get("source") == "manual":
        card = (txn.get("card") or "").lower()
        if "apple" in card:
            return "apple_card"
        if "chase" in card or "amazon" in card:
            return "chase"
        return overrides.get(f"card:{card}", "weekly")
    account_id = txn.get("accountId") or ""
    if account_id in overrides:
        return overrides[account_id]
    account = accounts.get(account_id, {})
    label = f"{account.get('institution', '')} {account.get('name', '')}".lower()
    if "chase" in label or "amazon" in label or "prime" in label:
        return "chase"
    if "apple" in label:
        return "apple_card"
    if account.get("type") == "depository":
        return "weekly"
    return "other"


def _net(txn: dict[str, Any]) -> float:
    gross = float(txn["amount"])
    note = txn.get("annotation") or {}
    kind, back = note.get("kind"), note.get("amount")
    if kind == "mistake":
        return 0.0
    if kind == "reimbursed":
        return round(max(0.0, gross - (float(back) if back is not None else gross)), 2)
    if kind == "split":
        return round(max(0.0, gross - (float(back) if back is not None else gross / 2)), 2)
    return gross


def spending(txns: list[dict[str, Any]], items: list[dict[str, Any]],
             overrides: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Charges that count as spending, newest first, with role and net amount."""
    accounts: dict[str, dict[str, Any]] = {}
    for item in items:
        institution = (item.get("institution") or {}).get("name") or ""
        for account in item.get("accounts") or []:
            accounts[account["id"]] = {**account, "institution": institution}
    out = []
    for txn in txns:
        if txn.get("duplicateOf") or float(txn.get("amount") or 0) <= 0:
            continue
        if (txn.get("category") or "") in _NOT_SPENDING:
            continue
        out.append({
            "id": txn["id"], "date": txn["date"], "merchant": txn.get("merchant") or txn.get("name") or "Unknown",
            "gross": round(float(txn["amount"]), 2), "net": _net(txn), "role": role_for(txn, accounts, overrides or {}),
            "category": txn.get("category") or "", "pending": bool(txn.get("pending")),
            "annotation": txn.get("annotation"),
        })
    return out


def _fetch(since: date) -> list[dict[str, Any]]:
    from integrations import mooney
    with _lock:
        overrides = _load().get("roles", {})
    return spending(mooney.transactions(since.isoformat()), mooney.accounts(), overrides)


def summarize(rows: list[dict[str, Any]], start: date, end: date) -> dict[str, Any]:
    """Totals by budget, top merchants and excuses for [start, end]."""
    rows = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
    totals = {role: round(sum(r["net"] for r in rows if r["role"] == role), 2) for role in ROLES}
    merchants = Counter()
    for r in rows:
        merchants[r["merchant"]] += r["net"]
    excuses = [{"merchant": r["merchant"], "amount": r["gross"], "kind": r["annotation"]["kind"],
                "note": r["annotation"].get("note") or ""} for r in rows if r.get("annotation")]
    return {"start": start.isoformat(), "end": end.isoformat(), "totals": totals,
            "top_merchants": [(m, round(v, 2)) for m, v in merchants.most_common(5)],
            "excuses": excuses, "count": len(rows)}


# --- Words ---------------------------------------------------------------------

_VOICE = (
    "You are JARVIS, Nick's AI butler: British, dry, teasing, always calls him 'sir'. You are writing "
    "one short phone notification about his spending. Facts are given as JSON; use only those numbers. "
    "Two or three sentences, plain text, no lists, no emoji. Tease lightly. If the week went over budget, "
    "give him a proper scolding: pointed and witty, never cruel, never shaming his worth. Reimbursements "
    "and splits are already netted out; mention a good excuse fairly, but if the same excuse repeats, "
    "notice it. A sensible suggestion is welcome when it fits."
)


def _template(kind: str, facts: dict[str, Any]) -> str:
    if kind == "heads_up":
        return f"${facts['amount']:.2f} at {facts['merchant']} on the {facts['card']}, sir. Noted."
    if kind == "nudge_pace":
        return (f"${facts['spent']:.2f} of this week's ${facts['budget']:.0f} gone by {facts['weekday']}, sir. "
                "The week is not over.")
    if kind == "nudge_repeat":
        return f"{facts['merchant']} {facts['times']} times this week, sir. A pattern is forming."
    if kind == "weekly":
        verdict = "over" if facts["over"] else "under"
        return f"This week: ${facts['spent']:.2f} of ${facts['budget']:.0f}, {verdict} budget, sir."
    if kind == "monthly":
        return "Last month, sir: " + ", ".join(f"{ROLE_NAMES[r]} ${v:.2f}" for r, v in facts["totals"].items() if v) + "."
    return json.dumps(facts)


def compose(kind: str, facts: dict[str, Any], *, client: Any = None) -> str:
    """The notification text: the model's words, or a plain template without one."""
    from brain import model_select, subagents, usage
    client = client if client is not None else subagents._client()
    if client is None:
        return _template(kind, facts)
    model = model_select.resolve(os.getenv("FINANCE_MODEL", "").strip() or "auto:haiku", "claude-haiku-5-5")
    try:
        response = client.messages.create(model=model, max_tokens=300, system=_VOICE, messages=[
            {"role": "user", "content": f"Notification kind: {kind}\nFacts: {json.dumps(facts, default=str)}"}])
        usage.record_response("finance_advisor", response)
        blocks = response.content if hasattr(response, "content") else response.get("content", [])
        text = "".join(getattr(b, "text", None) or (b.get("text", "") if isinstance(b, dict) else "") for b in blocks).strip()
        return text or _template(kind, facts)
    except Exception as exc:  # noqa: BLE001 - a missing word must not lose the notice
        log.warning("Finance wording failed: %s", exc)
        return _template(kind, facts)


# --- Triggers --------------------------------------------------------------------

Notice = tuple[str, str, str, dict[str, Any]]  # kind, title, body, facts

TITLES = {"heads_up": "Big purchase", "nudge_pace": "Spending pace", "nudge_repeat": "Spending pattern",
          "weekly": "Weekly money review", "monthly": "Monthly money check"}


def check(now: datetime | None = None, *, rows: list[dict[str, Any]] | None = None,
          words: Callable[[str, dict[str, Any]], str] | None = None) -> list[Notice]:
    """One pass of the rules. Returns the notices due now and records them
    as sent; the caller delivers (or, in dry run, only logs) them."""
    now = now or datetime.now()
    today = now.date()
    conf = settings()
    words = words or compose
    if rows is None:
        rows = _fetch(min(week_start(today) - timedelta(days=7), today.replace(day=1) - timedelta(days=31)))
    notices: list[Notice] = []
    with _lock:
        state = _load()
        seen = set(state.get("heads_up", []))
        if not state.get("primed"):
            # First run: today's backlog is history, not news.
            state["primed"] = now.isoformat()
            state["heads_up"] = sorted({r["id"] for r in rows})
            state["monthly_done"] = state.get("monthly_done") or _month_key(today.replace(day=1) - timedelta(days=1))
            _save(state)
            return []
        is_quiet = quiet(now, conf)
        recent_cutoff = (today - timedelta(days=3)).isoformat()

        # Big purchase: any card, over the line, at once (quiet hours: it waits).
        if not is_quiet:
            for r in rows:
                if r["id"] in seen or r["gross"] < conf["big_purchase"] or r["date"] < recent_cutoff:
                    continue
                facts = {"amount": r["gross"], "merchant": r["merchant"], "card": ROLE_NAMES[r["role"]],
                         "pending": r["pending"]}
                if r["role"] == "weekly":
                    week = summarize(rows, week_start(today), today)
                    facts.update(week_spent=week["totals"]["weekly"], weekly_budget=conf["weekly_budget"])
                notices.append(("heads_up", TITLES["heads_up"], words("heads_up", facts), facts))
                seen.add(r["id"])
            state["heads_up"] = sorted(seen)[-2000:]

        # Same-day nudge: at most one a day, after 9.
        if not is_quiet and now.hour >= 9 and state.get("nudged_on") != today.isoformat():
            week = summarize(rows, week_start(today), today)
            spent = week["totals"]["weekly"]
            budget = conf["weekly_budget"]
            elapsed = now.weekday() + 1
            pace = budget * elapsed / 7
            last7 = [r for r in rows if r["date"] >= (today - timedelta(days=6)).isoformat()]
            repeats = Counter(r["merchant"] for r in last7 if r["net"] > 0)
            merchant, times = repeats.most_common(1)[0] if repeats else ("", 0)
            nudged_merchants = state.get("nudged_merchants", {})
            nudge = None
            if budget and spent >= 0.5 * budget and spent > pace + 20:
                nudge = ("nudge_pace", {"spent": spent, "budget": budget, "weekday": now.strftime("%A"),
                                        "pace": round(pace, 2), "top": week["top_merchants"][:3]})
            elif times >= 3 and nudged_merchants.get(merchant) != _week_key(today):
                excuses = [e["note"] for e in week["excuses"] if e["merchant"] == merchant and e["note"]]
                nudge = ("nudge_repeat", {"merchant": merchant, "times": times, "excuses": excuses})
                nudged_merchants[merchant] = _week_key(today)
                state["nudged_merchants"] = nudged_merchants
            if nudge:
                notices.append((nudge[0], TITLES[nudge[0]], words(*nudge), nudge[1]))
                state["nudged_on"] = today.isoformat()

        # Weekly review: Sunday at the review hour.
        if now.weekday() == 6 and now.hour >= conf["weekly_review_hour"] and state.get("weekly_done") != _week_key(today):
            week = summarize(rows, week_start(today), today)
            spent = week["totals"]["weekly"]
            facts = {"spent": spent, "budget": conf["weekly_budget"], "over": spent > conf["weekly_budget"],
                     "over_by": round(max(0.0, spent - conf["weekly_budget"]), 2),
                     "apple_card_week": week["totals"]["apple_card"], "chase_week": week["totals"]["chase"],
                     "top_merchants": week["top_merchants"], "excuses": week["excuses"],
                     "repeated_excuses": _repeated(week["excuses"])}
            notices.append(("weekly", TITLES["weekly"], words("weekly", facts), facts))
            state["weekly_done"] = _week_key(today)

        # Monthly check: the 1st, after 9.
        last_month_end = today.replace(day=1) - timedelta(days=1)
        if today.day == 1 and now.hour >= 9 and not is_quiet and state.get("monthly_done") != _month_key(last_month_end):
            month = summarize(rows, last_month_end.replace(day=1), last_month_end)
            facts = {"month": last_month_end.strftime("%B"), "totals": month["totals"],
                     "top_merchants": month["top_merchants"], "budget": _budget_snapshot_summary()}
            notices.append(("monthly", TITLES["monthly"], words("monthly", facts), facts))
            state["monthly_done"] = _month_key(last_month_end)

        _save(state)
    return notices


def _repeated(excuses: list[dict[str, Any]]) -> list[str]:
    counts = Counter(e["note"].strip().lower() for e in excuses if e.get("note"))
    return [note for note, n in counts.items() if n >= 3]


def _week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def _budget_snapshot_summary() -> dict[str, Any]:
    """The bits of Mooney's budget snapshot the monthly check uses."""
    from integrations import mooney
    try:
        snapshot = mooney.budget().get("budget") or {}
    except Exception:  # noqa: BLE001 - the check still runs without it
        return {}
    keep = ("buckets", "bills", "subscriptions", "cards", "paySchedule", "weeklyBudget")
    return {k: snapshot[k] for k in keep if k in snapshot}


def record(kind: str, title: str, body: str, *, sent: bool) -> None:
    """Keep the last 200 notices (the dry-run log Nick reviews)."""
    with _lock:
        state = _load()
        log_entries = state.get("log", [])
        log_entries.append({"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, "title": title,
                            "body": body, "sent": sent})
        state["log"] = log_entries[-200:]
        _save(state)


def recent_log(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        return _load().get("log", [])[-limit:]


# --- Tools -----------------------------------------------------------------------

def _period(period: str, today: date) -> tuple[date, date, str]:
    period = (period or "week").lower().replace(" ", "_")
    if period == "last_week":
        start = week_start(today) - timedelta(days=7)
        return start, start + timedelta(days=6), "last week"
    if period == "month":
        return today.replace(day=1), today, "this month"
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, end.strftime("%B")
    return week_start(today), today, "this week"


def spending_summary(period: str = "week", category: str = "", *, today: date | None = None,
                     rows: list[dict[str, Any]] | None = None) -> str:
    today = today or date.today()
    start, end, label = _period(period, today)
    rows = rows if rows is not None else _fetch(start)
    if category:
        needle = category.lower()
        rows = [r for r in rows if needle in r["category"].lower() or needle in r["merchant"].lower()]
    s = summarize(rows, start, end)
    conf = settings()
    lines = [f"Spending {label} ({s['start']} to {s['end']}), net of reimbursements:"]
    for role in ROLES:
        if s["totals"][role] or role == "weekly":
            extra = f" of ${conf['weekly_budget']:.0f}" if role == "weekly" and period in ("week", "last_week", "") else ""
            lines.append(f"- {ROLE_NAMES[role]}: ${s['totals'][role]:.2f}{extra}")
    if s["top_merchants"]:
        lines.append("Top: " + ", ".join(f"{m} ${v:.2f}" for m, v in s["top_merchants"]))
    if s["excuses"]:
        lines.append("Noted: " + "; ".join(f"{e['merchant']} ${e['amount']:.2f} ({e['kind']}: {e['note']})"
                                           for e in s["excuses"][:6]))
    return "\n".join(lines)


_KINDS = {"reimbursed", "excused", "split", "mistake"}


def explain_transaction(description: str, kind: str = "excused", amount_back: float | None = None,
                        merchant: str = "", amount: float | None = None, days: int = 14,
                        remember: str = "", *, today: date | None = None,
                        rows: list[dict[str, Any]] | None = None) -> str:
    """Nick argues a charge: find it, annotate it in Mooney, maybe remember a rule."""
    from integrations import mooney
    if kind not in _KINDS:
        return f"kind must be one of {', '.join(sorted(_KINDS))}."
    today = today or date.today()
    since = today - timedelta(days=max(1, min(days, 90)))
    rows = rows if rows is not None else _fetch(since)
    rows = [r for r in rows if r["date"] >= since.isoformat()]
    words = [w for w in re.findall(r"[a-z0-9']+", f"{merchant} {description}".lower()) if len(w) > 2]

    def score(r: dict[str, Any]) -> float:
        name = r["merchant"].lower()
        s = sum(2.0 for w in words if w in name)
        if amount is not None:
            s += 3.0 if abs(r["gross"] - amount) < 0.01 else (1.0 if abs(r["gross"] - amount) < 2 else -1.0)
        return s

    ranked = sorted(rows, key=lambda r: (score(r), r["date"]), reverse=True)
    if not ranked or score(ranked[0]) <= 0:
        return "No matching charge in the last {} days. Ask Nick for the merchant or amount.".format(days)
    best = ranked[0]
    ties = [r for r in ranked[1:4] if score(r) == score(best)]
    try:
        mooney.annotate(best["id"], kind, amount_back, description)
    except Exception as exc:  # noqa: BLE001
        return f"Found {best['merchant']} ${best['gross']:.2f} on {best['date']}, but Mooney didn't take the note: {exc}"
    if remember:
        with _lock:
            state = _load()
            state.setdefault("rules", []).append({"merchant": best["merchant"], "kind": kind, "note": remember,
                                                  "at": today.isoformat()})
            _save(state)
    same = sum(1 for r in rows if r.get("annotation") and (r["annotation"].get("note") or "").strip().lower()
               == description.strip().lower() and r["date"] >= week_start(today).isoformat())
    note = f" That's the {same + 1}{_ordinal(same + 1)} time this week for that excuse." if same >= 2 else ""
    alternatives = (" Other close matches: " + "; ".join(f"{r['merchant']} ${r['gross']:.2f} {r['date']}" for r in ties)
                    if ties else "")
    netted = {"reimbursed": "netted out", "split": "split", "excused": "kept, excuse noted",
              "mistake": "dropped"}[kind]
    return (f"Noted on {best['merchant']} ${best['gross']:.2f} ({best['date']}, {ROLE_NAMES[best['role']]}): "
            f"{netted}.{note}{alternatives}")


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def set_role(account_or_card: str, role: str) -> str:
    """Put an account (Plaid account id or name) or Shortcut card name in a budget."""
    from integrations import mooney
    if role not in ROLES:
        return f"role must be one of {', '.join(ROLES)}."
    key = None
    needle = account_or_card.strip().lower()
    for item in mooney.accounts():
        for account in item.get("accounts") or []:
            if needle in (account.get("id", "").lower(), (account.get("name") or "").lower(), account.get("mask") or ""):
                key = account["id"]
    key = key or f"card:{needle}"
    with _lock:
        state = _load()
        state.setdefault("roles", {})[key] = role
        _save(state)
    return f"{account_or_card} now counts toward {ROLE_NAMES[role]}."


def status() -> dict[str, Any]:
    """For the companion's Settings > Money."""
    with _lock:
        state = _load()
    return {"enabled": enabled(), "settings": settings(), "log": state.get("log", [])[-20:],
            "rules": state.get("rules", [])}


def record_apple_pay(*, merchant: str, amount: float, card: str, when: datetime | None = None,
                     entry_id: str = "") -> str:
    """An Apple Pay payment from the Shortcut, into Mooney's ledger."""
    from integrations import mooney
    when = when or datetime.now()
    entry_id = entry_id or f"{when:%Y%m%d%H%M%S}-{abs(hash((merchant, amount, card))) % 10**8}"
    return mooney.add_manual(entry_id=entry_id, date=when.date().isoformat(), amount=round(float(amount), 2),
                             merchant=merchant.strip()[:120], card=card.strip()[:60])
