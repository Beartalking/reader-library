#!/usr/bin/env python3
"""
Goodreads -> Obsidian book library sync.

Since 2026-10-12 Goodreads is the source of truth for a book's rating, its
finished date and (for new entries) its cover. Notes and highlights stay in
Obsidian and are never touched here.

Reads the public RSS feeds of the `read` and `currently-reading` shelves
(the Goodreads API has been closed to new keys since 2020; the RSS needs no
login). For every Goodreads book:

- matched to an existing entry (by goodreads URL, then by title):
  rating and finished are overwritten with Goodreads values when they differ,
  status becomes Read for the read shelf, an empty goodreads field is filled,
  and a cover is downloaded only if the entry has none.
- not matched and added on/after the cutoff: a new entry is created in
  40_Books/<year>/ with Goodreads metadata and cover.
- not matched and older than the cutoff: reported only (those are old books
  that were never in the library, e.g. picture books with long titles).

`started` comes from the date a book was put on the currently-reading shelf,
recorded only for books shelved on/after the cutoff. The RSS has no real
"date started" field and the HTML pages that do need a login.

Usage:
    python3 sync_goodreads.py --dry-run     # show what would change
    python3 sync_goodreads.py               # apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from slugify import slugify

from add_book import ASSETS_DIR, BOOKS_DIR, build_file_content, safe_title_filename

USER_ID = "50582617"
RSS_URL = "https://www.goodreads.com/review/list_rss/{uid}?shelf={shelf}&page={page}"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
TRACKER = Path(__file__).parent / "goodreads_sync.json"
# Last Goodreads activity before this sync existed was 2026-04-28; everything
# on Goodreads up to then is history that the Notion sync already brought in.
DEFAULT_CUTOFF = "2026-04-29"


# ── Goodreads ────────────────────────────────────────────────────────────────

def fetch_shelf(shelf: str) -> list[dict]:
    items, page = [], 1
    while True:
        resp = requests.get(
            RSS_URL.format(uid=USER_ID, shelf=shelf, page=page),
            headers={"User-Agent": UA}, timeout=20,
        )
        resp.raise_for_status()
        page_items = list(ET.fromstring(resp.content).iter("item"))
        if not page_items:
            return items
        for it in page_items:
            g = lambda k: (it.findtext(k) or "").strip()
            items.append({
                "shelf": shelf,
                "book_id": g("book_id"),
                "title": g("title"),
                "author": g("author_name"),
                "rating": int(g("user_rating") or 0),
                "read_at": to_date(g("user_read_at")),
                "added": to_date(g("user_date_added")),
                "cover": full_size_cover(g("book_large_image_url")),
            })
        page += 1


def to_date(rfc822: str) -> str:
    if not rfc822:
        return ""
    return parsedate_to_datetime(rfc822).date().isoformat()


def full_size_cover(url: str) -> str:
    # ..._SX318_.jpg -> ....jpg gives the original upload. Goodreads serves a
    # "nophoto" placeholder when there is no cover; skip those.
    if not url or "nophoto" in url:
        return ""
    return re.sub(r"\._[A-Z]{2}\d+_(?=\.)", "", url)


# ── Library ──────────────────────────────────────────────────────────────────

def norm(title: str, short: bool = False) -> str:
    t = unicodedata.normalize("NFKC", title).lower()
    t = re.sub(r"\s*[\(（](chinese edition|[^)）]*edition)[\)）]", "", t)
    if short:
        t = re.sub(r"(\s-\s|[\(（:：—]).*", "", t)  # drop subtitles / "- Author"
    return re.sub(r"[\W_]+", "", t)


def split_frontmatter(text: str) -> tuple[list[str], str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    return text[4:end].split("\n"), text[end:]


def fm_get(lines: list[str], key: str) -> str:
    """Value of `key`, joining YAML list items (`rating:\\n  - ⭐⭐⭐`)."""
    for i, line in enumerate(lines):
        if line.startswith(key + ":"):
            value = line[len(key) + 1:].strip().strip('"')
            j = i + 1
            while not value and j < len(lines) and lines[j].startswith("  "):
                value = lines[j].strip().lstrip("- ").strip('"')
                j += 1
            return value
    return ""


def fm_set(lines: list[str], key: str, value: str, after: str = "") -> list[str]:
    """Replace the whole `key` block (incl. list items) with one scalar line."""
    out, i, done = [], 0, False
    while i < len(lines):
        if lines[i].startswith(key + ":"):
            out.append(f"{key}: {value}")
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                i += 1
            done = True
            continue
        out.append(lines[i])
        if after and lines[i].startswith(after + ":") and not done:
            # insert right after the `after` block, once
            k = i + 1
            while k < len(lines) and lines[k].startswith("  "):
                out.append(lines[k])
                k += 1
            if not any(l.startswith(key + ":") for l in lines):
                out.append(f"{key}: {value}")
                done = True
            i = k
            continue
        i += 1
    if not done:
        out.append(f"{key}: {value}")
    return out


def load_library() -> tuple[dict, dict, dict]:
    by_id, by_title, by_short = {}, {}, {}
    for path in sorted(BOOKS_DIR.rglob("*.md")):
        if "Research" in path.parts:
            continue
        parts = split_frontmatter(path.read_text(errors="ignore"))
        if not parts:
            continue
        lines = parts[0]
        m = re.search(r"/book/show/(\d+)", fm_get(lines, "goodreads"))
        if m:
            by_id.setdefault(m.group(1), path)
        for t in (fm_get(lines, "title"), fm_get(lines, "title_en"), path.stem):
            if t and norm(t):
                by_title.setdefault(norm(t), path)
                by_short.setdefault(norm(t, short=True), set()).add(path)
    # a short title shared by several entries (局部：…) is ambiguous: unusable
    by_short = {k: next(iter(v)) for k, v in by_short.items() if k and len(v) == 1}
    return by_id, by_title, by_short


def stars(n: int) -> str:
    return "⭐" * n


def cover_exists(lines: list[str]) -> bool:
    m = re.search(r"\[\[(.+?)\]\]", fm_get(lines, "cover"))
    return bool(m) and any(BOOKS_DIR.parent.rglob(m.group(1)))


def download(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception as e:
        print(f"    [warn] cover download failed: {e}")
        return False


# ── Sync ─────────────────────────────────────────────────────────────────────

def update_entry(path: Path, gr: dict, cutoff: str, dry: bool) -> list[str]:
    text = path.read_text()
    lines, rest = split_frontmatter(text)
    changes = []

    def setv(key, new, after=""):
        nonlocal lines
        old = fm_get(lines, key)
        if old != new:
            lines = fm_set(lines, key, new, after)
            changes.append(f"{key}: {old or '∅'} → {new}")

    if gr["rating"]:
        setv("rating", stars(gr["rating"]))
    if gr["shelf"] == "read":
        if gr["read_at"]:
            setv("finished", gr["read_at"])
        setv("status", "Read")
    elif gr["added"] >= cutoff and not fm_get(lines, "started"):
        setv("started", gr["added"], after="finished")
    if not fm_get(lines, "goodreads"):
        setv("goodreads", f"https://www.goodreads.com/book/show/{gr['book_id']}")

    if gr["cover"] and not cover_exists(lines):
        slug = slugify(fm_get(lines, "title") or path.stem, separator="-")
        changes.append(f"cover ← Goodreads ({slug}.jpg)")
        if not dry and download(gr["cover"], ASSETS_DIR / f"{slug}.jpg"):
            lines = fm_set(lines, "cover", f'"[[{slug}.jpg]]"')

    if changes and not dry:
        path.write_text("---\n" + "\n".join(lines) + rest)
    return changes


def create_entry(gr: dict, dry: bool) -> Path:
    year = (gr["read_at"] or datetime.now().date().isoformat())[:4]
    path = BOOKS_DIR / year / safe_title_filename(gr["title"])
    if dry:
        return path
    slug = slugify(gr["title"], separator="-")
    content = build_file_content(
        title=gr["title"].replace('"', "'"),
        author=gr["author"].replace('"', "'"),
        category="",
        google_books_id="",
        slug=slug,
        goodreads=f"https://www.goodreads.com/book/show/{gr['book_id']}",
        finished=gr["read_at"] if gr["shelf"] == "read" else "",
        status="Read" if gr["shelf"] == "read" else "Reading",
    )
    lines, rest = split_frontmatter(content)
    if gr["rating"]:
        lines = fm_set(lines, "rating", stars(gr["rating"]))
    if gr["shelf"] == "currently-reading":
        lines = fm_set(lines, "started", gr["added"], after="finished")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(lines) + rest)
    if gr["cover"]:
        download(gr["cover"], ASSETS_DIR / f"{slug}.jpg")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cutoff", help=f"create entries only for books added on/after this date (default: tracker, else {DEFAULT_CUTOFF})")
    args = ap.parse_args()

    tracker = json.loads(TRACKER.read_text()) if TRACKER.exists() else {}
    cutoff = args.cutoff or tracker.get("cutoff", DEFAULT_CUTOFF)

    shelves = fetch_shelf("read") + fetch_shelf("currently-reading")
    by_id, by_title, by_short = load_library()
    print(f"Goodreads: {len(shelves)} books · cutoff {cutoff} · {'DRY RUN' if args.dry_run else 'APPLY'}\n")

    updated = created = 0
    old_unmatched = []
    seen = set()
    # Two Goodreads editions of one book (控糖革命) map to the same entry; the
    # most recently read one wins, the other is skipped instead of flip-flopping.
    shelves.sort(key=lambda gr: (gr["shelf"] == "read", gr["read_at"]), reverse=True)
    for gr in shelves:
        path = (by_id.get(gr["book_id"]) or by_title.get(norm(gr["title"]))
                or by_short.get(norm(gr["title"], short=True)))
        if path in seen:
            continue
        if path:
            seen.add(path)
            changes = update_entry(path, gr, cutoff, args.dry_run)
            if changes:
                updated += 1
                print(f"[update] {path.relative_to(BOOKS_DIR)}")
                for c in changes:
                    print(f"    {c}")
        elif max(gr["added"], gr["read_at"]) >= cutoff:
            created += 1
            print(f"[create] {create_entry(gr, args.dry_run).relative_to(BOOKS_DIR)}  ({gr['shelf']}, {stars(gr['rating']) or 'no rating'})")
        else:
            old_unmatched.append(gr)

    if old_unmatched:
        print(f"\n[not in library, before cutoff — left alone] {len(old_unmatched)}")
        for gr in old_unmatched:
            print(f"    {gr['title'][:70]} | {gr['author']}")

    print(f"\nDone. updated {updated} · created {created} · old unmatched {len(old_unmatched)}")
    if not args.dry_run:
        tracker.update(cutoff=cutoff, last_run=datetime.now(timezone.utc).isoformat())
        TRACKER.write_text(json.dumps(tracker, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
