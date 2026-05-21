# Session State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一个会话内维护「锁定的召回文档 + LLM 对话历史」，支持 search→analysis 上下文复用和追问检测，状态持久化到 SQLite。

**Architecture:** 新增 `SessionStore`（SQLite 持久化）、`followup.is_followup`（GLM-4.6 快速分类）、`session_handler`（状态机主逻辑）三个组件；`chat.py` 新增两个函数；`main.py` 接收 `session_id` 并委托给 `session_handler`；前端透传 `session.id`。

**Tech Stack:** Python / SQLite（标准库 sqlite3）/ 智谱 GLM-4.6（追问分类）/ GLM-5.1（对话生成）/ React

---

## 文件变更清单

| 操作 | 路径 | 职责 |
|------|------|------|
| 新增 | `rag/storage/session_store.py` | SQLite 会话持久化（chat_sessions 表） |
| 新增 | `rag/followup.py` | 追问/新话题分类器 |
| 新增 | `rag/session_handler.py` | 状态机：search / 首次分析 / 追问 三条路径 |
| 修改 | `rag/chat.py` | 新增 `build_analysis_user_message` + `analyze_stream_with_history` |
| 修改 | `rag/generator.py` | `_SYSTEM_PROMPT` → `SYSTEM_PROMPT`（供 session_handler import） |
| 修改 | `main.py` | `QueryRequest` 加 `session_id`；初始化 `_session_store`；两个端点委托给 session_handler |
| 修改 | `frontend/src/hooks/useApi.js` | `queryApi` / `queryStreamApi` 请求体加 `session_id` |
| 修改 | `frontend/src/components/ChatArea.jsx` | `sendMessage` 透传 `session.id` |
| 新增 | `tests/test_session_store.py` | SessionStore 单元测试 |
| 新增 | `tests/test_followup.py` | is_followup 单元测试（mock LLM） |
| 新增 | `tests/test_session_handler.py` | 状态机路径测试（mock 依赖） |

---

## Task 1: `rag/storage/session_store.py` — SQLite 会话持久化

**Files:**
- Create: `rag/storage/session_store.py`
- Create: `tests/test_session_store.py`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_session_store.py`：

```python
import json
import tempfile
import unittest
from pathlib import Path
from rag.storage.session_store import SessionStore


def _store(tmp):
    return SessionStore(str(Path(tmp) / "notes.db"))


