"""
Module for page-level PDF editing: rotating, reordering, deleting, extracting, and replacing pages.
"""

from pathlib import Path
from typing import List, Union, Dict
from pypdf import PdfReader, PdfWriter
from pdf_editor.core.utils import validate_pdf, parse_page_ranges, PDFEditorError, InvalidPageRangeError


def rotate_pages(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    page_angles: Union[int, Dict[str, int]],
    target_pages: str = "all"
) -> Path:
    """
    Rotates specified pages by a given angle (90, 180, 270 degrees clockwise).

    :param input_path: Path to input PDF file.
    :param output_path: Destination path for output PDF file.
    :param page_angles: Either an integer angle (e.g. 90) combined with target_pages,
                        or a dictionary mapping range strings to angles, e.g. {"1-3": 90, "4": 180}.
    :param target_pages: Range specification if page_angles is a single integer.
    :return: Path object of modified PDF.
    """
    total_pages = validate_pdf(input_path)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    writer = PdfWriter()

    # Build mapping of 0-based page_index -> rotation angle adjustment
    rotation_map: Dict[int, int] = {}

    if isinstance(page_angles, int):
        if page_angles % 90 != 0:
            raise InvalidPageRangeError(f"Rotation angle must be a multiple of 90 degrees (got {page_angles}).")
        target_indices = parse_page_ranges(target_pages, total_pages)
        for idx in target_indices:
            rotation_map[idx] = page_angles
    elif isinstance(page_angles, dict):
        for range_str, angle in page_angles.items():
            if angle % 90 != 0:
                raise InvalidPageRangeError(f"Rotation angle must be a multiple of 90 degrees (got {angle}).")
            target_indices = parse_page_ranges(range_str, total_pages)
            for idx in target_indices:
                rotation_map[idx] = angle
    else:
        raise PDFEditorError("page_angles must be an integer angle or a dict of range->angle.")

    for i, page in enumerate(reader.pages):
        if i in rotation_map:
            angle = rotation_map[i]
            page.rotate(angle)
        writer.add_page(page)

    with open(out_file, "wb") as f:
        writer.write(f)

    return out_file


def reorder_pages(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    new_order: List[int]
) -> Path:
    """
    Reorders document pages according to a 1-based sequence list.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param new_order: List of 1-based page numbers in desired sequence (e.g. [3, 1, 2, 4]).
    :return: Path object of reordered PDF.
    """
    total_pages = validate_pdf(input_path)
    if not new_order:
        raise InvalidPageRangeError("New page order list cannot be empty.")

    for p in new_order:
        if p < 1 or p > total_pages:
            raise InvalidPageRangeError(f"Page number {p} in new order is out of bounds (1-{total_pages}).")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    writer = PdfWriter()

    for p_num in new_order:
        writer.add_page(reader.pages[p_num - 1])

    with open(out_file, "wb") as f:
        writer.write(f)

    return out_file


def delete_pages(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    pages_to_delete: str
) -> Path:
    """
    Deletes specified pages from a PDF document.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param pages_to_delete: Range specification of pages to remove (e.g. "2, 4-6").
    :return: Path object of modified PDF.
    """
    total_pages = validate_pdf(input_path)
    delete_indices = set(parse_page_ranges(pages_to_delete, total_pages))

    if len(delete_indices) >= total_pages:
        raise InvalidPageRangeError("Cannot delete all pages from document. At least 1 page must remain.")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    writer = PdfWriter()

    for idx, page in enumerate(reader.pages):
        if idx not in delete_indices:
            writer.add_page(page)

    with open(out_file, "wb") as f:
        writer.write(f)

    return out_file


def extract_pages(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    pages_to_extract: str
) -> Path:
    """
    Extracts a subset of pages into a new PDF document.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param pages_to_extract: Range specification (e.g. "1-3, 5").
    :return: Path object of created PDF file.
    """
    total_pages = validate_pdf(input_path)
    extract_indices = parse_page_ranges(pages_to_extract, total_pages)

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    writer = PdfWriter()

    for idx in extract_indices:
        writer.add_page(reader.pages[idx])

    with open(out_file, "wb") as f:
        writer.write(f)

    return out_file


def replace_pages(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    target_pages: str,
    replacement_pdf_path: Union[str, Path],
    replacement_pages: str = "all"
) -> Path:
    """
    Replaces specified target pages in input_path with pages from replacement_pdf_path.

    :param input_path: Path to target PDF file.
    :param output_path: Destination path for output PDF file.
    :param target_pages: Target pages range spec (e.g. "2" or "2-3").
    :param replacement_pdf_path: Source PDF file containing replacement pages.
    :param replacement_pages: Range spec of pages from replacement PDF (default "all").
    :return: Path object of modified PDF.
    """
    target_total = validate_pdf(input_path)
    replacement_total = validate_pdf(replacement_pdf_path)

    target_indices = parse_page_ranges(target_pages, target_total)
    replacement_indices = parse_page_ranges(replacement_pages, replacement_total)

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    main_reader = PdfReader(str(input_path))
    repl_reader = PdfReader(str(replacement_pdf_path))
    writer = PdfWriter()

    target_set = set(target_indices)
    repl_queue = [repl_reader.pages[i] for i in replacement_indices]
    repl_idx = 0

    for i in range(target_total):
        if i in target_set:
            if repl_idx < len(repl_queue):
                writer.add_page(repl_queue[repl_idx])
                repl_idx += 1
            # If target range is larger than replacement queue, extra target pages are replaced by cycling or skipped
        else:
            writer.add_page(main_reader.pages[i])

    # If there are remaining replacement pages, append them
    while repl_idx < len(repl_queue):
        writer.add_page(repl_queue[repl_idx])
        repl_idx += 1

    with open(out_file, "wb") as f:
        writer.write(f)

    return out_file
