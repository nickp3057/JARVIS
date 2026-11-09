"""Schwab (read-only): login handshake, token refresh, holdings, live prices."""
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import app.server as server
from app import client_tokens
from integrations import schwab

ENV = {"SCHWAB_APP_KEY": "key", "SCHWAB_APP_SECRET": "secret", "JARVIS_API_TOKEN": "admin",
       "SCHWAB_CALLBACK_URL": "https://nick-jarvis.example.ts.net/api/schwab/callback"}

ACCOUNTS = [{"securitiesAccount": {
    "currentBalances": {"cashBalance": 500.0, "liquidationValue": 2500.0},
    "positions": [
        {"instrument": {"symbol": "NVDA", "assetType": "EQUITY"}, "longQuantity": 10,
         "marketValue": 1800.0, "currentDayProfitLoss": 20.0, "averagePrice": 120.0},
        {"instrument": {"symbol": "SWVXX", "assetType": "MUTUAL_FUND"}, "longQuantity": 200,
         "marketValue": 200.0, "currentDayProfitLoss": 0.0, "averagePrice": 1.0},
    ]}}]


def response(payload, status=200):
    r = Mock(status_code=status, text=str(payload))
    r.json.return_value = payload
    return r


class SchwabTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for target, attr, name in ((schwab, "_TOKEN_FILE", "schwab_token.json"),
                                   (client_tokens, "_FILE", "client_tokens.json")):
            p = patch.object(target, attr, Path(tmp.name) / name)
            p.start()
            self.addCleanup(p.stop)
        env = patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        for key in ("SCHWAB_HUB_URL", "SCHWAB_HUB_SECRET", "SCHWAB_LOGIN_PAGE"):
            os.environ.pop(key, None)  # these tests are JARVIS's own login
        schwab._pending_logins.clear()
        schwab.unlink()

    def link(self) -> None:
        state = parse_qs(urlparse(schwab.login_url()).query)["state"][0]
        with patch.object(schwab.requests, "post", return_value=response(
                {"access_token": "a1", "refresh_token": "r1", "expires_in": 1800})):
            schwab.finish_login("C0.code@", state)

    def test_login_needs_the_state_jarvis_handed_out(self) -> None:
        url = schwab.login_url()
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["redirect_uri"], [ENV["SCHWAB_CALLBACK_URL"]])
        with self.assertRaises(PermissionError):
            schwab.finish_login("code", "forged")
        self.assertFalse(schwab.linked())
        self.link()
        self.assertTrue(schwab.linked())
        self.assertTrue(schwab.linked_event.is_set())  # the monitor fetches right away
        self.assertAlmostEqual(schwab.login_expires_at(), time.time() + 7 * 86400, delta=5)
        self.assertEqual(oct(schwab._TOKEN_FILE.stat().st_mode & 0o777), "0o600")

    def test_access_token_refreshes_but_login_clock_keeps_running(self) -> None:
        self.link()
        expires = schwab.login_expires_at()
        tokens = schwab._load_tokens()
        tokens["expires_at"] = time.time() + 10
        schwab._save_tokens(tokens)
        with patch.object(schwab.requests, "post", return_value=response(
                {"access_token": "a2", "refresh_token": "r2", "expires_in": 1800})) as post:
            self.assertEqual(schwab.access_token(), "a2")
            self.assertEqual(schwab.access_token(), "a2")  # cached now
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(schwab.login_expires_at(), expires)
        self.assertEqual(schwab.login_warning(expires - 3600), "soon")
        self.assertEqual(schwab.login_warning(expires + 1), "expired")
        self.assertIsNone(schwab.login_warning(expires - 3 * 86400))

    def test_revoked_refresh_token_means_log_in_again(self) -> None:
        self.link()
        tokens = schwab._load_tokens()
        tokens["expires_at"] = time.time() + 10
        schwab._save_tokens(tokens)
        refused = response({"error": "invalid_grant",
                            "error_description": "Refresh token is invalid, expired or revoked"}, 400)
        with patch.object(schwab.requests, "post", return_value=refused):
            with self.assertRaises(RuntimeError):
                schwab.access_token()
        self.assertFalse(schwab.linked())
        self.assertTrue(schwab.login_revoked())
        self.assertEqual(schwab.login_warning(), "expired")
        self.link()  # a fresh login clears it
        self.assertFalse(schwab.login_revoked())
        self.assertTrue(schwab.linked())

    def test_snapshot_folds_live_prices_into_holdings(self) -> None:
        self.link()
        with patch.object(schwab.requests, "get", return_value=response(ACCOUNTS)):
            schwab.refresh_accounts()
        self.assertEqual(schwab.streamed_symbols(), ["NVDA"])
        snap = schwab.snapshot()
        self.assertEqual((snap["value"], snap["cash"], snap["today_change"]), (2500.0, 500.0, 20.0))
        schwab._apply_quotes({"data": [{"service": "LEVELONE_EQUITIES",
                                        "content": [{"key": "NVDA", "3": 190.0}]}]})
        snap = schwab.snapshot()
        self.assertEqual(snap["value"], 2600.0)  # 10 x 190 replaces the 1800 Schwab last reported
        self.assertEqual(snap["today_change"], 120.0)  # Schwab's +20, plus 10 x (190 - 180) since
        self.assertEqual(snap["positions"][0]["symbol"], "NVDA")
        # Cost 10 x 120 + 200 x 1 = 1400; worth 1900 + 200 now.
        self.assertEqual((snap["total_gain"], snap["total_gain_pct"]), (700.0, 50.0))
        self.assertIn("up $120.00 today", schwab.describe())

    def test_old_stream_quote_cannot_override_newer_accounts_or_live_forever(self) -> None:
        with patch.object(schwab, "_get", return_value=ACCOUNTS), \
                patch.object(schwab.time, "time", return_value=100):
            schwab.refresh_accounts()
        with patch.object(schwab.time, "time", return_value=101):
            schwab._apply_quotes({"data": [{"service": "LEVELONE_EQUITIES",
                "content": [{"key": "NVDA", "3": 190.0}]}]})
            self.assertEqual(schwab.snapshot()["value"], 2600)
        with patch.object(schwab.time, "time", return_value=162):
            self.assertEqual(schwab.snapshot()["value"], 2500)
        with patch.object(schwab, "_get", return_value=ACCOUNTS), \
                patch.object(schwab.time, "time", return_value=110):
            schwab.refresh_accounts()
            self.assertEqual(schwab.snapshot()["value"], 2500)
            self.assertIsNone(schwab.snapshot()["positions"][0]["last"])

    def test_a_deposit_is_value_not_gain(self) -> None:
        self.link()
        with patch.object(schwab.requests, "get", return_value=response(ACCOUNTS)):
            schwab.refresh_accounts()
        before = schwab.snapshot()
        deposited = [{"securitiesAccount": {**ACCOUNTS[0]["securitiesAccount"], "currentBalances": {
            "cashBalance": 525.0, "liquidationValue": 2525.0}}}]
        with patch.object(schwab.requests, "get", return_value=response(deposited)):
            schwab.refresh_accounts()
        after = schwab.snapshot()
        self.assertEqual(after["value"] - before["value"], 25.0)
        self.assertEqual((after["today_change"], after["total_gain"]), (before["today_change"], before["total_gain"]))

    def test_yesterday_uses_the_last_two_full_days(self) -> None:
        self.link()
        with patch.object(schwab.requests, "get", return_value=response(ACCOUNTS)):
            schwab.refresh_accounts()
        ny = ZoneInfo("America/New_York")
        day = lambda d: int(datetime(2026, 10, d, 0, 0, tzinfo=ny).timestamp() * 1000)  # noqa: E731
        candles = {"candles": [{"datetime": day(23), "close": 170.0}, {"datetime": day(26), "close": 173.0},
                               {"datetime": day(27), "close": 180.0}]}  # today's partial candle
        with patch.object(schwab.requests, "get", return_value=response(candles)):
            schwab.refresh_yesterday(datetime(2026, 10, 27, 11, 0, tzinfo=ny))
        self.assertEqual(schwab.snapshot()["yesterday_change"], 30.0)

    def test_streamer_logs_in_subscribes_and_applies_quotes(self) -> None:
        import json
        import threading
        import websocket
        self.link()
        with patch.object(schwab.requests, "get", return_value=response(ACCOUNTS)):
            schwab.refresh_accounts()
        stop = threading.Event()
        sent = []
        messages = iter([
            {"response": [{"service": "ADMIN", "content": {"code": 0}}]},
            {"data": [{"service": "LEVELONE_EQUITIES", "content": [{"key": "NVDA", "3": 181.5}]}]},
        ])

        def recv():
            try:
                return json.dumps(next(messages))
            except StopIteration:
                stop.set()
                raise websocket.WebSocketTimeoutException()

        fake = Mock(recv=recv, send=lambda m: sent.append(json.loads(m)))
        prefs = {"streamerInfo": [{"streamerSocketUrl": "wss://x", "schwabClientCustomerId": "c",
                                   "schwabClientCorrelId": "k", "schwabClientChannel": "N1",
                                   "schwabClientFunctionId": "APIAPP"}]}
        with patch.object(schwab.requests, "get", return_value=response(prefs)), \
             patch.object(websocket, "create_connection", return_value=fake):
            schwab.stream_forever(stop)
        login, subs = (m["requests"][0] for m in sent)
        self.assertEqual((login["command"], login["parameters"]["Authorization"]), ("LOGIN", "a1"))
        self.assertEqual(subs["parameters"], {"keys": "NVDA", "fields": "0,3"})
        self.assertEqual(schwab.snapshot()["positions"][0]["last"], 181.5)
        self.assertFalse(schwab.snapshot()["streaming"])
        fake.close.assert_called_once()

    def test_endpoints(self) -> None:
        client = TestClient(server.app)
        self.addCleanup(client.close)
        tv = {"Authorization": "Bearer " + client_tokens.create("tv", ["status:read"])}
        self.assertFalse(client.get("/api/portfolio", headers=tv).json()["linked"])
        self.assertEqual(client.post("/api/schwab/login", headers=tv).status_code, 401)
        url = client.post("/api/schwab/login", headers={"Authorization": "Bearer admin"}).json()["url"]
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertEqual(client.get("/api/schwab/callback", params={"code": "x", "state": "nope"}).status_code, 403)
        with patch.object(schwab.requests, "post", return_value=response(
                {"access_token": "a1", "refresh_token": "r1", "expires_in": 1800})), \
             patch.object(server, "runtime"):
            page = client.get("/api/schwab/callback", params={"code": "x", "state": state})
        self.assertEqual(page.status_code, 200)
        self.assertIn("Linked", page.text)
        self.assertTrue(client.get("/api/portfolio", headers=tv).json()["linked"])

    def test_app_hands_over_the_caught_redirect(self) -> None:
        client = TestClient(server.app)
        self.addCleanup(client.close)
        admin = {"Authorization": "Bearer admin"}
        with patch.dict(os.environ, {"SCHWAB_CALLBACK_URL": "https://127.0.0.1:8182"}):
            body = client.post("/api/schwab/login", headers=admin).json()
        self.assertEqual(body["callback"], "https://127.0.0.1:8182")
        state = parse_qs(urlparse(body["url"]).query)["state"][0]
        self.assertEqual(client.post("/api/schwab/finish", headers=admin,
                                     json={"redirect_url": "https://127.0.0.1:8182/?x=1"}).status_code, 422)
        with patch.object(schwab.requests, "post", return_value=response(
                {"access_token": "a1", "refresh_token": "r1", "expires_in": 1800})) as post, \
             patch.object(server, "runtime"):
            done = client.post("/api/schwab/finish", headers=admin, json={
                "redirect_url": f"https://127.0.0.1:8182/?code=C0.abc%40&session=s&state={state}"})
        self.assertEqual(done.json(), {"linked": True})
        self.assertEqual(post.call_args.kwargs["data"]["code"], "C0.abc@")
        self.assertTrue(schwab.linked())



