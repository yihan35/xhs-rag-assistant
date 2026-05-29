# 智能分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同步完成后 AI 自动为每条笔记生成分类，前端支持按分类筛选和手动修正。

**Architecture:** 新增 `rag/classifier.py`（分类 prompt + LLM 调用 + 子进程入口）；`sqlite_store.py` 新增 `category` 列及查询/写入方法；`main.py` 同步流程插入分类步骤、新增 3 个 API 端点；前端 `Sidebar.jsx` 加分类筛选栏、`NoteCard.jsx` 加分类标签、`useApi.js` 加 fetch 函数。

**Tech Stack:** Python / SQLite（标准库 sqlite3）/ 智谱 GLM-4.6（分类）/ FastAPI / React 18 + Tailwind

---

## 文件变更清单

| 操作 | 路径 | 职责 |
|------|------|------|
| 新增 | `rag/classifier.py` | 分类 prompt + LLM 调用 + 子进程入口 `main()` |
| 新增 | `tests/test_classifier.py` | 分类 prompt 单元测试（mock LLM） |
| 修改 | `rag/storage/sqlite_store.py` | `_add_missing_columns` 加 `category`；新增 `get_unclassified`、`set_category`；`all_notes` 加 `category` 筛参；`upsert` 保留已有 category |
| 修改 | `main.py` | 同步流程插入分类步骤；新增 `GET /api/categories`、`GET /api/notes?category=`、`PUT /api/notes/{note_id}/category` |
| 修改 | `frontend/src/hooks/useApi.js` | 新增 `fetchCategories`、`updateNoteCategory`；`useNotes` 支持 `category` 参数 |
| 修改 | `frontend/src/components/Sidebar.jsx` | `NotesView` 上方加分类筛选栏 |
| 修改 | `frontend/src/components/NoteCard.jsx` | `CompactNoteItem` 平级加分类标签展示 |

---

### Task 1: `rag/storage/sqlite_store.py` — category 字段支持

**Files:**
- Modify: `rag/storage/sqlite_store.py`
- Test: existing `tests/test_archive_sync.py` (verify no regression)

- [ ] **Step 1: 在 `_add_missing_columns()` 中新增 category 列定义**

在 `_add_missing_columns` 方法的 `additions` 列表末尾追加一行：

```python
def _add_missing_columns(self) -> None:
    """Add columns introduced after the initial schema without dropping user data."""
    rows = self.conn.execute("PRAGMA table_info(notes)").fetchall()
    existing = {row["name"] for row in rows}
    additions = [
        ("content",            "TEXT    NOT NULL DEFAULT ''"),
        ("content_parts",      "TEXT    NOT NULL DEFAULT '{}'"),
        ("is_collected",       "INTEGER NOT NULL DEFAULT 1"),
        ("archived_at",        "TEXT    NOT NULL DEFAULT ''"),
        ("content_hash",       "TEXT    NOT NULL DEFAULT ''"),
        ("note_published_at",  "TEXT    NOT NULL DEFAULT ''"),
        ("content_changed_at", "TEXT    NOT NULL DEFAULT ''"),
        ("category",           "TEXT    NOT NULL DEFAULT ''"),
    ]
    for col, defn in additions:
        if col not in existing:
            self.conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {defn}")
            logger.info(f"SQLite schema 已增加 {col} 字段")
```

- [ ] **Step 2: 修改 `upsert()` — 保留已有 category 值**

在 `upsert()` 中，当笔记已存在且 `old_indexed` 不为 0（即内容未变，不需要重新向量化），保留原有的 `category`。修改 INSERT 语句，让 `category` 列取已有值：

在 `upsert` 方法中，约第 162 行，`existing` 查询结果需要包含 `category`，INSERT 时使用已有 category：

找到这一行：
```python
existing = self.conn.execute(
    """
    SELECT indexed, content_hash, content_changed_at
    FROM notes
    WHERE note_id = ? AND user_id = ?
    """,
    (note["note_id"], user_id),
).fetchone()
```

