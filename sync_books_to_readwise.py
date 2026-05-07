#!/usr/bin/env python3
"""
Obsidian Books -> Readwise highlight sync.

Scans Obsidian Books folder, parses highlights from markdown files,
and uploads them to Readwise via the Highlights API.

Usage:
    python3 sync_books_to_readwise.py              # full sync
    python3 sync_books_to_readwise.py --dry-run    # print what would be uploaded
    python3 sync_books_to_readwise.py --book "认知觉醒"  # sync single book
    python3 sync_books_to_readwise.py --limit 5    # sync at most N books
    python3 sync_books_to_readwise.py --reset       # clear tracker, re-check everything

Env vars required:
    READWISE_TOKEN - from readwise.io/access_token
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

READWISE_TOKEN = os.environ.get("READWISE_TOKEN", "")

VAULT_ROOT = Path(
    "/Users/bearliu/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bear Vault"
)
BOOKS_DIR = VAULT_ROOT / "40_Books"
TRACKER_PATH = Path(__file__).parent / "readwise_upload_tracker.json"

READWISE_HIGHLIGHTS_URL = "https://readwise.io/api/v2/highlights/"
READWISE_BOOKS_URL = "https://readwise.io/api/v2/books/"

# Max highlight text length per Readwise API
MAX_HIGHLIGHT_LEN = 8191


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

def load_tracker():
    # type: () -> dict
    if TRACKER_PATH.exists():
        with open(TRACKER_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"uploaded": {}, "last_run": None}


def save_tracker(tracker):
    # type: (dict) -> None
    with open(TRACKER_PATH, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Readwise API
# ---------------------------------------------------------------------------

def _request_with_retry(method, url, headers, max_retries=3, **kwargs):
    # type: (str, str, dict, int, ...) -> requests.Response
    for attempt in range(1, max_retries + 1):
        resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            print(f"  Rate limited. Waiting {wait}s... (attempt {attempt}/{max_retries})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def get_existing_books():
    # type: () -> Set[Tuple[str, str]]
    """Fetch all books from Readwise, return set of (title_lower, author_lower)."""
    if not READWISE_TOKEN:
        print("ERROR: READWISE_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    headers = {"Authorization": f"Token {READWISE_TOKEN}"}
    existing = set()  # type: Set[Tuple[str, str]]
    page = 1

    while True:
        params = {"category": "books", "page_size": 1000, "page": page}
        resp = _request_with_retry("GET", READWISE_BOOKS_URL, headers=headers, params=params)
        data = resp.json()

        for book in data.get("results", []):
            title = (book.get("title") or "").strip().lower()
            author = (book.get("author") or "").strip().lower()
            existing.add((title, author))

        if not data.get("next"):
            break
        page += 1

    return existing


def upload_highlights(highlights_payload):
    # type: (List[dict]) -> requests.Response
    """POST highlights to Readwise."""
    headers = {
        "Authorization": f"Token {READWISE_TOKEN}",
        "Content-Type": "application/json",
    }
    return _request_with_retry(
        "POST", READWISE_HIGHLIGHTS_URL, headers=headers,
        json={"highlights": highlights_payload}
    )


# ---------------------------------------------------------------------------
# Markdown Parser
# ---------------------------------------------------------------------------

def parse_frontmatter(content):
    # type: (str) -> dict
    """Extract YAML frontmatter as a dict (simple key:value parsing)."""
    fm = {}
    if not content.startswith("---"):
        return fm
    parts = content.split("---", 2)
    if len(parts) < 3:
        return fm
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        fm[key] = val
    return fm


def _extract_highlights_section(content):
    # type: (str) -> Optional[str]
    """Extract text between ## Highlights (or ### 摘记) and the next heading or EOF."""
    # Try ## Highlights first, then ### 摘记
    patterns = [
        r'## Highlights\s*\n',
        r'### 摘记\s*\n',
    ]
    for pat in patterns:
        match = re.search(pat, content)
        if match:
            start = match.end()
            # Find next ## heading
            next_heading = re.search(r'\n## ', content[start:])
            if next_heading:
                end = start + next_heading.start()
            else:
                end = len(content)
            section = content[start:end].strip()
            if section:
                return section
    return None


def _is_metadata_line(line):
    # type: (str) -> bool
    """Check if a line is metadata (book title, author, note count) to skip."""
    stripped = line.strip()
    # "X个笔记" pattern
    if re.match(r'^\d+个笔记$', stripped):
        return True
    # Empty or just dashes
    if not stripped or stripped == '---':
        return True
    return False


