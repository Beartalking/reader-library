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
from typing import Optional

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
LIBRARY_DIR = VAULT_ROOT / "42_Reader_Library"
TRACKER_PATH = Path(__file__).parent / "sync_tracker.json"

CONTENT_VAULT = Path(
    "/Users/bearliu/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bear Content Vault"
)
READER_DRAFTS_DIR = CONTENT_VAULT / "Social Posts" / "drafts" / "reader"

READWISE_EXPORT_URL = "https://readwise.io/api/v2/export/"
VALID_CATEGORIES = {"articles", "tweets", "videos", "podcasts"}

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Shared article context block (reused by both prompts)
_ARTICLE_CONTEXT = """\
## 文章信息

标题：{title}
作者：{author}
分类：{category}
来源链接：{source_url}

我划出的高亮：
{highlights_text}

我的个人笔记：
{doc_note}"""

READING_NOTE_PROMPT = """\
你正在帮 Bear Liu（内容创作者，现居新西兰）写一段私人阅读笔记，方便他日后快速回顾这篇内容的价值。

{article_context}

## 输出要求

用中文写，150-200 字，分三段（不需要加小标题，直接写内容）：

第一段：这篇讲了什么。1-2 句话概括原文的核心观点或论点。

第二段：高亮要点。把散落的 highlights 提炼成 2-3 个关键点，用简练的语言串起来。

第三段：对我的启发。这篇内容可以怎样帮到 Bear（他是 Fractional Product Designer，做 YouTube 频道、播客、独立产品）。具体到可执行的方向。

## 风格

信息密度高，口语化，方便快速扫描。像给自己写的备忘录。
禁止破折号、"不是……而是"句型、鸡汤、营销词、列表格式。"""

SHARING_POST_PROMPT = """\
你正在帮 Bear Liu 把一篇阅读笔记改写成社交媒体分享帖（LinkedIn / Twitter），面向设计师、创作者和独立开发者。

{article_context}

## 输出要求

第一行输出一个简短的中文标题（10-20 字，概括核心洞察，不要用原文标题直译）。
标题后空一行，接 200-300 字中文短文。

## 风格参考（从 Bear 过往帖子总结）

**开头**：用一个吸引人的洞察、反直觉的观点、或一个具体场景切入。绝对不要用"我最近读了……"。

**上下文**：在开头之后自然地带入 1-2 句关于作者或原文的背景介绍（谁写的、什么场景下的内容、为什么值得关注），让读者知道信息来源。不要生硬地"介绍作者"，融入行文中。

**主体**：70% 讲这篇内容的关键洞察和实用价值（对读者有什么用），30% Bear 自己的实践经验或反思。读者读完要觉得"学到了"。

**收尾**：自然收束，可以是一句点睛的观点，或者 Bear 接下来打算怎么用。不要 CTA，不要鸡汤。

**语气**：像一个有经验的朋友在分享他发现的好东西。真诚、具体、有观点。不炫技，不说教。长短句交替，口语化。

**绝对禁止**：
- 破折号（Em Dashes）
- "不是……而是"句型
- 鸡汤句："相信自己""勇敢追梦"
- 营销词："颠覆""赋能""打造""闭环"
- "总结一下" + 要点回顾
- 列表、表格
- emoji"""


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


def _build_article_context(book: dict, highlights_text: str, doc_note: str) -> str:
    return _ARTICLE_CONTEXT.format(
        title=book.get("title") or "（无标题）",
        author=book.get("author") or "（未知作者）",
        category=book.get("category", ""),
        source_url=book.get("source_url") or book.get("readwise_url") or "",
        highlights_text=highlights_text or "（无高亮）",
        doc_note=doc_note or "（无笔记）",
    )


def _call_claude(prompt: str, max_tokens: int) -> str:
    if not ANTHROPIC_API_KEY:
        return "[生成失败：ANTHROPIC_API_KEY 未设置，请手动补充]"
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        print(f"\n  WARNING: Claude API error: {exc}")
        return "[生成失败，请手动补充]"


