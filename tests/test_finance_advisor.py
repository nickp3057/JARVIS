"""The money advisor (brain/finance_advisor.py): rules, netting, arguing back,
privacy. Mooney and the model are faked; nothing leaves the test."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

import app.server as server
from brain import event_bus, finance_advisor, voice_trust
from integrations import mooney

ITEMS = [
    {"itemId": "i1", "institution": {"name": "MACU"}, "accounts": [{"id": "macu", "name": "Checking", "type": "depository"}]},
    {"itemId": "i2", "institution": {"name": "Chase"}, "accounts": [{"id": "chase", "name": "Prime Visa", "type": "credit"}]},
]


def txn(id, day, amount, merchant, account="macu", **extra):
    return {"id": id, "date": day, "amount": amount, "merchant": merchant, "accountId": account, "source": "plaid", **extra}


def words(kind, facts):
    return f"{kind}:{json.dumps(facts, sort_keys=True, default=str)}"


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = patch.object(finance_advisor, "_STATE_FILE", Path(self.tmp.name) / "finance.json")
        self.state.start()

    def tearDown(self):
        self.state.stop()
        self.tmp.cleanup()

    def rows(self, txns):
        return finance_advisor.spending(txns, ITEMS)

    def test_roles_netting_and_what_is_not_spending(self):
        rows = self.rows([
            txn("a", "2026-11-17", 40, "Golf Club", annotation={"kind": "reimbursed", "amount": 30}),
            txn("b", "2026-11-17", 20, "Lunch", annotation={"kind": "split"}),
            txn("c", "2026-11-18", 9, "Oops", annotation={"kind": "mistake"}),
            txn("d", "2026-11-18", 500, "Card payment", category="LOAN_PAYMENTS"),
            txn("e", "2026-11-18", -125, "Transfer in"),
            txn("f", "2026-11-19", 60, "Amazon", account="chase"),
            {"id": "m1", "date": "2026-11-19", "amount": 75, "merchant": "Target", "source": "manual", "card": "Apple Card"},
            {"id": "m2", "date": "2026-11-17", "amount": 40, "merchant": "Golf Club", "source": "manual", "card": "MACU Debit",
             "duplicateOf": "a"},
        ])
        by = {r["id"]: r for r in rows}
        self.assertEqual(set(by), {"a", "b", "c", "f", "m1"})
        self.assertEqual((by["a"]["net"], by["b"]["net"], by["c"]["net"]), (10, 10, 0))
        self.assertEqual((by["a"]["role"], by["f"]["role"], by["m1"]["role"]), ("weekly", "chase", "apple_card"))
        s = finance_advisor.summarize(rows, date(2026, 11, 17), date(2026, 11, 23))
        self.assertEqual(s["totals"]["weekly"], 20)

    def test_first_pass_primes_then_heads_up_once_and_waits_out_quiet_hours(self):
        old = self.rows([txn("old", "2026-11-20", 300, "TV", account="chase")])
        self.assertEqual(finance_advisor.check(datetime(2026, 11, 20, 14), rows=old, words=words), [])
        rows = self.rows([txn("old", "2026-11-20", 300, "TV", account="chase"),
                          txn("new", "2026-11-20", 64.5, "Costco", account="chase")])
        finance_advisor.update_settings(quiet_start="22:00", quiet_end="08:00")
        self.assertEqual(finance_advisor.check(datetime(2026, 11, 20, 23), rows=rows, words=words), [])
        notices = finance_advisor.check(datetime(2026, 11, 21, 9, 30), rows=rows, words=words)
        self.assertEqual([n[0] for n in notices], ["heads_up"])
        self.assertIn("Costco", notices[0][2])
        self.assertEqual(finance_advisor.check(datetime(2026, 11, 21, 10), rows=rows, words=words), [])

    def test_pace_nudge_once_a_day_and_sunday_review_scolds_when_over(self):
        finance_advisor.check(datetime(2026, 11, 17, 8), rows=[], words=words)  # prime (Tuesday)
        rows = self.rows([txn(f"t{i}", "2026-11-17", 30, f"Shop {i}") for i in range(3)])
        first = finance_advisor.check(datetime(2026, 11, 17, 18), rows=rows, words=words)
        self.assertEqual([n[0] for n in first], ["nudge_pace"])
        self.assertEqual(finance_advisor.check(datetime(2026, 11, 17, 19), rows=rows, words=words), [])
        rows += self.rows([txn("t9", "2026-11-21", 49, "Diner")])
        sunday = finance_advisor.check(datetime(2026, 11, 22, 12, 5), rows=rows, words=words)
        weekly = [n for n in sunday if n[0] == "weekly"]
        self.assertEqual(len(weekly), 1)
        self.assertTrue(weekly[0][3]["over"])
        self.assertEqual(weekly[0][3]["over_by"], 14)
        self.assertEqual([n for n in finance_advisor.check(datetime(2026, 11, 22, 13), rows=rows, words=words)
                          if n[0] == "weekly"], [])

    def test_repeat_merchant_nudge(self):
        finance_advisor.check(datetime(2026, 11, 19, 8), rows=[], words=words)
        rows = self.rows([txn(f"m{i}", f"2026-11-1{7 + i}", 8, "McDonald's",
                              annotation={"kind": "excused", "note": "I was hungry"}) for i in range(3)])
        notices = finance_advisor.check(datetime(2026, 11, 19, 12), rows=rows, words=words)
        self.assertEqual([n[0] for n in notices], ["nudge_repeat"])
        self.assertEqual(notices[0][3]["excuses"], ["I was hungry"] * 3)

    def test_explain_transaction_annotates_and_notices_repeated_excuse(self):
        rows = self.rows([
            txn("g", "2026-11-21", 40, "Golf Club"),
            txn("x1", "2026-11-18", 9, "McDonald's", annotation={"kind": "excused", "note": "I was hungry"}),
            txn("x2", "2026-11-19", 9, "McDonald's", annotation={"kind": "excused", "note": "I was hungry"}),
            txn("x3", "2026-11-22", 11, "McDonald's"),
        ])
        with patch.object(mooney, "annotate") as annotate:
            reply = finance_advisor.explain_transaction("golf, got paid back", "reimbursed", 40, merchant="golf",
                                                        today=date(2026, 11, 22), rows=rows, remember="golf is usually split")
            annotate.assert_called_once_with("g", "reimbursed", 40, "golf, got paid back")
            self.assertIn("netted out", reply)
            again = finance_advisor.explain_transaction("I was hungry", "excused", merchant="mcdonalds", amount=11,
                                                        today=date(2026, 11, 22), rows=rows)
        self.assertIn("3rd time this week", again)
        self.assertEqual(finance_advisor.status()["rules"][0]["note"], "golf is usually split")

    def test_finance_tools_answer_only_nick(self):
        guest = voice_trust.guest("voiceprint mismatch", 0.1)
        unconfirmed_room = voice_trust.Speaker("nick", True, reason="voice ID off", room=True)
        matched_room = voice_trust.Speaker("nick", True, score=0.8, reason="match", room=True)
        with voice_trust.speaking(guest):
            self.assertIn("Refused", voice_trust.refusal("spending_summary"))
        with voice_trust.speaking(unconfirmed_room):
            self.assertIn("stays between us", voice_trust.refusal("spending_summary"))
        with voice_trust.speaking(matched_room):
            self.assertIsNone(voice_trust.refusal("spending_summary"))
        self.assertIsNone(voice_trust.refusal("explain_transaction"))  # the phone / desktop


class PrivacyAndEndpointTests(unittest.TestCase):
    def setUp(self):
        event_bus.reset_for_tests()
        self.env = patch.dict(os.environ, {"JARVIS_API_TOKEN": "admin", "MOONEY_URL": "http://nas:4001",
                                           "MOONEY_JARVIS_TOKEN": "mj"})
        self.env.start()
        self.client = TestClient(server.app, base_url="http://localhost")

    def tearDown(self):
        self.client.close()
        self.env.stop()
        event_bus.reset_for_tests()
        server._runtime = None

    def test_private_alert_hidden_from_client_tokens_and_glance(self):
        server._runtime = Mock()  # no real runtime (its monitors would reach for Mooney)
        event_bus.publish("finance", "Weekly money review", "Over by $14, sir.", source="money", severity="info",
                          context={"private": True})

        async def first_frame(client):
            request = Mock(is_disconnected=AsyncMock(return_value=False))
            response = await server.client_events(request, since=0, last_event_id=None, client=client)
            frames = response.body_iterator
            try:
                return await asyncio.wait_for(anext(frames), 5)
            finally:
                await frames.aclose()

        self.assertIn("Over by $14", asyncio.run(first_frame(None)))  # the companion
        tv = asyncio.run(first_frame("living-room-tv"))
        self.assertNotIn("Over by", tv)
        self.assertIn(": private", tv)
        with patch.object(server.status_hub, "_fetch_weather", return_value=None, create=True):
            glance = server.status_hub.glance_status()
        self.assertEqual(glance["alerts"], [])

    def test_apple_pay_shortcut_endpoint(self):
        class FakeRuntime:
            woke = 0

            def wake_finance(self):
                FakeRuntime.woke += 1
        server._runtime = FakeRuntime()
        with patch.object(mooney, "add_manual", return_value="manual-x") as add:
            ok = self.client.post("/api/money/transaction", headers={"Authorization": "Bearer admin"},
                                  json={"merchant": "Target", "amount": "$1,234.50", "card": "Apple Card", "id": "abc"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(add.call_args.kwargs["amount"], 1234.5)
        self.assertEqual(add.call_args.kwargs["entry_id"], "abc")
        self.assertEqual(FakeRuntime.woke, 1)
        bad = self.client.post("/api/money/transaction", headers={"Authorization": "Bearer admin"},
                               json={"merchant": "Target", "amount": "lots"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.post("/api/money/transaction", json={"merchant": "x", "amount": 1}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
