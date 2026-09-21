"""
Unit tests for in-memory page management (delete, add, duplicate, merge, ...).
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import page_ops
from pdf_editor.core.utils import (
    InvalidPageRangeError,
    PDFEditorError,
    UnsupportedOperationError,
)


def _page_texts(document):
    """Returns the first line of text of every page."""
    return [page.get_text("text").strip().splitlines()[0] if page.get_text("text").strip() else ""
            for page in document]


def test_delete_pages(simple_pdf):
    """Deleting removes exactly the requested pages."""
    with fitz.open(simple_pdf) as doc:
        assert page_ops.delete_pages(doc, [1, 2]) == 2
        assert doc.page_count == 2
        assert _page_texts(doc) == ["Page number 1", "Page number 4"]


def test_delete_all_pages_is_refused(simple_pdf):
    """A document may never lose its last page."""
    with fitz.open(simple_pdf) as doc:
        with pytest.raises(UnsupportedOperationError):
            page_ops.delete_pages(doc, [0, 1, 2, 3])
        assert doc.page_count == 4


def test_delete_rejects_bad_indices(simple_pdf):
    """Out-of-range and empty selections are rejected."""
    with fitz.open(simple_pdf) as doc:
        with pytest.raises(InvalidPageRangeError):
            page_ops.delete_pages(doc, [9])
        with pytest.raises(InvalidPageRangeError):
            page_ops.delete_pages(doc, [])


def test_insert_blank_page_named_size(simple_pdf):
    """A blank page can use a named paper size and be inserted anywhere."""
    with fitz.open(simple_pdf) as doc:
        index = page_ops.insert_blank_page(doc, 1, paper="A4")
        assert index == 1
        assert doc.page_count == 5
        assert doc[1].rect.width == pytest.approx(595, abs=1)
        assert doc[1].get_text("text").strip() == ""


def test_insert_blank_page_landscape_and_match(simple_pdf):
    """Landscape swaps the axes and 'match' copies an existing page size."""
    with fitz.open(simple_pdf) as doc:
        page_ops.insert_blank_page(doc, 0, paper="A4", landscape=True)
        assert doc[0].rect.width > doc[0].rect.height

        page_ops.insert_blank_page(doc, None, match_page=1)
        assert doc[-1].rect.width == pytest.approx(doc[1].rect.width)
        assert doc[-1].rect.height == pytest.approx(doc[1].rect.height)


def test_insert_blank_page_validates_input(simple_pdf):
    """Unknown paper sizes and impossible positions are reported."""
    with fitz.open(simple_pdf) as doc:
        with pytest.raises(PDFEditorError):
            page_ops.insert_blank_page(doc, 0, paper="Napkin")
        with pytest.raises(InvalidPageRangeError):
            page_ops.insert_blank_page(doc, 99)
        with pytest.raises(InvalidPageRangeError):
            page_ops.insert_blank_page(doc, 0, match_page=42)


def test_duplicate_pages(simple_pdf):
    """Each copy is placed straight after its original."""
    with fitz.open(simple_pdf) as doc:
        created = page_ops.duplicate_pages(doc, [0, 2])
        assert created == [1, 4]
        assert doc.page_count == 6
        assert _page_texts(doc) == [
            "Page number 1",
            "Page number 1",
            "Page number 2",
            "Page number 3",
            "Page number 3",
            "Page number 4",
        ]


def test_move_page_in_both_directions(simple_pdf):
    """Pages can be moved forwards and backwards."""
    with fitz.open(simple_pdf) as doc:
        page_ops.move_page(doc, 0, 2)
        assert _page_texts(doc)[2] == "Page number 1"

        page_ops.move_page(doc, 3, 0)
        assert _page_texts(doc)[0] == "Page number 4"

        assert page_ops.move_page(doc, 1, 1) == 1
        with pytest.raises(InvalidPageRangeError):
            page_ops.move_page(doc, 0, 12)


def test_move_page_to_the_very_end(simple_pdf):
    """Moving a page behind the last one is allowed, including in short files."""
    with fitz.open(simple_pdf) as doc:
        page_ops.move_page(doc, 0, 3)
        assert _page_texts(doc)[3] == "Page number 1"

        page_ops.delete_pages(doc, [0, 1])
        assert doc.page_count == 2
        page_ops.move_page(doc, 0, 1)
        assert _page_texts(doc) == ["Page number 1", "Page number 4"]


def test_reorder_pages(simple_pdf):
    """A full permutation reorders the document."""
    with fitz.open(simple_pdf) as doc:
        page_ops.reorder_pages(doc, [3, 2, 1, 0])
        assert _page_texts(doc) == [
            "Page number 4",
            "Page number 3",
            "Page number 2",
            "Page number 1",
        ]
        with pytest.raises(InvalidPageRangeError):
            page_ops.reorder_pages(doc, [0, 1])


def test_rotate_pages_relative_and_absolute(simple_pdf):
    """Rotation adds to the current angle unless 'absolute' is requested."""
    with fitz.open(simple_pdf) as doc:
        page_ops.rotate_pages(doc, [0], 90)
        assert doc[0].rotation == 90
        page_ops.rotate_pages(doc, [0], 90)
        assert doc[0].rotation == 180
        page_ops.rotate_pages(doc, [0], 270, absolute=True)
        assert doc[0].rotation == 270

        with pytest.raises(InvalidPageRangeError):
            page_ops.rotate_pages(doc, [0], 45)


def test_merge_documents_appends_and_keeps_bookmarks(simple_pdf, other_pdf):
    """Merging adds the pages and a bookmark per source file."""
    with fitz.open(simple_pdf) as doc:
        added = page_ops.merge_documents(doc, [other_pdf])
        assert added == 2
        assert doc.page_count == 6
        assert _page_texts(doc)[4] == "Merged page 1"

        titles = [entry[1] for entry in doc.get_toc(simple=True)]
        assert other_pdf.stem in titles
        assert "Merged start" in titles


def test_merge_documents_at_position(simple_pdf, other_pdf):
    """Merged pages land exactly at the requested index."""
    with fitz.open(simple_pdf) as doc:
        page_ops.merge_documents(doc, [other_pdf], at=1, add_bookmarks=False)
        assert _page_texts(doc)[1] == "Merged page 1"
        assert _page_texts(doc)[3] == "Page number 2"


def test_merge_documents_reports_bad_sources(simple_pdf, broken_pdf, tmp_path):
    """Corrupt, missing and empty inputs produce clear errors."""
    with fitz.open(simple_pdf) as doc:
        with pytest.raises(PDFEditorError):
            page_ops.merge_documents(doc, [broken_pdf])
        with pytest.raises(PDFEditorError):
            page_ops.merge_documents(doc, [tmp_path / "missing.pdf"])
        with pytest.raises(PDFEditorError):
            page_ops.merge_documents(doc, [])
        assert doc.page_count == 4


def test_extract_pages_writes_new_file(simple_pdf, tmp_path):
    """Extraction writes the chosen pages into a fresh document."""
    target = tmp_path / "out" / "extract.pdf"
    with fitz.open(simple_pdf) as doc:
        written = page_ops.extract_pages(doc, [0, 3], target)
        assert doc.page_count == 4  # Source untouched.

    assert written.exists()
    with fitz.open(written) as extracted:
        assert extracted.page_count == 2
        assert _page_texts(extracted) == ["Page number 1", "Page number 4"]


def test_page_summary(simple_pdf):
    """The summary reports the facts shown in the sidebar."""
    with fitz.open(simple_pdf) as doc:
        summary = page_ops.page_summary(doc, 0)
        assert summary["number"] == 1
        assert summary["has_text"] is True
        assert summary["rotation"] == 0
        assert summary["width"] == pytest.approx(400, abs=1)

        with pytest.raises(InvalidPageRangeError):
            page_ops.page_summary(doc, 12)


def test_paper_sizes_are_available():
    """The dialog's paper size table is populated."""
    assert "A4" in page_ops.PAPER_SIZES
    assert page_ops.PAPER_SIZES["A4"][1] > page_ops.PAPER_SIZES["A4"][0]
