"""
Unit tests for importing Word, Excel and PowerPoint documents as PDF.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import doc_import
from pdf_editor.core.utils import PDFEditorError, UnsupportedOperationError

pytest.importorskip("reportlab", reason="ReportLab is not installed")

needs_office = pytest.mark.skipif(
    not doc_import.office_backend_available(),
    reason="Microsoft Office is not installed on this machine",
)


def pdf_text(path) -> str:
    """Returns all the text of a PDF."""
    with fitz.open(path) as document:
        return "\n".join(page.get_text("text") for page in document)


@pytest.mark.parametrize(
    "name, kind",
    [
        ("report.docx", doc_import.KIND_WORD),
        ("legacy.doc", doc_import.KIND_WORD),
        ("book.xlsx", doc_import.KIND_EXCEL),
        ("data.csv", doc_import.KIND_EXCEL),
        ("deck.pptx", doc_import.KIND_POWERPOINT),
        ("old.ppt", doc_import.KIND_POWERPOINT),
    ],
)
def test_detect_kind(name, kind):
    """Each supported extension maps onto its application."""
    assert doc_import.detect_kind(name) == kind
    assert doc_import.is_supported(name) is True


def test_detect_kind_rejects_other_files():
    """Unsupported types raise a helpful error."""
    assert doc_import.is_supported("photo.png") is False
    with pytest.raises(UnsupportedOperationError) as error:
        doc_import.detect_kind("photo.png")
    assert ".docx" in str(error.value)


def test_available_backends_lists_builtin():
    """The built-in converter is offered for the modern formats."""
    assert "builtin" in doc_import.available_backends("report.docx")
    assert "builtin" in doc_import.available_backends("book.xlsx")
    assert "builtin" in doc_import.available_backends("deck.pptx")
    # Legacy formats can only be read by Office.
    assert "builtin" not in doc_import.available_backends("legacy.doc")


def test_default_destination():
    """The PDF is proposed next to the document."""
    assert doc_import.default_destination("C:/docs/report.docx").name == "report.pdf"


def test_convert_word_document(word_document, tmp_path):
    """A Word file becomes a PDF with its text, table and picture."""
    target = tmp_path / "out" / "report.pdf"
    report = doc_import.convert_to_pdf(word_document, target, backend="builtin")

    assert report.backend == "builtin"
    assert report.kind == doc_import.KIND_WORD
    assert report.destination.exists()
    assert report.pages == 2  # The document contains one page break.

    text = pdf_text(report.destination)
    assert "Imported Report" in text
    assert "Section One" in text
    assert "First bullet" in text
    assert "120000" in text
    assert "Closing line of the document." in text

    with fitz.open(report.destination) as document:
        assert any(page.get_images() for page in document)


def test_word_conversion_keeps_bold_and_colour(word_document, tmp_path):
    """Character formatting survives the built-in converter."""
    report = doc_import.convert_to_pdf(word_document, tmp_path / "styled.pdf", backend="builtin")

    spans = [
        span
        for page in fitz.open(report.destination)
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
    ]
    assert any("Bold" in span["font"] for span in spans)
    assert any(span["color"] != 0 for span in spans)


def test_convert_excel_workbook(excel_workbook, tmp_path):
    """Every visible sheet becomes a section of the PDF."""
    report = doc_import.convert_to_pdf(excel_workbook, tmp_path / "book.pdf", backend="builtin")

    assert report.kind == doc_import.KIND_EXCEL
    assert report.pages == 2  # One page per sheet.

    text = pdf_text(report.destination)
    assert "Sales" in text
    assert "Notes" in text
    assert "120000" in text
    assert "Figures are provisional." in text


def test_convert_powerpoint_deck(powerpoint_deck, tmp_path):
    """Each slide becomes one page of the PDF."""
    report = doc_import.convert_to_pdf(powerpoint_deck, tmp_path / "deck.pdf", backend="builtin")

    assert report.kind == doc_import.KIND_POWERPOINT
    assert report.pages == 2

    text = pdf_text(report.destination)
    assert "Deck Title" in text
    assert "Picture Slide" in text
    assert "Bullet one" in text

    with fitz.open(report.destination) as document:
        assert document[1].get_images()
        # The page keeps the slide proportions rather than A4 ones.
        assert document[0].rect.width > document[0].rect.height


def test_conversion_reports_progress(word_document, tmp_path):
    """The progress callback runs from zero to one."""
    seen = []
    doc_import.convert_to_pdf(
        word_document, tmp_path / "p.pdf", backend="builtin",
        progress=lambda fraction, message: seen.append(fraction),
    )
    assert seen
    assert seen[0] < 0.2
    assert seen[-1] == pytest.approx(1.0)


def test_destination_defaults_next_to_the_source(word_document):
    """Without a destination the PDF lands beside the document."""
    report = doc_import.convert_to_pdf(word_document, backend="builtin")
    assert report.destination == word_document.with_suffix(".pdf")
    assert report.destination.exists()


def test_destination_suffix_is_added(word_document, tmp_path):
    """A missing .pdf suffix is added automatically."""
    report = doc_import.convert_to_pdf(word_document, tmp_path / "no_suffix", backend="builtin")
    assert report.destination.suffix == ".pdf"


def test_conversion_rejects_bad_input(word_document, tmp_path):
    """Missing files, unknown types and bad backends are reported."""
    with pytest.raises(PDFEditorError):
        doc_import.convert_to_pdf(tmp_path / "missing.docx", tmp_path / "a.pdf")

    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("hello")
    with pytest.raises(UnsupportedOperationError):
        doc_import.convert_to_pdf(unsupported, tmp_path / "b.pdf")

    with pytest.raises(PDFEditorError):
        doc_import.convert_to_pdf(word_document, tmp_path / "c.pdf", backend="magic")


def test_conversion_refuses_to_overwrite_the_source(tmp_path, word_document):
    """The PDF may never replace the document being read."""
    pdf_named_source = word_document.with_suffix(".pdf")
    pdf_named_source.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(PDFEditorError):
        doc_import.convert_to_pdf(pdf_named_source, pdf_named_source)


def test_missing_builtin_reader_is_explained(word_document, tmp_path, monkeypatch):
    """A missing package produces an instruction, not a stack trace."""
    monkeypatch.setattr(doc_import, "office_backend_available", lambda kind=None: False)
    monkeypatch.setattr(doc_import, "builtin_backend_available", lambda kind=None: False)

    with pytest.raises(UnsupportedOperationError) as error:
        doc_import.convert_to_pdf(word_document, tmp_path / "x.pdf")
    assert "python-docx" in str(error.value)


def test_requesting_office_when_absent(word_document, tmp_path, monkeypatch):
    """Asking for Office on a machine without it is refused clearly."""
    monkeypatch.setattr(doc_import, "office_backend_available", lambda kind=None: False)
    with pytest.raises(UnsupportedOperationError) as error:
        doc_import.convert_to_pdf(word_document, tmp_path / "y.pdf", backend="office")
    assert "Microsoft Office" in str(error.value)


def test_legacy_format_needs_office(tmp_path, monkeypatch):
    """The built-in converter cannot read the pre-2007 formats."""
    legacy = tmp_path / "old.doc"
    legacy.write_bytes(b"legacy binary content")
    monkeypatch.setattr(doc_import, "office_backend_available", lambda kind=None: False)

    with pytest.raises(UnsupportedOperationError):
        doc_import.convert_to_pdf(legacy, tmp_path / "z.pdf")


def test_report_summary(word_document, tmp_path):
    """The report says what was produced and how."""
    report = doc_import.convert_to_pdf(word_document, tmp_path / "s.pdf", backend="builtin")
    assert "report.docx" in report.summary
    assert "built-in converter" in report.summary
    assert report.duration >= 0.0


@needs_office
def test_office_backend_converts_word(word_document, tmp_path):
    """Microsoft Office produces a PDF when it is installed."""
    report = doc_import.convert_to_pdf(word_document, tmp_path / "office.pdf", backend="office")
    assert report.backend == "office"
    assert report.destination.exists()
    assert report.pages >= 1
    assert "Imported Report" in pdf_text(report.destination)
