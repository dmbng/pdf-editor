"""
Command-Line Interface for PDF Editor.
"""

import sys
import argparse
from pathlib import Path
from typing import List, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None

from pdf_editor import __version__
from pdf_editor.core.utils import PDFEditorError, validate_pdf
from pdf_editor.core.merger import merge_pdfs
from pdf_editor.core.splitter import split_by_ranges, split_by_bookmarks, split_to_individual_pages
from pdf_editor.core.editor import rotate_pages, reorder_pages, delete_pages, extract_pages, replace_pages
from pdf_editor.core.text_editor import replace_text, add_text, redact_text, search_text
from pdf_editor.core.word_export import export_to_docx, available_methods
from pdf_editor.core.compress import compress_pdf, format_size, LEVELS, describe_levels
from pdf_editor.core.doc_import import (
    convert_to_pdf,
    available_backends,
    default_destination,
    SUPPORTED_EXTENSIONS,
)
from pdf_editor.core.utils import parse_page_ranges


def print_success(message: str) -> None:
    if HAS_RICH:
        console.print(f"[bold green][SUCCESS][/bold green] {message}")
    else:
        print(f"[SUCCESS] {message}")


def print_error(message: str) -> None:
    if HAS_RICH:
        console.print(f"[bold red][ERROR][/bold red] {message}")
    else:
        print(f"[ERROR] {message}", file=sys.stderr)


def print_info(message: str) -> None:
    if HAS_RICH:
        console.print(f"[bold cyan][INFO][/bold cyan] {message}")
    else:
        print(f"[INFO] {message}")


