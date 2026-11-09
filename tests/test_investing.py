import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from brain import investing, subagents
from integrations import schwab
from tests.test_subagents import FakeClient

ET = ZoneInfo("America/New_York")


def _use(tid, name, **args):
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


def _text(t):
    return {"type": "text", "text": t}


def _account(cash=50.0, **positions):
    pos = {s: {"symbol": s, "type": "EQUITY", "quantity": 1.0, "value": v[0], "day_change": v[1], "cost": v[0]}
           for s, v in positions.items()}
    return {"cash": cash, "value": cash + sum(v[0] for v in positions.values()), "positions": pos}


# Roughly Nick's account on 2026-11-09.
NICK = _account(50.0, VOO=(83.0, 1.8), NVDA=(62.0, -3.8), TSLA=(26.0, 1.3), AAPL=(20.0, 0.5), INTC=(12.0, 0.4))


class InvestingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(investing, "_STATE_FILE", Path(self.tmp.name) / "investing.json"),
            patch.object(schwab, "accounts_by_number", side_effect=lambda: {"1234": self.account}),
            patch.object(schwab, "configured", return_value=True),
            patch.object(schwab, "open_orders", return_value=[]),
        ]
        self.account = {k: (dict(v) if isinstance(v, dict) else v) for k, v in NICK.items()}
        for p in self.patches:
            p.start()
        subagents.reset_for_tests()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class MathTests(InvestingTestCase):
    def test_cash_goes_to_the_most_underweight_first(self):
        acct = _account(50.0, VOO=(100.0, 0), NVDA=(50.0, 0))  # total 200
        plan = investing.cash_plan({"VOO": 50, "NVDA": 50}, acct)
        # NVDA is $50 under its $100 target, VOO is at target: all $50 to NVDA.
        self.assertEqual(plan, {"NVDA": 50.0})

    def test_leftover_cash_follows_targets_and_cash_target_is_kept(self):
        acct = _account(100.0, VOO=(50.0, 0), NVDA=(50.0, 0))  # total 200
        plan = investing.cash_plan({"VOO": 45, "NVDA": 45, "CASH": 10}, acct)
        # Keep $20 cash; spend $80: each is $40 under target.
        self.assertEqual(plan, {"VOO": 40.0, "NVDA": 40.0})

    def test_trims_only_past_the_band(self):
        acct = _account(0.0, VOO=(60.0, 0), INTC=(40.0, 0))
        with patch.dict("os.environ", {"INVESTING_BAND_PCT": "5"}):
            self.assertEqual(investing.trims({"VOO": 70, "INTC": 30}, acct), {"INTC": 10.0})
            self.assertEqual(investing.trims({"VOO": 64, "INTC": 36}, acct), {})

    def test_day_drops(self):
        acct = _account(0.0, NVDA=(92.0, -8.0))
        self.assertAlmostEqual(investing.day_drops(acct)["NVDA"], -8.0)

    def test_render_mentions_targets_and_plan(self):
        text = investing.render_account({**NICK, "account": "1234", "note": ""}, {"VOO": 60, "NVDA": 40})
        self.assertIn("Cash plan", text)
        self.assertIn("TSLA", text)


