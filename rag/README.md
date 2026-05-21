# RAG 模块 · rag/

**负责人：李奕涵**

本模块承担所有 AI 能力和向量存储：文本向量化、图片理解、视频转录、语义检索、LLM 生成回答。

---

## 目录结构

```
rag/
├── llm_config.py        # 统一 AI 客户端初始化（Zhipu + OpenAI）
├── embedder.py          # ChromaDB EmbeddingFunction 实现（Zhipu embedding-3）
├── vision.py            # 图片理解（GLM-4.6v OCR + 描述）
├── transcriber.py       # 视频转录（ffmpeg + Whisper-1）
├── chat.py              # 对话生成（GLM-5.1）+ 多轮对话接口
├── indexer.py           # 入库接口（供爬虫/ingest 调用）
├── retriever.py         # 检索接口（供后端 /api/query 调用）
├── generator.py         # 生成接口（供后端 /api/query 调用）
├── followup.py          # 追问分类器（追问识别，避免重复检索）
├── session_handler.py   # 会话状态机（三路径：搜索/首次分析/追问）
└── storage/
    ├── __init__.py          # NoteStore 门面（统一入口）
    ├── sqlite_store.py      # SQLite：元数据持久化
    ├── chroma_store.py      # ChromaDB：向量存储与语义检索
    └── session_store.py     # SQLite 会话存储（chat_sessions 表）
```

---

## 环境配置

### 必填
```
ZHIPUAI_API_KEY=your_key    # 向量化 / GLM-4.6v / GLM-5.1
```

### 选填（有视频内容时才需要）
```
OPENAI_API_KEY=your_key     # Whisper-1 视频转录
```

写入项目根目录 `.env` 文件，程序自动读取。

### 系统依赖
```bash
brew install ffmpeg   # 视频转录需要，仅有图文内容时可跳过
```

---

## 对外接口

> **后端 `main.py` 和入库脚本 `ingest.py` 只调用以下三层接口，不直接操作 rag/ 内部模块。**

---

### 1. 入库接口 — `rag/indexer.py`

#### `index_note(note, user_id) → bool`

将一条爬取到的笔记向量化后入库。

**输入：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `note` | `dict` | 笔记数据，字段见下表 |
| `user_id` | `str` | 用户 ID，**必填**，用于多租户隔离 |

`note` 必须包含的字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `note_id` | `str` | 笔记唯一 ID |
| `title` | `str` | 标题 |
| `content` | `str` | 正文 + 图片文字/描述（由爬虫拼好） |
| `note_url` | `str` | 笔记完整 URL（含 xsec_token） |
| `cover_url` | `str` | 封面图 URL |
| `tags` | `list[str]` | 标签列表 |
| `image_urls` | `list[str]` | 所有图片 URL |
| `likes` | `int` | 点赞数 |
| `note_type` | `str` | `"image"` 或 `"video"` |
| `crawled_at` | `str` | 爬取时间（ISO 格式） |

**输出：**

| 返回值 | 说明 |
|--------|------|
| `True` | 新笔记，首次入库 |
| `False` | 已存在，更新成功 |

**示例：**
```python
from rag.indexer import index_note

note = {
    "note_id": "69ef1b91...",
    "title": "字节后端一面复盘",
    "content": "面试官问了 MySQL 索引...\n[图片文字]: JVM 堆内存结构\n[图片描述]: 一张内存分布图",
    "note_url": "https://www.xiaohongshu.com/explore/69ef1b91...?xsec_token=...",
    "cover_url": "https://sns-img-hw.xhscdn.com/...",
    "tags": ["面经", "字节跳动", "后端"],
    "image_urls": ["https://..."],
    "likes": 1024,
    "note_type": "image",
    "crawled_at": "2024-01-15T10:30:00",
}
is_new = index_note(note, user_id="640c4bcc000000002a0088a8")
```

#### `index_notes(notes, user_id) → dict`

批量入库。

**输入：** `notes: list[dict]`（同上），`user_id: str`

**输出：**
```python
{"new": 3, "updated": 1, "failed": 0}
```

