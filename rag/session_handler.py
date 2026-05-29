"""
rag/session_handler.py
======================
会话状态机：封装 search / 首次分析 / 追问 三条路径。

对外接口：
    handle_search(req, session_store) -> dict
        search 模式：检索 + 存 docs，不调 LLM。
        返回 {"mode": "search", "sources": [...]}

    handle_stream(req, session_store) -> tuple[list[dict], Generator[str, None, None]]
        analysis 模式：判断追问/新话题，返回 (sources, chunk_generator)。
        chunk_generator 流式 yield LLM 输出，完成后自动存入 session。

判断优先级：
    1. docs 已锁定 + 上轮 LLM 已回复（messages[-1] == assistant） + is_followup → 追问，不检索
    2. docs 已锁定 + messages 为空（搜索后切换）→ 复用 docs，首次分析
    3. docs 为空 → 重新检索，首次分析
    4. docs 已锁定 + messages 有内容 + is_followup=False → 新话题，重新检索
"""

import logging
from typing import Generator

from .retriever import retrieve
from .followup import is_followup
from .chat import build_analysis_user_message, analyze_stream_with_history
from .generator import SYSTEM_PROMPT
from .storage.session_store import SessionStore

logger = logging.getLogger(__name__)


def _new_state() -> dict:
    return {"docs": None, "messages": [], "last_query": None}


def _format_sources(docs: list[dict]) -> list[dict]:
    return [
        {
            "note_id":   d.get("note_id", ""),
            "title":     d.get("title", ""),
            "note_url":  d.get("note_url", ""),
            "cover_url": d.get("cover_url", ""),
            "distance":  d.get("distance", 0.0),
        }
        for d in (docs or [])
    ]


def _stream_and_save(
    state: dict,
    session_id: str,
    user_id: str,
    session_store: SessionStore,
) -> Generator[str, None, None]:
    """
    流式输出 LLM 回复，完成（或被中断）后将 assistant message 存入 session。
    finally 块确保即使客户端断连也能保存已生成的内容。
    """
    full_reply = ""
    try:
        for chunk in analyze_stream_with_history(state["messages"], system_prompt=SYSTEM_PROMPT):
            full_reply += chunk
            yield chunk
    finally:
        # abort-with-empty-reply: full_reply 为空时跳过 append，
        # 但 state["messages"] 里可能已有新 user message（由调用方提前写入）。
        # 下次请求时 messages[-1].role == "user"，has_prior_analysis=False，
        # 状态机会自动走非追问路径重新分析，已有 user message 被覆盖。
        # 这是可接受的降级行为：LLM 未回复 → 下次请求视为新分析。
        if full_reply:
            state["messages"].append({"role": "assistant", "content": full_reply})
        session_store.save(session_id, user_id, state)


def handle_search(req, session_store: SessionStore) -> dict:
    """
    搜索模式：重新检索，保存 docs，清空 LLM 历史，不调 LLM。
    """
    docs = retrieve(req.query, req.user_id, top_k=req.top_k, mode="search")
    state = session_store.get(req.session_id) or _new_state()
    state.update({"docs": docs, "messages": [], "last_query": req.query})
    session_store.save(req.session_id, req.user_id, state)
    logger.info(f"[session] {req.session_id[:8]}… search → {len(docs)} docs")
    return {"mode": "search", "sources": _format_sources(docs)}


def handle_stream(
    req,
    session_store: SessionStore,
) -> tuple[list[dict], Generator[str, None, None]]:
    """
    分析模式：判断追问 / 新话题，返回 (sources, chunk_generator)。

    sources 在 SSE 流开始前推送给前端；
    chunk_generator 流式 yield LLM 输出，完成后自动存入 session。
    """
    state = session_store.get(req.session_id) or _new_state()

    # 追问判断：docs 已锁定 + 上轮 LLM 已回复 + 新问题被判为追问
    has_prior_analysis = bool(
        state["docs"]
        and state["messages"]
        and state["messages"][-1]["role"] == "assistant"
    )
    if has_prior_analysis and is_followup(req.query, state):
        logger.info(f"[session] {req.session_id[:8]}… → 追问，不检索")
        sources = _format_sources(state["docs"])
        state["messages"].append({"role": "user", "content": req.query})
        state["last_query"] = req.query
        # 两次 save 是刻意设计：此处先持久化 user message（crash 安全），
        # _stream_and_save 的 finally 在流结束后再追加 assistant 并第二次 save。
        # 流被中断时只有 user message 存档，下次请求因 messages[-1].role==user
        # 不满足 has_prior_analysis 而走重新分析路径，是已知且可接受的降级行为。
        session_store.save(req.session_id, req.user_id, state)
        return sources, _stream_and_save(state, req.session_id, req.user_id, session_store)

    # 新话题 / 首次分析
    if not state["docs"]:
        # docs 为空：直接分析或新话题，重新检索
        docs = retrieve(req.query, req.user_id, top_k=req.top_k, mode="analysis")
        state["docs"] = docs
        logger.info(f"[session] {req.session_id[:8]}… → 首次分析，检索 {len(docs)} docs")
    else:
        # docs 已有
        if has_prior_analysis:
            # 明确的新话题：重新检索，刷新 docs
            docs = retrieve(req.query, req.user_id, top_k=req.top_k, mode="analysis")
            state["docs"] = docs
            logger.info(f"[session] {req.session_id[:8]}… → 新话题，刷新 docs")
        else:
            # 搜索后切换来的：复用 docs
            docs = state["docs"]
            logger.info(f"[session] {req.session_id[:8]}… → 复用 search docs")

    sources = _format_sources(docs)
    first_msg = build_analysis_user_message(req.query, docs)
    state["messages"] = [{"role": "user", "content": first_msg}]
    state["last_query"] = req.query
    session_store.save(req.session_id, req.user_id, state)
    return sources, _stream_and_save(state, req.session_id, req.user_id, session_store)
