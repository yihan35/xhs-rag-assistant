import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from rag.storage.session_store import SessionStore


def _make_req(query, mode, session_id="sess-1", user_id="user-1", top_k=6):
    return SimpleNamespace(
        query=query, mode=mode, session_id=session_id,
        user_id=user_id, top_k=top_k,
    )


def _store(tmp):
    return SessionStore(str(Path(tmp) / "notes.db"))


FAKE_DOCS = [{"note_id": "n1", "title": "T", "content": "C",
              "note_url": "", "cover_url": "", "distance": 0.2}]


class HandleSearchTests(unittest.TestCase):

    def test_search_saves_docs_and_clears_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            # 预置一个有 messages 的旧状态
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": [{"role": "user", "content": "old"}],
                "last_query": "old query",
            })

            with patch("rag.session_handler.retrieve", return_value=FAKE_DOCS):
                from rag.session_handler import handle_search
                result = handle_search(_make_req("new query", "search"), store)

            state = store.get("sess-1")

        self.assertEqual(result["mode"], "search")
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["note_id"], "n1")
        self.assertEqual(state["messages"], [])           # messages 已清空
        self.assertEqual(state["last_query"], "new query")


class HandleStreamTests(unittest.TestCase):

    def test_retrieves_when_no_existing_docs(self):
        """首次分析：docs 为空，触发 retrieve"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)

            with patch("rag.session_handler.retrieve", return_value=FAKE_DOCS) as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("问题", "analysis"), store)
                list(gen)  # 消费 generator 触发 save

            state = store.get("sess-1")

        mock_retrieve.assert_called_once()
        self.assertEqual(sources[0]["note_id"], "n1")
        self.assertEqual(state["messages"][0]["role"], "user")
        self.assertEqual(state["messages"][1]["role"], "assistant")

    def test_reuses_docs_from_prior_search(self):
        """搜索后切换分析：复用 docs，不重新 retrieve"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": [], "last_query": "搜索词",
            })

            with patch("rag.session_handler.retrieve") as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("分析问题", "analysis"), store)
                list(gen)

            state = store.get("sess-1")

        mock_retrieve.assert_not_called()
        self.assertEqual(sources[0]["note_id"], "n1")
        # Verify DB state: messages should have user + assistant
        self.assertEqual(len(state["messages"]), 2)
        self.assertEqual(state["messages"][0]["role"], "user")
        self.assertEqual(state["messages"][1]["role"], "assistant")
        self.assertEqual(state["messages"][1]["content"], "回答")

    def test_followup_skips_retrieve_and_extends_messages(self):
        """追问：不 retrieve，直接追加消息"""
        prior_messages = [
            {"role": "user",      "content": "问题：xxx\n\n原文..."},
            {"role": "assistant", "content": "首次回答"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": prior_messages, "last_query": "首次问题",
            })

            with patch("rag.session_handler.retrieve") as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=True), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["追问回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("追问", "analysis"), store)
                list(gen)

            state = store.get("sess-1")

        mock_retrieve.assert_not_called()
        self.assertEqual(len(state["messages"]), 4)       # user, assistant, user, assistant
        self.assertEqual(state["messages"][2]["content"], "追问")
        self.assertEqual(state["messages"][3]["content"], "追问回答")

    def test_new_topic_clears_messages_and_retrieves(self):
        """新话题：is_followup=False，清空 messages，重新检索"""
        prior_messages = [
            {"role": "user",      "content": "问题：xxx\n\n原文..."},
            {"role": "assistant", "content": "旧回答"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": prior_messages, "last_query": "旧问题",
            })

            new_docs = [{"note_id": "n2", "title": "T2", "content": "C2",
                         "note_url": "", "cover_url": "", "distance": 0.3}]
            with patch("rag.session_handler.retrieve", return_value=new_docs) as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["新回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("全新话题", "analysis"), store)
                list(gen)

            state = store.get("sess-1")

        mock_retrieve.assert_called_once()
        self.assertEqual(sources[0]["note_id"], "n2")
        self.assertEqual(state["messages"][0]["role"], "user")   # 只有新的一轮
        self.assertEqual(len(state["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
