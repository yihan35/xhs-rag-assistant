"""
rag/chat.py
===========
智谱 GLM 对话接口，用于 analysis 模式（召回原文 → 生成总结/回答）。

GLM-5.1 thinking 模式默认关闭（需显式 enabled 才开启），
此处通过 extra_body 明确指定 disabled，避免未来默认行为变化带来的影响。

对外接口：
    analyze(...)                     → str            非流式，返回完整回答
    analyze_stream(...)              → Generator[str] 流式，逐块 yield content
    build_analysis_user_message(...) → str            构建含完整笔记原文的首条 user message
    analyze_stream_with_history(...) → Generator[str] 多轮对话路径，不重复传原文
"""

import logging
from typing import Generator

from .llm_config import zhipu_client, CHAT_MODEL

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = (
    "你是一个小红书收藏笔记助手。"
    "用户会提供若干篇从收藏夹检索到的笔记原文，"
    "请根据这些内容简洁、准确地回答用户的问题。"
    "如果笔记中没有相关信息，直接说明，不要编造。"
)

# 关闭 thinking 模式（GLM-5.1 虽默认关闭，此处显式声明防止行为漂移）
_NO_THINKING = {"thinking": {"type": "disabled"}}


def build_analysis_user_message(
    user_query: str,
    context_notes: list[dict],
) -> str:
    """
    构建首次分析的 user message 字符串（含完整笔记原文）。
    由 session_handler 调用，存入 session.messages[0]，
    后续追问不再重复传入原文，上下文不膨胀。
    """
    notes_text = "\n\n---\n\n".join(
        f"**笔记 {i}：{n.get('title', '无标题')}**\n{n.get('content', '（内容为空）')}"
        for i, n in enumerate(context_notes, 1)
    )
    return (
        f"# 参考资料（来自你的收藏笔记，共 {len(context_notes)} 篇）\n\n"
        f"{notes_text}\n\n"
        f"---\n\n"
        f"# 我的问题\n\n{user_query}"
    )


def _build_messages(
    user_query: str,
    context_notes: list[dict],
    system_prompt: str,
) -> list[dict]:
    """拼接 system + user 消息，含笔记上下文。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": build_analysis_user_message(user_query, context_notes)},
    ]


def analyze(
    user_query: str,
    context_notes: list[dict],
    system_prompt: str = _DEFAULT_SYSTEM,
    max_tokens: int = 4096,
    temperature: float = 0.5,
) -> str:
    """
    非流式：将召回的笔记拼入上下文，调用 GLM 生成总结或回答。

    参数：
        user_query     用户问题
        context_notes  list[dict]，每条含 title / content / note_id
        system_prompt  系统提示（可自定义）
        max_tokens     最大输出 token 数
        temperature    生成温度

    返回：模型回答文本，失败时返回空字符串。
    """
    if zhipu_client is None:
        raise EnvironmentError("ZHIPUAI_API_KEY 未设置，无法调用 Chat API")

    if not context_notes:
        return "未找到相关笔记，请尝试换个关键词搜索。"

    try:
        response = zhipu_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=_build_messages(user_query, context_notes, system_prompt),
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=_NO_THINKING,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Chat API 调用失败：{e}")
        return ""


def analyze_stream(
    user_query: str,
    context_notes: list[dict],
    system_prompt: str = _DEFAULT_SYSTEM,
    max_tokens: int = 4096,
    temperature: float = 0.5,
) -> Generator[str, None, None]:
    """
    流式版：逐块 yield content 字符串，供 SSE 端点使用。

    参数与 analyze() 相同。失败时 raise，由调用方捕获后发送错误事件。
    """
    if zhipu_client is None:
        raise EnvironmentError("ZHIPUAI_API_KEY 未设置，无法调用 Chat API")

    if not context_notes:
        yield "未找到相关笔记，请尝试换个关键词搜索。"
        return

    stream = zhipu_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=_build_messages(user_query, context_notes, system_prompt),
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        extra_body=_NO_THINKING,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None) or ""
        if content:
            yield content


def analyze_stream_with_history(
    messages: list[dict],
    system_prompt: str = _DEFAULT_SYSTEM,
    max_tokens: int = 4096,
    temperature: float = 0.5,
) -> Generator[str, None, None]:
    """
    多轮对话路径：直接用已有 messages 历史调 LLM，逐块 yield content。

    用于：
      - 首次分析（messages = [user(含原文)]）
      - 追问（messages = [user(含原文), assistant, user, ...]）

    不重复传原文，上下文不膨胀。
    由调用方（session_handler）负责 append + save。

    参数：
        messages  完整 LLM 对话历史，由 session_handler 维护
    """
    if zhipu_client is None:
        raise EnvironmentError("ZHIPUAI_API_KEY 未设置，无法调用 Chat API")

    if not messages:
        yield "暂无上下文，请先提问。"
        return

    stream = zhipu_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        extra_body=_NO_THINKING,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None) or ""
        if content:
            yield content
