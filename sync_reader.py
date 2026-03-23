#!/usr/bin/env python3
"""
Readwise Reader -> Obsidian sync script.

Usage:
    python3 sync_reader.py           # full sync
    python3 sync_reader.py --dry-run # print would-write paths, skip Claude & file writes
    python3 sync_reader.py --reset   # clear tracker and re-sync everything

Env vars required:
    READWISE_TOKEN    - from readwise.io/access_token
    ANTHROPIC_API_KEY - Claude API key
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv
from slugify import slugify

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

READWISE_TOKEN = os.environ.get("READWISE_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

VAULT_ROOT = Path(
    "/Users/bearliu/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bear Vault"
)
LIBRARY_DIR = VAULT_ROOT / "Reader Library"
TRACKER_PATH = Path(__file__).parent / "sync_tracker.json"

READWISE_EXPORT_URL = "https://readwise.io/api/v2/export/"
VALID_CATEGORIES = {"articles", "tweets", "videos", "podcasts"}

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
SUMMARY_PROMPT_TEMPLATE = """\
你正在帮我（Bear Liu，内容创作者，现居新西兰）写一段阅读体会。

## 我的文章信息

标题：{title}
作者：{author}
分类：{category}
来源链接：{source_url}

我划出的高亮：
{highlights_text}

我的个人笔记：
{doc_note}

## 写作要求

用第一人称中文写一段 200 字左右的阅读体会。融合高亮和笔记，写出我读完的真实感受和收获。

## Bear 的语言风格（必须严格遵守）

**核心声音**：像坐在对面喝咖啡的朋友在讲他刚做了什么。真诚、自嘲、跨文化视角。

**节奏**：长短句交替，口语化，像脱口秀节奏。长句铺垫，短句收。允许用"简直""其实""坦白说""对我来说"这类口头禅。

**内容比例**：70% 个人感受叙事 + 30% 提炼洞察。洞察像"啊哈时刻"，不是严谨论证。

**情绪回收**：写完严肃或感慨的段落后，用自嘲或幽默拉回来。

**绝对禁止**：
- 破折号（Em Dashes）
- "不是……而是"句型
- 鸡汤句："相信自己""勇敢追梦"
- 营销词："颠覆""赋能""打造""闭环"
- "总结一下" + 要点回顾
- "这篇文章讲了……"这种开头
- 列表、表格

**直接进入内容**，不要重复我的笔记原文。"""


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

def load_tracker() -> dict:
    if TRACKER_PATH.exists():
        with open(TRACKER_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"synced": {}, "last_sync": None}


def save_tracker(tracker: dict) -> None:
    with open(TRACKER_PATH, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


def mark_synced(tracker: dict, book_id: str, dest: str) -> None:
    tracker["synced"][str(book_id)] = {
        "dest": dest,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Readwise API
# ---------------------------------------------------------------------------

def fetch_articles(last_sync: str | None) -> list[dict]:
    if not READWISE_TOKEN:
        print("ERROR: READWISE_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Token {READWISE_TOKEN}"}
    params: dict = {}
    if last_sync:
        params["updatedAfter"] = last_sync

    articles: list[dict] = []
    next_cursor = None
    page = 0

    while True:
        page += 1
        if next_cursor:
            params["pageCursor"] = next_cursor
        else:
            params.pop("pageCursor", None)

        print(f"  Fetching page {page}…", end=" ", flush=True)
        resp = _get_with_retry(READWISE_EXPORT_URL, headers=headers, params=params)
        data = resp.json()

        results = data.get("results", [])
        print(f"{len(results)} items")

        for book in results:
            cat = book.get("category", "")
            doc_note = (book.get("document_note") or "").strip()
            if cat not in VALID_CATEGORIES:
                continue
            if not doc_note:
                continue
            articles.append(book)

        next_cursor = data.get("nextPageCursor")
        if not next_cursor:
            break

    return articles


def _get_with_retry(url: str, *, headers: dict, params: dict, max_retries: int = 3) -> requests.Response:
    for attempt in range(1, max_retries + 1):
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"\n  Rate limited. Waiting {wait}s… (attempt {attempt}/{max_retries})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


# ---------------------------------------------------------------------------
# Content building
# ---------------------------------------------------------------------------

def build_highlights_text(highlights: list[dict]) -> str:
    """Return formatted blockquote string for all highlights."""
    lines: list[str] = []
    for hl in highlights:
        text = (hl.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"> {text}")
        note = (hl.get("note") or "").strip()
        if note:
            lines.append(f">\n> *[批注：{note}]*")
        lines.append("")
    return "\n".join(lines).strip()


def generate_summary(book: dict, highlights_text: str, doc_note: str, dry_run: bool) -> str:
    if dry_run:
        return "[dry-run — 摘要未生成]"

    if not ANTHROPIC_API_KEY:
        return "[生成失败：ANTHROPIC_API_KEY 未设置，请手动补充]"

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        title=book.get("title") or "（无标题）",
        author=book.get("author") or "（未知作者）",
        category=book.get("category", ""),
        source_url=book.get("source_url") or book.get("readwise_url") or "",
        highlights_text=highlights_text or "（无高亮）",
        doc_note=doc_note or "（无笔记）",
    )

    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        print(f"\n  WARNING: Claude API error: {exc}")
        return "[生成失败，请手动补充]"


def build_note_content(book: dict, summary: str) -> str:
    title = book.get("title") or "（无标题）"
    author = book.get("author") or ""
    source_url = book.get("source_url") or book.get("readwise_url") or ""
    category = book.get("category", "")
    book_id = str(book.get("user_book_id", ""))
    doc_note = (book.get("document_note") or "").strip()

    # Date from most recent highlight's highlighted_at, or today
    date_str = _latest_highlight_date(book.get("highlights") or [])

    highlights = book.get("highlights", [])
    highlights_text = build_highlights_text(highlights)

    frontmatter = f"""---
