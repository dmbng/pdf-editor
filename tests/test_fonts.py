"""
Unit tests for font analysis and resolution.
"""

from pathlib import Path

import pymupdf as fitz
import pytest

from pdf_editor.core import fonts
from pdf_editor.core.fonts import FontResolver, SpanStyle

ARIAL = Path("C:/Windows/Fonts/arial.ttf")


def test_strip_subset_prefix():
    """Subset prefixes are removed, other names are untouched."""
    assert fonts.strip_subset_prefix("ABCDEF+Arial-BoldMT") == "Arial-BoldMT"
    assert fonts.strip_subset_prefix("Helvetica") == "Helvetica"
    assert fonts.strip_subset_prefix("") == ""


def test_colour_conversion_round_trip():
    """Packed sRGB integers convert to floats and back."""
    assert fonts.int_to_rgb(0xFF0000) == (1.0, 0.0, 0.0)
    assert fonts.int_to_rgb(0x000000) == (0.0, 0.0, 0.0)
    assert fonts.rgb_to_int((0.0, 1.0, 0.0)) == 0x00FF00
    assert fonts.int_to_rgb(fonts.rgb_to_int((0.2, 0.4, 0.6))) == pytest.approx(
        (0.2, 0.4, 0.6), abs=0.01
    )
    assert fonts.int_to_rgb("not a number") == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "flags, name, expected",
    [
        (0, "Helvetica", "helv"),
        (fonts.FLAG_BOLD, "Helvetica", "hebo"),
        (fonts.FLAG_ITALIC, "Helvetica", "heit"),
        (fonts.FLAG_BOLD | fonts.FLAG_ITALIC, "Helvetica", "hebi"),
        (fonts.FLAG_SERIF, "Times-Roman", "tiro"),
        (fonts.FLAG_SERIF | fonts.FLAG_BOLD, "Times-Bold", "tibo"),
        (fonts.FLAG_MONOSPACE, "Courier", "cour"),
        (0, "Arial-BoldItalicMT", "hebi"),
        (0, "GaramondPremier", "tiro"),
        (0, "Consolas", "cour"),
    ],
)
def test_base14_mapping(flags, name, expected):
    """Style flags and font names map onto the right base-14 alias."""
    assert SpanStyle(font=name, flags=flags).base14_name == expected


def test_style_from_span():
    """A PyMuPDF span becomes a style with the same attributes."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(40, 60), "Styled", fontsize=13, fontname="tibo", color=(1, 0, 0))
    span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]

    style = SpanStyle.from_span(span)
    assert style.size == pytest.approx(13.0)
    assert style.bold is True
    assert style.serif is True
    assert style.color == pytest.approx((1.0, 0.0, 0.0))
    assert style.line_height > style.size
    doc.close()


def test_style_transformations():
    """Scaling and overriding produce independent copies."""
    style = SpanStyle(font="Helvetica", size=10.0)
    assert style.scaled(0.5).size == pytest.approx(5.0)
    assert style.scaled(0.5).font == "Helvetica"

    bold = style.with_overrides(bold=True, size=14.0, color=(0.0, 0.0, 1.0))
    assert bold.bold is True
    assert bold.size == pytest.approx(14.0)
    assert bold.color == (0.0, 0.0, 1.0)
    assert style.bold is False  # The original is unchanged.
    assert bold.with_overrides(bold=False).bold is False


def test_resolver_falls_back_to_base14():
    """Without a page, resolution uses the standard fonts."""
    resolved = FontResolver().resolve(SpanStyle(font="Unknown Sans", flags=fonts.FLAG_BOLD), "Hello")
    assert resolved.name == "hebo"
    assert resolved.embedded is False
    assert resolved.standard_name == "hebo"
    assert resolved.text_length("Hello", 12) > 0
    assert resolved.covers("Hello") is True


@pytest.mark.skipif(not ARIAL.exists(), reason="Arial is not installed on this machine")
def test_resolver_reuses_embedded_fonts():
    """A font embedded in the document is used for the replacement text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(60, 80), "Embedded sample", fontsize=14,
                     fontfile=str(ARIAL), fontname="ARL")
    data = doc.tobytes()
    doc.close()

    doc = fitz.open(stream=data, filetype="pdf")
    page = doc[0]
    span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
    style = SpanStyle.from_span(span)

    resolved = FontResolver(document=doc).resolve(style, "New text", page)
    assert resolved.embedded is True
    assert resolved.standard_name is None
    assert resolved.covers("New text") is True

    disabled = FontResolver(document=doc, use_embedded=False).resolve(style, "New text", page)
    assert disabled.embedded is False
    doc.close()
