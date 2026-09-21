"""
Editing session: an open document plus undo/redo history and safe saving.

The GUI never touches ``pymupdf`` state directly.  It asks an :class:`EditSession`
for the current document, wraps every modification in :meth:`EditSession.edit`,
and lets the session take care of snapshots, the dirty flag and atomic export.

The whole file is loaded into memory when a session is opened.  That keeps the
original file unlocked (so "Save" can overwrite it) and makes undo a matter of
swapping byte buffers.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

import pymupdf as fitz

from pdf_editor.core.utils import (
    CorruptedPDFError,
    EncryptedPDFError,
    FileNotFoundPDFError,
    InvalidPageRangeError,
    PDFEditorError,
)

PathLike = Union[str, Path]

# How many undo steps are kept, and how much memory they may occupy in total.
MAX_HISTORY = 30
MAX_HISTORY_BYTES = 512 * 1024 * 1024


@dataclass
class RenderedPage:
    """A rasterised page ready to be shown in the UI."""

    pixmap: fitz.Pixmap
    scale: float
    page_index: int

    @property
    def width(self) -> int:
        """Bitmap width in pixels."""
        return self.pixmap.width

    @property
    def height(self) -> int:
        """Bitmap height in pixels."""
        return self.pixmap.height

    @property
    def samples(self) -> bytes:
        """Raw RGB bytes of the bitmap."""
        return self.pixmap.samples


@dataclass
class _Snapshot:
    """One entry of the undo/redo history."""

    label: str
    data: bytes
    page_index: int = 0


@dataclass
class EditSession:
    """
    An open PDF plus everything needed to edit it interactively.

    :param document: The open PyMuPDF document.
    :param path: File the document was loaded from (``None`` for new documents).
    :param max_history: How many undo steps to keep.
    :param max_history_bytes: How much memory the undo history may occupy.

    The history limits default to the desktop values and can be lowered on an
    individual session, which is what the web front end does so that several
    people sharing one small server cannot exhaust its memory.
    """

    document: fitz.Document
    path: Optional[Path] = None
    dirty: bool = False
    max_history: int = MAX_HISTORY
    max_history_bytes: int = MAX_HISTORY_BYTES
    _undo: List[_Snapshot] = field(default_factory=list, repr=False)
    _redo: List[_Snapshot] = field(default_factory=list, repr=False)

    # -- construction -------------------------------------------------------
    @classmethod
    def open(cls, path: PathLike, password: Optional[str] = None) -> "EditSession":
        """
        Opens a PDF file for editing.

        :param path: PDF file to open.
        :param password: Password for encrypted documents.
        :return: A ready to use :class:`EditSession`.
        :raises FileNotFoundPDFError: When the file does not exist.
        :raises EncryptedPDFError: When the file needs a (different) password.
        :raises CorruptedPDFError: When the file is not a readable PDF.
        """
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundPDFError(f"PDF file not found: '{file_path}'")

        try:
            data = file_path.read_bytes()
        except OSError as error:
            raise CorruptedPDFError(f"Cannot read '{file_path.name}': {error}") from error

        if not data[:5].startswith(b"%PDF-"):
            raise CorruptedPDFError(f"'{file_path.name}' is not a PDF file.")

        try:
            document = fitz.open(stream=data, filetype="pdf")
        except Exception as error:
            raise CorruptedPDFError(f"'{file_path.name}' is corrupted or unreadable: {error}") from error

        if document.needs_pass:
            if not document.authenticate(password or ""):
                document.close()
                raise EncryptedPDFError(
                    f"'{file_path.name}' is password protected. Provide the correct password to edit it."
                )
        if document.page_count == 0:
            document.close()
            raise CorruptedPDFError(f"'{file_path.name}' contains no pages.")

        return cls(document=document, path=file_path)

    @classmethod
    def create(cls, *, width: float = 595.0, height: float = 842.0) -> "EditSession":
        """
        Creates a session holding a new, single blank page document.

        :param width: Page width in points (A4 by default).
        :param height: Page height in points.
        :return: A new :class:`EditSession` with no associated file.
        """
        document = fitz.open()
        document.new_page(width=width, height=height)
        return cls(document=document, path=None, dirty=True)

    # -- basic properties ---------------------------------------------------
    def __len__(self) -> int:
        """Number of pages in the document."""
        return self.document.page_count

    @property
    def page_count(self) -> int:
        """Number of pages in the document."""
        return self.document.page_count

    @property
    def display_name(self) -> str:
        """File name shown in the window title."""
        return self.path.name if self.path else "Untitled.pdf"

    @property
    def metadata(self) -> Dict[str, str]:
        """Document metadata dictionary (title, author, subject, ...)."""
        return dict(self.document.metadata or {})

    def page(self, index: int) -> fitz.Page:
        """
        Returns a page by index.

        :param index: 0-based page index.
        :return: The requested page.
        :raises InvalidPageRangeError: When the index is out of bounds.
        """
        if index < 0 or index >= self.document.page_count:
            raise InvalidPageRangeError(
                f"Page {index + 1} does not exist (document has {self.document.page_count} page(s))."
            )
        return self.document[index]

    def set_metadata(self, values: Dict[str, str]) -> None:
        """
        Updates document metadata, keeping unspecified fields untouched.

        :param values: Metadata fields to change.
        """
        merged = self.metadata
        merged.update({key: value for key, value in values.items() if value is not None})
        self.document.set_metadata(merged)
        self.dirty = True

    # -- history ------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        """True when at least one edit can be undone."""
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        """True when an undone edit can be redone."""
        return bool(self._redo)

    @property
    def undo_label(self) -> Optional[str]:
        """Name of the edit that would be undone next."""
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> Optional[str]:
        """Name of the edit that would be redone next."""
        return self._redo[-1].label if self._redo else None

    def _serialise(self) -> bytes:
        """Serialises the current document state for the history."""
        return self.document.tobytes(deflate=True)

    def _trim_history(self) -> None:
        """Drops the oldest snapshots when the history grows too large."""
        while len(self._undo) > max(1, self.max_history):
            self._undo.pop(0)
        total = sum(len(item.data) for item in self._undo)
        while len(self._undo) > 1 and total > self.max_history_bytes:
            total -= len(self._undo.pop(0).data)

    def _load(self, data: bytes) -> None:
        """Replaces the in-memory document with a serialised snapshot."""
        replacement = fitz.open(stream=data, filetype="pdf")
        old = self.document
        self.document = replacement
        try:
            old.close()
        except Exception:
            pass

    @contextmanager
    def edit(self, label: str, page_index: int = 0) -> Iterator[fitz.Document]:
        """
        Context manager wrapping one undoable modification.

        A snapshot is taken before the block runs.  If the block raises, the
        snapshot is restored so a failed operation never leaves a half-edited
        document behind.

        :param label: Human readable name of the operation (shown in the UI).
        :param page_index: Page the user was looking at, restored on undo.
        :yield: The document to modify.
        """
        snapshot = _Snapshot(label=label, data=self._serialise(), page_index=page_index)
        try:
            yield self.document
        except Exception:
            self._load(snapshot.data)
            raise
        self._undo.append(snapshot)
        self._redo.clear()
        self._trim_history()
        self.dirty = True

    def undo(self) -> Tuple[Optional[str], int]:
        """
        Reverts the most recent edit.

        :return: ``(label, page index)`` of the reverted edit, or ``(None, 0)``.
        """
        if not self._undo:
            return None, 0
        snapshot = self._undo.pop()
        self._redo.append(
            _Snapshot(label=snapshot.label, data=self._serialise(), page_index=snapshot.page_index)
        )
        self._load(snapshot.data)
        self.dirty = True
        return snapshot.label, snapshot.page_index

    def redo(self) -> Tuple[Optional[str], int]:
        """
        Re-applies the most recently undone edit.

        :return: ``(label, page index)`` of the restored edit, or ``(None, 0)``.
        """
        if not self._redo:
            return None, 0
        snapshot = self._redo.pop()
        self._undo.append(
            _Snapshot(label=snapshot.label, data=self._serialise(), page_index=snapshot.page_index)
        )
        self._load(snapshot.data)
        self.dirty = True
        return snapshot.label, snapshot.page_index

    def clear_history(self) -> None:
        """Forgets all undo/redo steps (used after opening or saving)."""
        self._undo.clear()
        self._redo.clear()

    # -- rendering ----------------------------------------------------------
    def render(self, index: int, scale: float = 1.0, *, alpha: bool = False) -> RenderedPage:
        """
        Rasterises a page for display.

        :param index: 0-based page index.
        :param scale: Zoom factor where 1.0 means 72 dpi.
        :param alpha: Include an alpha channel.
        :return: A :class:`RenderedPage`.
        :raises InvalidPageRangeError: When the index is out of bounds.
        """
        page = self.page(index)
        matrix = fitz.Matrix(scale, scale)
        pixmap = page.get_pixmap(matrix=matrix, alpha=alpha)
        return RenderedPage(pixmap=pixmap, scale=scale, page_index=index)

    def thumbnail(self, index: int, max_width: int = 150) -> RenderedPage:
        """
        Renders a small preview of a page.

        :param index: 0-based page index.
        :param max_width: Width of the thumbnail in pixels.
        :return: A :class:`RenderedPage`.
        """
        page = self.page(index)
        scale = max_width / page.rect.width if page.rect.width else 1.0
        return self.render(index, scale)

    # -- saving -------------------------------------------------------------
    def save(
        self,
        destination: Optional[PathLike] = None,
        *,
        optimise: bool = True,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Path:
        """
        Writes the document to disk.

        The file is written to a temporary file first and then moved into place,
        so an interrupted save can never destroy the previous version.

        :param destination: Target file; defaults to the file that was opened.
        :param optimise: Garbage-collect and compress the output.
        :param metadata: Metadata fields to apply before saving.
        :return: Path of the written file.
        :raises PDFEditorError: When no target is known or writing fails.
        """
        target = Path(destination) if destination else self.path
        if target is None:
            raise PDFEditorError("No destination given: use 'Save As' to choose a file name.")
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")
        target.parent.mkdir(parents=True, exist_ok=True)

        if metadata:
            self.set_metadata(metadata)

        handle, temporary = tempfile.mkstemp(suffix=".pdf", dir=str(target.parent))
        os.close(handle)
        try:
            self.document.save(
                temporary,
                garbage=4 if optimise else 0,
                deflate=True,
                clean=optimise,
                pretty=False,
            )
            os.replace(temporary, target)
        except Exception as error:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
            raise PDFEditorError(f"Could not save '{target.name}': {error}") from error

        self.path = target
        self.dirty = False
        return target

    def export_bytes(self) -> bytes:
        """
        Returns the current document as PDF bytes without touching the disk.

        :return: Serialised PDF data.
        """
        return self.document.tobytes(garbage=4, deflate=True, clean=True)

    def close(self) -> None:
        """Closes the document and releases the undo history."""
        self.clear_history()
        try:
            self.document.close()
        except Exception:
            pass


__all__ = ["EditSession", "MAX_HISTORY", "RenderedPage"]
