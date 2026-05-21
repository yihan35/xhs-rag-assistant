"""
小红书收藏夹爬虫
===============
适用场景：爬取「自己账号」的收藏夹，构建私有 RAG 知识库。

认证方式：Cookie（从 cookies.json 读入）。
  - a1 / web_session / webId 是必填的三个字段
  - 获取方式见 crawler/get_my_cookies.py

签名策略（收藏夹列表接口强制需要 x-s/x-t）：
  优先级 1 —— xhs 库（pip install xhs）：最简单，推荐
  优先级 2 —— Playwright：无 xhs 库时自动降级，需 pip install playwright

笔记详情：直接 GET 页面 HTML，解析 window.__INITIAL_STATE__，无需签名。
"""

import json
import re
import logging
from datetime import datetime, timezone

import requests

from .models import RawNote
from .cookies import cookies_to_str, load_cookies
from .urls import BASE_URL, build_collect_url, build_note_url

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────
COLLECT_API  = "edith.xiaohongshu.com/api/sns/web/v2/note/collect/page"
ME_API       = "/api/sns/web/v2/user/me"
PAGE_SIZE    = 30

DEFAULT_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    "referer":        "https://www.xiaohongshu.com/",
    "origin":         "https://www.xiaohongshu.com",
    "accept":         "application/json, text/plain, */*",
    "accept-language":"zh-CN,zh;q=0.9",
}


