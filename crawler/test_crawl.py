"""
test_crawl.py
=============
验证爬虫模块是否正常工作。

运行前先完成以下两步：
  Step 0-A：python -m crawler.get_my_cookies    （自动从 Chrome 提取 Cookie）
  Step 0-B：将你的 user_id 填入下方 MY_USER_ID

运行：
  python -m crawler.test_crawl
"""

import json
import logging
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from crawler import (
    fetch_collect_list,
    fetch_note_detail,
    load_cookies,
    dump_initial_state,
)
from rag.storage import NoteStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# ══════════════════════════════════════════════════════════════════════
# ★ 修改这里
# ══════════════════════════════════════════════════════════════════════

COOKIES_FILE = str(PROJECT_ROOT / "data" / "cookies.json")   # crawler/get_my_cookies.py 生成的文件
DEBUG_STATE_FILE = str(PROJECT_ROOT / "data" / "debug_state.json")

MY_USER_ID = "640c4bcc000000002a0088a8"  # 你的小红书 user_id，运行 python -m crawler.get_my_cookies 后会打印出来
                 # 也可以从 https://www.xiaohongshu.com/user/profile 的 URL 里复制

# 如果暂时不想测收藏夹接口，可以直接填几个你自己的笔记 ID 快速验证详情接口
# 笔记 ID 从 URL 复制：https://www.xiaohongshu.com/explore/<note_id>
# 注意：部分笔记设置了"仅 App 可见"，填自己发布的公开笔记 ID 效果最稳定
QUICK_TEST_NOTE_IDS: list[str] = [
    # "6505318c000000001f03c5a6",  # 已失效（App 专属或已删除）
    # 填你自己的笔记 ID，例如：
    # "68xxxxxxxxxxxx",
]

# ══════════════════════════════════════════════════════════════════════


def sep(title=""):
    print("\n" + "─" * 60)
    if title:
        print(f"  {title}")
        print("─" * 60)


