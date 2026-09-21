"""
Module for splitting PDF files by page ranges, bookmarks/outlines, or into single pages.
"""

from pathlib import Path
from typing import List, Union
import pymupdf as fitz
from pypdf import PdfReader, PdfWriter
from pdf_editor.core.utils import validate_pdf, parse_page_ranges, PDFEditorError, InvalidPageRangeError


def split_by_ranges(
    input_path: Union[str, Path],
    range_specs: List[str],
    output_dir: Union[str, Path],
    output_prefix: str = "part"
) -> List[Path]:
    """
    Splits a PDF into multiple separate files based on a list of range specification strings.

    :param input_path: Path to the input PDF file.
    :param range_specs: List of page range strings (e.g. ['1-3', '4-7', '8-end']).
    :param output_dir: Destination directory for split PDF files.
    :param output_prefix: File name prefix for output split files.
    :return: List of Path objects to created split PDF files.
    """
    total_pages = validate_pdf(input_path)
    if not range_specs:
        raise InvalidPageRangeError("No range specifications provided for splitting.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    created_files: List[Path] = []

    for idx, range_spec in enumerate(range_specs, start=1):
        target_indices = parse_page_ranges(range_spec, total_pages)
        writer = PdfWriter()

        for page_idx in target_indices:
            writer.add_page(reader.pages[page_idx])

        dest_file = out_dir / f"{output_prefix}_{idx:02d}.pdf"
        with open(dest_file, "wb") as f:
            writer.write(f)
        created_files.append(dest_file)

    return created_files


def split_by_bookmarks(
    input_path: Union[str, Path],
    output_dir: Union[str, Path],
    output_prefix: str = "section"
) -> List[Path]:
    """
    Splits a PDF document based on top-level outline bookmarks.

    :param input_path: Path to input PDF file.
    :param output_dir: Destination directory for section files.
    :param output_prefix: Default prefix if bookmark title cannot be sanitized.
    :return: List of created output file paths.
    :raises PDFEditorError: If document has no bookmarks/outline.
    """
    total_pages = validate_pdf(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(input_path))
    toc = doc.get_toc()  # Returns list of [lvl, title, page_num]

    if not toc:
        doc.close()
        raise PDFEditorError(f"PDF file '{input_path}' contains no outline or bookmarks.")

    # Filter level 1 (top-level) bookmarks
    top_bookmarks = [(item[1], item[2]) for item in toc if item[0] == 1]
    if not top_bookmarks:
        # Fallback to any bookmarks if level 1 not explicitly present
        top_bookmarks = [(item[1], item[2]) for item in toc]

    # Deduplicate / order by page number
    top_bookmarks.sort(key=lambda x: x[1])

    # Calculate page ranges for each bookmark
    sections = []
    for i, (title, start_page) in enumerate(top_bookmarks):
        start_idx = max(1, start_page)
        if i < len(top_bookmarks) - 1:
            end_idx = max(start_idx, top_bookmarks[i + 1][1] - 1)
        else:
            end_idx = total_pages
        
        # Clean title for filename
        clean_title = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).strip()
        if not clean_title:
            clean_title = f"{output_prefix}_{i+1:02d}"
        else:
            clean_title = f"{i+1:02d}_{clean_title.replace(' ', '_')}"

        range_str = f"{start_idx}-{end_idx}"
        sections.append((clean_title, range_str))

    reader = PdfReader(str(input_path))
    created_files: List[Path] = []

    for name, r_spec in sections:
        target_indices = parse_page_ranges(r_spec, total_pages)
        writer = PdfWriter()
        for idx in target_indices:
            writer.add_page(reader.pages[idx])
        
        dest_file = out_dir / f"{name}.pdf"
        with open(dest_file, "wb") as f:
            writer.write(f)
        created_files.append(dest_file)

    doc.close()
    return created_files


def split_to_individual_pages(
    input_path: Union[str, Path],
    output_dir: Union[str, Path],
    output_prefix: str = "page"
) -> List[Path]:
    """
    Splits every page of a PDF file into individual 1-page PDF documents.

    :param input_path: Path to input PDF file.
    :param output_dir: Destination directory.
    :param output_prefix: Prefix for output single-page files.
    :return: List of created file paths.
    """
    total_pages = validate_pdf(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    created_files: List[Path] = []

    digits = max(3, len(str(total_pages)))
    for idx in range(total_pages):
        writer = PdfWriter()
        writer.add_page(reader.pages[idx])

        dest_file = out_dir / f"{output_prefix}_{idx+1:0{digits}d}.pdf"
        with open(dest_file, "wb") as f:
            writer.write(f)
        created_files.append(dest_file)

    return created_files
