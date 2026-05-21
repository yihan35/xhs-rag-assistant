"""
rag/rewriter.py
===============
Query 改写：用 GLM-4.6（非 thinking 模式）将用户对话式输入
预处理成更适合向量检索的核心语义词组，提升召回准确性。

对外接口：
    rewrite_query(query: str) -> str

示例：
    "帮我搜一下字节跳动后端面试经验" → "字节跳动 后端 面试经验"
    "有没有旅行攻略？" → "旅行攻略"
    "我想了解 MySQL 联合索引失效的场景" → "MySQL 联合索引失效 场景"
"""

import logging
from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_REWRITER_MODEL = "glm-4.6"

_REWRITE_SYSTEM = """\
你是一个搜索词优化助手，专门处理用户对话式输入，提炼出最适合向量检索的核心词组。

规则：
1. 去掉"帮我搜""查一下""有没有""我想了解""请问""告诉我"等口语化前缀/后缀
2. 去掉"你好""麻烦""谢谢"等寒暄
3. 保留所有具体的实体词、动词、限定词（品牌、地名、技术词、人名等）
4. 适当展开缩写（如"xhs"→"小红书"，"jd"→"京东"）
5. 只输出改写后的检索词，不加解释、不加标点句号、不换行

若输入本身已经很精炼（如"MySQL 索引"），原样返回即可。\
"""


def rewrite_query(query: str) -> str:
    """
    用 GLM-4.6 将用户 query 改写为更适合向量检索的核心词组。

    参数：
        query   原始用户输入

    返回：
        改写后的检索词；API 调用失败时返回原始 query。
    """
    if not query or not query.strip():
        return query

    if zhipu_client is None:
        return query

    try:
        resp = zhipu_client.chat.completions.create(
            model=_REWRITER_MODEL,
            messages=[
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user",   "content": query.strip()},
            ],
            temperature=0.1,
            max_tokens=128,
            extra_body={"thinking": {"type": "disabled"}},
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        if rewritten:
            if rewritten != query:
                logger.info(f"[rewriter] '{query}' → '{rewritten}'")
            return rewritten
    except Exception as e:
        logger.warning(f"[rewriter] 改写失败，回退原始 query：{e}")

    return query
