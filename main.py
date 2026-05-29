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


@app.get("/api/notes", summary="用户笔记列表")
def list_notes(
    user_id:   str = Query(..., min_length=1, description="小红书用户 ID"),
    page:      int = Query(default=1,  ge=1,  description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，最大 100"),
):
    """
    返回用户的笔记列表（从 SQLite 查询，按爬取时间倒序）。
    支持分页，可用于前端收藏夹展示。
    """
    with metadata_store() as store:
        all_notes = store.notes(user_id=user_id)
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


# ── 同步状态（进程级单例） ──────────────────────────────────────────

_sync_lock  = threading.Lock()
_sync_state: dict = {
    "running":   False,
    "error":     None,   # 最近一次失败的错误信息
    "last_sync": None,   # 最近一次成功完成的 ISO 时间
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
        if _sync_state["running"]:
            raise HTTPException(status_code=409, detail="同步任务已在运行中，请稍候")
        _sync_state["running"] = True
        _sync_state["error"]   = None

    env = os.environ.copy()
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

                # ── 第二步：导出调试页面（无论爬取是否成功均执行） ──
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