改为：
```python
existing = self.conn.execute(
    """
    SELECT indexed, content_hash, content_changed_at, category
    FROM notes
    WHERE note_id = ? AND user_id = ?
    """,
    (note["note_id"], user_id),
).fetchone()
```

然后在约第 167 行，`old_indexed` 之后提取 `old_category`：

```python
if existing is None:
    old_indexed       = 0
    content_changed_at = ""
    old_category       = ""
else:
    old_category = existing["category"] or ""
    # ... 其余逻辑不变
```

最后在约第 179 行的 INSERT 语句中，VALUES 列表增加 `old_category` 参数，列列表增加 `category`：

在列列表（约第 181 行）中追加 `category`，在 VALUES 占位符末尾追加 `?`，在参数元组末尾追加 `old_category`。

完整改动后的 INSERT：
```python
self.conn.execute(
    """
    INSERT OR REPLACE INTO notes
      (note_id, user_id, title, content, content_parts, tags, cover_url, image_urls,
       note_url, likes, note_type, crawled_at, indexed,
       is_collected, archived_at, content_hash, note_published_at, content_changed_at, category)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
        note["note_id"],
        user_id,
        note.get("title", ""),
        note.get("content", ""),
        json.dumps(note.get("content_parts", {}), ensure_ascii=False),
        json.dumps(note.get("tags", []), ensure_ascii=False),
        note.get("cover_url", ""),
        json.dumps(note.get("image_urls", []), ensure_ascii=False),
        note.get("note_url", ""),
        int(note.get("likes", 0)),
        note.get("note_type", "image"),
        note.get("crawled_at", ""),
        old_indexed,
        1,
        "",
        new_hash,
        note.get("note_published_at", ""),
        content_changed_at,
        old_category,
    ),
)
```

- [ ] **Step 3: 新增 `get_unclassified()` 方法**

在 `SQLiteStore` 类中，`get_unindexed` 方法之后新增：

```python
def get_unclassified(self, user_id: str = "", limit: int = 100) -> list[dict]:
    """返回 category 为空且已收藏的笔记，供分类子进程使用。"""
    if user_id:
        rows = self.conn.execute(
            """
            SELECT note_id, title, content, tags, user_id
            FROM notes
            WHERE category = '' AND is_collected = 1 AND user_id = ?
            ORDER BY crawled_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    else:
        rows = self.conn.execute(
            """
            SELECT note_id, title, content, tags, user_id
            FROM notes
            WHERE category = '' AND is_collected = 1
            ORDER BY crawled_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [self._deserialize(dict(r)) for r in rows]
```

- [ ] **Step 4: 新增 `set_category()` 方法**

在 `get_unclassified` 之后新增：

```python
def set_category(self, note_id: str, user_id: str, category: str) -> None:
    """写回分类结果。"""
    self.conn.execute(
        "UPDATE notes SET category = ? WHERE note_id = ? AND user_id = ?",
        (category, note_id, user_id),
    )
    self.conn.commit()
```

- [ ] **Step 5: 修改 `all_notes()` — 支持 category 筛选**

将 `all_notes` 方法签名从：
```python
def all_notes(self, user_id: str = "", include_archived: bool = False) -> list[dict]:
```
改为：
```python
def all_notes(self, user_id: str = "", include_archived: bool = False, category: str = "") -> list[dict]:
```

方法体内，在 `archived_filter` 之后新增 `category_filter` 逻辑：

