"""
rag/storage/sqlite_store.py
=======================
SQLite 元数据存储层。

职责：
  - 持久化笔记元数据（note_id / title / tags / cover / likes 等）
  - 去重（(note_id, user_id) 复合主键，支持多用户）
  - 追踪 indexed 状态（是否已写入 ChromaDB）
  - 为前端列表展示提供查询接口
"""

import hashlib
import json
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id           TEXT    NOT NULL,
    user_id           TEXT    NOT NULL DEFAULT '',
    title             TEXT    NOT NULL DEFAULT '',
    content           TEXT    NOT NULL DEFAULT '',
    content_parts     TEXT    NOT NULL DEFAULT '{}',
    tags              TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    cover_url         TEXT    NOT NULL DEFAULT '',
    image_urls        TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    note_url          TEXT    NOT NULL DEFAULT '',
    likes             INTEGER NOT NULL DEFAULT 0,
    note_type         TEXT    NOT NULL DEFAULT 'image',
    crawled_at        TEXT    NOT NULL DEFAULT '',
    indexed           INTEGER NOT NULL DEFAULT 0,      -- 0=未入向量库, 1=已入向量库
    is_collected      INTEGER NOT NULL DEFAULT 1,      -- 1=当前仍在收藏夹, 0=历史归档
    archived_at       TEXT    NOT NULL DEFAULT '',     -- 取消收藏后本地归档时间
    content_hash      TEXT    NOT NULL DEFAULT '',     -- MD5(title+content)，用于检测内容变化
    update_seen_hash  TEXT    NOT NULL DEFAULT '',     -- 用户已确认的最新内容版本
    note_published_at TEXT    NOT NULL DEFAULT '',     -- 帖子发布时间（小红书 API 返回的原始时间戳）
    content_changed_at TEXT   NOT NULL DEFAULT '',     -- 本地检测到内容变化的时间
    PRIMARY KEY (note_id, user_id)
);
"""


class SQLiteStore:
    def __init__(self, db_path: str = "data/notes.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # 检测旧 schema 并自动迁移
        self._migrate_schema_if_needed()

        self.conn.executescript(_SCHEMA)
        self._add_missing_columns()
        self.conn.commit()
        logger.debug(f"SQLite 已连接：{db_path}")

    def _migrate_schema_if_needed(self) -> None:
        """
        检测是否是旧的单字段主键 schema，若是则迁移为复合主键 (note_id, user_id)。
        判断依据：notes 表存在，且建表语句中包含 'TEXT PRIMARY KEY'（旧内联写法）。
        幂等：新 schema 不含该字符串，第二次运行不触发。
        """
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchone()

        if row is None:
            # 表不存在，后续 _SCHEMA 会创建
            return

        table_sql: str = row[0] or ""
        if "TEXT PRIMARY KEY" not in table_sql:
            # 已是新 schema 或无 notes 表，无需迁移
            return

        logger.warning("检测到旧 schema（单字段主键），开始迁移为复合主键 (note_id, user_id)…")
        self.conn.executescript("""
            BEGIN;
            CREATE TABLE notes_new (
                note_id     TEXT    NOT NULL,
                user_id     TEXT    NOT NULL DEFAULT '',
                title       TEXT    NOT NULL DEFAULT '',
                content     TEXT    NOT NULL DEFAULT '',
                content_parts TEXT  NOT NULL DEFAULT '{}',
                tags        TEXT    NOT NULL DEFAULT '[]',
                cover_url   TEXT    NOT NULL DEFAULT '',
                image_urls  TEXT    NOT NULL DEFAULT '[]',
                note_url    TEXT    NOT NULL DEFAULT '',
                likes       INTEGER NOT NULL DEFAULT 0,
                note_type   TEXT    NOT NULL DEFAULT 'image',
                crawled_at  TEXT    NOT NULL DEFAULT '',
                indexed     INTEGER NOT NULL DEFAULT 0,
                is_collected INTEGER NOT NULL DEFAULT 1,
                archived_at  TEXT    NOT NULL DEFAULT '',
                PRIMARY KEY (note_id, user_id)
            );
            INSERT INTO notes_new
              (note_id, user_id, title, tags, cover_url, image_urls,
               note_url, likes, note_type, crawled_at, indexed)
              SELECT note_id, user_id, title, tags, cover_url, image_urls,
                     note_url, likes, note_type, crawled_at, indexed
              FROM notes;
            DROP TABLE notes;
            ALTER TABLE notes_new RENAME TO notes;
            COMMIT;
        """)
        logger.info("Schema 迁移完成：已升级为复合主键 (note_id, user_id)")

    def _add_missing_columns(self) -> None:
        """Add columns introduced after the initial schema without dropping user data."""
        rows = self.conn.execute("PRAGMA table_info(notes)").fetchall()
        existing = {row["name"] for row in rows}
        additions = [
            ("content",            "TEXT    NOT NULL DEFAULT ''"),
            ("content_parts",      "TEXT    NOT NULL DEFAULT '{}'"),
            ("is_collected",       "INTEGER NOT NULL DEFAULT 1"),
            ("archived_at",        "TEXT    NOT NULL DEFAULT ''"),
            ("content_hash",       "TEXT    NOT NULL DEFAULT ''"),
            ("update_seen_hash",   "TEXT    NOT NULL DEFAULT ''"),
            ("note_published_at",  "TEXT    NOT NULL DEFAULT ''"),
            ("content_changed_at", "TEXT    NOT NULL DEFAULT ''"),
        ]
        for col, defn in additions:
            if col not in existing:
                self.conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {defn}")
                logger.info(f"SQLite schema 已增加 {col} 字段")
        self.conn.execute(
            """
            UPDATE notes
            SET update_seen_hash = content_hash
            WHERE update_seen_hash = '' AND content_hash != ''
            """
        )

    # ── 写入 ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(note: dict) -> str:
        """Compute a stable version hash for user-visible note fields."""
        payload = {
            "title": note.get("title", ""),
            "content": note.get("content", ""),
            "content_parts": note.get("content_parts", {}),
            "tags": note.get("tags", []),
            "cover_url": note.get("cover_url", ""),
            "image_urls": note.get("image_urls", []),
            "note_url": note.get("note_url", ""),
            "likes": int(note.get("likes", 0) or 0),
            "note_type": note.get("note_type", "image"),
            "note_published_at": note.get("note_published_at", ""),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    def upsert(self, note: dict, user_id: str) -> bool:
        """
        插入或更新一条笔记元数据。
        返回 True 表示新记录；False 表示已存在（已更新）。

        新增逻辑：
          - 每次写入时计算 content_hash
          - 若 hash 与已存记录不同，更新 content_changed_at（内容变化时间）
          - 内容变化的记录会被 is_indexed() 视为未索引，触发重新向量化
        """
        from datetime import datetime, timezone

        existing = self.conn.execute(
            """
            SELECT indexed, content_hash, update_seen_hash, content_changed_at
            FROM notes
            WHERE note_id = ? AND user_id = ?
            """,
            (note["note_id"], user_id),
        ).fetchone()

        new_hash = self._compute_hash(note)
        now = datetime.now(timezone.utc).isoformat()

        if existing is None:
            # 新记录
            old_indexed       = 0
            content_changed_at = ""
            update_seen_hash   = new_hash
        else:
            old_hash = existing["content_hash"] or ""
            if old_hash and old_hash != new_hash:
                # 内容发生变化：重置 indexed，记录变化时间，后续触发重新向量化
                old_indexed        = 0
                content_changed_at = now
                update_seen_hash   = existing["update_seen_hash"] or old_hash
                logger.info(
                    f"[{note['note_id']}] 内容已更新（hash 变化），将重新向量化"
                )
            else:
                old_indexed        = existing["indexed"]
                content_changed_at = existing["content_changed_at"] or ""
                update_seen_hash   = existing["update_seen_hash"] or new_hash

        self.conn.execute(
            """
            INSERT OR REPLACE INTO notes
              (note_id, user_id, title, content, content_parts, tags, cover_url, image_urls,
               note_url, likes, note_type, crawled_at, indexed,
               is_collected, archived_at, content_hash, update_seen_hash, note_published_at, content_changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note["note_id"],
                user_id,
                note.get("title", ""),
                note.get("content", ""),
                json.dumps(note.get("content_parts", {}), ensure_ascii=False),
                json.dumps(note.get("tags", []), ensure_ascii=False),
                note.get("cover_url", ""),
                json.dumps(note.get("image_urls", []), ensure_ascii=False),
                note.get("note_url", ""),
                int(note.get("likes", 0)),
                note.get("note_type", "image"),
                note.get("crawled_at", ""),
                old_indexed,
                1,
                "",
                new_hash,
                update_seen_hash,
                note.get("note_published_at", ""),
                content_changed_at,
            ),
        )
        self.conn.commit()
        is_new = existing is None
        logger.debug(f"[{note['note_id']}] SQLite {'INSERT' if is_new else 'UPDATE'}")
        return is_new

    def mark_indexed(self, note_id: str, user_id: str) -> None:
        """将 indexed 置为 1，表示已写入 ChromaDB。user_id 必填，防止跨用户误更新。"""
        self.conn.execute(
            "UPDATE notes SET indexed = 1 WHERE note_id = ? AND user_id = ?",
            (note_id, user_id),
        )
        self.conn.commit()

    def mark_uncollected_missing(self, user_id: str, current_note_ids: set[str]) -> list[str]:
        """
        将当前收藏列表中不存在的历史记录软归档。

        返回本次新归档的 note_id 列表。归档后 indexed 置 0，因为对应 ChromaDB
        向量会被删除，不再参与 RAG 检索。
        """
        from datetime import datetime, timezone

        current_note_ids = {nid for nid in current_note_ids if nid}
        if current_note_ids:
            placeholders = ",".join("?" * len(current_note_ids))
            rows = self.conn.execute(
                f"""
                SELECT note_id FROM notes
                WHERE user_id = ?
                  AND is_collected = 1
                  AND note_id NOT IN ({placeholders})
                ORDER BY crawled_at DESC
                """,
                (user_id, *current_note_ids),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT note_id FROM notes
                WHERE user_id = ? AND is_collected = 1
                ORDER BY crawled_at DESC
                """,
                (user_id,),
            ).fetchall()

        archived_ids = [row["note_id"] for row in rows]
        if not archived_ids:
            return []

        placeholders = ",".join("?" * len(archived_ids))
        archived_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            f"""
            UPDATE notes
            SET is_collected = 0,
                archived_at = ?,
                indexed = 0
            WHERE user_id = ?
              AND note_id IN ({placeholders})
            """,
            (archived_at, user_id, *archived_ids),
        )
        self.conn.commit()
        logger.info(f"已归档 {len(archived_ids)} 条已取消收藏的笔记")
        return archived_ids

    # ── 查询 ──────────────────────────────────────────────────────

    def exists(self, note_id: str, user_id: str = "") -> bool:
        if user_id:
            return (
                self.conn.execute(
                    "SELECT 1 FROM notes WHERE note_id = ? AND user_id = ?",
                    (note_id, user_id),
                ).fetchone()
                is not None
            )
        return (
            self.conn.execute(
                "SELECT 1 FROM notes WHERE note_id = ?", (note_id,)
            ).fetchone()
            is not None
        )

    def is_indexed(self, note_id: str, user_id: str) -> bool:
        """检查某用户的某笔记是否已入向量库。user_id 必填。"""
        row = self.conn.execute(
            "SELECT indexed FROM notes WHERE note_id = ? AND user_id = ?",
            (note_id, user_id),
        ).fetchone()
        return bool(row and row["indexed"])

    def get_unindexed(self, user_id: str = "") -> list[dict]:
        """返回尚未写入 ChromaDB 的笔记（indexed=0）。user_id 非空时只返回该用户的。"""
        if user_id:
            rows = self.conn.execute(
                """
                SELECT * FROM notes
                WHERE indexed = 0 AND is_collected = 1 AND user_id = ?
                ORDER BY crawled_at DESC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM notes
                WHERE indexed = 0 AND is_collected = 1
                ORDER BY crawled_at DESC
                """
            ).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def all_notes(self, user_id: str = "", include_archived: bool = False) -> list[dict]:
        """返回当前收藏笔记；include_archived=True 时包含历史归档。"""
        archived_filter = "" if include_archived else " AND is_collected = 1"
        if user_id:
            rows = self.conn.execute(
                f"SELECT * FROM notes WHERE user_id = ?{archived_filter} ORDER BY crawled_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM notes WHERE 1=1{archived_filter} ORDER BY crawled_at DESC"
            ).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def count(self, user_id: str = "", include_archived: bool = False) -> int:
        archived_filter = "" if include_archived else " AND is_collected = 1"
        if user_id:
            return self.conn.execute(
                f"SELECT COUNT(*) FROM notes WHERE user_id = ?{archived_filter}", (user_id,)
            ).fetchone()[0]
        return self.conn.execute(f"SELECT COUNT(*) FROM notes WHERE 1=1{archived_filter}").fetchone()[0]

    def get_updated(self, user_id: str = "") -> list[dict]:
        """
        返回内容已发生变化的笔记（content_changed_at 非空）。
        这些笔记已被重新向量化，但用户尚未通过 UI 感知到变化。
        """
        if user_id:
            rows = self.conn.execute(
                "SELECT * FROM notes WHERE content_changed_at != '' AND content_hash != update_seen_hash"
                " AND is_collected = 1 AND user_id = ?"
                " ORDER BY content_changed_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM notes WHERE content_changed_at != '' AND content_hash != update_seen_hash"
                " AND is_collected = 1"
                " ORDER BY content_changed_at DESC"
            ).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def count_updated(self, user_id: str = "") -> int:
        """返回内容有更新的笔记数量。"""
        if user_id:
            return self.conn.execute(
                "SELECT COUNT(*) FROM notes WHERE content_changed_at != '' AND content_hash != update_seen_hash"
                " AND is_collected = 1 AND user_id = ?",
                (user_id,),
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM notes WHERE content_changed_at != '' AND content_hash != update_seen_hash"
            " AND is_collected = 1"
        ).fetchone()[0]

    def mark_updates_seen(self, user_id: str, note_id: str | None = None) -> int:
        """Mark one note, or all notes for a user, as seen at the current version."""
        if note_id:
            cursor = self.conn.execute(
                """
                UPDATE notes
                SET update_seen_hash = content_hash
                WHERE user_id = ? AND note_id = ? AND is_collected = 1
                """,
                (user_id, note_id),
            )
        else:
            cursor = self.conn.execute(
                """
                UPDATE notes
                SET update_seen_hash = content_hash
                WHERE user_id = ? AND is_collected = 1
                """,
                (user_id,),
            )
        self.conn.commit()
        return cursor.rowcount

    # ── 内部工具 ──────────────────────────────────────────────────

    @staticmethod
    def _deserialize(row: dict) -> dict:
        """将 JSON 字符串字段还原为 Python list。"""
        for field in ("tags", "image_urls", "content_parts"):
            v = row.get(field, "[]")
            try:
                row[field] = json.loads(v) if isinstance(v, str) else v
            except Exception:
                row[field] = {} if field == "content_parts" else []
        return row

    def close(self) -> None:
        self.conn.close()
