"""
Image placement operations: insert, move, resize, replace and delete.

Images are handled in two stages so the GUI can offer a natural editing feel:

* A :class:`PendingImage` is a placement the user is still dragging around.  It
  only carries geometry, so moving and resizing costs nothing.
* Once committed, the picture becomes a normal PDF image XObject.  Existing images
  (committed ones as well as images that were already in the file) can still be
  moved, resized, replaced or deleted through :class:`ImageRef` handles.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pymupdf as fitz

from pdf_editor.core.utils import ImageOperationError

# Raster formats accepted by the editor.
SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".webp",
    ".pnm",
    ".pgm",
    ".ppm",
}

# Smallest placement the editor allows, in PDF points.
MIN_IMAGE_SIZE = 8.0

PathLike = Union[str, Path]


@dataclass(frozen=True)
class ImageInfo:
    """Pixel dimensions and format of an image file."""

    width: int
    height: int
    format: str

    @property
    def aspect(self) -> float:
        """Width divided by height (falls back to 1.0 for degenerate images)."""
        return (self.width / self.height) if self.height else 1.0


@dataclass
class ImageRef:
    """A picture that is already placed on a page."""

    xref: int
    bbox: fitz.Rect
    width: int = 0
    height: int = 0
    number: int = 0

    @property
    def aspect(self) -> float:
        """Aspect ratio of the source image."""
        return (self.width / self.height) if self.height else 1.0


@dataclass
class PendingImage:
    """
    An image the user is positioning but which is not written to the PDF yet.

    :param path: Source file on disk.
    :param rect: Current placement rectangle in unrotated PDF coordinates.
    :param info: Cached pixel dimensions of the source file.
    :param keep_ratio: Whether resizing preserves the original aspect ratio.
    :param rotate: Rotation applied when the image is committed (0/90/180/270).
    """

    path: Path
    rect: fitz.Rect
    info: ImageInfo
    keep_ratio: bool = True
    rotate: int = 0

    def moved_to(self, x: float, y: float) -> None:
        """Moves the placement so that its top-left corner sits at ``(x, y)``."""
        width, height = self.rect.width, self.rect.height
        self.rect = fitz.Rect(x, y, x + width, y + height)

    def resize_to(self, rect: fitz.Rect, *, anchor: Optional[Tuple[float, float]] = None) -> None:
        """
        Resizes the placement, optionally locking the aspect ratio.

        :param rect: Requested rectangle.
        :param anchor: Corner kept fixed while the ratio is enforced; defaults to
            the top-left corner of ``rect``.
        """
        new_rect = fitz.Rect(rect)
        new_rect.normalize()
        if new_rect.width < MIN_IMAGE_SIZE:
            new_rect.x1 = new_rect.x0 + MIN_IMAGE_SIZE
        if new_rect.height < MIN_IMAGE_SIZE:
            new_rect.y1 = new_rect.y0 + MIN_IMAGE_SIZE

        if self.keep_ratio:
            anchor_x, anchor_y = anchor if anchor else (new_rect.x0, new_rect.y0)
            width = new_rect.width
            height = width / self.info.aspect if self.info.aspect else new_rect.height
            if height > new_rect.height:
                height = new_rect.height
                width = height * self.info.aspect
            left = new_rect.x0 if abs(anchor_x - new_rect.x0) < 1e-6 else new_rect.x1 - width
            top = new_rect.y0 if abs(anchor_y - new_rect.y0) < 1e-6 else new_rect.y1 - height
            new_rect = fitz.Rect(left, top, left + width, top + height)
        self.rect = new_rect

    def clamped(self, page_rect: fitz.Rect) -> None:
        """Keeps the placement inside the page, shifting it if necessary."""
        rect = fitz.Rect(self.rect)
        if rect.width > page_rect.width:
            rect.x1 = rect.x0 + page_rect.width
        if rect.height > page_rect.height:
            rect.y1 = rect.y0 + page_rect.height
        dx = min(0.0, page_rect.x1 - rect.x1) - min(0.0, rect.x0 - page_rect.x0)
        dy = min(0.0, page_rect.y1 - rect.y1) - min(0.0, rect.y0 - page_rect.y0)
        self.rect = rect + (dx, dy, dx, dy)

    def commit(self, page: fitz.Page, *, overlay: bool = True) -> int:
        """
        Writes the image onto the page.

        :param page: Target page.
        :param overlay: Draw above (True) or below (False) existing content.
        :return: The xref of the created image object.
        """
        return insert_image(
            page,
            self.rect,
            self.path,
            keep_ratio=self.keep_ratio,
            rotate=self.rotate,
            overlay=overlay,
        )


def read_image_info(path: PathLike) -> ImageInfo:
    """
    Reads the pixel size of an image file and validates that it can be decoded.

    :param path: Image file on disk.
    :return: An :class:`ImageInfo` instance.
    :raises ImageOperationError: For missing files, unsupported or broken images.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise ImageOperationError(f"Image file not found: '{file_path}'")
    if file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
        raise ImageOperationError(
            f"Unsupported image type '{file_path.suffix or 'unknown'}'. Supported: {supported}"
        )

    # Pillow gives the friendliest diagnostics; PyMuPDF is the fallback decoder.
    try:
        from PIL import Image  # Imported lazily so the core stays importable without Pillow.

        with Image.open(file_path) as image:
            image.verify()
        with Image.open(file_path) as image:
            return ImageInfo(width=image.width, height=image.height, format=image.format or "")
    except ImportError:
        pass
    except Exception as error:
        raise ImageOperationError(f"'{file_path.name}' is not a readable image: {error}") from error

    try:
        pixmap = fitz.Pixmap(str(file_path))
        return ImageInfo(width=pixmap.width, height=pixmap.height, format=file_path.suffix.lstrip("."))
    except Exception as error:
        raise ImageOperationError(f"'{file_path.name}' is not a readable image: {error}") from error