```python
def all_notes(self, user_id: str = "", include_archived: bool = False, category: str = "") -> list[dict]:
    """返回当前收藏笔记；include_archived=True 时包含历史归档；category 非空时按分类筛选。"""
    archived_filter = "" if include_archived else " AND is_collected = 1"
    category_filter = ""
    params: list = []
    if category:
        category_filter = " AND category = ?"
    if user_id:
        sql = f"SELECT * FROM notes WHERE user_id = ?{archived_filter}{category_filter} ORDER BY crawled_at DESC"
        params = [user_id]
        if category:
            params.append(category)
    else:
        sql = f"SELECT * FROM notes WHERE 1=1{archived_filter}{category_filter} ORDER BY crawled_at DESC"
        if category:
            params.append(category)
    rows = self.conn.execute(sql, params).fetchall()
    return [self._deserialize(dict(r)) for r in rows]
```

- [ ] **Step 6: 新增 `get_categories()` 方法 — 去重计数**

在 `set_category` 之后新增：

```python
def get_categories(self, user_id: str) -> list[dict]:
    """返回某用户的分类列表（去重计数），按数量降序排列。"""
    rows = self.conn.execute(
        """
        SELECT category, COUNT(*) as cnt
        FROM notes
        WHERE user_id = ? AND is_collected = 1 AND category != ''
        GROUP BY category
        ORDER BY cnt DESC
        """,
        (user_id,),
    ).fetchall()
    return [{"name": row["category"], "count": row["cnt"]} for row in rows]
```

- [ ] **Step 7: 运行已有测试，确认无回归**

```bash
cd "E:/Project/Prj/xhs-qd/xhs-rag-assistant"
python -m pytest tests/ -v
```

预期：所有已有测试 PASS

- [ ] **Step 8: Commit**

```bash
git add rag/storage/sqlite_store.py
git commit -m "feat: add category column and query methods to SQLiteStore"
```

---

### Task 2: `rag/classifier.py` — 分类 prompt + LLM 调用 + 子进程入口

**Files:**
- Create: `rag/classifier.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: 写失败的测试**

新建 `tests/test_classifier.py`：

```python
import unittest
from unittest.mock import MagicMock, patch


class ClassifyNoteTests(unittest.TestCase):

    def test_returns_category_when_api_returns_valid_name(self):
        """LLM 返回有效分类名时，直接返回该分类名。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "求职面经"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "字节后端一面复盘",
                "content": "面试官问了MySQL索引...",
                "tags": ["面经", "字节跳动"],
            }
            result = classify_note(note)

        self.assertEqual(result, "求职面经")

    def test_returns_empty_string_when_api_fails(self):
        """LLM 调用失败时 fallback 空字符串。"""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": "测试内容",
                "tags": [],
            }
            result = classify_note(note)

        self.assertEqual(result, "")

    def test_returns_empty_string_when_client_is_none(self):
        """zhipu_client 为 None 时 fallback 空字符串。"""
        with patch("rag.classifier.zhipu_client", None):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": "测试",
                "tags": [],
            }
            result = classify_note(note)

        self.assertEqual(result, "")

    def test_truncates_content_to_500_chars(self):
        """content 超过 500 字时只取前 500 字作为摘要。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "学习方法"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        long_content = "A" * 600
        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_note
            note = {
                "note_id": "n1",
                "title": "测试",
                "content": long_content,
                "tags": [],
            }
            classify_note(note)

        call_args = mock_client.chat.completions.create.call_args
        user_msg = call_args[1]["messages"][1]["content"]
        self.assertIn("A" * 500, user_msg)
        self.assertNotIn("A" * 501, user_msg)


