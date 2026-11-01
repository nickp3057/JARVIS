import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from brain import history

class HistoryStorageTests(unittest.TestCase):
    def test_session_queries_use_index_and_connections_close(self):
        connections = []
        class Connection(sqlite3.Connection):
            closed = False
            def close(self):
                self.closed = True
                super().close()
        connect = sqlite3.connect
        def tracked(*args, **kwargs):
            conn = connect(*args, factory=Connection, **kwargs)
            connections.append(conn)
            return conn
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(history, "_DB_PATH", Path(directory) / "history.db"), \
                patch.object(history.sqlite3, "connect", side_effect=tracked):
            session = history.start_new_session()
            history.append_turn("question", "answer", session)
            self.assertEqual(len(history.load_session_messages(session)), 2)
            self.assertEqual(history.list_sessions()[0]["last_message_id"], 2)
            self.assertTrue(all(c.closed for c in connections))
            conn = history._connect()
            try:
                plan = conn.execute("EXPLAIN QUERY PLAN SELECT id FROM conversation WHERE session_id=? ORDER BY id DESC LIMIT 40", (session,)).fetchall()
                self.assertTrue(any("idx_conversation_session_id" in row[3] for row in plan))
            finally:
                conn.close()