def _is_image_line(line):
    # type: (str) -> bool
    """Check if a line is an image reference."""
    stripped = line.strip()
    if stripped == '[图片]':
        return True
    if re.match(r'^!\[\[.*\.(jpg|jpeg|png|gif|webp)', stripped, re.IGNORECASE):
        return True
    return False


def _is_chapter_header(line):
    # type: (str) -> bool
    """Check if a line is a chapter/section header (not a highlight)."""
    stripped = line.strip()
    # # **Chapter** or # **2. Chapter**
    if re.match(r'^#+\s+\*\*', stripped):
        return True
    # **第X章 ...** or **章节名**
    if re.match(r'^\*\*第.+章.*\*\*$', stripped):
        return True
    if re.match(r'^\*\*.+\*\*$', stripped) and len(stripped) < 40:
        return True
    return False


def _parse_wechat_format(section):
    # type: (str) -> List[dict]
    """Parse WeChat Read bullet format with - prefix, chapter headers, inline notes."""
    highlights = []  # type: List[dict]
    lines = section.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty, metadata, images, chapter headers
        if not stripped or _is_metadata_line(stripped) or _is_image_line(stripped):
            i += 1
            continue
        if _is_chapter_header(stripped):
            i += 1
            continue
        # Skip "来自微信读书" footer
        if stripped == "来自微信读书":
            i += 1
            continue

        # Inline note: **YYYY/MM/DD 发表想法**
        if re.match(r'^\*\*\d{4}/\d{2}/\d{2}\s+发表想法\*\*$', stripped):
            note_text = ""
            highlight_text = ""
            i += 1
            # Next line(s) are the note content
            while i < len(lines) and not lines[i].strip().startswith("> 原文："):
                if lines[i].strip():
                    note_text += lines[i].strip() + " "
                i += 1
            # > 原文：... is the actual highlight
            if i < len(lines) and lines[i].strip().startswith("> 原文："):
                highlight_text = lines[i].strip()[len("> 原文："):]
                i += 1
            note_text = note_text.strip()
            highlight_text = highlight_text.strip()
            if highlight_text:
                highlights.append({"text": highlight_text, "note": note_text})
            elif note_text:
                highlights.append({"text": note_text, "note": ""})
            continue

        # Regular bullet: - text
        if stripped.startswith("- "):
            text = stripped[2:].strip()
            if text and not _is_image_line(text):
                highlights.append({"text": text, "note": ""})
            i += 1
            continue

        # Skip other lines (standalone chapter names etc.)
        i += 1

    return highlights


def _is_standalone_metadata(line, title=""):
    # type: (str, str) -> bool
    """Check if a line is standalone metadata to skip in paragraph format."""
    stripped = line.strip()
    if not stripped:
        return True
    if stripped == "---":
        return True
    # "X个笔记" pattern
    if re.match(r'^\d+个笔记$', stripped):
        return True
    # Book title wrapped in 《》
    if re.match(r'^《.+》$', stripped):
        return True
    # "来自微信读书"
    if stripped == "来自微信读书":
        return True
    # Short standalone line that looks like author name or chapter header
    # (only in the header area, handled by skipping_header logic)
    return False


def _is_chapter_name(line):
    # type: (str) -> bool
    """Check if a line is a short chapter/section name (not a highlight)."""
    stripped = line.strip()
    if not stripped:
        return False
    # Very short lines (< 15 chars) that are standalone are likely chapter names
    # in the paragraph format (e.g., 楔子, 火山, 接触, 互动)
    if len(stripped) < 15 and "\n" not in stripped:
        # But not if it looks like a real quote (has punctuation typical of quotes)
        if not any(c in stripped for c in "。！？，；：""''、"):
            return True
    return False