def main():
    # ── 0. 加载 Cookie ─────────────────────────────────────────────
    try:
        cookies = load_cookies(COOKIES_FILE)
        print(f"[OK] Cookie 已加载，共 {len(cookies)} 个字段")
        for k in ("a1", "web_session", "webId"):
            if k not in cookies:
                print(f"     {k}: ✗ 缺失！")
            else:
                print(f"     {k}: ✓  (len={len(cookies[k])})")

        # 用一个公开笔记页验证 cookie 是否能过认证（不依赖 edith 域）
        print("\n正在验证 Cookie 有效性（www 域名）...")
        import requests as _req
        _s = _req.Session()
        _s.headers.update({
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/135.0.0.0 Safari/537.36",
            "referer": "https://www.xiaohongshu.com/",
        })
        _s.cookies.update(cookies)
        _r = _s.get("https://www.xiaohongshu.com", timeout=10, allow_redirects=True)
        if "login" in _r.url:
            print("[ERROR] Cookie 已失效，被跳转到登录页。")
            print("        请在 Chrome 重新登录小红书，再运行 python -m crawler.get_my_cookies")
            sys.exit(1)
        else:
            print(f"[OK] Cookie 有效（www 域名），最终 URL: {_r.url[:60]}")
    except FileNotFoundError:
        print(f"[ERROR] 找不到 {COOKIES_FILE}")
        print("        请先运行：python -m crawler.get_my_cookies")
        sys.exit(1)

    # ── 1. 确定 note 列表（含 xsec_token）────────────────────────────
    # fetch_collect_list 现在返回 list[dict]，每条含 note_id / xsec_token / title 等
    note_metas: list[dict] = []

    if QUICK_TEST_NOTE_IDS:
        # 快速模式：手动填写的 ID，没有 xsec_token（可能被 XHS 拦截，建议用收藏夹模式）
        note_metas = [{"note_id": nid, "xsec_token": ""} for nid in QUICK_TEST_NOTE_IDS]
    else:
        if not MY_USER_ID:
            print("\n[ERROR] 请填写 MY_USER_ID 或 QUICK_TEST_NOTE_IDS")
            print("        运行 python -m crawler.get_my_cookies 后会自动打印 user_id")
            sys.exit(1)

        sep("Step 1 · 获取收藏夹列表")
        print("[INFO] 将打开浏览器窗口，若未登录请手动登录小红书后等待自动继续")
        note_metas = fetch_collect_list(MY_USER_ID, cookies)
        print(f"[OK] 共获取到 {len(note_metas)} 条收藏")
        if not note_metas:
            print("[WARN] 收藏夹为空。请先去小红书收藏几篇帖子，再重新运行本脚本。")
            sys.exit(0)
        for m in note_metas:
            print(f"     {m['note_id']}  {m['title'][:30]!r}  token={'✓' if m['xsec_token'] else '✗'}")

    # ── 2. 打印第一条的原始 __INITIAL_STATE__（字段确认）─────────────
    first_id = note_metas[0]["note_id"]
    sep(f"Step 2 · 原始 __INITIAL_STATE__（{first_id}）")
    print(f"[INFO] 运行后检查 {DEBUG_STATE_FILE}，确认字段路径无误后可注释此步骤")
    try:
        dump_initial_state(first_id, cookies, save_path=DEBUG_STATE_FILE)
        print(f"[OK] 已保存到 {DEBUG_STATE_FILE}")
    except Exception as e:
        print(f"[WARN] 获取原始结构失败：{e}")

    # ── 3. 爬取前 3 条详情 ──────────────────────────────────────────
    sep("Step 3 · 爬取前 3 条笔记详情")
    results = []
    for meta in note_metas[:3]:
        nid   = meta["note_id"]
        token = meta.get("xsec_token", "")
        print(f"  → {nid} (token={'✓' if token else '✗'}) ...", end=" ", flush=True)
        try:
            data = fetch_note_detail(nid, cookies, xsec_token=token)
            if data:
                results.append(data)
                print(f"OK  [{data['note_type']}] {data['title'][:25]!r}")
            else:
                print("SKIP（返回 None）")
        except Exception as e:
            print(f"ERROR  {e}")

    # ── 4. Schema 校验 ─────────────────────────────────────────────
    sep("Step 4 · Schema 校验")
    required = {
        "note_id", "title", "content", "tags",
        "note_url", "cover_url", "image_urls",
        "likes", "note_type", "crawled_at",
    }
    all_pass = True
    for i, r in enumerate(results, 1):
        missing = required - set(r.keys())
        if missing:
            print(f"  [FAIL] 第 {i} 条缺字段：{missing}")
            all_pass = False
        else:
            print(f"  [PASS] 第 {i} 条  note_id={r['note_id']}  type={r['note_type']}")

    if not results:
        print("\n[WARN] 没有成功爬取任何数据，请检查 Cookie 是否有效")
        return

    if not all_pass:
        print("\n[FAIL] Schema 校验未通过，请检查爬虫字段解析")
        return

    print("\n[ALL PASS] Schema 验证通过")

    # ── 5. 写入存储层 ──────────────────────────────────────────────
    sep("Step 5 · 写入 SQLite + ChromaDB")
    with NoteStore() as store:
        for r in results:
            is_new = store.save(r, user_id=MY_USER_ID)
            tag = "新增" if is_new else "更新"
            print(f"  [{tag}] {r['note_id']}  {r['title'][:30]!r}")

        stats = store.stats()
        print(f"\n  SQLite 总计：{stats['sqlite_total']} 条")
        print(f"  ChromaDB 已向量化：{stats['chroma_indexed']} 条")

    # ── 6. 验证检索 ────────────────────────────────────────────────
    sep("Step 6 · 语义检索验证")
    with NoteStore() as store:
        query = "MySQL 索引优化"
        print(f"  查询：{query!r}")
        hits = store.search(query, user_id=MY_USER_ID, n_results=3)
        if hits:
            for h in hits:
                print(f"  → [{h['distance']:.3f}] {h['note_id']}  {h['title'][:30]!r}")
        else:
            print("  无结果（ChromaDB 可能还在初始化嵌入模型）")


if __name__ == "__main__":
    main()