---

### 2. 检索接口 — `rag/retriever.py`

#### `retrieve(query, user_id, folder_id=None, top_k=6) → list[dict]`

语义检索，返回与 query 最相关的笔记列表。

**输入：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | `str` | 必填 | 用户自然语言问题 |
| `user_id` | `str` | 必填 | 严格隔离，不得省略 |
| `folder_id` | `str\|None` | `None` | 收藏夹筛选（预留，暂未实现） |
| `top_k` | `int` | `6` | 最多返回条数 |

**输出：** `list[dict]`，每条字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `note_id` | `str` | 笔记 ID |
| `title` | `str` | 标题 |
| `content` | `str` | 完整正文（向量库中存储的原文） |
| `user_id` | `str` | 所属用户 |
| `distance` | `float` | 余弦距离，0~2，**越小越相关** |
| `note_url` | `str` | 原始链接（从 SQLite 补充） |
| `cover_url` | `str` | 封面图（从 SQLite 补充） |

**过滤规则：** `distance >= 0.8` 的结果自动丢弃（可在 `retriever.py` 修改 `_DISTANCE_THRESHOLD`）。

**示例：**
```python
from rag.retriever import retrieve

docs = retrieve(
    query="MySQL 联合索引失效的场景",
    user_id="640c4bcc000000002a0088a8",
    top_k=6,
)
# docs[0] = {
#   "note_id": "69ef1b91...",
#   "title": "字节后端一面复盘",
#   "content": "...",
#   "distance": 0.31,
#   "note_url": "https://...",
#   "cover_url": "https://...",
# }
```

---

### 3. 生成接口 — `rag/generator.py`

#### `generate(query, retrieved_docs, mode) → dict`

根据检索结果和模式生成最终响应。

**输入：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | `str` | 用户原始问题 |
| `retrieved_docs` | `list[dict]` | `retrieve()` 的返回值 |
| `mode` | `str` | `"search"` 或 `"analysis"` |

**两种模式的区别：**

| 模式 | 行为 | 是否调用 LLM |
|------|------|-------------|
| `"search"` | 直接返回检索到的帖子列表 | 否 |
| `"analysis"` | 将笔记内容 + 问题送入 GLM-5.1，生成综合性回答 | 是 |

**输出：** `dict`

```python
{
    "mode": "search",   # 或 "analysis"
    "answer": None,     # search 模式固定为 None；analysis 模式为 str
    "sources": [
        {
            "note_id":   "69ef1b91...",
            "title":     "字节后端一面复盘",
            "note_url":  "https://www.xiaohongshu.com/explore/...",
            "cover_url": "https://sns-img-hw.xhscdn.com/...",
        },
        # ...
    ]
}
```

**示例：**
```python
from rag.retriever import retrieve
from rag.generator import generate

docs   = retrieve("MySQL 索引", user_id="640c4bcc...")
result = generate("MySQL 索引有哪些注意事项？", docs, mode="analysis")

print(result["answer"])   # GLM-5.1 生成的综合回答
print(result["sources"])  # 引用的笔记列表
```

---

## 对外接口（新增会话管理）

### 4. 会话管理接口 — `rag/session_handler.py`

会话状态机，封装搜索 → 首次分析 → 追问三条路径，自动判断是否需要重新检索。

#### `handle_search(req, session_store) → dict`

搜索模式：重新检索，锁定 docs，清空 LLM 历史，不调用 LLM。

**输入：**
- `req.query` — 用户查询
- `req.user_id` — 用户 ID
- `req.session_id` — 会话 ID
- `req.top_k` — 返回条数（可选，默认 6）
- `session_store` — SessionStore 实例

**输出：**
```python
{
    "mode": "search",
    "sources": [{"note_id", "title", "note_url", "cover_url", "distance"}, ...]
}
```

#### `handle_stream(req, session_store) → tuple[list[dict], Generator[str, None, None]]`

分析模式：自动判断是否为追问，返回 (sources, chunk_generator)。

