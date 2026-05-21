# 会话状态机设计文档

**日期**：2026-05-21  
**负责人**：李奕涵（RAG 层）  
**状态**：已确认，待实现

---

## 背景与目标

当前后端完全无状态，每次请求独立检索+生成。用户无法在同一会话内连续追问，也无法先搜索、再对搜索结果做 AI 分析。

本次重构目标：
1. 在一个会话内维护「锁定的召回文档」和「LLM 对话历史」
2. 支持「搜索后切换分析」的上下文复用
3. 支持追问（不重新检索，直接延续 LLM 对话）
4. 会话状态持久化到 SQLite，服务重启后可恢复

---

## 状态机

```
会话开始
    │
    ├─ 搜索模式 ──► 向量检索 ──► 返回帖子列表
    │                               │
    │               session.docs 锁定，session.messages = []
    │                               │
    │                               └─ 用户切换分析模式 ──► 复用 docs，首次分析
    │
    └─ 分析模式 ──► 向量检索 + LLM 分析
                                    │
                            【锁定上下文】
                            session.docs 固定
                            session.messages 开始积累
                                    │
                            用户追问
                                    │
                   ┌────────────────┴────────────────┐
                   │                                 │
              is_followup=True                 is_followup=False
                   │                                 │
              不检索                           重新检索
              延续 messages 历史               清空 messages
                                               刷新 session.docs
```

---

## 数据层

### SQLite 新表（复用 `data/notes.db`）

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    docs_json     TEXT NOT NULL DEFAULT '[]',
    messages_json TEXT NOT NULL DEFAULT '[]',
    last_query    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id);
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | TEXT | 前端 session UUID，主键 |
| `user_id` | TEXT | 小红书用户 ID，隔离数据 |
| `docs_json` | TEXT | JSON 序列化的 `list[dict]`，`retrieve()` 返回值，含 content |
| `messages_json` | TEXT | JSON 序列化的 LLM 对话历史，首条含完整笔记原文 |
| `last_query` | TEXT | 上一轮用户问题，供 `is_followup` 使用 |
| `created_at` / `updated_at` | REAL | Unix 时间戳 |

### 新文件：`rag/storage/session_store.py`

```python
class SessionStore:
    def __init__(self, db_path: str): ...
    def get(self, session_id: str) -> dict | None: ...
    def save(self, session_id: str, user_id: str, state: dict) -> None: ...
    def delete(self, session_id: str) -> None: ...
```

`state` 结构：
```python
{
    "docs":       list[dict] | None,  # retrieve() 原始返回，含 content
    "messages":   list[dict],         # LLM messages（role/content）
    "last_query": str | None
}
```

`SessionStore` 与 `SQLiteStore` 隔离：共享同一个 db 文件，但不互相 import，各自管理自己的表。

---

## 状态机逻辑

### 新文件：`rag/session_handler.py`

对外唯一入口：

```python
def handle_query(req: QueryRequest, session_store: SessionStore) -> dict | Generator
```

判断优先级（顺序固定）：

```python
state = session_store.get(req.session_id) or new_state()

# 情况 1：追问
# 前置条件：docs 已锁定 + messages 非空（保证有过 LLM 对话） + LLM 判为追问
if state["docs"] and state["messages"] and is_followup(req.query, state):
    state["messages"].append({"role": "user", "content": req.query})
    session_store.save(req.session_id, req.user_id, state)
    return stream_with_history(state["messages"], state, session_store, req)

# 情况 2：搜索模式
if req.mode == "search":
    docs = retrieve(req.query, req.user_id, top_k=req.top_k)
    state.update({"docs": docs, "messages": [], "last_query": req.query})
    session_store.save(req.session_id, req.user_id, state)
    return {"mode": "search", "sources": format_sources(docs)}

# 情况 3：分析模式
# 3a：搜索后切换来的，复用 docs；3b：直接分析，重新检索
if not state["docs"]:
    docs = retrieve(req.query, req.user_id, top_k=req.top_k)
    state["docs"] = docs
else:
    docs = state["docs"]

state.update({"messages": [], "last_query": req.query})
session_store.save(req.session_id, req.user_id, state)
return stream_first_analysis(req.query, docs, state, session_store, req)
```