def _parse_paragraph_format(section):
    # type: (str) -> List[dict]
    """Parse plain text paragraph format."""
    highlights = []  # type: List[dict]

    lines = section.split("\n")
    clean_lines = []  # type: List[str]

    # Skip header metadata: first few non-empty lines that are book title,
    # author name, "X个笔记" etc.
    header_done = False
    header_skip_count = 0

    for line in lines:
        stripped = line.strip()

        # Always skip these
        if _is_image_line(stripped):
            continue
        if stripped == "来自微信读书":
            continue
        if stripped == "---":
            if not header_done:
                continue
            # --- in body is a separator, skip it
            continue

        # Header skipping: skip metadata-looking lines at the top
        if not header_done:
            if not stripped:
                # Empty lines in header area, skip
                if header_skip_count > 0:
                    continue
                else:
                    continue
            if _is_standalone_metadata(stripped):
                header_skip_count += 1
                continue
            # Check if this looks like author name (short, < 20 chars, no quotes)
            if header_skip_count > 0 and len(stripped) < 25 and not any(c in stripped for c in "。！？""''"):
                header_skip_count += 1
                continue
            header_done = True

        if not stripped:
            clean_lines.append("")
            continue

        clean_lines.append(stripped)

    text = "\n".join(clean_lines).strip()
    if not text:
        return highlights

    # Decide splitting strategy:
    # If text has double-newline separations, use those
    # If text is mostly single-newline separated lines (each > 20 chars), split on single newline
    double_blocks = re.split(r'\n\s*\n', text)
    if len(double_blocks) > 2:
        # Double-newline format (e.g., 龙蛋)
        for para in double_blocks:
            para = para.strip()
            if not para:
                continue
            if _is_chapter_name(para):
                continue
            if _is_chapter_header(para):
                continue
            if _is_image_line(para):
                continue
            if len(para) < 5:
                continue
            highlights.append({"text": para, "note": ""})
    else:
        # Single-newline format (e.g., 月亮与六便士)
        # Each line is likely a separate highlight
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if _is_chapter_name(line):
                continue
            if _is_chapter_header(line):
                continue
            if _is_image_line(line):
                continue
            if len(line) < 5:
                continue
            highlights.append({"text": line, "note": ""})

    return highlights


def _detect_format(section):
    # type: (str) -> str
    """Detect the highlight format: 'bullet' or 'paragraph'."""
    lines = section.split("\n")
    bullet_count = 0
    total_lines = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        total_lines += 1
        if stripped.startswith("- "):
            bullet_count += 1

    if total_lines > 0 and bullet_count / total_lines > 0.3:
        return "bullet"
    return "paragraph"