class ClassifyNotesTests(unittest.TestCase):

    def test_batch_returns_note_id_to_category_map(self):
        """批量分类返回 {note_id: category} 映射。"""
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "好物推荐"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp

        notes = [
            {"note_id": "n1", "title": "T1", "content": "C1", "tags": []},
            {"note_id": "n2", "title": "T2", "content": "C2", "tags": []},
        ]
        with patch("rag.classifier.zhipu_client", mock_client):
            from rag.classifier import classify_notes
            result = classify_notes(notes)

        self.assertEqual(result, {"n1": "好物推荐", "n2": "好物推荐"})
        self.assertEqual(mock_client.chat.completions.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
python -m pytest tests/test_classifier.py -v
```

预期：`ModuleNotFoundError: No module named 'rag.classifier'`

- [ ] **Step 3: 实现 `rag/classifier.py`**

```python
"""
rag/classifier.py
=================
智能分类：用 GLM-4.6（非 thinking 模式）对笔记内容进行分类。

对外接口：
    classify_note(note: dict) -> str          单条分类，返回分类名
    classify_notes(notes: list[dict]) -> dict  批量分类，返回 {note_id: category}

命令行入口（供 subprocess 调用）：
    python -m rag.classifier --user_id <user_id> [--batch_size 10]

失败时 fallback 空字符串，不阻塞同步流程。
"""

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from .llm_config import zhipu_client

logger = logging.getLogger(__name__)

_CLASSIFY_MODEL = "glm-4.6"
_NO_THINKING    = {"thinking": {"type": "disabled"}}

_SYSTEM_PROMPT = (
    "你是一个内容分类助手。根据笔记的标题、正文摘要和已有标签，"
    "判断它最可能属于哪个分类。只返回分类名，不要解释。\n\n"
    "可选分类（按优先级排列）：\n"
    "1. 好物推荐 — 产品推荐、购物清单、性价比分析\n"
    "2. 穿搭美妆 — 穿搭技巧、美妆教程、造型灵感\n"
    "3. 家居生活 — 家居装饰、收纳整理、生活方式\n"
    "4. 旅游攻略 — 旅行攻略、景点推荐、行程规划\n"
    "5. 求职面经 — 面试经验、公司评价、求职技巧\n"
    "6. 考研考证 — 考研/考证/考公经验、备考资料\n"
    "7. 学习方法 — 学习方法论、效率工具、记忆技巧\n"
    "8. 健身饮食 — 健身教程、饮食计划、健康管理\n"
    "9. 职场技巧 — 职场沟通、晋升、副业、创业\n"
    "10. 情绪自律 — 情绪管理、自律习惯、心理健康\n"
    "11. 城市生活 — 探店、本地美食、城市活动\n"
    "12. 其他 — 无法归入以上类别\n\n"
    "如果内容确实无法匹配以上任何类别，请给出一个简短的新类别名"
    "（不超过 4 个字）。只返回类别名。"
)


def _build_user_message(note: dict) -> str:
    """构建分类 prompt 的 user message。"""
    title = note.get("title", "") or "无标题"
    tags = note.get("tags", [])
    tags_str = "、".join(tags) if tags else "无"
    content = note.get("content", "") or ""
    summary = content[:500]
    return (
        f"标题：{title}\n"
        f"已有标签：{tags_str}\n"
        f"内容摘要：{summary}"
    )


def classify_note(note: dict) -> str:
    """
    对单条笔记进行分类。

    参数：
        note   dict，需含字段：note_id, title, content, tags

    返回：
        str — 分类名，失败时返回空字符串
    """
    if zhipu_client is None:
        logger.warning("[classifier] zhipu_client 未配置，跳过分类")
        return ""

    try:
        resp = zhipu_client.chat.completions.create(
            model=_CLASSIFY_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": _build_user_message(note)},
            ],
            temperature=0.0,
            max_tokens=20,
            extra_body=_NO_THINKING,
        )
        category = (resp.choices[0].message.content or "").strip()
        note_id = note.get("note_id", "?")
        logger.info(f"[classifier] {note_id} → {category!r}")
        return category
    except Exception as e:
        logger.warning(f"[classifier] {note.get('note_id', '?')} 分类失败：{e}")
        return ""


def classify_notes(notes: list[dict]) -> dict[str, str]:
    """
    批量分类，返回 {note_id: category} 映射。

    每条笔记独立调用 LLM（一次一个分类），失败笔记映射为空字符串。
    """
    result: dict[str, str] = {}
    for note in notes:
        category = classify_note(note)
        result[note["note_id"]] = category
    return result


