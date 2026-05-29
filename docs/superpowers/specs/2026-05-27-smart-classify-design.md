# 智能分类设计文档

**日期**：2026-05-27
**负责人**：曾君毅（前端 + 分类 prompt 设计）
**状态**：已确认，待实现

---

## 背景与目标

当前系统已实现收藏同步、向量检索和 AI 问答，但笔记列表仅支持按时间排序浏览。用户收藏量大时（几十到上百条），缺乏结构化的分类导航。

项目计划书（第五节）定义本功能为：

> AI 自动分类与标签 — 自动生成内容分类，用户可移动、重命名与修正，保证可控性。

本次设计目标：
1. 同步完成后 AI 自动为每条笔记生成一个分类（单值，区别于 `tags` 多标签）
2. 用户可在前端按分类筛选笔记
3. 用户可修正分类结果（移动、重命名）
4. 重点是分类 prompt 设计（组长指示）

---

## 数据层

### notes 表新增字段

```sql
ALTER TABLE notes ADD COLUMN category TEXT NOT NULL DEFAULT '';
```

通过 `SQLiteStore._add_missing_columns()` 机制自动添加，无需手动迁移。已有笔记的 `category` 默认为空字符串，首次分类后填充。

### 与 tags 的关系

| 字段 | 来源 | 类型 | 语义 |
|------|------|------|------|
| `tags` | XHS 页面 DOM 提取 | JSON array | 笔记发布者打的原始标签，多值 |
| `category` | AI 分类 | TEXT 单值 | 用户侧的结构化分类导航，单值 |

`category` 是分类导航维度（"这篇笔记属于哪个类别"），`tags` 是笔记原始属性（"发布者标了什么标签"）。两者互补，不互相替代。

---

## 分类体系

### 预设类别（对齐项目计划书用户画像）

基于计划书定义的三类核心用户，预设 11 个类别：

| 类别 | 对应用户画像 | 覆盖内容 |
|------|-------------|---------|
| 好物推荐 | 囤货型决策者 | 产品推荐、购物清单、平价替代 |
| 穿搭美妆 | 囤货型决策者 | 穿搭技巧、美妆教程 |
| 家居生活 | 囤货型决策者 | 家居装饰、收纳、生活方式 |
| 旅游攻略 | 攻略收集者 | 旅行攻略、景点推荐、行程规划 |
| 求职面经 | 攻略收集者 | 面试经验、公司评价、求职技巧 |
| 考研考证 | 攻略收集者 | 考研/考证经验、备考资料 |
| 学习方法 | 知识囤积者 | 学习方法论、效率工具、记忆技巧 |
| 健身饮食 | 知识囤积者 | 健身教程、饮食计划、健康管理 |
| 职场技巧 | 知识囤积者 | 职场沟通、晋升技巧、副业 |
| 情绪自律 | 知识囤积者 | 情绪管理、自律习惯、心理健康 |
| 城市生活 | 攻略收集者 | 城市探店、本地生活、美食推荐 |
| 其他 | — | 无法归入以上类别的内容 |

### 分类策略（混合模式）

LLM 优先从预设列表中选择。若内容确实匹配不到任何预设类别，允许给出一个新的简短类别名。用户后续可重命名。

---

## 分类模块

### 新文件：`rag/classifier.py`

对外接口：

```python
# 单条分类
classify_note(note: dict) -> str

# 批量分类（返回分类结果映射）
classify_notes(notes: list[dict]) -> dict[str, str]
# 返回 {"note_id": "category", ...}

# 命令行入口（供 subprocess 调用）
# python -m rag.classifier
#   --user_id <user_id>   只分类该用户的笔记
#   --batch_size 10       每批次篇数（默认 10）
```

**核心函数 `classify_note(note)`**：
- 输入：note dict（含 `title`、`content`、`tags` 字段）
- 构建 prompt → 调用 LLM → 返回分类名
- 若 LLM 调用失败，返回空字符串（不阻塞流程）

### 分类子进程入口（`main()`）

`python -m rag.classifier` 作为子进程执行：
1. 从 SQLite 查询 `category = ''` 且 `is_collected = 1` 的笔记
2. 按 `user_id` 分批，每批调用 `classify_notes()`
3. 逐条写回 `category` 字段
4. 日志输出到 `data/sync.log`（与同步日志合并）

### Prompt 设计

参考 `rag/followup.py` 的模式：GLM-4.6、非思考模式、temperature=0、max_tokens 小。

```
System: 你是一个内容分类助手。根据笔记的标题、正文摘要和已有标签，
        判断它最可能属于哪个分类。只返回分类名，不要解释。

        可选分类（按优先级排列）：
        1. 好物推荐 — 产品推荐、购物清单、性价比分析
        2. 穿搭美妆 — 穿搭技巧、美妆教程、造型灵感
        3. 家居生活 — 家居装饰、收纳整理、生活方式
        4. 旅游攻略 — 旅行攻略、景点推荐、行程规划
        5. 求职面经 — 面试经验、公司评价、求职技巧
        6. 考研考证 — 考研/考证/考公经验、备考资料
        7. 学习方法 — 学习方法论、效率工具、记忆技巧
        8. 健身饮食 — 健身教程、饮食计划、健康管理
        9. 职场技巧 — 职场沟通、晋升、副业、创业
        10. 情绪自律 — 情绪管理、自律习惯、心理健康
        11. 城市生活 — 探店、本地美食、城市活动
        12. 其他 — 无法归入以上类别

        如果内容确实无法匹配以上任何类别，请给出一个简短的新类别名
        （不超过 4 个字）。只返回类别名。

User:   标题：{title}
        已有标签：{tags}
        内容摘要：{content_summary}
```

