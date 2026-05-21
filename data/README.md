# data/

本目录存放本地运行时数据，不是源码模块。

## 文件说明

- `notes.db`：SQLite 数据库，保存笔记元数据，以及开发调试用的 `content` / `content_parts`。
- `chroma_db/`：ChromaDB 向量库，保存笔记 `content` 的 document 和 embedding，用于语义检索。
- `notes_debug.html`：开发调试页面，由 `python tools/export_notes_debug.py` 生成，用来查看正文、OCR、图片描述、收藏状态和 ChromaDB document。
- `cookies.json`：小红书登录 Cookie，由 `python -m crawler.get_my_cookies` 或 `./sync_xhs.sh` 自动生成。
- `debug_state.json`：爬虫调试文件，保存小红书页面的原始 `window.__INITIAL_STATE__`，仅 `python -m crawler.test_crawl` 调试时生成。
- `server.pid`：后端服务进程号，由 `./start_server.sh` 生成，`./stop_server.sh` 使用。
- `server.log`：后端服务日志，由 `./start_server.sh` 生成。

## 注意

- 这些文件包含本地用户数据，不应提交到 git。
- 删除 `data/chroma_db/` 会清空向量索引，需要重新同步或重建索引。
- 删除 `data/notes.db` 会清空本地笔记元数据。
- 删除 `data/cookies.json` 后，下次同步会尝试重新从 Chrome 提取 Cookie。
- 删除 `data/server.pid` 不会停止服务，只会让停止脚本失去 PID 记录。
- 存储读写代码位于 `rag/storage/`；本目录只放数据库文件。

## 开发调试字段

- `content`：最终写入 ChromaDB、用于 embedding/RAG 的完整文本。
- `content_parts`：结构化拆分，包含 `body`、`images[].ocr_text`、`images[].description` 和 `video_transcript`。
- `is_collected`：是否仍在用户当前收藏夹中。`1` 表示当前收藏，`0` 表示用户已取消收藏后本地归档。
- `archived_at`：本地检测到取消收藏并归档的时间。归档笔记保留在 SQLite 中供开发排查，但对应 ChromaDB 向量会被删除，不参与 RAG 检索。

导出调试页面：

```bash
python tools/export_notes_debug.py
```