HUB = {"SCHWAB_HUB_URL": "https://schwab.example/api/token", "SCHWAB_HUB_SECRET": "s" * 40}


def lent(payload, status=200):
    r = response(payload, status)
    r.headers = {"Content-Type": "application/json"}
    return r


class BorrowedLoginTests(unittest.TestCase):
    """One Schwab login for JARVIS and Coopa: JARVIS borrows the Schwab hub's token."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(schwab, "_TOKEN_FILE", Path(tmp.name) / "schwab_token.json")
        p.start()
        self.addCleanup(p.stop)
        env = patch.dict(os.environ, {**HUB, "JARVIS_API_TOKEN": "admin"})
        env.start()
        self.addCleanup(env.stop)
        for key in ("SCHWAB_APP_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL", "SCHWAB_LOGIN_PAGE"):
            os.environ.pop(key, None)
        schwab.unlink()
        self.addCleanup(schwab.unlink)

    def test_borrows_the_token_and_asks_again_only_when_it_runs_low(self) -> None:
        now = time.time()
        good = lent({"access_token": "t1", "expires_at": now + 1500, "login_expires_at": now + 5 * 86400})
        with patch.object(schwab.requests, "get", return_value=good) as get:
            self.assertTrue(schwab.configured() and schwab.borrowing())
            self.assertEqual(schwab.access_token(), "t1")
            self.assertTrue(schwab.linked())
            self.assertIsNone(schwab.login_warning())
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.args[0], HUB["SCHWAB_HUB_URL"])
            self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + "s" * 40)
        schwab._borrowed["expires_at"] = now + 30
        with patch.object(schwab.requests, "get", return_value=lent(
                {"access_token": "t2", "expires_at": now + 1800, "login_expires_at": now + 5 * 86400})):
            self.assertEqual(schwab.access_token(), "t2")

    def test_coopa_logged_out_alerts_once_and_points_at_coopa(self) -> None:
        with patch.object(schwab.requests, "get", return_value=lent(
                {"error": "rejected", "login_required": True, "revoked": True}, 409)):
            self.assertFalse(schwab.linked())
            self.assertTrue(schwab.login_revoked())
            self.assertEqual(schwab.login_warning(), "expired")
            first = schwab.login_expires_at()
            schwab._borrowed["checked_at"] -= 120  # a later retry, still logged out
            self.assertEqual(schwab.login_expires_at(), first)  # same alert key: no repeat
            with self.assertRaises(RuntimeError):
                schwab.access_token()
        self.assertEqual(schwab.login_url(), "https://schwab.example/")
        self.assertIn("Schwab hub", schwab.describe())

    def test_coopa_unreachable_keeps_the_known_expiry(self) -> None:
        now = time.time()
        with patch.object(schwab.requests, "get", return_value=lent(
                {"access_token": "t1", "expires_at": now + 1500, "login_expires_at": now + 86400 * 3})):
            schwab.access_token()
        schwab._borrowed["expires_at"] = now
        with patch.object(schwab.requests, "get", side_effect=schwab.requests.ConnectionError("down")):
            self.assertTrue(schwab.linked())
            self.assertEqual(schwab.login_expires_at(), now + 86400 * 3)
            with self.assertRaises(RuntimeError):
                schwab.access_token()

    def test_app_login_opens_the_hub_in_safari(self) -> None:
        client = TestClient(server.app)
        self.addCleanup(client.close)
        with patch.dict(os.environ, {"SCHWAB_LOGIN_PAGE": "https://schwab.example/"}):
            body = client.post("/api/schwab/login", headers={"Authorization": "Bearer admin"}).json()
        self.assertEqual(body, {"url": "https://schwab.example/", "callback": "", "external": True})


if __name__ == "__main__":
    unittest.main()
