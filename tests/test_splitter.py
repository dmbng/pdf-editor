"""
Unit tests for PDF Splitter.
"""

import pytest
from generate_samples import generate_sample_report
from pdf_editor.core.splitter import split_by_ranges, split_by_bookmarks, split_to_individual_pages
from pdf_editor.core.utils import validate_pdf


def test_split_by_ranges(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_dir = tmp_path / "split_ranges"

    files = split_by_ranges(doc_path, ["1-2", "3-4"], out_dir)
    assert len(files) == 2
    assert validate_pdf(files[0]) == 2
    assert validate_pdf(files[1]) == 2


def test_split_to_individual_pages(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_dir = tmp_path / "split_single"

    files = split_to_individual_pages(doc_path, out_dir)
    assert len(files) == 4
    for f in files:
        assert validate_pdf(f) == 1


def test_split_by_bookmarks(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_dir = tmp_path / "split_bookmarks"

    files = split_by_bookmarks(doc_path, out_dir)
    assert len(files) == 4
