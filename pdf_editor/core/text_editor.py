"""
Module for in-document text operations: searching, finding & replacing text, adding text overlays, and redacting text.
"""

from pathlib import Path
from typing import List, Union, Tuple, Dict, Any, Optional
import pymupdf as fitz
from pdf_editor.core.utils import validate_pdf, parse_page_ranges, PDFEditorError, InvalidPageRangeError


def replace_text(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    old_text: str,
    new_text: str,
    pages: str = "all",
    case_sensitive: bool = True,
    text_color: Optional[Tuple[float, float, float]] = (0, 0, 0),  # RGB tuple 0.0-1.0
    bg_color: Optional[Tuple[float, float, float]] = (1, 1, 1)    # White background for redaction
) -> int:
    """
    Replaces occurrences of old_text with new_text in the specified pages.
    Redacts original text and inserts replacement text cleanly.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param old_text: String text to search for and replace.
    :param new_text: Replacement text string.
    :param pages: Page range spec (e.g. 'all', '1-3', '5').
    :param case_sensitive: Whether match should be case-sensitive.
    :param text_color: RGB tuple for replacement text color (e.g. (0, 0, 0) for black).
    :param bg_color: RGB tuple for redaction box fill color (e.g. (1, 1, 1) for white).
    :return: Total number of text replacements performed.
    """
    total_pages = validate_pdf(input_path)
    if not old_text:
        raise PDFEditorError("Search text 'old_text' cannot be empty.")

    target_indices = parse_page_ranges(pages, total_pages)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(input_path))
    replacements_count = 0

    flags = 0 if case_sensitive else fitz.TEXT_DEHYPHENATE  # basic flags

    for idx in target_indices:
        page = doc[idx]
        rects = page.search_for(old_text)
        
        if not rects and not case_sensitive:
            # Case insensitive search fallback if standard search yielded empty
            text_instances = page.get_text("words")
            # find matching words
            old_lower = old_text.lower()
            rects = [fitz.Rect(w[:4]) for w in text_instances if old_lower in w[4].lower()]

        if not rects:
            continue

        for rect in rects:
            replacements_count += 1
            
            # Step 1: Redact existing text
            page.add_redact_annot(rect, fill=bg_color)
            page.apply_redactions()

            # Step 2: Calculate appropriate font size from rect height
            # Height of box gives estimated baseline to cap height
            font_size = max(7.0, rect.height * 0.75)
            
            # Insert replacement text at baseline point
            baseline_point = fitz.Point(rect.x0, rect.y1 - (rect.height * 0.15))
            page.insert_text(
                baseline_point,
                new_text,
                fontsize=font_size,
                fontname="helv",
                color=text_color
            )

    doc.save(str(out_file))
    doc.close()
    return replacements_count


def add_text(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    text: str,
    page_num: int = 1,
    x: float = 50.0,
    y: float = 50.0,
    font_size: float = 12.0,
    text_color: Tuple[float, float, float] = (0, 0, 0),
    font_name: str = "helv"
) -> Path:
    """
    Inserts a new text string overlay at specified coordinates on a given page.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param text: Text string to insert.
    :param page_num: 1-based page number.
    :param x: X-coordinate in PDF points from left margin.
    :param y: Y-coordinate in PDF points from top margin.
    :param font_size: Size of font in points.
    :param text_color: RGB tuple for font color (0.0 - 1.0).
    :param font_name: Standard font name ('helv', 'times', 'cour').
    :return: Path object of modified PDF.
    """
    total_pages = validate_pdf(input_path)
    if page_num < 1 or page_num > total_pages:
        raise InvalidPageRangeError(f"Page number {page_num} is out of bounds (1-{total_pages}).")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(input_path))
    page = doc[page_num - 1]

    point = fitz.Point(x, y)
    page.insert_text(point, text, fontsize=font_size, fontname=font_name, color=text_color)

    doc.save(str(out_file))
    doc.close()
    return out_file


def redact_text(
    input_path: Union[str, Path],
    output_path: Union[str, Path],
    target_text: str,
    pages: str = "all",
    fill_color: Tuple[float, float, float] = (0, 0, 0)  # Blackout default
) -> int:
    """
    Permanently redacts (blacks out/whites out) sensitive text matches.

    :param input_path: Path to input PDF file.
    :param output_path: Path to output PDF file.
    :param target_text: Text string to redact.
    :param pages: Target page range specification.
    :param fill_color: RGB fill color tuple (e.g. (0,0,0) for black, (1,1,1) for white).
    :return: Total number of redacted text areas.
    """
    total_pages = validate_pdf(input_path)
    if not target_text:
        raise PDFEditorError("Redaction target text cannot be empty.")

    target_indices = parse_page_ranges(pages, total_pages)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(input_path))
    redactions_count = 0

    for idx in target_indices:
        page = doc[idx]
        rects = page.search_for(target_text)
        for rect in rects:
            redactions_count += 1
            page.add_redact_annot(rect, fill=fill_color)
        if rects:
            page.apply_redactions()

    doc.save(str(out_file))
    doc.close()
    return redactions_count


def search_text(
    input_path: Union[str, Path],
    query: str,
    pages: str = "all"
) -> List[Dict[str, Any]]:
    """
    Searches for query string in specified PDF pages and returns match locations and snippets.

    :param input_path: Path to input PDF file.
    :param query: Text string to search for.
    :param pages: Page range specification.
    :return: List of dicts containing match details: page, rect, snippet.
    """
    total_pages = validate_pdf(input_path)
    if not query:
        raise PDFEditorError("Search query cannot be empty.")

    target_indices = parse_page_ranges(pages, total_pages)
    doc = fitz.open(str(input_path))
    results = []

    for idx in target_indices:
        page = doc[idx]
        rects = page.search_for(query)
        for rect in rects:
            # Extract surrounding snippet if possible
            snippet_rect = fitz.Rect(max(0, rect.x0 - 50), max(0, rect.y0 - 10), min(page.rect.width, rect.x1 + 50), min(page.rect.height, rect.y1 + 10))
            snippet = page.get_text("text", clip=snippet_rect).replace("\n", " ").strip()
            
            results.append({
                "page": idx + 1,
                "rect": (round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)),
                "snippet": snippet
            })

    doc.close()
    return results
