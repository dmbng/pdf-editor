"""
Unit tests for PDF compression.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import compress
from pdf_editor.core.utils import PDFEditorError


def page_texts(path):
    """Returns the text of every page of a PDF."""
    with fitz.open(path) as doc:
        return [page.get_text("text").strip() for page in doc]


def test_profiles_are_ordered():
    """The three levels get progressively more aggressive."""
    assert compress.LEVELS == ("low", "medium", "high")
    low, medium, high = (compress.PROFILES[key] for key in compress.LEVELS)

    assert low.jpeg_quality > medium.jpeg_quality > high.jpeg_quality
    assert low.resamples is False  # Low never lowers the resolution.
    assert medium.resamples and high.resamples
    assert medium.dpi_target > high.dpi_target
    assert all(profile.description for profile in compress.PROFILES.values())


@pytest.mark.parametrize("level", ["low", "medium", "high"])
def test_compression_keeps_the_document_readable(photo_pdf, tmp_path, level):
    """Every level keeps pages, text and images intact."""
    target = tmp_path / f"{level}.pdf"
    report = compress.compress_pdf(photo_pdf, target, level=level)

    assert target.exists()
    assert report.level == level
    assert report.compressed_bytes <= report.original_bytes

    with fitz.open(target) as doc:
        assert doc.page_count == 2
        assert "Photo page 1" in doc[0].get_text("text")
        assert len(doc[0].get_images(full=True)) == 1


def test_higher_levels_produce_smaller_files(photo_pdf, tmp_path):
    """Medium beats low, and high beats medium."""
    sizes = {}
    for level in compress.LEVELS:
        report = compress.compress_pdf(photo_pdf, tmp_path / f"{level}.pdf", level=level)
        sizes[level] = report.compressed_bytes

    assert sizes["medium"] < sizes["low"]
    assert sizes["high"] < sizes["medium"]
    assert sizes["high"] < photo_pdf.stat().st_size / 2


def test_report_numbers_are_consistent(photo_pdf, tmp_path):
    """The report adds up and describes itself."""
    report = compress.compress_pdf(photo_pdf, tmp_path / "out.pdf", level="high")

    assert report.original_bytes == photo_pdf.stat().st_size
    assert report.compressed_bytes == report.destination.stat().st_size
    assert report.saved_bytes == report.original_bytes - report.compressed_bytes
    assert 0.0 < report.ratio < 1.0
    assert report.images == 1
    assert report.image_bytes_after < report.image_bytes_before
    assert "smaller" in report.summary
    assert report.duration >= 0.0


def test_lossless_mode_leaves_images_alone(photo_pdf, tmp_path):
    """Turning off image processing keeps every pixel."""
    report = compress.compress_pdf(
        photo_pdf, tmp_path / "lossless.pdf", level="high", downsample_images=False
    )
    assert report.image_bytes_after == report.image_bytes_before

    with fitz.open(report.destination) as doc:
        info = doc[0].get_image_info()[0]
        assert info["width"] == 1200
        assert info["height"] == 900


def test_already_compressed_file_is_left_alone(photo_pdf, tmp_path):
    """Compressing twice keeps the smaller original and says so."""
    first = compress.compress_pdf(photo_pdf, tmp_path / "once.pdf", level="high")
    second = compress.compress_pdf(first.destination, tmp_path / "twice.pdf", level="high")

    assert second.compressed_bytes <= first.compressed_bytes
    if second.saved_bytes == 0:
        assert second.warnings
        assert "already compressed" in second.warnings[0]
        assert "already compact" in second.summary


def test_keep_larger_writes_the_bigger_result(simple_pdf, tmp_path):
    """The safety net can be switched off deliberately."""
    report = compress.compress_pdf(
        simple_pdf, tmp_path / "forced.pdf", level="high", keep_larger=True
    )
    assert report.destination.exists()
    assert not any("already compressed" in warning for warning in report.warnings)


def test_compress_an_open_document(paragraph_pdf, tmp_path):
    """Unsaved edits are included when an open document is compressed."""
    document = fitz.open(paragraph_pdf)
    try:
        document[0].insert_text(fitz.Point(60, 500), "UNSAVED MARKER", fontsize=12)
        report = compress.compress_pdf(document, tmp_path / "live.pdf", level="medium")
    finally:
        document.close()

    assert "UNSAVED MARKER" in page_texts(report.destination)[0]


def test_metadata_and_bookmarks_survive(simple_pdf, tmp_path):
    """Compression preserves the document information."""
    with fitz.open(simple_pdf) as doc:
        doc.set_toc([[1, "Start", 1], [1, "Later", 3]])
        source = tmp_path / "with_toc.pdf"
        doc.save(str(source))

    report = compress.compress_pdf(source, tmp_path / "small.pdf", level="medium")
    with fitz.open(report.destination) as doc:
        assert doc.metadata["title"] == "Simple"
        assert doc.metadata["author"] == "Test Suite"
        assert [entry[1] for entry in doc.get_toc()] == ["Start", "Later"]


def test_destination_suffix_and_folders(photo_pdf, tmp_path):
    """The .pdf suffix is added and missing folders are created."""
    report = compress.compress_pdf(photo_pdf, tmp_path / "nested" / "out", level="low")
    assert report.destination.suffix == ".pdf"
    assert report.destination.exists()


def test_progress_is_reported(photo_pdf, tmp_path):
    """The progress callback runs from zero to one."""
    seen = []
    compress.compress_pdf(
        photo_pdf, tmp_path / "p.pdf", level="medium",
        progress=lambda fraction, message: seen.append(fraction),
    )
    assert seen
    assert seen[-1] == pytest.approx(1.0)
    assert seen == sorted(seen)


def test_unknown_level_and_missing_file(photo_pdf, tmp_path):
    """Bad arguments produce readable errors."""
    with pytest.raises(PDFEditorError) as error:
        compress.compress_pdf(photo_pdf, tmp_path / "x.pdf", level="extreme")
    assert "low" in str(error.value)

    with pytest.raises(PDFEditorError):
        compress.compress_pdf(tmp_path / "missing.pdf", tmp_path / "y.pdf")


def test_format_size():
    """Byte counts are formatted for humans."""
    assert compress.format_size(0) == "0 B"
    assert compress.format_size(999) == "999 B"
    assert compress.format_size(2048) == "2.0 KB"
    assert compress.format_size(5 * 1024 * 1024) == "5.0 MB"
    assert compress.format_size(-5) == "0 B"


def test_describe_levels():
    """Every level has a help line."""
    lines = compress.describe_levels()
    assert len(lines) == 3
    assert all(" - " in line for line in lines)
