"""Charles Schwab, read-only: Nick's portfolio, live.

JARVIS's own Schwab developer app (``SCHWAB_APP_KEY`` / ``SCHWAB_APP_SECRET``,
callback ``SCHWAB_CALLBACK_URL``, registered as ``https://127.0.0.1:8182``).
Nick links it from the companion (Settings > Charles Schwab > Log in): Core
hands the app Schwab's login page, which the app opens in its own web view;
when Schwab redirects to the callback (nothing listens there) the app catches
the address and posts it to ``/api/schwab/finish``, and Core trades the code
for tokens. A callback on Core itself (``/api/schwab/callback``) works too if
the app is ever registered with Core's Tailscale HTTPS name. Schwab's
refresh token dies after 7 days whatever we do, so the runtime monitor asks
Nick to tap Log in again a day before; his password never touches Core.

Live: holdings and cash come from the accounts endpoint every
``SCHWAB_POLL_SECONDS`` (30); prices for every stock/ETF held come over
Schwab's streamer (LEVELONE_EQUITIES, a websocket) the moment they change,
so the value and today's change move tick by tick. Yesterday's change uses
daily candles for today's holdings, once a day. Nothing here can trade.
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

_API = "https://api.schwabapi.com"
_TOKEN_FILE = Path(__file__).resolve().parents[1] / "memory" / "schwab_token.json"
_REFRESH_LIFETIME_S = 7 * 24 * 3600
_MARKET_TZ = ZoneInfo("America/New_York")
_STREAMED = ("EQUITY", "ETF", "COLLECTIVE_INVESTMENT")
# LEVELONE_EQUITIES fields: 0 symbol, 3 last price, 12 previous close.
_FIELDS = "0,3,12"

_lock = threading.RLock()
# Set on a fresh login so the monitor fetches now, not after its wait.
linked_event = threading.Event()
_pending_logins: dict[str, float] = {}
_state: dict[str, Any] = {
    "accounts": None,       # parsed holdings (see _parse_accounts)
    "accounts_at": 0.0,
    "prices": {},           # symbol -> live last price
    "closes": {},           # symbol -> previous close
    "yesterday": None,      # {"date": "YYYY-MM-DD", "change": float}
    "streaming": False,
    "error": "",
}


def configured() -> bool:
    return all(os.getenv(k, "").strip() for k in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL"))


# --- Tokens ------------------------------------------------------------------

def _load_tokens() -> dict[str, Any] | None:
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        return data if data.get("refresh_token") else None
    except (OSError, ValueError, AttributeError):
        return None


def _save_tokens(data: dict[str, Any] | None) -> None:
    if data is None:
        _TOKEN_FILE.unlink(missing_ok=True)
        return
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(_TOKEN_FILE)


def login_expires_at() -> float | None:
    """When the 7-day refresh token dies (epoch seconds); None if not linked."""
    tokens = _load_tokens()
    return tokens["refresh_issued_at"] + _REFRESH_LIFETIME_S if tokens else None


def linked() -> bool:
    expires = login_expires_at()
    return expires is not None and expires > time.time()


def _token_request(form: dict[str, str]) -> dict[str, Any]:
    key, secret = os.getenv("SCHWAB_APP_KEY", "").strip(), os.getenv("SCHWAB_APP_SECRET", "").strip()
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    resp = requests.post(f"{_API}/v1/oauth/token", data=form, timeout=15,
                         headers={"Authorization": f"Basic {basic}"})
    if resp.status_code != 200:
        raise RuntimeError(f"Schwab token request failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def login_url() -> str:
    """Schwab's login page for the phone to open; the state ties the callback to it."""
    if not configured():
        raise RuntimeError("Schwab isn't set up on Core yet (SCHWAB_APP_KEY / SECRET / CALLBACK_URL).")
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for old in [s for s, t in _pending_logins.items() if t < now]:
            _pending_logins.pop(old)
        _pending_logins[state] = now + 600
    query = urlencode({"client_id": os.getenv("SCHWAB_APP_KEY", "").strip(),
                       "redirect_uri": os.getenv("SCHWAB_CALLBACK_URL", "").strip(), "state": state})
    return f"{_API}/v1/oauth/authorize?{query}"


