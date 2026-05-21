"""
rag/expander.py
===============
Query 语义扩展：用 GLM-4.6 为核心检索词生成 2 个同义/相关词组，
供多路检索使用，提升同义词和相关词的召回覆盖率。

对外接口：
    expand_query(query: str) -> list[str]

示例：
    "减脂食谱" → ["低卡料理", "健身饮食"]
    "MySQL 索引优化" → ["数据库查询性能", "SQL 执行计划"]
    "日本旅行攻略" → ["东京自由行", "日本景点推荐"]

说明：
    - 返回列表不含原始 query（原始 query 由调用方自行持有）
    - API 失败或扩展词为空时静默返回 []，调用方降级为单路检索
"""

import logging
from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_EXPANDER_MODEL = "glm-4.6"

_EXPAND_SYSTEM = """\
你是一个搜索词扩展助手。给定一个检索词组，生成 2 个语义相关但表达不同的同义/上位词检索词组，
帮助扩大检索覆盖范围。

规则：
1. 每行输出一个检索词组，共输出 2 行
2. 词组应与原词语义相关，但用词不同（同义词、上位词、相关领域词）
3. 每个词组控制在 2-6 个词以内
4. 只输出词组，不加序号、不加解释、不加标点句号
5. 如果原词已经很宽泛（如"旅行"），可以给出更具体的相关词

示例：
输入：减脂食谱
输出：
低卡料理
健身饮食计划\
"""


def expand_query(query: str) -> list[str]:
    """
    为检索词生成 2 个语义扩展词组。

    参数：
        query   已经过 rewrite_query 处理的核心检索词

    返回：
        list[str]，0-2 个扩展词组；失败时返回 []。
    """
    if not query or not query.strip():
        return []

    if zhipu_client is None:
        return []

    try:
        resp = zhipu_client.chat.completions.create(
            model=_EXPANDER_MODEL,
            messages=[
                {"role": "system", "content": _EXPAND_SYSTEM},
                {"role": "user",   "content": query.strip()},
            ],
            temperature=0.4,   # 适当多样性，但不要太发散
            max_tokens=64,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            return []

        expanded = [line.strip() for line in raw.splitlines() if line.strip()]
        # 去掉与原始 query 完全相同的词（去重）
        expanded = [e for e in expanded if e != query][:2]

        if expanded:
            logger.info(f"[expander] '{query}' → {expanded}")
        return expanded

    except Exception as e:
        logger.warning(f"[expander] 扩展失败，降级为单路检索：{e}")
        return []
