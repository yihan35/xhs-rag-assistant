import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


def sample_note(note_id: str, title: str | None = None) -> dict:
    return {
        "note_id": note_id,
        "title": title or note_id,
        "content": f"{title or note_id} content",
        "content_parts": {},
        "tags": [],
        "cover_url": "",
        "image_urls": [],
        "note_url": "",
        "likes": 0,
        "note_type": "image",
        "crawled_at": "2026-05-21T00:00:00+00:00",
    }


def sample_rich_note(note_id: str, title: str, body: str, media_text: str) -> dict:
    note = sample_note(note_id, title)
    note["content"] = f"{body}\n{media_text}"
    note["content_parts"] = {
        "body": body,
        "images": [{"ocr_text": media_text, "description": ""}],
        "video_transcript": "",
    }
    return note


class ArchiveSyncTests(unittest.TestCase):
    def test_sqlite_marks_missing_notes_archived_and_hides_them_by_default(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("keep"), user_id="user-1")
            store.mark_indexed("keep", "user-1")
            store.upsert(sample_note("gone"), user_id="user-1")
            store.mark_indexed("gone", "user-1")

            archived = store.mark_uncollected_missing("user-1", {"keep"})
            active_rows = store.all_notes(user_id="user-1")
            all_rows = store.all_notes(user_id="user-1", include_archived=True)
            store.close()

        self.assertEqual(archived, ["gone"])
        self.assertEqual([row["note_id"] for row in active_rows], ["keep"])
        by_id = {row["note_id"]: row for row in all_rows}
        self.assertEqual(by_id["keep"]["is_collected"], 1)
        self.assertEqual(by_id["keep"]["archived_at"], "")
        self.assertEqual(by_id["gone"]["is_collected"], 0)
        self.assertNotEqual(by_id["gone"]["archived_at"], "")
        self.assertEqual(by_id["gone"]["indexed"], 0)

    def test_sqlite_upsert_restores_archived_note_to_current_collection(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("note-1"), user_id="user-1")
            store.mark_uncollected_missing("user-1", set())

            store.upsert(sample_note("note-1", "restored"), user_id="user-1")
            row = store.all_notes(user_id="user-1")[0]
            store.close()

        self.assertEqual(row["note_id"], "note-1")
        self.assertEqual(row["is_collected"], 1)
        self.assertEqual(row["archived_at"], "")
        self.assertEqual(row["title"], "restored")

    def test_sqlite_tracks_unread_updates_and_marks_seen(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("note-1", "first"), user_id="user-1")

            self.assertEqual(store.count_updated("user-1"), 0)
            self.assertEqual(store.get_updated("user-1"), [])

            store.upsert(sample_note("note-1", "changed"), user_id="user-1")
            updated_rows = store.get_updated("user-1")

            self.assertEqual(store.count_updated("user-1"), 1)
            self.assertEqual([row["note_id"] for row in updated_rows], ["note-1"])
            self.assertNotEqual(updated_rows[0]["content_changed_at"], "")

            changed = store.mark_updates_seen("user-1", "note-1")

            self.assertEqual(changed, 1)
            self.assertEqual(store.count_updated("user-1"), 0)
            self.assertEqual(store.get_updated("user-1"), [])
            store.close()

    def test_sqlite_update_tracking_is_scoped_to_collected_user_notes(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("note-1", "first"), user_id="user-1")
            store.upsert(sample_note("note-1", "first"), user_id="user-2")
            store.upsert(sample_note("other", "first"), user_id="user-1")

            store.upsert(sample_note("note-1", "changed"), user_id="user-1")
            store.upsert(sample_note("note-1", "changed"), user_id="user-2")
            store.mark_uncollected_missing("user-2", set())

            self.assertEqual(store.count_updated("user-1"), 1)
            self.assertEqual(store.count_updated("user-2"), 0)
            self.assertEqual([row["note_id"] for row in store.get_updated("user-1")], ["note-1"])
            store.close()

    def test_sqlite_lightweight_text_check_baselines_new_notes(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))

            result = store.upsert_lightweight_text(sample_note("note-1", "first"), user_id="user-1")

            self.assertEqual(result, "new")
            self.assertEqual(store.count_updated("user-1"), 0)
            self.assertEqual(store.get_updated("user-1"), [])
            store.close()

    def test_sqlite_lightweight_text_check_marks_existing_text_updates_unread(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("note-1", "first"), user_id="user-1")

            result = store.upsert_lightweight_text(sample_note("note-1", "changed"), user_id="user-1")
            updated_rows = store.get_updated("user-1")

            self.assertEqual(result, "updated")
            self.assertEqual(store.count_updated("user-1"), 1)
            self.assertEqual(updated_rows[0]["title"], "changed")
            self.assertNotEqual(updated_rows[0]["text_update_hash"], updated_rows[0]["text_seen_hash"])
            self.assertEqual(updated_rows[0]["indexed"], 0)

            store.mark_updates_seen("user-1", "note-1")

            self.assertEqual(store.count_updated("user-1"), 0)
            store.close()

    def test_sqlite_lightweight_text_check_ignores_media_text_from_full_sync(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(
                sample_rich_note(
                    "note-1",
                    "same title",
                    "same body",
                    "[图片文字]: full sync OCR text",
                ),
                user_id="user-1",
            )

            result = store.upsert_lightweight_text(
                sample_note("note-1", "same title") | {
                    "content": "same body",
                    "content_parts": {"body": "same body", "images": [], "video_transcript": ""},
                },
                user_id="user-1",
            )

            self.assertEqual(result, "unchanged")
            self.assertEqual(store.count_updated("user-1"), 0)
            store.close()

    def test_sqlite_text_hash_backfill_uses_content_parts_body(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "notes.db"
            store = SQLiteStore(str(db_path))
            rich_note = sample_rich_note(
                "note-1",
                "same title",
                "same body",
                "[图片文字]: old OCR text",
            )
            store.upsert(rich_note, user_id="user-1")
            store.conn.execute("UPDATE notes SET text_update_hash = '', text_seen_hash = ''")
            store.conn.commit()
            store.close()

            store = SQLiteStore(str(db_path))
            result = store.upsert_lightweight_text(
                sample_note("note-1", "same title") | {
                    "content": "same body",
                    "content_parts": {"body": "same body", "images": [], "video_transcript": ""},
                },
                user_id="user-1",
            )

            self.assertEqual(result, "unchanged")
            self.assertEqual(store.count_updated("user-1"), 0)
            store.close()

    def test_sqlite_reset_text_update_baseline_clears_false_positive(self):
        from rag.storage.sqlite_store import SQLiteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteStore(str(Path(tmp) / "notes.db"))
            store.upsert(sample_note("note-1", "first"), user_id="user-1")
            store.upsert_lightweight_text(sample_note("note-1", "changed"), user_id="user-1")

            self.assertEqual(store.count_updated("user-1"), 1)

            reset_count = store.reset_text_update_baseline("user-1")

            self.assertEqual(reset_count, 1)
            self.assertEqual(store.count_updated("user-1"), 0)
            row = store.all_notes(user_id="user-1")[0]
            self.assertEqual(row["content_changed_at"], "")
            store.close()

    def test_note_store_archive_missing_deletes_archived_vectors(self):
        from rag.storage import NoteStore

        with tempfile.TemporaryDirectory() as tmp:
            store = NoteStore(db_path=str(Path(tmp) / "notes.db"), init_chroma=False)
            store.chroma = MagicMock()
            store.sqlite.upsert(sample_note("keep"), user_id="user-1")
            store.sqlite.mark_indexed("keep", "user-1")
            store.sqlite.upsert(sample_note("gone"), user_id="user-1")
            store.sqlite.mark_indexed("gone", "user-1")

            archived = store.archive_missing("user-1", {"keep"})
            store.close()

        self.assertEqual(archived, ["gone"])
        store.chroma.delete.assert_called_once_with(["gone"])


if __name__ == "__main__":
    unittest.main()
