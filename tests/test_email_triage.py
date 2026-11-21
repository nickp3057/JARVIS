import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import email_rules, email_triage, knowledge_vault, standing_orders, subagents
from integrations import gmail
from tests.test_subagents import FakeClient


def _msg(mid, sender, subject, *, name="", starred=False, unsub=""):
    return {"id": mid, "thread_id": mid, "from_name": name, "from": sender, "subject": subject,
            "snippet": subject, "received": 0, "labels": ["INBOX"] + (["STARRED"] if starred else []),
            "starred": starred, "unsubscribe": unsub}


def _use(tid, name, **args):
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


class FakeGmail:
    def __init__(self, messages, labels=None):
        self.messages = {m["id"]: m for m in messages}
        self.labels = labels or {"Mission Emails/Elder Smith": "L1", "Mission Emails/Sister Ray": "L2", "Bills": "L3"}
        self.trashed, self.filed, self.untrashed = [], [], []
        self.unread_filed, self.created, self.colored, self.queries = [], [], [], []

    def _file(self, i, label, mark_read=True):
        self.filed.append((i, label))
        if not mark_read:
            self.unread_filed.append(i)
        self.messages.get(i, {}).get("labels", []).remove("INBOX") if "INBOX" in self.messages.get(i, {}).get("labels", []) else None
        return True

    def _create(self, name):
        self.created.append(name)
        self.labels[name] = f"N{len(self.created)}"
        return self.labels[name]

    def _list(self, q, n):
        self.queries.append(q)
        return list(self.messages)[:n]

    def patches(self):
        return [
            patch.object(gmail, "is_linked", return_value=True),
            patch.object(gmail, "available", return_value=True),
            patch.object(gmail, "user_labels", side_effect=lambda: dict(self.labels)),
            patch.object(gmail, "list_message_ids", side_effect=self._list),
            patch.object(gmail, "get_message", side_effect=lambda i: self.messages.get(i)),
            patch.object(gmail, "trash", side_effect=lambda i: self.trashed.append(i) or True),
            patch.object(gmail, "file_to_label", side_effect=self._file),
            patch.object(gmail, "create_label", side_effect=self._create),
            patch.object(gmail, "set_label_color", side_effect=lambda i, bg, fg: self.colored.append((i, bg)) or True),
            patch.object(gmail, "untrash", side_effect=lambda i: self.untrashed.append(i) or True),
        ]


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(self.tmp / "vault"), "EMAIL_NEVER_TRASH": "",
                                           "EMAIL_TRIAGE_DRY_RUN": "false", "EMAIL_UNSUB_THRESHOLD": "2"})
        self.env.start()
        self.files = [patch.object(email_triage, "_STATE_FILE", self.tmp / "state.json"),
                      patch.object(email_triage, "_LOG_FILE", self.tmp / "log.jsonl"),
                      patch.object(email_rules, "_FILE", self.tmp / "rules.json"),
                      patch.object(standing_orders, "all_orders", side_effect=lambda: list(self.orders)),
                      patch.dict(email_triage._colored_labels, clear=True)]
        self.orders = []
        for p in self.files:
            p.start()
        subagents.reset_for_tests()

    def tearDown(self):
        for p in self.files:
            p.stop()
        self.env.stop()

    def _run(self, fake, script, **kw):
        ps = fake.patches()
        for p in ps:
            p.start()
        self.client = FakeClient(script)
        try:
            return email_triage.triage(client=self.client, **kw)
        finally:
            for p in ps:
                p.stop()

    def test_trash_file_keep_and_guards(self):
        fake = FakeGmail([
            _msg("a", "deals@shop.com", "50% off", unsub="<https://shop.com/u>"),
            _msg("b", "elder.smith@mission.org", "Week 12", name="Elder Smith"),
            _msg("c", "bank@bank.com", "Statement ready"),
            _msg("d", "me@friend.com", "lunch?", starred=True),
            _msg("e", "x@y.com", "hmm"),
        ])
        script = [[
            _use("1", "trash_email", id="a", reason="promo"),
            _use("2", "file_email", id="b", folder="elder smith", reason="missionary"),
            _use("3", "trash_email", id="zzz", reason="not in batch"),
            _use("4", "file_email", id="c", folder="Bills", reason="not a missionary folder"),
            _use("5", "keep_email", id="c", reason="bank"),
        ], [{"type": "text", "text": "done"}]]
        summary = self._run(fake, script)
        self.assertEqual(fake.trashed, ["a"])
        self.assertEqual(fake.filed, [("b", "L1")])  # case-insensitive folder match; Bills refused
        self.assertIn("Sorted 5 emails: trashed 1, filed 1 missionary email, left 3 in your inbox.", summary)
        # Starred never reaches the model; email text is fenced as data.
        prompt = self.client.requests[0]["messages"][0]["content"]
        self.assertNotIn("lunch?", prompt)
        self.assertIn("<emails>", prompt)
        # Second pass: everything already processed.
        self.assertIn("Nothing new", self._run(fake, []))

    def test_model_failure_leaves_mail_for_next_pass(self):
        fake = FakeGmail([_msg("a", "deals@shop.com", "50% off")])

        class Broken(FakeClient):
            def create(self, **kwargs):
                raise RuntimeError("overloaded")

        ps = fake.patches()
        for p in ps:
            p.start()
        try:
            summary = email_triage.triage(client=Broken([]))
            self.assertIn("next pass", summary)
            self.assertEqual(fake.trashed, [])
            summary = email_triage.triage(client=FakeClient([[_use("1", "trash_email", id="a", reason="promo")],
                                                             [{"type": "text", "text": "ok"}]]))
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(fake.trashed, ["a"])

    def test_protected_sender_is_never_trashed(self):
        os.environ["EMAIL_NEVER_TRASH"] = "school.edu"
        fake = FakeGmail([_msg("a", "prof@cs.school.edu", "Sale on hoodies")])
        self._run(fake, [[_use("1", "trash_email", id="a", reason="promo")], [{"type": "text", "text": "ok"}]])
        self.assertEqual(fake.trashed, [])

    def test_learned_missionary_sender_skips_the_model(self):
        for i in range(2):
            fake = FakeGmail([_msg(f"m{i}", "sister.ray@mission.org", f"Week {i}")])
            self._run(fake, [[_use("1", "file_email", id=f"m{i}", folder="Sister Ray", reason="letter")],
                             [{"type": "text", "text": "ok"}]])
        fake = FakeGmail([_msg("m9", "sister.ray@mission.org", "Week 9")])
        self._run(fake, [])  # an empty script would raise if the model were asked
        self.assertEqual(fake.filed, [("m9", "L2")])

    def test_unsubscribe_list_note_and_clear(self):
        for i in range(2):
            fake = FakeGmail([_msg(f"p{i}", "news@spam.com", f"Deal {i}", name="Spammy", unsub="<mailto:u@spam.com>, <https://spam.com/u>")])
            summary = self._run(fake, [[_use("1", "trash_email", id=f"p{i}", reason="promo")], [{"type": "text", "text": "ok"}]])
        self.assertIn("New on your unsubscribe list: Spammy", summary)
        note = (self.tmp / "vault" / "Jarvis Memory" / "Email Unsubscribe List.md").read_text()
        self.assertIn("news@spam.com", note)
        self.assertIn("https://spam.com/u", note)
        self.assertIn("Spammy", email_triage.describe_unsubscribe_list())
        email_triage.mark_unsubscribed("spammy")
        self.assertIn("No senders", email_triage.describe_unsubscribe_list())

    def test_restore_untrashes_and_protects(self):
        fake = FakeGmail([_msg("a", "coach@team.com", "Practice moved")])
        self._run(fake, [[_use("1", "trash_email", id="a", reason="?")], [{"type": "text", "text": "ok"}]])
        with fake.patches()[-1]:
            reply = email_triage.restore("coach")
        self.assertEqual(fake.untrashed, ["a"])
        self.assertIn("won't trash coach@team.com", reply)
        fake.messages = {"b": _msg("b", "coach@team.com", "Sale")}
        self._run(fake, [[_use("1", "trash_email", id="b", reason="promo")], [{"type": "text", "text": "ok"}]])
        self.assertEqual(fake.trashed, ["a"])

    def test_dry_run_moves_nothing(self):
        os.environ["EMAIL_TRIAGE_DRY_RUN"] = "true"
        fake = FakeGmail([_msg("a", "deals@shop.com", "50% off")])
        summary = self._run(fake, [[_use("1", "trash_email", id="a", reason="promo")], [{"type": "text", "text": "ok"}]])
        self.assertEqual(fake.trashed, [])
        self.assertTrue(summary.startswith("Dry run"))
        self.assertIn("Trashed (1)", email_triage.report())

    def test_unlinked(self):
        with patch.object(gmail, "is_linked", return_value=False):
            self.assertIn("isn't linked", email_triage.triage())

    def test_model_call_does_not_hold_the_state_lock(self):
        import threading
        fake = FakeGmail([_msg("a", "deals@shop.com", "50% off")])
        free = []

        class Probe(FakeClient):
            def create(self, **kwargs):
                got = []

                def probe():
                    ok = email_triage._state_lock.acquire(timeout=1)
                    got.append(ok)
                    if ok:
                        email_triage._state_lock.release()

                t = threading.Thread(target=probe)
                t.start()
                t.join()
                free.append(bool(got and got[0]))
                return super().create(**kwargs)

        ps = fake.patches()
        for p in ps:
            p.start()
        try:
            email_triage.triage(client=Probe([[_use("1", "keep_email", id="a", reason="x")],
                                              [{"type": "text", "text": "ok"}]]))
        finally:
            for p in ps:
                p.stop()
        self.assertTrue(free and all(free))  # restore/unsubscribe could run meanwhile

    def test_sender_protected_mid_pass_is_not_trashed(self):
        fake = FakeGmail([_msg("a", "deals@shop.com", "50% off")])

        class Restorer(FakeClient):
            def create(self, **kwargs):
                with email_triage._state_lock:  # Nick restores this sender while the model thinks
                    state = email_triage._load()
                    state["protected"].append("deals@shop.com")
                    email_triage._save(state)
                return super().create(**kwargs)

        ps = fake.patches()
        for p in ps:
            p.start()
        try:
            email_triage.triage(client=Restorer([[_use("1", "trash_email", id="a", reason="promo")],
                                                 [{"type": "text", "text": "ok"}]]))
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(fake.trashed, [])
        self.assertIn("deals@shop.com", email_triage._load()["protected"])  # not overwritten by the pass

    def test_action_log_rotates(self):
        import json
        import time
        log = self.tmp / "log.jsonl"
        old = {"ts": time.time() - 200 * 86400, "id": "old", "action": "trash"}
        new = {"ts": time.time(), "id": "new", "action": "trash"}
        log.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\nnot json\n", encoding="utf-8")
        email_triage._rotate_log()
        kept = [json.loads(line)["id"] for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kept, ["new"])
        with patch.object(email_triage, "_LOG_MAX_LINES", 2):
            for i in range(5):
                email_triage._log_action({"ts": time.time(), "id": str(i)})
            email_triage._rotate_log()
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 2)


    # --- Filing rules ---------------------------------------------------------

    def _amazon_rule(self, folder="Amazon", **kw):
        rule, _ = email_rules.add_rule(folder, ["amazon.com"], ["shipped", "delivered", "return"], "red", **kw)
        return rule

    def test_rule_files_kept_mail_unread_and_colours_the_label(self):
        labels = {"Kinda Important": "K", "Kinda Important/Amazon": "KA"}
        fake = FakeGmail([_msg("s", "shipment-tracking@amazon.com", "Shipped 2 items: Office Supplies")], labels)
        self._run(fake, [[_use("1", "keep_email", id="s", reason="shipping")], [{"type": "text", "text": "ok"}]])
        self.assertEqual(fake.filed, [])  # no rule yet: kept, like tonight
        fake.messages.update({
            "d": _msg("d", "order-update@amazon.com", "Delivered 2 items"),
            "r": _msg("r", "return@amazon.com", "Your return is complete"),
            "p": _msg("p", "store-news@amazon.com", "Today's Big Deals"),
            "o": _msg("o", "x@notamazon.co", "Shipped: your thing"),
            "st": _msg("st", "order-update@amazon.com", "Delivered: starred one", starred=True),
        })
        self._amazon_rule()
        script = [[_use("1", "trash_email", id="p", reason="promo"), _use("2", "keep_email", id="o", reason="?")],
                  [{"type": "text", "text": "ok"}]]
        summary = self._run(fake, script)
        filed = sorted(i for i, _ in fake.filed)
        self.assertEqual(filed, ["d", "r", "s"])  # the already-kept "s" moves too; promo, lookalike, starred don't
        self.assertTrue(all(label == "KA" for _, label in fake.filed))  # "Amazon" found the nested label
        self.assertEqual(sorted(fake.unread_filed), ["d", "r", "s"])
        self.assertEqual(fake.colored, [("KA", "#fb4c2f")])
        self.assertEqual(fake.created, [])
        self.assertIn("filed 3 to Kinda Important/Amazon", summary)
        self.assertIn("from:(amazon.com)", " ".join(fake.queries))
        # The rule's mail never reached the model, and the colour isn't re-applied every pass.
        prompt = self.client.requests[0]["messages"][0]["content"]
        self.assertNotIn("Delivered 2 items", prompt)
        self._run(fake, [])
        self.assertEqual(len(fake.colored), 1)

    def test_rule_creates_a_missing_folder_but_not_in_dry_run(self):
        fake = FakeGmail([_msg("d", "order-update@amazon.com", "Delivered")], {})
        self._amazon_rule("Kinda Important/Amazon")
        os.environ["EMAIL_TRIAGE_DRY_RUN"] = "true"
        self._run(fake, [[_use("1", "keep_email", id="d", reason="x")], [{"type": "text", "text": "ok"}]])
        self.assertEqual((fake.created, fake.filed), ([], []))
        os.environ["EMAIL_TRIAGE_DRY_RUN"] = "false"
        self._run(fake, [])
        self.assertEqual(fake.created, ["Kinda Important/Amazon"])
        self.assertEqual(fake.filed, [("d", "N1")])

    def test_model_sees_rule_folders_and_email_orders(self):
        self.orders = [{"id": "1", "text": "When sorting email, keep anything from the school."},
                       {"id": "2", "text": "Use Celsius."}]
        labels = {"Kinda Important/Amazon": "KA"}
        fake = FakeGmail([_msg("a", "ship-confirm@amazon.com", "Out for delivery today")], labels)
        self._amazon_rule("Kinda Important/Amazon")
        self._run(fake, [[_use("1", "file_email", id="a", folder="Kinda Important/Amazon", reason="amazon delivery")],
                         [{"type": "text", "text": "ok"}]])
        prompt = self.client.requests[0]["messages"][0]["content"]
        self.assertIn("Kinda Important/Amazon: mail from amazon.com", prompt)
        self.assertIn("keep anything from the school", prompt)
        self.assertNotIn("Celsius", prompt)
        self.assertEqual(fake.filed, [("a", "KA")])
        self.assertEqual(fake.unread_filed, ["a"])
        # A model filing into a rule folder never teaches "always file this sender".
        fake.messages = {"b": _msg("b", "ship-confirm@amazon.com", "Your account")}
        self._run(fake, [[_use("1", "keep_email", id="b", reason="account")], [{"type": "text", "text": "ok"}]])
        self.assertEqual(len(fake.filed), 1)


class EmailRuleTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(email_rules, "_FILE", Path(tempfile.mkdtemp()) / "rules.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_matching(self):
        rule = {"from": ["amazon.com", "shipment-tracking@"], "subject": ["return", "delivered"]}
        hit = lambda sender, subject: email_rules.matches(rule, {"from": sender, "from_name": "", "subject": subject})
        self.assertTrue(hit("order-update@amazon.com", "Delivered: 2 items"))
        self.assertTrue(hit("a@marketplace.amazon.com", "Returned item refund"))
        self.assertTrue(hit("shipment-tracking@other.com", "delivered"))
        self.assertFalse(hit("order-update@amazon.com", "Undelivered? Big deals"))  # word start only
        self.assertFalse(hit("x@notamazon.com", "Delivered"))
        self.assertTrue(email_rules.matches({"from": [], "subject": ["invoice"]},
                                            {"from": "a@b.com", "subject": "Invoice 12"}))

    def test_add_validate_replace_remove(self):
        self.assertIsNone(email_rules.add_rule("Amazon")[0])  # would file everything
        self.assertIsNone(email_rules.add_rule("Amazon", ["amazon.com"], color="magenta")[0])
        first, _ = email_rules.add_rule(" Kinda Important / Amazon ", "amazon.com", ["Shipped"], "Red")
        self.assertEqual((first["folder"], first["subject"], first["color"]), ("Kinda Important/Amazon", ["shipped"], "red"))
        second, message = email_rules.add_rule("kinda important/amazon", ["amazon.com"], ["delivered"])
        self.assertTrue(message.startswith("Updated"))
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(email_rules.all_rules()), 1)
        self.assertIn("Kinda Important/Amazon", email_rules.list_rules())
        self.assertIn("Removed", email_rules.remove_rule("amazon"))
        self.assertIn("No email filing rules", email_rules.list_rules())

    def test_query_and_cli(self):
        rule = {"from": ["amazon.com", "@ups.com"], "subject": ["x"]}
        self.assertEqual(email_rules.gmail_query(rule, 3), "in:inbox newer_than:3d from:(amazon.com OR ups.com)")
        self.assertEqual(email_rules.gmail_query({"from": ["amazon"]}, 3), 'in:inbox newer_than:3d ("amazon")')
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(email_rules._cli(["add", "Bills", "--from", "bank.com", "--color", "green"]), 0)
        self.assertEqual(email_rules.all_rules()[0]["color"], "green")


