# 前端模块 · frontend/

**负责人：曾君毅**

基于 React 18 + Vite + Tailwind CSS 构建的 Web 界面，实现收藏夹浏览与智能问答。

---

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| React | 18 | UI 框架 |
| Vite | 5 | 构建工具，含开发代理 |
| Tailwind CSS | 3 | 样式 |
| lucide-react | - | 图标库 |

---

## 快速启动

```bash
# 在 frontend/ 目录下
npm install
npm run dev
# 浏览器访问 http://localhost:5173
```

> **前提：后端必须在 `http://localhost:8000` 上运行。**  
> Vite 已配置代理（`/api` → `localhost:8000`），前端无需跨域配置。

---

## 目录结构

```
frontend/
├── src/
│   ├── App.jsx                  # 根组件：布局编排、状态管理
│   ├── main.jsx                 # React 挂载入口
│   ├── index.css                # Tailwind 基础样式 + 自定义组件类
│   ├── hooks/
│   │   └── useApi.js            # API 请求封装（useNotes, queryApi）
│   └── components/
│       ├── Sidebar.jsx          # 左侧栏：收藏列表、过滤、统计
│       ├── ChatArea.jsx         # 主区域：对话输入、消息历史、欢迎页
│       ├── MessageBubble.jsx    # 单条消息气泡（用户 / AI 回答 + 来源卡片）
│       ├── NoteCard.jsx         # 笔记卡片（侧边栏紧凑版 + 搜索结果完整版）
│       ├── ModeToggle.jsx       # 搜索 / 分析模式切换按钮组
│       ├── SettingsModal.jsx    # 设置弹窗（修改用户 ID）
│       └── TypingDots.jsx       # 加载中动画（三点跳动）
├── public/
│   └── favicon.svg
├── index.html
├── package.json
├── vite.config.js               # 代理配置（/api → :8000）
├── tailwind.config.js           # 主题色、动画扩展
└── postcss.config.js
```

---

## API 接口调用规范

前端通过 `src/hooks/useApi.js` 与后端通信，所有请求路径以 `/api` 开头（经 Vite 代理转发）。

---

### GET `/api/stats`

获取存储统计信息，用于侧边栏展示"共 N 条收藏"。

**请求：** 无参数

**响应：**
```json
{
  "sqlite_total": 120,
  "chroma_indexed": 115
}
```

---

### GET `/api/notes`

获取用户的收藏笔记列表，用于侧边栏展示。

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | `string` | 是 | 用户 ID |
| `page` | `int` | 否 | 页码，默认 1 |
| `page_size` | `int` | 否 | 每页条数，默认 20，最大 100 |

**响应：**
```json
{
  "total": 120,
  "page": 1,
  "page_size": 20,
  "notes": [
    {
      "note_id": "69ef1b910000000035024d5e",
      "title": "字节后端一面复盘",
      "tags": ["面经", "字节跳动", "后端"],
      "cover_url": "https://sns-img-hw.xhscdn.com/...",
      "note_url": "https://www.xiaohongshu.com/explore/...",
      "likes": 1024,
      "note_type": "image",
      "crawled_at": "2024-01-15T10:30:00",
      "indexed": 1
    }
  ]
}
```

---

### POST `/api/query`

核心查询接口：语义检索 + 可选 AI 生成。

**请求体（JSON）：**

