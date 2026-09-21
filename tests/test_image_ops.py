"""
Unit tests for image insertion, moving, resizing and deletion.
"""

import pymupdf as fitz
import pytest

from pdf_editor.core import image_ops
from pdf_editor.core.image_ops import ImageInfo, PendingImage
from pdf_editor.core.utils import ImageOperationError


def test_read_image_info(png_file):
    """Reading a valid image reports its pixel size."""
    info = image_ops.read_image_info(png_file)
    assert (info.width, info.height) == (120, 60)
    assert info.aspect == pytest.approx(2.0)


def test_read_image_info_rejects_bad_input(tmp_path):
    """Missing files, wrong extensions and broken data are refused."""
    with pytest.raises(ImageOperationError):
        image_ops.read_image_info(tmp_path / "nope.png")

    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("hello")
    with pytest.raises(ImageOperationError):
        image_ops.read_image_info(unsupported)

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image at all")
    with pytest.raises(ImageOperationError):
        image_ops.read_image_info(corrupt)


def test_fit_rect_preserves_aspect():
    """Fitting centres the image and keeps its ratio."""
    fitted = image_ops.fit_rect(fitz.Rect(0, 0, 200, 200), 2.0)
    assert fitted.width == pytest.approx(200)
    assert fitted.height == pytest.approx(100)
    assert fitted.y0 == pytest.approx(50)


def test_default_placement_is_centred():
    """A new image starts centred on the page."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    rect = image_ops.default_placement(page, ImageInfo(200, 100, "PNG"), fraction=0.5)
    assert rect.width == pytest.approx(200)
    assert rect.height == pytest.approx(100)
    assert (rect.x0 + rect.x1) / 2 == pytest.approx(200)
    doc.close()


def test_insert_list_and_find(png_file):
    """An inserted image can be listed and picked up by a click."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)

    xref = image_ops.insert_image(page, fitz.Rect(50, 50, 250, 150), png_file)
    assert xref > 0

    images = image_ops.list_images(page)
    assert len(images) == 1
    assert images[0].bbox.x0 == pytest.approx(50, abs=1)

    assert image_ops.find_image_at(page, fitz.Point(100, 100)) is not None
    assert image_ops.find_image_at(page, fitz.Point(350, 400)) is None
    doc.close()


def test_insert_rejects_tiny_and_rotated_input(png_file):
    """Degenerate placements and invalid rotations are refused."""
    doc = fitz.open()
    page = doc.new_page()
    with pytest.raises(ImageOperationError):
        image_ops.insert_image(page, fitz.Rect(10, 10, 12, 12), png_file)
    with pytest.raises(ImageOperationError):
        image_ops.insert_image(page, fitz.Rect(10, 10, 200, 120), png_file, rotate=45)
    doc.close()


def test_move_image_relocates_and_keeps_other_content(png_file):
    """Moving an image leaves no visible copy behind and keeps the text."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    page.insert_text(fitz.Point(40, 40), "text stays here", fontsize=12)
    image_ops.insert_image(page, fitz.Rect(50, 60, 250, 160), png_file)

    ref = image_ops.find_image_at(page, fitz.Point(100, 100))
    moved = image_ops.move_image(page, ref, fitz.Rect(150, 300, 350, 400))

    images = image_ops.list_images(page)
    assert len(images) == 1
    assert images[0].xref == moved.xref
    assert images[0].bbox.x0 == pytest.approx(150, abs=1)
    assert "text stays here" in page.get_text("text")
    doc.close()


def test_delete_image(png_file):
    """A deleted image disappears from the page."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    image_ops.insert_image(page, fitz.Rect(50, 60, 250, 160), png_file)

    ref = image_ops.list_images(page)[0]
    image_ops.delete_image(page, ref)
    assert image_ops.list_images(page) == []
    doc.close()


def test_replace_image_keeps_geometry(png_file, tmp_path):
    """Replacing swaps the pixels but not the placement."""
    other = tmp_path / "other.png"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 30))
    pixmap.set_rect(pixmap.irect, (200, 30, 30))
    pixmap.save(str(other))

    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    image_ops.insert_image(page, fitz.Rect(50, 60, 250, 160), png_file)
    ref = image_ops.list_images(page)[0]

    image_ops.replace_image(page, ref, other)
    replaced = image_ops.list_images(page)[0]
    assert replaced.bbox.x0 == pytest.approx(ref.bbox.x0, abs=1)
    doc.close()


def test_extract_image_to_file(png_file, tmp_path):
    """An embedded image can be written back to disk."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    image_ops.insert_image(page, fitz.Rect(50, 60, 250, 160), png_file)
    ref = image_ops.list_images(page)[0]

    written = image_ops.extract_image_to_file(doc, ref.xref, tmp_path / "out")
    assert written.exists()
    assert written.stat().st_size > 0
    doc.close()


def test_pending_image_geometry(png_file):
    """The pending placement moves, resizes and stays inside the page."""
    info = image_ops.read_image_info(png_file)
    pending = PendingImage(path=png_file, rect=fitz.Rect(0, 0, 200, 100), info=info)

    pending.moved_to(50, 60)
    assert pending.rect == fitz.Rect(50, 60, 250, 160)

    pending.resize_to(fitz.Rect(50, 60, 150, 200))
    assert pending.rect.width / pending.rect.height == pytest.approx(info.aspect, abs=0.01)

    pending.keep_ratio = False
    pending.resize_to(fitz.Rect(10, 10, 300, 40))
    assert pending.rect.height == pytest.approx(30)

    pending.rect = fitz.Rect(380, 480, 580, 580)
    pending.clamped(fitz.Rect(0, 0, 400, 500))
    assert pending.rect.x1 <= 400.01
    assert pending.rect.y1 <= 500.01


def test_pending_image_commit(png_file):
    """Committing writes the placement into the page."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    info = image_ops.read_image_info(png_file)
    pending = PendingImage(path=png_file, rect=fitz.Rect(40, 40, 240, 140), info=info)

    xref = pending.commit(page)
    assert xref > 0
    assert len(image_ops.list_images(page)) == 1
    doc.close()


def test_image_pixmap_handles_missing_xref():
    """Asking for a non-existing image raises a descriptive error."""
    doc = fitz.open()
    doc.new_page()
    with pytest.raises(ImageOperationError):
        image_ops.image_pixmap(doc, 9999)
    doc.close()
