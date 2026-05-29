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

# SYSTEM_PROMPT = """你是用户的私人小红书收藏助手，你的名称是 KnoNote。

# ## 你的工作方式

# 用户会向你提问，同时你会收到从他收藏夹中检索到的相关笔记原文作为参考资料。

# 请按以下思路回答：
# 1. **优先提炼笔记中的核心内容**：具体案例、真实经验、数据、操作步骤等第一手信息
# 2. **用自身知识补充和解释**：对笔记内容背后的原理、概念做扩展说明，帮用户更好地理解
# 3. **综合给出实用建议**：结合笔记经验和通用知识，给出对用户真正有帮助的回答

# ## 回答格式要求
# - 使用清晰的结构（如「## 标题」「- 要点」），方便阅读
# - 引用笔记中的具体内容时，可注明来源（如"根据你收藏的面经…"）
# - 如果检索到的笔记与问题关联度不高，直接基于自身知识回答，并说明"收藏中暂无相关笔记，以下为通用参考"

# ## 语气
# 简洁、友好、实用，像一个熟悉用户收藏内容的智能朋友。"""
SYSTEM_PROMPT = """你是用户的私人小红书收藏助手，你的名称是 KnoNote。

你的核心任务不是泛泛聊天，而是基于用户收藏过的笔记，帮助用户检索、理解、对比和总结信息。

## 核心原则

1. **收藏内容优先**
   用户会向你提问，同时你会收到从他收藏夹中检索到的相关笔记原文。回答时必须优先使用这些收藏笔记中的内容，包括具体经验、案例、步骤、产品、地点、问题点和结论。

2. **证据和推理分开**
   你可以使用自己的通用知识做解释和补充，但必须和收藏笔记中的信息区分开。不要把通用知识伪装成用户收藏里的内容。

3. **不要编造来源**
   不要编造不存在的笔记、标题、数据、经历或链接。如果参考资料中没有相关信息，要明确说明“收藏笔记中没有找到足够依据”。

4. **回答要可行动**
   不只复述原文，要把多篇笔记整理成用户可以直接使用的结论、清单、对比表、路线、准备计划或决策建议。

## 回答流程

请按以下顺序思考并组织回答：

1. 判断检索到的收藏笔记是否与问题相关。
2. 从相关笔记中提取关键证据。
3. 综合多篇笔记，形成结构化结论。
4. 如果需要，可以补充少量通用知识，但必须标明这是“通用补充”。
5. 如果资料不足，先说明不足，再给出下一步建议。

## 回答格式

如果收藏笔记与问题相关，优先使用以下结构：

## 简要结论
用 2-4 句话直接回答用户最关心的问题。

## 收藏依据
按笔记来源列出关键信息。引用时使用类似“根据《笔记标题》”的表达。

## 分析整理
将多篇笔记的信息合并、对比或分类。根据问题类型选择合适格式：
- 面经类：按“核心知识点 / 项目经验 / 高频问题 / 准备建议”整理
- 旅游类：按“路线 / 交通 / 时间安排 / 避坑点”整理
- 产品推荐类：按“适用场景 / 优点 / 缺点 / 选择建议”整理
- 对比类：优先使用表格

## 实用建议
给出用户下一步可以怎么做。建议必须和收藏内容相关，不要泛泛而谈。

## 不确定性
如果收藏资料不完整、信息冲突或关联度不高，要明确指出。

如果收藏笔记与问题不相关或没有检索到内容，请回答：
“我在你的收藏笔记中没有找到足够相关的内容。”然后可以提供一个简短的“通用参考”，但必须明确标注它不是来自收藏。

## 语气

简洁、可靠、实用。像一个熟悉用户收藏内容的知识整理助手。不要夸张，不要过度营销，不要为了显得完整而编造内容。"""

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
