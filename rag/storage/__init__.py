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
        try:
            from crawler.cover_cache import cache_cover_image

            note = dict(note)
            note["cover_url"] = cache_cover_image(note.get("note_id", ""), note.get("cover_url", ""))
        except Exception as e:
            logger.warning(f"[{note.get('note_id')}] 封面缓存步骤失败，继续入库：{e}")

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
        混合检索（向量语义 + BM25 关键词），用 RRF 合并两路结果。

        RRF 公式：score(d) = Σ 1/(k + rank_i(d))，k=60（标准值）。
        同一笔记在两路均命中时得分叠加，只在一路命中时得分较低但仍保留。

        user_id 非空时只搜索该用户的收藏。
        ChromaDB 检索后，批量查 SQLite 补充 note_url 和 cover_url。
        """
        if self.chroma is None:
            raise RuntimeError("ChromaDB 未初始化，无法语义检索")

        # 多召回一些候选，RRF 合并后再截断到 n_results
        fetch_k = max(n_results * 2, 20)

        # 路1：向量语义检索
        vec_hits = self.chroma.search(query, user_id=user_id, n_results=fetch_k)

        # 路2：BM25 全文检索
        bm25_hits = self.sqlite.fts_search(query, user_id=user_id, n_results=fetch_k)

        # RRF 合并
        hits = self._rrf_merge(vec_hits, bm25_hits, top_k=n_results)

        if not hits:
            return hits

        # 批量回查 SQLite，补充 note_url / cover_url / content
        note_ids = [h["note_id"] for h in hits]
        placeholders = ",".join("?" * len(note_ids))
        if user_id:
            rows = self.sqlite.conn.execute(
                f"SELECT note_id, note_url, cover_url, content, title FROM notes "
                f"WHERE note_id IN ({placeholders}) AND user_id = ? AND is_collected = 1",
                (*note_ids, user_id),
            ).fetchall()
        else:
            rows = self.sqlite.conn.execute(
                f"SELECT note_id, note_url, cover_url, content, title FROM notes "
                f"WHERE note_id IN ({placeholders}) AND is_collected = 1",
                note_ids,
            ).fetchall()

        meta_map = {
            r["note_id"]: {
                "note_url":  r["note_url"],
                "cover_url": r["cover_url"],
                "content":   r["content"],
                "title":     r["title"],
            }
            for r in rows
        }
        active_hits = []
        for hit in hits:
            meta = meta_map.get(hit["note_id"], {})
            if not meta:
                continue
            hit["note_url"]  = meta.get("note_url",  "")
            hit["cover_url"] = meta.get("cover_url", "")
            # 向量检索已携带 content；BM25-only 命中的笔记从 SQLite 补充
            if not hit.get("content"):
                hit["content"] = meta.get("content", "")
            if not hit.get("title"):
                hit["title"] = meta.get("title", "")
            active_hits.append(hit)

        return active_hits

    @staticmethod
    def _rrf_merge(
        vec_hits: list[dict],
        bm25_hits: list[dict],
        top_k: int,
        k: int = 60,
    ) -> list[dict]:
        """
        Reciprocal Rank Fusion：合并向量检索和 BM25 两路结果。

        参数：
            vec_hits   向量检索结果（list[dict]，含 note_id / content / title / distance）
            bm25_hits  BM25 检索结果（list[dict]，含 note_id / title / bm25）
            top_k      最终返回条数
            k          RRF 平滑参数，默认 60（学术标准值）

        返回：
            list[dict]，每条含 note_id / content / title / distance / rrf_score，
            按 rrf_score 降序排列，取前 top_k 条。
        """
        scores: dict[str, float] = {}
        # 保留各路命中的原始字段，优先取向量检索的（含 content）
        note_data: dict[str, dict] = {}

        for rank, hit in enumerate(vec_hits, start=1):
            nid = hit["note_id"]
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (k + rank)
            note_data.setdefault(nid, hit)

        for rank, hit in enumerate(bm25_hits, start=1):
            nid = hit["note_id"]
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (k + rank)
            note_data.setdefault(nid, hit)

        sorted_ids = sorted(scores, key=lambda nid: scores[nid], reverse=True)[:top_k]

        result = []
        for nid in sorted_ids:
            entry = dict(note_data[nid])
            entry["rrf_score"] = scores[nid]
            result.append(entry)

        return result

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