class SessionStoreTests(unittest.TestCase):

    def test_get_returns_none_for_unknown_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            self.assertIsNone(s.get("no-such-id"))

    def test_save_and_get_round_trip(self):
        state = {
            "docs":       [{"note_id": "n1", "title": "T", "content": "C", "distance": 0.1}],
            "messages":   [{"role": "user", "content": "hello"}],
            "last_query": "hello",
        }
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", state)
            got = s.get("sess-1")

        self.assertEqual(got["docs"][0]["note_id"], "n1")
        self.assertEqual(got["messages"][0]["content"], "hello")
        self.assertEqual(got["last_query"], "hello")

    def test_save_overwrites_existing_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", {"docs": None, "messages": [], "last_query": None})
            s.save("sess-1", "user-1", {"docs": [{"note_id": "n1"}], "messages": [], "last_query": "q"})
            got = s.get("sess-1")

        self.assertEqual(got["docs"][0]["note_id"], "n1")
        self.assertEqual(got["last_query"], "q")

    def test_delete_removes_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _store(tmp)
            s.save("sess-1", "user-1", {"docs": None, "messages": [], "last_query": None})
            s.delete("sess-1")
            self.assertIsNone(s.get("sess-1"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /Users/liyihan/projects/xhs-rag-assistant
python -m pytest tests/test_session_store.py -v
```

预期：`ModuleNotFoundError: No module named 'rag.storage.session_store'`

- [ ] **Step 3: 实现 `rag/storage/session_store.py`**

```python
"""
rag/storage/session_store.py
=============================
SQLite 会话持久化：在 notes.db 中维护 chat_sessions 表。

chat_sessions 表与 notes 表隔离（无外键依赖），共享同一个 db 文件。

对外接口：
    SessionStore(db_path)
        .get(session_id)  -> dict | None
        .save(session_id, user_id, state)
        .delete(session_id)

state 结构：
    {
        "docs":       list[dict] | None,   # retrieve() 返回值，含 content
        "messages":   list[dict],          # LLM 对话历史（role/content）
        "last_query": str | None
    }
"""

import json
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    docs_json     TEXT NOT NULL DEFAULT '[]',
    messages_json TEXT NOT NULL DEFAULT '[]',
    last_query    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
ON chat_sessions(user_id);
"""

_UPSERT = """
INSERT INTO chat_sessions
    (session_id, user_id, docs_json, messages_json, last_query, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    docs_json     = excluded.docs_json,
    messages_json = excluded.messages_json,
    last_query    = excluded.last_query,
    updated_at    = excluded.updated_at;
"""


class SessionStore:
    """SQLite 持久化会话状态，线程安全（每次调用独立 connect）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._init_table()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_table(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_INDEX)

    def get(self, session_id: str) -> dict | None:
        """返回 session state，不存在时返回 None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT docs_json, messages_json, last_query FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "docs":       json.loads(row[0]) or None,
            "messages":   json.loads(row[1]),
            "last_query": row[2],
        }

    def save(self, session_id: str, user_id: str, state: dict) -> None:
        """插入或覆盖 session 状态。"""
        now = time.time()
        with self._conn() as conn:
            conn.execute(_UPSERT, (
                session_id,
                user_id,
                json.dumps(state.get("docs") or [], ensure_ascii=False),
                json.dumps(state.get("messages") or [], ensure_ascii=False),
                state.get("last_query"),
                now,
                now,
            ))
        logger.debug(f"[session_store] saved {session_id[:8]}…")

    def delete(self, session_id: str) -> None:
        """删除 session 记录。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/test_session_store.py -v
```

预期：4 个测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/storage/session_store.py tests/test_session_store.py
git commit -m "feat: add SQLite-backed SessionStore for chat session persistence"
```

---

## Task 2: `rag/followup.py` — 追问分类器

**Files:**
- Create: `rag/followup.py`
- Create: `tests/test_followup.py`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_followup.py`：

```python
import unittest
from unittest.mock import MagicMock, patch


class FollowupTests(unittest.TestCase):

    def test_returns_false_when_no_last_query(self):
        from rag.followup import is_followup
        state = {"docs": [], "messages": [], "last_query": None}
        self.assertFalse(is_followup("新问题", state))

    def test_returns_true_when_api_says_yes(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "yes"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.followup.zhipu_client", mock_client):
            from rag.followup import is_followup
            state = {"last_query": "面试经验有哪些？"}
            result = is_followup("第一点能展开吗？", state)

        self.assertTrue(result)

    def test_returns_false_when_api_says_no(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "no"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.followup.zhipu_client", mock_client):
            from rag.followup import is_followup
            state = {"last_query": "面试经验有哪些？"}
            result = is_followup("有哪些旅行攻略？", state)

        self.assertFalse(result)

    def test_returns_false_on_api_failure(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("network error")

        with patch("rag.followup.zhipu_client", mock_client):
            from rag.followup import is_followup
            state = {"last_query": "面试经验"}
            result = is_followup("追问", state)

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/test_followup.py -v
```

预期：`ModuleNotFoundError: No module named 'rag.followup'`

- [ ] **Step 3: 实现 `rag/followup.py`**

```python
"""
rag/followup.py
===============
追问判断：用 GLM-4.6（非 thinking 模式）快速分类。

对外接口：
    is_followup(query: str, state: dict) -> bool

失败时 fallback False（降级为重新检索，不会出错）。
"""

import logging
from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_FOLLOWUP_MODEL = "glm-4.6"
_NO_THINKING    = {"thinking": {"type": "disabled"}}

_SYSTEM = (
    "判断用户的新问题是否是对上一个问题的追问或延伸（同一话题），"
    "还是一个全新的话题。只回答 yes 或 no，不要解释。"
)


def is_followup(query: str, state: dict) -> bool:
    """
    判断 query 是否是对 state["last_query"] 的追问。

    参数：
        query   当前用户问题
        state   session state，含 last_query 字段

    返回：
        True  = 追问，不需要重新检索
        False = 新话题（或判断失败），需要重新检索
    """
    last = state.get("last_query")
    if not last:
        return False

    if zhipu_client is None:
        return False

    try:
        resp = zhipu_client.chat.completions.create(
            model=_FOLLOWUP_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": f"上一个问题：{last}\n新问题：{query}"},
            ],
            temperature=0.0,
            max_tokens=5,
            extra_body=_NO_THINKING,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        result = answer.startswith("yes")
        logger.info(f"[followup] '{query[:30]}' → {'追问' if result else '新话题'}")
        return result
    except Exception as e:
        logger.warning(f"[followup] 判断失败，fallback False：{e}")
        return False
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/test_followup.py -v
```

预期：4 个测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/followup.py tests/test_followup.py
git commit -m "feat: add is_followup classifier using GLM-4.6"
```

---

## Task 3: `rag/chat.py` — 新增多轮对话函数

**Files:**
- Modify: `rag/chat.py`

本任务不写新测试（函数是现有 `analyze_stream` 的薄封装，逻辑已覆盖）。

- [ ] **Step 1: 在 `rag/chat.py` 末尾追加两个函数**

在文件末尾（`analyze_stream` 之后）追加：

```python
def build_analysis_user_message(
    user_query: str,
    context_notes: list[dict],
) -> str:
    """
    构建首次分析的 user message 字符串（含完整笔记原文）。
    由 session_handler 调用，存入 session.messages[0]，
    后续追问不再重复传入原文，上下文不膨胀。
    """
    notes_text = "\n\n---\n\n".join(
        f"**笔记 {i}：{n.get('title', '无标题')}**\n{n.get('content', '（内容为空）')}"
        for i, n in enumerate(context_notes, 1)
    )
    return (
        f"# 参考资料（来自你的收藏笔记，共 {len(context_notes)} 篇）\n\n"
        f"{notes_text}\n\n"
        f"---\n\n"
        f"# 我的问题\n\n{user_query}"
    )


def analyze_stream_with_history(
    messages: list[dict],
    system_prompt: str = _DEFAULT_SYSTEM,
    max_tokens: int = 4096,
    temperature: float = 0.5,
) -> Generator[str, None, None]:
    """
    多轮对话路径：直接用已有 messages 历史调 LLM，逐块 yield content。

    用于：
      - 首次分析（messages = [user(含原文)]）
      - 追问（messages = [user(含原文), assistant, user, ...]）

    不重复传原文，上下文不膨胀。
    由调用方（session_handler）负责 append + save。

    参数：
        messages  完整 LLM 对话历史，由 session_handler 维护
    """
    if zhipu_client is None:
        raise EnvironmentError("ZHIPUAI_API_KEY 未设置，无法调用 Chat API")

    if not messages:
        yield "暂无上下文，请先提问。"
        return

    stream = zhipu_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        extra_body=_NO_THINKING,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None) or ""
        if content:
            yield content
```

- [ ] **Step 2: 同时修改 `rag/generator.py`：将 `_SYSTEM_PROMPT` 改为 `SYSTEM_PROMPT`（公开）**

找到 `generator.py` 第 29 行的 `_SYSTEM_PROMPT = """…"""` 定义，将变量名改为 `SYSTEM_PROMPT`（去掉下划线前缀）。同时将第 84、109 行引用处也一并更新：

```python
# 第 29 行：
SYSTEM_PROMPT = """你是用户的私人小红书收藏助手「拾光智行」。
...（内容不变）..."""

# 第 84 行（analyze 调用处）：
answer = _analyze(
    user_query=query,
    context_notes=retrieved_docs,
    system_prompt=SYSTEM_PROMPT,   # ← 去掉下划线
)

# 第 109 行（analyze_stream 调用处）：
yield from _analyze_stream(
    user_query=query,
    context_notes=retrieved_docs,
    system_prompt=SYSTEM_PROMPT,   # ← 去掉下划线
)
```

- [ ] **Step 3: 运行现有测试，确认没有破坏**

```bash
python -m pytest tests/ -v
```

预期：所有已有测试仍然 PASS

- [ ] **Step 4: Commit**

```bash
git add rag/chat.py rag/generator.py
git commit -m "feat: add build_analysis_user_message and analyze_stream_with_history to chat.py"
```

---

## Task 4: `rag/session_handler.py` — 状态机核心

**Files:**
- Create: `rag/session_handler.py`
- Create: `tests/test_session_handler.py`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_session_handler.py`：

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from rag.storage.session_store import SessionStore


def _make_req(query, mode, session_id="sess-1", user_id="user-1", top_k=6):
    return SimpleNamespace(
        query=query, mode=mode, session_id=session_id,
        user_id=user_id, top_k=top_k,
    )


def _store(tmp):
    return SessionStore(str(Path(tmp) / "notes.db"))


FAKE_DOCS = [{"note_id": "n1", "title": "T", "content": "C",
              "note_url": "", "cover_url": "", "distance": 0.2}]


class HandleSearchTests(unittest.TestCase):

    def test_search_saves_docs_and_clears_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            # 预置一个有 messages 的旧状态
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": [{"role": "user", "content": "old"}],
                "last_query": "old query",
            })

            with patch("rag.session_handler.retrieve", return_value=FAKE_DOCS):
                from rag.session_handler import handle_search
                result = handle_search(_make_req("new query", "search"), store)

            state = store.get("sess-1")

        self.assertEqual(result["mode"], "search")
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["note_id"], "n1")
        self.assertEqual(state["messages"], [])           # messages 已清空
        self.assertEqual(state["last_query"], "new query")


class HandleStreamTests(unittest.TestCase):

    def _fake_chunks(self, text="回答"):
        yield text

    def test_retrieves_when_no_existing_docs(self):
        """首次分析：docs 为空，触发 retrieve"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)

            with patch("rag.session_handler.retrieve", return_value=FAKE_DOCS) as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("问题", "analysis"), store)
                list(gen)  # 消费 generator 触发 save

            state = store.get("sess-1")

        mock_retrieve.assert_called_once()
        self.assertEqual(sources[0]["note_id"], "n1")
        self.assertEqual(state["messages"][0]["role"], "user")
        self.assertEqual(state["messages"][1]["role"], "assistant")

    def test_reuses_docs_from_prior_search(self):
        """搜索后切换分析：复用 docs，不重新 retrieve"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": [], "last_query": "搜索词",
            })

            with patch("rag.session_handler.retrieve") as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("分析问题", "analysis"), store)
                list(gen)

        mock_retrieve.assert_not_called()
        self.assertEqual(sources[0]["note_id"], "n1")

    def test_followup_skips_retrieve_and_extends_messages(self):
        """追问：不 retrieve，直接追加消息"""
        prior_messages = [
            {"role": "user",      "content": "问题：xxx\n\n原文..."},
            {"role": "assistant", "content": "首次回答"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": prior_messages, "last_query": "首次问题",
            })

            with patch("rag.session_handler.retrieve") as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=True), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["追问回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("追问", "analysis"), store)
                list(gen)

            state = store.get("sess-1")

        mock_retrieve.assert_not_called()
        self.assertEqual(len(state["messages"]), 4)       # user, assistant, user, assistant
        self.assertEqual(state["messages"][2]["content"], "追问")
        self.assertEqual(state["messages"][3]["content"], "追问回答")

    def test_new_topic_clears_messages_and_retrieves(self):
        """新话题：is_followup=False，清空 messages，重新检索"""
        prior_messages = [
            {"role": "user",      "content": "问题：xxx\n\n原文..."},
            {"role": "assistant", "content": "旧回答"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.save("sess-1", "user-1", {
                "docs": FAKE_DOCS, "messages": prior_messages, "last_query": "旧问题",
            })

            new_docs = [{"note_id": "n2", "title": "T2", "content": "C2",
                         "note_url": "", "cover_url": "", "distance": 0.3}]
            with patch("rag.session_handler.retrieve", return_value=new_docs) as mock_retrieve, \
                 patch("rag.session_handler.is_followup", return_value=False), \
                 patch("rag.session_handler.analyze_stream_with_history", return_value=iter(["新回答"])):
                from rag.session_handler import handle_stream
                sources, gen = handle_stream(_make_req("全新话题", "analysis"), store)
                list(gen)

            state = store.get("sess-1")

        mock_retrieve.assert_called_once()
        self.assertEqual(sources[0]["note_id"], "n2")
        self.assertEqual(state["messages"][0]["role"], "user")   # 只有新的一轮
        self.assertEqual(len(state["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/test_session_handler.py -v
```

预期：`ModuleNotFoundError: No module named 'rag.session_handler'`

- [ ] **Step 3: 实现 `rag/session_handler.py`**

```python
"""
rag/session_handler.py
======================
会话状态机：封装 search / 首次分析 / 追问 三条路径。

对外接口：
    handle_search(req, session_store) -> dict
        search 模式：检索 + 存 docs，不调 LLM。
        返回 {"mode": "search", "sources": [...]}

    handle_stream(req, session_store) -> tuple[list[dict], Generator[str, None, None]]
        analysis 模式：判断追问/新话题，返回 (sources, chunk_generator)。
        chunk_generator 流式 yield LLM 输出，完成后自动存入 session。

判断优先级：
    1. docs 已锁定 + 上轮 LLM 已回复 + is_followup → 追问，不检索
    2. docs 已锁定 + messages 为空（搜索后切换）→ 复用 docs，首次分析
    3. docs 为空 → 重新检索，首次分析
"""

import logging
from typing import Generator

from .retriever import retrieve as _retrieve
from .followup import is_followup
from .chat import build_analysis_user_message, analyze_stream_with_history
from .generator import SYSTEM_PROMPT
from .storage.session_store import SessionStore

logger = logging.getLogger(__name__)


def _new_state() -> dict:
    return {"docs": None, "messages": [], "last_query": None}


def _format_sources(docs: list[dict]) -> list[dict]:
    return [
        {
            "note_id":   d.get("note_id", ""),
            "title":     d.get("title", ""),
            "note_url":  d.get("note_url", ""),
            "cover_url": d.get("cover_url", ""),
            "distance":  d.get("distance", 0.0),
        }
        for d in (docs or [])
    ]


def _stream_and_save(
    state: dict,
    session_id: str,
    user_id: str,
    session_store: SessionStore,
) -> Generator[str, None, None]:
    """
    流式输出 LLM 回复，完成（或被中断）后将 assistant message 存入 session。
    finally 块确保即使客户端断连也能保存已生成的内容。
    """
    full_reply = ""
    try:
        for chunk in analyze_stream_with_history(state["messages"], system_prompt=SYSTEM_PROMPT):
            full_reply += chunk
            yield chunk
    finally:
        if full_reply:
            state["messages"].append({"role": "assistant", "content": full_reply})
        session_store.save(session_id, user_id, state)


def handle_search(req, session_store: SessionStore) -> dict:
    """
    搜索模式：重新检索，保存 docs，清空 LLM 历史，不调 LLM。
    """
    docs = _retrieve(req.query, req.user_id, top_k=req.top_k)
    state = session_store.get(req.session_id) or _new_state()
    state.update({"docs": docs, "messages": [], "last_query": req.query})
    session_store.save(req.session_id, req.user_id, state)
    logger.info(f"[session] {req.session_id[:8]}… search → {len(docs)} docs")
    return {"mode": "search", "sources": _format_sources(docs)}


def handle_stream(
    req,
    session_store: SessionStore,
) -> tuple[list[dict], Generator[str, None, None]]:
    """
    分析模式：判断追问 / 新话题，返回 (sources, chunk_generator)。

    sources 在 SSE 流开始前推送给前端；
    chunk_generator 流式 yield LLM 输出，完成后自动存入 session。
    """
    state = session_store.get(req.session_id) or _new_state()

    # 追问判断：docs 已锁定 + 上轮 LLM 已回复 + 新问题被判为追问
    has_prior_analysis = bool(
        state["docs"]
        and state["messages"]
        and state["messages"][-1]["role"] == "assistant"
    )
    if has_prior_analysis and is_followup(req.query, state):
        logger.info(f"[session] {req.session_id[:8]}… → 追问，不检索")
        sources = _format_sources(state["docs"])
        state["messages"].append({"role": "user", "content": req.query})
        state["last_query"] = req.query
        # 先保存（含新 user message），streaming 结束后 _stream_and_save 再存 assistant
        session_store.save(req.session_id, req.user_id, state)
        return sources, _stream_and_save(state, req.session_id, req.user_id, session_store)

    # 新话题 / 首次分析
    if not state["docs"]:
        # docs 为空：直接分析或新话题，重新检索
        docs = _retrieve(req.query, req.user_id, top_k=req.top_k)
        state["docs"] = docs
        logger.info(f"[session] {req.session_id[:8]}… → 首次分析，检索 {len(docs)} docs")
    else:
        # docs 已有（搜索后切换 / is_followup=False 的新话题）：更新 docs
        if has_prior_analysis:
            # 明确的新话题：重新检索，刷新 docs
            docs = _retrieve(req.query, req.user_id, top_k=req.top_k)
            state["docs"] = docs
            logger.info(f"[session] {req.session_id[:8]}… → 新话题，刷新 docs")
        else:
            # 搜索后切换来的：复用 docs
            docs = state["docs"]
            logger.info(f"[session] {req.session_id[:8]}… → 复用 search docs")

    sources = _format_sources(docs)
    first_msg = build_analysis_user_message(req.query, docs)
    state["messages"] = [{"role": "user", "content": first_msg}]
    state["last_query"] = req.query
    session_store.save(req.session_id, req.user_id, state)
    return sources, _stream_and_save(state, req.session_id, req.user_id, session_store)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/test_session_handler.py -v
```

预期：5 个测试全部 PASS

- [ ] **Step 5: 运行全量测试，确认没有回归**

```bash
python -m pytest tests/ -v
```

预期：所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add rag/session_handler.py tests/test_session_handler.py
git commit -m "feat: add session_handler state machine (search/analysis/followup)"
```

---

## Task 5: `main.py` — 接入 session_id 和 session_handler

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 修改 `main.py` 顶部 import 区域**

在现有 import 块中（`from rag.generator import generate, generate_stream` 之后）新增：

```python
from rag.storage.session_store import SessionStore
from rag import session_handler
```

- [ ] **Step 2: 初始化 `_session_store` 单例**

在 `_sync_lock` / `_sync_state` 定义（约第 266 行）**之前**，新增：

```python
# ── Session 存储（进程级单例，复用 notes.db） ─────────────────────
_session_store = SessionStore(os.path.join(_PROJECT_ROOT, "data", "notes.db"))
```

- [ ] **Step 3: 修改 `QueryRequest`，新增 `session_id` 字段**

将现有 `QueryRequest` 替换为：

```python
class QueryRequest(BaseModel):
    query:      str                            = Field(..., min_length=1, description="用户问题")
    user_id:    str                            = Field(..., min_length=1, description="小红书用户 ID")
    mode:       Literal["search", "analysis"] = Field(default="search",  description="search=仅返回相关帖子；analysis=LLM 总结回答")
    top_k:      int                            = Field(default=6, ge=1, le=20, description="最多返回条数")
    session_id: str                            = Field(..., min_length=1, description="前端会话 UUID，用于关联后端状态")
```

- [ ] **Step 4: 重写 `/api/query` 端点，委托给 `handle_search`**

将整个 `/api/query` 函数替换为：

```python
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
```

- [ ] **Step 5: 重写 `/api/stream` 端点，委托给 `handle_stream`**

将整个 `/api/stream` 函数替换为：

```python
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
```

- [ ] **Step 6: 运行全量测试**

```bash
python -m pytest tests/ -v
```

预期：所有测试 PASS（main.py 改动不影响现有测试）

- [ ] **Step 7: 验证服务器能启动**

```bash
cd /Users/liyihan/projects/xhs-rag-assistant
python -c "import main; print('OK')"
```

预期：打印 `OK`，无报错

- [ ] **Step 8: Commit**

```bash
git add main.py
git commit -m "feat: wire session_id and session_handler into /api/query and /api/stream"
```

---

## Task 6: 前端 — 透传 `session_id`

**Files:**
- Modify: `frontend/src/hooks/useApi.js`
- Modify: `frontend/src/components/ChatArea.jsx`

- [ ] **Step 1: 修改 `useApi.js` — `queryApi` 加 `sessionId` 参数**

找到 `queryApi` 函数（约第 101 行），将其替换为：

```js
/** search 模式：JSON 响应 */
export async function queryApi({ query, userId, mode, sessionId, topK = 6 }) {
  const res = await fetch(`${BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      user_id: userId,
      mode,
      top_k: topK,
      session_id: sessionId,
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `请求失败 (${res.status})`)
  }
  return res.json()
}
```

- [ ] **Step 2: 修改 `useApi.js` — `queryStreamApi` 加 `sessionId` 参数**

找到 `queryStreamApi` 函数签名（约第 121 行），将参数和请求体替换为：

```js
export async function queryStreamApi({ query, userId, sessionId, topK = 6 }, callbacks = {}, signal) {
  const res = await fetch(`${BASE}/api/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      user_id: userId,
      mode: 'analysis',
      top_k: topK,
      session_id: sessionId,
    }),
    signal,
  })
  // 其余逻辑（reader 读取、事件分发）保持不变
```

其余 SSE 读取逻辑不需要改动。

- [ ] **Step 3: 修改 `ChatArea.jsx` — `sendMessage` 透传 `session.id`**

找到 `sendMessage` 中的两处 API 调用（约第 89–128 行），分别加入 `sessionId`：

```js
// analysis 模式（约第 90 行）
await queryStreamApi(
  { query, userId, sessionId: session.id },   // ← 加 sessionId
  {
    onSources: ...
    onChunk: ...
    onDone: ...
    onError: ...
  },
  abortRef.current.signal,
)

// search 模式（约第 122 行）
const result = await queryApi({ query, userId, mode, sessionId: session.id })  // ← 加 sessionId
```

- [ ] **Step 4: 启动前端开发服务器，手动验证**

```bash
cd /Users/liyihan/projects/xhs-rag-assistant/frontend
npm run dev
```

打开 http://localhost:5173，执行以下验证场景：

**场景 A — 搜索后切换分析：**
1. 选择搜索模式，发送「面试经验」
2. 切换到分析模式，发送「帮我总结一下」
3. 预期：控制台 Network 里 `/api/stream` 的 sources 与步骤 1 相同，无二次检索（查看服务端日志应有「复用 search docs」）

**场景 B — 追问：**
1. 分析模式发送「有哪些面试技巧？」
2. 再发送「第一点能展开说说吗？」
3. 预期：服务端日志显示「追问，不检索」，sources 与上一条相同

**场景 C — 新话题：**
1. 分析模式发送「有哪些面试技巧？」
2. 再发送「推荐一些旅行攻略」（完全不同话题）
3. 预期：服务端日志显示「新话题，刷新 docs」，sources 刷新

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useApi.js frontend/src/components/ChatArea.jsx
git commit -m "feat: pass session_id from frontend to backend API calls"
```

---

## Task 7: README 更新

**Files:**
- Modify or Create: `rag/README.md`
- Modify or Create: `rag/storage/README.md`

- [ ] **Step 1: 检查 README 是否存在**

```bash
ls /Users/liyihan/projects/xhs-rag-assistant/rag/
ls /Users/liyihan/projects/xhs-rag-assistant/rag/storage/
```

- [ ] **Step 2: 在 `rag/` 目录的 README（或 `rag/` 下各文件的 docstring）中补充说明**

若 `rag/README.md` 存在，追加如下内容；若不存在，在相关文件的模块 docstring 中补充即可（已在 Task 2–4 的代码注释中覆盖）。

需记录的变更：
- `followup.py`：新增，追问分类器，GLM-4.6，失败 fallback False
- `session_handler.py`：新增，状态机核心，封装三条路径
- `chat.py`：新增 `build_analysis_user_message` + `analyze_stream_with_history`
- `generator.py`：`_SYSTEM_PROMPT` → `SYSTEM_PROMPT`（公开）

- [ ] **Step 3: 在 `rag/storage/` 目录的 README 中补充说明**

需记录的变更：
- `session_store.py`：新增，SQLite `chat_sessions` 表，存 docs/messages/last_query
- `notes.db` 现在包含两张表：`notes`（原有）和 `chat_sessions`（新增）

- [ ] **Step 4: Commit**

```bash
git add rag/ 
git commit -m "docs: update READMEs for session state machine changes"
```

---

## 自检结果

**Spec 覆盖检查：**

| 需求 | 对应 Task |
|------|-----------|
| SQLite chat_sessions 表 | Task 1 |
| SessionStore get/save/delete | Task 1 |
| is_followup GLM-4.6 分类器 | Task 2 |
| 追问检测：docs + messages[-1] == assistant | Task 4 Step 3 |
| 搜索模式：检索 + 存 docs + 清空 messages | Task 4（handle_search） |
| 搜索→分析切换：复用 docs | Task 4（handle_stream，has_prior_analysis=False） |
| 直接分析：重新检索 | Task 4（handle_stream，docs 为空） |
| 新话题：刷新 docs + 清空 messages | Task 4（handle_stream，is_followup=False） |
| QueryRequest 加 session_id | Task 5 Step 3 |
| /api/query 委托 handle_search | Task 5 Step 4 |
| /api/stream 委托 handle_stream | Task 5 Step 5 |
| 前端透传 session_id | Task 6 |
| README 更新 | Task 7 |

**无 Placeholder**：所有步骤含完整代码。

**类型一致性**：`handle_search` 返回 `dict`，`handle_stream` 返回 `tuple[list[dict], Generator]`，与 Task 5 的 main.py 调用一致。`_format_sources` 在 session_handler 内定义并在两个函数中复用。
