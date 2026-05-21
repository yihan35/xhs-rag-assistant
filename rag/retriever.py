"""
rag/retriever.py
================
检索：语义召回 + 相关度过滤。
负责人：李奕涵

对外接口（后端调用）：
    retrieve(query, user_id, folder_id=None, top_k=6) -> list[dict]

安全原则（必须遵守）：
    检索时强制带 user_id 过滤，严禁 A 用户的 query 召回 B 用户的笔记。
    见 code review 清单第一条。

TODO（李奕涵）：
    [ ] 支持 folder_id 过滤（需要 SQLite 先有 folder 字段）
    [ ] 相关度阈值参数化（当前写死 0.8）
    [ ] 支持混合检索（向量 + 关键词 BM25）
"""

import logging
from rag.storage import NoteStore
from rag.rewriter import rewrite_query

logger = logging.getLogger(__name__)

# 余弦距离阈值：超过此值视为不相关，宁可说「没找到」也不返回噪声
_DISTANCE_THRESHOLD = 0.5


def retrieve(
    query: str,
    user_id: str,
    folder_id: str | None = None,
    top_k: int = 6,
) -> list[dict]:
    """
    语义检索，返回相关笔记列表。

    参数：
        query      用户自然语言 query
        user_id    必填，强制隔离用户数据
        folder_id  可选，限定收藏夹范围（当前版本暂不支持，留接口）
        top_k      最多返回条数，默认 6

    返回 list[dict]，每条含：
        note_id   str
        title     str
        content   str   完整正文（供 generator 使用）
        user_id   str
        distance  float 余弦距离（越小越相关）

    空结果或全部超过阈值时返回 []。
    """
    if not user_id:
        raise ValueError("user_id 不能为空，必须指定用户")

    # 用 GLM-4.6 改写 query，去掉口语化前缀，提升向量检索召回率
    search_query = rewrite_query(query)

    with NoteStore() as store:
        hits = store.search(search_query, user_id=user_id, n_results=top_k)

    # 过滤相关度太低的结果
    relevant = [h for h in hits if h.get("distance", 1.0) < _DISTANCE_THRESHOLD]

    if not relevant and hits:
        logger.info(
            f"[retrieve] 所有结果距离 > {_DISTANCE_THRESHOLD}，返回空。"
            f"最近距离={hits[0].get('distance', '?'):.3f}"
        )

    return relevant
