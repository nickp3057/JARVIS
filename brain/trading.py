"""Real Schwab orders, each one approved by Nick.

Nick turned trading on 2026-11-10 ("enable jarvis trading on my charles
schwab"), with the guardrails agreed on 2026-10-27:

- **Every order waits for Nick.** ``stage`` works out the exact order (shares,
  price, dollars, account) from a live quote and opens a ``confirm_gate``
  proposal; nothing reaches Schwab until Nick taps Approve in the companion's
  chat, says "yes" as his next message, or "approve <code>". The model can
  never approve its own order (the gate only reads Nick's own message).
  Always ``propose``, never ``act``: an order is never "just done".
- **Caps:** ``TRADING_MAX_ORDER_USD`` (100) per order and
  ``TRADING_MAX_DAY_USD`` (250) per market day, buys and sells together,
  checked when staged and again the moment Nick approves.
- **Cash only.** A buy may spend ``trade_cash`` (a cash account's
  cashAvailableForTrading; a margin account's plain cash, never margin) plus
  sells still in flight at Schwab. No options, no shorting: stocks and ETFs
  (Schwab's ``EQUITY``) only, and a sell never exceeds what Schwab says is held.
- **Kill switch:** ``TRADING_ENABLED=false`` stops new orders being staged
  or placed (default on since Nick asked for it).

Order shape: a dollar amount worth at least one share becomes whole shares
(rounded down) as a DAY limit order 0.5% past the last price, so a jump can't
fill far from what Nick approved. Less than one share becomes a fractional
market order, which Schwab may refuse (whether the Trader API takes
fractional orders is unconfirmed; the hub README says to test with $1 first),
and the refusal is reported as is. Selling (nearly) everything sells the exact
quantity held.

Journal: ``memory/trades.json`` (every order placed, for the day cap and
``describe``).
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jarvis_logger import get_logger

log = get_logger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "memory" / "trades.json"
_ET = ZoneInfo("America/New_York")
_LIMIT_SLACK = 0.005
_TRADABLE = {"EQUITY", "COLLECTIVE_INVESTMENT", "ETF"}
_lock = threading.RLock()


def _num(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, "") or default))
    except ValueError:
        return default


def enabled() -> bool:
    return os.getenv("TRADING_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def max_order() -> float:
    return _num("TRADING_MAX_ORDER_USD", 100)


def max_day() -> float:
    return _num("TRADING_MAX_DAY_USD", 250)


def _today() -> str:
    return datetime.now(_ET).date().isoformat()


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(orders: list[dict[str, Any]]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(orders[-500:], indent=1), encoding="utf-8")
        tmp.replace(_FILE)
    except OSError as exc:
        log.error("Could not save the trade journal: %s", exc)


def spent_today() -> float:
    with _lock:
        return sum(o["dollars"] for o in _load() if o.get("day") == _today())


@dataclass
class Order:
    account: str          # last four digits
    symbol: str
    side: str             # "buy" | "sell"
    quantity: float
    price: float          # last price used to size it
    limit: float | None   # None = market (fractional)
    proposal: str | None  # investing proposal id, if any

    @property
    def dollars(self) -> float:
        """The most it can cost (buy) or roughly what it raises (sell)."""
        return round(self.quantity * (self.limit or self.price * (1 + _LIMIT_SLACK)), 2)

    def describe(self) -> str:
        shares = f"{self.quantity:g} share{'s' if self.quantity != 1 else ''}"
        verb = "buy" if self.side == "buy" else "sell"
        if self.limit is None:
            how = f"at the market price (about ${self.price:,.2f} each, fractional)"
        else:
            how = f"at ${self.limit:,.2f} {'or less' if self.side == 'buy' else 'or more'} (last ${self.price:,.2f})"
        tag = f" for {self.proposal}" if self.proposal else ""
        return (f"{verb} {shares} of {self.symbol} {how}, about ${self.quantity * self.price:,.2f}, "
                f"in account ...{self.account}{tag}")

    def body(self) -> dict[str, Any]:
        order: dict[str, Any] = {
            "orderType": "MARKET" if self.limit is None else "LIMIT",
            "session": "NORMAL", "duration": "DAY", "orderStrategyType": "SINGLE",
            "orderLegCollection": [{"instruction": self.side.upper(), "quantity": self.quantity,
                                    "instrument": {"symbol": self.symbol, "assetType": "EQUITY"}}],
        }
        if self.limit is not None:
            order["price"] = f"{self.limit:.2f}"
        return order


def size(symbol: str, side: str, dollars: float, price: float, held: float, account: str,
         proposal: str | None = None) -> Order:
    """Turn "$27 of SPCX" into an exact order."""
    shares = dollars / price
    if side == "sell" and shares >= held * 0.97:
        quantity = round(held, 6)     # (nearly) everything: sell exactly what's held
    elif shares >= 1:
        quantity = float(math.floor(shares))
    else:
        quantity = math.floor(shares * 10_000) / 10_000
    whole = quantity == int(quantity)
    limit = None
    if whole:
        slack = 1 + _LIMIT_SLACK if side == "buy" else 1 - _LIMIT_SLACK
        limit = round(price * slack, 2)
    return Order(account, symbol, side, quantity, price, limit, proposal)


def _in_flight_sells(account: dict[str, Any] | None = None) -> float:
    """Roughly what open Schwab sell orders will raise (counts toward a buy)."""
    from integrations import schwab
    try:
        sells = [o for o in schwab.open_orders(days=2) if o["side"] == "sell"]
    except Exception:  # noqa: BLE001
        return 0.0
    if not sells:
        return 0.0
    prices = schwab.quotes([o["symbol"] for o in sells])
    return sum(float(o.get("quantity") or 0) * prices.get(o["symbol"], {}).get("last", 0) for o in sells)


_staged_sells: list[tuple[float, float]] = []  # (when, roughly what it raises), staged not yet approved


def _staged_sells_value() -> float:
    with _lock:
        now = time.time()
        return sum(v for t, v in _staged_sells if now - t < 600)


def check(order: Order, account: dict[str, Any], *, extra_cash: float = 0.0) -> str | None:
    """Why this order may not go, or None."""
    if not enabled():
        return "trading is switched off (TRADING_ENABLED)"
    if order.quantity <= 0:
        return f"that's less than a ten-thousandth of a share of {order.symbol}"
    if order.dollars > max_order():
        return f"${order.dollars:,.2f} is over the ${max_order():,.2f} per-order cap (TRADING_MAX_ORDER_USD)"
    today = spent_today()
    if today + order.dollars > max_day():
        return (f"it would take today's orders to ${today + order.dollars:,.2f}, over the "
                f"${max_day():,.2f} daily cap (TRADING_MAX_DAY_USD)")
    held = float(account["positions"].get(order.symbol, {}).get("quantity", 0) or 0)
    if order.side == "sell" and order.quantity > held + 1e-9:
        return f"Schwab shows only {held:g} shares of {order.symbol} in ...{order.account}"
    if order.side == "buy":
        cash = float(account.get("trade_cash", account.get("cash", 0)) or 0) + extra_cash
        if order.dollars > cash + 0.005:
            return (f"it needs ${order.dollars:,.2f} and only ${cash:,.2f} is available without borrowing "
                    "(sell something first, or deposit)")
    return None


def _target(symbol: str, side: str, dollars: float) -> tuple[Order | None, dict[str, Any] | None, str]:
    """Live-sized order for the main account, or (None, None, why not)."""
    from brain import investing
    from integrations import schwab

    symbol = symbol.strip().upper()
    if side not in {"buy", "sell"}:
        return None, None, "side must be buy or sell"
    if not (0 < dollars < 1_000_000):
        return None, None, "the amount must be a positive number of dollars"
    main = investing.main_account()
    if main is None:
        return None, None, "Schwab hasn't sent the accounts yet"
    # Schwab's own figures, not investing's manual extras: only real shares can be sold.
    account = schwab.accounts_by_number().get(main["account"])
    if account is None:
        return None, None, f"account ...{main['account']} isn't in Schwab's data"
    quote = schwab.quotes([symbol]).get(symbol)
    if not quote:
        return None, None, f"Schwab has no live price for {symbol}"
    if str(quote.get("type") or "").upper() not in _TRADABLE:
        return None, None, f"{symbol} isn't a stock or ETF (Schwab calls it {quote.get('type') or 'unknown'})"
    held = float(account["positions"].get(symbol, {}).get("quantity", 0) or 0)
    if side == "sell" and held <= 0:
        return None, None, f"Schwab shows no {symbol} in ...{main['account']}"
    return size(symbol, side, dollars, quote["last"], held, main["account"]), account, ""


def stage(session_id: str | None, symbol: str, side: str, dollars: float, proposal: str | None = None) -> str:
    """Size the order and wait for Nick's approval. Returns the prompt for him."""
    from brain import confirm_gate

    if not enabled():
        return "Real orders are switched off (TRADING_ENABLED), sir. Nothing was staged."
    try:
        order, account, why = _target(symbol, side, dollars)
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not size %s %s", side, symbol)
        return f"I couldn't price that order, sir ({exc}). Nothing was staged."
    if order is None or account is None:
        return f"I can't stage that, sir: {why}."
    order.proposal = proposal
    in_flight = _in_flight_sells() + _staged_sells_value() if order.side == "buy" else 0.0
    problem = check(order, account, extra_cash=in_flight)
    if problem:
        return f"I won't stage that order, sir: {problem}. Nothing was sent to Schwab."
    if order.side == "sell":
        with _lock:
            _staged_sells.append((time.time(), order.quantity * order.price))
    note = ""
    if order.limit is None:
        note = (" It's under one share, so it goes as a fractional market order; Schwab's API may "
                "refuse those, and I'll say so if it does.")
    elif abs(order.quantity * order.price - dollars) > max(1.0, dollars * 0.1):
        note = f" Whole shares only, so that's ${order.quantity * order.price:,.2f} rather than ${dollars:,.2f}."
    return confirm_gate.propose(session_id, order.describe(), lambda: execute(order)) + note


