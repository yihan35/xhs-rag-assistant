# 动态建议问题 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 首页建议问题从写死改为根据用户收藏内容 LLM 动态生成。

**Architecture:** main.py 新增进程级 `_suggestions_cache` 字典 + `GET /api/suggestions` 端点，同步/分类完成后清缓存；ChatArea.jsx 的 `SUGGESTIONS` 改为 `useState` + `useEffect` 调 API。

**Tech Stack:** Python / FastAPI / 智谱 GLM-4.6 / React 18

---

## 文件变更清单

| 操作 | 路径 | 职责 |
|------|------|------|
| 修改 | `main.py` | 新增 `_suggestions_cache`、`GET /api/suggestions`、缓存失效逻辑 |
| 修改 | `frontend/src/components/ChatArea.jsx` | 硬编码 → 动态获取 |

---

### Task 1: `main.py` — 建议问题缓存 + API + 失效

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 在分类状态之后新增缓存和端点**

在 `_classify_lock` / `_classify_state` 定义之后（约第 418 行），`class ClassifyRequest` 之前，插入：

```python
# ── 建议问题缓存（进程级） ──────────────────────────────────────────

_suggestions_cache: dict[str, list[str]] = {}


class SuggestionsResponse(BaseModel):
    suggestions: list[str]


@app.get("/api/suggestions", response_model=SuggestionsResponse, summary="获取个性化建议问题")
def get_suggestions(
    user_id: str = Query(..., min_length=1, description="小红书用户 ID"),
):
    """根据用户收藏内容动态生成引导问题。收藏为空时返回默认问题。"""
    DEFAULT = [
        "面试经验有哪些总结？",
        "有没有旅行攻略推荐？",
        "求职简历怎么写？",
        "好用的生产力工具？",
    ]

    # 检查缓存
    if user_id in _suggestions_cache:
        return {"suggestions": _suggestions_cache[user_id]}

    # 查用户收藏
    with metadata_store() as store:
        total = store.sqlite.count(user_id=user_id)
        if total == 0:
            return {"suggestions": DEFAULT}
        categories = store.sqlite.get_categories(user_id=user_id)
        notes = store.sqlite.all_notes(user_id=user_id, category="")
        if not notes:
            notes = store.sqlite.all_notes(user_id=user_id)
        notes = notes[:10]

    # 拼分类和标题
    cats_str = ", ".join(f"{c['name']}({c['count']})" for c in categories[:8]) if categories else "暂无分类"
    titles_str = "\n".join(f"- {n.get('title', '无标题')[:40]}" for n in notes)

    from rag.llm_config import zhipu_client
    if zhipu_client is None:
        return {"suggestions": DEFAULT}

    try:
        resp = zhipu_client.chat.completions.create(
            model="glm-4.6",
            messages=[
                {"role": "system", "content": (
                    "你是一个对话引导助手。根据用户的收藏内容，"
                    "生成 4 个用户可能感兴趣的问题。"
                    "只返回问题列表，每行一个问题，以 '- ' 开头。"
                    "问题应该覆盖不同分类，引导用户深入探索自己的收藏。"
                    "每个问题 10-20 字，中文口语风格。"
                )},
                {"role": "user", "content": (
                    f"该用户收藏了 {total} 篇笔记\n"
                    f"分类分布：{cats_str}\n"
                    f"部分笔记标题：\n{titles_str}"
                )},
            ],
            temperature=0.3,
            max_tokens=200,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = resp.choices[0].message.content or ""
        suggestions = [line.lstrip("- ").strip() for line in raw.split("\n") if line.strip().startswith("-")]
        suggestions = suggestions[:4]
        if len(suggestions) < 2:
            suggestions = DEFAULT
    except Exception:
        logger.warning("[suggestions] LLM 生成失败，使用默认问题")
        suggestions = DEFAULT

    _suggestions_cache[user_id] = suggestions
    return {"suggestions": suggestions}
```

- [ ] **Step 2: 同步完成后清缓存**

在 `_sync_state["last_sync"] = datetime.now(timezone.utc).isoformat()` 之后（约第 587 行），加一行：

```python
                _suggestions_cache.pop(_sync_user_id, None)
```

完整上下文：
```python
            else:
                _sync_state["last_sync"] = datetime.now(timezone.utc).isoformat()
                _suggestions_cache.pop(_sync_user_id, None)
                logger.info(f"同步子进程完成，日志：{log_path}")
```

- [ ] **Step 3: 分类完成后清缓存**

在 `_classify_state["last_run"] = datetime.now(timezone.utc).isoformat()` 之后（约第 460 行），加一行：

```python
                _suggestions_cache.pop(req.user_id, None)
```

完整上下文：
```python
            if result.returncode == 0:
                _classify_state["last_run"] = datetime.now(timezone.utc).isoformat()
                _suggestions_cache.pop(req.user_id, None)
                logger.info(f"手动分类完成，user_id={req.user_id}")
```

- [ ] **Step 4: 验证后端**

```bash
cd "E:/Project/Prj/xhs-qd/xhs-rag-assistant"
python -c "import main; print('OK')"
```