def build_session(cookies: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.cookies.update(cookies)
    return session


# ═══════════════════════════════════════════════════════════════════
# 获取当前登录用户的 user_id
# ═══════════════════════════════════════════════════════════════════

def get_my_user_id(cookies: dict) -> str:
    """
    通过 /api/sns/web/v2/user/me 获取当前 Cookie 对应的 user_id。
    这是获取自己 user_id 最可靠的方式，无需手动查找。

    返回 user_id 字符串；失败时抛 RuntimeError。
    """
    # 该接口也需要签名，优先走 xhs 库
    try:
        client = _make_xhs_client(cookies)
        # xhs 库直接提供 get_self_info 或 me 接口
        info = client.get_me()
        uid = (
            info.get("data", {}).get("user_id")
            or info.get("user_id")
            or info.get("id")
        )
        if uid:
            return str(uid)
    except Exception as e:
        logger.debug(f"xhs 库获取 me 失败，尝试备用方式：{e}")

    # 备用：直接读 Cookie 里的 user_id 字段（部分浏览器会写入）
    for key in ("user_id", "userId", "xhsTrack"):
        if key in cookies:
            return str(cookies[key])

    raise RuntimeError(
        "无法自动获取 user_id。\n"
        "请手动打开 https://www.xiaohongshu.com/user/profile，\n"
        "从 URL 中复制你的 user_id（形如 5a1234...）填入 crawler/test_crawl.py。"
    )


# ═══════════════════════════════════════════════════════════════════
# 签名 —— 构造 xhs 客户端（优先级 1：xhs 库）
# ═══════════════════════════════════════════════════════════════════

def _make_playwright_sign_fn(cookies: dict):
    """
    构造基于 Playwright 的签名函数，供 XhsClient(sign=...) 使用。

    xhs 库调用约定：sign(url, data=None, a1="", web_session="")
        → {"x-s": ..., "x-t": ..., "x-s-common": ...}

    关键：x-s-common 是每个浏览器会话固定的指纹头，不随请求变化。
    通过拦截首次真实 API 请求来捕获它，之后所有签名复用同一值。
    """
    cookie_list = [
        {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
        for k, v in cookies.items()
    ]
    _state: dict = {}

    def _init_browser():
        from playwright.sync_api import sync_playwright  # type: ignore

        pw      = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context()
        ctx.add_cookies(cookie_list)
        page    = ctx.new_page()

        # 拦截发往 edith.xiaohongshu.com 的请求，捕获 x-s-common
        def capture_common(req):
            if "edith.xiaohongshu.com" in req.url and not _state.get("x-s-common"):
                common = req.headers.get("x-s-common", "")
                if common:
                    _state["x-s-common"] = common
                    logger.debug(f"x-s-common 已捕获（{len(common)} 字节）")

        page.on("request", capture_common)

        # domcontentloaded 就够，避免 networkidle 超时
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)

        # 等待签名函数就绪
        page.wait_for_function("typeof window._webmsxyw === 'function'", timeout=15_000)

        # 等几秒让初始 API 请求触发（从而捕获 x-s-common）
        page.wait_for_timeout(4_000)

        _state.update({"pw": pw, "browser": browser, "page": page})
        logger.info(
            f"Playwright 浏览器已就绪，"
            f"x-s-common={'已捕获' if _state.get('x-s-common') else '未捕获（将尝试主动触发）'}"
        )

        # 如果还没捕获到，主动调一个轻量接口触发请求
        if not _state.get("x-s-common"):
            try:
                page.evaluate("window._webmsxyw('/api/sns/web/v2/user/me', {})")
                page.wait_for_timeout(1_000)
            except Exception:
                pass

    def sign(url: str, data=None, a1: str = "", web_session: str = "") -> dict:
        if "page" not in _state:
            _init_browser()

        page = _state["page"]
        try:
            result = page.evaluate(
                f"window._webmsxyw('{url}', {json.dumps(data or {})})"
            )
        except Exception as e:
            logger.warning(f"签名执行失败（{e}），重新加载后重试")
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_function("typeof window._webmsxyw === 'function'", timeout=15_000)
            result = page.evaluate(
                f"window._webmsxyw('{url}', {json.dumps(data or {})})"
            )

        # window._webmsxyw 返回 {"X-s": "...", "X-t": 数字}
        return {
            "x-s":        result.get("X-s") or result.get("s", ""),
            "x-t":        str(result.get("X-t") or result.get("t", "")),
            "x-s-common": _state.get("x-s-common", ""),
        }

    return sign


def _make_xhs_client(cookies: dict):
    """
    返回一个携带 Playwright 签名函数的 XhsClient 实例。
    xhs 库本身没有内置签名，必须通过 sign= 参数传入。
    """
    from xhs import XhsClient  # type: ignore
    cookie_str = cookies_to_str(cookies)
    sign_fn    = _make_playwright_sign_fn(cookies)
    return XhsClient(cookie=cookie_str, sign=sign_fn)


# ═══════════════════════════════════════════════════════════════════
# 收藏夹列表
# ═══════════════════════════════════════════════════════════════════

# 持久化 profile 目录（存浏览器登录态，不依赖 cookies.json）
import os as _os
_PROFILE_DIR = _os.path.expanduser("~/.xhs_playwright_profile")


class XHSCrawler:
    """
    Reusable Playwright crawler session.

    ingest.py uses this class so one Chromium profile/context can fetch the
    collection list and many note details without relaunching the browser for
    every note.
    """

    def __init__(
        self,
        cookies: dict,
        profile_dir: str = _PROFILE_DIR,
        headless: bool = False,
    ):
        self.cookies = cookies
        self.profile_dir = profile_dir
        self.headless = headless
        self._pw = None
        self.ctx = None
        self.page = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    def open(self):
        if self.ctx is not None:
            return self

        from playwright.sync_api import sync_playwright  # type: ignore

        _os.makedirs(self.profile_dir, exist_ok=True)
        logger.info(f"使用持久化 profile：{self.profile_dir}")

        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            args=["--window-size=1280,900"],
            viewport={"width": 1280, "height": 900},
        )
        self._inject_cookies_if_needed()
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        return self

    def close(self) -> None:
        if self.ctx is not None:
            self.ctx.close()
            self.ctx = None
            self.page = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def _inject_cookies_if_needed(self) -> None:
        try:
            existing = self.ctx.cookies()
            has_session = any(c["name"] == "web_session" for c in existing
                              if ".xiaohongshu.com" in c.get("domain", ""))
        except Exception:
            has_session = False

        if not has_session and self.cookies:
            logger.info("profile 里无 XHS session，注入 cookies...")
            self.ctx.add_cookies([
                {"name": k, "value": v, "domain": ".xiaohongshu.com", "path": "/"}
                for k, v in self.cookies.items()
            ])

    def _init_xhs_page(self) -> None:
        logger.info("初始化 XHS 页面（建立 Akamai 指纹）...")
        self.page.goto(f"{BASE_URL}/explore", wait_until="domcontentloaded", timeout=30_000)
        self.page.wait_for_timeout(3_000)
        if "login" in self.page.url.lower():
            logger.warning("未登录！请在浏览器中手动登录小红书，登录完成后按 Enter 继续...")
            input("登录完成后按 Enter：")

    def fetch_collect_list(self, user_id: str) -> list[dict]:
        note_metas: list[dict] = []
        all_page_data: list[dict] = []
        collect_url = build_collect_url(user_id)

        def on_response(resp):
            if COLLECT_API in resp.url:
                try:
                    body = resp.json()
                    if body.get("success") or body.get("code") == 0:
                        data = body.get("data") or {}
                        all_page_data.append(data)
                        count = sum(len(p.get("notes", [])) for p in all_page_data)
                        logger.info(f"拦截到收藏分页，累计 {count} 条")
                    else:
                        logger.warning(
                            f"collect API 失败：code={body.get('code')} msg={body.get('msg')}"
                        )
                except Exception as e:
                    logger.debug(f"解析 collect 响应失败：{e}")

        self.open()
        self.page.on("response", on_response)
        try:
            self._init_xhs_page()

            logger.info(f"跳转收藏页：{collect_url}")
            self.page.goto(collect_url, wait_until="domcontentloaded", timeout=30_000)
            self.page.wait_for_timeout(4_000)

            clicked = False
            try:
                el = self.page.get_by_text("收藏", exact=True).first
                el.click(timeout=5_000)
                self.page.wait_for_timeout(2_000)
                logger.info("已点击「收藏」标签（get_by_text exact）")
                clicked = True
            except Exception:
                pass

            if not clicked:
                try:
                    for tab in self.page.locator(".reds-tab-item").all():
                        if tab.inner_text().strip() == "收藏":
                            tab.click(timeout=5_000)
                            self.page.wait_for_timeout(2_000)
                            logger.info("已点击「收藏」标签（.reds-tab-item loop）")
                            clicked = True
                            break
                except Exception:
                    pass

            if not clicked:
                logger.warning("未能点击「收藏」标签，将等待页面自行加载")

            prev = -1
            no_change = 0
            for _ in range(100):
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self.page.wait_for_timeout(1_500)
                cur = sum(len(p.get("notes", [])) for p in all_page_data)
                if cur == prev:
                    no_change += 1
                    if no_change >= 5:
                        logger.info("连续无新数据，到底了")
                        break
                else:
                    no_change = 0
                    prev = cur
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass

        seen: set[str] = set()
        for pg in all_page_data:
            for note in pg.get("notes", []):
                nid = note.get("note_id") or note.get("id")
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                cover = note.get("cover") or {}
                note_metas.append({
                    "note_id":    nid,
                    "xsec_token": note.get("xsec_token", ""),
                    "title":      note.get("display_title") or note.get("title") or "",
                    "note_type":  note.get("type", "normal"),
                    "cover_url":  cover.get("url") or cover.get("urlDefault") or "",
                    "likes":      str((note.get("interact_info") or {}).get("liked_count", "0")),
                })

        logger.info(f"收藏夹共 {len(note_metas)} 条笔记")
        return note_metas

    def fetch_note_detail(
        self,
        note_id: str,
        xsec_token: str = "",
        xsec_source: str = "pc_collect",
    ) -> dict | None:
        if not xsec_token:
            logger.warning(f"[{note_id}] 未提供 xsec_token，笔记可能无法访问")

        note_url = build_note_url(note_id, xsec_token, xsec_source)
        api_note_holder: list[dict] = []

        def on_response(resp):
            for pat in _NOTE_API_PATTERNS:
                if pat in resp.url:
                    try:
                        body = resp.json()
                        logger.debug(f"[{note_id}] 拦截 {pat}: code={body.get('code')} "
                                     f"success={body.get('success')}")
                        if body.get("success") or body.get("code") == 0:
                            nd = _extract_note_from_api_body(body, note_id)
                            if nd:
                                api_note_holder.append(nd)
                                logger.info(f"[{note_id}] 从 API 获得结构化数据")
                    except Exception as exc:
                        logger.debug(f"[{note_id}] 解析 {pat} 响应失败：{exc}")
                    break

        self.open()
        self.page.on("response", on_response)
        try:
            logger.info(f"[{note_id}] 导航到 {note_url}")
            self.page.goto(note_url, wait_until="domcontentloaded", timeout=30_000)
            try:
                self.page.wait_for_selector(
                    ", ".join(_TITLE_SELECTORS + [".error-page", ".note-unavailable"]),
                    timeout=10_000,
                )
            except Exception:
                pass
            self.page.wait_for_timeout(3_000)

            if not api_note_holder:
                try:
                    body_text = self.page.inner_text("body")
                    if "暂时无法浏览" in body_text or "该内容暂不支持" in body_text:
                        logger.warning(f"[{note_id}] 笔记不可访问（App 专属或已删除）")
                        return None
                except Exception:
                    pass
            if "login" in self.page.url.lower():
                logger.warning(f"[{note_id}] 未登录，请先手动登录")
                return None

            def _try_selectors(selectors: list[str]) -> str:
                for sel in selectors:
                    try:
                        el = self.page.locator(sel).first
                        if el.count() > 0:
                            txt = el.inner_text()
                            if txt and txt.strip():
                                return txt.strip()
                    except Exception:
                        continue
                return ""

            dom_title = _try_selectors(_TITLE_SELECTORS)
            dom_desc = _try_selectors(_DESC_SELECTORS)

            dom_tags: list[str] = []
            try:
                dom_tags = self.page.evaluate("""
                    () => {
                        const spans = document.querySelectorAll('a[href*="/search_result"] span, .tag, [class*="tag"]');
                        const tags = [];
                        spans.forEach(el => {
                            const t = el.innerText.trim().replace(/^#/, '');
                            if (t && !tags.includes(t)) tags.push(t);
                        });
                        return tags;
                    }
                """)
            except Exception:
                pass

            dom_images: list[str] = []
            try:
                dom_images = self.page.evaluate("""
                    () => {
                        const imgs = document.querySelectorAll(
                            '.swiper-slide img[src], .note-image img[src], .image-view img[src]'
                        );
                        const urls = [];
                        imgs.forEach(img => {
                            const s = img.getAttribute('src') || img.getAttribute('data-src') || '';
                            if (s && s.startsWith('http') && !urls.includes(s)) urls.push(s);
                        });
                        return urls;
                    }
                """)
            except Exception:
                pass
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass

        api_data = api_note_holder[0] if api_note_holder else {}
        title = dom_title or api_data.get("title") or api_data.get("display_title") or ""
        desc = dom_desc or api_data.get("desc") or api_data.get("description") or ""
        tags = dom_tags or _parse_tags(api_data)
        ntype_raw = api_data.get("type") or api_data.get("note_type") or ""
        ntype = "video" if ntype_raw == "video" else "image"

        if api_data:
            cover_url, other_images = _parse_images(api_data)
        else:
            cover_url = dom_images[0] if dom_images else ""
            other_images = dom_images[1:] if len(dom_images) > 1 else []

        all_images = ([cover_url] if cover_url else []) + other_images
        if not title and not desc and not all_images:
            logger.warning(f"[{note_id}] 未能提取任何笔记内容，跳过")
            return None

        media_text, media_parts = _enhance_media(ntype, all_images, api_data)
        parts = [p for p in [desc, media_text] if p and p.strip()]
        content = "\n".join(parts)
        content_parts = {
            "body": desc,
            "images": media_parts.get("images", []),
            "video_transcript": media_parts.get("video_transcript", ""),
        }

        return RawNote(
            note_id=note_id,
            title=title,
            content=content,
            tags=tags,
            note_url=note_url,
            cover_url=cover_url,
            image_urls=other_images,
            likes=_parse_likes(api_data),
            note_type=ntype,
            crawled_at=datetime.now(timezone.utc).isoformat(),
            content_parts=content_parts,
            note_published_at=_parse_published_at(api_data),
        ).to_dict()