def generate_reading_note(book: dict, highlights_text: str, doc_note: str, dry_run: bool) -> str:
    if dry_run:
        return "[dry-run — Reading Note 未生成]"
    ctx = _build_article_context(book, highlights_text, doc_note)
    prompt = READING_NOTE_PROMPT.format(article_context=ctx)
    return _call_claude(prompt, max_tokens=400)


def generate_sharing_post(book: dict, highlights_text: str, doc_note: str, dry_run: bool) -> str:
    if dry_run:
        return "[dry-run — Sharing Post 未生成]"
    ctx = _build_article_context(book, highlights_text, doc_note)
    prompt = SHARING_POST_PROMPT.format(article_context=ctx)
    return _call_claude(prompt, max_tokens=800)


_READWISE_DEFAULT_PREFIX = "readwise-assets.s3.amazonaws.com/static/images/"
ASSETS_DIR = VAULT_ROOT / "_assets" / "reader"


def _has_real_cover(book: dict) -> bool:
    """Return True if cover_image_url is a real image, not a Readwise placeholder."""
    url = (book.get("cover_image_url") or "").strip()
    if not url:
        return False
    return _READWISE_DEFAULT_PREFIX not in url


def _capture_screenshot(source_url: str, slug: str) -> Optional[str]:
    """Take a screenshot of source_url, save to assets, return vault-relative path."""
    if not source_url or source_url.startswith("mailto:"):
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("    WARNING: playwright not installed, skipping screenshot")
        return None

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.png"
    abs_path = ASSETS_DIR / filename

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(source_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(abs_path))
            browser.close()
        return f"_assets/reader/{filename}"
    except Exception as exc:
        print(f"    WARNING: Screenshot failed: {exc}")
        return None


def _get_sharing_image(book: dict, slug: str) -> Optional[str]:
    """Return markdown image string for sharing post, or None."""
    if _has_real_cover(book):
        url = book["cover_image_url"]
        return f"![{book.get('title', '')}]({url})"

    source_url = book.get("source_url") or ""
    rel_path = _capture_screenshot(source_url, slug)
    if rel_path:
        return f"![[{rel_path}]]"
    return None


def build_note_content(book: dict, reading_note: str, sharing_post: str, sharing_image: Optional[str] = None) -> str:
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

    sections += ["## Reading Note", "", reading_note, ""]

    # Sharing Post: title (first line of sharing_post) + image + body + link
    sections += ["## Sharing Post", ""]
    post_lines = sharing_post.strip().split("\n", 1)
    post_title = post_lines[0].strip()
    post_body = post_lines[1].strip() if len(post_lines) > 1 else ""
    sections += [post_title, ""]
    if sharing_image:
        sections += [sharing_image, ""]
    if post_body:
        sections += [post_body, ""]
    if source_url:
        sections += [f"原文链接：{source_url}", ""]

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

    rel = f"42_Reader_Library/{year}/{month}/{filename}"
    abs_path = LIBRARY_DIR / year / month / filename
    return rel, abs_path


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_to_vault(abs_path: Path, content: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)


