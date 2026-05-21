import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


class CrawlerHelperTests(unittest.TestCase):
    def test_load_cookies_reads_json_file(self):
        from crawler.cookies import load_cookies

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cookies.json"
            path.write_text(json.dumps({"web_session": "abc"}), encoding="utf-8")

            self.assertEqual(load_cookies(str(path)), {"web_session": "abc"})

    def test_cookies_to_str_formats_cookie_header(self):
        from crawler.cookies import cookies_to_str

        self.assertEqual(
            cookies_to_str({"a1": "one", "web_session": "two"}),
            "a1=one; web_session=two",
        )

    def test_parse_user_id_from_homepage_html(self):
        from crawler.cookies import parse_user_id_from_homepage_html

        html = '<script>{"userId":"640c4bcc000000002a0088a8"}</script>'

        self.assertEqual(
            parse_user_id_from_homepage_html(html),
            "640c4bcc000000002a0088a8",
        )

    def test_build_note_url_includes_xsec_when_present(self):
        from crawler.urls import build_note_url

        self.assertEqual(
            build_note_url("abc123", "token456"),
            "https://www.xiaohongshu.com/explore/abc123"
            "?xsec_token=token456&xsec_source=pc_collect",
        )

    def test_build_note_url_omits_xsec_when_missing(self):
        from crawler.urls import build_note_url

        self.assertEqual(
            build_note_url("abc123", ""),
            "https://www.xiaohongshu.com/explore/abc123",
        )

    def test_resolve_user_id_prefers_explicit_value_then_detected_value(self):
        from crawler.ingest import resolve_user_id

        self.assertEqual(resolve_user_id("explicit", "detected"), "explicit")
        self.assertEqual(resolve_user_id("", "detected"), "detected")

    def test_format_duration(self):
        from crawler.ingest import format_duration

        self.assertEqual(format_duration(42), "42s")
        self.assertEqual(format_duration(125), "2m 5s")

    def test_note_store_imports_from_rag_storage(self):
        from rag.storage import NoteStore

        self.assertEqual(NoteStore.__name__, "NoteStore")

    def test_runtime_paths_live_under_data(self):
        from crawler.get_my_cookies import COOKIES_FILE as EXPORT_COOKIES_FILE
        from crawler.ingest import COOKIES_FILE as INGEST_COOKIES_FILE
        from crawler.test_crawl import DEBUG_STATE_FILE, COOKIES_FILE as TEST_COOKIES_FILE

        self.assertTrue(str(EXPORT_COOKIES_FILE).endswith("data/cookies.json"))
        self.assertTrue(str(INGEST_COOKIES_FILE).endswith("data/cookies.json"))
        self.assertTrue(str(TEST_COOKIES_FILE).endswith("data/cookies.json"))
        self.assertTrue(str(DEBUG_STATE_FILE).endswith("data/debug_state.json"))

    def test_chroma_client_initializes_once_under_concurrency(self):
        import rag.storage.chroma_store as chroma_store

        class FakeClient:
            pass

        calls = []

        def fake_create_client(path):
            calls.append(path)
            time.sleep(0.01)
            return FakeClient()

        chroma_store._chroma_client = None
        threads = [
            threading.Thread(target=chroma_store._get_client, args=("data/chroma_db",))
            for _ in range(8)
        ]

        with patch.object(chroma_store, "_create_persistent_client", side_effect=fake_create_client):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(calls, ["data/chroma_db"])

    def test_metadata_store_does_not_initialize_chroma(self):
        from rag import storage

        with patch.object(storage, "ChromaStore", side_effect=AssertionError("should not init chroma")):
            with storage.metadata_store() as store:
                self.assertIsNotNone(store.sqlite)
                self.assertIsNone(store.chroma)


if __name__ == "__main__":
    unittest.main()
