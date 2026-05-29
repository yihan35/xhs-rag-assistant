import unittest
from unittest.mock import MagicMock, patch


class ClassifyNoteTests(unittest.TestCase):

    def test_returns_category_when_api_returns_valid_name(self):
        """LLM 返回有效分类名时，直接返回该分类名。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "求职面经"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "字节后端一面复盘",
                "content": "面试官问了MySQL索引...",
                "tags": ["面经", "字节跳动"],
            }
            result = classify_note(note)

        self.assertEqual(result, "求职面经")

    def test_returns_empty_string_when_api_fails(self):
        """LLM 调用失败时 fallback 空字符串。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": "测试内容",
                "tags": [],
            }
            result = classify_note(note)

        self.assertEqual(result, "")

    def test_returns_empty_string_when_client_is_none(self):
        """zhipu_client 为 None 时 fallback 空字符串。"""
        with patch("rag.classifier.zhipu_client", None):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": "测试",
                "tags": [],
            }
            result = classify_note(note)

        self.assertEqual(result, "")

    def test_truncates_content_to_500_chars(self):
        """content 超过 500 字时只取前 500 字作为摘要。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "学习方法"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        long_content = "A" * 600
        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": long_content,
                "tags": [],
            }
            classify_note(note)

        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        self.assertIn("A" * 500, user_msg)
        self.assertNotIn("A" * 501, user_msg)


class ClassifyNotesTests(unittest.TestCase):

    def test_batch_returns_note_id_to_category_map(self):
        """批量分类返回 {note_id: category} 映射。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "好物推荐"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        notes = [
            {"note_id": "n1", "title": "T1", "content": "C1", "tags": []},
            {"note_id": "n2", "title": "T2", "content": "C2", "tags": []},
        ]
        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_notes
            result = classify_notes(notes)

        self.assertEqual(result, {"n1": "好物推荐", "n2": "好物推荐"})
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
