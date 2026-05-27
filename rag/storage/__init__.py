"""
rag.storage
=======
双层持久化：SQLite（元数据）+ ChromaDB（向量）。

对外只暴露 NoteStore，通过 note_id 联结两层。

用法：
    store = NoteStore()
    store.save(raw_note_dict, user_id="640c4bcc...")
    hits  = store.search("MySQL 索引优化")
    store.close()
"""

import logging
from .sqlite_store import SQLiteStore
from .chroma_store import ChromaStore

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH     = "data/notes.db"
DEFAULT_CHROMA_PATH = "data/chroma_db"


class NoteStore:
    """
    SQLite + ChromaDB 双层存储的统一入口。

    save()   — 元数据写 SQLite，content 向量化写 ChromaDB，indexed 标记置 1
    search() — 语义检索（走 ChromaDB）
    notes()  — 元数据列表（走 SQLite，供前端展示）
    close()  — 关闭 SQLite 连接（ChromaDB PersistentClient 无需显式关闭）
    """

    def __init__(
        self,
        db_path: str     = DEFAULT_DB_PATH,
        chroma_path: str = DEFAULT_CHROMA_PATH,
        init_chroma: bool = True,
    ):
        self.sqlite = SQLiteStore(db_path)
        self.chroma = ChromaStore(chroma_path) if init_chroma else None

    # ── 写入 ──────────────────────────────────────────────────────

    def save(self, note: dict, user_id: str) -> bool:
        """
        将一条 RawNote.to_dict() 结果持久化。

        流程：
          1. SQLite upsert 元数据（保留已有 indexed 状态）
          2. ChromaDB upsert content 向量
          3. 若 ChromaDB 写入成功，SQLite 将 indexed 置 1

        返回 True 表示新笔记，False 表示已存在（已更新）。
        """
        is_new = self.sqlite.upsert(note, user_id)
        action = "新增" if is_new else "更新"

        content = (note.get("content") or "").strip()
        if content:
            if self.chroma is None:
                raise RuntimeError("ChromaDB 未初始化，无法写入向量索引")
            self.chroma.upsert(
                note_id=note["note_id"],
                content=content,
                user_id=user_id,
                title=note.get("title", ""),
            )
            self.sqlite.mark_indexed(note["note_id"], user_id)
            logger.info(
                f"[{note['note_id']}] {action} ✓  "
                f"SQLite + ChromaDB  title={note.get('title', '')[:30]!r}"
            )
        else:
            logger.warning(
                f"[{note['note_id']}] {action} ⚠  "
                f"content 为空，仅存入 SQLite（indexed=0）"
            )

        return is_new

    # ── 查询 ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        user_id: str = "",
        n_results: int = 5,
    ) -> list[dict]:
        """
        语义检索，返回最相关的笔记列表。
        user_id 非空时只搜索该用户的收藏。
        ChromaDB 检索后，批量查 SQLite 补充 note_url 和 cover_url。
        """
        if self.chroma is None:
            raise RuntimeError("ChromaDB 未初始化，无法语义检索")
        hits = self.chroma.search(query, user_id=user_id, n_results=n_results)
        if not hits:
            return hits

        # 批量回查 SQLite，补充 note_url / cover_url
        note_ids = [h["note_id"] for h in hits]
        placeholders = ",".join("?" * len(note_ids))
        if user_id:
            rows = self.sqlite.conn.execute(
                f"SELECT note_id, note_url, cover_url FROM notes "
                f"WHERE note_id IN ({placeholders}) AND user_id = ? AND is_collected = 1",
                (*note_ids, user_id),
            ).fetchall()
        else:
            rows = self.sqlite.conn.execute(
                f"SELECT note_id, note_url, cover_url FROM notes "
                f"WHERE note_id IN ({placeholders}) AND is_collected = 1",
                note_ids,
            ).fetchall()

        url_map = {
            r["note_id"]: {"note_url": r["note_url"], "cover_url": r["cover_url"]}
            for r in rows
        }
        active_hits = []
        for hit in hits:
            extra = url_map.get(hit["note_id"], {})
            if not extra:
                continue
            hit["note_url"]  = extra.get("note_url",  "")
            hit["cover_url"] = extra.get("cover_url", "")
            active_hits.append(hit)

        return active_hits

    def notes(self, user_id: str = "") -> list[dict]:
        """从 SQLite 返回元数据列表（供前端展示）。"""
        return self.sqlite.all_notes(user_id=user_id)

    def archive_missing(self, user_id: str, current_note_ids: set[str]) -> list[str]:
        """
        软归档已不在当前收藏夹中的笔记，并删除对应 ChromaDB 向量。
        SQLite 历史记录保留，默认列表和检索不再使用这些内容。
        """
        archived_ids = self.sqlite.mark_uncollected_missing(user_id, current_note_ids)
        if archived_ids and self.chroma is not None:
            self.chroma.delete(archived_ids)
        return archived_ids

    def updated_notes(self, user_id: str = "") -> list[dict]:
        """返回内容已变化的笔记列表（content_changed_at 非空）。"""
        return self.sqlite.get_updated(user_id=user_id)

    def mark_updates_seen(self, user_id: str, note_id: str | None = None) -> int:
        """Mark one updated note, or all updated notes, as seen for the user."""
        return self.sqlite.mark_updates_seen(user_id=user_id, note_id=note_id)

    # ── 统计 ──────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "sqlite_total":   self.sqlite.count(),
            "chroma_indexed": self.chroma.count() if self.chroma is not None else 0,
            "updated_count":  self.sqlite.count_updated(),
        }

    # ── 生命周期 ──────────────────────────────────────────────────

    def close(self) -> None:
        self.sqlite.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def metadata_store(db_path: str = DEFAULT_DB_PATH) -> NoteStore:
    """Create a NoteStore for SQLite-only metadata reads."""
    return NoteStore(db_path=db_path, init_chroma=False)


__all__ = ["NoteStore", "SQLiteStore", "ChromaStore", "metadata_store"]
