"""
Unit tests for text search, replace, overlay, and redaction.
"""

import pytest
import fitz
from generate_samples import generate_sample_report
from pdf_editor.core.text_editor import replace_text, add_text, redact_text, search_text


def test_search_text(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    results = search_text(doc_path, "Technical Report")
    assert len(results) >= 1
    assert results[0]["page"] == 1


def test_replace_text(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "replaced_text.pdf"

    count = replace_text(doc_path, out_path, old_text="DRAFT", new_text="APPROVED", pages="1")
    assert count >= 1

    # Verify original text is gone and replacement is found
    search_old = search_text(out_path, "DRAFT")
    search_new = search_text(out_path, "APPROVED")
    assert len(search_old) == 0
    assert len(search_new) >= 1


def test_add_text(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "added_text.pdf"

    add_text(doc_path, out_path, text="CONFIDENTIAL STAMP", page_num=1, x=100.0, y=100.0, font_size=16.0)
    
    results = search_text(out_path, "CONFIDENTIAL STAMP")
    assert len(results) >= 1


def test_redact_text(tmp_path):
    doc_path = generate_sample_report(tmp_path / "report.pdf")
    out_path = tmp_path / "redacted.pdf"

    count = redact_text(doc_path, out_path, target_text="SECRET-PASSPHRASE-42", pages="2")
    assert count >= 1

    # Verify redacted text cannot be found anymore
    results = search_text(out_path, "SECRET-PASSPHRASE-42")
    assert len(results) == 0
