import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DebugExportTests(unittest.TestCase):
    def test_sqlite_store_persists_content_and_content_parts(self):
        from rag.storage.sqlite_store import SQLiteStore

        content_parts = {
            "body": "帖子正文",
            "images": [
                {
                    "url": "https://example.com/1.jpg",
                    "ocr_text": "图片里的文字",
                    "description": "图片描述",
                }
            ],
            "video_transcript": "",
        }

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(
                {
                    "note_id": "note-1",
                    "title": "测试笔记",
                    "content": "帖子正文\n[图片文字]: 图片里的文字",
                    "content_parts": content_parts,
                    "tags": ["tag"],
                    "cover_url": "",
                    "image_urls": ["https://example.com/1.jpg"],
                    "note_url": "https://example.com/note",
                    "likes": 3,
                    "note_type": "image",
                    "crawled_at": "2026-05-21T00:00:00+00:00",
                },
                user_id="user-1",
            )

            rows = store.all_notes(user_id="user-1")
            store.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "帖子正文\n[图片文字]: 图片里的文字")
        self.assertEqual(rows[0]["content_parts"], content_parts)

    def test_build_debug_html_shows_content_parts_and_chroma_document(self):
        from tools.export_notes_debug import build_debug_html

        note = {
            "note_id": "note-1",
            "user_id": "user-1",
            "title": "测试笔记",
            "content": "帖子正文\n[图片文字]: 图片里的文字",
            "content_parts": {
                "body": "帖子正文",
                "images": [
                    {
                        "url": "https://example.com/1.jpg",
                        "ocr_text": "图片里的文字",
                        "description": "图片描述",
                    }
                ],
                "video_transcript": "",
            },
            "image_urls": ["https://example.com/1.jpg"],
            "note_url": "https://example.com/note",
            "indexed": 1,
        }
        chroma_docs = {
            "note-1": {
                "document": "帖子正文\n[图片文字]: 图片里的文字",
                "metadata": {"title": "测试笔记"},
            }
        }

        html = build_debug_html([note], chroma_docs)

        self.assertIn("测试笔记", html)
        self.assertIn("图片里的文字", html)
        self.assertIn("图片描述", html)
        self.assertIn("最终写入向量库的 content", html)
        self.assertIn("ChromaDB document", html)
        self.assertIn("SQLite content 与 ChromaDB document 一致", html)

    def test_build_debug_html_marks_missing_ocr_and_chroma_mismatch(self):
        from tools.export_notes_debug import build_debug_html

        note = {
            "note_id": "note-2",
            "user_id": "user-1",
            "title": "只有标题",
            "content": "只有标题",
            "content_parts": {},
            "image_urls": [],
            "note_url": "",
            "indexed": 1,
            "is_collected": 0,
            "archived_at": "2026-05-21T00:00:00+00:00",
        }
        chroma_docs = {
            "note-2": {
                "document": "不同内容",
                "metadata": {"title": "只有标题"},
            }
        }

        html = build_debug_html([note], chroma_docs)

        self.assertIn("OCR 段数：0", html)
        self.assertIn("收藏状态：已归档", html)
        self.assertIn("SQLite content 与 ChromaDB document 不一致", html)

    def test_export_script_runs_as_command_from_repo_root(self):
        from rag.storage.sqlite_store import SQLiteStore

        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "notes.db"
            output_path = tmp_path / "notes_debug.html"
            store = SQLiteStore(str(db_path))
            store.upsert(
                {
                    "note_id": "note-3",
                    "title": "命令行测试",
                    "content": "命令行 content",
                    "content_parts": {},
                    "tags": [],
                    "cover_url": "",
                    "image_urls": [],
                    "note_url": "",
                    "likes": 0,
                    "note_type": "image",
                    "crawled_at": "2026-05-21T00:00:00+00:00",
                },
                user_id="user-1",
            )
            store.close()

            result = subprocess.run(
                [
                    sys.executable,
                    "tools/export_notes_debug.py",
                    "--db-path",
                    str(db_path),
                    "--chroma-path",
                    str(tmp_path / "missing_chroma"),
                    "--output",
                    str(output_path),
                ],
                cwd=project_root,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output_path.exists())
            self.assertIn("命令行测试", output_path.read_text(encoding="utf-8"))

    def test_sync_script_exports_debug_html_after_ingest(self):
        project_root = Path(__file__).resolve().parents[1]
        script = (project_root / "sync_xhs.sh").read_text(encoding="utf-8")

        self.assertIn("python tools/export_notes_debug.py", script)
        self.assertLess(
            script.index("python -m crawler.ingest"),
            script.index("python tools/export_notes_debug.py"),
        )


if __name__ == "__main__":
    unittest.main()
