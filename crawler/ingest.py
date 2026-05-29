"""
crawler/ingest.py
=================
全量同步：小红书收藏夹 → SQLite + ChromaDB

运行：
    python -m crawler.ingest

流程：
  1. 从 data/cookies.json 加载认证信息；不存在时自动从 Chrome 提取
  2. 自动检测当前登录用户 user_id；也可用 XHS_USER_ID 显式指定
  3. Playwright 拦截收藏夹 API，拿到 note_id + xsec_token 列表
  4. 归档已取消收藏的历史笔记，并从 ChromaDB 移除向量
  5. 复用同一个浏览器会话逐条爬取详情（已 indexed 的自动跳过）
  6. 写入 SQLite（元数据）+ ChromaDB（向量）
"""

from __future__ import annotations

import logging
import os
import random
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from crawler import XHSCrawler, detect_user_id, load_or_extract_cookies
from crawler.cover_cache import cache_cover_image
from rag.storage import NoteStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,   # 子进程 stdout 会被重定向到 data/sync.log
)
logger = logging.getLogger(__name__)

COOKIES_FILE = str(PROJECT_ROOT / "data" / "cookies.json")
MY_USER_ID = os.getenv("XHS_USER_ID", "")

# ── 爬取速率控制 ──────────────────────────────────────────────────
# 每条笔记爬取成功后的随机等待区间（秒）
# 收到风控警告时建议调大，例如 (8, 15)
CRAWL_DELAY_MIN = float(os.getenv("CRAWL_DELAY_MIN", "5"))
CRAWL_DELAY_MAX = float(os.getenv("CRAWL_DELAY_MAX", "10"))

DB_PATH = str(PROJECT_ROOT / "data" / "notes.db")
CHROMA_PATH = str(PROJECT_ROOT / "data" / "chroma_db")


def resolve_user_id(explicit_user_id: str, detected_user_id: str) -> str:
    """Resolve the user_id used for syncing."""
    return (explicit_user_id or detected_user_id or "").strip()


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def main() -> int:
    started = time.perf_counter()

    cookies = load_or_extract_cookies(COOKIES_FILE)
    logger.info(f"Cookie 已加载（{len(cookies)} 个字段）")

    try:
        detected_user_id = detect_user_id(cookies)
    except Exception as e:
        detected_user_id = ""
        logger.warning(f"自动检测 user_id 失败：{e}")

    user_id = resolve_user_id(MY_USER_ID, detected_user_id)
    if not user_id:
        logger.error("无法确定 user_id。请先在 Chrome 登录小红书，或设置环境变量 XHS_USER_ID")
        return 1
    logger.info(f"本次同步用户：{user_id}")

    found_count = 0
    new_count = updated_count = skipped_count = error_count = 0
    archived_count = 0

    with XHSCrawler(cookies) as crawler:
        logger.info("正在获取收藏夹列表（将打开浏览器）...")
        note_metas = crawler.fetch_collect_list(user_id)
        found_count = len(note_metas)
        logger.info(f"收藏夹共 {found_count} 条笔记")

        with NoteStore(db_path=DB_PATH, chroma_path=CHROMA_PATH) as store:
            before = store.stats()
            logger.info(
                f"存储层就绪：SQLite {before['sqlite_total']} 条，"
                f"ChromaDB {before['chroma_indexed']} 条已向量化"
            )

            current_note_ids = {meta["note_id"] for meta in note_metas if meta.get("note_id")}
            archived_ids = store.archive_missing(user_id, current_note_ids)
            archived_count = len(archived_ids)
            if archived_count:
                logger.info(f"本次归档已取消收藏笔记：{archived_count} 条")

            if not note_metas:
                logger.warning("收藏夹为空，已将该用户原有当前收藏全部归档")
                after = store.stats()
                print_sync_summary(
                    started,
                    found_count,
                    0,
                    0,
                    0,
                    0,
                    archived_count,
                    after["sqlite_total"],
                    after["chroma_indexed"],
                )
                return 0

            for i, meta in enumerate(note_metas, 1):
                note_id = meta["note_id"]
                token = meta.get("xsec_token", "")
                title = meta.get("title", "")[:30]
                logger.info(
                    f"──────────────────────────────────────────────────────"
                )
                logger.info(
                    f"▶ 正在同步第 {i} 篇 / 共 {found_count} 篇"
                    f"  [{note_id}]  {title!r}"
                )

                if store.sqlite.is_indexed(note_id, user_id):
                    cached_cover = cache_cover_image(note_id, meta.get("cover_url", ""))
                    if cached_cover and cached_cover != meta.get("cover_url", ""):
                        store.sqlite.update_cover_url(note_id, user_id, cached_cover)
                    logger.info("  跳过（已在向量库中）")
                    skipped_count += 1
                    continue

                try:
                    note = crawler.fetch_note_detail(note_id, xsec_token=token)
                except Exception as e:
                    logger.error(f"  爬取失败：{e}")
                    error_count += 1
                    continue

                if note is None:
                    logger.warning("  笔记不可访问，跳过")
                    skipped_count += 1
                    continue

                is_new = store.save(note, user_id=user_id)
                if is_new:
                    new_count += 1
                else:
                    updated_count += 1

                # 随机延迟，避免请求过于密集触发小红书风控
                delay = random.uniform(CRAWL_DELAY_MIN, CRAWL_DELAY_MAX)
                logger.info(f"  等待 {delay:.1f}s 后继续...")
                time.sleep(delay)

            after = store.stats()

    print_sync_summary(
        started,
        found_count,
        new_count,
        updated_count,
        skipped_count,
        error_count,
        archived_count,
        after["sqlite_total"],
        after["chroma_indexed"],
    )
    return 0


def print_sync_summary(
    started: float,
    found_count: int,
    new_count: int,
    updated_count: int,
    skipped_count: int,
    error_count: int,
    archived_count: int,
    sqlite_total: int,
    chroma_indexed: int = 0,
) -> None:
    elapsed = format_duration(time.perf_counter() - started)
    success_count = new_count + updated_count

    print()
    print("=" * 56)
    print("  小红书收藏同步完成")
    print(f"  发现收藏：{found_count}")
    print(f"  爬取成功：{success_count}  新增：{new_count}  更新：{updated_count}")
    print(f"  跳过：{skipped_count}  失败：{error_count}  归档：{archived_count}")
    print(f"  SQLite 当前收藏：{sqlite_total} 条")
    print(f"  ChromaDB 已向量化：{chroma_indexed} 条")
    print(f"  本次耗时：{elapsed}")
    print("=" * 56)


if __name__ == "__main__":
    raise SystemExit(main())
