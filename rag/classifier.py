"""
rag/classifier.py
=================
智能分类：用 GLM-4.6（非 thinking 模式）对笔记内容进行分类。

对外接口：
    classify_note(note: dict) -> str          单条分类，返回分类名
    classify_notes(notes: list[dict]) -> dict  批量分类，返回 {note_id: category}

命令行入口（供 subprocess 调用）：
    python -m rag.classifier --user_id <user_id> [--batch_size 10]

失败时 fallback 空字符串，不阻塞同步流程。
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_CLASSIFY_MODEL = "glm-4.6"
_NO_THINKING    = {"thinking": {"type": "disabled"}}

_SYSTEM_PROMPT = (
    "你是一个内容分类助手。根据笔记的标题、正文摘要和已有标签，"
    "判断它最可能属于哪个分类。只返回分类名，不要解释。\n\n"
    "可选分类（按优先级排列）：\n"
    "1. 好物推荐 — 产品推荐、购物清单、性价比分析\n"
    "2. 穿搭美妆 — 穿搭技巧、美妆教程、造型灵感\n"
    "3. 家居生活 — 家居装饰、收纳整理、生活方式\n"
    "4. 旅游攻略 — 旅行攻略、景点推荐、行程规划\n"
    "5. 求职面经 — 面试经验、公司评价、求职技巧\n"
    "6. 考研考证 — 考研/考证/考公经验、备考资料\n"
    "7. 学习方法 — 学习方法论、效率工具、记忆技巧\n"
    "8. 健身饮食 — 健身教程、饮食计划、健康管理\n"
    "9. 职场技巧 — 职场沟通、晋升、副业、创业\n"
    "10. 情绪自律 — 情绪管理、自律习惯、心理健康\n"
    "11. 城市生活 — 探店、本地美食、城市活动\n"
    "12. 其他 — 无法归入以上类别\n\n"
    "如果内容确实无法匹配以上任何类别，请给出一个简短的新类别名"
    "（不超过 4 个字）。只返回类别名。"
)


def _build_user_message(note: dict) -> str:
    """构建分类 prompt 的 user message。"""
    title = note.get("title", "") or "无标题"
    tags = note.get("tags", [])
    tags_str = "、".join(tags) if tags else "无"
    content = note.get("content", "") or ""
    summary = content[:500]
    return (
        f"标题：{title}\n"
        f"已有标签：{tags_str}\n"
        f"内容摘要：{summary}"
    )


def classify_note(note: dict) -> str:
    """
    对单条笔记进行分类。

    参数：
        note   dict，需含字段：note_id, title, content, tags

    返回：
        str — 分类名，失败时返回空字符串
    """
    if zhipu_client is None:
        logger.warning("[classifier] zhipu_client 未配置，跳过分类")
        return ""

    try:
        resp = zhipu_client.chat.completions.create(
            model=_CLASSIFY_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": _build_user_message(note)},
            ],
            temperature=0.0,
            max_tokens=20,
            extra_body=_NO_THINKING,
        )
        category = (resp.choices[0].message.content or "").strip()
        note_id = note.get("note_id", "?")
        logger.info(f"[classifier] {note_id} → {category!r}")
        return category
    except Exception as e:
        logger.warning(f"[classifier] {note.get('note_id', '?')} 分类失败：{e}")
        return ""


def classify_notes(notes: list[dict]) -> dict[str, str]:
    """
    批量分类，返回 {note_id: category} 映射。

    每条笔记独立调用 LLM（一次一个分类），失败笔记映射为空字符串。
    """
    result: dict[str, str] = {}
    for note in notes:
        category = classify_note(note)
        result[note["note_id"]] = category
    return result


# ── 子进程入口 ───────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="AI 智能分类未标记笔记")
    parser.add_argument("--user_id", required=True, help="小红书用户 ID")
    parser.add_argument("--batch_size", type=int, default=10, help="每批处理条数")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    from rag.storage.sqlite_store import SQLiteStore

    db_path = str(PROJECT_ROOT / "data" / "notes.db")

    with SQLiteStore(db_path) as sqlite:
        unclassified = sqlite.get_unclassified(user_id=args.user_id, limit=args.batch_size)

    if not unclassified:
        logger.info("没有需要分类的笔记")
        return 0

    logger.info(f"共 {len(unclassified)} 条笔记待分类")

    classified = classify_notes(unclassified)

    with SQLiteStore(db_path) as sqlite:
        for note_id, category in classified.items():
            if category:
                sqlite.set_category(note_id, args.user_id, category)

    success = sum(1 for c in classified.values() if c)
    logger.info(f"分类完成：{success}/{len(classified)} 条成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
