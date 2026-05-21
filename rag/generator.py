"""
rag/generator.py
================
生成：双模式问答（search / analysis）。
负责人：李奕涵

对外接口（后端调用）：
    generate(query, retrieved_docs, mode) -> dict
        非流式，search 模式直接返回帖子列表，analysis 模式调用 LLM。

    generate_stream(query, retrieved_docs) -> Generator[str]
        流式，仅用于 analysis 模式，逐块 yield LLM 输出文本。
        sources 需由调用方（main.py SSE 端点）在流开始前单独发送。

返回格式（generate，与后端接口契约一致）：
    {
        "mode":    "search" | "analysis",
        "answer":  str | None,   # search 模式为 None
        "sources": [{"note_id", "title", "note_url", "cover_url"}, ...]
    }
"""

import logging
from typing import Generator
from .chat import analyze as _analyze, analyze_stream as _analyze_stream

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是用户的私人小红书收藏助手「拾光智行」。

## 你的工作方式

用户会向你提问，同时你会收到从他收藏夹中检索到的相关笔记原文作为参考资料。

请按以下思路回答：
1. **优先提炼笔记中的核心内容**：具体案例、真实经验、数据、操作步骤等第一手信息
2. **用自身知识补充和解释**：对笔记内容背后的原理、概念做扩展说明，帮用户更好地理解
3. **综合给出实用建议**：结合笔记经验和通用知识，给出对用户真正有帮助的回答

## 回答格式要求
- 使用清晰的结构（如「## 标题」「- 要点」），方便阅读
- 引用笔记中的具体内容时，可注明来源（如"根据你收藏的面经…"）
- 如果检索到的笔记与问题关联度不高，直接基于自身知识回答，并说明"收藏中暂无相关笔记，以下为通用参考"

## 语气
简洁、友好、实用，像一个熟悉用户收藏内容的智能朋友。"""


def _build_sources(retrieved_docs: list[dict]) -> list[dict]:
    """从检索结果提取 sources 字段（无 content，供前端展示用）。"""
    return [
        {
            "note_id":   doc.get("note_id", ""),
            "title":     doc.get("title", ""),
            "note_url":  doc.get("note_url", ""),
            "cover_url": doc.get("cover_url", ""),
        }
        for doc in retrieved_docs
    ]


def generate(query: str, retrieved_docs: list[dict], mode: str) -> dict:
    """
    双模式生成（非流式）。

    参数：
        query          用户问题
        retrieved_docs retrieve() 的返回值，list[dict]，含 content / title / note_id
        mode           "search"（直接返回帖子列表）或 "analysis"（LLM 总结）

    返回：
        {
            "mode":    str,
            "answer":  str | None,
            "sources": list[dict]
        }
    """
    sources = _build_sources(retrieved_docs)

    if mode == "search":
        return {"mode": "search", "answer": None, "sources": sources}

    # analysis 模式：把原文喂给 LLM
    answer = _analyze(
        user_query=query,
        context_notes=retrieved_docs,
        system_prompt=SYSTEM_PROMPT,
    )

    return {"mode": "analysis", "answer": answer, "sources": sources}


def generate_stream(
    query: str,
    retrieved_docs: list[dict],
) -> Generator[str, None, None]:
    """
    流式 analysis 生成，逐块 yield LLM 输出文本。

    仅供 analysis 模式的 SSE 端点调用；search 模式不需要流式。
    sources 由调用方在流开始前单独发送给前端。

    参数：
        query          用户原始问题（用于 LLM prompt，不是改写后的检索词）
        retrieved_docs retrieve() 的返回值

    yield：str，每块 LLM 输出文本片段
    """
    yield from _analyze_stream(
        user_query=query,
        context_notes=retrieved_docs,
        system_prompt=SYSTEM_PROMPT,
    )
