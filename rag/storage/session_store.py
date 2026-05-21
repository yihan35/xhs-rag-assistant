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
from pathlib import Path

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
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
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
            "docs":       json.loads(row[0]) or None,   # [] 和 None 均表示「无已锁定文档」
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