def parse_book_md(path):
    # type: (Path) -> Optional[dict]
    """Parse a book markdown file, return structured data or None if no highlights."""
    content = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)

    title = fm.get("title", path.stem)
    author = fm.get("author", "")
    goodreads = fm.get("goodreads", "")
    finished = fm.get("finished", "")

    # Extract highlights section
    section = _extract_highlights_section(content)
    if not section:
        return None

    # Detect format and parse
    fmt = _detect_format(section)
    if fmt == "bullet":
        highlights = _parse_wechat_format(section)
    else:
        highlights = _parse_paragraph_format(section)

    # Filter out empty/too-short highlights
    filtered = []  # type: List[dict]
    for hl in highlights:
        text = hl["text"].strip()
        if len(text) < 5:
            continue
        # Truncate if too long
        if len(text) > MAX_HIGHLIGHT_LEN:
            text = text[:MAX_HIGHLIGHT_LEN - 3] + "..."
        filtered.append({"text": text, "note": hl.get("note", "")})

    if not filtered:
        return None

    return {
        "title": title,
        "author": author,
        "goodreads": goodreads,
        "finished": finished,
        "highlights": filtered,
        "source_path": str(path),
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def scan_books():
    # type: () -> List[Path]
    """Scan all book markdown files across year folders."""
    paths = []  # type: List[Path]
    for year_dir in sorted(BOOKS_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        if year_dir.name in ("Assets", "Unknown"):
            continue
        for md_file in sorted(year_dir.glob("*.md")):
            paths.append(md_file)
    # Also check Unknown folder
    unknown_dir = BOOKS_DIR / "Unknown"
    if unknown_dir.is_dir():
        for md_file in sorted(unknown_dir.glob("*.md")):
            paths.append(md_file)
    return paths


def build_tracker_key(path):
    # type: (Path) -> str
    """Build a stable key for the tracker from the file path."""
    # Use relative path from Books dir
    try:
        return str(path.relative_to(BOOKS_DIR))
    except ValueError:
        return str(path)


def build_highlights_payload(book_data):
    # type: (dict) -> List[dict]
    """Build Readwise API payload from parsed book data."""
    payload = []  # type: List[dict]
    title = book_data["title"]
    author = book_data["author"]
    goodreads = book_data.get("goodreads", "")
    finished = book_data.get("finished", "")

    # Build highlighted_at from finished date
    highlighted_at = None  # type: Optional[str]
    if finished and finished != "None":
        try:
            dt = datetime.strptime(finished, "%Y-%m-%d")
            highlighted_at = dt.strftime("%Y-%m-%dT00:00:00+00:00")
        except ValueError:
            pass

    for idx, hl in enumerate(book_data["highlights"]):
        entry = {
            "text": hl["text"],
            "title": title,
            "author": author,
            "category": "books",
            "source_type": "obsidian_books",
            "location": idx + 1,
            "location_type": "order",
        }
        if hl.get("note"):
            entry["note"] = hl["note"]
        if goodreads:
            entry["source_url"] = goodreads
        if highlighted_at:
            entry["highlighted_at"] = highlighted_at

        payload.append(entry)

    return payload


def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description="Sync Obsidian book highlights to Readwise.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be uploaded; no API calls.")
    parser.add_argument("--book", type=str, metavar="TITLE",
                        help="Sync a single book by title (partial match).")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="Sync at most N books.")
    parser.add_argument("--reset", action="store_true",
                        help="Clear tracker and re-check everything.")
    args = parser.parse_args()

    dry_run = args.dry_run  # type: bool
    if dry_run:
        print("=== DRY RUN mode — no API calls ===\n")

    tracker = load_tracker()
    if args.reset:
        print("Resetting tracker...")
        tracker = {"uploaded": {}, "last_run": None}
        save_tracker(tracker)

    # Scan all books
    all_paths = scan_books()
    print(f"Found {len(all_paths)} book files.\n")

    # Filter by --book if specified
    if args.book:
        search = args.book.lower()
        all_paths = [p for p in all_paths if search in p.stem.lower()]
        print(f"Filtered to {len(all_paths)} matching '{args.book}'.\n")

    # Fetch existing Readwise books for dedup (skip in dry-run)
    existing_books = set()  # type: Set[Tuple[str, str]]
    if not dry_run:
        print("Fetching existing books from Readwise...")
        existing_books = get_existing_books()
        print(f"Found {len(existing_books)} existing books in Readwise.\n")

    uploaded_count = 0
    skipped_tracker = 0
    skipped_existing = 0
    skipped_empty = 0
    error_count = 0

    for path in all_paths:
        tracker_key = build_tracker_key(path)

        # Layer 1: local tracker
        if tracker_key in tracker["uploaded"]:
            skipped_tracker += 1
            continue

        # Parse book
        book_data = parse_book_md(path)
        if not book_data:
            skipped_empty += 1
            continue

        title = book_data["title"]
        author = book_data["author"]
        hl_count = len(book_data["highlights"])

        # Layer 2: Readwise existing books
        if not dry_run and (title.strip().lower(), author.strip().lower()) in existing_books:
            skipped_existing += 1
            if not args.book:  # Show skip reason when filtering by book
                continue
            # If user explicitly asked for this book, still show info but note it exists
            print(f"  NOTE: '{title}' already exists in Readwise, uploading anyway (dedup by content)")

        if dry_run:
            print(f"[would-upload] {title}")
            print(f"  Author: {author}")
            print(f"  Highlights: {hl_count}")
            for i, hl in enumerate(book_data["highlights"][:3]):
                preview = hl["text"][:80] + ("..." if len(hl["text"]) > 80 else "")
                print(f"  [{i+1}] {preview}")
                if hl.get("note"):
                    print(f"      Note: {hl['note'][:60]}")
            if hl_count > 3:
                print(f"  ... and {hl_count - 3} more")
            print()
            uploaded_count += 1
        else:
            # Build and upload
            payload = build_highlights_payload(book_data)
            try:
                print(f"Uploading: {title} ({hl_count} highlights)...", end=" ", flush=True)
                upload_highlights(payload)
                print("OK")

                # Record in tracker
                tracker["uploaded"][tracker_key] = {
                    "title": title,
                    "highlight_count": hl_count,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                }
                save_tracker(tracker)
                uploaded_count += 1

                # Rate limiting
                time.sleep(0.3)
            except Exception as exc:
                print(f"ERROR: {exc}")
                error_count += 1

        # Check limit
        if args.limit and uploaded_count >= args.limit:
            print(f"\nReached limit of {args.limit} books.")
            break

    # Update last_run
    if not dry_run:
        tracker["last_run"] = datetime.now(timezone.utc).isoformat()
        save_tracker(tracker)

    print(f"\nDone.")
    print(f"  Uploaded: {uploaded_count}")
    print(f"  Skipped (tracker): {skipped_tracker}")
    print(f"  Skipped (already in Readwise): {skipped_existing}")
    print(f"  Skipped (no highlights): {skipped_empty}")
    if error_count:
        print(f"  Errors: {error_count}")


if __name__ == "__main__":
    main()
