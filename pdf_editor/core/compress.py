"""
PDF compression.

Three levels are offered.  All of them rebuild the file structure losslessly
(unused objects removed, duplicated objects merged, streams deflated, cross
reference streams packed).  What differs is how hard the images are squeezed:

======  ==============================================================
Level   Effect on images
======  ==============================================================
low     Recompressed at quality 78, resolution untouched.
medium  Images above 200 dpi are resampled to 100 dpi, quality 70.
high    Images above 150 dpi are resampled to 72 dpi, quality 45.
======  ==============================================================

Text and vector graphics are never rasterised, so they stay perfectly sharp at
any level; only bitmap images lose detail.  Fonts stay embedded, metadata is
preserved, and an image is only rewritten when the new version is actually
smaller, so compressing never degrades a file for nothing.
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Union

import pymupdf as fitz

from pdf_editor.core.utils import PDFEditorError

PathLike = Union[str, Path]
Source = Union[fitz.Document, bytes, PathLike]
ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class CompressionProfile:
    """Settings behind one compression level."""

    key: str
    label: str
    description: str
    dpi_threshold: int
    dpi_target: int
    jpeg_quality: int
    subset_fonts: bool
    effort: int

    @property
    def resamples(self) -> bool:
        """True when this level lowers the resolution of large images."""
        return self.dpi_target > 0 and self.dpi_threshold > self.dpi_target


# The three levels offered in the user interface and on the command line.
PROFILES: Dict[str, CompressionProfile] = {
    "low": CompressionProfile(
        key="low",
        label="Low - safest",
        description=(
            "Rebuilds the file and recompresses images without lowering their "
            "resolution. Stays fit for printing."
        ),
        dpi_threshold=0,
        dpi_target=0,
        jpeg_quality=78,
        subset_fonts=False,
        effort=0,
    ),
    "medium": CompressionProfile(
        key="medium",
        label="Medium - balanced",
        description=(
            "Resamples images above 200 dpi and subsets fonts. "
            "Good for sharing and e-mail."
        ),
        dpi_threshold=200,
        dpi_target=100,
        jpeg_quality=70,
        subset_fonts=True,
        effort=0,
    ),
    "high": CompressionProfile(
        key="high",
        label="High - smallest file",
        description=(
            "Resamples images down to screen resolution. "
            "Text stays sharp, photos become softer."
        ),
        dpi_threshold=150,
        dpi_target=72,
        jpeg_quality=45,
        subset_fonts=True,
        effort=100,
    ),
}

LEVELS = tuple(PROFILES)


@dataclass
class CompressionReport:
    """Result of a compression run."""

    destination: Path
    level: str
    original_bytes: int
    compressed_bytes: int
    image_bytes_before: int = 0
    image_bytes_after: int = 0
    images: int = 0
    duration: float = 0.0
    warnings: List[str] = field(default_factory=list)

    @property
    def saved_bytes(self) -> int:
        """Bytes saved (never negative)."""
        return max(0, self.original_bytes - self.compressed_bytes)

    @property
    def ratio(self) -> float:
        """Share of the original size that was removed, from 0.0 to 1.0."""
        if self.original_bytes <= 0:
            return 0.0
        return max(0.0, 1.0 - self.compressed_bytes / self.original_bytes)

    @property
    def summary(self) -> str:
        """Short human readable description of the result."""
        if self.saved_bytes <= 0:
            return (
                f"'{self.destination.name}' was already compact "
                f"({format_size(self.original_bytes)}); nothing was gained."
            )
        return (
            f"{format_size(self.original_bytes)} to {format_size(self.compressed_bytes)}, "
            f"{self.ratio * 100:.0f}% smaller"
        )


def format_size(size: int) -> str:
    """
    Formats a byte count for display.

    :param size: Number of bytes.
    :return: A short string such as ``"1.4 MB"``.
    """
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            precision = 0 if unit == "B" else 1
            return f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _document_bytes(source: Source) -> bytes:
    """
    Normalises any accepted source into PDF bytes.

    :param source: Open document, raw PDF bytes, or a path.
    :return: The PDF data.
    :raises PDFEditorError: When the source cannot be read.
    """
    if isinstance(source, fitz.Document):
        return source.tobytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    path = Path(source)
    if not path.exists():
        raise PDFEditorError(f"PDF file not found: '{path}'")
    try:
        return path.read_bytes()
    except OSError as error:
        raise PDFEditorError(f"Cannot read '{path.name}': {error}") from error


def _image_statistics(document: fitz.Document) -> tuple:
    """
    Measures how much of a document is taken up by bitmap images.

    :param document: Document to inspect.
    :return: ``(number of distinct images, total stream bytes)``.
    """
    seen: Set[int] = set()
    total = 0
    for page in document:
        try:
            entries = page.get_images(full=True)
        except Exception:
            continue
        for entry in entries:
            xref = int(entry[0])
            if xref in seen:
                continue
            seen.add(xref)
            try:
                total += len(document.xref_stream_raw(xref))
            except Exception:
                continue
    return len(seen), total


def compress_pdf(
    source: Source,
    destination: PathLike,
    *,
    level: str = "medium",
    downsample_images: bool = True,
    keep_larger: bool = False,
    progress: Optional[ProgressCallback] = None,
) -> CompressionReport:
    """
    Writes a smaller copy of a PDF.

    :param source: Open document, PDF bytes, or a path. Passing the open document
        compresses the current state, including edits that were not saved yet.
    :param destination: Target file; the ``.pdf`` suffix is added when missing.
    :param level: ``"low"``, ``"medium"`` or ``"high"``; see :data:`PROFILES`.
    :param downsample_images: Set to False to keep every image untouched and rely
        on structural compression alone (fully lossless).
    :param keep_larger: Write the compressed result even when it is bigger than
        the original. By default the original is kept in that case.
    :param progress: Called as ``progress(fraction, message)`` during the run.
    :return: A :class:`CompressionReport`.
    :raises PDFEditorError: For an unknown level or when writing fails.
    """
    started = time.time()
    profile = PROFILES.get(str(level).lower())
    if profile is None:
        raise PDFEditorError(
            f"Unknown compression level '{level}'. Choose one of: {', '.join(LEVELS)}."
        )

    target = Path(destination)
    if target.suffix.lower() != ".pdf":
        target = target.with_suffix(".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(0.05, "Reading the document...")
    data = _document_bytes(source)
    original_bytes = len(data)

    warnings: List[str] = []
    document = fitz.open(stream=data, filetype="pdf")

    try:
        if progress:
            progress(0.15, "Analysing images...")
        images, image_bytes_before = _image_statistics(document)

        if downsample_images and images:
            if progress:
                progress(0.35, f"Recompressing {images} image(s)...")
            options = dict(
                quality=profile.jpeg_quality,
                lossy=True,
                lossless=True,
                color=True,
                gray=True,
                bitonal=False,  # Scanned black and white pages stay readable.
            )
            if profile.resamples:
                options["dpi_threshold"] = profile.dpi_threshold
                options["dpi_target"] = profile.dpi_target
            try:
                document.rewrite_images(**options)
            except Exception as error:
                warnings.append(f"Images were left untouched: {error}")

        if profile.subset_fonts:
            if progress:
                progress(0.6, "Subsetting fonts...")
            try:
                document.subset_fonts()
            except Exception as error:
                warnings.append(f"Fonts could not be subset: {error}")

        if progress:
            progress(0.8, "Writing the compressed file...")
        _, image_bytes_after = _image_statistics(document)

        handle, temporary = tempfile.mkstemp(suffix=".pdf", dir=str(target.parent))
        os.close(handle)
        try:
            document.save(
                temporary,
                garbage=4,
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                clean=True,
                use_objstms=1,
                compression_effort=profile.effort,
            )
            compressed_bytes = os.path.getsize(temporary)

            if compressed_bytes >= original_bytes and not keep_larger:
                # Already optimised: keep the original rather than making it bigger.
                warnings.append(
                    "The document was already compressed, so the original was kept unchanged."
                )
                with open(temporary, "wb") as handle_out:
                    handle_out.write(data)
                compressed_bytes = original_bytes
                image_bytes_after = image_bytes_before

            os.replace(temporary, target)
        except Exception as error:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
            raise PDFEditorError(f"Could not write '{target.name}': {error}") from error
    finally:
        document.close()

    if progress:
        progress(1.0, "Finished")

    return CompressionReport(
        destination=target,
        level=profile.key,
        original_bytes=original_bytes,
        compressed_bytes=compressed_bytes,
        image_bytes_before=image_bytes_before,
        image_bytes_after=image_bytes_after,
        images=images,
        duration=time.time() - started,
        warnings=warnings,
    )


def describe_levels() -> List[str]:
    """
    Returns one description line per compression level, for help texts.

    :return: Lines such as ``"medium - Resamples images above 200 dpi..."``.
    """
    return [f"{profile.key} - {profile.description}" for profile in PROFILES.values()]


__all__ = [
    "LEVELS",
    "PROFILES",
    "CompressionProfile",
    "CompressionReport",
    "compress_pdf",
    "describe_levels",
    "format_size",
]
