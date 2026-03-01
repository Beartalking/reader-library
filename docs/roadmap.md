# Reader Library — Roadmap

---

## Phase 1: Readwise Reader Sync ✅ 完成于 2026-03-01

**目标**：将 Readwise Reader 中带高亮或笔记的阅读记录，自动同步到 Obsidian Bear Vault，并用 Claude 生成第一人称阅读摘要。

### 设计决策

**API 选型**：Readwise v2 Export API（`/api/v2/export/`）
- 一次调用返回文章 + 所有高亮（嵌套结构）
- `document_note` = Notebook 面板里的个人感悟
- 支持 `updatedAfter` 增量拉取
- 认证：`Authorization: Token <READWISE_TOKEN>`

**过滤规则**（最终版）：
- 分类限定：`articles` / `tweets` / `videos` / `podcasts`
- 必须有 `document_note`（83 篇，排除 795 篇纯高亮条目）

**输出路径**：
```
Bear Vault/Reader Library/YYYY/MM/YYYY-MM-DD-{slug}.md
```

**Claude 模型**：`claude-haiku-4-5-20251001`（快速、低成本，生成 ~200 字摘要）

### 实现的功能

- Readwise v2 Export API 分页拉取
- `sync_tracker.json` 去重 + 增量同步
- 高亮格式化为 `> blockquote`，行内批注附在引用后
- Claude Haiku 生成第一人称中文摘要，失败时写占位符不中断
- `--dry-run`（预览）/ `--reset`（清空重跑）CLI 模式
- API 429 限流自动重试（最多 3 次）

### 踩坑记录

- Readwise v2 Export API 的 book ID 字段是 `user_book_id`，不是 `id`
- 没有 `num_highlights` 和 `last_highlight_at` 字段，需要从 `highlights` 列表推算
- Python 3.9 不支持 `str | None` 语法，需要 `from __future__ import annotations`
- `python-dotenv` 的 `load_dotenv()` 在 heredoc 脚本中会报 `AssertionError`，需用文件脚本调用

---

## Phase 2: 待规划

> Bear 提到有类似的阅读收集任务，作为 Phase 2 展开。

---

## 长期方向（待讨论）

- 定时自动同步（launchd plist，每天早上跑一次）
- 支持 `--since YYYY-MM-DD` 指定时间段重跑
- 为"只有高亮，无笔记"的 795 篇单独处理（轻量归档，不调 Claude）
- Obsidian Dataview 查询模板（按分类、日期浏览 Reader Library）
