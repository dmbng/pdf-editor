"""
Font analysis and resolution helpers used by the text editing engine.

When a run of text is replaced inside an existing PDF, the replacement has to be
drawn with a font that is as close as possible to the original one, otherwise the
edit is immediately visible.  This module turns the raw span information returned
by PyMuPDF (``page.get_text("dict")``) into a :class:`SpanStyle` and resolves that
style to a concrete, drawable ``pymupdf.Font`` object.

Resolution order:

1. Re-use the font that is actually embedded in the document, provided it can be
   extracted and contains a glyph for every character that must be drawn.
2. Fall back to the matching PDF base-14 font (Helvetica / Times / Courier in the
   regular, bold, italic and bold-italic variants).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import pymupdf as fitz

# Subset fonts are named like "ABCDEF+Arial-BoldMT"; the prefix carries no meaning.
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")

# PyMuPDF span flag bits.
FLAG_SUPERSCRIPT = 1 << 0
FLAG_ITALIC = 1 << 1
FLAG_SERIF = 1 << 2
FLAG_MONOSPACE = 1 << 3
FLAG_BOLD = 1 << 4

# Base-14 aliases understood by PyMuPDF, keyed by family.
# Tuple order is (regular, bold, italic, bold-italic).
_BASE14 = {
    "helv": ("helv", "hebo", "heit", "hebi"),
    "tiro": ("tiro", "tibo", "tiit", "tibi"),
    "cour": ("cour", "cobo", "coit", "cobi"),
}

_BOLD_WORDS = ("bold", "black", "heavy", "semibold", "demibold", "extrabold")
_ITALIC_WORDS = ("italic", "oblique")
_SERIF_WORDS = ("times", "serif", "roman", "georgia", "garamond", "minion", "cambria")
_MONO_WORDS = ("courier", "mono", "consol", "menlo")

# Aliases of the PDF standard fonts: text drawn with these needs no embedding.
BASE14_ALIASES = frozenset(
    alias for variants in _BASE14.values() for alias in variants
) | {"symb", "zadb"}

# Cache of resolved pymupdf.Font objects; building them is comparatively expensive.
_FONT_CACHE: Dict[str, fitz.Font] = {}


# Suffixes foundries add to font names; they carry no styling information.
_NOISE_SUFFIXES = ("mt", "ps", "std", "pro", "regular", "roman", "book", "normal")


def strip_subset_prefix(font_name: str) -> str:
    """Removes the six-letter subset prefix from an embedded font name."""
    return _SUBSET_PREFIX.sub("", font_name or "")


def font_key(font_name: str) -> str:
    """
    Reduces a font name to a comparable key.

    The same face is often spelled differently in the text spans and in the page
    resources ("ArialMT" versus "Arial Regular"), so both are stripped down to
    their bare family plus style before they are compared.

    :param font_name: Raw font name.
    :return: Lower case key without punctuation or foundry suffixes.
    """
    base = re.sub(r"[^a-z0-9]", "", strip_subset_prefix(font_name).lower())
    changed = True
    while changed:
        changed = False
        for suffix in _NOISE_SUFFIXES:
            if base.endswith(suffix) and len(base) > len(suffix):
                base = base[: -len(suffix)]
                changed = True
    return base


def int_to_rgb(color: int) -> Tuple[float, float, float]:
    """Converts a PyMuPDF sRGB integer (0xRRGGBB) into a 0.0-1.0 RGB tuple."""
    try:
        value = int(color)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    return (
        ((value >> 16) & 0xFF) / 255.0,
        ((value >> 8) & 0xFF) / 255.0,
        (value & 0xFF) / 255.0,
    )


def rgb_to_int(color: Tuple[float, float, float]) -> int:
    """Converts a 0.0-1.0 RGB tuple back into a packed sRGB integer."""
    r, g, b = (max(0.0, min(1.0, float(component))) for component in color)
    return (int(round(r * 255)) << 16) | (int(round(g * 255)) << 8) | int(round(b * 255))


@dataclass(frozen=True)
class SpanStyle:
    """Visual attributes of a run of text extracted from a PDF page."""

    font: str = "Helvetica"
    size: float = 11.0
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    flags: int = 0
    ascender: float = 0.8
    descender: float = -0.2
    alpha: float = 1.0

    @property
    def bold(self) -> bool:
        """True when the span is bold (per flags or per font name)."""
        if self.flags & FLAG_BOLD:
            return True
        return any(word in self.font.lower() for word in _BOLD_WORDS)

    @property
    def italic(self) -> bool:
        """True when the span is italic/oblique."""
        if self.flags & FLAG_ITALIC:
            return True
        return any(word in self.font.lower() for word in _ITALIC_WORDS)

    @property
    def serif(self) -> bool:
        """True when the span uses a serif typeface."""
        if self.flags & FLAG_MONOSPACE:
            return False
        if self.flags & FLAG_SERIF:
            return True
        return any(word in self.font.lower() for word in _SERIF_WORDS)

    @property
    def mono(self) -> bool:
        """True when the span uses a monospaced typeface."""
        if self.flags & FLAG_MONOSPACE:
            return True
        return any(word in self.font.lower() for word in _MONO_WORDS)

    @property
    def line_height(self) -> float:
        """Natural line height of the style, in points."""
        extent = float(self.ascender) - float(self.descender)
        if extent <= 0:
            extent = 1.2
        return self.size * extent

    @property
    def base14_name(self) -> str:
        """Best matching base-14 font alias for this style."""
        family = "cour" if self.mono else ("tiro" if self.serif else "helv")
        regular, bold, italic, bold_italic = _BASE14[family]
        if self.bold and self.italic:
            return bold_italic
        if self.bold:
            return bold
        if self.italic:
            return italic
        return regular

    @classmethod
    def from_span(cls, span: dict) -> "SpanStyle":
        """Builds a style from a PyMuPDF ``dict``/``rawdict`` span."""
        raw_alpha = span.get("alpha", 255)
        return cls(
            font=strip_subset_prefix(span.get("font", "Helvetica")),
            size=float(span.get("size", 11.0)) or 11.0,
            color=int_to_rgb(span.get("color", 0)),
            flags=int(span.get("flags", 0)),
            ascender=float(span.get("ascender", 0.8)),
            descender=float(span.get("descender", -0.2)),
            alpha=(float(raw_alpha) / 255.0) if raw_alpha > 1 else float(raw_alpha),
        )

    def scaled(self, factor: float) -> "SpanStyle":
        """Returns a copy of the style with the font size multiplied by ``factor``."""
        return SpanStyle(
            font=self.font,
            size=max(1.0, self.size * factor),
            color=self.color,
            flags=self.flags,
            ascender=self.ascender,
            descender=self.descender,
            alpha=self.alpha,
        )

    def with_overrides(
        self,
        *,
        font: Optional[str] = None,
        size: Optional[float] = None,
        color: Optional[Tuple[float, float, float]] = None,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
    ) -> "SpanStyle":
        """Returns a copy of the style with individual attributes overridden."""
        flags = self.flags
        if bold is not None:
            flags = (flags | FLAG_BOLD) if bold else (flags & ~FLAG_BOLD)
        if italic is not None:
            flags = (flags | FLAG_ITALIC) if italic else (flags & ~FLAG_ITALIC)
        return SpanStyle(
            font=font if font is not None else self.font,
            size=float(size) if size is not None else self.size,
            color=tuple(color) if color is not None else self.color,
            flags=flags,
            ascender=self.ascender,
            descender=self.descender,
            alpha=self.alpha,
        )


@dataclass
class ResolvedFont:
    """A drawable font together with the information needed to report it back."""

    font: fitz.Font
    name: str
    embedded: bool = False

    @property
    def standard_name(self) -> Optional[str]:
        """
        Alias to use for a PDF standard font, or ``None`` when embedding is needed.

        Writing standard fonts by name keeps the output small and leaves the
        document using the very same font resources it used before the edit.
        """
        return self.name if (not self.embedded and self.name in BASE14_ALIASES) else None

    def text_length(self, text: str, size: float) -> float:
        """Width of ``text`` in points when drawn at ``size``."""
        if not text:
            return 0.0
        return self.font.text_length(text, fontsize=size)

    def covers(self, text: str) -> bool:
        """True when the font provides a glyph for every character in ``text``."""
        return all(self.font.has_glyph(ord(char)) for char in text if char not in "\n\r\t")


def base14_font(alias: str) -> fitz.Font:
    """Returns a cached base-14 ``pymupdf.Font`` for the given alias."""
    font = _FONT_CACHE.get(alias)
    if font is None:
        font = fitz.Font(alias)
        _FONT_CACHE[alias] = font
    return font


@dataclass
class FontResolver:
    """
    Resolves :class:`SpanStyle` objects to drawable fonts for a single document.

    Extracted embedded font buffers are cached per font xref so that re-laying out
    a paragraph does not extract the same font file repeatedly.
    """

    document: Optional[fitz.Document] = None
    use_embedded: bool = True
    _embedded_cache: Dict[int, Optional[fitz.Font]] = field(default_factory=dict, repr=False)

    def _embedded_font_for(self, page: fitz.Page, style: SpanStyle) -> Optional[fitz.Font]:
        """Finds and loads the embedded font of ``style`` from the page resources."""
        if not self.use_embedded or page is None:
            return None
        doc = self.document or page.parent
        if doc is None:
            return None

        wanted = font_key(style.font)
        try:
            page_fonts = page.get_fonts(full=True)
        except Exception:
            return None

        for entry in page_fonts:
            xref, ext, basefont = entry[0], entry[1], entry[3]
            if font_key(basefont) != wanted:
                continue
            if xref in self._embedded_cache:
                return self._embedded_cache[xref]

            font: Optional[fitz.Font] = None
            if ext in ("ttf", "otf", "cff", "pfa"):
                try:
                    _name, _ext, _subtype, buffer = doc.extract_font(xref)
                    if buffer:
                        font = fitz.Font(fontbuffer=buffer)
                except Exception:
                    font = None  # Unusable/protected font: silently fall back.
            self._embedded_cache[xref] = font
            return font
        return None

    def resolve(self, style: SpanStyle, text: str = "", page: Optional[fitz.Page] = None) -> ResolvedFont:
        """
        Resolves ``style`` to a font able to render ``text``.

        :param style: Visual style of the original text run.
        :param text: Characters that must be drawable by the returned font.
        :param page: Page whose resources are searched for the embedded original.
        :return: A :class:`ResolvedFont` (never ``None``).
        """
        if page is not None:
            embedded = self._embedded_font_for(page, style)
            if embedded is not None:
                resolved = ResolvedFont(embedded, strip_subset_prefix(style.font), embedded=True)
                if not text or resolved.covers(text):
                    return resolved

        fallback = ResolvedFont(base14_font(style.base14_name), style.base14_name, embedded=False)
        if text and not fallback.covers(text):
            # Last resort: a broad Unicode face shipped with PyMuPDF.
            for candidate in ("figo", "notos", "china-ss"):
                try:
                    wide = ResolvedFont(base14_font(candidate), candidate, embedded=False)
                except Exception:
                    continue
                if wide.covers(text):
                    return wide
        return fallback
