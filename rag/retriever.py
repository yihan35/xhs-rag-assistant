"""
rag/retriever.py
================
检索：Query 改写 → 多路扩展检索（向量 + BM25）→ RRF 合并 → 相关度过滤。
负责人：李奕涵

对外接口（后端调用）：
    retrieve(query, user_id, folder_id=None, top_k=6) -> list[dict]

安全原则（必须遵守）：
    检索时强制带 user_id 过滤，严禁 A 用户的 query 召回 B 用户的笔记。
    见 code review 清单第一条。

检索管道：
    1. rewrite_query()   去口语化，提炼核心词组 q0
    2. expand_query()    语义扩展，生成 q1、q2（失败则降级为单路）
    3. 多路混合检索       [q0, q1, q2] 各自走 向量+BM25 混合检索
    4. RRF 合并          同一笔记多路命中得分叠加，取 top_k
    5. 距离阈值过滤       仅对向量路有效（BM25-only 命中不过滤）

TODO（李奕涵）：
    [ ] 支持 folder_id 过滤（需要 SQLite 先有 folder 字段）
    [ ] 相关度阈值参数化（当前写死 0.5）
"""

import logging
from rag.storage import NoteStore
from rag.rewriter import rewrite_query
from rag.expander import expand_query
from rag.debug_logging import llm_io_logging_enabled, to_log_json

logger = logging.getLogger(__name__)

# 余弦距离阈值：仅用于纯向量命中的笔记；BM25 命中的笔记不受此限
_DISTANCE_THRESHOLD = 0.5

# RRF 平滑参数（与 NoteStore._rrf_merge 保持一致）
_RRF_K = 60


def retrieve(
    query: str,
    user_id: str,
    folder_id: str | None = None,
    top_k: int = 6,
    mode: str = "unknown",
) -> list[dict]:
    """
    混合多路检索，返回相关笔记列表。

    参数：
        query      用户自然语言 query
        user_id    必填，强制隔离用户数据
        folder_id  可选，限定收藏夹范围（当前版本暂不支持，留接口）
        top_k      最多返回条数，默认 6

    返回 list[dict]，每条含：
        note_id    str
        title      str
        content    str    完整正文（供 generator 使用）
        user_id    str
        distance   float  余弦距离（向量命中时有值；BM25-only 命中为 1.0）
        rrf_score  float  RRF 合并分数（越高越相关）

    空结果时返回 []。
    """
    if not user_id:
        raise ValueError("user_id 不能为空，必须指定用户")

    # Step 1：query 改写，去口语化
    q0 = rewrite_query(query)

    # Step 2：语义扩展，生成扩展词组（失败时静默返回 []）
    expanded = expand_query(q0)
    all_queries = [q0] + expanded  # [q0, q1?, q2?]
    if llm_io_logging_enabled():
        logger.info(
            "[llm-io][retrieve][%s] original_query=%s\nrewritten_query=%s\nexpanded_queries=%s",
            mode,
            query,
            q0,
            to_log_json(expanded),
        )

    # Step 3：多路混合检索，每路结果独立 RRF
    # 为让多路 RRF 叠加有意义，每路单独检索 fetch_k 条
    fetch_k = max(top_k * 2, 20)

    with NoteStore() as store:
        all_hits = _multi_query_search(store, all_queries, user_id, fetch_k)

    # Step 4：跨路 RRF 合并（复用 NoteStore._rrf_merge 的静态方法思路，此处内联实现多路）
    merged = _multi_rrf_merge(all_hits, top_k=top_k, k=_RRF_K)

    # Step 5：相关度过滤（仅对纯向量命中有效，BM25 命中的保留）
    relevant = _filter_by_relevance(merged, threshold=_DISTANCE_THRESHOLD)

    if not relevant and merged:
        logger.info(
            f"[retrieve] {len(merged)} 条合并结果均被相关度过滤，返回空。"
            f"最高 rrf_score={merged[0].get('rrf_score', 0):.4f}"
        )
    if llm_io_logging_enabled():
        titles = [
            {
                "rank": i,
                "note_id": doc.get("note_id", ""),
                "title": doc.get("title", ""),
                "distance": doc.get("distance"),
                "rrf_score": doc.get("rrf_score"),
            }
            for i, doc in enumerate(relevant, start=1)
        ]
        logger.info(
            "[llm-io][retrieve][%s] recalled_titles=%s",
            mode,
            to_log_json(titles),
        )

    return relevant


def _multi_query_search(
    store: NoteStore,
    queries: list[str],
    user_id: str,
    fetch_k: int,
) -> list[list[dict]]:
    """对每个 query 独立调用混合检索，返回各路结果列表。"""
    results = []
    for q in queries:
        hits = store.search(q, user_id=user_id, n_results=fetch_k)
        results.append(hits)
        logger.debug(f"[retrieve] query='{q}' → {len(hits)} 条")
    return results


def _multi_rrf_merge(
    all_hits: list[list[dict]],
    top_k: int,
    k: int = 60,
) -> list[dict]:
    """
    多路 RRF 合并：每路结果按名次计分，同一笔记跨路得分累加。

    参数：
        all_hits  各路检索结果，list[list[dict]]
        top_k     最终取前 N 条
        k         RRF 平滑参数

    返回：
        list[dict]，按 rrf_score 降序，含原始字段 + rrf_score。
    """
    scores: dict[str, float] = {}
    note_data: dict[str, dict] = {}

    for hits in all_hits:
        for rank, hit in enumerate(hits, start=1):
            nid = hit["note_id"]
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (k + rank)
            # 优先保留 content 字段最丰富的版本（向量路已携带）
            if nid not in note_data or not note_data[nid].get("content"):
                note_data[nid] = dict(hit)
            elif hit.get("bm25_match"):
                note_data[nid]["bm25_match"] = True
                note_data[nid]["bm25"] = hit.get("bm25", note_data[nid].get("bm25"))

    sorted_ids = sorted(scores, key=lambda nid: scores[nid], reverse=True)[:top_k]
    result = []
    for nid in sorted_ids:
        entry = dict(note_data[nid])
        entry["rrf_score"] = scores[nid]
        result.append(entry)

    return result


def _filter_by_relevance(hits: list[dict], threshold: float) -> list[dict]:
    """
    过滤相关度太低的笔记。

    规则：
    - 有 distance 字段（向量命中）且 distance >= threshold → 过滤
    - 无 distance 或 distance 为默认值 1.0（BM25-only 命中）→ 保留
      （BM25 命中说明关键词精确匹配，不应因向量距离被丢弃）
    """
    result = []
    for h in hits:
        if h.get("bm25_match"):
            result.append(h)
            continue
        dist = h.get("distance", 1.0)
        # BM25-only 命中的笔记 distance 字段不存在或为 1.0（默认值）
        is_bm25_only = "distance" not in h or dist >= 0.999
        if is_bm25_only or dist < threshold:
            result.append(h)
    return result