def finish_login(code: str, state: str) -> None:
    """The callback: trade Schwab's one-time code for tokens."""
    with _lock:
        expires = _pending_logins.pop(state or "", 0)
    if expires < time.time():
        raise PermissionError("That Schwab login wasn't started from JARVIS, or it's over 10 minutes old.")
    data = _token_request({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": os.getenv("SCHWAB_CALLBACK_URL", "").strip()})
    now = time.time()
    _save_tokens({"access_token": data["access_token"], "refresh_token": data["refresh_token"],
                  "expires_at": now + float(data.get("expires_in", 1800)), "refresh_issued_at": now})
    with _lock:
        _state["error"] = ""
    linked_event.set()


def has_accounts() -> bool:
    with _lock:
        return _state["accounts"] is not None


def finish_from_redirect(redirect_url: str) -> None:
    """Where Schwab tried to send the browser (``<callback>?code=...&state=...``)."""
    query = parse_qs(urlparse(redirect_url).query)
    code, state = query.get("code", [""])[0], query.get("state", [""])[0]
    if not code:
        raise ValueError("That address has no Schwab code in it.")
    finish_login(code, state)


def unlink() -> None:
    _save_tokens(None)
    with _lock:
        _state.update(accounts=None, prices={}, closes={}, yesterday=None)


def access_token() -> str:
    """A live access token (they last 30 minutes), refreshed as needed."""
    with _lock:
        tokens = _load_tokens()
        if not tokens or not linked():
            raise RuntimeError("Schwab isn't linked. Log in from the companion app.")
        if tokens["expires_at"] - time.time() > 60:
            return tokens["access_token"]
        data = _token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        tokens.update(access_token=data["access_token"],
                      expires_at=time.time() + float(data.get("expires_in", 1800)))
        # The refresh token's 7 days run from login, not from each refresh.
        tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
        _save_tokens(tokens)
        return tokens["access_token"]


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    resp = requests.get(f"{_API}{path}", params=params, timeout=15,
                        headers={"Authorization": f"Bearer {access_token()}", "Accept": "application/json"})
    if resp.status_code != 200:
        raise RuntimeError(f"Schwab {path}: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


# --- Holdings ------------------------------------------------------------------

def _parse_accounts(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """Every account rolled into one: cash, value and positions by symbol."""
    cash = value = 0.0
    positions: dict[str, dict[str, Any]] = {}
    for entry in raw:
        account = entry.get("securitiesAccount", {})
        balances = account.get("currentBalances", {})
        cash += float(balances.get("cashBalance", 0) or 0) + float(balances.get("moneyMarketFund", 0) or 0)
        value += float(balances.get("liquidationValue", 0) or 0)
        for p in account.get("positions", []):
            instrument = p.get("instrument", {})
            symbol = instrument.get("symbol", "")
            quantity = float(p.get("longQuantity", 0) or 0) - float(p.get("shortQuantity", 0) or 0)
            if not symbol or not quantity:
                continue
            held = positions.setdefault(symbol, {
                "symbol": symbol, "type": instrument.get("assetType", ""), "quantity": 0.0,
                "value": 0.0, "day_change": 0.0, "cost": 0.0})
            held["quantity"] += quantity
            held["value"] += float(p.get("marketValue", 0) or 0)
            held["day_change"] += float(p.get("currentDayProfitLoss", 0) or 0)
            held["cost"] += float(p.get("averagePrice", 0) or 0) * quantity
    return {"cash": cash, "value": value, "positions": positions}


def refresh_accounts() -> None:
    parsed = _parse_accounts(_get("/trader/v1/accounts", {"fields": "positions"}))
    with _lock:
        _state.update(accounts=parsed, accounts_at=time.time(), error="")


def streamed_symbols() -> list[str]:
    with _lock:
        accounts = _state["accounts"]
        if not accounts:
            return []
        return sorted(s for s, p in accounts["positions"].items() if p["type"] in _STREAMED)


def refresh_yesterday(now: datetime | None = None) -> None:
    """What today's holdings did on the last full trading day (daily candles)."""
    today = (now or datetime.now(_MARKET_TZ)).date()
    with _lock:
        done = _state["yesterday"]
        positions = dict(_state["accounts"]["positions"]) if _state["accounts"] else {}
    if done and done["date"] == today.isoformat():
        return
    change = 0.0
    for symbol in streamed_symbols():
        candles = _get("/marketdata/v1/pricehistory", {
            "symbol": symbol, "periodType": "month", "period": 1,
            "frequencyType": "daily", "frequency": 1}).get("candles", [])
        closes = [c["close"] for c in candles
                  if datetime.fromtimestamp(c["datetime"] / 1000, _MARKET_TZ).date() < today]
        if len(closes) >= 2:
            change += positions[symbol]["quantity"] * (closes[-1] - closes[-2])
    with _lock:
        _state["yesterday"] = {"date": today.isoformat(), "change": change}


# --- Streamer (live prices) ------------------------------------------------------

def _apply_quotes(message: dict[str, Any]) -> None:
    with _lock:
        for block in message.get("data", []):
            if block.get("service") != "LEVELONE_EQUITIES":
                continue
            for quote in block.get("content", []):
                symbol = quote.get("key")
                if "3" in quote:
                    _state["prices"][symbol] = float(quote["3"])
                if "12" in quote:
                    _state["closes"][symbol] = float(quote["12"])


def stream_forever(stop: threading.Event) -> None:
    """Hold Schwab's streamer open and subscribed to what Nick holds.
    Reconnects with backoff; re-subscribes when the holdings change."""
    import websocket  # websocket-client

    backoff = 15.0
    while not stop.is_set():
        if not linked() or not streamed_symbols():
            stop.wait(30)
            continue
        ws = None
        try:
            info = _get("/trader/v1/userPreference")["streamerInfo"][0]
            ids = {"SchwabClientCustomerId": info["schwabClientCustomerId"],
                   "SchwabClientCorrelId": info["schwabClientCorrelId"]}
            ws = websocket.create_connection(info["streamerSocketUrl"], timeout=30)
            ws.send(json.dumps({"requests": [{
                "requestid": "0", "service": "ADMIN", "command": "LOGIN", **ids,
                "parameters": {"Authorization": access_token(),
                               "SchwabClientChannel": info["schwabClientChannel"],
                               "SchwabClientFunctionId": info["schwabClientFunctionId"]}}]}))
            subscribed: list[str] = []
            request_id = 1
            with _lock:
                _state["streaming"] = True
            backoff = 15.0
            while not stop.is_set():
                symbols = streamed_symbols()
                if symbols != subscribed:
                    ws.send(json.dumps({"requests": [{
                        "requestid": str(request_id), "service": "LEVELONE_EQUITIES", "command": "SUBS", **ids,
                        "parameters": {"keys": ",".join(symbols), "fields": _FIELDS}}]}))
                    subscribed, request_id = symbols, request_id + 1
                try:
                    message = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue  # quiet market; heartbeats keep it alive
                for response in message.get("response", []):
                    if response.get("content", {}).get("code", 0) not in (0, None):
                        raise RuntimeError(f"Schwab streamer: {response['content']}")
                _apply_quotes(message)
        except Exception as exc:  # noqa: BLE001 - reconnect whatever broke
            log.warning("Schwab streamer dropped: %s", exc)
            with _lock:
                _state["streaming"] = False
            stop.wait(backoff)
            backoff = min(backoff * 2, 300.0)
        finally:
            with _lock:
                _state["streaming"] = False
            if ws is not None:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass


def set_error(text: str) -> None:
    with _lock:
        _state["error"] = text


# --- Snapshot ---------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    """For /api/portfolio, the glance and the tool: value, today's and
    yesterday's change and cash, with live prices folded in."""
    out: dict[str, Any] = {"configured": configured(), "linked": linked(),
                           "login_expires_at": login_expires_at()}
    with _lock:
        accounts, prices, closes = _state["accounts"], dict(_state["prices"]), dict(_state["closes"])
        out.update(streaming=_state["streaming"], error=_state["error"], updated_at=_state["accounts_at"],
                   yesterday_change=_state["yesterday"]["change"] if _state["yesterday"] else None)
    if not accounts:
        return out
    value, today, positions = accounts["value"], 0.0, []
    for symbol, p in accounts["positions"].items():
        last = prices.get(symbol)
        live_value = p["quantity"] * last if last is not None else p["value"]
        value += live_value - p["value"]
        if last is not None and closes.get(symbol):
            change = p["quantity"] * (last - closes[symbol])
        else:
            change = p["day_change"]
        today += change
        positions.append({"symbol": symbol, "quantity": p["quantity"], "value": round(live_value, 2),
                          "today_change": round(change, 2), "last": last})
    positions.sort(key=lambda p: -abs(p["value"]))
    start = value - today
    out.update(value=round(value, 2), cash=round(accounts["cash"], 2), today_change=round(today, 2),
               today_pct=round(100 * today / start, 2) if start else 0.0, positions=positions[:12])
    return out


def describe() -> str:
    snap = snapshot()
    if not snap["configured"]:
        return "Schwab isn't connected to me yet, sir."
    if not snap["linked"]:
        return "My Schwab login has lapsed, sir. Tap Log in under Settings > Charles Schwab in the app."
    if "value" not in snap:
        return "I'm still fetching your Schwab accounts, sir." + (f" ({snap['error']})" if snap["error"] else "")
    sign = lambda n: ("up" if n >= 0 else "down") + f" ${abs(n):,.2f}"  # noqa: E731
    parts = [f"Portfolio ${snap['value']:,.2f}, {sign(snap['today_change'])} today ({snap['today_pct']:+.2f}%)"]
    if snap["yesterday_change"] is not None:
        parts.append(f"{sign(snap['yesterday_change'])} yesterday")
    parts.append(f"${snap['cash']:,.2f} in cash")
    movers = sorted(snap["positions"], key=lambda p: -abs(p["today_change"]))[:3]
    if movers:
        parts.append("biggest movers: " + ", ".join(f"{p['symbol']} {p['today_change']:+,.2f}" for p in movers))
    return "; ".join(parts) + "."


def login_warning(now: float | None = None) -> str | None:
    """"soon" within a day of the 7-day login running out, "expired" after; else None."""
    expires = login_expires_at()
    if expires is None:
        return None
    left = expires - (now or time.time())
    return "expired" if left <= 0 else "soon" if left < 24 * 3600 else None


def market_open(now: datetime | None = None) -> bool:
    """Regular or extended hours (4am-8pm ET, weekdays): when prices move."""
    now = now or datetime.now(_MARKET_TZ)
    return now.weekday() < 5 and 4 <= now.hour < 20


def expires_text(expires: float) -> str:
    return datetime.fromtimestamp(expires).strftime("%a %-I:%M %p")
