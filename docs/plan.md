# Reader Library — Plan

## 上次完成（2026-10-12）

- **新增 `sync_goodreads.py`（book-note Pipeline F）**：Bear 定分数统一在 Goodreads 打，rating / finished / started / 新条目封面以 Goodreads 为准，笔记摘记仍在 Obsidian
  - 数据源：书架公开 RSS（`read` + `currently-reading`），书评页和书架网页要登录，抓不到「开始读」日期，改用放进 Currently Reading 的日期
  - 首跑：20 本分数、77 本历史读完日期改成 Goodreads 的（Bear 同意历史日期也以 Goodreads 为准）；鳄鱼君补封面；Hit Refresh 补链接；两遍幂等
  - Dune 与 9 本早年的书对不上，留着没动（见 book-note SKILL.md Pipeline F「已知的坑」）
- Readwise 同步：上传 8 本；另 8 本老书 Readwise 里已有部分摘记（2021-2024 导入），Bear 没要求补，没强制补传
- 删了两个误建条目（Everything Is Illuminated、The Designer of Books），是日记书架同步把读书笔记里提到的书名当成了在读的书

## 上次完成（2026-09-26）

- 同步 8 篇新文章（2026-08-27 以来）
- **Phase 4a**：停掉 Sharing Post 与 Content Vault 草稿，理由与后续见 `roadmap.md` Phase 4
- 当天同步出的 8 份 `drafts/reader/` 草稿删了 7 份；Motion Design 那份（`2026-09-08-the-best-motion-design-animation-of-2026.md`）是 `ready: true`，已被标为待发，留着等 Bear 定

## 上次完成（2026-08-08）

- 文档路径对齐代码实况：`README.md` / `docs/roadmap.md` / `sync_books.py` docstring 里的
  `Bear Vault/Reader Library/` → `42_Reader_Library/`、`Bear Vault/Books/` → `40_Books/`
  （代码在 c5b0625「迁移 Books → 40_Books」时已改，文档漏了）
- 未提交的 `sync_reader.py` 路径迁移（`Reader Library` → `42_Reader_Library`）一并入仓
- 实测 `add_book.py "Everyday Matters" --author "Danny Gregory"`：Google Books 429 限流 →
  豆瓣 fallback 命中**同名的另一本书**，抓回错误封面（一张洗手池照片）。
  最后手工换成 Open Library 按 ISBN 取的正确封面

## 上次完成（2026-04-06）

- `add_book.py` 增加豆瓣封面 fallback：Google Books 失败后改查 `book.douban.com/j/subject_suggest`
- 封面 URL 从 `/subject/s/` 升级到 `/subject/l/` 大图
- `download_cover()` 支持 Referer header（豆瓣图床防盗链必需）
- 豆瓣 suggest API 响应不稳（有时 60s 超时），所以加了 3 次 retry，timeout 30s
- 实测 `add_book.py "星震"` 时 Google 返回 429 限流，豆瓣 fallback 正常拉到封面
- Caveat: 豆瓣不返回 category，豆瓣命中时 `category` 留空需手动填

## 上次完成（2026-03-23）

- Reading Summary 拆为双层：Reading Note（私人笔记）+ Sharing Post（社交分享帖）
- Sharing Post 格式：标题 → 图片 → 正文 → 原文链接
- 图片策略：有真实 cover 引用 URL，无 cover 用 Playwright 截图，mailto 跳过
- Content Vault 同步：`sync_reader.py` 自动创建 `drafts/reader/` draft 文件，兼容 content-publisher
- 2026 年 8 篇文章全部重新生成并同步到两个 Vault
- `--test N` 预览模式可用

## 下次待办

- [ ] 日记书架同步（voice-daily-note 的 bookshelf）会把读书笔记里**提到的**书名也建成「在读」条目，10-12 误建过两条，Bear 没定修不修
- [ ] Goodreads 同步要不要并进定时任务（目前随 book-note 手动跑）

- [ ] `add_book.py` 加 Open Library 按 ISBN 取封面的 fallback（比豆瓣同名匹配可靠），
      并在豆瓣命中时把书名/作者打印出来提示人工核对
- [ ] **Phase 4b 每周阅读素材 summary**：接进周复盘，开工前先把 roadmap 里的待定项跟 Bear 过一遍
- [ ] 考虑批量重新生成 2024-2025 年旧文章（约 80 篇，需确认是否值得 API 开销）
- [ ] 定时自动同步（launchd plist）

## 遗留问题

- `sync_goodreads.py` 只改 frontmatter、不搬文件：读完日期跨年改动后，条目所在年份文件夹可能和 `finished` 年份对不上（10-12 有 1 本）
- Readwise 的老书（The Design of Everyday Things、月亮与六便士等 8 本）Readwise 里摘记比 Obsidian 少，要补可 `--book "书名"` 单本强制传

- 豆瓣 suggest 只按书名模糊匹配，`--author` 不参与筛选，英文书容易命中同名的另一本书。
  豆瓣 fallback 拿回的封面每次都要人眼核一下
- mailto 来源的文章无法截图也无法提供有效的原文链接
