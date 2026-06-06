"""
main.py
=======
FastAPI 后端入口。
负责人：莫仕玉（接口实现）/ 李奕涵（RAG 层对接）

运行：
    uvicorn main:app --reload --port 8000

API 端点：
    GET  /                  健康检查
    GET  /api/stats         存储统计（SQLite + ChromaDB 条目数）
    POST /api/query         search 模式：语义检索，存 docs 至 session
    POST /api/stream        analysis 模式：流式 SSE，支持追问
    GET  /api/notes         用户笔记列表（分页）
    GET  /api/updates       内容有变化的笔记列表
    POST /api/sync          触发收藏夹同步（异步，后台执行）
    GET  /api/sync/status   查询同步任务状态

依赖：
    pip install fastapi uvicorn[standard]
"""

import json
import logging
import os
import random
import subprocess
import sys
import threading
from datetime import datetime, timezone

# 确保项目根目录在 import 路径中
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal

from crawler import XHSCrawler, detect_user_id, load_or_extract_cookies
from rag.storage import NoteStore, metadata_store
from rag.storage.session_store import SessionStore
from rag import session_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="拾光智行 · 小红书收藏 RAG 助手",
    version="0.1.0",
    description="将小红书收藏夹变成可对话的私人知识库",
)

# CORS：允许本地前端开发服务器调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Create React App
        "http://localhost:5173",   # Vite
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic 模型 ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query:      str                            = Field(..., min_length=1, description="用户问题")
    user_id:    str                            = Field(..., min_length=1, description="小红书用户 ID")
    mode:       Literal["search", "analysis"] = Field(default="search",  description="search=仅返回相关帖子；analysis=LLM 总结回答")
    top_k:      int                            = Field(default=6, ge=1, le=20, description="最多返回条数")
    session_id: str                            = Field(..., min_length=1, description="前端会话 UUID，用于关联后端状态")


class SourceItem(BaseModel):
    note_id:   str
    title:     str
    note_url:  str
    cover_url: str
    distance:  float


class QueryResponse(BaseModel):
    mode:    str
    answer:  str | None
    sources: list[SourceItem]
    total:   int


class CategoryUpdateRequest(BaseModel):
    user_id:  str = Field(..., min_length=1, description="小红书用户 ID")
    category: str = Field(..., description="新分类名")


class UpdateSeenRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="小红书用户 ID")
    note_id: str | None = Field(default=None, description="留空则标记全部更新为已读")


class UpdateCheckRequest(BaseModel):
    user_id: str = Field(default="", description="小红书用户 ID，留空则自动检测")


_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_COVERS_DIR = os.path.join(_PROJECT_ROOT, "data", "covers")
os.makedirs(_COVERS_DIR, exist_ok=True)
app.mount("/covers", StaticFiles(directory=_COVERS_DIR), name="covers")

# ── Session 存储（进程级单例，复用 notes.db） ─────────────────────
_session_store = SessionStore(os.path.join(_PROJECT_ROOT, "data", "notes.db"))


# ── 路由 ───────────────────────────────────────────────────────────

@app.get("/", summary="健康检查")
def health_check():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/api/stats", summary="存储统计")
def get_stats():
    """返回 SQLite 总条目数和 ChromaDB 已向量化条目数。"""
    with NoteStore() as store:
        return store.stats()


@app.post("/api/query", response_model=QueryResponse, summary="搜索模式：检索并存 docs")
def query(req: QueryRequest):
    """
    search 模式：语义检索，将 docs 存入 session，直接返回帖子列表。
    analysis 模式请使用 POST /api/stream（SSE 流式）。
    """
    if req.mode != "search":
        raise HTTPException(status_code=400, detail="此接口仅支持 search 模式，analysis 请用 /api/stream")
    try:
        result = session_handler.handle_search(req, _session_store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"handle_search 失败：{e}")
        raise HTTPException(status_code=500, detail=f"检索失败：{e}")

    sources = [
        SourceItem(
            note_id=s["note_id"],
            title=s.get("title", ""),
            note_url=s.get("note_url", ""),
            cover_url=s.get("cover_url", ""),
            distance=s.get("distance", 0.0),
        )
        for s in result["sources"]
    ]
    return QueryResponse(mode="search", answer=None, sources=sources, total=len(sources))