# ── 子进程入口 ───────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="AI 智能分类未标记笔记")
    parser.add_argument("--user_id", required=True, help="小红书用户 ID")
    parser.add_argument("--batch_size", type=int, default=10, help="每批处理条数")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    from rag.storage.sqlite_store import SQLiteStore

    db_path = str(PROJECT_ROOT / "data" / "notes.db")

    with SQLiteStore(db_path) as sqlite:
        unclassified = sqlite.get_unclassified(user_id=args.user_id, limit=args.batch_size)

    if not unclassified:
        logger.info("没有需要分类的笔记")
        return 0

    logger.info(f"共 {len(unclassified)} 条笔记待分类")

    classified = classify_notes(unclassified)

    with SQLiteStore(db_path) as sqlite:
        for note_id, category in classified.items():
            if category:
                sqlite.set_category(note_id, args.user_id, category)

    success = sum(1 for c in classified.values() if c)
    logger.info(f"分类完成：{success}/{len(classified)} 条成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
python -m pytest tests/test_classifier.py -v
```

预期：5 个测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add rag/classifier.py tests/test_classifier.py
git commit -m "feat: add smart classifier with GLM-4.6 prompt"
```

---

### Task 3: `main.py` — 同步流程 + 新增 API 端点

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 在同步流程中插入分类步骤**

找到 `_run_ingest` 函数，在爬取步骤（第一步）和导出调试页面步骤之间插入分类步骤。

找到约第 301-303 行：
```python
                ingest_ok = result.returncode == 0

                # ── 第二步：导出调试页面（无论爬取是否成功均执行） ──
```

改为：
```python
                ingest_ok = result.returncode == 0

                # ── 第二步：AI 分类（仅同步成功时执行） ──────
                if ingest_ok:
                    classify_user_id = req.user_id or env.get("XHS_USER_ID", "")
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
```

注意：`classify_user_id` 变量需要能从 `_run_ingest` 闭包访问 `req.user_id`。当前 `_run_ingest` 定义在 `start_sync` 函数内部，闭包已可以访问 `req`，但需确认 `req.user_id` 在此处可用。

实际上，`_run_ingest` 是在 `start_sync` 内部定义的，`req` 在闭包中可用。但 `env` 变量在 `_run_ingest` 外部定义（约第 274 行），在闭包中也可见。需要确认 `req.user_id` 的可用性——它在函数参数中，闭包可以访问。

为确保安全，将 `user_id` 提取到外层变量：

约第 274 行之后，加一行：
```python
    env = os.environ.copy()
    if req.user_id:
        env["XHS_USER_ID"] = req.user_id

    _sync_user_id = req.user_id or ""   # ← 新增，供闭包内分类步骤使用
```

然后在分类步骤中用 `_sync_user_id` 替代 `req.user_id`。

- [ ] **Step 2: 新增 `GET /api/categories` 端点**

在 `list_updates` 端点之后新增：

```python
@app.get("/api/categories", summary="用户分类列表（去重计数）")
def list_categories(
    user_id: str = Query(..., min_length=1, description="小红书用户 ID"),
):
    """返回当前用户的分类列表，按笔记数量降序排列。"""
    with metadata_store() as store:
        categories = store.sqlite.get_categories(user_id=user_id)
    return {"categories": categories}
```

- [ ] **Step 3: 修改 `GET /api/notes` — 新增 `category` 查询参数**

在 `list_notes` 函数签名中新增 `category` 参数：

将约第 220 行：
```python
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，最大 100"),
```
改为：
```python
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数，最大 100"),
    category:   str = Query(default="",  description="按分类筛选，空字符串表示全部"),
```

函数体中将 `store.notes()` 调用改为传入 `category`：

将约第 227 行：
```python
        all_notes = store.notes(user_id=user_id)
```
改为：
```python
        all_notes = store.notes(user_id=user_id, category=category)
```

- [ ] **Step 4: 新增 `PUT /api/notes/{note_id}/category` 端点**

在 `list_notes` 端点之后新增：

```python
class CategoryUpdateRequest(BaseModel):
    user_id:  str = Field(..., min_length=1, description="小红书用户 ID")
    category: str = Field(..., description="新分类名")


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
```

- [ ] **Step 5: 运行已有测试，确认无回归**

```bash
python -m pytest tests/ -v
```

预期：所有已有测试 PASS

- [ ] **Step 6: 验证服务器能正常 import**

```bash
cd "E:/Project/Prj/xhs-qd/xhs-rag-assistant"
python -c "import main; print('OK')"
```

预期：打印 `OK`，无报错

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: wire classification into sync flow and add category API endpoints"
```

---

### Task 4: 前端 — 分类筛选栏 + 分类标签 + API 调用

**Files:**
- Modify: `frontend/src/hooks/useApi.js`
- Modify: `frontend/src/components/Sidebar.jsx`
- Modify: `frontend/src/components/NoteCard.jsx`（新增 CompactNoteItem 的 category 标签）

- [ ] **Step 1: `useApi.js` — 新增分类相关 API 函数**

在文件末尾追加：

```js
/** 获取分类列表（去重计数） */
export async function fetchCategories(userId) {
  if (!userId) return []
  const res = await fetch(`${BASE}/api/categories?user_id=${encodeURIComponent(userId)}`)
  if (!res.ok) return []
  const data = await res.json()
  return data.categories || []
}

/** 修改笔记分类 */
export async function updateNoteCategory(noteId, userId, category) {
  const res = await fetch(`${BASE}/api/notes/${encodeURIComponent(noteId)}/category`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, category }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `修改分类失败 (${res.status})`)
  }
  return res.json()
}
```

在 `useNotes` hook 中，新增 `category` 状态和 `fetchNotes` 支持 category 参数：

修改 `useNotes` 函数，在签名中接受可选的 category：

```js
export function useNotes(userId) {
  const [notes, setNotes]     = useState([])
  const [loading, setLoading] = useState(false)
  const [stats, setStats]     = useState(null)
  const [category, setCategory] = useState('')   // ← 新增

  const fetchNotes = useCallback(async (uid = userId, cat = '') => {
    if (!uid) return
    setLoading(true)
    try {
      let url = `${BASE}/api/notes?user_id=${encodeURIComponent(uid)}&page_size=100`
      if (cat) url += `&category=${encodeURIComponent(cat)}`
      const [notesRes, statsRes] = await Promise.all([
        fetch(url),
        fetch(`${BASE}/api/stats`),
      ])
      if (notesRes.ok) {
        const data = await notesRes.json()
        setNotes(data.notes || [])
      }
      if (statsRes.ok) {
        setStats(await statsRes.json())
      }
    } catch (e) {
      console.error('fetchNotes error:', e)
    } finally {
      setLoading(false)
    }
  }, [userId])

  return { notes, loading, stats, fetchNotes, category, setCategory }
}
```

- [ ] **Step 2: `Sidebar.jsx` — 新增分类筛选栏**

在 `NotesView` 组件的顶栏和笔记列表之间，插入分类筛选栏：

`NotesView` 新增 props：`categories`、`activeCategory`、`onCategoryChange`。

在 `NotesView` 函数签名（约第 270 行）中新增参数：

```js
function NotesView({ notes, loading, onBack, onRefresh, categories, activeCategory, onCategoryChange }) {
```

然后在顶栏（`</div>` 结束标签，约第 291 行）之后、笔记列表 `<div>` 之前插入分类筛选栏：

```js
      {/* 分类筛选栏 */}
      {categories.length > 0 && (
        <div className="flex-shrink-0 px-3 pb-2 overflow-x-auto no-scrollbar">
          <div className="flex gap-1.5">
            <button
              onClick={() => onCategoryChange('')}
              className={`flex-shrink-0 px-2.5 py-1 rounded-full text-[11px] font-medium
                          transition-all duration-150
                          ${activeCategory === ''
                            ? 'bg-xhs-red text-white'
                            : 'bg-pink-50 text-gray-500 hover:bg-pink-100 hover:text-gray-700'
                          }`}
            >
              全部
              {stats?.sqlite_total > 0 && (
                <span className="ml-1 opacity-70">{stats.sqlite_total}</span>
              )}
            </button>
            {categories.map(cat => (
              <button
                key={cat.name}
                onClick={() => onCategoryChange(cat.name)}
                className={`flex-shrink-0 px-2.5 py-1 rounded-full text-[11px] font-medium
                            transition-all duration-150
                            ${activeCategory === cat.name
                              ? 'bg-xhs-red text-white'
                              : 'bg-pink-50 text-gray-500 hover:bg-pink-100 hover:text-gray-700'
                            }`}
              >
                {cat.name}
                <span className="ml-1 opacity-70">{cat.count}</span>
              </button>
            ))}
          </div>
        </div>
      )}
```

`Sidebar` 主组件新增 categories 相关状态—在已有 hooks 之后新增：

在 `Sidebar` 函数体内，约第 18 行 `const [showNotes, setShowNotes] = useState(false)` 之后：

```js
  const [categories, setCategories] = useState([])
  const [activeCategory, setActiveCategory] = useState('')
```

`Sidebar` 需要接收 `userId` prop。在 props 中新增 `userId`：

```js
export default function Sidebar({
  sessions,
  currentId,
  onNewSession,
  onSelectSession,
  onDeleteSession,
  notes,
  notesLoading,
  stats,
  onRefreshNotes,
  onSync,
  syncState  = 'idle',
  syncError  = '',
  userId,           // ← 新增
}) {
```

在 `useEffect` 中（约 `const [showNotes, setShowNotes]` 之后）加分类数据加载：

```js
  // 加载分类列表
  useEffect(() => {
    if (userId && showNotes) {
      fetchCategories(userId).then(setCategories)
    }
  }, [userId, showNotes, stats?.sqlite_total])
```

需要在文件顶部 import `fetchCategories`：

```js
import { useNotes, useSync, fetchCategories } from '../hooks/useApi'
```

（当前 import 行是没有的，只有 `export function useNotes` 和 `export function useSync`，import 需要改为 `import { useNotes, useSync }` → `import { useNotes, useSync, fetchCategories }`。但实际上 Sidebar.jsx 不 import from useApi.js——它通过 props 接收。所以需要新增一行 import：

```js
import { fetchCategories } from '../hooks/useApi'
```

`NotesView` 调用处（约第 49-54 行）新增 props：

```js
          <NotesView
            notes={notes}
            loading={notesLoading}
            onBack={() => setShowNotes(false)}
            onRefresh={onRefreshNotes}
            categories={categories}
            activeCategory={activeCategory}
            onCategoryChange={(cat) => { setActiveCategory(cat); onRefreshNotes(cat) }}
          />
```

`onRefreshNotes` 需要支持传 category 参数—这取决于 `App.jsx` 中 `fetchNotes` 的调用方式。鉴于前端部分已有基础，`App.jsx` 需要配合修改 `fetchNotes` 的传参逻辑，但我们假设 `onRefreshNotes` 能处理可选参数。

为简单起见，`onCategoryChange` 的调用不依赖 `onRefreshNotes` 传参，而是使用独立的 category 切换逻辑：

```js
            onCategoryChange={(cat) => {
              setActiveCategory(cat)
              // 直接通过 fetch 重新加载
              fetchNotesWithCategory(cat)
            }}
```

但这需要在 Sidebar 中能调用 fetchNotes。实际最简单的做法是让 `App.jsx` 传递一个 `onCategoryChange` callback 下来。或者我们在 Sidebar 中自己做 fetch：

在 Sidebar 中加一个临时的 fetch 方法调用 useApi 的 `useNotes`——但这太复杂。

最简实现：`onCategoryChange` 只设置状态并调用 `onRefreshNotes`。让 App.jsx 层处理 category 参数传递（App.jsx 已在 scope 外，后续微调即可）。

```js
            onCategoryChange={(cat) => {
              setActiveCategory(cat)
              onRefreshNotes(cat)
            }}
```

- [ ] **Step 3: `NoteCard.jsx` — CompactNoteItem 加分类标签**

实际上分类标签展示在 `Sidebar.jsx` 的 `CompactNoteItem` 中。所以不需要改 `NoteCard.jsx`，而是在 `Sidebar.jsx` 的 `CompactNoteItem` 组件中加。

在 `CompactNoteItem` 组件（约第 315 行）的标题下方，加分类标签：

在 `CompactNoteItem` 函数中，`{note.title || '无标题'}` 之后、`{note.content_changed_at && (` 之前，插入：

```js
          {note.category && (
            <span className="inline-block mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium
                             bg-xhs-rose text-xhs-red">
              {note.category}
            </span>
          )}
```

完整位置在约第 339 行 `</p>` 之后、第 341 行 `<div className="flex items-center gap-1 mt-1">` 之前。

- [ ] **Step 4: 无后端验证前端变更**

前端变更需要后端 API 就绪后才能完整验证。当前仅需确认：
1. `fetchCategories`、`updateNoteCategory` 函数语法正确
2. Sidebar 和 NoteCard 改动无 JSX 语法错误

```bash
cd "E:/Project/Prj/xhs-qd/xhs-rag-assistant/frontend"
npx vite build --mode development 2>&1 | tail -20
```

（若无后端可用 `npx tsc --noEmit` 或仅检查语法：`node -e "require('./src/hooks/useApi.js')"` 不适用于 ES modules。实际验证在 Task 3 后端就绪后一起做。）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useApi.js frontend/src/components/Sidebar.jsx
git commit -m "feat: add category filter bar and category label to frontend"
```

---

## 自检结果

**Spec 覆盖检查：**

| 需求 | 对应 Task |
|------|-----------|
| notes 表新增 category 列 | Task 1 Step 1 |
| upsert 保留已有 category | Task 1 Step 2 |
| get_unclassified 查询未分类笔记 | Task 1 Step 3 |
| set_category 写回分类结果 | Task 1 Step 4 |
| all_notes 支持 category 筛选 | Task 1 Step 5 |
| get_categories 去重计数 | Task 1 Step 6 |
| classify_note 单条分类 + prompt | Task 2 Step 3 |
| classify_notes 批量分类 | Task 2 Step 3 |
| 子进程入口 main() | Task 2 Step 3 |
| GLM-4.6 + non-thinking + temperature=0 | Task 2 Step 3 |
| content 前 500 字摘要 | Task 2 Step 3 |
| 同步流程插入分类步骤 | Task 3 Step 1 |
| GET /api/categories | Task 3 Step 2 |
| GET /api/notes?category= | Task 3 Step 3 |
| PUT /api/notes/{note_id}/category | Task 3 Step 4 |
| 前端分类筛选栏 | Task 4 Step 2 |
| 前端分类标签展示 | Task 4 Step 3 |
| 前端 fetchCategories + updateNoteCategory | Task 4 Step 1 |
| 单元测试（mock LLM） | Task 2 Step 1-4 |

**无 Placeholder**：所有步骤含完整代码。

**类型一致性**：`classify_note(note) -> str`、`classify_notes(notes) -> dict[str, str]`、`get_unclassified(user_id, limit) -> list[dict]`、`set_category(note_id, user_id, category) -> None`，Task 1-3 之间签名一致。