def parse_color_string(color_str: str) -> tuple:
    """Parses a color string like '0,0,0' or '255,255,255' or '1.0,0.0,0.0' into (R,G,B) 0.0-1.0 floats."""
    parts = [p.strip() for p in color_str.split(",")]
    if len(parts) != 3:
        raise PDFEditorError(f"Color must be formatted as 'R,G,B' (got '{color_str}')")
    try:
        vals = [float(p) for p in parts]
        if any(v > 1.0 for v in vals):
            # Scale 0-255 to 0.0-1.0
            vals = [v / 255.0 for v in vals]
        return (vals[0], vals[1], vals[2])
    except ValueError:
        raise PDFEditorError(f"Invalid numeric values in color specification: '{color_str}'")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-editor",
        description="A powerful command-line toolkit to merge, split, edit pages, and edit text in PDF files.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available operations")

    # Merge subcommand
    p_merge = subparsers.add_parser("merge", help="Merge multiple PDF files into one")
    p_merge.add_argument("-i", "--inputs", nargs="+", required=True, help="List of PDF files to merge")
    p_merge.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_merge.add_argument("--no-bookmarks", action="store_true", help="Disable adding outline bookmarks per source PDF")

    # Split subcommand
    p_split = subparsers.add_parser("split", help="Split a PDF into separate files")
    p_split.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_split.add_argument("-o", "--output-dir", required=True, help="Output directory for split PDFs")
    p_split.add_argument("--ranges", help="Page range specs separated by commas (e.g. '1-3,4-6,7-end')")
    p_split.add_argument("--by-bookmarks", action="store_true", help="Split document at top-level bookmarks")
    p_split.add_argument("--all", action="store_true", help="Split every single page into an individual file")

    # Rotate subcommand
    p_rotate = subparsers.add_parser("rotate", help="Rotate pages in a PDF")
    p_rotate.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_rotate.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_rotate.add_argument("-a", "--angle", type=int, required=True, choices=[90, 180, 270, 360], help="Rotation angle degrees")
    p_rotate.add_argument("-p", "--pages", default="all", help="Target pages range spec (default: 'all')")

    # Reorder subcommand
    p_reorder = subparsers.add_parser("reorder", help="Reorder pages in a PDF")
    p_reorder.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_reorder.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_reorder.add_argument("--order", required=True, help="Comma-separated 1-based page order (e.g. '3,1,2,4')")

    # Delete subcommand
    p_delete = subparsers.add_parser("delete", help="Delete pages from a PDF")
    p_delete.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_delete.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_delete.add_argument("-p", "--pages", required=True, help="Pages range spec to delete (e.g. '2,4-6')")

    # Extract subcommand
    p_extract = subparsers.add_parser("extract", help="Extract pages to a new PDF")
    p_extract.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_extract.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_extract.add_argument("-p", "--pages", required=True, help="Pages range spec to extract (e.g. '1-3,5')")

    # Replace pages subcommand
    p_replace_p = subparsers.add_parser("replace-pages", help="Replace target pages with pages from another PDF")
    p_replace_p.add_argument("-i", "--input", required=True, help="Target main PDF file path")
    p_replace_p.add_argument("-r", "--replacement", required=True, help="Replacement source PDF file path")
    p_replace_p.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_replace_p.add_argument("--target-pages", required=True, help="Target pages range in main PDF to replace")
    p_replace_p.add_argument("--source-pages", default="all", help="Source pages range in replacement PDF (default: 'all')")

    # Replace text subcommand
    p_repl_txt = subparsers.add_parser("replace-text", help="Find and replace text within PDF pages")
    p_repl_txt.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_repl_txt.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_repl_txt.add_argument("--old", required=True, help="Text to search for and replace")
    p_repl_txt.add_argument("--new", required=True, help="Replacement text")
    p_repl_txt.add_argument("-p", "--pages", default="all", help="Pages range spec (default: 'all')")
    p_repl_txt.add_argument("--case-insensitive", action="store_true", help="Perform case-insensitive search")
    p_repl_txt.add_argument("--color", default="0,0,0", help="Replacement text color as 'R,G,B' (default: '0,0,0')")

    # Add text subcommand
    p_add_txt = subparsers.add_parser("add-text", help="Add new text overlay at coordinates")
    p_add_txt.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_add_txt.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_add_txt.add_argument("-t", "--text", required=True, help="Text string to insert")
    p_add_txt.add_argument("--page", type=int, default=1, help="1-based page number (default: 1)")
    p_add_txt.add_argument("-x", type=float, default=50.0, help="X coordinate from left margin (default: 50.0)")
    p_add_txt.add_argument("-y", type=float, default=50.0, help="Y coordinate from top margin (default: 50.0)")
    p_add_txt.add_argument("--font-size", type=float, default=12.0, help="Font size in points (default: 12.0)")
    p_add_txt.add_argument("--color", default="0,0,0", help="Font color as 'R,G,B' (default: '0,0,0')")

    # Redact text subcommand
    p_redact = subparsers.add_parser("redact-text", help="Permanently redact/blackout text")
    p_redact.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_redact.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_redact.add_argument("-t", "--target", required=True, help="Target string to redact")
    p_redact.add_argument("-p", "--pages", default="all", help="Target pages range spec (default: 'all')")
    p_redact.add_argument("--color", default="0,0,0", help="Redaction fill color as 'R,G,B' (default: '0,0,0' black)")

    # Search text subcommand
    p_search = subparsers.add_parser("search-text", help="Search for text matches and print locations")
    p_search.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_search.add_argument("-q", "--query", required=True, help="Search query text")
    p_search.add_argument("-p", "--pages", default="all", help="Target pages range spec (default: 'all')")

    # Export to Word subcommand
    p_word = subparsers.add_parser("to-word", help="Export a PDF to a Word (.docx) document")
    p_word.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_word.add_argument("-o", "--output", required=True, help="Output .docx file path")
    p_word.add_argument("-p", "--pages", default="all", help="Pages to export (e.g. '1-3,5'; default: 'all')")
    p_word.add_argument("--method", choices=["auto", "layout", "text"], default="auto",
                        help="'layout' keeps the page layout, 'text' writes text and images only")

    # Compress subcommand
    p_compress = subparsers.add_parser("compress", help="Write a smaller copy of a PDF")
    p_compress.add_argument("-i", "--input", required=True, help="Input PDF file path")
    p_compress.add_argument("-o", "--output", required=True, help="Output PDF file path")
    p_compress.add_argument("-l", "--level", choices=list(LEVELS), default="medium",
                            help="Compression level. " + "; ".join(describe_levels()))
    p_compress.add_argument("--keep-images", action="store_true",
                            help="Never recompress images (structure only, fully lossless)")

    # Import documents subcommand
    p_import = subparsers.add_parser(
        "import-doc", help="Convert a Word, Excel or PowerPoint document into a PDF"
    )
    p_import.add_argument("-i", "--input", required=True,
                          help="Input document (" + ", ".join(sorted(SUPPORTED_EXTENSIONS)) + ")")
    p_import.add_argument("-o", "--output", default=None,
                          help="Output PDF file path (default: same name as the input)")
    p_import.add_argument("--backend", choices=["auto", "office", "builtin"], default="auto",
                          help="'office' uses Microsoft Office, 'builtin' uses python-docx/openpyxl/python-pptx")

    # Info subcommand
    p_info = subparsers.add_parser("info", help="Display PDF metadata, page count, and bookmarks")
    p_info.add_argument("-i", "--input", required=True, help="Input PDF file path")

    return parser


