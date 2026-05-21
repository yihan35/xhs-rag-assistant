import unittest
from unittest.mock import MagicMock, patch

from rag.followup import is_followup


class FollowupTests(unittest.TestCase):

    def test_returns_false_when_no_last_query(self):
        state = {"docs": [], "messages": [], "last_query": None}
        self.assertFalse(is_followup("新问题", state))

    def test_returns_true_when_api_says_yes(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "yes"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.followup.zhipu_client", mock_client):
            state = {"last_query": "面试经验有哪些？"}
            result = is_followup("第一点能展开吗？", state)

        self.assertTrue(result)

    def test_returns_false_when_api_says_no(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "no"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.followup.zhipu_client", mock_client):
            state = {"last_query": "面试经验有哪些？"}
            result = is_followup("有哪些旅行攻略？", state)

        self.assertFalse(result)

    def test_returns_false_on_api_failure(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("network error")

        with patch("rag.followup.zhipu_client", mock_client):
            state = {"last_query": "面试经验"}
            result = is_followup("追问", state)

        self.assertFalse(result)

    def test_returns_false_when_client_is_none(self):
        with patch("rag.followup.zhipu_client", None):
            state = {"last_query": "some prior query"}
            self.assertFalse(is_followup("new question", state))


if __name__ == "__main__":
    unittest.main()
