# Reader Library

Readwise Reader → Obsidian 自动同步脚本。

将 Readwise Reader 中带有个人笔记（Document Note）的文章，自动同步到 Obsidian Bear Vault，并用 Claude 生成第一人称阅读摘要。

---

## 快速开始

```bash
# 安装依赖
pip3 install -r requirements.txt

# 配置 .env（复制下方模板）
cp .env.example .env  # 或直接编辑 .env

# 测试连接，不写文件，不调 Claude
python3 sync_reader.py --dry-run

# 正式同步
python3 sync_reader.py
```

---

## 环境变量

`.env` 文件（不提交到 git）：

```
READWISE_TOKEN=your_token_here      # readwise.io/access_token
ANTHROPIC_API_KEY=your_key_here     # console.anthropic.com
```

---

## 用法

| 命令 | 说明 |
|---|---|
| `python3 sync_reader.py` | 增量同步新内容 |
| `python3 sync_reader.py --dry-run` | 预览会写哪些文件，不实际写入 |
| `python3 sync_reader.py --reset` | 清空 tracker，全量重跑 |

---

## 输出格式

文件写入路径：
```
Bear Vault/42_Reader_Library/YYYY/MM/YYYY-MM-DD-{slug}.md
```

每篇文章包含：
- **Frontmatter**：标题、作者、日期、来源链接、分类、Readwise ID
- **Highlights**：划线原文（blockquote 格式），含行内批注
- **My Notes**：Readwise Notebook 面板里写的个人感悟
- **Reading Summary**：Claude Haiku 生成的 ~200 字第一人称阅读体会

---

## 过滤规则

- 分类：`articles` / `tweets` / `videos` / `podcasts`（排除 books）
- 必须有 `document_note`（即 Notebook 面板写了笔记）

---

## 文件结构

```
reader-library/
├── sync_reader.py      # 主脚本
├── requirements.txt    # 依赖
├── sync_tracker.json   # 同步状态（自动生成，不提交）
├── .env                # 密钥（不提交）
├── README.md           # 本文件
└── docs/
    └── roadmap.md      # 项目路线图
```

---

## Changelog

### Phase 1 — 2026-03-01

- 实现 Readwise v2 Export API 全量 + 增量拉取
- 过滤出有 Document Note 的文章（83 篇首次同步）
- 用 `python-slugify` 生成文件名，按 `YYYY/MM/` 分目录
- Claude Haiku 生成第一人称中文摘要
- `sync_tracker.json` 去重，支持中断续跑
- `--dry-run` / `--reset` 模式
- 输出路径从 Bear Content Vault 迁移至 Bear Vault