- 如果是追问（同一话题）：不重新检索，复用前次 docs，直接多轮对话
- 如果是新话题：重新检索，获取新 docs，从头分析
- 如果是首次分析：检索后，生成首个分析结果

**输入：** 同 `handle_search`

**输出：**
```python
sources = [{"note_id", "title", "note_url", "cover_url", "distance"}, ...]
for chunk in generator:
    # chunk: str，LLM 流式输出，最后自动存入 session
```

**示例：**
```python
from rag.session_handler import handle_search, handle_stream
from rag.storage.session_store import SessionStore

store = SessionStore("data/notes.db")

# 搜索：获取相关笔记列表
result = handle_search(req, store)
print(result["sources"])  # 显示笔记卡片

# 分析：流式生成总结回答
sources, gen = handle_stream(req, store)
for chunk in gen:
    print(chunk, end="", flush=True)  # SSE 推送每个 chunk
```

---

## 对外接口（新增追问判断）

### 5. 追问判断 — `rag/followup.py`

使用 GLM-4.6 快速判断新问题是否为上轮问题的追问（同一话题），决定是否需要重新检索。

#### `is_followup(query, state) → bool`

**输入：**
- `query` — 当前用户问题
- `state` — session state，含 `last_query` 字段

**输出：**
- `True` — 追问，不需要重新检索
- `False` — 新话题（或判断失败），需要重新检索

失败时 fallback False（自动降级为重新检索）。

**示例：**
```python
from rag.followup import is_followup

state = {"last_query": "MySQL 索引有哪些注意事项？", ...}
is_followup("联合索引为什么会失效？", state)  # 可能返回 True（追问）
```

---

## 存储层接口（新增会话持久化）

### SessionStore — `rag/storage/session_store.py`

会话状态在 SQLite 中持久化（`notes.db` 的 `chat_sessions` 表）。

#### API

```python
from rag.storage.session_store import SessionStore

store = SessionStore("data/notes.db")

# 读取会话
state = store.get(session_id)
# 返回: {"docs": [...], "messages": [...], "last_query": "..."} 或 None

# 保存会话
store.save(session_id, user_id, state)

# 删除会话
store.delete(session_id)
```

#### 数据库结构

`notes.db` 现在包含两张表：

**notes 表（原有）**
- 存储笔记元数据：note_id, title, tags, etc.

**chat_sessions 表（新增）**
```sql
CREATE TABLE chat_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    docs_json     TEXT,        -- 检索到的文档（retrieve 返回值）
    messages_json TEXT,        -- LLM 对话历史：[{role, content}, ...]
    last_query    TEXT,        -- 上一个问题（用于追问判断）
    created_at    REAL,
    updated_at    REAL
);
```

---

## Chat 新增接口 — `rag/chat.py`

新增两个函数，用于多轮对话和会话管理。

#### `build_analysis_user_message(user_query, context_notes) → str`

构建首次分析的 user message（含完整笔记原文）。由 `session_handler` 调用，存入 `session.messages[0]`，后续追问不再重复传入原文。

```python
from rag.chat import build_analysis_user_message

msg = build_analysis_user_message(
    user_query="MySQL 索引有哪些注意事项？",
    context_notes=[{"title": "...", "content": "..."}, ...]
)
# msg = "# 参考资料（共 N 篇）\n\n..." （含完整笔记文本）
```

#### `analyze_stream_with_history(messages, system_prompt) → Generator[str]`

多轮对话路径：直接用已有 messages 历史调用 LLM，逐块 yield 内容。不重复传原文，上下文不膨胀。

```python
from rag.chat import analyze_stream_with_history
from rag.generator import SYSTEM_PROMPT

messages = [
    {"role": "user",   "content": "# 参考资料\n\n笔记 1: ...\n\n# 问题\n\n..."},
    {"role": "assistant", "content": "...（前一个回答）"},
    {"role": "user",   "content": "追问：..."},
]

for chunk in analyze_stream_with_history(messages, system_prompt=SYSTEM_PROMPT):
    print(chunk, end="", flush=True)
```