class TargetsAndProposalsTests(InvestingTestCase):
    def test_weights_must_add_to_100(self):
        self.assertIn("not 100", investing.set_targets({"VOO": 50, "NVDA": 20}))
        self.assertIn("Targets set", investing.set_targets({"voo": 80, "NVDA": 20}))
        self.assertEqual(investing._load()["targets"], {"VOO": 80.0, "NVDA": 20.0})

    def test_accept_suggested(self):
        state = investing._load()
        state["suggested"] = {"weights": {"VOO": 70, "NVDA": 30}, "reasons": "x", "at": 0}
        investing._save(state)
        investing.set_targets(accept_suggested=True)
        state = investing._load()
        self.assertEqual(state["targets"], {"VOO": 70, "NVDA": 30})
        self.assertIsNone(state["suggested"])

    def test_suggest_job_records_weights_for_approval(self):
        client = FakeClient([[_use("1", "suggest_targets", weights={"VOO": 120}, reasons="x")],
                             [_use("2", "suggest_targets", weights={"VOO": 60, "NVDA": 25, "AAPL": 15},
                                   reasons="core plus favourites")],
                             [_text("Done.")]])
        summary = investing.suggest_targets_job(client=client)
        self.assertIn("VOO 60%", summary)
        self.assertEqual(investing._load()["suggested"]["weights"], {"VOO": 60, "NVDA": 25, "AAPL": 15})
        # The bad first try was bounced back to the model.
        self.assertIn("0-100", client.requests[1]["messages"][2]["content"][0]["content"])

    def test_review_proposes_within_limits_and_never_trades(self):
        investing.set_targets({"VOO": 60, "NVDA": 40})
        client = FakeClient([[
            _use("1", "propose_trade", symbol="NVDA", side="buy", dollars=40, reason="Underweight, market dip."),
            _use("2", "propose_trade", symbol="INTC", side="sell", dollars=500, reason="Too much."),
            _use("3", "propose_trade", symbol="AMD", side="buy", dollars=5, reason="New idea."),
        ], [_text("All good, sir.")]])
        summary = investing.review_job(client=client)
        pending = investing.pending_proposals()
        self.assertEqual([(p["symbol"], p["kind"]) for p in pending], [("NVDA", "rebalance"), ("AMD", "idea")])
        self.assertEqual([p["id"] for p in pending], ["P1", "P2"])
        self.assertIn("only holds", client.requests[1]["messages"][2]["content"][1]["content"])
        self.assertIn("Proposals:", summary)
        self.assertIn("not placing orders", investing.decide("P1", True))
        self.assertEqual(investing.decide("P1", True), "P1 is already approved, sir.")

    def test_approving_a_target_change_updates_targets(self):
        investing.set_targets({"VOO": 60, "NVDA": 40})
        client = FakeClient([[_use("1", "propose_target_change", symbol="NVDA", target_pct=30, reason="Trim risk.")],
                             [_text("ok")]])
        investing.review_job(client=client)
        investing.decide("P1", True)
        self.assertEqual(investing._load()["targets"]["NVDA"], 30)

    def test_emergency_sell_is_capped_and_only_proposed(self):
        self.account["positions"]["NVDA"]["day_change"] = -10.0
        client = FakeClient([[_use("1", "emergency_sell", fraction=0.9, reason="fraud")],
                             [_use("2", "emergency_sell", fraction=0.25, reason="SEC fraud charges.")],
                             [_text("Sold a quarter.")]])
        with patch.dict("os.environ", {"INVESTING_EMERGENCY_SELL_PCT": "25"}):
            summary = investing.emergency_job("NVDA", -14.0, client=client)
        self.assertIn("not placing orders", summary)
        (p,) = investing.pending_proposals()
        self.assertEqual((p["kind"], p["side"], p["dollars"]), ("emergency", "sell", 15.5))

    def test_emergency_hold(self):
        client = FakeClient([[_use("1", "no_action", reason="Whole market fell.")], [_text("Holding.")]])
        self.assertIn("holding", investing.emergency_job("NVDA", -9.0, client=client))
        self.assertEqual(investing.pending_proposals(), [])


class ScheduleTests(InvestingTestCase):
    def _at(self, *args):
        return datetime(*args, tzinfo=ET)

    def test_first_run_suggests_weights(self):
        jobs = investing.due_jobs(self._at(2026, 11, 9, 20, 0), investing._load(), NICK)
        self.assertEqual(jobs, [("suggest", None)])

    def test_review_drop_and_playground_on_monday(self):
        state = investing._load()
        state["targets"] = {"VOO": 100}
        acct = _account(0.0, NVDA=(90.0, -10.0))
        kinds = [k for k, _ in investing.due_jobs(self._at(2026, 11, 9, 11, 0), state, acct)]
        self.assertEqual(kinds, ["emergency", "review", "playground"])
        state["runs"] = {"review": "2026-11-09", "playground": "2026-11-09"}
        state["drop_checked"] = {"NVDA": "2026-11-09"}
        self.assertEqual(investing.due_jobs(self._at(2026, 11, 9, 11, 0), state, acct), [])
        # Before the review hour, weekends and after the close: nothing.
        state["runs"] = {}
        state["drop_checked"] = {}
        self.assertEqual([k for k, _ in investing.due_jobs(self._at(2026, 11, 9, 9, 45), state, acct)], ["emergency"])
        self.assertEqual(investing.due_jobs(self._at(2026, 11, 8, 11, 0), state, acct), [])

    def test_no_account_no_jobs(self):
        self.assertEqual(investing.due_jobs(self._at(2026, 11, 9, 11, 0), investing._load(), None), [])


