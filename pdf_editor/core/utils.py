"""
Utility functions, validation, page range parsing, and exception definitions for PDF Editor.
"""

import os
from pathlib import Path
from typing import List, Union
import pymupdf as fitz
from pypdf import PdfReader


class PDFEditorError(Exception):
    """Base exception class for PDF Editor errors."""
    pass


class FileNotFoundPDFError(PDFEditorError):
    """Raised when an input PDF file is not found."""
    pass


class CorruptedPDFError(PDFEditorError):
    """Raised when an input PDF file is corrupted or invalid."""
    pass


class EncryptedPDFError(PDFEditorError):
    """Raised when an input PDF file is password protected/encrypted."""
    pass


class InvalidPageRangeError(PDFEditorError):
    """Raised when a specified page range or index is invalid or out of bounds."""
    pass


class UnsupportedOperationError(PDFEditorError):
    """Raised when an edit cannot be performed on the given document/selection."""
    pass


class ImageOperationError(PDFEditorError):
    """Raised when an image cannot be read, decoded, or placed on a page."""
    pass


class TextEditError(PDFEditorError):
    """Raised when a text selection cannot be edited (e.g. scanned/image-only page)."""
    pass


def validate_pdf(file_path: Union[str, Path], must_exist: bool = True) -> int:
    """
    Validates a PDF file: checks existence, readability, encryption, and corruption.

    :param file_path: Path to the PDF file.
    :param must_exist: Whether to require the file to exist on disk.
    :return: Total number of pages in the PDF file.
    :raises FileNotFoundPDFError: If file does not exist.
    :raises CorruptedPDFError: If file is not a valid PDF or corrupted.
    :raises EncryptedPDFError: If file is password protected and cannot be read.
    """
    path = Path(file_path)
    if must_exist and not path.exists():
        raise FileNotFoundPDFError(f"PDF file not found: '{file_path}'")

    if not path.is_file():
        raise FileNotFoundPDFError(f"Specified path is not a file: '{file_path}'")

    # Fast header check
    try:
        with open(path, "rb") as f:
            header = f.read(5)
            if not header.startswith(b"%PDF-"):
                raise CorruptedPDFError(f"File '{file_path}' does not have a valid PDF header.")
    except (OSError, IOError) as e:
        raise CorruptedPDFError(f"Cannot read file '{file_path}': {str(e)}")

    # Check with fitz for corruption and page count
    try:
        doc = fitz.open(path)
        if doc.is_encrypted:
            # Try opening with empty password
            if not doc.authenticate(""):
                doc.close()
                raise EncryptedPDFError(f"PDF file '{file_path}' is encrypted and requires a password.")
        
        page_count = doc.page_count
        doc.close()
        return page_count
    except EncryptedPDFError:
        raise
    except Exception as e:
        # Fallback check with pypdf
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    raise EncryptedPDFError(f"PDF file '{file_path}' is encrypted with a password.")
            return len(reader.pages)
        except EncryptedPDFError:
            raise
        except Exception as pypdf_err:
            raise CorruptedPDFError(f"PDF file '{file_path}' is corrupted or unreadable: {str(e)} / {str(pypdf_err)}")


def parse_page_ranges(range_str: str, total_pages: int) -> List[int]:
    """
    Parses human-readable 1-based page ranges (e.g. '1-3,5,7-end', 'all', 'even', 'odd')
    into a list of 0-based page indices.

    :param range_str: Page specification string.
    :param total_pages: Total number of pages available in the document.
    :return: Sorted list of unique 0-based page indices.
    :raises InvalidPageRangeError: If range format is invalid or indices out of bounds.
    """
    if not range_str or not range_str.strip():
        raise InvalidPageRangeError("Page range string cannot be empty.")

    clean_str = range_str.strip().lower()

    if total_pages <= 0:
        raise InvalidPageRangeError("Total document pages must be greater than 0.")

    if clean_str == "all":
        return list(range(total_pages))
    if clean_str == "even":
        return [i for i in range(total_pages) if (i + 1) % 2 == 0]
    if clean_str == "odd":
        return [i for i in range(total_pages) if (i + 1) % 2 != 0]

    pages = set()
    parts = [p.strip() for p in clean_str.split(",") if p.strip()]

    for part in parts:
        if "-" in part:
            subparts = part.split("-")
            if len(subparts) != 2:
                raise InvalidPageRangeError(f"Invalid range specification: '{part}'")
            
            start_str, end_str = subparts[0].strip(), subparts[1].strip()
            
            # Handle start index
            if start_str == "start" or start_str == "":
                start = 1
            else:
                try:
                    start = int(start_str)
                except ValueError:
                    raise InvalidPageRangeError(f"Invalid page number '{start_str}' in range '{part}'")

            # Handle end index
            if end_str == "end" or end_str == "":
                end = total_pages
            else:
                try:
                    end = int(end_str)
                except ValueError:
                    raise InvalidPageRangeError(f"Invalid page number '{end_str}' in range '{part}'")

            if start < 1:
                raise InvalidPageRangeError(f"Page numbers must be >= 1 (got {start})")
            if end > total_pages:
                raise InvalidPageRangeError(f"Page number {end} exceeds total pages ({total_pages})")
            if start > end:
                raise InvalidPageRangeError(f"Start page {start} cannot be greater than end page {end}")

            for p in range(start, end + 1):
                pages.add(p - 1)
        else:
            try:
                p_num = int(part)
            except ValueError:
                raise InvalidPageRangeError(f"Invalid page number entry: '{part}'")

            if p_num < 1:
                raise InvalidPageRangeError(f"Page numbers must be >= 1 (got {p_num})")
            if p_num > total_pages:
                raise InvalidPageRangeError(f"Page number {p_num} exceeds total pages ({total_pages})")
            
            pages.add(p_num - 1)

    result = sorted(list(pages))
    if not result:
        raise InvalidPageRangeError(f"No valid pages selected by range '{range_str}'")
    return result