def fit_rect(bounds: fitz.Rect, aspect: float) -> fitz.Rect:
    """
    Returns the largest rectangle with ``aspect`` that fits inside ``bounds``.

    :param bounds: Rectangle the image must fit into.
    :param aspect: Desired width/height ratio.
    :return: A centred rectangle with the requested aspect ratio.
    """
    box = fitz.Rect(bounds)
    box.normalize()
    if aspect <= 0 or box.is_empty:
        return box

    width = box.width
    height = width / aspect
    if height > box.height:
        height = box.height
        width = height * aspect
    left = box.x0 + (box.width - width) / 2.0
    top = box.y0 + (box.height - height) / 2.0
    return fitz.Rect(left, top, left + width, top + height)


def default_placement(page: fitz.Page, info: ImageInfo, *, fraction: float = 0.4) -> fitz.Rect:
    """
    Computes a sensible first placement for a newly added image.

    :param page: Page the image will be added to.
    :param info: Pixel dimensions of the image.
    :param fraction: Share of the page width the image should occupy.
    :return: A centred rectangle preserving the image aspect ratio.
    """
    page_rect = page.rect
    width = page_rect.width * fraction
    height = width / info.aspect if info.aspect else width
    if height > page_rect.height * 0.6:
        height = page_rect.height * 0.6
        width = height * info.aspect
    left = page_rect.x0 + (page_rect.width - width) / 2.0
    top = page_rect.y0 + (page_rect.height - height) / 2.0
    return fitz.Rect(left, top, left + width, top + height)


def insert_image(
    page: fitz.Page,
    rect: fitz.Rect,
    source: Union[PathLike, bytes],
    *,
    keep_ratio: bool = True,
    rotate: int = 0,
    overlay: bool = True,
) -> int:
    """
    Places an image on a page.

    :param page: Target page.
    :param rect: Placement rectangle in unrotated PDF coordinates.
    :param source: Image file path or raw image bytes.
    :param keep_ratio: Preserve the aspect ratio inside ``rect``.
    :param rotate: Rotation in degrees; must be a multiple of 90.
    :param overlay: Draw above (True) or below (False) existing page content.
    :return: The xref of the inserted image.
    :raises ImageOperationError: When the image cannot be placed.
    """
    box = fitz.Rect(rect)
    box.normalize()
    if box.width < MIN_IMAGE_SIZE or box.height < MIN_IMAGE_SIZE:
        raise ImageOperationError("The image placement area is too small.")
    if rotate % 90 != 0:
        raise ImageOperationError(f"Image rotation must be a multiple of 90 degrees (got {rotate}).")

    try:
        if isinstance(source, (bytes, bytearray)):
            return page.insert_image(
                box,
                stream=bytes(source),
                keep_proportion=keep_ratio,
                rotate=rotate % 360,
                overlay=overlay,
            )
        file_path = Path(source)
        read_image_info(file_path)  # Validates before touching the document.
        return page.insert_image(
            box,
            filename=str(file_path),
            keep_proportion=keep_ratio,
            rotate=rotate % 360,
            overlay=overlay,
        )
    except ImageOperationError:
        raise
    except Exception as error:
        raise ImageOperationError(f"Could not place the image on the page: {error}") from error


def list_images(page: fitz.Page) -> List[ImageRef]:
    """
    Lists every image placed on a page, in drawing order.

    :param page: Page to inspect.
    :return: List of :class:`ImageRef` handles (last entry is the topmost image).
    """
    refs: List[ImageRef] = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return refs

    for info in infos:
        xref = int(info.get("xref", 0))
        if xref <= 0:
            continue  # Inline image: it has no xref and cannot be manipulated.
        if int(info.get("width", 0)) <= 1 and int(info.get("height", 0)) <= 1:
            # Deleting an image leaves an invisible 1x1 placeholder behind; it is
            # not something the user can see or should be able to select.
            continue
        refs.append(
            ImageRef(
                xref=xref,
                bbox=fitz.Rect(info["bbox"]),
                width=int(info.get("width", 0)),
                height=int(info.get("height", 0)),
                number=int(info.get("number", 0)),
            )
        )
    return refs