```json
{
  "query": "MySQL 联合索引有哪些注意事项",
  "user_id": "640c4bcc000000002a0088a8",
  "mode": "search",
  "top_k": 6
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | `string` | 是 | 用户问题，非空字符串 |
| `user_id` | `string` | 是 | 用户 ID，非空字符串 |
| `mode` | `string` | 否 | `"search"`（默认）或 `"analysis"` |
| `top_k` | `int` | 否 | 返回条数，1~20，默认 6 |

**两种模式说明：**

| mode | 行为 | `answer` 字段 | 等待时间 |
|------|------|--------------|---------|
| `search` | 语义检索，直接返回相关帖子 | `null` | < 1s |
| `analysis` | 检索 + GLM-5.1 综合回答 | 非空字符串 | 3~15s |

**成功响应（200）：**

```json
{
  "mode": "search",
  "answer": null,
  "sources": [
    {
      "note_id": "69ef1b910000000035024d5e",
      "title": "字节后端一面复盘",
      "note_url": "https://www.xiaohongshu.com/explore/69ef1b91...?xsec_token=...",
      "cover_url": "https://sns-img-hw.xhscdn.com/...",
      "distance": 0.31
    }
  ],
  "total": 2
}
```

`analysis` 模式时 `answer` 为 AI 生成的 Markdown 文本：

```json
{
  "mode": "analysis",
  "answer": "## MySQL 联合索引注意事项\n\n根据你收藏的面经...\n\n### 1. 最左前缀原则\n...",
  "sources": [...],
  "total": 2
}
```

**错误响应：**

| 状态码 | 场景 |
|--------|------|
| `422` | query 或 user_id 为空 |
| `503` | AI API Key 未配置 |
| `500` | 检索或生成内部错误 |

---

## 用户 ID 配置

用户 ID 存储在 `localStorage`（key: `xhs_user_id`），默认值为 `640c4bcc000000002a0088a8`。

用户可点击右上角 ⚙️ 图标打开设置弹窗修改。

**查找自己的用户 ID：**
登录小红书网页版，个人主页 URL 为：`https://www.xiaohongshu.com/user/profile/{user_id}`

---

## 状态管理

前端采用 React 本地状态（无 Redux/Zustand），数据流如下：

```
App.jsx
  ├── userId (localStorage 持久化)
  ├── useNotes(userId)  →  Sidebar
  │     ├── notes[]
  │     ├── stats
  │     └── loading
  └── ChatArea
        ├── messages[]    (本地，刷新后清空)
        ├── mode          ("search" | "analysis")
        └── loading
```

---

## Markdown 渲染说明

`MessageBubble.jsx` 中的 `AnswerContent` 组件支持简单 Markdown：

| 语法 | 渲染效果 |
|------|---------|
| `## 标题` | 粗体大字 |
| `### 标题` | 粗体中字 |
| `- 列表项` | 红色圆点列表 |
| `1. 有序列表` | 数字标注列表 |
| `**粗体**` | 加粗 |

如需更完整的 Markdown 支持，可引入 `react-markdown` 库替换 `AnswerContent`。

---

## 样式约定

### 自定义颜色（tailwind.config.js）

| 变量 | 色值 | 用途 |
|------|------|------|
| `xhs-red` | `#FF2442` | 主色、按钮、高亮 |
| `xhs-pink` | `#FF6B9D` | 辅助、图标 |
| `xhs-rose` | `#FFE4EA` | 浅色背景、标签底色 |
| `xhs-light` | `#FFF5F7` | 页面背景 |
| `xhs-dark` | `#CC1C36` | 按钮 hover 态 |

### 常用组件类（index.css）

```css
.btn-primary   /* 主要按钮：红色渐变 */
.card          /* 卡片容器：白色圆角 + 阴影 */
.input-base    /* 输入框基础样式 */
.tag           /* 标签胶囊 */
```

---

## 开发建议

1. **新增页面**：在 `src/` 下创建新的页面组件，在 `App.jsx` 中通过状态切换显示（当前无路由，后续可引入 `react-router-dom`）

2. **新增 API 调用**：统一在 `src/hooks/useApi.js` 中添加，保持组件的纯粹性

3. **修改主题色**：在 `tailwind.config.js` 的 `theme.extend.colors.xhs` 中统一修改

4. **动画**：新增动画在 `tailwind.config.js` 的 `keyframes` 中定义，然后在 `animation` 中注册

---

## 构建部署

```bash
npm run build
# 产物在 frontend/dist/
# 可由后端 FastAPI 以 StaticFiles 挂载，或单独部署到 CDN
```