title: "{title.replace('"', "'")}"
author: "{author.replace('"', "'")}"
date: {date_str}
source: {source_url}
category: {category}
readwise_id: {book_id}
---"""

    sections = [frontmatter, ""]

    if highlights_text:
        sections += ["## Highlights", "", highlights_text, ""]

    if doc_note:
        sections += ["## My Notes", "", doc_note, ""]

    sections += ["## Reading Summary", "", summary, ""]

    return "\n".join(sections)


def _parse_date(dt_str: str | None) -> str:
    if not dt_str:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


def _latest_highlight_date(highlights: list[dict]) -> str:
    """Return YYYY-MM-DD of the most recent highlight, or today if none."""
    dates = []
    for hl in highlights:
        ts = hl.get("highlighted_at") or hl.get("created_at")
        if ts:
            dates.append(ts)
    if dates:
        return _parse_date(max(dates))
    return datetime.now().strftime("%Y-%m-%d")


def _build_dest_path(book: dict) -> tuple[str, Path]:
    """Return (relative_str, absolute_path) for the output file."""
    date_str = _latest_highlight_date(book.get("highlights") or [])
    year, month = date_str[:4], date_str[5:7]

    title = book.get("title") or f"untitled-{book.get('user_book_id', 'unknown')}"
    raw_slug = slugify(title, max_length=60, word_boundary=True)
    filename = f"{date_str}-{raw_slug}.md"

    rel = f"Reader Library/{year}/{month}/{filename}"
    abs_path = LIBRARY_DIR / year / month / filename
    return rel, abs_path


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_to_vault(abs_path: Path, content: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Readwise Reader to Obsidian.")
    parser.add_argument("--dry-run", action="store_true", help="Print would-write paths; skip file writes and Claude calls.")
    parser.add_argument("--reset", action="store_true", help="Clear tracker and re-sync everything.")
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    if dry_run:
        print("=== DRY RUN mode — no files written, no Claude calls ===\n")

    tracker = load_tracker()

    if args.reset:
        print("Resetting tracker…")
        tracker = {"synced": {}, "last_sync": None}
        save_tracker(tracker)

    last_sync = tracker.get("last_sync")
    print(f"Last sync: {last_sync or 'never (full fetch)'}")
    print("Fetching articles from Readwise…")

    articles = fetch_articles(last_sync)
    print(f"\nFetched {len(articles)} eligible articles total.\n")

    already_synced = 0
    new_count = 0
    error_count = 0

    for book in articles:
        book_id = str(book.get("user_book_id", ""))
        title = book.get("title") or f"(id:{book_id})"

        if book_id in tracker["synced"]:
            already_synced += 1
            continue

        rel_path, abs_path = _build_dest_path(book)

        if dry_run:
            print(f"[would-write] {rel_path}")
            print(f"             Title: {title}")
            print(f"             Highlights: {len(book.get('highlights') or [])}, Note: {'yes' if book.get('document_note') else 'no'}")
            print()
            new_count += 1
            continue

        print(f"Syncing: {title[:70]}")

        doc_note = (book.get("document_note") or "").strip()
        highlights_text = build_highlights_text(book.get("highlights", []))
        summary = generate_summary(book, highlights_text, doc_note, dry_run=False)

        content = build_note_content(book, summary)

        try:
            write_to_vault(abs_path, content)
        except Exception as exc:
            print(f"  ERROR writing {abs_path}: {exc}")
            error_count += 1
            continue

        mark_synced(tracker, book_id, rel_path)
        save_tracker(tracker)
        new_count += 1
        print(f"  -> {rel_path}")

    # Update last_sync timestamp
    if not dry_run:
        tracker["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_tracker(tracker)

    print(f"\nDone. Already synced: {already_synced}, New: {new_count}, Errors: {error_count}")


if __name__ == "__main__":
    main()