@app.post("/api/stream", summary="analysis 模式流式输出（SSE）")
def query_stream(req: QueryRequest):
    """
    analysis 模式 SSE 流式版本。支持三条路径：
      - 首次分析（直接分析）
      - 搜索后切换分析（复用已有 docs）
      - 追问（不重新检索，延续 LLM 对话历史）

    事件格式（text/event-stream）：
        data: {"type": "sources", "sources": [...], "total": N}\\n\\n
        data: {"type": "chunk",   "content": "..."}\\n\\n
        data: {"type": "done"}\\n\\n
        data: {"type": "error",   "message": "..."}\\n\\n
    """
    if req.mode != "analysis":
        raise HTTPException(status_code=400, detail="此接口仅支持 analysis 模式，search 请用 /api/query")
    try:
        sources_payload, chunk_gen = session_handler.handle_stream(req, _session_store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"handle_stream 失败：{e}")
        raise HTTPException(status_code=500, detail=f"处理失败：{e}")

    def event_stream():
        # 先推送 sources，前端可立即渲染引用卡片
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources_payload, 'total': len(sources_payload)}, ensure_ascii=False)}\n\n"

        # 流式推送 LLM 生成内容
        try:
            for chunk in chunk_gen:
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
        except EnvironmentError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return
        except Exception as e:
            logger.error(f"streaming 失败：{e}")
            yield f"data: {json.dumps({'type': 'error', 'message': f'生成失败：{e}'}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/updates", summary="内容有变化的笔记列表")
def list_updates(
    user_id: str = Query(..., min_length=1, description="小红书用户 ID"),
):
    """
    返回内容自上次同步以来发生过变化的笔记（content_changed_at 非空）。
    前端用此接口展示「内容已更新」提醒徽章。
    """
    with metadata_store() as store:
        updated = store.updated_notes(user_id=user_id)
    # 过滤掉大字段，只返回展示需要的字段
    result = [
        {k: v for k, v in note.items() if k not in {"content", "content_parts"}}
        for note in updated
    ]
    return {"total": len(result), "notes": result}


@app.get("/api/categories", summary="用户分类列表（去重计数）")
def list_categories(
    user_id: str = Query(..., min_length=1, description="小红书用户 ID"),
):
    """返回当前用户的分类列表，按笔记数量降序排列。"""
    with metadata_store() as store:
        categories = store.sqlite.get_categories(user_id=user_id)
    return {"categories": categories}


@app.post("/api/updates/seen", summary="标记收藏帖子更新提醒为已读")
def mark_updates_seen(req: UpdateSeenRequest):
    """
    将一个或全部收藏帖子更新提醒标记为已读。
    前端可在用户点击更新提醒或进入帖子详情后调用此接口。
    """
    with metadata_store() as store:
        updated = store.mark_updates_seen(user_id=req.user_id, note_id=req.note_id)
    return {"updated": updated}


_check_lock = threading.Lock()
_check_state: dict = {
    "running": False,
    "error": None,
    "last_check": None,
    "last_user_id": "",
    "found": 0,
    "checked": 0,
    "updated": 0,
    "created": 0,
    "skipped": 0,
}


def _resolve_check_user_id(explicit_user_id: str, cookies: dict) -> str:
    user_id = (explicit_user_id or "").strip()
    if user_id:
        return user_id
    return (detect_user_id(cookies) or "").strip()


def _run_update_check(user_id: str) -> None:
    created = updated = skipped = checked = found = 0
    try:
        cookies_path = os.path.join(_PROJECT_ROOT, "data", "cookies.json")
        cookies = load_or_extract_cookies(cookies_path)
        resolved_user_id = _resolve_check_user_id(user_id, cookies)
        if not resolved_user_id:
            raise RuntimeError("无法确定 user_id，请先设置用户 ID 或登录小红书")

        with XHSCrawler(cookies) as crawler:
            note_metas = crawler.fetch_collect_list(resolved_user_id)
            found = len(note_metas)
            with metadata_store() as store:
                current_note_ids = {meta["note_id"] for meta in note_metas if meta.get("note_id")}
                store.archive_missing(resolved_user_id, current_note_ids)

                for meta in note_metas:
                    note_id = meta.get("note_id", "")
                    if not note_id:
                        skipped += 1
                        continue
                    try:
                        note = crawler.fetch_note_text_snapshot(
                            note_id,
                            xsec_token=meta.get("xsec_token", ""),
                        )
                    except Exception as exc:
                        logger.warning(f"[{note_id}] 快检失败：{exc}")
                        skipped += 1
                        continue
                    if note is None:
                        skipped += 1
                        continue
                    result = store.save_lightweight_text(note, user_id=resolved_user_id)
                    checked += 1
                    if result == "new":
                        created += 1
                    elif result == "updated":
                        updated += 1

        _check_state.update({
            "error": None,
            "last_check": datetime.now(timezone.utc).isoformat(),
            "last_user_id": resolved_user_id,
            "found": found,
            "checked": checked,
            "updated": updated,
            "created": created,
            "skipped": skipped,
        })
    except Exception as exc:
        _check_state["error"] = str(exc)
        logger.error(f"收藏更新快检失败：{exc}")
    finally:
        _check_state["running"] = False