def main(args: Optional[List[str]] = None) -> int:
    parser = create_parser()
    parsed_args = parser.parse_args(args)

    if not parsed_args.command:
        parser.print_help()
        return 0

    cmd = parsed_args.command

    try:
        if cmd == "merge":
            dest = merge_pdfs(
                input_paths=parsed_args.inputs,
                output_path=parsed_args.output,
                add_bookmarks=not parsed_args.no_bookmarks
            )
            print_success(f"Merged {len(parsed_args.inputs)} files into '{dest}'")

        elif cmd == "split":
            if parsed_args.all:
                files = split_to_individual_pages(parsed_args.input, parsed_args.output_dir)
                print_success(f"Split document into {len(files)} single-page files in '{parsed_args.output_dir}'")
            elif parsed_args.by_bookmarks:
                files = split_by_bookmarks(parsed_args.input, parsed_args.output_dir)
                print_success(f"Split document into {len(files)} section files by bookmarks in '{parsed_args.output_dir}'")
            elif parsed_args.ranges:
                r_list = [r.strip() for r in parsed_args.ranges.split(",") if r.strip()]
                files = split_by_ranges(parsed_args.input, r_list, parsed_args.output_dir)
                print_success(f"Split document into {len(files)} files by specified ranges in '{parsed_args.output_dir}'")
            else:
                print_error("Please specify split mode: --ranges, --by-bookmarks, or --all")
                return 1

        elif cmd == "rotate":
            dest = rotate_pages(
                input_path=parsed_args.input,
                output_path=parsed_args.output,
                page_angles=parsed_args.angle,
                target_pages=parsed_args.pages
            )
            print_success(f"Rotated pages by {parsed_args.angle} degrees saved to '{dest}'")

        elif cmd == "reorder":
            order_list = [int(p.strip()) for p in parsed_args.order.split(",") if p.strip()]
            dest = reorder_pages(parsed_args.input, parsed_args.output, order_list)
            print_success(f"Reordered pages saved to '{dest}'")

        elif cmd == "delete":
            dest = delete_pages(parsed_args.input, parsed_args.output, parsed_args.pages)
            print_success(f"Deleted pages ({parsed_args.pages}) saved to '{dest}'")

        elif cmd == "extract":
            dest = extract_pages(parsed_args.input, parsed_args.output, parsed_args.pages)
            print_success(f"Extracted pages ({parsed_args.pages}) saved to '{dest}'")

        elif cmd == "replace-pages":
            dest = replace_pages(
                input_path=parsed_args.input,
                output_path=parsed_args.output,
                target_pages=parsed_args.target_pages,
                replacement_pdf_path=parsed_args.replacement,
                replacement_pages=parsed_args.source_pages
            )
            print_success(f"Replaced target pages with replacement PDF saved to '{dest}'")

        elif cmd == "replace-text":
            color_rgb = parse_color_string(parsed_args.color)
            count = replace_text(
                input_path=parsed_args.input,
                output_path=parsed_args.output,
                old_text=parsed_args.old,
                new_text=parsed_args.new,
                pages=parsed_args.pages,
                case_sensitive=not parsed_args.case_insensitive,
                text_color=color_rgb
            )
            print_success(f"Replaced {count} occurrences of '{parsed_args.old}' with '{parsed_args.new}'. Output saved to '{parsed_args.output}'")

        elif cmd == "add-text":
            color_rgb = parse_color_string(parsed_args.color)
            dest = add_text(
                input_path=parsed_args.input,
                output_path=parsed_args.output,
                text=parsed_args.text,
                page_num=parsed_args.page,
                x=parsed_args.x,
                y=parsed_args.y,
                font_size=parsed_args.font_size,
                text_color=color_rgb
            )
            print_success(f"Added text overlay to page {parsed_args.page} saved to '{dest}'")

        elif cmd == "redact-text":
            color_rgb = parse_color_string(parsed_args.color)
            count = redact_text(
                input_path=parsed_args.input,
                output_path=parsed_args.output,
                target_text=parsed_args.target,
                pages=parsed_args.pages,
                fill_color=color_rgb
            )
            print_success(f"Redacted {count} occurrences of '{parsed_args.target}'. Output saved to '{parsed_args.output}'")

        elif cmd == "search-text":
            results = search_text(parsed_args.input, parsed_args.query, parsed_args.pages)
            if not results:
                print_info(f"No occurrences of '{parsed_args.query}' found.")
            else:
                print_info(f"Found {len(results)} match(es) for '{parsed_args.query}':")
                if HAS_RICH:
                    table = Table(title=f"Search Results for '{parsed_args.query}'")
                    table.add_column("Page", style="cyan", justify="right")
                    table.add_column("Bounding Box (x0, y0, x1, y1)", style="magenta")
                    table.add_column("Snippet Context", style="green")
                    for r in results:
                        table.add_row(str(r["page"]), str(r["rect"]), r["snippet"])
                    console.print(table)
                else:
                    for r in results:
                        print(f"  Page {r['page']} | Box: {r['rect']} | '{r['snippet']}'")

        elif cmd == "to-word":
            total_pages = validate_pdf(parsed_args.input)
            pages = None
            if parsed_args.pages.strip().lower() != "all":
                pages = parse_page_ranges(parsed_args.pages, total_pages)
            if not available_methods():
                print_error("Word export needs the 'pdf2docx' package: pip install pdf2docx")
                return 1

            report = export_to_docx(
                parsed_args.input,
                parsed_args.output,
                pages=pages,
                method=parsed_args.method,
                progress=lambda fraction, message: print_info(f"{fraction * 100:3.0f}%  {message}"),
            )
            print_success(report.summary)
            for warning in report.warnings:
                print_info(warning)

        elif cmd == "compress":
            validate_pdf(parsed_args.input)
            report = compress_pdf(
                parsed_args.input,
                parsed_args.output,
                level=parsed_args.level,
                downsample_images=not parsed_args.keep_images,
                progress=lambda fraction, message: print_info(f"{fraction * 100:3.0f}%  {message}"),
            )
            print_success(
                f"{report.destination}: {format_size(report.original_bytes)} -> "
                f"{format_size(report.compressed_bytes)} ({report.ratio * 100:.0f}% smaller)"
            )
            for warning in report.warnings:
                print_info(warning)

        elif cmd == "import-doc":
            source = Path(parsed_args.input)
            if not source.exists():
                print_error(f"Document not found: '{source}'")
                return 1
            if not available_backends(source):
                print_error(
                    "No converter is available for this file. Install Microsoft Office, "
                    "or the python-docx, openpyxl, python-pptx and reportlab packages."
                )
                return 1

            destination = parsed_args.output or default_destination(source)
            report = convert_to_pdf(
                source,
                destination,
                backend=parsed_args.backend,
                progress=lambda fraction, message: print_info(f"{fraction * 100:3.0f}%  {message}"),
            )
            print_success(report.summary)
            for warning in report.warnings:
                print_info(warning)

        elif cmd == "info":
            total_pages = validate_pdf(parsed_args.input)
            import pymupdf as fitz
            doc = fitz.open(parsed_args.input)
            metadata = doc.metadata
            toc = doc.get_toc()
            doc.close()

            if HAS_RICH:
                panel_text = (
                    f"[bold]File:[/bold] {parsed_args.input}\n"
                    f"[bold]Total Pages:[/bold] {total_pages}\n"
                    f"[bold]Title:[/bold] {metadata.get('title', 'N/A')}\n"
                    f"[bold]Author:[/bold] {metadata.get('author', 'N/A')}\n"
                    f"[bold]Producer:[/bold] {metadata.get('producer', 'N/A')}\n"
                    f"[bold]Bookmarks/Outline Count:[/bold] {len(toc)}"
                )
                console.print(Panel(panel_text, title="PDF Information", border_style="blue"))
                if toc:
                    t_table = Table(title="Bookmarks Outline")
                    t_table.add_column("Level", justify="right")
                    t_table.add_column("Title", style="yellow")
                    t_table.add_column("Target Page", justify="right")
                    for lvl, title, page in toc:
                        t_table.add_row(str(lvl), title, str(page))
                    console.print(t_table)
            else:
                print(f"File: {parsed_args.input}")
                print(f"Total Pages: {total_pages}")
                print(f"Title: {metadata.get('title', 'N/A')}")
                print(f"Bookmarks Count: {len(toc)}")
                if toc:
                    for lvl, title, page in toc:
                        print(f"  {'  ' * (lvl - 1)}- {title} (Page {page})")

        return 0

    except PDFEditorError as e:
        print_error(str(e))
        return 1
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
