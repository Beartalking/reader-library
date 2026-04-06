#!/usr/bin/env python3
"""
Add a new book to Obsidian Reader Library.

Usage:
    python3 add_book.py "The E-Myth Revisited"
    python3 add_book.py "Atomic Habits" --author "James Clear"
    python3 add_book.py "Some Book" --year 2025
    python3 add_book.py "Some Book" --force   # overwrite if exists
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from slugify import slugify

VAULT_ROOT = Path(
    "/Users/bearliu/Library/Mobile Documents/iCloud~md~obsidian/Documents/Bear Vault"
)
BOOKS_DIR = VAULT_ROOT / "Books"
ASSETS_DIR = VAULT_ROOT / "_assets" / "books"

GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
DOUBAN_SUGGEST_API = "https://book.douban.com/j/subject_suggest"
DOUBAN_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CATEGORY_MAP = [
    (r"^Business|^Economics", "Business"),
    (r"^Fiction|^Comics|^Graphic", "Fiction"),
    (r"^Design|^Architecture", "Design"),
    (r"^Psychology|^Self-Help|^Self Help", "Psychology"),
    (r"^Biography|^Autobiography|^Memoir", "Biography"),
    (r"^Science|^Technology|^Computers|^Programming", "Technology"),
    (r"^History", "History"),
    (r"^Philosophy", "Philosophy"),
    (r"^Health|^Medical|^Fitness", "Health"),
]


def map_category(raw_categories: list[str]) -> str:
    for cat in raw_categories:
        for pattern, label in CATEGORY_MAP:
            if re.match(pattern, cat, re.IGNORECASE):
                return label
    if raw_categories:
        first_word = raw_categories[0].split()[0]
        return first_word
    return ""


def search_google_books(title: str, author: str = "") -> list[dict]:
    query = f"intitle:{title}"
    if author:
        query += f"+inauthor:{author}"
    params = {"q": query, "maxResults": 5, "printType": "books"}
    try:
        resp = requests.get(GOOGLE_BOOKS_API, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [warn] Google Books API error: {e}")
        return []
    return data.get("items", [])


def pick_best_match(items: list[dict], title: str) -> dict | None:
    if not items:
        return None
    title_lower = title.lower()
    for item in items:
        info = item.get("volumeInfo", {})
        if title_lower in info.get("title", "").lower():
            return item
    return items[0]


def search_douban(title: str, retries: int = 3) -> dict | None:
    """Fallback: search Douban Books suggest API. Returns dict with
    title, author, cover_url, url on success, else None. Retries on
    timeout because Douban's suggest endpoint is flaky."""
    items = None
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                DOUBAN_SUGGEST_API,
                params={"q": title},
                headers={"User-Agent": DOUBAN_UA, "Referer": "https://book.douban.com/"},
                timeout=30,
            )
            resp.raise_for_status()
            items = resp.json()
            break
        except Exception as e:
            last_err = e
            print(f"  [warn] Douban attempt {attempt}/{retries} failed: {e}")
    if items is None:
        print(f"  [warn] Douban gave up after {retries} attempts: {last_err}")
        return None
    if not items:
        return None
    title_lower = title.lower()
    best = None
    for item in items:
        if title_lower in item.get("title", "").lower():
            best = item
            break
    best = best or items[0]
    cover = best.get("pic", "")
    # Upgrade small cover to large: /subject/s/ -> /subject/l/
    if cover:
        cover = cover.replace("/subject/s/", "/subject/l/")
    return {
        "title": best.get("title", title),
        "author": best.get("author_name", ""),
        "cover_url": cover,
        "url": best.get("url", ""),
    }


def download_cover(url: str, dest: Path, referer: str = "") -> bool:
    try:
        url = url.replace("zoom=1", "zoom=2").replace("http://", "https://")
        headers = {"User-Agent": DOUBAN_UA}
        if referer:
            headers["Referer"] = referer
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"  [warn] Cover download failed: {e}")
        return False


