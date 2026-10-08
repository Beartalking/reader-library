"""Reading Note generation goes through claude_cli (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import claude_cli  # noqa: E402
import sync_reader  # noqa: E402

BOOK = {"title": "T", "author": "A", "category": "articles", "source_url": "https://x"}


def test_reading_note_uses_claude_cli(monkeypatch):
    seen = {}

    def fake(system, user, model="sonnet", timeout=300):
        seen.update(system=system, user=user, model=model)
        return "  a note  "

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_cli, "complete", fake)
    assert sync_reader.generate_reading_note(BOOK, "hl", "", dry_run=False) == "a note"
    assert seen["system"] is None
    assert seen["model"] == "claude-haiku-4-5-20251001"
    assert "hl" in seen["user"]


def test_reading_note_failure_placeholder(monkeypatch):
    def boom(*a, **k):
        raise claude_cli.ClaudeCLIError("usage limit")

    monkeypatch.setattr(claude_cli, "complete", boom)
    assert sync_reader.generate_reading_note(BOOK, "hl", "", dry_run=False) == "[生成失败，请手动补充]"
