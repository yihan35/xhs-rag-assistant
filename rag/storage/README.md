# 存储模块 · rag/storage/

**负责人：李奕涵**

负责两部分数据的持久化：笔记元数据 + 向量数据（SQLite + ChromaDB），以及会话状态（SQLite）。

---

## 目录结构

```
rag/storage/
├── __init__.py          # NoteStore 门面（unified interface）
├── sqlite_store.py      # SQLite：笔记元数据持久化
├── chroma_store.py      # ChromaDB：向量存储与语义检索
├── session_store.py     # SQLite 会话持久化（chat_sessions 表）
└── README.md            # 本文件
```

---

## 公开接口

### 1. 笔记存储 — `rag/storage/__init__.py`（NoteStore）

统一入口，屏蔽 SQLite + ChromaDB 细节。

```python
from rag.storage import NoteStore

# 上下文管理器
with NoteStore(db_path="data/notes.db") as store:
    # 保存笔记（含向量）
    is_new = store.save(note_dict, user_id="...")
    
    # 语义检索（向量 + 元数据补充）
    hits = store.search(query="MySQL 索引", user_id="...", n_results=6)
    
    # 获取用户所有笔记元数据
    notes = store.notes(user_id="...")
    
    # 统计信息
    stats = store.stats()  # {"sqlite_total": 120, "chroma_indexed": 115}
```

**主要方法：**

| 方法 | 返回值 | 说明 |
|------|--------|------|
| `save(note_dict, user_id)` | `bool` | True=新笔记，False=更新 |
| `search(query, user_id, n_results=6)` | `list[dict]` | 语义检索，返回 note_id, title, content, distance 等 |
| `notes(user_id)` | `list[dict]` | 该用户所有笔记的元数据列表 |
| `stats()` | `dict` | `{"sqlite_total": int, "chroma_indexed": int}` |

---

### 2. 会话存储 — `rag/storage/session_store.py`（SessionStore）

会话状态持久化，存储检索文档、LLM 对话历史、最近一次查询。

```python
from rag.storage.session_store import SessionStore

store = SessionStore(db_path="data/notes.db")

# 读取会话状态
state = store.get(session_id)
# 返回格式：
# {
#     "docs": [{"note_id", "title", "content", "distance", ...}, ...] | None,
#     "messages": [{"role": "user" | "assistant", "content": str}, ...],
#     "last_query": "上一个问题" | None
# }

# 保存会话状态
store.save(session_id, user_id, state)

# 删除会话
store.delete(session_id)
```

**方法详解：**

#### `get(session_id) -> dict | None`

读取会话状态。不存在返回 None。

**返回值示例：**
```python
{
    "docs": [
        {
            "note_id": "69ef1b91...",
            "title": "MySQL 索引那些事",
            "content": "...",
            "distance": 0.32,
            "note_url": "https://...",
            "cover_url": "https://..."
        },
        # ...
    ],
    "messages": [
        {
            "role": "user",
            "content": "# 参考资料...\n\n# 问题\n\nMySQL 索引注意事项？"
        },
        {
            "role": "assistant",
            "content": "关于 MySQL 索引有以下几点需要注意..."
        },
        {
            "role": "user",
            "content": "联合索引为什么会失效？"
        }
    ],
    "last_query": "联合索引为什么会失效？"
}
```

#### `save(session_id, user_id, state) -> None`

保存或更新会话状态（自动 upsert）。

**参数：**
- `session_id` — 会话 ID（主键）
- `user_id` — 用户 ID（用于隔离和清理）
- `state` — 会话状态字典

#### `delete(session_id) -> None`

删除会话记录。

---

## 数据库结构

### SQLite (data/notes.db)

#### notes 表（原有，笔记元数据）

```sql
CREATE TABLE notes (
    note_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT,
    content TEXT,
    tags TEXT,  -- JSON array
    note_url TEXT,
    cover_url TEXT,
    image_urls TEXT,  -- JSON array
    likes INTEGER,
    note_type TEXT,
    crawled_at TEXT,
    indexed BOOLEAN,
    PRIMARY KEY (note_id, user_id)
);
```

#### chat_sessions 表（新增，会话持久化）

