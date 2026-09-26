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
Bear Vault/42_Reader_Library/YYYY/MM/YYYY-MM-DD-{slug}.md
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

## Phase 2: Notion Book Library Sync ✅ 完成于 2026-03-01

**目标**：将 Notion Book Library 数据库（265 本书，2019–2026）一次性搬运到 Obsidian Bear Vault `Books/` 目录。

### 数据概览

- **总量**：265 本书（253 Read / 5 Reading / 4 To Read / 3 None）
- **有中文笔记**：100 本；**有英文笔记**：34 本
- **年份跨度**：2019–2026

### 输出格式

```
Bear Vault/40_Books/YYYY/YYYY-{slug}.md
Bear Vault/_assets/books/{slug}.jpg
```

Frontmatter 包含：title / author / finished / category / format / status / rating / goodreads / notion_id / cover

页面内容按 heading_3 分节：Why I Read This / Summary / My Notes / Highlights / Reference/Links

### 实现要点

- `sync_books.py`：独立脚本，不依赖 Claude（纯搬运）
- Notion API 直接调用（不用 notion-client SDK，用 requests 保持一致）
- 封面图：external URL 直接 GET，Notion S3 URL 同样直接 GET，失败跳过不中断
- `books_tracker.json` 去重 + 支持断点续跑
- `--dry-run` / `--reset` CLI 模式与 Phase 1 一致

### 设计决策

- 多年份取第一个；无年份放 `Unknown/` 目录
- 图片块输出 `[图片]` 占位符，书签块输出 URL 或 `[caption](url)`
- `has_children` 递归一层（处理 callout / toggle 里的段落）

---

## Phase 3: Reading Summary 双层优化 + Content Vault 同步 ✅ 完成于 2026-03-23

**目标**：将单一的 Reading Summary 拆为两层（私人笔记 + 社交分享帖），并自动同步 Sharing Post 到 Content Vault 供发布。

### 设计决策

**双层 Summary**：
- **Reading Note**：150-200 字中文，三段式（讲了什么 → 高亮要点 → 对我的启发），信息密度高，方便快速扫描
- **Sharing Post**：200-300 字中文社交帖，标题 → 图片 → 正文 → 原文链接，面向读者有用的角度

**Sharing Post 风格**（参考 Notion Web snippets 2.0 旧帖子）：
- 洞察/反直觉观点切入，不用"我最近读了"
- 自然带入作者/原文背景
- 70% 内容价值 + 30% Bear 个人实践
- 自然收束，无 CTA 无鸡汤

**图片策略**：
- `cover_image_url` 非 Readwise 占位图 → 直接引用 URL
- 占位图 + 有效 source_url → Playwright 截图保存到 `_assets/reader/`
- mailto 来源 → 跳过

**Content Vault 同步**：
- 输出路径：`Bear Content Vault/Social Posts/drafts/reader/YYYY-MM-DD-{slug}.md`
- Frontmatter 兼容 content-publisher：`publish_twitter_cn`, `publish_linkedin_en`, `ready: false`, `status: draft`, `source: reader`
- 正常 sync 流程自动创建 draft，用户在 Obsidian 审阅后改 `ready: true` 即可发布

### 实现要点

- 两个独立 prompt（`READING_NOTE_PROMPT` + `SHARING_POST_PROMPT`），共享文章上下文
- `_call_claude()` 通用调用函数，`generate_reading_note()` / `generate_sharing_post()` 各自调用
- `write_sharing_draft()` 从 Sharing Post 提取内容写入 Content Vault draft
- `--test N` CLI 参数预览模式（stdout，不写文件）
- 2026 年 8 篇已全部重新生成并同步

---

## Phase 4: 从「出帖子」改为「攒素材」 🚧 2026-09-26 起

**起因**（Bear，2026-09-26）：Reader 同步出来的 Sharing Post 本质是转述别人的内容，
太机械，不像他写的。有价值的原创是「一个普遍适用的 insight + 他自己的具体案例」，
这需要攒几篇、由他来接上自己的经历，不能一篇一篇自动出成稿。

### 4a. 停掉 Sharing Post ✅ 2026-09-26

- `sync_reader.py` 不再生成 Sharing Post，不再写 Content Vault `drafts/reader/`，不再截图
- 42_Reader_Library 里的笔记只剩：Highlights / My Notes / Reading Note
- Phase 3 的 `SHARING_POST_PROMPT` / `write_sharing_draft()` / Playwright 截图已删，要找回看 git 历史（855266e）
- 2026-09-26 同步出的 8 份 `drafts/reader/` 草稿删了 7 份；Motion Design 那份已是 `ready: true`，留着等 Bear 定

### 4b. 每周阅读素材 summary（接进周复盘） ⬜ 下次开工

**设想**：Bear 做周复盘时，把这周读过的文章快速 summary 给他看，作为下一次写作的素材。

待定（开工前先跟 Bear 确认，别自己拍板）：
- **挂在哪**：`/goals` 周复盘里加一段，还是独立一个 `sync_reader.py --weekly` 由周复盘调用
- **「这周」怎么算**：按高亮日期（`_latest_highlight_date`）还是按同步日期
- **素材卡长什么样**：倾向是每条提炼一个通用 insight + 留一个「我的案例：____」空位给 Bear 填，
  不写成稿。Bear 还在想，格式等他定
- **合写**：同主题几篇归到一起（例：2026-09-25 的四篇 LinkedIn 获客 → 一条 insight + FDP 的真实经历）。
  是 summary 里就按主题分组，还是只列出来让 Bear 自己挑
- **落在哪**：周复盘里直接展示即可，还是另存一份素材清单进 Obsidian（和 content-planner
  `32_Content_Ideas/` 选题池是什么关系，别跟它抢活）
- 只收有 `document_note` 的文章，还是纯高亮的也算进来

---

## 长期方向（待讨论）

- 定时自动同步（launchd plist，每天早上跑一次）
- 支持 `--since YYYY-MM-DD` 指定时间段重跑
- 为"只有高亮，无笔记"的 795 篇单独处理（轻量归档，不调 Claude）
- Obsidian Dataview 查询模板（按分类、日期浏览 Reader Library）
- 批量重新生成 2024-2025 年旧文章的 Reading Note
