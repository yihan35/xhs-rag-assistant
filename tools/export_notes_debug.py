"""
Export a developer-only HTML view of SQLite notes and ChromaDB documents.

Usage:
    python tools/export_notes_debug.py
"""

from __future__ import annotations

import argparse
import json
import sys
from html import escape
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "notes.db"
DEFAULT_CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "notes_debug.html"


def load_sqlite_notes(db_path: str | Path, user_id: str = "") -> list[dict]:
    from rag.storage.sqlite_store import SQLiteStore

    store = SQLiteStore(str(db_path))
    try:
        return store.all_notes(user_id=user_id, include_archived=True)
    finally:
        store.close()


def load_chroma_documents(chroma_path: str | Path) -> dict[str, dict]:
    try:
        import chromadb  # type: ignore

        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_collection("xhs_notes")
        rows = collection.get(include=["documents", "metadatas"])
    except Exception as exc:
        return {"__error__": {"document": str(exc), "metadata": {}}}

    ids = rows.get("ids") or []
    docs = rows.get("documents") or []
    metas = rows.get("metadatas") or []
    return {
        note_id: {
            "document": docs[i] or "",
            "metadata": metas[i] or {},
        }
        for i, note_id in enumerate(ids)
    }


def build_debug_html(notes: list[dict], chroma_docs: dict[str, dict]) -> str:
    cards = "\n".join(_render_note_card(note, chroma_docs) for note in notes)
    chroma_error = chroma_docs.get("__error__", {}).get("document", "")
    error_block = (
        f'<div class="banner error">ChromaDB 读取失败：{escape(chroma_error)}</div>'
        if chroma_error else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>KnoNote Notes Debug</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f8;
      --panel: #ffffff;
      --border: #e6e8eb;
      --muted: #69717d;
      --text: #1f2933;
      --good: #0f8a5f;
      --warn: #b7791f;
      --bad: #c53030;
      --code: #f3f5f7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 18px 28px;
      background: rgba(255,255,255,.94);
      border-bottom: 1px solid var(--border);
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0; font-size: 20px; }}
    .summary {{ margin-top: 4px; color: var(--muted); font-size: 13px; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    .banner {{
      margin-bottom: 16px;
      padding: 12px 14px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--panel);
    }}
    .error {{ color: var(--bad); border-color: #fed7d7; background: #fff5f5; }}
    article {{
      margin-bottom: 18px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }}
    .card-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
    }}
    h2 {{ margin: 0; font-size: 17px; }}
    .meta {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      word-break: break-all;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid var(--border);
      white-space: nowrap;
    }}
    .ok {{ color: var(--good); background: #f0fff8; border-color: #b7ebd4; }}
    .mismatch {{ color: var(--bad); background: #fff5f5; border-color: #fed7d7; }}
    .missing {{ color: var(--warn); background: #fffaf0; border-color: #fbd38d; }}
    .stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--border);
      color: var(--muted);
      font-size: 13px;
    }}
    section {{ padding: 14px 18px; border-bottom: 1px solid var(--border); }}
    section:last-child {{ border-bottom: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 13px; color: #3b4450; }}
    pre {{
      margin: 0;
      padding: 12px;
      min-height: 36px;
      overflow: auto;
      border-radius: 6px;
      background: var(--code);
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .empty {{ color: var(--muted); }}
    a {{ color: #2563eb; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .image-part {{ margin-bottom: 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>KnoNote Notes Debug</h1>
    <div class="summary">开发调试视图：SQLite content / content_parts 与 ChromaDB document 对比，共 {len(notes)} 条笔记。</div>
  </header>
  <main>
    {error_block}
    {cards or '<div class="banner">没有读到笔记。</div>'}
  </main>
</body>
</html>
"""


def _render_note_card(note: dict, chroma_docs: dict[str, dict]) -> str:
    note_id = str(note.get("note_id", ""))
    title = str(note.get("title") or "无标题")
    content = str(note.get("content") or "")
    content_parts = _normalize_content_parts(note.get("content_parts"))
    chroma_doc = str((chroma_docs.get(note_id) or {}).get("document") or "")
    images = content_parts.get("images", [])
    ocr_count = sum(1 for item in images if (item.get("ocr_text") or "").strip())
    desc_count = sum(1 for item in images if (item.get("description") or "").strip())
    has_video = bool((content_parts.get("video_transcript") or "").strip())
    is_indexed = int(note.get("indexed") or 0) == 1
    is_collected = int(note.get("is_collected", 1) or 0) == 1
    archived_at = str(note.get("archived_at") or "")

    if chroma_doc and content:
        match_label = "SQLite content 与 ChromaDB document 一致" if content == chroma_doc else "SQLite content 与 ChromaDB document 不一致"
        match_class = "ok" if content == chroma_doc else "mismatch"
    elif chroma_doc and not content:
        match_label = "SQLite content 为空，仅 ChromaDB 有 document"
        match_class = "missing"
    elif content and not chroma_doc:
        match_label = "ChromaDB document 缺失"
        match_class = "mismatch"
    else:
        match_label = "SQLite content 与 ChromaDB document 均为空"
        match_class = "missing"

    note_url = str(note.get("note_url") or "")
    link_html = f' · <a href="{escape(note_url)}" target="_blank" rel="noreferrer">原文链接</a>' if note_url else ""

    return f"""
<article>
  <div class="card-head">
    <div>
      <h2>{escape(title)}</h2>
      <div class="meta">note_id: {escape(note_id)} · user_id: {escape(str(note.get("user_id", "")))}{link_html}</div>
    </div>
    <span class="badge {match_class}">{escape(match_label)}</span>
  </div>
  <div class="stats">
    <span>indexed：{'是' if is_indexed else '否'}</span>
    <span>收藏状态：{'当前收藏' if is_collected else '已归档'}</span>
    <span>归档时间：{escape(archived_at) if archived_at else '无'}</span>
    <span>content 字数：{len(content)}</span>
    <span>Chroma document 字数：{len(chroma_doc)}</span>
    <span>图片数：{len(note.get("image_urls") or [])}</span>
    <span>OCR 段数：{ocr_count}</span>
    <span>图片描述段数：{desc_count}</span>
    <span>视频转录：{'有' if has_video else '无'}</span>
  </div>
  <section>
    <h3>原始正文 body</h3>
    {_pre(content_parts.get("body") or "")}
  </section>
  <section>
    <h3>图片 OCR / 描述</h3>
    {_render_image_parts(images)}
  </section>
  <section>
    <h3>视频转录</h3>
    {_pre(content_parts.get("video_transcript") or "")}
  </section>
  <section>
    <h3>最终写入向量库的 content</h3>
    {_pre(content)}
  </section>
  <section>
    <h3>ChromaDB document</h3>
    {_pre(chroma_doc)}
  </section>
  <section>
    <h3>content_parts JSON</h3>
    {_pre(json.dumps(content_parts, ensure_ascii=False, indent=2))}
  </section>
</article>
"""


def _normalize_content_parts(value: Any) -> dict:
    if isinstance(value, dict):
        parts = value
    elif isinstance(value, str) and value.strip():
        try:
            parts = json.loads(value)
        except json.JSONDecodeError:
            parts = {}
    else:
        parts = {}

    return {
        "body": parts.get("body", "") if isinstance(parts, dict) else "",
        "images": parts.get("images", []) if isinstance(parts, dict) and isinstance(parts.get("images"), list) else [],
        "video_transcript": parts.get("video_transcript", "") if isinstance(parts, dict) else "",
    }


def _render_image_parts(images: list[dict]) -> str:
    if not images:
        return '<pre class="empty">无图片 OCR / 描述结果</pre>'

    chunks = []
    for i, item in enumerate(images, 1):
        url = str(item.get("url") or "")
        ocr_text = str(item.get("ocr_text") or "")
        description = str(item.get("description") or "")
        chunks.append(
            f"""<div class="image-part">
  <div class="meta">图片 {i}: {escape(url)}</div>
  <h3>OCR 文字</h3>
  {_pre(ocr_text)}
  <h3>图片描述</h3>
  {_pre(description)}
</div>"""
        )
    return "\n".join(chunks)


def _pre(value: str) -> str:
    if not value:
        return '<pre class="empty">空</pre>'
    return f"<pre>{escape(value)}</pre>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export KnoNote notes debug HTML.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite notes.db path")
    parser.add_argument("--chroma-path", default=str(DEFAULT_CHROMA_PATH), help="ChromaDB directory")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Output HTML path")
    parser.add_argument("--user-id", default="", help="Optional user_id filter")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    notes = load_sqlite_notes(args.db_path, user_id=args.user_id)
    chroma_docs = load_chroma_documents(args.chroma_path)
    html = build_debug_html(notes, chroma_docs)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")

    print(f"已导出开发调试页面：{output}")
    print(f"SQLite 笔记数：{len(notes)}")
    if "__error__" not in chroma_docs:
        print(f"ChromaDB document 数：{len(chroma_docs)}")
    else:
        print(f"ChromaDB 读取失败：{chroma_docs['__error__']['document']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
