"""
rag/followup.py
===============
追问判断：用 GLM-4.6（非 thinking 模式）快速分类。

对外接口：
    is_followup(query: str, state: dict) -> bool

失败时 fallback False（降级为重新检索，不会出错）。
"""

import logging
from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_FOLLOWUP_MODEL = "glm-4.6"
_NO_THINKING    = {"thinking": {"type": "disabled"}}

_SYSTEM = (
    "判断用户的新问题是否是对上一个问题的追问或延伸（同一话题），"
    "还是一个全新的话题。只回答 yes 或 no，不要解释。"
)


def is_followup(query: str, state: dict) -> bool:
    """
    判断 query 是否是对 state["last_query"] 的追问。

    参数：
        query   当前用户问题
        state   session state，含 last_query 字段

    返回：
        True  = 追问，不需要重新检索
        False = 新话题（或判断失败），需要重新检索
    """
    last = state.get("last_query")
    if not last:
        return False

    if zhipu_client is None:
        logger.warning("[followup] zhipu_client 未配置，跳过追问判断")
        return False

    try:
        resp = zhipu_client.chat.completions.create(
            model=_FOLLOWUP_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": f"上一个问题：{last}\n新问题：{query}"},
            ],
            temperature=0.0,
            max_tokens=5,
            extra_body=_NO_THINKING,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        result = answer.startswith("yes")
        logger.info(f"[followup] '{query[:30]}' → {'追问' if result else '新话题'}")
        return result
    except Exception as e:
        logger.warning(f"[followup] 判断失败，fallback False：{e}")
        return False