```sql
CREATE TABLE chat_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    docs_json     TEXT NOT NULL DEFAULT '[]',      -- retrieve 返回的文档列表
    messages_json TEXT NOT NULL DEFAULT '[]',      -- LLM 对话历史
    last_query    TEXT,                            -- 最后一次查询（追问判断用）
    created_at    REAL NOT NULL,                   -- 创建时间（Unix timestamp）
    updated_at    REAL NOT NULL                    -- 更新时间（Unix timestamp）
);

CREATE INDEX idx_chat_sessions_user
ON chat_sessions(user_id);  -- 按用户 ID 查询、清理
```

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | TEXT | UUID 或任意字符串，主键 |
| `user_id` | TEXT | 用户标识，用于多租户隔离 |
| `docs_json` | TEXT | JSON 序列化的检索文档列表，空时为 `"[]"` |
| `messages_json` | TEXT | JSON 序列化的 LLM 对话历史，格式：`[{"role": "...", "content": "..."}, ...]` |
| `last_query` | TEXT | 最后一次用户查询，用于 `is_followup()` 判断 |
| `created_at` | REAL | 会话创建时间 |
| `updated_at` | REAL | 最后修改时间 |

### ChromaDB (data/chroma_db/)

向量库，存储笔记内容的 embedding。

```
集合名：xhs_notes
  ├── documents: 笔记内容
  ├── embeddings: 2048 维向量（Zhipu embedding-3）
  ├── metadatas: {"note_id", "user_id", "title"}
  └── ids: 内部自增 ID
```

---

## 使用示例

### 完整工作流

```python
from rag.retriever import retrieve
from rag.session_handler import handle_search, handle_stream
from rag.storage.session_store import SessionStore
from rag.followup import is_followup

store = SessionStore("data/notes.db")

# 用户第一次搜索：搜索模式
class Req:
    query = "MySQL 索引优化"
    user_id = "640c4bcc..."
    session_id = "sess_xxx"
    top_k = 6

req = Req()

# 1. 搜索 + 保存 docs
result = handle_search(req, store)
print("搜索结果：", result["sources"])  # 显示笔记卡片

# 2. 用户点击分析：切换到分析模式
# session_handler 会自动：
# - 检测 docs 已锁定 + messages 为空 → 复用 docs，首次分析
# - 构建含原文的首条 user message
# - 调用 LLM 流式生成回答
sources, gen = handle_stream(req, store)

for chunk in gen:
    print(chunk, end="", flush=True)  # SSE 推送

# 3. 用户追问
req.query = "联合索引为什么会失效？"

# session_handler 会自动：
# - 读取 state（含前次 docs + messages）
# - 调用 is_followup() 判断：是同一话题 → 不重新检索
# - messages 追加新问题，调用 LLM 流式回复
sources, gen = handle_stream(req, store)
for chunk in gen:
    print(chunk, end="", flush=True)
```

### 直接操作会话

```python
state = store.get("sess_xxx")
if state:
    print(f"已有 {len(state['messages'])} 轮对话")
    print(f"当前锁定 {len(state['docs'])} 篇笔记")

# 手动清理（e.g., 用户主动清空历史)
store.delete("sess_xxx")
```

---

## 性能与清理

### 自动清理

- 暂无自动清理机制，前端应在用户明确操作时调用 `store.delete(session_id)`
- 服务端可周期性清理过期会话（建议 TTL=7 天）

### 索引优化

```sql
-- 查询用户所有会话（清理时常用）
SELECT session_id, created_at FROM chat_sessions 
WHERE user_id = '...' 
ORDER BY updated_at DESC;

-- 查看表大小
SELECT COUNT(*) FROM chat_sessions;
```

---

## 扩展

### 从 notes 迁移到 chat_sessions

如果后续需要分离笔记库和会话库，可以：

1. 创建独立数据库 `chat_sessions.db`
2. 修改 `SessionStore.__init__` 的 `db_path` 参数默认值
3. SQLite 默认支持跨库 ATTACH，可以无缝迁移

---

## 本地测试

```bash
# 测试会话读写
python -c "
from rag.storage.session_store import SessionStore

store = SessionStore('data/notes.db')

# 保存会话
state = {
    'docs': [{'note_id': '123', 'title': 'test'}],
    'messages': [{'role': 'user', 'content': 'hello'}],
    'last_query': 'hello'
}
store.save('test_sess', 'test_user', state)

# 读取
loaded = store.get('test_sess')
print('保存成功：', loaded == state)

# 删除
store.delete('test_sess')
print('删除成功')
"
```
