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
    text_update_hash  TEXT    NOT NULL DEFAULT '',     -- title+正文的轻量快检版本
    text_seen_hash    TEXT    NOT NULL DEFAULT '',     -- 用户已确认的轻量文字版本
    note_published_at TEXT    NOT NULL DEFAULT '',     -- 帖子发布时间（小红书 API 返回的原始时间戳）
    content_changed_at TEXT   NOT NULL DEFAULT '',     -- 本地检测到内容变化的时间
    category          TEXT    NOT NULL DEFAULT '',     -- AI 分类或用户手动分类
    PRIMARY KEY (note_id, user_id)
);

-- FTS5 全文检索虚拟表（BM25 混合检索）
-- 使用 trigram tokenizer：将文本切成三字符滑窗，天然支持中文及任意子串匹配，
-- 无需中文分词库，对品牌名（SK-II）、地名、人名等精确实体检索效果好。
-- note_id / user_id 标为 UNINDEXED（存储但不建 trigram 索引），供 JOIN 过滤使用
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    user_id UNINDEXED,
    title,
    content,
    tokenize='trigram'
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
        self._sync_fts_index()
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
            ("text_update_hash",   "TEXT    NOT NULL DEFAULT ''"),
            ("text_seen_hash",     "TEXT    NOT NULL DEFAULT ''"),
            ("note_published_at",  "TEXT    NOT NULL DEFAULT ''"),
            ("content_changed_at", "TEXT    NOT NULL DEFAULT ''"),
            ("category",           "TEXT    NOT NULL DEFAULT ''"),
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
        self._backfill_text_hashes()

    def _sync_fts_index(self) -> None:
        """
        幂等地将 notes 表中尚未同步到 notes_fts 的记录补录进去。
        用于存量数据迁移：首次建 FTS 表时或 FTS 表与主表不一致时调用。
        """
        self.conn.execute("""
            INSERT INTO notes_fts(note_id, user_id, title, content)
            SELECT n.note_id, n.user_id, n.title, n.content
            FROM notes n
            WHERE NOT EXISTS (
                SELECT 1 FROM notes_fts f
                WHERE f.note_id = n.note_id AND f.user_id = n.user_id
            )
        """)
        logger.debug("FTS 索引同步完成")

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

    @staticmethod
    def _compute_text_hash(note: dict) -> str:
        """Compute the lightweight title/body version used by fast checks."""
        payload = {
            "title": (note.get("title") or "").strip(),
            "content": SQLiteStore._extract_text_body(note),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    @staticmethod
    def _extract_text_body(note: dict) -> str:
        """Return the body text used for lightweight update checks."""
        parts = note.get("content_parts")
        if isinstance(parts, str):
            try:
                parts = json.loads(parts)
            except Exception:
                parts = {}
        if isinstance(parts, dict):
            body = parts.get("body")
            if body is not None:
                return str(body).strip()
        return (note.get("content") or "").strip()

    def _backfill_text_hashes(self) -> None:
        rows = self.conn.execute(
            """
            SELECT note_id, user_id, title, content, content_parts
            FROM notes
            WHERE text_update_hash = '' OR text_seen_hash = ''
            """
        ).fetchall()
        for row in rows:
            text_hash = self._compute_text_hash(dict(row))
            self.conn.execute(
                """
                UPDATE notes
                SET text_update_hash = CASE WHEN text_update_hash = '' THEN ? ELSE text_update_hash END,
                    text_seen_hash = CASE WHEN text_seen_hash = '' THEN ? ELSE text_seen_hash END
                WHERE note_id = ? AND user_id = ?
                """,
                (text_hash, text_hash, row["note_id"], row["user_id"]),
            )

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
            SELECT indexed, content_hash, update_seen_hash, text_update_hash,
                   text_seen_hash, content_changed_at, category
            FROM notes
            WHERE note_id = ? AND user_id = ?
            """,
            (note["note_id"], user_id),
        ).fetchone()

        new_hash = self._compute_hash(note)
        new_text_hash = self._compute_text_hash(note)
        now = datetime.now(timezone.utc).isoformat()

        if existing is None:
            # 新记录
            old_indexed        = 0
            content_changed_at = ""
            old_category       = ""
            update_seen_hash   = new_hash
            text_seen_hash     = new_text_hash
        else:
            old_category = existing["category"] or ""
            old_hash = existing["content_hash"] or ""
            old_text_hash = existing["text_update_hash"] or self._compute_text_hash(note)
            if (old_hash and old_hash != new_hash) or (old_text_hash and old_text_hash != new_text_hash):
                # 内容发生变化：重置 indexed，记录变化时间，后续触发重新向量化
                old_indexed        = 0
                content_changed_at = now
                update_seen_hash   = existing["update_seen_hash"] or old_hash
                text_seen_hash     = existing["text_seen_hash"] or old_text_hash
                logger.info(
                    f"[{note['note_id']}] 内容已更新（hash 变化），将重新向量化"
                )
            else:
                old_indexed        = existing["indexed"]
                content_changed_at = existing["content_changed_at"] or ""
                update_seen_hash   = existing["update_seen_hash"] or new_hash
                text_seen_hash     = existing["text_seen_hash"] or new_text_hash

        self.conn.execute(
            """
            INSERT OR REPLACE INTO notes
              (note_id, user_id, title, content, content_parts, tags, cover_url, image_urls,
               note_url, likes, note_type, crawled_at, indexed,
               is_collected, archived_at, content_hash, update_seen_hash,
               text_update_hash, text_seen_hash, note_published_at, content_changed_at,
               category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                new_text_hash,
                text_seen_hash,
                note.get("note_published_at", ""),
                content_changed_at,
                old_category,
            ),
        )
        # 显式同步 FTS 索引（INSERT OR REPLACE 不触发 DELETE trigger，需手动维护）
        self._upsert_fts(note["note_id"], user_id, note.get("title", ""), note.get("content", ""))
        self.conn.commit()
        is_new = existing is None
        logger.debug(f"[{note['note_id']}] SQLite {'INSERT' if is_new else 'UPDATE'}")
        return is_new

    def _upsert_fts(self, note_id: str, user_id: str, title: str, content: str) -> None:
        """删除旧 FTS 记录再插入新记录，确保 FTS 与 notes 表同步。"""
        self.conn.execute(
            "DELETE FROM notes_fts WHERE note_id = ? AND user_id = ?",
            (note_id, user_id),
        )
        self.conn.execute(
            "INSERT INTO notes_fts(note_id, user_id, title, content) VALUES (?, ?, ?, ?)",
            (note_id, user_id, title, content),
        )

    def upsert_lightweight_text(self, note: dict, user_id: str) -> str:
        """Update only title/body metadata for fast update checks."""
        from datetime import datetime, timezone

        note_id = note["note_id"]
        existing = self.conn.execute(
            """
            SELECT title, content, content_hash, update_seen_hash, text_update_hash, text_seen_hash
            FROM notes
            WHERE note_id = ? AND user_id = ?
            """,
            (note_id, user_id),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()
        text_hash = self._compute_text_hash(note)

        if existing is None:
            content_hash = self._compute_hash(note)
            self.conn.execute(
                """
                INSERT INTO notes
                  (note_id, user_id, title, content, content_parts, tags, cover_url, image_urls,
                   note_url, likes, note_type, crawled_at, indexed, is_collected, archived_at,
                   content_hash, update_seen_hash, text_update_hash, text_seen_hash,
                   note_published_at, content_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    user_id,
                    note.get("title", ""),
                    note.get("content", ""),
                    json.dumps(note.get("content_parts", {}), ensure_ascii=False),
                    json.dumps(note.get("tags", []), ensure_ascii=False),
                    note.get("cover_url", ""),
                    json.dumps(note.get("image_urls", []), ensure_ascii=False),
                    note.get("note_url", ""),
                    int(note.get("likes", 0) or 0),
                    note.get("note_type", "image"),
                    note.get("crawled_at", now),
                    0,
                    1,
                    "",
                    content_hash,
                    content_hash,
                    text_hash,
                    text_hash,
                    note.get("note_published_at", ""),
                    "",
                ),
            )
            self._upsert_fts(note_id, user_id, note.get("title", ""), note.get("content", ""))
            self.conn.commit()
            return "new"

        old_text_hash = existing["text_update_hash"] or self._compute_text_hash(dict(existing))
        if old_text_hash == text_hash:
            self.conn.execute(
                """
                UPDATE notes
                SET is_collected = 1,
                    archived_at = '',
                    crawled_at = ?
                WHERE note_id = ? AND user_id = ?
                """,
                (note.get("crawled_at", now), note_id, user_id),
            )
            self.conn.commit()
            return "unchanged"

        self.conn.execute(
            """
            UPDATE notes
            SET title = ?,
                content = ?,
                content_parts = ?,
                note_url = COALESCE(NULLIF(?, ''), note_url),
                cover_url = COALESCE(NULLIF(?, ''), cover_url),
                image_urls = ?,
                likes = ?,
                note_type = ?,
                crawled_at = ?,
                indexed = 0,
                is_collected = 1,
                archived_at = '',
                text_update_hash = ?,
                text_seen_hash = CASE WHEN text_seen_hash = '' THEN ? ELSE text_seen_hash END,
                content_changed_at = ?
            WHERE note_id = ? AND user_id = ?
            """,
            (
                note.get("title", ""),
                note.get("content", ""),
                json.dumps(note.get("content_parts", {}), ensure_ascii=False),
                note.get("note_url", ""),
                note.get("cover_url", ""),
                json.dumps(note.get("image_urls", []), ensure_ascii=False),
                int(note.get("likes", 0) or 0),
                note.get("note_type", "image"),
                note.get("crawled_at", now),
                text_hash,
                old_text_hash,
                now,
                note_id,
                user_id,
            ),
        )
        self._upsert_fts(note_id, user_id, note.get("title", ""), note.get("content", ""))
        self.conn.commit()
        return "updated"

    def mark_indexed(self, note_id: str, user_id: str) -> None:
        """将 indexed 置为 1，表示已写入 ChromaDB。user_id 必填，防止跨用户误更新。"""
        self.conn.execute(
            "UPDATE notes SET indexed = 1 WHERE note_id = ? AND user_id = ?",
            (note_id, user_id),
        )
        self.conn.commit()

    def update_cover_url(self, note_id: str, user_id: str, cover_url: str) -> None:
        """更新本地封面 URL。用于已向量化笔记补齐封面缓存，不影响索引状态。"""
        self.conn.execute(
            "UPDATE notes SET cover_url = ? WHERE note_id = ? AND user_id = ?",
            (cover_url, note_id, user_id),
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
        # 归档的笔记从 FTS 删除，不再参与关键词检索
        for nid in archived_ids:
            self.conn.execute(
                "DELETE FROM notes_fts WHERE note_id = ? AND user_id = ?",
                (nid, user_id),
            )
        self.conn.commit()
        logger.info(f"已归档 {len(archived_ids)} 条已取消收藏的笔记")
        return archived_ids

    def set_category(self, note_id: str, user_id: str, category: str) -> None:
        """写回分类结果。"""
        self.conn.execute(
            "UPDATE notes SET category = ? WHERE note_id = ? AND user_id = ?",
            (category, note_id, user_id),
        )
        self.conn.commit()

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

    def get_unclassified(self, user_id: str = "", limit: int = 100) -> list[dict]:
        """返回 category 为空且已收藏的笔记，供分类子进程使用。"""
        if user_id:
            rows = self.conn.execute(
                """
                SELECT note_id, title, content, tags, user_id
                FROM notes
                WHERE category = '' AND is_collected = 1 AND user_id = ?
                ORDER BY crawled_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT note_id, title, content, tags, user_id
                FROM notes
                WHERE category = '' AND is_collected = 1
                ORDER BY crawled_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def all_notes(self, user_id: str = "", include_archived: bool = False, category: str = "") -> list[dict]:
        """返回当前收藏笔记；include_archived=True 时包含历史归档。"""
        archived_filter = "" if include_archived else " AND is_collected = 1"
        category_filter = " AND category = ?" if category else ""
        if user_id:
            params = (user_id,)
            if category:
                params = (user_id, category)
            rows = self.conn.execute(
                f"SELECT * FROM notes WHERE user_id = ?{archived_filter}{category_filter} ORDER BY crawled_at DESC",
                params,
            ).fetchall()
        else:
            params = (category,) if category else ()
            rows = self.conn.execute(
                f"SELECT * FROM notes WHERE 1=1{archived_filter}{category_filter} ORDER BY crawled_at DESC",
                params,
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
                "SELECT * FROM notes WHERE content_changed_at != ''"
                " AND text_update_hash != text_seen_hash"
                " AND is_collected = 1 AND user_id = ?"
                " ORDER BY content_changed_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM notes WHERE content_changed_at != ''"
                " AND text_update_hash != text_seen_hash"
                " AND is_collected = 1"
                " ORDER BY content_changed_at DESC"
            ).fetchall()
        return [self._deserialize(dict(r)) for r in rows]

    def count_updated(self, user_id: str = "") -> int:
        """返回内容有更新的笔记数量。"""
        if user_id:
            return self.conn.execute(
                "SELECT COUNT(*) FROM notes WHERE content_changed_at != ''"
                " AND text_update_hash != text_seen_hash"
                " AND is_collected = 1 AND user_id = ?",
                (user_id,),
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM notes WHERE content_changed_at != ''"
            " AND text_update_hash != text_seen_hash"
            " AND is_collected = 1"
        ).fetchone()[0]

    def fts_search(
        self,
        query: str,
        user_id: str = "",
        n_results: int = 10,
    ) -> list[dict]:
        """
        BM25 全文检索（SQLite FTS5）。

        返回 list[dict]，每条含：
            note_id   str
            title     str
            bm25      float  BM25 分数（FTS5 返回的负值，越小越相关；此处取绝对值）

        注：仅返回 is_collected=1 的笔记（通过 JOIN notes 过滤）。
        query 为空或 FTS 表不存在时返回 []。
        """
        if not query or not query.strip():
            return []

        try:
            fts_query = self._build_fts_query(query)
            if fts_query is None:
                # 所有词均 < 3 字符，trigram 无法处理，静默降级
                logger.debug(f"[fts_search] query 词项均过短，跳过 FTS：{repr(query)}")
                return []
            if user_id:
                rows = self.conn.execute(
                    f"""
                    SELECT f.note_id, f.title, bm25(notes_fts) AS score
                    FROM notes_fts f
                    JOIN notes n ON n.note_id = f.note_id AND n.user_id = f.user_id
                    WHERE notes_fts MATCH ? AND f.user_id = ? AND n.is_collected = 1
                    ORDER BY score
                    LIMIT ?
                    """,
                    (fts_query, user_id, n_results),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    f"""
                    SELECT f.note_id, f.title, bm25(notes_fts) AS score
                    FROM notes_fts f
                    JOIN notes n ON n.note_id = f.note_id AND n.user_id = f.user_id
                    WHERE notes_fts MATCH ? AND n.is_collected = 1
                    ORDER BY score
                    LIMIT ?
                    """,
                    (fts_query, n_results),
                ).fetchall()

            return [
                {
                    "note_id": row[0],
                    "title":   row[1],
                    "bm25":    abs(row[2]),  # 转正值，越大越相关
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning(f"[fts_search] FTS 检索失败，降级跳过：{e}")
            return []

    @staticmethod
    def _build_fts_query(query: str) -> str | None:
        """
        将用户 query 转换为 FTS5 trigram MATCH 表达式。

        trigram tokenizer 对每个词建立三字符滑窗索引，要求单个词项 >= 3 字符。
        多词 query 拆分为各词独立的 AND 条件（`"词1" "词2"`），每词用双引号包裹。

        对于长度 < 3 字符的词（如两字中文词"京都"）：
          - 若这是 query 中唯一的词 → 返回 None，让调用方跳过 FTS（降级为纯向量）
          - 若与其他 >= 3 字符的词共存 → 跳过该短词，仅用长词召回

        返回：
            str   有效的 FTS5 MATCH 表达式
            None  无法构建有效 query（调用方应返回 []）
        """
        # 去掉 FTS5 保留运算符，保留连字符（SK-II 等品牌名）和空格
        clean = query.replace('"', ' ').replace("'", ' ').replace('*', ' ').strip()
        if not clean:
            return None

        words = clean.split()
        # 过滤掉 < 3 字符的词（trigram 最短要求）
        long_words = [w for w in words if len(w) >= 3]

        if not long_words:
            return None  # 全部是短词，降级为向量检索

        # 每个词用双引号包裹（转义特殊字符，作为子串短语搜索）
        return " ".join(f'"{w}"' for w in long_words)

    def get_categories(self, user_id: str) -> list[dict]:
        """返回某用户的分类列表（去重计数），按数量降序排列。"""
        rows = self.conn.execute(
            """
            SELECT category, COUNT(*) as cnt
            FROM notes
            WHERE user_id = ? AND is_collected = 1 AND category != ''
            GROUP BY category
            ORDER BY cnt DESC
            """,
            (user_id,),
        ).fetchall()
        return [{"name": row["category"], "count": row["cnt"]} for row in rows]

    def mark_updates_seen(self, user_id: str, note_id: str | None = None) -> int:
        """Mark one note, or all notes for a user, as seen at the current version."""
        if note_id:
            cursor = self.conn.execute(
                """
                UPDATE notes
                SET update_seen_hash = content_hash,
                    text_seen_hash = text_update_hash
                WHERE user_id = ? AND note_id = ? AND is_collected = 1
                """,
                (user_id, note_id),
            )
        else:
            cursor = self.conn.execute(
                """
                UPDATE notes
                SET update_seen_hash = content_hash,
                    text_seen_hash = text_update_hash
                WHERE user_id = ? AND is_collected = 1
                """,
                (user_id,),
            )
        self.conn.commit()
        return cursor.rowcount

    def reset_text_update_baseline(self, user_id: str = "") -> int:
        """Reset lightweight text-update state to the current title/body baseline."""
        if user_id:
            rows = self.conn.execute(
                """
                SELECT note_id, user_id, title, content, content_parts
                FROM notes
                WHERE user_id = ? AND is_collected = 1
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT note_id, user_id, title, content, content_parts
                FROM notes
                WHERE is_collected = 1
                """
            ).fetchall()

        for row in rows:
            text_hash = self._compute_text_hash(dict(row))
            self.conn.execute(
                """
                UPDATE notes
                SET text_update_hash = ?,
                    text_seen_hash = ?,
                    content_changed_at = CASE
                        WHEN content_hash = update_seen_hash THEN ''
                        ELSE content_changed_at
                    END
                WHERE note_id = ? AND user_id = ?
                """,
                (text_hash, text_hash, row["note_id"], row["user_id"]),
            )
        self.conn.commit()
        return len(rows)

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

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self.conn.close()