预期：`OK`

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: add dynamic suggestions API with in-memory cache"
```

---

### Task 2: `ChatArea.jsx` — 动态获取建议问题

**Files:**
- Modify: `frontend/src/components/ChatArea.jsx`

- [ ] **Step 1: 替换硬编码 SUGGESTIONS 为动态状态**

删除第 6-11 行的 `const SUGGESTIONS = [...]`，在 `ChatArea` 组件内的 state 声明区新增：

在 `const [autoScroll, setAutoScroll] = useState(true)` 之后（约第 17 行）：

```jsx
  const [suggestions, setSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
```

- [ ] **Step 2: 新增 useEffect 加载建议**

在 `// 自动调整 textarea 高度` 的 `useEffect` 之后（约第 47 行之后），新增：

```jsx
  // 加载个性化建议问题
  useEffect(() => {
    if (!userId || noteCount === null || noteCount === undefined) return
    setSuggestionsLoading(true)
    fetch(`${BASE}/api/suggestions?user_id=${encodeURIComponent(userId)}`)
      .then(res => res.ok ? res.json() : Promise.reject(res))
      .then(data => setSuggestions(data.suggestions || []))
      .catch(() => setSuggestions([]))
      .finally(() => setSuggestionsLoading(false))
  }, [userId, noteCount])
```

文件顶部需要 `BASE` 常量。当前没有导入但 useApi.js 中定义了 `const BASE = ''`。需要新增：

在第 1 行 import 后加：
```jsx
const BASE = ''
```

或者直接在 fetch URL 中写 `/api/suggestions?...`（Vite proxy 转发）。

实际上直接用绝对路径：
```jsx
  useEffect(() => {
    if (!userId || noteCount === null || noteCount === undefined) return
    setSuggestionsLoading(true)
    fetch(`/api/suggestions?user_id=${encodeURIComponent(userId)}`)
      .then(res => res.ok ? res.json() : Promise.reject(res))
      .then(data => setSuggestions(data.suggestions || []))
      .catch(() => setSuggestions([]))
      .finally(() => setSuggestionsLoading(false))
  }, [userId, noteCount])
```

- [ ] **Step 3: 修改 WelcomeScreen 传递加载状态**

找到 WelcomeScreen 调用处（约第 304 行附近）：

当前：
```jsx
          <WelcomeScreen mode={mode} onSuggest={handleSuggest} />
```

改为：
```jsx
          <WelcomeScreen mode={mode} onSuggest={handleSuggest} suggestions={suggestions} loading={suggestionsLoading} />
```

- [ ] **Step 4: 修改 WelcomeScreen 组件使用动态数据**

找到 `function WelcomeScreen`（约第 285 行），修改函数签名和渲染：

```jsx
function WelcomeScreen({ mode, onSuggest, suggestions, loading }) {
  const DEFAULT_SUGGESTIONS = [
    '面试经验有哪些总结？',
    '有没有旅行攻略推荐？',
    '求职简历怎么写？',
    '好用的生产力工具？',
  ]
  const items = loading ? [] : (suggestions.length > 0 ? suggestions : DEFAULT_SUGGESTIONS)

  return (
    <div className="flex flex-col items-center justify-center min-h-[60%] gap-8 animate-fade-in">
      <div className="text-center">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-xhs-red to-xhs-pink
                        flex items-center justify-center mx-auto shadow-lg mb-4">
          <Sparkles size={28} className="text-white" />
        </div>
        <h2 className="text-xl font-bold text-gray-800">你好，我是 KnoNote</h2>
        <p className="text-sm text-gray-500 mt-2 max-w-sm leading-relaxed">
          {mode === 'analysis'
            ? '我会读取你的小红书收藏笔记，用 AI 总结回答你的问题'
            : '搜索你的小红书收藏，找到最相关的笔记'}
        </p>
      </div>

      <div className="w-full max-w-md">
        <div className="flex items-center gap-2 mb-3">
          <Lightbulb size={13} className="text-xhs-pink" />
          <span className="text-xs text-gray-400 font-medium">
            {loading ? '正在生成建议...' : '试试这些问题'}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {loading ? (
            // 骨架屏
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-[52px] rounded-xl bg-gray-100 animate-pulse" />
            ))
          ) : (
            items.map(s => (
              <button
                key={s}
                onClick={() => onSuggest(s)}
                className="text-left text-sm px-4 py-3 rounded-xl bg-white border border-pink-100
                           hover:border-xhs-red/40 hover:shadow-card hover:text-xhs-red
                           transition-all duration-200 text-gray-600 leading-snug"
              >
                {s}
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
```

替换整个 `WelcomeScreen` 函数（约第 285-322 行）。

- [ ] **Step 5: 验证前端构建**

```bash
cd "E:/Project/Prj/xhs-qd/xhs-rag-assistant/frontend"
npx vite build --mode development 2>&1 | tail -5
```

预期：构建成功

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ChatArea.jsx
git commit -m "feat: replace static suggestions with dynamic API-driven questions"
```

---

## 自检结果

**Spec 覆盖检查：**

| 需求 | 对应 Task |
|------|-----------|
| 进程级 `_suggestions_cache` | Task 1 Step 1 |
| `GET /api/suggestions` 端点 | Task 1 Step 1 |
| 收藏 0 条 fallback 默认问题 | Task 1 Step 1 |
| LLM 生成 prompt（分类+标题） | Task 1 Step 1 |
| GLM-4.6 / thinking=off / temp=0.3 | Task 1 Step 1 |
| 缓存命中直接返回 | Task 1 Step 1 |
| 同步完成清缓存 | Task 1 Step 2 |
| 分类完成清缓存 | Task 1 Step 3 |
| ChatArea 动态获取 | Task 2 Steps 1-4 |
| 加载中骨架屏 | Task 2 Step 4 |
| 失败 fallback 默认问题 | Task 2 Step 4 |

**无 Placeholder**：所有步骤含完整代码。

**类型一致性**：`_suggestions_cache: dict[str, list[str]]`，`SuggestionsResponse.suggestions: list[str]`。
