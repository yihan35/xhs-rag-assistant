import tempfile
import unittest
from pathlib import Path
from rag.storage.session_store import SessionStore


def _store(tmp):
    return SessionStore(str(Path(tmp) / "notes.db"))


class SessionStoreTests(unittest.TestCase):

    def test_get_returns_none_for_unknown_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            self.assertIsNone(s.get("no-such-id"))

    def test_save_and_get_round_trip(self):
        state = {
            "docs":       [{"note_id": "n1", "title": "T", "content": "C", "distance": 0.1}],
            "messages":   [{"role": "user", "content": "hello"}],
            "last_query": "hello",
        }
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", state)
            got = s.get("sess-1")

        self.assertEqual(got["docs"][0]["note_id"], "n1")
        self.assertEqual(got["messages"][0]["content"], "hello")
        self.assertEqual(got["last_query"], "hello")

    def test_save_overwrites_existing_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", {"docs": None, "messages": [], "last_query": None})
            s.save("sess-1", "user-1", {"docs": [{"note_id": "n1"}], "messages": [], "last_query": "q"})
            got = s.get("sess-1")

        self.assertEqual(got["docs"][0]["note_id"], "n1")
        self.assertEqual(got["last_query"], "q")

    def test_delete_removes_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", {"docs": None, "messages": [], "last_query": None})
            s.delete("sess-1")
            self.assertIsNone(s.get("sess-1"))


if __name__ == "__main__":
    unittest.main()
