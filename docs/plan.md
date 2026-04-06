# Reader Library — Plan

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

- [ ] 考虑批量重新生成 2024-2025 年旧文章（约 80 篇，需确认是否值得 API 开销）
- [ ] Sharing Post 英文版支持
- [ ] 定时自动同步（launchd plist）
- [ ] 测试 Playwright 截图在实际无 cover 文章上的效果

## 遗留问题

- 2 篇旧文件（`2026-03-01` 和中文文件名的 `2026-03-03`）是早期格式，没有 Sharing Post section，未同步到 Content Vault
- mailto 来源的文章无法截图也无法提供有效的原文链接
