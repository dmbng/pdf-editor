"""
Page management for an open document: delete, add, duplicate, reorder and merge.

The functions in :mod:`pdf_editor.core.editor` work file-to-file and are used by
the command line interface.  The functions here operate on an already open
``pymupdf.Document`` so the GUI can apply a change, show it, and let the user undo
it without touching the disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import pymupdf as fitz

from pdf_editor.core.utils import (
    InvalidPageRangeError,
    PDFEditorError,
    UnsupportedOperationError,
    validate_pdf,
)

PathLike = Union[str, Path]

# Page sizes offered by the "add blank page" dialog, in points (width, height).
PAPER_SIZES: Dict[str, Tuple[float, float]] = {
    "A3": fitz.paper_size("a3"),
    "A4": fitz.paper_size("a4"),
    "A5": fitz.paper_size("a5"),
    "Letter": fitz.paper_size("letter"),
    "Legal": fitz.paper_size("legal"),
    "Tabloid": fitz.paper_size("tabloid"),
}


def _validate_indices(document: fitz.Document, indices: Iterable[int]) -> List[int]:
    """
    Normalises and validates a collection of 0-based page indices.

    :param document: Document the indices refer to.
    :param indices: Page indices, in any order, possibly with duplicates.
    :return: Sorted list of unique valid indices.
    :raises InvalidPageRangeError: When the list is empty or out of bounds.
    """
    unique = sorted({int(index) for index in indices})
    if not unique:
        raise InvalidPageRangeError("No pages were selected.")
    total = document.page_count
    for index in unique:
        if index < 0 or index >= total:
            raise InvalidPageRangeError(
                f"Page {index + 1} does not exist (document has {total} page(s))."
            )
    return unique


def delete_pages(document: fitz.Document, indices: Iterable[int]) -> int:
    """
    Deletes pages from an open document.

    :param document: Document to modify in place.
    :param indices: 0-based indices of the pages to remove.
    :return: Number of pages deleted.
    :raises InvalidPageRangeError: For invalid indices.
    :raises UnsupportedOperationError: When the deletion would empty the document.
    """
    targets = _validate_indices(document, indices)
    if len(targets) >= document.page_count:
        raise UnsupportedOperationError(
            "A PDF must keep at least one page; deleting every page is not allowed."
        )
    document.delete_pages(targets)
    return len(targets)


def insert_blank_page(
    document: fitz.Document,
    index: Optional[int] = None,
    *,
    width: Optional[float] = None,
    height: Optional[float] = None,
    paper: str = "A4",
    landscape: bool = False,
    match_page: Optional[int] = None,
) -> int:
    """
    Inserts an empty page.

    :param document: Document to modify in place.
    :param index: Position of the new page; ``None`` appends at the end.
    :param width: Explicit page width in points (overrides ``paper``).
    :param height: Explicit page height in points (overrides ``paper``).
    :param paper: Named paper size, see :data:`PAPER_SIZES`.
    :param landscape: Swap width and height of the named paper size.
    :param match_page: Copy the size of this existing page instead.
    :return: Index of the newly created page.
    :raises InvalidPageRangeError: When ``index`` or ``match_page`` is invalid.
    :raises PDFEditorError: For an unknown paper size or a non-positive size.
    """
    total = document.page_count
    position = total if index is None else int(index)
    if position < 0 or position > total:
        raise InvalidPageRangeError(f"Cannot insert a page at position {position + 1}.")

    if match_page is not None:
        if match_page < 0 or match_page >= total:
            raise InvalidPageRangeError(f"Page {match_page + 1} does not exist.")
        reference = document[match_page].rect
        page_width, page_height = reference.width, reference.height
    elif width and height:
        page_width, page_height = float(width), float(height)
    else:
        key = next((name for name in PAPER_SIZES if name.lower() == str(paper).lower()), None)
        if key is None:
            raise PDFEditorError(
                f"Unknown paper size '{paper}'. Available: {', '.join(PAPER_SIZES)}"
            )
        page_width, page_height = PAPER_SIZES[key]
        if landscape:
            page_width, page_height = page_height, page_width

    if page_width <= 0 or page_height <= 0:
        raise PDFEditorError("Page dimensions must be positive numbers.")

    document.new_page(pno=position, width=page_width, height=page_height)
    return position


def duplicate_pages(document: fitz.Document, indices: Iterable[int]) -> List[int]:
    """
    Duplicates pages, placing each copy directly after its original.

    :param document: Document to modify in place.
    :param indices: 0-based indices of the pages to duplicate.
    :return: Indices of the created copies, in ascending order.
    :raises InvalidPageRangeError: For invalid indices.
    """
    targets = _validate_indices(document, indices)
    created: List[int] = []
    # Copy from the back so that earlier indices stay valid while inserting; every
    # insertion shifts the copies that were made before it one position down.
    for index in sorted(targets, reverse=True):
        document.fullcopy_page(index, index + 1)
        created = [position + 1 if position > index else position for position in created]
        created.append(index + 1)
    return sorted(created)


def move_page(document: fitz.Document, source: int, destination: int) -> int:
    """
    Moves a single page to another position.

    :param document: Document to modify in place.
    :param source: Current 0-based index of the page.
    :param destination: Desired 0-based index after the move.
    :return: The final index of the moved page.
    :raises InvalidPageRangeError: When either index is out of bounds.
    """
    total = document.page_count
    if source < 0 or source >= total:
        raise InvalidPageRangeError(f"Page {source + 1} does not exist.")
    if destination < 0 or destination >= total:
        raise InvalidPageRangeError(f"Cannot move a page to position {destination + 1}.")
    if source == destination:
        return source

    # move_page() inserts *before* the given index, so moving a page further down
    # needs +1; -1 is the only way to express "behind the last page".
    if destination > source:
        target = destination + 1
        if target >= total:
            target = -1
    else:
        target = destination
    document.move_page(source, target)
    return destination


def reorder_pages(document: fitz.Document, order: Sequence[int]) -> None:
    """
    Applies a completely new page order.

    :param document: Document to modify in place.
    :param order: 0-based indices of every page in the desired sequence.
    :raises InvalidPageRangeError: When the sequence is not a permutation.
    """
    total = document.page_count
    if sorted(order) != list(range(total)):
        raise InvalidPageRangeError(
            "The new order must list each of the document's pages exactly once."
        )
    document.select(list(order))


def rotate_pages(
    document: fitz.Document,
    indices: Iterable[int],
    angle: int,
    *,
    absolute: bool = False,
) -> int:
    """
    Rotates pages by (or to) a multiple of 90 degrees.

    :param document: Document to modify in place.
    :param indices: 0-based indices of the pages to rotate.
    :param angle: Rotation in degrees; must be a multiple of 90.
    :param absolute: Set the rotation instead of adding to the current one.
    :return: Number of pages rotated.
    :raises InvalidPageRangeError: For invalid indices or angles.
    """
    if angle % 90 != 0:
        raise InvalidPageRangeError(f"Rotation must be a multiple of 90 degrees (got {angle}).")
    targets = _validate_indices(document, indices)
    for index in targets:
        page = document[index]
        page.set_rotation(angle % 360 if absolute else (page.rotation + angle) % 360)
    return len(targets)


def _normalise_toc(entries: List[List]) -> List[List]:
    """
    Makes a table of contents structurally valid for ``set_toc``.

    :param entries: Raw ``[level, title, page]`` rows.
    :return: Rows whose levels start at 1 and never jump by more than one.
    """
    cleaned: List[List] = []
    previous_level = 0
    for entry in entries:
        level, title, page = int(entry[0]), str(entry[1]), int(entry[2])
        level = max(1, min(level, previous_level + 1))
        cleaned.append([level, title, page])
        previous_level = level
    return cleaned


def merge_documents(
    document: fitz.Document,
    sources: Sequence[PathLike],
    *,
    at: Optional[int] = None,
    add_bookmarks: bool = True,
) -> int:
    """
    Inserts the pages of other PDF files into the open document.

    Bookmarks of the merged files are kept and, optionally, grouped under a
    top-level entry named after each source file.

    :param document: Document to modify in place.
    :param sources: PDF files to merge in, in the given order.
    :param at: Insert position (0-based); ``None`` appends at the end.
    :param add_bookmarks: Create one top-level bookmark per merged file.
    :return: Total number of pages added.
    :raises PDFEditorError: When a source file is missing, encrypted or corrupt.
    """
    if not sources:
        raise PDFEditorError("No PDF files were given to merge.")

    position = document.page_count if at is None else max(0, min(int(at), document.page_count))
    added = 0
    new_entries: List[List] = []

    for source in sources:
        path = Path(source)
        validate_pdf(path)  # Raises a descriptive error for bad input files.
        try:
            with fitz.open(str(path)) as incoming:
                if incoming.page_count == 0:
                    continue
                insert_at = position + added
                document.insert_pdf(
                    incoming,
                    from_page=0,
                    to_page=incoming.page_count - 1,
                    start_at=insert_at,
                    annots=True,
                    links=True,
                )
                if add_bookmarks:
                    new_entries.append([1, path.stem, insert_at + 1])
                for level, title, page in incoming.get_toc(simple=True):
                    if page < 1:
                        continue
                    new_entries.append(
                        [level + (1 if add_bookmarks else 0), title, insert_at + page]
                    )
                added += incoming.page_count
        except PDFEditorError:
            raise
        except Exception as error:
            raise PDFEditorError(f"Could not merge '{path.name}': {error}") from error

    if new_entries:
        # Bookmarks pointing at deleted pages report page -1; drop them.
        existing = [row for row in document.get_toc(simple=True) if int(row[2]) >= 1]
        combined = sorted(existing + new_entries, key=lambda row: (int(row[2]), int(row[0])))
        try:
            document.set_toc(_normalise_toc(combined))
        except Exception:
            pass  # A malformed outline must never block the merge itself.
    return added


def extract_pages(document: fitz.Document, indices: Iterable[int], destination: PathLike) -> Path:
    """
    Writes selected pages of the open document to a new PDF file.

    :param document: Source document (left unchanged).
    :param indices: 0-based indices of the pages to export.
    :param destination: Path of the PDF file to create.
    :return: Path of the written file.
    :raises InvalidPageRangeError: For invalid indices.
    """
    targets = _validate_indices(document, indices)
    target_path = Path(destination)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    output = fitz.open()
    try:
        for index in targets:
            output.insert_pdf(document, from_page=index, to_page=index, annots=True, links=True)
        output.set_metadata(document.metadata or {})
        output.save(str(target_path), garbage=4, deflate=True)
    finally:
        output.close()
    return target_path


def page_summary(document: fitz.Document, index: int) -> Dict[str, object]:
    """
    Collects the facts the thumbnail sidebar shows about one page.

    :param document: Document being inspected.
    :param index: 0-based page index.
    :return: Dictionary with size, rotation, and content counts.
    :raises InvalidPageRangeError: When the index is out of bounds.
    """
    if index < 0 or index >= document.page_count:
        raise InvalidPageRangeError(f"Page {index + 1} does not exist.")
    page = document[index]
    return {
        "index": index,
        "number": index + 1,
        "width": round(page.rect.width, 1),
        "height": round(page.rect.height, 1),
        "rotation": page.rotation,
        "images": len(page.get_images(full=True)),
        "has_text": bool(page.get_text("text").strip()),
        "annotations": len(list(page.annots())),
    }


__all__ = [
    "PAPER_SIZES",
    "delete_pages",
    "duplicate_pages",
    "extract_pages",
    "insert_blank_page",
    "merge_documents",
    "move_page",
    "page_summary",
    "reorder_pages",
    "rotate_pages",
]