class GmailHelperTests(unittest.TestCase):
    def test_summarize_message(self):
        raw = {"id": "1", "threadId": "t", "snippet": "hi", "internalDate": "1000", "labelIds": ["INBOX", "STARRED"],
               "payload": {"headers": [{"name": "From", "value": "Elder Smith <Elder.Smith@Mission.org>"},
                                       {"name": "Subject", "value": "Week 1"},
                                       {"name": "List-Unsubscribe", "value": "<mailto:a@b.c>, <https://x.y/u>"}]}}
        msg = gmail.summarize_message(raw)
        self.assertEqual((msg["from"], msg["from_name"], msg["subject"]), ("elder.smith@mission.org", "Elder Smith", "Week 1"))
        self.assertTrue(msg["starred"])
        self.assertEqual(msg["unsubscribe"], "https://x.y/u")

    def test_missionary_folders(self):
        with patch.dict(os.environ, {"EMAIL_MISSIONARY_PARENT": "Missionaries", "EMAIL_MISSIONARY_LABELS": "Elder Jones"}):
            folders = email_triage.missionary_folders({"Missionaries/Elder Smith": "1", "Elder Jones": "2", "Bills": "3"})
        self.assertEqual(folders, {"Elder Smith": "1", "Elder Jones": "2"})


if __name__ == "__main__":
    unittest.main()