def build_file_content(
    title: str,
    author: str,
    category: str,
    google_books_id: str,
    slug: str,
    goodreads: str = "",
    finished: str = "",
    book_format: str = "",
    status: str = "Reading",
    notes: str = "",
    highlights: str = "",
    summary: str = "",
) -> str:
    finished_line = finished if finished else ""
    format_line = book_format if book_format else ""
    goodreads_line = goodreads if goodreads else ""

    content = f"""---
title: "{title}"
author: "{author}"
finished: {finished_line}
category: {category}
format: {format_line}
status: {status}
rating:
goodreads: {goodreads_line}
google_books_id: {google_books_id}
notion_id:
en_review: false
zh_review: false
cover: "[[{slug}.jpg]]"
---

![[{slug}.jpg]]

## Summary

{summary if summary else ""}
---

## My Notes

{notes if notes else ""}

---

## Highlights

{highlights if highlights else ""}

---
"""
    return content


def safe_title_filename(title: str) -> str:
    safe = title.replace("/", " & ").replace(":", " —").strip(". ")
    return safe + ".md"


def create_book(
    title: str,
    author: str = "",
    year: int = None,
    force: bool = False,
    finished: str = "",
    book_format: str = "",
    status: str = "Reading",
    notes: str = "",
    highlights: str = "",
    summary: str = "",
) -> tuple[Path, bool]:
    if year is None:
        year = datetime.now().year

    slug = slugify(title, separator="-")
    year_dir = BOOKS_DIR / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)

    md_path = year_dir / safe_title_filename(title)

    if md_path.exists() and not force:
        print(f"  [skip] File already exists: {md_path}")
        print("         Use --force to overwrite.")
        return md_path, False

    print(f"Searching Google Books for: {title!r}...")
    items = search_google_books(title, author)

    best = pick_best_match(items, title)
    book_author = author
    book_title = title
    category = ""
    google_books_id = ""
    cover_url = ""
    cover_referer = ""

    if best:
        info = best.get("volumeInfo", {})
        book_title = info.get("title", title)
        authors = info.get("authors", [])
        if not book_author and authors:
            book_author = ", ".join(authors)
        categories = info.get("categories", [])
        category = map_category(categories)
        google_books_id = best.get("id", "")
        image_links = info.get("imageLinks", {})
        cover_url = image_links.get("thumbnail", image_links.get("smallThumbnail", ""))
        print(f"  Found: {book_title!r} by {book_author!r} [{category}]")
    else:
        print(f"  [warn] Google Books returned nothing. Trying Douban...")
        douban = search_douban(title)
        if douban and douban.get("cover_url"):
            book_title = douban["title"]
            if not book_author and douban.get("author"):
                book_author = douban["author"]
            cover_url = douban["cover_url"]
            cover_referer = "https://book.douban.com/"
            print(f"  Found on Douban: {book_title!r} by {book_author!r}")
        else:
            print(f"  [warn] No results found for {title!r}. Creating file with empty frontmatter.")

    cover_saved = False
    if cover_url:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        cover_path = ASSETS_DIR / f"{slug}.jpg"
        cover_saved = download_cover(cover_url, cover_path, referer=cover_referer)
        if cover_saved:
            print(f"  Cover saved: {cover_path}")

    content = build_file_content(
        title=book_title,
        author=book_author,
        category=category,
        google_books_id=google_books_id,
        slug=slug,
        finished=finished,
        book_format=book_format,
        status=status,
        notes=notes,
        highlights=highlights,
        summary=summary,
    )

    md_path.write_text(content, encoding="utf-8")
    return md_path, True


def main():
    parser = argparse.ArgumentParser(description="Add a new book to Obsidian Reader Library")
    parser.add_argument("title", help="Book title")
    parser.add_argument("--author", default="", help="Author name (optional)")
    parser.add_argument("--year", type=int, default=None, help="Year finished (default: current year)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file")
    args = parser.parse_args()

    path, created = create_book(
        title=args.title,
        author=args.author,
        year=args.year,
        force=args.force,
    )

    if created:
        print(f"\nCreated: {path}")
        print("\nNext steps:")
        print("  1. Fill in rating (manually)")
        print("  2. Run: python3 sync_books_to_readwise.py --book \"TITLE\" (sync highlights to Readwise)")


if __name__ == "__main__":
    main()
