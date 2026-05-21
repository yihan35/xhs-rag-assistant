"""
crawler/get_my_cookies.py
=========================
从本机 Chrome 自动提取小红书 Cookie，写入 data/cookies.json。

运行：
    python -m crawler.get_my_cookies
"""

from __future__ import annotations

from pathlib import Path

from crawler.cookies import (
    NEEDED_KEYS,
    check_required,
    detect_user_id,
    extract_from_chrome,
    save_cookies,
    slim_cookies,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COOKIES_FILE = PROJECT_ROOT / "data" / "cookies.json"


def main() -> int:
    print("正在从 Chrome 提取小红书 Cookie...")
    cookies = slim_cookies(extract_from_chrome())
    check_required(cookies)
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_cookies(cookies, str(COOKIES_FILE))

    print(f"[OK] 已写入 {COOKIES_FILE}（共 {len(cookies)} 个字段）")
    print(f"     包含必填字段：{NEEDED_KEYS & set(cookies)}")

    print("\n正在获取 user_id...")
    try:
        uid = detect_user_id(cookies)
        if uid:
            print(f"[OK] 检测到 user_id = {uid}")
            print("     crawler/ingest.py 会优先自动使用这个登录用户")
        else:
            print("[INFO] 自动检测失败。请设置环境变量 XHS_USER_ID")
    except Exception as e:
        print(f"[WARN] 自动获取 user_id 失败：{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
