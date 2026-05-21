# 爬虫模块 · crawler/

本模块负责从小红书网页端采集收藏夹数据：获取收藏列表、抓取笔记详情（文字 + 图片 + 视频）。

---

## 目录结构

```
crawler/
├── __init__.py          # 对外暴露公共函数 / XHSCrawler
├── cookies.py           # Chrome Cookie 读取、data/cookies.json 持久化、user_id 检测
├── get_my_cookies.py    # Cookie 导出工具（可选手动运行）
├── ingest.py            # 全量同步入口（爬取 + 入库一体）
├── models.py            # RawNote 数据模型
├── test_crawl.py        # 端到端爬虫测试
├── urls.py              # 小红书 URL 拼接工具
└── xhs_crawler.py       # Playwright 爬虫核心实现

# 根目录保留
sync_xhs.sh              # 推荐执行入口，调用 python -m crawler.ingest
```

---

## 快速上手

### 第一步：登录小红书

在 Chrome 中登录 [小红书网页版](https://www.xiaohongshu.com)。同步脚本会优先读取已有 `data/cookies.json`，没有时自动从 Chrome 提取 Cookie。

如需手动刷新 Cookie，可运行：

```bash
python -m crawler.get_my_cookies
```

它会自动读取 Chrome Cookie 并保存到 `data/cookies.json`。  
**Cookie 有效期约 30 天，过期后重新运行此步骤。**

### 第二步：同步收藏夹

```bash
./sync_xhs.sh
# 如果自动识别 user_id 失败，可显式指定：
# XHS_USER_ID=你的用户ID ./sync_xhs.sh
```

首次运行会打开 Chromium 浏览器窗口，自动翻页抓取收藏夹。同步过程会复用同一个浏览器会话抓取多篇笔记，避免每篇笔记都重新启动浏览器。根目录脚本会输出开始时间、结束时间、总耗时、发现收藏数、爬取成功数、新增/更新/跳过/失败数。  
**重复运行是安全的**：已入库的笔记自动跳过。

---

## 对外接口

> `crawler/__init__.py` 对外暴露以下三个函数，其他模块通过这里调用。

---

### `load_cookies(path) → dict`

从文件加载 Cookie。

**输入：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `path` | `str` | `cookies.json` 文件路径 |

**输出：** `dict`，键值对格式的 Cookie，例如：
```python
{"web_session": "xxx", "a1": "xxx", ...}
```

**示例：**
```python
from crawler import load_cookies
cookies = load_cookies("cookies.json")
```

---

### `fetch_collect_list(user_id, cookies) → list[dict]`

通过 Playwright 拦截收藏夹 API，获取所有收藏笔记的基本信息列表。

**工作原理：**  
打开 Chromium，导航到用户收藏页，监听 XHS 后台 API 响应（`/api/sns/web/v2/note/collect/page`），自动翻页直到无新数据。

**输入：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `user_id` | `str` | 小红书用户 ID |
| `cookies` | `dict` | `load_cookies()` 返回的 Cookie |

**输出：** `list[dict]`，每条字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `note_id` | `str` | 笔记唯一 ID |
| `xsec_token` | `str` | 安全 Token，访问详情页**必须携带** |
| `title` | `str` | 标题（可能为空） |
| `note_type` | `str` | `"normal"`（图文）或 `"video"` |
| `cover_url` | `str` | 封面图 URL |
| `likes` | `int` | 点赞数 |

**示例：**
```python
from crawler import fetch_collect_list, load_cookies

cookies = load_cookies("cookies.json")
note_metas = fetch_collect_list("640c4bcc000000002a0088a8", cookies)
# [
#   {
#     "note_id": "69ef1b91...",
#     "xsec_token": "ABxxx...",
#     "title": "字节后端一面复盘",
#     "note_type": "normal",
#     "cover_url": "https://...",
#     "likes": 1024,
#   },
#   ...
# ]
```

> **注意：** `xsec_token` 不可省略。访问笔记详情页时，URL 必须拼接 `?xsec_token={token}&xsec_source=pc_collect`，否则小红书返回"暂时无法浏览"。

---

### `fetch_note_detail(note_id, cookies, xsec_token="", xsec_source="pc_collect") → dict | None`

抓取单条笔记的完整内容（文字 + 图片理解 + 视频转录）。

**工作原理：**
1. Playwright 导航到笔记详情页（带 token）
2. 拦截 XHS 内容 API 获取结构化数据
3. 调用 `rag.vision` 对每张图片进行 OCR + 描述（GLM-4.6v）
4. 调用 `rag.transcriber` 对视频音频进行转录（Whisper-1，有视频时）
5. 拼装最终 `content` 字段：`正文\n[图片文字]: xxx\n[图片描述]: xxx\n[视频转录]: xxx`

**输入：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `note_id` | `str` | 必填 | 笔记 ID |
| `cookies` | `dict` | 必填 | 登录 Cookie |
| `xsec_token` | `str` | `""` | 来自 `fetch_collect_list` 的 token |
| `xsec_source` | `str` | `"pc_collect"` | 来源标识，保持默认值即可 |

**输出：** `dict` 或 `None`

成功时返回 `dict`，字段与 `RawNote.to_dict()` 一致：

| 字段 | 类型 | 说明 |
|------|------|------|
| `note_id` | `str` | 笔记 ID |
| `title` | `str` | 标题 |
| `content` | `str` | 完整正文（含图片文字/描述/视频转录） |
| `note_url` | `str` | 完整 URL（含 xsec_token） |
| `cover_url` | `str` | 封面图 URL |
| `tags` | `list[str]` | 标签 |
| `image_urls` | `list[str]` | 所有图片 URL |
| `likes` | `int` | 点赞数 |
| `note_type` | `str` | `"image"` 或 `"video"` |
| `crawled_at` | `str` | 爬取时间（ISO 格式） |

失败时返回 `None`（笔记不可访问、仅限 App 查看等情况）。

**示例：**
```python
from crawler import fetch_note_detail, load_cookies

cookies = load_cookies("cookies.json")
note = fetch_note_detail(
    note_id="69ef1b91...",
    cookies=cookies,
    xsec_token="ABxxx...",
)

if note:
    print(note["title"])
    print(note["content"][:200])
```

---

## content 字段格式规范

`content` 是 RAG 层向量化的核心字段，格式固定如下：

```
{笔记正文原文}

[图片文字]: {图片中识别到的文字，多张图片换行分隔}
[图片描述]: {图片内容描述}
[视频转录]: {视频语音转文字，仅视频笔记有}
```

**示例：**
```
今天去参加了字节后端一面，整整 65 分钟，全程八股文...

[图片文字]: Java内存模型 堆 栈 方法区 本地方法栈
[图片描述]: 一张展示Java JVM内存结构分区的示意图，包含堆内存、栈帧等模块
```

> RAG 层会将此字段整体向量化存入 ChromaDB，不做拆分。

---

## 数据流

```
小红书收藏夹页面
      │
      │ Playwright 拦截 API 响应
      ▼
fetch_collect_list()
  → [{ note_id, xsec_token, title, cover_url, ... }, ...]
      │
      │ 逐条
      ▼
fetch_note_detail(note_id, cookies, xsec_token)
  → { note_id, title, content, note_url, cover_url, ... }
      │
      │ 调用 rag.indexer
      ▼
index_note(note, user_id)
  → SQLite + ChromaDB
```

---

## 注意事项

1. **登录态**：Playwright 使用 `~/.xhs_playwright_profile` 持久化 profile，Cookie 注入后会自动保持登录状态。

2. **反爬限制**：每条笔记详情抓取有随机延迟（1~3 秒），不要去掉，否则容易触发风控。

3. **xsec_token 时效**：从收藏列表获取的 token 有效期未知，建议在获取列表后尽快处理详情，避免长时间搁置。

4. **图片处理时间**：每张图片调用一次 GLM-4.6v，图片多的笔记（10+ 张）可能需要 1~2 分钟。

5. **无头模式**：爬虫默认非无头（可见浏览器窗口），方便调试。生产环境可在 `xhs_crawler.py` 中开启 `headless=True`。

---

## 依赖

```bash
pip install playwright requests browser-cookie3
playwright install chromium
```
