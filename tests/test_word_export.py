"""
Unit tests for the Word (.docx) export.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import word_export
from pdf_editor.core.utils import PDFEditorError, UnsupportedOperationError

docx = pytest.importorskip("docx", reason="python-docx is not installed")


def read_docx_text(path) -> str:
    """Returns the concatenated text of a .docx file."""
    document = docx.Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def count_pictures(path) -> int:
    """Counts the images embedded in a .docx file."""
    document = docx.Document(str(path))
    return sum(1 for part in document.part.package.parts if "image" in part.content_type)


def test_available_methods_reports_backends():
    """At least the text backend is available once python-docx is installed."""
    methods = word_export.available_methods()
    assert "text" in methods
    assert word_export.text_backend_available() is True


@pytest.mark.parametrize("method", ["layout", "text"])
def test_export_writes_readable_document(paragraph_pdf, tmp_path, method):
    """Both backends produce a document containing the PDF text."""
    if method not in word_export.available_methods():
        pytest.skip(f"the '{method}' backend is not installed")

    target = tmp_path / "exported.docx"
    report = word_export.export_to_docx(paragraph_pdf, target, method=method)

    assert report.destination.exists()
    assert report.destination.stat().st_size > 0
    assert report.pages == 2
    assert report.method == method

    text = read_docx_text(target)
    assert "Editing Engine Test Page" in text
    assert "Second page heading" in text


def test_export_selected_pages_only(paragraph_pdf, tmp_path):
    """A page selection limits what ends up in the document."""
    target = tmp_path / "page_two.docx"
    report = word_export.export_to_docx(paragraph_pdf, target, pages=[1], method="text")

    assert report.pages == 1
    text = read_docx_text(target)
    assert "Second page heading" in text
    assert "Editing Engine Test Page" not in text


def test_export_keeps_images(png_file, tmp_path):
    """Pictures on the page are carried into the Word document."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 60), "Picture below", fontsize=14)
    page.insert_image(fitz.Rect(50, 90, 250, 190), filename=str(png_file))
    source = tmp_path / "with_image.pdf"
    doc.save(str(source))
    doc.close()

    target = tmp_path / "with_image.docx"
    word_export.export_to_docx(source, target, method="text")

    assert "Picture below" in read_docx_text(target)
    assert count_pictures(target) >= 1


def test_export_preserves_bold_and_size(paragraph_pdf, tmp_path):
    """Character formatting survives the text backend."""
    target = tmp_path / "styled.docx"
    word_export.export_to_docx(paragraph_pdf, target, pages=[0], method="text")

    document = docx.Document(str(target))
    runs = [run for paragraph in document.paragraphs for run in paragraph.runs if run.text.strip()]
    assert runs
    assert any(run.bold for run in runs)
    assert any(run.font.size is not None for run in runs)


def test_export_from_open_document_includes_unsaved_edits(paragraph_pdf, tmp_path):
    """Exporting an open document captures edits that were never saved."""
    document = fitz.open(paragraph_pdf)
    try:
        document[0].insert_text(fitz.Point(60, 400), "UNSAVED MARKER", fontsize=12)
        target = tmp_path / "live.docx"
        word_export.export_to_docx(document, target, pages=[0], method="text")
    finally:
        document.close()

    assert "UNSAVED MARKER" in read_docx_text(target)


def test_export_adds_the_docx_suffix(paragraph_pdf, tmp_path):
    """A missing suffix is added and parent folders are created."""
    report = word_export.export_to_docx(
        paragraph_pdf, tmp_path / "nested" / "report", pages=[0], method="text"
    )
    assert report.destination.suffix == ".docx"
    assert report.destination.exists()


def test_export_reports_progress(paragraph_pdf, tmp_path):
    """The progress callback runs from zero to one."""
    seen = []
    word_export.export_to_docx(
        paragraph_pdf, tmp_path / "progress.docx", method="text",
        progress=lambda fraction, message: seen.append(fraction),
    )
    assert seen
    assert seen[0] == pytest.approx(0.0)
    assert seen[-1] == pytest.approx(1.0)
    assert all(0.0 <= value <= 1.0 for value in seen)


def test_export_rejects_bad_input(paragraph_pdf, tmp_path):
    """Invalid pages, methods and sources are reported clearly."""
    with pytest.raises(PDFEditorError):
        word_export.export_to_docx(paragraph_pdf, tmp_path / "a.docx", pages=[99])
    with pytest.raises(PDFEditorError):
        word_export.export_to_docx(paragraph_pdf, tmp_path / "b.docx", pages=[])
    with pytest.raises(PDFEditorError):
        word_export.export_to_docx(paragraph_pdf, tmp_path / "c.docx", method="magic")
    with pytest.raises(PDFEditorError):
        word_export.export_to_docx(tmp_path / "missing.pdf", tmp_path / "d.docx")


def test_export_without_any_backend(paragraph_pdf, tmp_path, monkeypatch):
    """A helpful error explains which package to install."""
    monkeypatch.setattr(word_export, "layout_backend_available", lambda: False)
    monkeypatch.setattr(word_export, "text_backend_available", lambda: False)

    with pytest.raises(UnsupportedOperationError) as error:
        word_export.export_to_docx(paragraph_pdf, tmp_path / "none.docx")
    assert "pdf2docx" in str(error.value)


def test_requesting_a_missing_backend(paragraph_pdf, tmp_path, monkeypatch):
    """Asking for a backend that is not installed is refused."""
    monkeypatch.setattr(word_export, "layout_backend_available", lambda: False)
    with pytest.raises(UnsupportedOperationError):
        word_export.export_to_docx(paragraph_pdf, tmp_path / "x.docx", method="layout")


def test_report_summary(paragraph_pdf, tmp_path):
    """The report describes what happened."""
    report = word_export.export_to_docx(
        paragraph_pdf, tmp_path / "summary.docx", pages=[0], method="text"
    )
    assert "1 page" in report.summary
    assert "text and images only" in report.summary
    assert report.duration >= 0.0
