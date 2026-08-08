#!/usr/bin/env python3
"""
Notion Book Library -> Obsidian sync script.

Pulls all 265 books from the Notion Book Library database and writes
Markdown files to Bear Vault/40_Books/YYYY/YYYY-{slug}.md.
Cover images are downloaded to Bear Vault/_assets/books/{slug}.jpg.

Usage:
    python3 sync_books.py           # full sync (idempotent)
    python3 sync_books.py --dry-run # print would-write paths, skip file writes
    python3 sync_books.py --reset   # clear tracker and re-sync everything

Env vars required:
    NOTION_TOKEN        - Notion integration token
    NOTION_BOOK_DB_ID   - Notion Book Library database ID
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
from dotenv import load_dotenv
from slugify import slugify

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_BOOK_DB_ID = os.environ.get("NOTION_BOOK_DB_ID", "")

VAULT_ROOT = Path(
    "/Users/bearliu/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bear Vault"
)
BOOKS_DIR = VAULT_ROOT / "40_Books"
ASSETS_DIR = VAULT_ROOT / "_assets" / "books"
TRACKER_PATH = Path(__file__).parent / "books_tracker.json"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


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


def mark_synced(tracker: dict, notion_id: str, dest: str) -> None:
    tracker["synced"][notion_id] = {
        "dest": dest,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Notion API helpers
# ---------------------------------------------------------------------------

def _notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _notion_get(path: str, params: dict | None = None) -> dict:
    url = f"{NOTION_API_BASE}{path}"
    for attempt in range(1, 4):
        resp = requests.get(url, headers=_notion_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 30))
            print(f"\n  Rate limited. Waiting {wait}s… (attempt {attempt}/3)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after 3 retries: {url}")


def _notion_post(path: str, payload: dict) -> dict:
    url = f"{NOTION_API_BASE}{path}"
    for attempt in range(1, 4):
        resp = requests.post(url, headers=_notion_headers(), json=payload, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 30))
            print(f"\n  Rate limited. Waiting {wait}s… (attempt {attempt}/3)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after 3 retries: {url}")


# ---------------------------------------------------------------------------
# Fetch books
# ---------------------------------------------------------------------------

def fetch_all_books() -> list[dict]:
    """Paginated fetch of all books from the Notion database."""
    if not NOTION_TOKEN:
        print("ERROR: NOTION_TOKEN not set.", file=sys.stderr)
        sys.exit(1)
    if not NOTION_BOOK_DB_ID:
        print("ERROR: NOTION_BOOK_DB_ID not set.", file=sys.stderr)
        sys.exit(1)

    books: list[dict] = []
    start_cursor: str | None = None
    page_num = 0

    while True:
        page_num += 1
        payload: dict = {"page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor

        print(f"  Fetching page {page_num}…", end=" ", flush=True)
        data = _notion_post(f"/databases/{NOTION_BOOK_DB_ID}/query", payload)

        results = data.get("results", [])
        print(f"{len(results)} books")
        books.extend(results)

        if data.get("has_more"):
            start_cursor = data.get("next_cursor")
        else:
            break

    return books


# ---------------------------------------------------------------------------
# Fetch page blocks
# ---------------------------------------------------------------------------

def fetch_page_blocks(page_id: str) -> list[dict]:
    """Fetch all blocks for a page (one level of recursion for has_children)."""
    data = _notion_get(f"/blocks/{page_id}/children", params={"page_size": 100})
    blocks = data.get("results", [])

    # Recurse one level for blocks that have children
    expanded: list[dict] = []
    for block in blocks:
        expanded.append(block)
        if block.get("has_children"):
            child_data = _notion_get(
                f"/blocks/{block['id']}/children", params={"page_size": 100}
            )
            child_blocks = child_data.get("results", [])
            for cb in child_blocks:
                cb["_parent_type"] = block.get("type", "")
            expanded.extend(child_blocks)

    return expanded


# ---------------------------------------------------------------------------
# Block content parsing
# ---------------------------------------------------------------------------

def _rich_text_to_md(rich_text: list[dict]) -> str:
    """Convert Notion rich_text array to plain Markdown string."""
    parts: list[str] = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        annotations = rt.get("annotations", {})
        href = rt.get("href")

        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("code"):
            text = f"`{text}`"
        if href:
            text = f"[{text}]({href})"

        parts.append(text)
    return "".join(parts)


def _block_to_md(block: dict) -> str | None:
    """Convert a single Notion block to Markdown. Returns None to skip."""
    btype = block.get("type", "")
    data = block.get(btype, {})
    rt = data.get("rich_text", [])

    if btype == "paragraph":
        text = _rich_text_to_md(rt)
        return text if text else ""

    if btype in ("heading_1", "heading_2", "heading_3"):
        text = _rich_text_to_md(rt)
        level = {"heading_1": "#", "heading_2": "##", "heading_3": "###"}[btype]
        return f"{level} {text}" if text else None

    if btype == "bulleted_list_item":
        text = _rich_text_to_md(rt)
        return f"- {text}" if text else None

    if btype == "numbered_list_item":
        text = _rich_text_to_md(rt)
        return f"1. {text}" if text else None

    if btype == "quote":
        text = _rich_text_to_md(rt)
        return f"> {text}" if text else None

    if btype == "code":
        text = _rich_text_to_md(rt)
        lang = data.get("language", "")
        return f"```{lang}\n{text}\n```" if text else None

    if btype == "image":
        return "[图片]"

    if btype == "bookmark":
        url = data.get("url", "")
        caption = _rich_text_to_md(data.get("caption", []))
        if caption:
            return f"[{caption}]({url})"
        return url if url else None

    if btype == "divider":
        return "---"

    if btype == "callout":
        text = _rich_text_to_md(rt)
        return f"> {text}" if text else None

    # Unknown block types — skip silently
    return None


def parse_blocks_into_sections(blocks: list[dict]) -> dict[str, str]:
    """
    Walk blocks, split on heading_3 markers, return dict of section_name -> content.
    Known sections: Summary, My notes, Highlights, Reference/Links
    """
    SECTION_KEYS = {
        "summary": "Summary",
        "my notes": "My notes",
        "highlights": "Highlights",
        "reference/links": "Reference/Links",
        "reference": "Reference/Links",
        "links": "Reference/Links",
    }

    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    pre_section_lines: list[str] = []

    for block in blocks:
        btype = block.get("type", "")

        if btype == "heading_3":
            rt = block.get("heading_3", {}).get("rich_text", [])
            heading_text = _rich_text_to_md(rt).strip()
            key = heading_text.lower()
            # Map to canonical name
            section_name = SECTION_KEYS.get(key)
            if section_name:
                current_section = section_name
                if current_section not in sections:
                    sections[current_section] = []
                continue
            # Unknown heading_3 — treat as content
            current_section = None

        line = _block_to_md(block)
        if line is None:
            continue

        if current_section:
            sections[current_section].append(line)
        else:
            pre_section_lines.append(line)

    # Convert lists to strings, stripping leading/trailing blank lines
    result: dict[str, str] = {}
    for name, lines in sections.items():
        text = "\n".join(lines).strip()
        if text:
            result[name] = text

    # Pre-section lines (content before any heading) → merge into "My notes"
    pre_text = "\n".join(pre_section_lines).strip()
    if pre_text:
        if "My notes" in result:
            result["My notes"] = pre_text + "\n\n" + result["My notes"]
        else:
            result["My notes"] = pre_text

    return result


# ---------------------------------------------------------------------------
# Property extraction
# ---------------------------------------------------------------------------

def _rt_plain(rich_text: list[dict]) -> str:
    """Extract plain text from rich_text array (no markdown annotations)."""
    return "".join(rt.get("plain_text", "") for rt in rich_text).strip()


def _extract_title(prop: dict) -> str:
    return _rt_plain(prop.get("title", []))


def _extract_rich_text(prop: dict) -> str:
    """For body fields like Why, preserve markdown formatting."""
    rt = prop.get("rich_text", [])
    return _rich_text_to_md(rt).strip()


def _extract_select(prop: dict) -> str:
    sel = prop.get("select")
    return sel["name"] if sel else ""


def _extract_multi_select(prop: dict) -> list[str]:
    return [s["name"] for s in prop.get("multi_select", [])]


def _extract_date(prop: dict) -> str:
    d = prop.get("date")
    if d and d.get("start"):
        return d["start"][:10]  # YYYY-MM-DD
    return ""


def _extract_url(prop: dict) -> str:
    return prop.get("url") or ""


def _extract_checkbox(prop: dict) -> bool:
    return prop.get("checkbox", False)


def extract_book_props(page: dict) -> dict:
    """Extract all relevant properties from a Notion page object."""
    props = page.get("properties", {})

    def get(key: str) -> dict:
        return props.get(key, {})

    title = _extract_title(get("Name"))
    author = _rt_plain(get("Author").get("rich_text", []))
    finished = _extract_date(get("Finished Time"))
    category = _extract_select(get("Category"))
    fmt = _extract_select(get("Format"))
    status = _extract_select(get("Status"))
    rating = _extract_select(get("My Score"))
    years = _extract_multi_select(get("Year"))
    goodreads = _extract_url(get("Goodreads"))
    why = _extract_rich_text(get("Why"))
    en_review = _extract_checkbox(get("English Review"))
    zh_review = _extract_checkbox(get("Chinese Review"))
    cover_files = get("Cover").get("files", [])

    return {
        "title": title,
        "author": author,
        "finished": finished,
        "category": category,
        "format": fmt,
        "status": status,
        "rating": rating,
        "years": years,
        "goodreads": goodreads,
        "why": why,
        "en_review": en_review,
        "zh_review": zh_review,
        "cover_files": cover_files,
    }


# ---------------------------------------------------------------------------
# Cover image download
# ---------------------------------------------------------------------------

def get_cover_url(cover_files: list[dict]) -> str | None:
    """Extract the best available cover URL from Notion files property."""
    for f in cover_files:
        ftype = f.get("type", "")
        if ftype == "external":
            url = f.get("external", {}).get("url", "")
            if url:
                return url
        elif ftype == "file":
            url = f.get("file", {}).get("url", "")
            if url:
                return url
    return None


def download_cover(cover_files: list[dict], slug: str, dry_run: bool) -> str | None:
    """
    Download cover to _assets/books/{slug}.{ext}.
    Returns the filename (e.g. "the-e-myth-revisited.jpg") or None on failure.
    """
    url = get_cover_url(cover_files)
    if not url:
        return None

    if dry_run:
        return f"{slug}.jpg"  # placeholder

    # Determine extension from URL
    url_path = url.split("?")[0].lower()
    ext = "jpg"
    for candidate in (".png", ".webp", ".jpeg", ".gif"):
        if url_path.endswith(candidate):
            ext = candidate.lstrip(".")
            break
    if url_path.endswith(".jpg") or url_path.endswith(".jpeg"):
        ext = "jpg"

    filename = f"{slug}.{ext}"
    dest = ASSETS_DIR / filename

    if dest.exists():
        return filename  # already downloaded

    try:
        resp = requests.get(url, timeout=20, stream=True)
        resp.raise_for_status()
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except Exception as exc:
        print(f"\n  WARNING: cover download failed ({exc}), skipping.")
        return None


# ---------------------------------------------------------------------------
# Build Markdown content
# ---------------------------------------------------------------------------

def build_book_content(props: dict, sections: dict[str, str], notion_id: str, cover_filename: str | None) -> str:
    """Assemble the full Markdown file content."""

    # --- Frontmatter ---
    def fm_str(val: str) -> str:
        return f'"{val.replace(chr(34), chr(39))}"' if val else '""'

    year = props["years"][0] if props["years"] else ""

    fm_lines = [
        "---",
        f'title: {fm_str(props["title"])}',
        f'author: {fm_str(props["author"])}',
    ]
    if props["finished"]:
        fm_lines.append(f'finished: {props["finished"]}')
    if props["category"]:
        fm_lines.append(f'category: {props["category"]}')
    if props["format"]:
        fm_lines.append(f'format: {props["format"]}')
    if props["status"]:
        fm_lines.append(f'status: {props["status"]}')
    if props["rating"]:
        fm_lines.append(f'rating: {fm_str(props["rating"])}')
    if props["goodreads"]:
        fm_lines.append(f'goodreads: {props["goodreads"]}')
    fm_lines.append(f'notion_id: {notion_id}')
    if cover_filename:
        fm_lines.append(f'cover: "[[{cover_filename}]]"')
    fm_lines.append("---")

    body_parts: list[str] = ["\n".join(fm_lines)]

    # Cover embed
    if cover_filename:
        body_parts.append(f"\n![[{cover_filename}]]")

    # Why I Read This
    if props["why"]:
        body_parts.append(f"\n## Why I Read This\n\n{props['why']}")

    # Page sections
    for section_name in ("Summary", "My notes", "Highlights", "Reference/Links"):
        content = sections.get(section_name, "")
        if content:
            # Use friendly display name
            display = section_name if section_name != "My notes" else "My Notes"
            body_parts.append(f"\n## {display}\n\n{content}")

    return "\n".join(body_parts) + "\n"


# ---------------------------------------------------------------------------
# Path building
# ---------------------------------------------------------------------------

def build_dest_path(props: dict, slug: str) -> tuple[str, Path]:
    """Return (relative_str, absolute_path) for the output Markdown file."""
    years = props["years"]
    year = years[0] if years else "Unknown"

    title = props.get("title", "").strip()
    filename = safe_title_filename(title) if title else f"{slug}.md"
    rel = f"Books/{year}/{filename}"
    abs_path = BOOKS_DIR / year / filename
    return rel, abs_path


def safe_title_filename(title: str) -> str:
    """Convert a book title to a safe filename, preserving original characters."""
    if not title:
        return "untitled.md"
    safe = title.replace("/", " & ").replace(":", " —").strip(". ")
    return safe + ".md"


def make_slug(title: str) -> str:
    if not title:
        return "untitled"
    return slugify(title, max_length=60, word_boundary=True)


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
    parser = argparse.ArgumentParser(description="Sync Notion Book Library to Obsidian.")
    parser.add_argument("--dry-run", action="store_true", help="Print would-write paths; skip file writes and cover downloads.")
    parser.add_argument("--reset", action="store_true", help="Clear tracker and re-sync everything.")
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    if dry_run:
        print("=== DRY RUN mode — no files written, no covers downloaded ===\n")

    tracker = load_tracker()

    if args.reset:
        print("Resetting tracker…")
        tracker = {"synced": {}, "last_sync": None}
        save_tracker(tracker)

    print("Fetching books from Notion…")
    books = fetch_all_books()
    print(f"\nFetched {len(books)} books total.\n")

    already_synced = 0
    new_count = 0
    error_count = 0

    for page in books:
        notion_id = page["id"]
        props = extract_book_props(page)
        title = props["title"] or f"(id:{notion_id})"
        slug = make_slug(props["title"])

        if notion_id in tracker["synced"]:
            already_synced += 1
            continue

        rel_path, abs_path = build_dest_path(props, slug)

        if dry_run:
            year = props["years"][0] if props["years"] else "Unknown"
            has_cover = bool(get_cover_url(props["cover_files"]))
            print(f"[would-write] {rel_path}")
            print(f"             Title: {title}")
            print(f"             Year: {year}  Status: {props['status']}  Rating: {props['rating']}")
            print(f"             Cover: {'yes' if has_cover else 'no'}  Why: {'yes' if props['why'] else 'no'}")
            print()
            new_count += 1
            continue

        print(f"Syncing: {title[:70]}")

        # Download cover
        cover_filename = download_cover(props["cover_files"], slug, dry_run=False)

        # Fetch page blocks
        try:
            blocks = fetch_page_blocks(notion_id)
            sections = parse_blocks_into_sections(blocks)
        except Exception as exc:
            print(f"  WARNING: failed to fetch blocks: {exc}")
            sections = {}

        # Build and write content
        content = build_book_content(props, sections, notion_id, cover_filename)

        try:
            write_to_vault(abs_path, content)
        except Exception as exc:
            print(f"  ERROR writing {abs_path}: {exc}")
            error_count += 1
            continue

        mark_synced(tracker, notion_id, rel_path)
        save_tracker(tracker)
        new_count += 1
        print(f"  -> {rel_path}")

    if not dry_run:
        tracker["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_tracker(tracker)

    print(f"\nDone. Already synced: {already_synced}, New: {new_count}, Errors: {error_count}")


if __name__ == "__main__":
    main()