# ═══════════════════════════════════════════════════════════════════
# __INITIAL_STATE__ 解析（笔记详情专用，无需签名）
# ═══════════════════════════════════════════════════════════════════

def extract_initial_state(html: str) -> dict:
    """
    从笔记页 HTML 提取 window.__INITIAL_STATE__。

    XHS 输出的是 JS 对象字面量，含 undefined/NaN/Infinity 等非法 JSON 值。
    用正则精确替换：只替换作为 JSON 值出现的裸关键词，不碰字符串内容。
    """
    pattern = r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*(?:</script>|;)"
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        raise ValueError("找不到 __INITIAL_STATE__（Cookie 可能已过期）")

    raw = match.group(1)
    # 只替换裸值（前面是 : 或 [ 或 ,，后面是 , } ] 之类），不改字符串内容
    raw = re.sub(r'(?<=[:,\[{])\s*undefined\s*(?=[,\]}])', 'null', raw)
    raw = re.sub(r'(?<=[:,\[{])\s*NaN\s*(?=[,\]}])', 'null', raw)
    raw = re.sub(r'(?<=[:,\[{])\s*Infinity\s*(?=[,\]}])', 'null', raw)
    return json.loads(raw)


def dump_initial_state(note_id: str, cookies: dict, save_path: str = None) -> dict:
    """
    调试用：打印某笔记的完整原始 __INITIAL_STATE__，供字段确认。
    """
    url = f"{BASE_URL}/explore/{note_id}"
    resp = build_session(cookies).get(url, timeout=15)
    resp.raise_for_status()

    state = extract_initial_state(resp.text)
    pretty = json.dumps(state, ensure_ascii=False, indent=2)

    if save_path:
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(pretty)
        logger.info(f"原始结构已保存到 {save_path}")
    else:
        print(pretty)

    return state


