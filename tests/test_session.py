"""
Unit tests for the editing session: history, rendering and saving.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import page_ops
from pdf_editor.core.session import EditSession
from pdf_editor.core.utils import (
    CorruptedPDFError,
    EncryptedPDFError,
    FileNotFoundPDFError,
    InvalidPageRangeError,
    PDFEditorError,
)


def test_open_reads_the_document(simple_pdf):
    """Opening loads the pages and the metadata."""
    session = EditSession.open(simple_pdf)
    try:
        assert session.page_count == 4
        assert len(session) == 4
        assert session.display_name == "simple.pdf"
        assert session.dirty is False
        assert session.metadata["title"] == "Simple"
    finally:
        session.close()


def test_open_reports_bad_files(tmp_path, broken_pdf):
    """Missing and corrupt files raise descriptive errors."""
    with pytest.raises(FileNotFoundPDFError):
        EditSession.open(tmp_path / "missing.pdf")
    with pytest.raises(CorruptedPDFError):
        EditSession.open(broken_pdf)


def test_open_encrypted_document(tmp_path, simple_pdf):
    """An encrypted file needs its password."""
    protected = tmp_path / "protected.pdf"
    with fitz.open(simple_pdf) as doc:
        doc.save(str(protected), encryption=fitz.PDF_ENCRYPT_AES_256,
                 owner_pw="owner", user_pw="secret")

    with pytest.raises(EncryptedPDFError):
        EditSession.open(protected)

    session = EditSession.open(protected, "secret")
    try:
        assert session.page_count == 4
    finally:
        session.close()


def test_create_makes_a_blank_document():
    """A new session starts with one blank page."""
    session = EditSession.create()
    try:
        assert session.page_count == 1
        assert session.path is None
        assert session.display_name == "Untitled.pdf"
    finally:
        session.close()


def test_edit_records_history(simple_pdf):
    """Each edit block becomes one undo step."""
    session = EditSession.open(simple_pdf)
    try:
        assert session.can_undo is False

        with session.edit("Delete page", page_index=0):
            page_ops.delete_pages(session.document, [1])

        assert session.page_count == 3
        assert session.dirty is True
        assert session.can_undo is True
        assert session.undo_label == "Delete page"

        label, page_index = session.undo()
        assert label == "Delete page"
        assert page_index == 0
        assert session.page_count == 4
        assert session.can_redo is True

        session.redo()
        assert session.page_count == 3
    finally:
        session.close()


def test_undo_redo_without_history(simple_pdf):
    """Undo and redo are safe no-ops on an untouched document."""
    session = EditSession.open(simple_pdf)
    try:
        assert session.undo() == (None, 0)
        assert session.redo() == (None, 0)
    finally:
        session.close()


def test_failed_edit_is_rolled_back(simple_pdf):
    """A failing operation leaves the document exactly as it was."""
    session = EditSession.open(simple_pdf)
    try:
        with pytest.raises(RuntimeError):
            with session.edit("Broken operation"):
                page_ops.delete_pages(session.document, [0])
                raise RuntimeError("something went wrong")

        assert session.page_count == 4
        assert session.can_undo is False
    finally:
        session.close()


def test_new_edit_clears_the_redo_stack(simple_pdf):
    """Editing after an undo discards the redo history."""
    session = EditSession.open(simple_pdf)
    try:
        with session.edit("First"):
            page_ops.delete_pages(session.document, [0])
        session.undo()
        assert session.can_redo is True

        with session.edit("Second"):
            page_ops.insert_blank_page(session.document, 0)
        assert session.can_redo is False
    finally:
        session.close()


def test_render_and_thumbnail(simple_pdf):
    """Rendering honours the zoom factor and the thumbnail width."""
    session = EditSession.open(simple_pdf)
    try:
        normal = session.render(0, 1.0)
        assert normal.width == pytest.approx(400, abs=2)

        zoomed = session.render(0, 2.0)
        assert zoomed.width == pytest.approx(800, abs=3)
        assert len(zoomed.samples) > len(normal.samples)

        thumb = session.thumbnail(0, 100)
        assert thumb.width == pytest.approx(100, abs=2)

        with pytest.raises(InvalidPageRangeError):
            session.render(9)
    finally:
        session.close()


def test_save_and_save_as(simple_pdf, tmp_path):
    """Saving writes to the original file and to a chosen one."""
    session = EditSession.open(simple_pdf)
    try:
        with session.edit("Delete page"):
            page_ops.delete_pages(session.document, [0])

        saved = session.save()
        assert saved == simple_pdf
        assert session.dirty is False
        with fitz.open(saved) as reopened:
            assert reopened.page_count == 3

        target = tmp_path / "exports" / "copy.pdf"
        exported = session.save(target)
        assert exported.exists()
        assert session.path == exported
    finally:
        session.close()


def test_save_adds_the_pdf_suffix(simple_pdf, tmp_path):
    """A missing '.pdf' suffix is added automatically."""
    session = EditSession.open(simple_pdf)
    try:
        written = session.save(tmp_path / "no_suffix")
        assert written.suffix == ".pdf"
        assert written.exists()
    finally:
        session.close()


def test_save_without_target_raises():
    """A brand new document must be given a file name."""
    session = EditSession.create()
    try:
        with pytest.raises(PDFEditorError):
            session.save()
    finally:
        session.close()


def test_metadata_is_preserved_and_updatable(simple_pdf, tmp_path):
    """Metadata survives a save and can be changed deliberately."""
    session = EditSession.open(simple_pdf)
    try:
        session.set_metadata({"author": "New Author"})
        target = session.save(tmp_path / "meta.pdf")
        with fitz.open(target) as reopened:
            assert reopened.metadata["author"] == "New Author"
            assert reopened.metadata["title"] == "Simple"
    finally:
        session.close()


def test_export_bytes_round_trip(simple_pdf):
    """The document can be serialised without touching the disk."""
    session = EditSession.open(simple_pdf)
    try:
        data = session.export_bytes()
        assert data.startswith(b"%PDF-")
        with fitz.open(stream=data, filetype="pdf") as reopened:
            assert reopened.page_count == 4
    finally:
        session.close()


def test_page_accessor_validates_index(simple_pdf):
    """Asking for a page outside the document is an error."""
    session = EditSession.open(simple_pdf)
    try:
        assert session.page(0).number == 0
        with pytest.raises(InvalidPageRangeError):
            session.page(-1)
        with pytest.raises(InvalidPageRangeError):
            session.page(99)
    finally:
        session.close()


def test_clear_history(simple_pdf):
    """Clearing the history drops undo and redo steps."""
    session = EditSession.open(simple_pdf)
    try:
        with session.edit("Change"):
            page_ops.insert_blank_page(session.document, 0)
        session.clear_history()
        assert session.can_undo is False
        assert session.can_redo is False
    finally:
        session.close()
