"""Real Schwab orders (brain/trading.py) and the chat Approve/Deny path."""
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from brain import confirm_gate, investing, trading
from integrations import schwab

SESSION = "s-trade"


def _schwab_time(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _account(cash=0.26, trade_cash=None, **held):
    """held: symbol -> (quantity, value)."""
    positions = {s: {"symbol": s, "type": "EQUITY", "quantity": q, "value": v, "day_change": 0.0, "cost": v}
                 for s, (q, v) in held.items()}
    return {"cash": cash, "trade_cash": cash if trade_cash is None else trade_cash,
            "value": cash + sum(v for _, v in held.values()), "positions": positions}


class TradingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.account = _account(0.26, SPCX=(2.0, 51.8), VOO=(0.13, 82.0))
        self.prices = {"SPCX": {"last": 25.9, "type": "EQUITY"}, "VOO": {"last": 610.0, "type": "EQUITY"},
                       "NVDA": {"last": 20.0, "type": "EQUITY"}, "SPY240C": {"last": 1.0, "type": "OPTION"}}
        self.placed = []
        self.open = []
        self.recent = []
        for p in (
            patch.object(trading, "_FILE", Path(tmp.name) / "trades.json"),
            patch.object(investing, "_STATE_FILE", Path(tmp.name) / "investing.json"),
            patch.object(schwab, "accounts_by_number", side_effect=lambda: {"1234": self.account}),
            patch.object(schwab, "quotes", side_effect=lambda syms: {s: self.prices[s] for s in syms if s in self.prices}),
            patch.object(schwab, "open_orders", side_effect=lambda days=7: list(self.open)),
            patch.object(schwab, "recent_orders", side_effect=lambda days=7: list(self.recent)),
            patch.object(schwab, "refresh_accounts", return_value=None),
            patch.object(schwab, "place_order", side_effect=self._place),
            patch.object(schwab, "order_status", return_value={"status": "WORKING", "filled": 0, "price": None, "why": ""}),
            patch.dict(os.environ, {"TRADING_ENABLED": "true", "TRADING_MAX_ORDER_USD": "100",
                                    "TRADING_MAX_DAY_USD": "250", "INVESTING_MAIN_ACCOUNT": ""}),
        ):
            p.start()
            self.addCleanup(p.stop)
        confirm_gate.clear_for_tests()
        trading.clear_for_tests()

    def _place(self, last4, body):
        self.placed.append((last4, body))
        return str(1000 + len(self.placed))

    def _code(self, reply):
        return confirm_gate.pending(SESSION)[-1]["code"]

    def test_nothing_reaches_schwab_until_nick_approves(self):
        reply = trading.stage(SESSION, "SPCX", "sell", 27.0)
        self.assertIn("sell 1 share of SPCX at $25.77 or more", reply)
        self.assertEqual(self.placed, [])
        code = self._code(reply)
        done = confirm_gate.conversation_action(f"approve {code}", SESSION)
        self.assertIn("Sent to Schwab", done)
        last4, body = self.placed[0]
        self.assertEqual(last4, "1234")
        self.assertEqual(body["orderType"], "LIMIT")
        self.assertEqual(body["price"], "25.77")
        self.assertEqual(body["orderLegCollection"][0]["instruction"], "SELL")
        self.assertEqual(body["orderLegCollection"][0]["quantity"], 1.0)
        self.assertAlmostEqual(trading.spent_today(), 25.77)

    def test_deny_button_drops_only_that_order(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        first = self._code(None)
        self.prices["SPCX"]["last"] = 26.0
        trading.stage(SESSION, "SPCX", "sell", 52.0)  # everything held
        second = self._code(None)
        reply = confirm_gate.conversation_action(f"deny {first}", SESSION)
        self.assertTrue(reply.startswith("Denied: sell 1 share"))
        self.assertEqual([a["code"] for a in confirm_gate.pending(SESSION)], [second])
        self.assertEqual(self.placed, [])

    def test_caps_kill_switch_and_asset_types(self):
        self.account["trade_cash"] = 10_000
        self.assertIn("per-order cap", trading.stage(SESSION, "NVDA", "buy", 150))
        self.assertIn("isn't a stock or ETF", trading.stage(SESSION, "SPY240C", "buy", 5))
        self.assertIn("Schwab shows no NVDA", trading.stage(SESSION, "NVDA", "sell", 5))
        with patch.dict(os.environ, {"TRADING_ENABLED": "false"}):
            self.assertIn("switched off", trading.stage(SESSION, "NVDA", "buy", 20))
        with patch.dict(os.environ, {"TRADING_MAX_DAY_USD": "50"}):
            trading.stage(SESSION, "NVDA", "buy", 40)
            confirm_gate.conversation_action("yes", SESSION)
            self.assertIn("daily cap", trading.stage(SESSION, "NVDA", "buy", 40))

    def test_approval_rechecks_and_kill_switch_stops_a_staged_order(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        with patch.dict(os.environ, {"TRADING_ENABLED": "false"}):
            reply = confirm_gate.conversation_action("yes", SESSION)
        self.assertIn("I stopped before sending it", reply)
        self.assertEqual(self.placed, [])

    def test_under_one_share_is_a_fractional_market_order(self):
        self.account["trade_cash"] = 100
        reply = trading.stage(SESSION, "VOO", "buy", 27.0)
        self.assertIn("buy 0.0442 shares of VOO at the market price", reply)
        self.assertIn("fractional market order", reply)
        confirm_gate.conversation_action("yes", SESSION)
        self.assertEqual(self.placed[0][1]["orderType"], "MARKET")
        self.assertNotIn("price", self.placed[0][1])

    def test_schwab_rejection_is_reported_first_and_not_counted(self):
        # 2026-11-10: order 1008619430771, 0.1914 SPCX, cancelled by the firm at once.
        self.account["positions"]["SPCX"]["quantity"] = 0.38
        self.prices["SPCX"]["last"] = 141.04
        state = investing._load()
        investing._add_proposal(state, kind="rebalance", symbol="SPCX", side="sell", dollars=27.0, reason="trim")
        investing._save(state)
        investing.stage_order("P1", SESSION)
        with patch.object(schwab, "order_status", return_value={
                "status": "REJECTED", "filled": 0, "price": None, "why": "No trades are currently allowed"}):
            reply = confirm_gate.conversation_action("yes", SESSION)
        self.assertTrue(reply.startswith('Schwab rejected it, sir: "No trades are currently allowed". Nothing was sold'))
        self.assertIn("fractional", reply)
        self.assertEqual(trading.spent_today(), 0)
        self.assertEqual(investing.find_proposal("P1")["status"], "pending")
        self.assertIn("rejected by Schwab", trading.describe())

    def test_approved_proposal_becomes_an_order_and_is_marked(self):
        state = investing._load()
        investing._add_proposal(state, kind="rebalance", symbol="SPCX", side="sell", dollars=27.0, reason="trim")
        investing._save(state)
        reply = investing.decide("P1", True, session_id=SESSION)
        self.assertIn("Approved P1.", reply)
        self.assertIn("for P1", reply)
        confirm_gate.conversation_action("yes", SESSION)
        p = investing.find_proposal("P1")
        self.assertEqual((p["status"], p["order_id"]), ("ordered", "1001"))
        self.assertIn("already sent to Schwab", investing.stage_order("P1", SESSION))


    # 2026-11-14 review: only cash that is really there, and a lost answer is never "refused".
    def test_buy_is_cash_only_but_counts_a_sell_in_flight(self):
        self.prices["VOO"]["last"] = 26.0
        self.assertIn("only $0.26 is available", trading.stage(SESSION, "VOO", "buy", 27.0))
        sell = {"symbol": "SPCX", "side": "sell", "quantity": 1, "filled": 0, "status": "WORKING",
                "account": "1234", "price": 25.77}
        self.open = [sell]  # an unfilled DAY limit sell may never fill: not cash
        self.assertIn("only $0.26 is available", trading.stage(SESSION, "VOO", "buy", 27.0))
        self.open = [{**sell, "filled": 1, "account": "9999"}]  # another account on the login
        self.assertIn("only $0.26 is available", trading.stage(SESSION, "VOO", "buy", 27.0))
        self.open = [{**sell, "filled": 1}]  # filled, this account: counts
        self.assertIn("buy 1 share of VOO", trading.stage(SESSION, "VOO", "buy", 27.0))

    def test_open_buys_reduce_the_cash(self):
        self.account["trade_cash"] = 30.0
        self.prices["VOO"]["last"] = 26.0
        self.open = [{"symbol": "NVDA", "side": "buy", "quantity": 1, "filled": 0, "status": "WORKING",
                      "account": "1234", "price": 20.0}]
        self.assertIn("only $10.00 is available", trading.stage(SESSION, "VOO", "buy", 27.0))

    def test_timeout_after_sending_is_unknown_not_refused(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        self.recent = [{"id": "777", "account": "1234", "symbol": "SPCX", "side": "sell", "quantity": 1.0,
                        "status": "WORKING", "entered_at": _schwab_time(time.time())}]
        with patch.object(schwab, "place_order", side_effect=schwab.OrderOutcomeUnknown("read timed out")):
            reply = confirm_gate.conversation_action("yes", SESSION)
        self.assertNotIn("refused", reply)
        self.assertIn("can't say whether it went through", reply)
        self.assertIn("777", reply)
        self.assertIn("check the Schwab app", reply)
        journal = trading._load()
        self.assertEqual((journal[0]["status"], journal[0]["id"]), ("unknown", "777"))
        self.assertAlmostEqual(trading.spent_today(), 25.77)  # counts toward the daily cap
        self.assertIn("outcome unknown", trading.describe())

    def test_timeout_with_no_match_still_journals(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        with patch.object(schwab, "place_order", side_effect=schwab.OrderOutcomeUnknown("reset")):
            reply = confirm_gate.conversation_action("yes", SESSION)
        self.assertIn("may still have gone through", reply)
        self.assertEqual(trading._load()[0]["status"], "unknown")
        self.assertAlmostEqual(trading.spent_today(), 25.77)

    def test_http_error_is_refused_and_not_counted(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        with patch.object(schwab, "place_order", side_effect=RuntimeError("Schwab refused the order: HTTP 400")):
            reply = confirm_gate.conversation_action("yes", SESSION)
        self.assertIn("Schwab refused it", reply)
        self.assertEqual(trading.spent_today(), 0)

    def test_missing_location_finds_the_order_id(self):
        trading.stage(SESSION, "SPCX", "sell", 27.0)
        self.recent = [
            {"id": "old", "account": "1234", "symbol": "SPCX", "side": "sell", "quantity": 1.0,
             "status": "FILLED", "entered_at": _schwab_time(time.time() - 3600)},
            {"id": "888", "account": "1234", "symbol": "SPCX", "side": "sell", "quantity": 1.0,
             "status": "WORKING", "entered_at": _schwab_time(time.time())},
        ]
        with patch.object(schwab, "place_order", return_value="") as place, \
                patch.object(schwab, "order_status", return_value={"status": "WORKING"}) as status:
            reply = confirm_gate.conversation_action("yes", SESSION)
        place.assert_called_once()
        status.assert_called_once_with("1234", "888")
        self.assertIn("Order 888", reply)
        self.assertEqual(trading._load()[0]["id"], "888")


class ApprovalsRouteTests(unittest.TestCase):
    def setUp(self):
        import app.server as server
        confirm_gate.clear_for_tests()
        env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "t"})
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(server.app)

    def test_lists_open_proposals_for_the_session(self):
        confirm_gate.propose("s1", "save the room as a new scene 'Movie'", lambda: "ok")
        resp = self.client.get("/api/approvals", params={"session_id": "s1"}, headers={"Authorization": "Bearer t"})
        self.assertEqual(resp.status_code, 200)
        (item,) = resp.json()["approvals"]
        self.assertEqual(item["description"], "save the room as a new scene 'Movie'")
        self.assertRegex(item["code"], r"^\d{4}$")
        self.assertEqual(self.client.get("/api/approvals", params={"session_id": "s2"},
                                         headers={"Authorization": "Bearer t"}).json(), {"approvals": []})
        self.assertEqual(self.client.get("/api/approvals", params={"session_id": "s1"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
