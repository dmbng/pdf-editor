"""
Unit tests for PDF Merger.
"""

import pytest
from generate_samples import generate_sample_report, generate_sample_invoice
from pdf_editor.core.merger import merge_pdfs
from pdf_editor.core.utils import validate_pdf, PDFEditorError


def test_merge_pdfs(tmp_path):
    f1 = generate_sample_report(tmp_path / "report.pdf")
    f2 = generate_sample_invoice(tmp_path / "invoice.pdf")
    output = tmp_path / "merged_output.pdf"

    dest = merge_pdfs([f1, f2], output, add_bookmarks=True)
    assert dest.exists()
    
    total_pages = validate_pdf(dest)
    # report.pdf has 4 pages, invoice.pdf has 1 page = 5 pages total
    assert total_pages == 5


def test_merge_insufficient_files():
    with pytest.raises(PDFEditorError):
        merge_pdfs(["single.pdf"], "output.pdf")
