import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync_books_to_readwise import _detect_format, _parse_pdf_format

PDF_SECTION = """> think of onboarding as less of an interface problem and more of an interpersonal one. ^a3f2b1c8d4e5f607

> talk about them, not you. ^7c2e91f4a1b2c3d4

---
"""


def test_detects_pdf_format() -> None:
    assert _detect_format(PDF_SECTION) == "pdf"


def test_strips_blockquote_and_block_id() -> None:
    got = _parse_pdf_format(PDF_SECTION)

    assert got[0]["text"] == (
        "think of onboarding as less of an interface problem "
        "and more of an interpersonal one.")
    assert got[1]["text"] == "talk about them, not you."


def test_block_id_never_leaks_into_readwise() -> None:
    for hl in _parse_pdf_format(PDF_SECTION):
        assert "^" not in hl["text"]
        assert not hl["text"].startswith(">")


def test_wechat_bullet_format_still_detected() -> None:
    # 回归：别把老格式弄坏了
    wechat = "- 既然没有问题需要解决，动物是不必发展出好奇心和智力的。\n- 另一条摘记\n"

    assert _detect_format(wechat) == "bullet"


def test_plain_paragraph_format_still_detected() -> None:
    # 回归：没有 blockquote 的纯文本还是 paragraph
    plain = "这是一段摘记文字，足够长。\n\n这是另一段摘记文字，也足够长。\n"

    assert _detect_format(plain) == "paragraph"
