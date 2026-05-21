"""
rag/storage/chroma_store.py
=======================
ChromaDB 向量存储层。

职责：
  - 将笔记 content 向量化后持久化
  - metadata 携带 note_id / user_id，供查询时过滤
  - 提供语义检索接口

嵌入模型：
  智谱 embedding-3（2048 维，余弦距离）
  通过 rag.embedder.ZhipuEmbeddingFunction 调用，需设置 ZHIPUAI_API_KEY。

注意：
  若之前用本地模型（384 维）建过集合，维度不匹配会报错。
  切换模型时请先删除旧数据：rm -rf data/chroma_db
"""

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "xhs_notes"

# ── ChromaDB 单例 ──────────────────────────────────────────────
# chromadb 1.x Rust 后端要求 PersistentClient 全进程只创建一次；
# 每次 NoteStore() 重新 new 会触发 RustBindingsAPI 初始化 bug。
_chroma_client = None
_chroma_client_lock = threading.Lock()


def _create_persistent_client(chroma_path: str):
    import chromadb  # type: ignore

    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=chroma_path)


def _get_client(chroma_path: str):
    """返回模块级 ChromaDB PersistentClient 单例。"""
    global _chroma_client
    if _chroma_client is None:
        with _chroma_client_lock:
            if _chroma_client is None:
                _chroma_client = _create_persistent_client(chroma_path)
                logger.info(f"[ChromaDB] PersistentClient 初始化完成：{chroma_path}")
    return _chroma_client


class ChromaStore:
    def __init__(self, chroma_path: str = "data/chroma_db"):
        from rag.embedder import ZhipuEmbeddingFunction  # type: ignore

        self.client = _get_client(chroma_path)

        ef = ZhipuEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        logger.debug(
            f"ChromaDB 已连接：{chroma_path}，"
            f"集合 {_COLLECTION_NAME!r} 共 {self.collection.count()} 条"
        )

    # ── 写入 ──────────────────────────────────────────────────────

    def upsert(self, note_id: str, content: str,
               user_id: str, title: str = "") -> None:
        """
        将笔记 content 向量化后写入集合（insert or update by note_id）。
        content 为空时跳过。

        embedding 文本结构：将标题重复两次置于正文之前，使标题在语义向量中
        获得更高权重，避免 OCR/转录噪声稀释标题关键词的语义。
        格式："{title}\n{title}\n\n{content}"
        """
        content = content.strip()
        if not content:
            logger.warning(f"[{note_id}] content 为空，跳过 ChromaDB 写入")
            return

        # 标题加权：标题重复两次 + 正文，提升标题词在 embedding 中的权重
        title_clean = (title or "").strip()
        document = f"{title_clean}\n{title_clean}\n\n{content}" if title_clean else content

        self.collection.upsert(
            ids=[note_id],
            documents=[document],
            metadatas=[{
                "note_id": note_id,
                "user_id": user_id,
                "title":   title,
            }],
        )
        logger.debug(f"[{note_id}] ChromaDB upsert 完成")

    def delete(self, note_ids: list[str]) -> None:
        """按 note_id 删除 ChromaDB 中的向量记录。"""
        note_ids = [note_id for note_id in note_ids if note_id]
        if not note_ids:
            return
        self.collection.delete(ids=note_ids)
        logger.info(f"ChromaDB 已删除 {len(note_ids)} 条归档笔记向量")

    # ── 查询 ──────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        user_id: str = "",
        n_results: int = 5,
    ) -> list[dict]:
        """
        语义检索。
        user_id 非空时只搜索该用户的笔记（通过 metadata 过滤）。

        返回 list[dict]，每条含：
            note_id   str
            content   str   完整正文
            title     str
            user_id   str
            distance  float  余弦距离（越小越相似）
        """
        # ChromaDB where 过滤（可选）
        where = {"user_id": {"$eq": user_id}} if user_id else None

        # 若集合为空则直接返回
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query],
            n_results=min(n_results, self.collection.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        hits: list[dict] = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] or {}
            hits.append({
                "note_id":  doc_id,
                "content":  results["documents"][0][i],
                "title":    meta.get("title", ""),
                "user_id":  meta.get("user_id", ""),
                "distance": results["distances"][0][i],
            })
        return hits

    # ── 统计 ──────────────────────────────────────────────────────

    def count(self) -> int:
        return self.collection.count()
