"""
One-off repair script for covers that were saved before local cover caching.

Usage:
    python tests/repair_existing_covers.py
    XHS_USER_ID=... python tests/repair_existing_covers.py
    python tests/repair_existing_covers.py --db-only

Default mode opens the XHS collect page, reads fresh cover URLs from the
collection list, caches them into data/covers, and updates notes.cover_url to
/covers/<file>. It does not re-fetch note details or rebuild vectors.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from crawler import XHSCrawler, detect_user_id, load_or_extract_cookies
from crawler.cover_cache import cache_cover_image
from rag.storage.sqlite_store import SQLiteStore

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "notes.db"
DEFAULT_COOKIES_FILE = PROJECT_ROOT / "data" / "cookies.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair existing note covers by caching them locally."
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="SQLite notes database path.",
    )
    parser.add_argument(
        "--cookies-file",
        default=str(DEFAULT_COOKIES_FILE),
        help="XHS cookies JSON file.",
    )
    parser.add_argument(
        "--user-id",
        default=os.getenv("XHS_USER_ID", ""),
        help="XHS user_id. Defaults to XHS_USER_ID, the single active SQLite user, or auto detection.",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="Do not open XHS. Only cache remote cover URLs already stored in SQLite.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned SQLite updates without changing notes.cover_url.",
    )
    return parser.parse_args()


def infer_single_user_id(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id
            FROM notes
            WHERE is_collected = 1 AND user_id != ''
            """
        ).fetchall()
    finally:
        conn.close()

    return rows[0][0] if len(rows) == 1 else ""


def resolve_user_id(explicit_user_id: str, cookies_file: str, db_path: str) -> tuple[str, dict]:
    cookies = load_or_extract_cookies(cookies_file)

    if explicit_user_id:
        return explicit_user_id.strip(), cookies

    inferred = infer_single_user_id(db_path)
    if inferred:
        logger.info(f"从 SQLite 推断 user_id：{inferred}")
        return inferred, cookies

    try:
        detected = detect_user_id(cookies).strip()
    except Exception as exc:
        logger.warning(f"自动检测 user_id 失败：{exc}")
        detected = ""

    if detected:
        return detected, cookies

    return "", cookies


def active_notes_by_id(store: SQLiteStore, user_id: str) -> dict[str, dict]:
    return {
        note["note_id"]: note
        for note in store.all_notes(user_id=user_id)
        if note.get("note_id")
    }


def repair_cover(
    store: SQLiteStore,
    note_id: str,
    user_id: str,
    old_cover_url: str,
    fresh_cover_url: str,
    dry_run: bool,
) -> str:
    if not fresh_cover_url:
        return "no_cover"

    cached_cover = cache_cover_image(note_id, fresh_cover_url)
    if not cached_cover.startswith("/covers/"):
        return "failed"

    if cached_cover == old_cover_url:
        return "already_ok"

    logger.info(f"[{note_id}] cover_url: {old_cover_url!r} -> {cached_cover!r}")
    if not dry_run:
        store.update_cover_url(note_id, user_id, cached_cover)
    return "updated"


def repair_from_collect_list(
    store: SQLiteStore,
    user_id: str,
    cookies: dict,
    dry_run: bool,
) -> dict[str, int]:
    existing_notes = active_notes_by_id(store, user_id)
    stats = {
        "existing": len(existing_notes),
        "collect_found": 0,
        "matched": 0,
        "updated": 0,
        "already_ok": 0,
        "failed": 0,
        "no_cover": 0,
        "not_in_db": 0,
    }

    with XHSCrawler(cookies) as crawler:
        metas = crawler.fetch_collect_list(user_id)

    stats["collect_found"] = len(metas)
    for meta in metas:
        note_id = meta.get("note_id", "")
        note = existing_notes.get(note_id)
        if not note:
            stats["not_in_db"] += 1
            continue

        stats["matched"] += 1
        result = repair_cover(
            store=store,
            note_id=note_id,
            user_id=user_id,
            old_cover_url=note.get("cover_url", ""),
            fresh_cover_url=meta.get("cover_url", ""),
            dry_run=dry_run,
        )
        stats[result] += 1

    return stats


def repair_from_db_only(store: SQLiteStore, user_id: str, dry_run: bool) -> dict[str, int]:
    notes = active_notes_by_id(store, user_id)
    stats = {
        "existing": len(notes),
        "updated": 0,
        "already_ok": 0,
        "failed": 0,
        "no_cover": 0,
    }

    for note_id, note in notes.items():
        cover_url = note.get("cover_url", "")
        result = repair_cover(
            store=store,
            note_id=note_id,
            user_id=user_id,
            old_cover_url=cover_url,
            fresh_cover_url=cover_url,
            dry_run=dry_run,
        )
        stats[result] += 1

    return stats


def print_summary(stats: dict[str, int], dry_run: bool) -> None:
    print()
    print("=" * 56)
    print("  封面修复完成" + ("（dry-run，未更新 SQLite）" if dry_run else ""))
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print("=" * 56)
    print()
    print("说明：failed 通常表示远端封面 URL 已失效或被 CDN 拒绝。")
    print("默认模式会用收藏列表里的新 URL 修复；如果仍 failed，前端会显示帖子图标兜底。")


def main() -> int:
    args = parse_args()
    store = SQLiteStore(args.db_path)
    try:
        if args.db_only:
            user_id = args.user_id.strip() or infer_single_user_id(args.db_path)
            if not user_id:
                logger.error("无法确定 user_id。请设置 XHS_USER_ID 或传 --user-id。")
                return 1
            logger.info(f"DB-only 修复用户：{user_id}")
            stats = repair_from_db_only(store, user_id, args.dry_run)
        else:
            user_id, cookies = resolve_user_id(args.user_id, args.cookies_file, args.db_path)
            if not user_id:
                logger.error("无法确定 user_id。请先登录小红书，或设置 XHS_USER_ID / --user-id。")
                return 1
            logger.info(f"收藏列表修复用户：{user_id}")
            stats = repair_from_collect_list(store, user_id, cookies, args.dry_run)

        print_summary(stats, args.dry_run)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