class PlaygroundTests(InvestingTestCase):
    def test_deposits_and_benchmark(self):
        state = investing._load()
        with patch.dict("os.environ", {"PLAYGROUND_MONTHLY": "5"}):
            self.assertEqual(investing.accrue_playground(state, lambda s: 500.0, now=1000.0), 1)
            self.assertEqual(investing.accrue_playground(state, lambda s: 500.0, now=1000.0 + 86400), 0)
            self.assertEqual(investing.accrue_playground(state, lambda s: 500.0, now=1000.0 + 61 * 86400), 2)
        pg = state["playground"]
        self.assertEqual((pg["cash"], pg["deposits"]), (15.0, 15.0))
        self.assertAlmostEqual(pg["voo_qty"], 0.03)

    def test_paper_trades_cash_only(self):
        prices = {"VOO": 500.0, "RKLB": 20.0}
        with patch.object(schwab, "quotes", side_effect=lambda syms: {s: {"last": prices[s]} for s in syms if s in prices}), \
                patch.dict("os.environ", {"PLAYGROUND_MONTHLY": "5"}):
            client = FakeClient([
                [_use("1", "buy", symbol="RKLB", dollars=50, reason="too much")],
                [_use("2", "buy", symbol="RKLB", dollars=4, reason="Space launches growing.")],
                [_use("3", "sell", symbol="RKLB", fraction=0.5, reason="Take some off."),
                 _use("4", "journal", note="Bought Rocket Lab.")],
                [_text("Bought a little Rocket Lab.")],
            ])
            summary = investing.playground_job(client=client)
            pg = investing._load()["playground"]
        self.assertEqual(summary, "Bought a little Rocket Lab.")
        self.assertIn("You have $5.00", client.requests[1]["messages"][2]["content"][0]["content"])
        self.assertAlmostEqual(pg["positions"]["RKLB"]["qty"], 0.1)
        self.assertAlmostEqual(pg["cash"], 3.0)
        self.assertEqual(pg["journal"][0]["note"], "Bought Rocket Lab.")
        self.assertIn("Plain VOO", investing.describe_playground(pg, prices))


class SubagentServerToolTests(unittest.TestCase):
    def test_web_search_is_opt_in_and_pause_turn_resumes(self):
        class Resp:
            def __init__(self, content, stop):
                self.content, self.stop_reason = content, stop

        class Client:
            def __init__(self):
                self.requests = []
                self.messages = self
                self.replies = [Resp([_text("searching")], "pause_turn"), Resp([_text("Done.")], "end_turn")]

            def create(self, **kw):
                self.requests.append(kw)
                return self.replies.pop(0)

        client = Client()
        spec = subagents.SubagentSpec(name="t", system="s", tools=[], default_model="claude-sonnet-5-5",
                                      web_search_uses=3)
        result = subagents.run(spec, "go", client=client)
        self.assertEqual((result.ok, result.text, result.rounds), (True, "Done.", 2))
        self.assertEqual(client.requests[0]["tools"][0]["name"], "web_search")
        self.assertEqual(client.requests[0]["tools"][0]["max_uses"], 3)


if __name__ == "__main__":
    unittest.main()


class NotesAndOrdersTests(InvestingTestCase):
    def test_notes_and_open_orders_reach_the_agent(self):
        investing.add_note("I don't own Amazon.")
        with patch.object(schwab, "open_orders", return_value=[
                {"symbol": "SPCX", "side": "buy", "quantity": 0.35, "status": "QUEUED", "entered": "2026-11-08"}]):
            text = investing.render_account({**NICK, "account": "1234", "note": ""}, {})
        self.assertIn("I don't own Amazon.", text)
        self.assertIn("buy 0.35 SPCX (queued", text)

    def test_an_old_suggestion_is_redone_once(self):
        state = investing._load()
        state["suggested"] = {"weights": {"VOO": 100}, "reasons": "", "at": 0}  # v1
        now = datetime(2026, 11, 9, 20, 0, tzinfo=ET)
        self.assertEqual(investing.due_jobs(now, state, NICK), [("suggest", None)])
        state["runs"]["suggest@2"] = "2026-11-09"
        self.assertEqual(investing.due_jobs(now, state, NICK), [])
        state["suggested"]["v"] = 2
        state["runs"] = {}
        self.assertEqual(investing.due_jobs(now, state, NICK), [])


class ManualHoldingTests(InvestingTestCase):
    def test_reported_holding_counts_until_schwab_shows_it(self):
        self.account["cash"] = 50.25  # what the API reported on 2026-11-09
        with patch.object(schwab, "quotes", return_value={"SPCX": {"last": 140.0}}):
            self.assertIn("Noted", investing.record_holding("spcx", 51.87, 50.0))
            account = investing.main_account()
        self.assertAlmostEqual(account["cash"], 0.25)
        self.assertAlmostEqual(account["positions"]["SPCX"]["value"], 51.87, places=2)
        self.assertIn("Reported by Nick", account["note"])
        # Once the API lists it, the manual entry goes away.
        self.account["positions"]["SPCX"] = {"symbol": "SPCX", "type": "EQUITY", "quantity": 0.37,
                                             "value": 51.0, "day_change": 0, "cost": 50.0}
        self.account["cash"] = 0.25
        account = investing.main_account()
        self.assertEqual(investing._load()["manual"], {})
        self.assertAlmostEqual(account["cash"], 0.25)