def find_image_at(page: fitz.Page, point: fitz.Point) -> Optional[ImageRef]:
    """
    Returns the topmost image under a point.

    :param page: Page to inspect.
    :param point: Point in unrotated PDF coordinates.
    :return: An :class:`ImageRef`, or ``None`` when the point hits no image.
    """
    target = fitz.Point(point)
    for ref in reversed(list_images(page)):
        if ref.bbox.contains(target):
            return ref
    return None


def image_pixmap(document: fitz.Document, xref: int) -> fitz.Pixmap:
    """
    Builds a pixmap for an embedded image, restoring transparency if present.

    :param document: Document owning the image.
    :param xref: Cross-reference number of the image object.
    :return: A ``pymupdf.Pixmap`` ready to be re-inserted.
    :raises ImageOperationError: When the image cannot be decoded.
    """
    try:
        raw = document.extract_image(xref)
        pixmap = fitz.Pixmap(document, xref)
        smask = raw.get("smask", 0)
        if smask:
            pixmap = fitz.Pixmap(pixmap, fitz.Pixmap(document, smask))
        if pixmap.colorspace and pixmap.colorspace.n == 4:  # CMYK cannot be inserted directly.
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        return pixmap
    except Exception as error:
        raise ImageOperationError(f"Could not read the embedded image (xref {xref}): {error}") from error


def delete_image(page: fitz.Page, ref: ImageRef) -> None:
    """
    Removes an image from a page.

    Every placement of the same image object on this page disappears, which is the
    behaviour users expect when an illustration is used once.

    :param page: Page being edited.
    :param ref: Handle of the image to delete.
    :raises ImageOperationError: When the image cannot be removed.
    """
    try:
        page.delete_image(ref.xref)
    except Exception as error:
        raise ImageOperationError(f"Could not delete the image: {error}") from error


def move_image(page: fitz.Page, ref: ImageRef, new_rect: fitz.Rect, *, keep_ratio: bool = True) -> ImageRef:
    """
    Moves and/or resizes an image that is already on the page.

    The picture is re-inserted at the new rectangle and the old placement removed,
    which is the only reliable way to relocate an existing PDF image.

    :param page: Page being edited.
    :param ref: Handle of the image to move.
    :param new_rect: Target rectangle in unrotated PDF coordinates.
    :param keep_ratio: Preserve the aspect ratio inside ``new_rect``.
    :return: A fresh :class:`ImageRef` pointing at the new placement.
    :raises ImageOperationError: When the image cannot be relocated.
    """
    box = fitz.Rect(new_rect)
    box.normalize()
    if box.width < MIN_IMAGE_SIZE or box.height < MIN_IMAGE_SIZE:
        raise ImageOperationError("The target area for the image is too small.")

    document = page.parent
    pixmap = image_pixmap(document, ref.xref)
    try:
        page.delete_image(ref.xref)
        new_xref = page.insert_image(box, pixmap=pixmap, keep_proportion=keep_ratio, overlay=True)
    except Exception as error:
        raise ImageOperationError(f"Could not move the image: {error}") from error

    for candidate in list_images(page):
        if candidate.xref == new_xref:
            return candidate
    return ImageRef(xref=new_xref, bbox=box, width=pixmap.width, height=pixmap.height)


def replace_image(page: fitz.Page, ref: ImageRef, source: PathLike) -> None:
    """
    Swaps the pixels of an existing image while keeping its position and size.

    :param page: Page being edited.
    :param ref: Handle of the image to replace.
    :param source: New image file.
    :raises ImageOperationError: When the replacement cannot be applied.
    """
    read_image_info(source)
    try:
        page.replace_image(ref.xref, filename=str(Path(source)))
    except Exception as error:
        raise ImageOperationError(f"Could not replace the image: {error}") from error


def extract_image_to_file(document: fitz.Document, xref: int, destination: PathLike) -> Path:
    """
    Saves an embedded image to disk in its original encoding when possible.

    :param document: Document owning the image.
    :param xref: Cross-reference number of the image object.
    :param destination: Target file; the correct suffix is appended if missing.
    :return: Path of the written file.
    :raises ImageOperationError: When the image cannot be extracted.
    """
    try:
        raw = document.extract_image(xref)
    except Exception as error:
        raise ImageOperationError(f"Could not extract the image: {error}") from error

    target = Path(destination)
    extension = f".{raw.get('ext', 'png')}"
    if target.suffix.lower() != extension:
        target = target.with_suffix(extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw["image"])
    return target


__all__ = [
    "ImageInfo",
    "ImageRef",
    "MIN_IMAGE_SIZE",
    "PendingImage",
    "SUPPORTED_IMAGE_EXTENSIONS",
    "default_placement",
    "delete_image",
    "extract_image_to_file",
    "find_image_at",
    "fit_rect",
    "image_pixmap",
    "insert_image",
    "list_images",
    "move_image",
    "read_image_info",
    "replace_image",
]