# ═══════════════════════════════════════════════════════════════════
# 字段解析辅助函数
# ═══════════════════════════════════════════════════════════════════

def _locate_note_data(state: dict, note_id: str) -> dict:
    """
    在 __INITIAL_STATE__ 中定位笔记核心 dict。
    标准路径：state["note"]["noteDetailMap"][note_id]["note"]
    找不到时做宽松深度搜索。
    """
    try:
        return state["note"]["noteDetailMap"][note_id]["note"]
    except (KeyError, TypeError):
        pass

    def _search(obj):
        if isinstance(obj, dict):
            if obj.get("id") == note_id or obj.get("noteId") == note_id:
                return obj
            for v in obj.values():
                r = _search(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for item in obj:
                r = _search(item)
                if r:
                    return r
        return None

    return _search(state) or {}


def _parse_images(note_data: dict) -> tuple[str, list[str]]:
    images = note_data.get("imageList") or note_data.get("image_list") or []
    if not images:
        return "", []

    def best_url(img: dict) -> str:
        for key in ("urlDefault", "url"):
            v = img.get(key)
            if v:
                return v
        info = img.get("infoList") or []
        return info[-1].get("url", "") if info else ""

    urls = [u for u in (best_url(img) for img in images) if u]
    return (urls[0] if urls else ""), urls[1:]


def _parse_tags(note_data: dict) -> list[str]:
    raw = note_data.get("tagList") or note_data.get("tag_list") or []
    return [t.get("name") or t.get("text", "") for t in raw if isinstance(t, dict)]


def _parse_likes(note_data: dict) -> int:
    info = note_data.get("interactInfo") or note_data.get("interact_info") or {}
    raw  = info.get("likedCount") or info.get("liked_count") or "0"
    try:
        return int(str(raw).replace(",", "").replace("万", "0000"))
    except ValueError:
        return 0


def _parse_published_at(note_data: dict) -> str:
    """
    解析帖子发布时间，返回 ISO 8601 字符串（UTC）。
    XHS API 的 time / createTime 字段通常是毫秒级 Unix 时间戳（整数）。
    若字段缺失或解析失败，返回空字符串。
    """
    raw = (
        note_data.get("time")
        or note_data.get("createTime")
        or note_data.get("create_time")
        or note_data.get("publishTime")
        or note_data.get("publish_time")
        or 0
    )
    if not raw:
        return ""
    try:
        ts = int(raw)
        if ts > 1e12:       # 毫秒 → 秒
            ts //= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def _get_video_url(note_data: dict) -> str:
    video    = note_data.get("video") or {}
    media    = video.get("media") or {}
    consumer = video.get("consumer") or {}
    for key in ("url", "masterUrl", "h264Url"):
        v = media.get(key)
        if v:
            return v
    return consumer.get("originVideoKey", "")


# ═══════════════════════════════════════════════════════════════════
# 媒体增强：智谱 Vision（图片理解）/ OpenAI Whisper（视频转录）
# ═══════════════════════════════════════════════════════════════════

def _enhance_media(ntype: str, all_images: list[str], api_data: dict) -> tuple[str, dict]:
    """
    根据笔记类型增强 content：
      - image：对每张图调用智谱 Vision，追加 [图片文字]/[图片描述]
      - video：用 OpenAI Whisper 转录，追加 [视频转录]

    失败时单条跳过，不中断整体流程。
    返回 (追加文本, 结构化拆分)，追加文本不含正文，空时返回 ""。
    """
    try:
        if ntype == "video":
            from rag.transcriber import transcribe_video  # type: ignore
            video_url = _get_video_url(api_data)
            transcript = transcribe_video(video_url)
            if transcript:
                return f"[视频转录]: {transcript}", {
                    "images": [],
                    "video_transcript": transcript,
                }
        else:
            from rag.vision import extract_image_parts, format_image_parts  # type: ignore
            image_parts = extract_image_parts(all_images)
            return format_image_parts(image_parts), {
                "images": image_parts,
                "video_transcript": "",
            }
    except Exception as e:
        logger.warning(f"媒体增强失败，跳过：{e}")
    return "", {"images": [], "video_transcript": ""}


# ═══════════════════════════════════════════════════════════════════
# 主爬取函数
# ═══════════════════════════════════════════════════════════════════

# XHS 笔记页加载时会调用这些 edith API，拦截后可拿到结构化数据
_NOTE_API_PATTERNS = [
    "/api/sns/web/v1/feed",
    "/api/sns/web/v3/feed",
    "/api/sns/web/v1/note/detail",
    "/api/sns/h5/v1/note_info",
]

# DOM 选择器（按优先级）
_TITLE_SELECTORS = [
    "#detail-title",
    ".note-detail-title",
    ".title",
    "h1",
]
_DESC_SELECTORS = [
    "#detail-desc",
    ".note-detail-desc",
    ".note-text",
    ".desc",
    ".content",
]
_IMG_SELECTORS = [
    ".swiper-slide img[src]",
    ".note-image img[src]",
    ".image-view img[src]",
    "img.note-cover[src]",
]


def _extract_note_from_api_body(body: dict, note_id: str) -> dict:
    """从 feed/detail API 的 JSON 响应中提取 note dict。"""
    data  = body.get("data") or {}
    items = data.get("items") or []
    # /api/sns/web/v1/feed 返回 {data: {items: [{note_card: {...}}]}}
    for item in items:
        note = (item.get("note_card") or item.get("noteCard")
                or item.get("note") or {})
        nid = note.get("note_id") or note.get("id") or note.get("noteId") or ""
        if nid == note_id:
            return note
        if nid and not items:           # 只有一条就直接用
            return note
    # /api/sns/h5/v1/note_info 直接在 data 里
    if data.get("note_id") or data.get("id"):
        return data
    # 兜底：若 data 非空就返回
    return data if data else {}


# Backward-compatible function API. New ingestion code should prefer XHSCrawler
# directly so multiple note details share one browser context.
def fetch_collect_list(user_id: str, cookies: dict) -> list[dict]:
    with XHSCrawler(cookies) as crawler:
        return crawler.fetch_collect_list(user_id)


def fetch_note_detail(note_id: str, cookies: dict,
                      xsec_token: str = "", xsec_source: str = "pc_collect") -> dict | None:
    with XHSCrawler(cookies) as crawler:
        return crawler.fetch_note_detail(note_id, xsec_token, xsec_source)
