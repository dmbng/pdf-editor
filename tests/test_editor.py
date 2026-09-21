"""
Unit tests for page-level editing (rotate, reorder, delete, extract, replace).
"""

import pytest
from generate_samples import generate_sample_report, generate_sample_invoice
from pdf_editor.core.editor import (
    rotate_pages,
    reorder_pages,
    delete_pages,
    extract_pages,
    replace_pages
)
from pdf_editor.core.utils import validate_pdf, InvalidPageRangeError


def test_rotate_pages(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "rotated.pdf"

    dest = rotate_pages(doc_path, out_path, page_angles=90, target_pages="1")
    assert dest.exists()
    assert validate_pdf(dest) == 4


def test_reorder_pages(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "reordered.pdf"

    dest = reorder_pages(doc_path, out_path, new_order=[4, 3, 2, 1])
    assert dest.exists()
    assert validate_pdf(dest) == 4


def test_delete_pages(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "deleted.pdf"

    dest = delete_pages(doc_path, out_path, pages_to_delete="2,4")
    assert dest.exists()
    assert validate_pdf(dest) == 2


def test_delete_all_pages_error(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "deleted_all.pdf"

    with pytest.raises(InvalidPageRangeError):
        delete_pages(doc_path, out_path, pages_to_delete="1-4")


def test_extract_pages(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "extracted.pdf"

    dest = extract_pages(doc_path, out_path, pages_to_extract="1,3")
    assert dest.exists()
    assert validate_pdf(dest) == 2


def test_replace_pages(tmp_path):
    main_pdf = generate_sample_report(tmp_path / "report.pdf")
    repl_pdf = generate_sample_invoice(tmp_path / "invoice.pdf")
    out_path = tmp_path / "replaced.pdf"

    dest = replace_pages(main_pdf, out_path, target_pages="2", replacement_pdf_path=repl_pdf, replacement_pages="1")
    assert dest.exists()
    assert validate_pdf(dest) == 4