**关键修正**：情况 1 额外检查 `state["messages"]` 非空。若用户只做过搜索（docs 有值，messages 为空），切换到分析模式时应走情况 3a，而非被误判为追问。

---

## `is_followup` 分类器

### 新文件：`rag/followup.py`

- 模型：`glm-4.6`，非 thinking 模式，`temperature=0.0`，`max_tokens=5`
- 输入：`last_query`（上轮问题） + `query`（当前问题）
- 输出：`bool`，调用失败时 fallback `False`（降级为重新检索，安全）

```
System: 判断用户的新问题是否是对上一个问题的追问或延伸（同一话题），
        还是一个全新的话题。只回答 yes 或 no，不要解释。

User:   上一个问题：{last_query}
        新问题：{query}
```

---

## LLM 多轮对话

### `rag/chat.py` 新增函数

现有 `analyze` / `analyze_stream` 保持不变。新增：

**`analyze_stream_first(query, docs, system_prompt)`**

首次分析路径。将笔记原文拼入第一条 user message：
```
问题：{query}

相关笔记原文：
【笔记 1：标题】
内容...
---
【笔记 2：标题】
内容...
```
流式 yield 文本块，返回完整回答供外层存入 `messages`。

**`analyze_stream_with_history(messages, system_prompt)`**

追问路径。`messages` 已含完整历史（包括首条大原文），直接加上新 user message 发给 LLM。不重复传原文，上下文不膨胀。

两个函数都只负责 yield，`session_handler` 负责 append + save。

---

## API 变更

### `main.py` — `QueryRequest`

```python
class QueryRequest(BaseModel):
    query:      str
    user_id:    str
    mode:       Literal["search", "analysis"] = "search"
    top_k:      int = Field(default=6, ge=1, le=20)
    session_id: str = Field(..., description="前端会话 UUID")
```

`/api/query`（非流式，搜索模式）和 `/api/stream`（SSE，分析模式）都改为调用 `session_handler.handle_query(req, session_store)`，自身不再持有检索/生成逻辑。

`SessionStore` 以进程级单例初始化（与 `_sync_state` 同级），避免重复开连接。

---

## 前端变更

变更范围极小，UI 逻辑不动。

### `frontend/src/hooks/useApi.js`

`queryApi` 和 `queryStreamApi` 请求体各加一个字段：
```js
session_id: sessionId   // 传入参数，即前端 session.id
```

### `frontend/src/components/ChatArea.jsx`

`sendMessage` 调用 API 时透传 `session.id`：
```js
queryApi({ query, userId, mode, sessionId: session.id })
queryStreamApi({ query, userId, sessionId: session.id }, callbacks, signal)
```

`useSessions.js`、session UI 消息结构、`MessageBubble`、`Sidebar` 均**不需要改动**。

---

## 各目录 README 更新

实现完成后，需在以下 README 中补充新增/修改的内容说明：

- `rag/README.md`（如有）：新增 `followup.py`、`session_handler.py`
- `rag/storage/README.md`（如有）：新增 `session_store.py` 及 `chat_sessions` 表
- `rag/chat.py` 模块顶部 docstring：补充两个新函数

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `rag/followup.py` | is_followup 分类器 |
| 新增 | `rag/session_handler.py` | 状态机核心逻辑 |
| 新增 | `rag/storage/session_store.py` | SQLite 会话持久化 |
| 修改 | `rag/chat.py` | 新增两个多轮对话函数 |
| 修改 | `main.py` | QueryRequest 加 session_id，端点调 session_handler |
| 修改 | `frontend/src/hooks/useApi.js` | 请求体加 session_id |
| 修改 | `frontend/src/components/ChatArea.jsx` | 透传 session.id |

---

## 不在本次范围内

- Session 过期/清理策略（本地使用，30条上限由前端控制）
- 跨设备同步
- Session 列表接口（前端已有 localStorage 恢复机制）
