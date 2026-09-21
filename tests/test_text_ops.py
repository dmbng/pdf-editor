"""
Unit tests for the interactive text editing engine.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import text_ops
from pdf_editor.core.fonts import SpanStyle
from pdf_editor.core.text_ops import TextSelection, Word
from pdf_editor.core.utils import TextEditError


def _body_selection(page, count=3, skip=0):
    """Selects words from the wrapped body paragraph of the test page."""
    words = text_ops.page_words(page)
    body = [word for word in words if word.rect.y0 > 130 and word.rect.y1 < 270]
    assert body, "the fixture should contain a wrapped body paragraph"
    block = body[0].block
    in_block = [word for word in body if word.block == block]
    return TextSelection(0, in_block[skip : skip + count])


def test_page_words_and_selection(paragraph_pdf):
    """Words are extracted and a rubber band selects the ones it touches."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        words = text_ops.page_words(page)
        assert len(words) > 20
        assert words[0].text == "Editing"

        selection = text_ops.select_words_in_rect(page, fitz.Rect(45, 70, 300, 95))
        assert "Editing" in selection.text
        assert not selection.is_empty
        assert selection.rects


def test_select_word_and_line(paragraph_pdf):
    """Double and triple click helpers select a word and a whole line."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        word = text_ops.page_words(page)[0]
        point = fitz.Point((word.rect.x0 + word.rect.x1) / 2, (word.rect.y0 + word.rect.y1) / 2)

        single = text_ops.select_word_at(page, point)
        assert single.text == word.text

        line = text_ops.select_line_at(page, point)
        assert len(line.words) >= len(single.words)
        assert single.text in line.text


def test_probe_style_reads_font_and_colour(paragraph_pdf):
    """The dominant style of a selection is reported back."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = text_ops.select_words_in_rect(page, fitz.Rect(45, 98, 300, 118))
        style = text_ops.probe_style(page, selection)
        assert style.bold is True
        assert style.size == pytest.approx(13.0, abs=0.5)
        assert style.color[0] > style.color[1]  # Reddish text.


def test_replace_with_reflow_moves_following_words(paragraph_pdf):
    """A longer replacement pushes the rest of the paragraph along."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=3)
        original = selection.text

        result = text_ops.replace_selection(
            page, selection, "A MUCH LONGER REPLACEMENT PHRASE INDEED"
        )
        text = page.get_text("text")

        assert result.reflowed is True
        assert "A MUCH LONGER REPLACEMENT PHRASE INDEED" in text
        assert original not in text
        assert "reflow" in text  # Tail of the paragraph survived.
        assert "second paragraph" in text  # Neighbouring block untouched.


def test_replace_keeps_font_family_and_size(paragraph_pdf):
    """The replacement inherits the style of the text it replaces."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=2)
        text_ops.replace_selection(page, selection, "Inherited")

        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if "Inherited" in span["text"]:
                        assert span["size"] == pytest.approx(11.0, abs=0.3)
                        assert "Helvetica" in span["font"]
                        return
        pytest.fail("the replacement text was not written to the page")


def test_replace_accepts_style_override(paragraph_pdf):
    """An explicit style overrides the detected one."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=2)
        style = SpanStyle(font="Courier", size=14.0, color=(0.0, 0.4, 0.0), flags=8)

        text_ops.replace_selection(page, selection, "Monospaced", style=style)
        spans = [
            span
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if "Monospaced" in span["text"]
        ]
        assert spans
        assert "Courier" in spans[0]["font"]
        assert spans[0]["size"] == pytest.approx(14.0, abs=0.3)


def test_delete_selection_closes_the_gap(paragraph_pdf):
    """Deleting words removes them and pulls the remaining text together."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=4)
        removed = selection.text

        result = text_ops.delete_selection(page, selection)
        text = page.get_text("text")

        assert removed not in text
        assert "reflow" in text
        assert result.new_text == ""
        assert "Deleted" in result.summary


def test_replace_without_reflow_keeps_neighbours_in_place(paragraph_pdf):
    """In-place editing leaves every other word exactly where it was."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=2)
        follower = [
            word for word in text_ops.page_words(page)
            if word.block == selection.words[0].block and word.rect.x0 > selection.bbox.x1
        ][0]
        before = follower.rect.x0

        text_ops.replace_selection(page, selection, "Short", reflow=False)

        after = [word for word in text_ops.page_words(page) if word.text == follower.text]
        assert after
        assert after[0].rect.x0 == pytest.approx(before, abs=0.6)


def test_long_replacement_is_scaled_down(paragraph_pdf):
    """Text that cannot fit is condensed instead of overflowing."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=2)
        result = text_ops.replace_selection(page, selection, "X" * 40, reflow=False)
        assert result.font_scale < 1.0
        assert result.warnings


def test_highlight_and_remove(paragraph_pdf):
    """Highlighting adds annotations that can be removed again."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=3)
        created = text_ops.highlight_selection(page, selection, (1.0, 0.9, 0.2))
        assert created == len(selection.rects)
        assert len(list(page.annots())) == created

        centre = fitz.Point(
            (selection.bbox.x0 + selection.bbox.x1) / 2,
            (selection.bbox.y0 + selection.bbox.y1) / 2,
        )
        assert text_ops.remove_highlights_at(page, centre) >= 1


def test_redact_selection_removes_text(paragraph_pdf):
    """Redaction deletes the characters for good."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=3)
        removed = selection.text

        assert text_ops.redact_selection(page, selection) == len(selection.rects)
        assert removed not in page.get_text("text")