---

## Generator 变更 — `rag/generator.py`

### `SYSTEM_PROMPT`（公开）

原 `_SYSTEM_PROMPT` 重命名为 `SYSTEM_PROMPT`，供 `session_handler` 和 `chat` 模块 import。

```python
from rag.generator import SYSTEM_PROMPT
```

---

## 内部模块说明（李奕涵维护）

### AI 模型配置 — `rag/llm_config.py`

| 常量 | 值 | 用途 |
|------|----|------|
| `EMBEDDING_MODEL` | `"embedding-3"` | 2048 维向量化 |
| `CHAT_MODEL` | `"glm-5.1"` | 对话/总结生成 |
| `VISION_MODEL` | `"glm-4.6v"` | 图片 OCR + 描述 |
| `zhipu_client` | `ZhipuAI` 实例 | 所有智谱 API 调用 |
| `whisper_client` | `OpenAI` 实例或 `None` | 视频转录，无 key 时为 None |

### 图片理解 — `rag/vision.py`

```python
from rag.vision import extract_image_content

text = extract_image_content("https://image-url...")
# 返回: "[图片文字]: xxx\n[图片描述]: xxx"
# 或空字符串（图片无实质内容时）
```

### 视频转录 — `rag/transcriber.py`

```python
from rag.transcriber import transcribe_video

text = transcribe_video("https://video-url...")
# 返回: "视频语音转录文本"
# 或空字符串（无 OPENAI_API_KEY 或 ffmpeg 未安装时）
```

---

## 存储层说明 — `rag/storage/`

### 三层存储架构

```
SQLite (data/notes.db)
  ├── notes 表：笔记元数据
  │   └── note_id, user_id, title, tags, cover_url,
  │       image_urls, note_url, likes, note_type,
  │       crawled_at, indexed
  │       PRIMARY KEY (note_id, user_id)
  │
  └── chat_sessions 表：会话持久化
      └── session_id, user_id, docs_json, messages_json,
          last_query, created_at, updated_at
          PRIMARY KEY (session_id)

ChromaDB (data/chroma_db/)
  └── 向量数据：content 向量（2048 维）
              metadata: {note_id, user_id, title}
              通过 note_id 与 SQLite 关联
```

### NoteStore 接口 — `rag/storage/__init__.py`

```python
from rag.storage import NoteStore

with NoteStore() as store:
    # 保存（SQLite upsert + ChromaDB upsert）
    is_new = store.save(note_dict, user_id="...")

    # 语义检索（ChromaDB 检索 + SQLite 补充字段）
    hits = store.search("MySQL 索引", user_id="...", n_results=5)

    # 元数据列表（仅 SQLite，供前端展示）
    notes = store.notes(user_id="...")

    # 统计
    stats = store.stats()  # {"sqlite_total": 120, "chroma_indexed": 115}
```

> 切换 embedding 模型时（维度变化），必须 `rm -rf data/chroma_db` 后重建。

---

## 本地测试

```bash
# 端到端测试（爬虫 + 存储 + 检索全链路）
python -m crawler.test_crawl

# 单独测试检索
python -c "
from rag.retriever import retrieve
docs = retrieve('MySQL 索引', user_id='640c4bcc000000002a0088a8')
for d in docs:
    print(f'[{d[\"distance\"]:.3f}] {d[\"title\"]}')
"

# 单独测试生成
python -c "
from rag.retriever import retrieve
from rag.generator import generate
docs = retrieve('面试技巧', user_id='640c4bcc000000002a0088a8')
r = generate('如何准备技术面试？', docs, mode='analysis')
print(r['answer'])
"
```

---

## 待办事项

- [ ] `retriever.py`：支持 `folder_id` 过滤（等 SQLite 加 folder 字段后）
- [ ] `indexer.py`：接入 LangChain 文本分块（视频转录等长文本）
- [ ] `generator.py`：流式输出 `stream=True`，减少等待感
- [ ] `rag/storage`：每用户独立 ChromaDB Collection（当前共用 `xhs_notes`）