def execute(order: Order) -> str:
    """Nick approved: re-check against fresh numbers, then send it."""
    from brain import investing
    from integrations import schwab

    try:
        schwab.refresh_accounts()
    except Exception as exc:  # noqa: BLE001
        log.warning("Account refresh before an order failed: %s", exc)
    account = schwab.accounts_by_number().get(order.account)
    if account is None:
        return f"Schwab didn't return account ...{order.account}, sir, so nothing was sent."
    with _lock:
        problem = check(order, account, extra_cash=_in_flight_sells() if order.side == "buy" else 0.0)
        if problem:
            return f"I stopped before sending it, sir: {problem}. Nothing went to Schwab."
        try:
            order_id = schwab.place_order(order.account, order.body())
        except Exception as exc:  # noqa: BLE001
            log.exception("Order refused: %s", order.describe())
            return f"Schwab refused it, sir: {exc}. Nothing was bought or sold."
        journal = _load()
        journal.append({"at": time.time(), "day": _today(), "id": order_id, "account": order.account,
                        "symbol": order.symbol, "side": order.side, "quantity": order.quantity,
                        "limit": order.limit, "price": order.price, "dollars": order.dollars,
                        "proposal": order.proposal})
        _save(journal)
    log.info("Placed Schwab order %s: %s", order_id, order.describe())
    if order.proposal:
        investing.mark_ordered(order.proposal, order_id)
    status = ""
    try:
        state = schwab.order_status(order.account, order_id)
        status = f" Schwab says {state['status'].lower().replace('_', ' ')}"
        if state.get("price"):
            status += f" at ${float(state['price']):,.2f}"
        if state["status"] == "REJECTED" and state.get("why"):
            status += f" ({state['why']})"
        status += "."
    except Exception:  # noqa: BLE001
        pass
    return f"Sent to Schwab, sir: {order.describe()}. Order {order_id}.{status}"


def describe() -> str:
    """One status line for investing_status."""
    if not enabled():
        return "Trading: off (TRADING_ENABLED), so I only propose."
    with _lock:
        today = [o for o in _load() if o.get("day") == _today()]
    line = (f"Trading: on. Every order waits for your Approve; caps ${max_order():,.0f} an order, "
            f"${max_day():,.0f} a day (${sum(o['dollars'] for o in today):,.2f} used today).")
    if today:
        line += " Today: " + "; ".join(f"{o['side']} {o['quantity']:g} {o['symbol']} (order {o['id']})" for o in today) + "."
    return line


def clear_for_tests() -> None:
    with _lock:
        _staged_sells.clear()