def test_insert_text_box_wraps_and_scales():
    """A new text box wraps its content and shrinks when the box is small."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)

    used = text_ops.insert_text_box(
        page, fitz.Rect(20, 20, 200, 90), "Some inserted text that needs to wrap over lines.",
        style=SpanStyle(size=12.0),
    )
    assert used.y1 <= 90.5
    assert "inserted text" in page.get_text("text")
    assert len(page.get_text("text").strip().splitlines()) > 1
    doc.close()


def test_search_page_finds_matches(paragraph_pdf):
    """Searching returns one rectangle per hit."""
    with fitz.open(paragraph_pdf) as doc:
        hits = text_ops.search_page(doc[0], "paragraph")
        assert hits
        assert all(isinstance(rect, fitz.Rect) for rect in hits)
        assert text_ops.search_page(doc[0], "not-in-the-document") == []


def test_page_has_text_detects_empty_pages():
    """Pages without a text layer are reported as such."""
    doc = fitz.open()
    blank = doc.new_page()
    assert text_ops.page_has_text(blank) is False
    blank.insert_text(fitz.Point(40, 40), "now it has text")
    assert text_ops.page_has_text(blank) is True
    doc.close()


def test_editing_an_empty_selection_raises(paragraph_pdf):
    """Every editing entry point rejects an empty selection."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        empty = TextSelection(0, [])
        with pytest.raises(TextEditError):
            text_ops.replace_selection(page, empty, "text")
        with pytest.raises(TextEditError):
            text_ops.highlight_selection(page, empty)
        with pytest.raises(TextEditError):
            text_ops.redact_selection(page, empty)


def test_editing_a_page_without_text_raises():
    """Editing an image-only page reports a helpful error."""
    doc = fitz.open()
    page = doc.new_page()
    phantom = TextSelection(0, [Word(fitz.Rect(10, 10, 60, 24), "ghost", 0, 0, 0)])
    with pytest.raises(TextEditError):
        text_ops.replace_selection(page, phantom, "text")
    doc.close()


def test_insert_empty_text_box_raises():
    """An empty text box is rejected."""
    doc = fitz.open()
    page = doc.new_page()
    with pytest.raises(TextEditError):
        text_ops.insert_text_box(page, fitz.Rect(10, 10, 100, 40), "   ")
    doc.close()


def test_selection_helpers():
    """The selection value object exposes the geometry the UI needs."""
    words = [
        Word(fitz.Rect(10, 10, 40, 20), "one", 0, 0, 0),
        Word(fitz.Rect(45, 10, 70, 20), "two", 0, 0, 1),
        Word(fitz.Rect(10, 25, 40, 35), "three", 0, 1, 0),
        Word(fitz.Rect(10, 60, 40, 70), "other", 1, 0, 0),
    ]
    selection = TextSelection(0, words)
    assert selection.text == "one two three other"
    assert selection.blocks == [0, 1]
    assert len(selection.rects) == 3  # Three distinct lines.
    assert selection.bbox == fitz.Rect(10, 10, 70, 70)
    assert len(selection.limited_to_block(0).words) == 3
    assert TextSelection(0, []).is_empty is True


def test_estimate_text_width_scales_with_size():
    """The width helper reacts to the font size."""
    small = text_ops.estimate_text_width("Hello", SpanStyle(size=10))
    large = text_ops.estimate_text_width("Hello", SpanStyle(size=20))
    assert large > small > 0


def test_extract_block_and_find_block(paragraph_pdf):
    """Blocks can be located by number and by point."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=1)
        number = selection.block

        block = text_ops.extract_block(page, number)
        assert block is not None
        assert block.chars
        assert block.lines
        assert "reflow" in block.text

        found = text_ops.find_block_at(page, fitz.Point(*selection.words[0].rect.tl))
        assert found is not None
        assert text_ops.extract_block(page, 999) is None


def test_edited_text_stays_on_one_line(paragraph_pdf):
    """Words written by an edit keep forming normal, searchable lines."""
    with fitz.open(paragraph_pdf) as doc:
        page = doc[0]
        selection = _body_selection(page, count=2)
        text_ops.replace_selection(page, selection, "Rewritten opening")

        line = next(
            (line for line in page.get_text("text").splitlines() if "Rewritten opening" in line),
            "",
        )
        assert len(line.split()) > 3  # Neighbouring words share the line.
        assert page.search_for("Rewritten opening")


def test_alignment_detection(paragraph_pdf):
    """Justified paragraphs are recognised, ragged ones are not."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 60, 400, 300),
        "A justified paragraph repeated several times so that at least two body "
        "lines reach the right margin of the column. " * 2,
        fontsize=11,
        fontname="helv",
        align=text_ops.ALIGN_JUSTIFY,
    )
    justified = text_ops.extract_block(page, 0)
    assert text_ops._detect_alignment(justified) == text_ops.ALIGN_JUSTIFY
    doc.close()

    with fitz.open(paragraph_pdf) as source:
        block = text_ops.extract_block(source[0], _body_selection(source[0], count=1).block)
        assert text_ops._detect_alignment(block) in (
            text_ops.ALIGN_LEFT,
            text_ops.ALIGN_JUSTIFY,
        )
