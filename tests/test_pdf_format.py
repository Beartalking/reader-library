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


BILINGUAL_SECTION = """> 如果人们难以阅读字体，他们会将这种困难感转移到文本本身的含义。
> *If people have trouble reading a font, they transfer that feeling to the meaning.* ^a3f2b1c8d4e5f607

> 现在的研究表明，「神奇」的数字是四。
> *Current research shows that the magical number is four.* ^b7e1c9a2f4d80513

---
"""

# 老书是连续无空行的紧凑排法，每行自带锚点。分组规则必须按锚点断句，
# 按空行断句会把这 3 行并成一条，丢掉后两条。
LEGACY_COMPACT_SECTION = """> first highlight from an older single language book ^1111111111111111
> second highlight from that same book ^2222222222222222
> third highlight from that same book ^3333333333333333
"""


def test_bilingual_entry_is_detected_as_pdf_format() -> None:
    assert _detect_format(BILINGUAL_SECTION) == "pdf"


def test_bilingual_entry_syncs_chinese_only() -> None:
    """Bear 要中文进 Readwise。逐行解析会把中英拆成两条分别传上去。"""
    got = _parse_pdf_format(BILINGUAL_SECTION)

    assert len(got) == 2
    assert got[0]["text"] == (
        "如果人们难以阅读字体，他们会将这种困难感转移到文本本身的含义。")
    assert got[1]["text"] == "现在的研究表明，「神奇」的数字是四。"
    assert all("If people have trouble" not in hl["text"] for hl in got)


def test_legacy_compact_section_keeps_every_highlight() -> None:
    got = _parse_pdf_format(LEGACY_COMPACT_SECTION)

    assert [hl["text"] for hl in got] == [
        "first highlight from an older single language book",
        "second highlight from that same book",
        "third highlight from that same book",
    ]


def test_entry_missing_its_block_id_is_not_silently_dropped() -> None:
    """手工编辑删掉锚点时，宁可当成一条传上去，也不要静默丢掉划线。"""
    orphan = "> a highlight whose block id got deleted by hand\n"

    got = _parse_pdf_format(orphan)

    assert [hl["text"] for hl in got] == [
        "a highlight whose block id got deleted by hand"]


def test_wechat_bullet_format_still_detected() -> None:
    # 回归：别把老格式弄坏了
    wechat = "- 既然没有问题需要解决，动物是不必发展出好奇心和智力的。\n- 另一条摘记\n"

    assert _detect_format(wechat) == "bullet"


def test_plain_paragraph_format_still_detected() -> None:
    # 回归：没有 blockquote 的纯文本还是 paragraph
    plain = "这是一段摘记文字，足够长。\n\n这是另一段摘记文字，也足够长。\n"

    assert _detect_format(plain) == "paragraph"
