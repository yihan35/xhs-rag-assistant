import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class LlmIoLoggingTests(unittest.TestCase):

    def test_retrieve_logs_rewritten_query_and_recalled_titles(self):
        from rag.retriever import retrieve

        class FakeStore:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def search(self, query, user_id="", n_results=5):
                return [{
                    "note_id": f"{query}-id",
                    "title": f"标题 {query}",
                    "content": "正文",
                    "distance": 0.1,
                }]

        with patch.dict(os.environ, {"KNONOTE_LOG_LLM_IO": "1"}), \
             patch("rag.retriever.rewrite_query", return_value="改写后 query"), \
             patch("rag.retriever.expand_query", return_value=["扩展 query"]), \
             patch("rag.retriever.NoteStore", return_value=FakeStore()), \
             self.assertLogs("rag.retriever", level="INFO") as logs:
            docs = retrieve("原始 query", "user-1", top_k=2, mode="search")

        output = "\n".join(logs.output)
        self.assertEqual(len(docs), 2)
        self.assertIn("[llm-io][retrieve][search]", output)
        self.assertIn("original_query=原始 query", output)
        self.assertIn("rewritten_query=改写后 query", output)
        self.assertIn("扩展 query", output)
        self.assertIn("标题 改写后 query", output)
        self.assertIn("标题 扩展 query", output)

    def test_analyze_stream_with_history_logs_prompt_and_complete_output(self):
        from rag.chat import analyze_stream_with_history

        def chunk(content):
            delta = SimpleNamespace(content=content)
            choice = SimpleNamespace(delta=delta)
            return SimpleNamespace(choices=[choice])

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=MagicMock(return_value=[chunk("第一段"), chunk("第二段")])
                )
            )
        )

        messages = [{"role": "user", "content": "用户问题和收藏原文"}]
        with patch.dict(os.environ, {"KNONOTE_LOG_LLM_IO": "1"}), \
             patch("rag.chat.zhipu_client", fake_client), \
             self.assertLogs("rag.chat", level="INFO") as logs:
            chunks = list(analyze_stream_with_history(messages, system_prompt="系统提示"))

        output = "\n".join(logs.output)
        self.assertEqual(chunks, ["第一段", "第二段"])
        self.assertIn("[llm-io][analysis][analyze_stream_with_history][prompt]", output)
        self.assertIn('"role": "system"', output)
        self.assertIn("系统提示", output)
        self.assertIn("用户问题和收藏原文", output)
        self.assertIn("[llm-io][analysis][analyze_stream_with_history][output]", output)
        self.assertIn("第一段第二段", output)


if __name__ == "__main__":
    unittest.main()
