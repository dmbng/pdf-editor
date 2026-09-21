"""
Unit tests for core utilities, range parser, and PDF validator.
"""

import pytest
from pathlib import Path
from pdf_editor.core.utils import (
    parse_page_ranges,
    validate_pdf,
    InvalidPageRangeError,
    FileNotFoundPDFError,
    CorruptedPDFError
)


def test_parse_page_ranges_valid():
    assert parse_page_ranges("1-3, 5", 5) == [0, 1, 2, 4]
    assert parse_page_ranges("all", 4) == [0, 1, 2, 3]
    assert parse_page_ranges("even", 6) == [1, 3, 5]
    assert parse_page_ranges("odd", 6) == [0, 2, 4]
    assert parse_page_ranges("2-end", 4) == [1, 2, 3]
    assert parse_page_ranges("1-2, 2-3", 4) == [0, 1, 2]  # Deduplication


def test_parse_page_ranges_invalid():
    with pytest.raises(InvalidPageRangeError):
        parse_page_ranges("10", 5)

    with pytest.raises(InvalidPageRangeError):
        parse_page_ranges("5-2", 5)

    with pytest.raises(InvalidPageRangeError):
        parse_page_ranges("0", 5)

    with pytest.raises(InvalidPageRangeError):
        parse_page_ranges("abc", 5)


def test_validate_pdf_not_found():
    with pytest.raises(FileNotFoundPDFError):
        validate_pdf("non_existent_file.pdf")


def test_validate_pdf_corrupted(tmp_path):
    bad_file = tmp_path / "bad.pdf"
    bad_file.write_text("Not a PDF file content")

    with pytest.raises(CorruptedPDFError):
        validate_pdf(bad_file)