@app.post("/api/updates/check", summary="后台快速检测收藏帖子文字更新")
def start_update_check(req: UpdateCheckRequest = UpdateCheckRequest()):
    with _sync_lock:
        with _check_lock:
            if _sync_state["running"]:
                raise HTTPException(status_code=409, detail="完整同步正在运行中，请稍后再快检")
            if _check_state["running"]:
                raise HTTPException(status_code=409, detail="更新快检正在运行中，请稍后")
            _check_state.update({
                "running": True,
                "error": None,
                "found": 0,
                "checked": 0,
                "updated": 0,
                "created": 0,
                "skipped": 0,
            })
    threading.Thread(target=_run_update_check, args=(req.user_id,), daemon=True).start()
    return {"status": "started"}


@app.get("/api/updates/check/status", summary="查询收藏帖子更新快检状态")
def get_update_check_status():
    return dict(_check_state)


@app.get("/api/notes", summary="用户笔记列表")
def list_notes(
    user_id:   str = Query(..., min_length=1, description="小红书用户 ID"),
    page:      int = Query(default=1,  ge=1,  description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，最大 100"),
    category:   str = Query(default="",  description="按分类筛选，空字符串表示全部"),
):
    """
    返回用户的笔记列表（从 SQLite 查询，按爬取时间倒序）。
    支持分页，可用于前端收藏夹展示。
    """
    with metadata_store() as store:
        all_notes = store.notes(user_id=user_id, category=category)
    all_notes = [
        {k: v for k, v in note.items() if k not in {"content", "content_parts"}}
        for note in all_notes
    ]

    total = len(all_notes)
    start = (page - 1) * page_size
    end   = start + page_size

    return {
        "total":     total,
        "page":      page,
        "page_size": page_size,
        "notes":     all_notes[start:end],
    }


@app.put("/api/notes/{note_id}/category", summary="修正笔记分类")
def update_note_category(note_id: str, req: CategoryUpdateRequest):
    """用户手动修改某条笔记的分类。"""
    with NoteStore() as store:
        existing = store.sqlite.conn.execute(
            "SELECT 1 FROM notes WHERE note_id = ? AND user_id = ?",
            (note_id, req.user_id),
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="笔记不存在")
        store.sqlite.set_category(note_id, req.user_id, req.category)
    return {"status": "ok", "note_id": note_id, "category": req.category}


# ── 同步状态（进程级单例） ──────────────────────────────────────────

_sync_lock  = threading.Lock()
_sync_state: dict = {
    "running":   False,
    "error":     None,   # 最近一次失败的错误信息
    "last_sync": None,   # 最近一次成功完成的 ISO 时间
}

# ── 分类状态（进程级单例） ──────────────────────────────────────────

_classify_lock  = threading.Lock()
_classify_state: dict = {
    "running":   False,
    "error":     None,
    "last_run":  None,
}


class SuggestionsResponse(BaseModel):
    suggestions: list[str]


@app.get("/api/suggestions", response_model=SuggestionsResponse, summary="获取个性化建议问题")
def get_suggestions(
    user_id: str = Query(..., min_length=1, description="小红书用户 ID"),
):
    """根据用户收藏内容动态生成引导问题。收藏为空时返回默认问题。"""
    DEFAULT = [
        "面试经验有哪些总结？",
        "有没有旅行攻略推荐？",
        "求职简历怎么写？",
        "好用的生产力工具？",
    ]

    # 查用户收藏
    with metadata_store() as store:
        total = store.sqlite.count(user_id=user_id)
        if total == 0:
            return {"suggestions": DEFAULT}
        categories = store.sqlite.get_categories(user_id=user_id)
        notes = store.sqlite.all_notes(user_id=user_id)
        cat_count = {c['name']: c['count'] for c in categories}
        sample_size = max(4, min(12, len(categories) * 2))
        max_guarantee = sample_size // 2
        # 保底 + 加权采样（保底不超过一半名额，确保大类永远有机会）
        if len(notes) > sample_size:
            by_cat: dict[str, list] = {}
            for n in notes:
                by_cat.setdefault(n.get('category', '其他'), []).append(n)
            # 随机选 max_guarantee 个分类各保底 1 条
            cat_names = list(by_cat.keys())
            random.shuffle(cat_names)
            guaranteed_cats = cat_names[:max_guarantee]
            guaranteed = [random.choice(by_cat[c]) for c in guaranteed_cats]
            guaranteed_ids = {n['note_id'] for n in guaranteed}
            # 剩余名额从全量池加权采样（含所有分类）
            remaining = sample_size - len(guaranteed)
            pool = [n for n in notes if n['note_id'] not in guaranteed_ids]
            if remaining > 0:
                weights = [cat_count.get(n.get('category', ''), 1) for n in pool]
                scored = [(random.random() ** (1.0 / w), n) for n, w in zip(pool, weights)]
                scored.sort(key=lambda x: x[0], reverse=True)
                notes = guaranteed + [n for _, n in scored[:remaining]]
            else:
                notes = guaranteed
            random.shuffle(notes)
        random.shuffle(categories)

    # 拼分类和标题
    cats_str = ", ".join(f"{c['name']}({c['count']})" for c in categories[:8]) if categories else "暂无分类"
    titles_str = "\n".join(f"- {n.get('title', '无标题')[:40]}" for n in notes)

    from rag.llm_config import zhipu_client
    if zhipu_client is None:
        return {"suggestions": DEFAULT}

    hints = [
        "这次多关注用户收藏量最多的分类。",
        "这次尝试跨分类组合提问。",
        "这次从实用角度出发，问一些能立刻行动的问题。",
        "这次从好奇心角度出发，问一些能引发探索的问题。",
        "这次关注冷门或小众的分类方向。",
    ]

    try:
        resp = zhipu_client.chat.completions.create(
            model="glm-4.6",
            messages=[
                {"role": "system", "content": (
                    "你是一个对话引导助手。根据用户收藏中实际存在的笔记，"
                    "生成 4 个该用户一定能在收藏中找到相关内容的问题。"
                    "每个问题都必须有至少一篇笔记能回答。"
                    "只返回问题列表，每行一个问题，以 '- ' 开头。"
                    "问题应该覆盖不同分类，每个问题 10-20 字，中文口语风格。"
                    + random.choice(hints)
                )},
                {"role": "user", "content": (
                    f"该用户收藏了 {total} 篇笔记，请从以下笔记中提炼出可回答的问题：\n"
                    f"分类分布：{cats_str}\n"
                    f"部分笔记标题：\n{titles_str}"
                )},
            ],
            temperature=1.0,
            max_tokens=200,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = resp.choices[0].message.content or ""
        suggestions = [line.lstrip("- ").strip() for line in raw.split("\n") if line.strip().startswith("-")]
        suggestions = suggestions[:4]
        if len(suggestions) < 2:
            suggestions = DEFAULT
    except Exception:
        logger.warning("[suggestions] LLM 生成失败，使用默认问题")
        suggestions = DEFAULT

    return {"suggestions": suggestions}


class ClassifyRequest(BaseModel):
    user_id: str = Field(..., min_length=1, description="小红书用户 ID")


@app.post("/api/classify", summary="触发 AI 智能分类（异步，后台执行）")
def start_classify(req: ClassifyRequest):
    """
    对未分类的笔记执行 AI 分类，后台子进程执行，接口立即返回。
    通过 GET /api/classify/status 轮询进度。
    """
    with _classify_lock:
        if _classify_state["running"]:
            raise HTTPException(status_code=409, detail="分类任务已在运行中，请稍候")
        _classify_state["running"] = True
        _classify_state["error"]   = None

    env = os.environ.copy()
    log_path = os.path.join(_PROJECT_ROOT, "data", "sync.log")

    def _run_classify():
        try:
            with open(log_path, "a", encoding="utf-8", buffering=1) as log_file:
                log_file.write(
                    f"\n{'=' * 56}\n"
                    f"手动触发 AI 分类：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"{'=' * 56}\n"
                )
                log_file.flush()
                result = subprocess.run(
                    [sys.executable, "-m", "rag.classifier", "--user_id", req.user_id],
                    cwd=_PROJECT_ROOT,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            if result.returncode == 0:
                _classify_state["last_run"] = datetime.now(timezone.utc).isoformat()
                logger.info(f"手动分类完成，user_id={req.user_id}")
            else:
                try:
                    with open(log_path, encoding="utf-8") as f:
                        tail = f.read()[-500:].strip()
                except Exception:
                    tail = ""
                _classify_state["error"] = tail or f"退出码 {result.returncode}"
                logger.error(f"手动分类失败（code={result.returncode}）")
        except Exception as exc:
            _classify_state["error"] = str(exc)
            logger.error(f"分类子进程异常：{exc}")
        finally:
            _classify_state["running"] = False

    threading.Thread(target=_run_classify, daemon=True).start()
    return {"status": "started"}


@app.get("/api/classify/status", summary="查询分类任务状态")
def get_classify_status():
    return {
        "running":  _classify_state["running"],
        "error":    _classify_state["error"],
        "last_run": _classify_state["last_run"],
    }


class SyncRequest(BaseModel):
    user_id: str = Field(default="", description="小红书用户 ID，留空则由 ingest 自动检测")


@app.post("/api/sync", summary="触发收藏夹同步（异步，后台执行）")
def start_sync(req: SyncRequest = SyncRequest()):
    """
    启动一次收藏夹同步，等价于运行 sync_xhs.sh。
    同步在后台子进程中执行（含 Playwright 浏览器），接口立即返回。

    - 若已有同步任务正在运行，返回 409。
    - 通过 GET /api/sync/status 轮询进度。
    """
    with _sync_lock:
        if _check_state["running"]:
            raise HTTPException(status_code=409, detail="更新快检正在运行中，请稍后再同步")
        if _sync_state["running"]:
            raise HTTPException(status_code=409, detail="同步任务已在运行中，请稍候")
        _sync_state["running"] = True
        _sync_state["error"]   = None

    env = os.environ.copy()
    _sync_user_id = req.user_id or ""
    if req.user_id:
        env["XHS_USER_ID"] = req.user_id

    log_path = os.path.join(_PROJECT_ROOT, "data", "sync.log")

    def _run_ingest():
        os.makedirs(os.path.join(_PROJECT_ROOT, "data"), exist_ok=True)
        ingest_ok = False
        try:
            with open(log_path, "w", encoding="utf-8", buffering=1) as log_file:
                log_file.write(
                    f"{'=' * 56}\n"
                    f"同步开始：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"{'=' * 56}\n"
                )
                log_file.flush()

                # ── 第一步：爬取同步 ────────────────────────────
                result = subprocess.run(
                    [sys.executable, "-m", "crawler.ingest"],
                    cwd=_PROJECT_ROOT,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                ingest_ok = result.returncode == 0

                # ── 第二步：AI 分类（仅同步成功时执行） ──────
                if ingest_ok:
                    classify_user_id = _sync_user_id or env.get("XHS_USER_ID", "")
                    if classify_user_id:
                        log_file.write(
                            f"\n{'=' * 56}\n"
                            f"AI 智能分类\n"
                            f"{'=' * 56}\n"
                        )
                        log_file.flush()
                        subprocess.run(
                            [
                                sys.executable, "-m", "rag.classifier",
                                "--user_id", classify_user_id,
                            ],
                            cwd=_PROJECT_ROOT,
                            env=env,
                            stdout=log_file,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )

                # ── 第三步：导出调试页面（无论爬取是否成功均执行） ──
                log_file.write(
                    f"\n{'=' * 56}\n"
                    f"导出开发调试页面\n"
                    f"{'=' * 56}\n"
                )
                log_file.flush()
                subprocess.run(
                    [sys.executable, "tools/export_notes_debug.py"],
                    cwd=_PROJECT_ROOT,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

            if not ingest_ok:
                try:
                    with open(log_path, encoding="utf-8") as f:
                        tail = f.read()[-1000:].strip()
                except Exception:
                    tail = ""
                _sync_state["error"] = tail or f"退出码 {result.returncode}"
                logger.error(f"同步子进程失败（code={result.returncode}）")
            else:
                _sync_state["last_sync"] = datetime.now(timezone.utc).isoformat()
                logger.info(f"同步子进程完成，日志：{log_path}")
        except Exception as exc:
            _sync_state["error"] = str(exc)
            logger.error(f"同步子进程异常：{exc}")
        finally:
            _sync_state["running"] = False

    threading.Thread(target=_run_ingest, daemon=True).start()
    return {"status": "started"}


@app.get("/api/sync/status", summary="查询同步任务状态")
def get_sync_status():
    """返回当前同步任务的运行状态、最近错误信息和最后成功时间。"""
    return {
        "running":   _sync_state["running"],
        "error":     _sync_state["error"],
        "last_sync": _sync_state["last_sync"],
    }
