# 动态建议问题设计文档

**日期**：2026-06-05
**负责人**：曾君毅
**状态**：已确认，待实现

---

## 背景

首页 WelcomeScreen 的「试试这些问题」目前是写死的 4 个通用问题（ChatArea.jsx:6-11），与用户实际收藏内容无关，引导效果弱。

## 目标

根据用户收藏内容动态生成个性化引导问题，提高用户首次提问的质量和参与度。

## 设计

### 缓存策略

进程级缓存 + 同步后失效，与现有 `_sync_state`/`_classify_state` 模式一致。

```
首次请求 → LLM 生成 → 缓存 → 返回
再次请求 → 缓存命中 → 返回
同步完成 → 清除缓存 → 下次重新生成
```

### 数据流

```
GET /api/suggestions?user_id=xxx
  │
  ├─ 收藏数 = 0 → 返回硬编码默认问题（不调 LLM）
  │
  └─ 收藏数 > 0
       ├─ 缓存命中 → 直接返回
       └─ 缓存未命中
            │
            ├─ 取分类列表（get_categories）+ 取笔记标题（all_notes limit 10）
            ├─ 拼 prompt → GLM-4.6（thinking=off, temperature=0.3）
            ├─ 返回 4 个个性化问题
            └─ 写入缓存
```

### Prompt 设计

```
System: 你是一个对话引导助手。根据用户的收藏内容，
        生成 4 个用户可能感兴趣的问题。
        只返回问题列表，每行一个问题，以 "- " 开头。
        问题应该覆盖不同分类，引导用户深入探索自己的收藏。

        规则：
        - 每个问题 10-20 字
        - 尽量覆盖不同分类方向
        - 用中文口语风格
        - 如果收藏太少（<3 条），少生成些问题

User:   该用户收藏了 {n} 篇笔记
        分类分布：{categories}
        部分笔记标题：{titles}
```

模型：`glm-4.6 | temperature=0.3 | max_tokens=200 | thinking=disabled`

### 缓存 key

`user_id`（字符串），不同用户独立缓存。

### 失效时机

同步完成时清除当前用户的缓存。`_run_ingest` 和 `start_classify` 成功后调用清除。

## 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `main.py` | 新增 `_suggestions_cache`、`GET /api/suggestions`、同步完成后清缓存 |
| 修改 | `frontend/src/components/ChatArea.jsx` | 硬编码 `SUGGESTIONS` → `useState` + `useEffect` |

## 不在本次范围内

- 持久化存储（数据库）
- 用户手动刷新建议
- A/B 测试
