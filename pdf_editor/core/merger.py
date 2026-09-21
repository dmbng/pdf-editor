"""
Module for merging multiple PDF files into a single output document.
"""

from pathlib import Path
from typing import List, Union
from pypdf import PdfWriter
from pdf_editor.core.utils import validate_pdf, PDFEditorError, InvalidPageRangeError


def merge_pdfs(
    input_paths: List[Union[str, Path]],
    output_path: Union[str, Path],
    add_bookmarks: bool = True
) -> Path:
    """
    Merges multiple PDF files into a single document.

    :param input_paths: List of paths to input PDF files.
    :param output_path: Destination path for the merged PDF.
    :param add_bookmarks: If True, adds table of contents bookmarks for each merged file.
    :return: Path object of the created merged PDF file.
    :raises PDFEditorError: If fewer than 2 input files provided or validation fails.
    """
    if not input_paths or len(input_paths) < 2:
        raise PDFEditorError("At least 2 input PDF files are required for merging.")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    writer = PdfWriter()

    try:
        for idx, path_item in enumerate(input_paths):
            p = Path(path_item)
            validate_pdf(p)  # Validates existence, encryption, and corruption
            bookmark_title = p.stem if add_bookmarks else None
            writer.append(str(p), outline_item=bookmark_title)

        writer.write(str(out_file))
        return out_file
    except PDFEditorError:
        raise
    except Exception as e:
        raise PDFEditorError(f"Error during PDF merge operation: {str(e)}")
    finally:
        writer.close()