def write_sharing_draft(book: dict, sharing_post: str, sharing_image: Optional[str] = None) -> Optional[str]:
    """Extract Sharing Post and write as a Content Vault draft. Returns relative path or None."""
    source_url = book.get("source_url") or book.get("readwise_url") or ""
    date_str = _latest_highlight_date(book.get("highlights") or [])
    title_raw = book.get("title") or "(untitled)"
    raw_slug = slugify(title_raw, max_length=60, word_boundary=True)
    filename = f"{date_str}-{raw_slug}.md"
    abs_path = READER_DRAFTS_DIR / filename

    # Parse title (first line) and body from sharing_post
    post_lines = sharing_post.strip().split("\n", 1)
    post_title = post_lines[0].strip().lstrip("# ").strip()
    post_body = post_lines[1].strip() if len(post_lines) > 1 else ""

    # Build frontmatter compatible with content-publisher
    fm_title = post_title.replace('"', "'")
    frontmatter = f"""---
title: "{fm_title}"
date: {date_str}
publish_twitter_cn: true
publish_linkedin_en: true
ready: false
status: draft
source: reader
source_url: "{source_url}"
---"""

    # Assemble: title → image → body → link
    sections = [frontmatter, "", post_title, ""]
    if sharing_image:
        sections += [sharing_image, ""]
    if post_body:
        sections += [post_body, ""]
    if source_url:
        sections += [f"原文链接：{source_url}", ""]

    content = "\n".join(sections)
    write_to_vault(abs_path, content)
    rel = f"Social Posts/drafts/reader/{filename}"
    return rel


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Readwise Reader to Obsidian.")
    parser.add_argument("--dry-run", action="store_true", help="Print would-write paths; skip file writes and Claude calls.")
    parser.add_argument("--reset", action="store_true", help="Clear tracker and re-sync everything.")
    parser.add_argument("--test", type=int, metavar="N", help="Preview mode: generate for N most recent articles, print to stdout, don't write files.")
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    test_mode: int = args.test or 0
    if dry_run:
        print("=== DRY RUN mode — no files written, no Claude calls ===\n")
    if test_mode:
        print(f"=== TEST mode — generating for {test_mode} most recent articles, stdout only ===\n")

    tracker = load_tracker()

    if args.reset:
        print("Resetting tracker…")
        tracker = {"synced": {}, "last_sync": None}
        save_tracker(tracker)

    last_sync = tracker.get("last_sync")
    print(f"Last sync: {last_sync or 'never (full fetch)'}")
    print("Fetching articles from Readwise…")

    # In test mode, fetch everything (ignore last_sync) so we can pick recent ones
    fetch_since = None if test_mode else last_sync
    articles = fetch_articles(fetch_since)
    print(f"\nFetched {len(articles)} eligible articles total.\n")

    # Test mode: pick the N most recent (by highlight date), skip tracker
    if test_mode:
        articles.sort(
            key=lambda b: _latest_highlight_date(b.get("highlights") or []),
            reverse=True,
        )
        articles = articles[:test_mode]
        for book in articles:
            title = book.get("title") or "(untitled)"
            print(f"{'=' * 60}")
            print(f"TEST: {title[:70]}")
            print(f"{'=' * 60}")
            doc_note = (book.get("document_note") or "").strip()
            highlights_text = build_highlights_text(book.get("highlights", []))
            print("\n--- Reading Note ---\n")
            note = generate_reading_note(book, highlights_text, doc_note, dry_run=False)
            print(note)
            print("\n--- Sharing Post ---\n")
            cover = book.get("cover_image_url", "")
            has_cover = _has_real_cover(book)
            print(f"[Image: {'cover' if has_cover else 'would screenshot'} — {cover[:80]}]")
            post = generate_sharing_post(book, highlights_text, doc_note, dry_run=False)
            print(post)
            source = book.get("source_url") or book.get("readwise_url") or ""
            if source:
                print(f"\n原文链接：{source}")
            print()
        print(f"\nTest done. Generated for {len(articles)} articles.")
        return

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
        reading_note = generate_reading_note(book, highlights_text, doc_note, dry_run=False)
        sharing_post = generate_sharing_post(book, highlights_text, doc_note, dry_run=False)

        raw_slug = slugify(title, max_length=60, word_boundary=True)
        sharing_image = _get_sharing_image(book, raw_slug)

        content = build_note_content(book, reading_note, sharing_post, sharing_image)

        try:
            write_to_vault(abs_path, content)
        except Exception as exc:
            print(f"  ERROR writing {abs_path}: {exc}")
            error_count += 1
            continue

        # Also write sharing draft to Content Vault
        try:
            draft_rel = write_sharing_draft(book, sharing_post, sharing_image)
            if draft_rel:
                print(f"  -> draft: {draft_rel}")
        except Exception as exc:
            print(f"  WARNING: draft write failed: {exc}")

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
