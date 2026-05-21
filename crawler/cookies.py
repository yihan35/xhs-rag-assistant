"""
Cookie helpers for XHS crawling.

This module contains only file/browser-cookie concerns. Playwright crawling code
imports these helpers instead of duplicating cookie handling.
"""

import json
import re
import sys

import requests

NEEDED_KEYS = {"a1", "web_session", "webId"}
OPTIONAL_KEYS = {"acw_tc", "gid", "sec_poison_id"}


def load_cookies(path: str = "cookies.json") -> dict:
    """Load a cookie dict from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cookies(cookies: dict, path: str = "cookies.json") -> None:
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def cookies_to_str(cookies: dict) -> str:
    """Convert a cookie dict to the header format used by xhs."""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def extract_from_chrome() -> dict:
    """Read XHS cookies from the local Chrome profile."""
    try:
        import browser_cookie3  # type: ignore
    except ImportError:
        print("[ERROR] 请先安装：pip install browser-cookie3")
        sys.exit(1)

    jar = browser_cookie3.chrome(domain_name=".xiaohongshu.com")
    cookies = {c.name: c.value for c in jar}
    if not cookies:
        print("[ERROR] 未找到小红书 Cookie，请确认已在 Chrome 登录 xiaohongshu.com")
        sys.exit(1)
    return cookies


def slim_cookies(cookies: dict) -> dict:
    """Keep known useful cookies, falling back to the full set if required keys are missing."""
    keep = NEEDED_KEYS | OPTIONAL_KEYS
    slim = {k: v for k, v in cookies.items() if k in keep}
    return slim if NEEDED_KEYS.issubset(slim) else cookies


def check_required(cookies: dict) -> bool:
    ok = True
    missing = NEEDED_KEYS - set(cookies.keys())
    if missing:
        print(f"[WARN] 缺少关键 Cookie：{missing}")
        ok = False

    if not cookies.get("web_session", ""):
        print("[WARN] web_session 为空")
        ok = False

    if not ok:
        print()
        print("手动提取方法（Mac）：")
        print("  1. Chrome 打开 https://www.xiaohongshu.com 并登录")
        print("  2. Cmd+Option+I 打开 DevTools → Application → Cookies")
        print("  3. 找到 web_session，单击后在底部复制完整 Value")
        print("  4. 编辑 cookies.json，把 web_session 的值替换")
    return ok


def parse_user_id_from_homepage_html(html: str) -> str:
    match = re.search(r'"userId"\s*:\s*"([^"]+)"', html)
    return match.group(1) if match else ""


def detect_user_id(cookies: dict) -> str:
    """Best-effort user_id detection from the logged-in homepage."""
    session = requests.Session()
    session.headers.update({
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        "referer": "https://www.xiaohongshu.com/",
    })
    session.cookies.update(cookies)
    resp = session.get("https://www.xiaohongshu.com", timeout=10)
    return parse_user_id_from_homepage_html(resp.text)


def load_or_extract_cookies(path: str = "cookies.json") -> dict:
    """Load cookies from disk, or extract and persist them from Chrome if missing."""
    try:
        return load_cookies(path)
    except FileNotFoundError:
        print(f"[INFO] 找不到 {path}，尝试从 Chrome 自动提取小红书 Cookie...")

    cookies = slim_cookies(extract_from_chrome())
    check_required(cookies)
    save_cookies(cookies, path)
    print(f"[OK] 已写入 {path}（共 {len(cookies)} 个字段）")
    return cookies