**content_summary 处理**：content 可能很长（含图片文字/视频转录），取前 500 字作为摘要送入 LLM，避免超出 token 限制。

**模型参数**：
- model: `glm-4.6`（与 `rag/llm_config.py` 中 `VISION_MODEL` 同系列，快速便宜）
- temperature: 0.0（分类需要确定性输出）
- max_tokens: 20（分类名很短）
- extra_body: `{"thinking": {"type": "disabled"}}`（非思考模式，加速响应）

---

## 同步流程改动

### `crawler/ingest.py` — 不改动

`ingest.py` 保持现有逻辑不变。分类不嵌入爬取流程，避免增加同步耗时。

### `main.py` — `_run_ingest()` 线程

在现有两步流程（爬取 → 导出调试页）中间插入分类步骤：

```python
# ── 第二步：AI 分类（新增） ────────────────────
if ingest_ok and user_id:  # 仅同步成功时分类
    subprocess.run(
        [sys.executable, "-m", "rag.classifier", "--user_id", user_id],
        cwd=_PROJECT_ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

# ── 第三步：导出调试页面（原第二步） ──────────
# ... 现有代码不变
```

`user_id` 从 `SyncRequest.user_id` 或环境变量 `XHS_USER_ID` 获取。

### `rag/storage/sqlite_store.py` 改动

1. `_add_missing_columns()` 新增 `category` 列定义
2. `upsert()` 写入时保留已有 `category` 值（分类结果不被重复同步覆盖）
3. 新增方法：

```python
def get_unclassified(self, user_id: str, limit: int = 100) -> list[dict]:
    """查询 category 为空且已收藏的笔记，供分类子进程使用。"""

def set_category(self, note_id: str, user_id: str, category: str) -> None:
    """写回分类结果。"""
```

---

## API 变更

### `main.py` API 变更

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/categories` | GET | 新增：返回某用户的分类列表（去重计数） |
| `/api/notes` | GET | 已有：新增 `?category=` 筛选参数 |
| `/api/notes/{note_id}/category` | PUT | 新增：用户手动修正笔记分类 |

#### `GET /api/categories`

查询参数：`user_id`（必填）

响应：
```json
{
  "categories": [
    {"name": "求职面经", "count": 15},
    {"name": "学习方法", "count": 8},
    ...
  ]
}
```

#### `GET /api/notes` 新增参数

在现有分页参数基础上增加：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `category` | string | 否 | 按分类筛选，空字符串表示查询未分类笔记 |

`SQLiteStore.all_notes()` 增加 `category: str = ""` 参数，非空时加 WHERE 条件。

---

## 前端变更

### `frontend/src/components/Sidebar.jsx`

1. 笔记列表上方新增**分类筛选栏**：水平滚动的分类标签按钮（"全部" + 各分类名 + 计数）
2. 点击分类标签过滤笔记列表
3. 分类列表从 `GET /api/categories` 获取

### `frontend/src/hooks/useApi.js`

新增两个 API 函数：
- `fetchCategories(userId)` — 调用 `GET /api/categories`
- `updateNoteCategory(noteId, userId, category)` — 调用 `PUT /api/notes/{note_id}/category`（用户修正分类时使用）

### `frontend/src/components/NoteCard.jsx`

每条笔记卡片上显示分类标签（小号 pill），用户可点击修改。

### 分类修正端点

```
PUT /api/notes/{note_id}/category
Body: {"user_id": "xxx", "category": "新分类名"}
```

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `rag/classifier.py` | 分类 prompt + LLM 调用 + 子进程入口 `main()` |
| 修改 | `rag/storage/sqlite_store.py` | 新增 `category` 列 + `get_unclassified()` + `set_category()` + `all_notes()` 筛选 |
| 修改 | `main.py` | 同步流程插入分类步骤；新增 `/api/categories`；`/api/notes` 加 `category` 参数；新增 `PUT /api/notes/{note_id}/category` |
| 修改 | `frontend/src/components/Sidebar.jsx` | 分类筛选栏 |
| 修改 | `frontend/src/components/NoteCard.jsx` | 分类标签展示 + 修改入口 |
| 修改 | `frontend/src/hooks/useApi.js` | 分类相关 API 调用 |
| 新增 | `tests/test_classifier.py` | 分类 prompt 单元测试（mock LLM） |

---

## 不在本次范围内

- 分类的拖拽排序或批量移动
- 用户自定义创建新分类（重命名已覆盖）
- 分类的历史记录/版本管理
- 多分类/嵌套分类（产品计划书定义为单分类）
