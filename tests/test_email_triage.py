import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brain import email_triage, knowledge_vault, subagents
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

    def patches(self):
        return [
            patch.object(gmail, "is_linked", return_value=True),
            patch.object(gmail, "available", return_value=True),
            patch.object(gmail, "user_labels", side_effect=lambda: dict(self.labels)),
            patch.object(gmail, "list_message_ids", side_effect=lambda q, n: list(self.messages)[:n]),
            patch.object(gmail, "get_message", side_effect=lambda i: self.messages.get(i)),
            patch.object(gmail, "trash", side_effect=lambda i: self.trashed.append(i) or True),
            patch.object(gmail, "file_to_label", side_effect=lambda i, label: self.filed.append((i, label)) or True),
            patch.object(gmail, "untrash", side_effect=lambda i: self.untrashed.append(i) or True),
        ]


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(self.tmp / "vault"), "EMAIL_NEVER_TRASH": "",
                                           "EMAIL_TRIAGE_DRY_RUN": "false", "EMAIL_UNSUB_THRESHOLD": "2"})
        self.env.start()
        self.files = [patch.object(email_triage, "_STATE_FILE", self.tmp / "state.json"),
                      patch.object(email_triage, "_LOG_FILE", self.tmp / "log.jsonl")]
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
