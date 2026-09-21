"""
Interactive text editing engine.

This module implements the "highlight a run of text, delete it, type something
else" workflow on top of PyMuPDF.  A PDF has no notion of editable paragraphs, so
an edit is performed in three steps:

1. **Read** the affected paragraph (``block``) down to character level, keeping the
   style (font, size, colour, bold/italic) of every single character.
2. **Re-layout** the paragraph after splicing the replacement text into the
   character stream, using the real font metrics of each run.  This is what makes
   the surrounding words reflow instead of overlapping or leaving a hole.
3. **Redact and rewrite**: the original text is removed with a redaction that
   leaves images and vector graphics untouched, and the new layout is drawn with
   ``pymupdf.TextWriter`` so the original fonts stay embedded.

Everything in here works on an already opened ``pymupdf.Page`` so the GUI can edit
in memory and only write to disk when the user exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf as fitz

from pdf_editor.core.fonts import FontResolver, ResolvedFont, SpanStyle
from pdf_editor.core.utils import TextEditError

# Alignment constants (mirroring the PyMuPDF/PDF convention).
ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2
ALIGN_JUSTIFY = 3

# Minimum scale factor the engine may apply to make replacement text fit.
MIN_FONT_SCALE = 0.62
# Padding added around redaction rectangles so anti-aliased glyph edges disappear.
REDACT_PADDING = 0.6


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Word:
    """A single word as reported by ``page.get_text("words")``."""

    rect: fitz.Rect
    text: str
    block: int
    line: int
    number: int

    @classmethod
    def from_tuple(cls, item: Sequence) -> "Word":
        """Builds a :class:`Word` from a PyMuPDF word tuple."""
        return cls(
            rect=fitz.Rect(item[:4]),
            text=item[4],
            block=int(item[5]),
            line=int(item[6]),
            number=int(item[7]),
        )


@dataclass
class TextSelection:
    """A contiguous run of words the user has highlighted on one page."""

    page_index: int
    words: List[Word] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing is selected."""
        return not self.words

    @property
    def text(self) -> str:
        """Plain text of the selection, words joined by single spaces."""
        return " ".join(word.text for word in self.words)

    @property
    def block(self) -> Optional[int]:
        """Block (paragraph) number of the first selected word."""
        return self.words[0].block if self.words else None

    @property
    def blocks(self) -> List[int]:
        """All distinct block numbers touched by the selection, in order."""
        seen: List[int] = []
        for word in self.words:
            if word.block not in seen:
                seen.append(word.block)
        return seen

    @property
    def bbox(self) -> fitz.Rect:
        """Bounding box covering every selected word."""
        rect = fitz.Rect()
        for word in self.words:
            rect |= word.rect
        return rect

    @property
    def rects(self) -> List[fitz.Rect]:
        """One rectangle per selected text line (used for highlighting)."""
        per_line: Dict[Tuple[int, int], fitz.Rect] = {}
        for word in self.words:
            key = (word.block, word.line)
            if key in per_line:
                per_line[key] |= word.rect
            else:
                per_line[key] = fitz.Rect(word.rect)
        return [per_line[key] for key in sorted(per_line)]

    def limited_to_block(self, block: int) -> "TextSelection":
        """Returns a copy of the selection restricted to a single block."""
        return TextSelection(self.page_index, [w for w in self.words if w.block == block])


@dataclass(frozen=True)
class CharBox:
    """A single character with its geometry and style."""

    char: str
    bbox: fitz.Rect
    origin: Tuple[float, float]
    line: int
    style: SpanStyle


@dataclass
class LineInfo:
    """Geometry of one rendered text line inside a block."""

    index: int
    bbox: fitz.Rect
    baseline: float
    origin_x: float


@dataclass
class BlockText:
    """A paragraph of a page, decomposed to character level."""

    number: int
    bbox: fitz.Rect
    chars: List[CharBox]
    lines: List[LineInfo]

    @property
    def text(self) -> str:
        """Plain text of the block with newlines between rendered lines."""
        parts: List[str] = []
        previous_line = None
        for char in self.chars:
            if previous_line is not None and char.line != previous_line:
                parts.append("\n")
            parts.append(char.char)
            previous_line = char.line
        return "".join(parts)


@dataclass
class EditResult:
    """Outcome of a text edit, returned so the UI can report what happened."""

    page_index: int
    block_number: int
    old_text: str
    new_text: str
    reflowed: bool
    font_scale: float = 1.0
    expanded: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Short human readable description of the edit."""
        action = "Deleted" if not self.new_text.strip() else "Replaced"
        detail = f"{action} {len(self.old_text)} character(s)"
        if self.font_scale < 0.999:
            detail += f", text scaled to {self.font_scale * 100:.0f}% to fit"
        if self.expanded:
            detail += ", paragraph box extended"
        return detail


@dataclass
class _Token:
    """An atomic layout unit: either a word or a whitespace gap."""

    text: str
    style: SpanStyle
    font: ResolvedFont
    width: float
    is_space: bool


@dataclass
class _Placed:
    """A token positioned on a baseline, ready to be written to the page."""

    text: str
    x: float
    y: float
    style: SpanStyle
    font: ResolvedFont


@dataclass
class _StyledRun:
    """A run of characters sharing one style."""

    text: str
    style: SpanStyle


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------
def page_words(page: fitz.Page) -> List[Word]:
    """
    Returns every word of a page in reading order.

    :param page: Page to analyse.
    :return: List of :class:`Word` objects (empty for image-only pages).
    """
    return [Word.from_tuple(item) for item in page.get_text("words")]


def select_words_in_rect(page: fitz.Page, rect: fitz.Rect, page_index: int = 0) -> TextSelection:
    """
    Selects every word that intersects a rubber-band rectangle.

    :param page: Page being edited.
    :param rect: Selection rectangle in unrotated PDF coordinates.
    :param page_index: Index of the page, stored on the selection.
    :return: A :class:`TextSelection` (possibly empty).
    """
    box = fitz.Rect(rect)
    box.normalize()
    if box.is_empty:
        box = fitz.Rect(box.x0 - 0.5, box.y0 - 0.5, box.x1 + 0.5, box.y1 + 0.5)

    words = [word for word in page_words(page) if box.intersects(word.rect)]
    return TextSelection(page_index, words)


def select_word_at(page: fitz.Page, point: fitz.Point, page_index: int = 0) -> TextSelection:
    """
    Selects the single word under a point (used for double-click selection).

    :param page: Page being edited.
    :param point: Point in unrotated PDF coordinates.
    :param page_index: Index of the page, stored on the selection.
    :return: A selection holding at most one word.
    """
    target = fitz.Point(point)
    for word in page_words(page):
        if word.rect.contains(target):
            return TextSelection(page_index, [word])
    return TextSelection(page_index, [])


def select_line_at(page: fitz.Page, point: fitz.Point, page_index: int = 0) -> TextSelection:
    """
    Selects the whole text line under a point (used for triple-click selection).

    :param page: Page being edited.
    :param point: Point in unrotated PDF coordinates.
    :param page_index: Index of the page, stored on the selection.
    :return: A selection covering the entire line.
    """
    words = page_words(page)
    for word in words:
        if word.rect.contains(fitz.Point(point)):
            return TextSelection(
                page_index,
                [w for w in words if w.block == word.block and w.line == word.line],
            )
    return TextSelection(page_index, [])


def search_page(page: fitz.Page, needle: str, *, case_sensitive: bool = False) -> List[fitz.Rect]:
    """
    Finds all occurrences of ``needle`` on a page.

    :param page: Page to search.
    :param needle: Text to look for.
    :param case_sensitive: When False, matching ignores letter case.
    :return: List of hit rectangles in unrotated PDF coordinates.
    """
    if not needle:
        return []
    hits = list(page.search_for(needle))
    if hits or case_sensitive:
        return hits

    # PyMuPDF's search is already case-insensitive in recent versions; this is a
    # defensive fallback for exotic encodings.
    lowered = needle.lower()
    return [word.rect for word in page_words(page) if lowered in word.text.lower()]


# ---------------------------------------------------------------------------
# Block extraction and style probing
# ---------------------------------------------------------------------------
def _iter_text_blocks(page: fitz.Page) -> Iterable[dict]:
    """Yields the raw text blocks of a page (character level)."""
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type", 1) == 0:
            yield block


def extract_block(page: fitz.Page, block_number: int) -> Optional[BlockText]:
    """
    Decomposes one paragraph of a page into characters and line geometry.

    :param page: Page being edited.
    :param block_number: Block number as reported by ``get_text("words")``.
    :return: A :class:`BlockText`, or ``None`` when the block does not exist.
    """
    for raw in _iter_text_blocks(page):
        if raw.get("number") != block_number:
            continue
        chars: List[CharBox] = []
        lines: List[LineInfo] = []
        for line_index, line in enumerate(raw.get("lines", [])):
            line_bbox = fitz.Rect(line["bbox"])
            baseline = None
            origin_x = line_bbox.x0
            for span in line.get("spans", []):
                style = SpanStyle.from_span(span)
                if baseline is None:
                    baseline = float(span.get("origin", (line_bbox.x0, line_bbox.y1))[1])
                    origin_x = float(span.get("origin", (line_bbox.x0, 0))[0])
                for char in span.get("chars", []):
                    chars.append(
                        CharBox(
                            char=char["c"],
                            bbox=fitz.Rect(char["bbox"]),
                            origin=tuple(char["origin"]),
                            line=line_index,
                            style=style,
                        )
                    )
            lines.append(
                LineInfo(
                    index=line_index,
                    bbox=line_bbox,
                    baseline=baseline if baseline is not None else line_bbox.y1,
                    origin_x=origin_x,
                )
            )
        return BlockText(number=block_number, bbox=fitz.Rect(raw["bbox"]), chars=chars, lines=lines)
    return None


def find_block_at(page: fitz.Page, point: fitz.Point) -> Optional[int]:
    """
    Returns the number of the text block containing a point, if any.

    :param page: Page being inspected.
    :param point: Point in unrotated PDF coordinates.
    :return: Block number or ``None``.
    """
    for raw in _iter_text_blocks(page):
        if fitz.Rect(raw["bbox"]).contains(fitz.Point(point)):
            return raw.get("number")
    return None


def probe_style(page: fitz.Page, selection: TextSelection) -> SpanStyle:
    """
    Determines the dominant style of a selection.

    The style whose characters cover the largest area inside the selection wins,
    which gives a sensible answer even for mixed bold/regular selections.

    :param page: Page being edited.
    :param selection: Highlighted words.
    :return: The dominant :class:`SpanStyle`; a default style when unknown.
    """
    if selection.is_empty:
        return SpanStyle()

    rects = selection.rects
    weights: Dict[SpanStyle, float] = {}
    for raw in _iter_text_blocks(page):
        for line in raw.get("lines", []):
            for span in line.get("spans", []):
                span_rect = fitz.Rect(span["bbox"])
                covered = 0.0
                for rect in rects:
                    overlap = fitz.Rect(span_rect) & rect
                    if not overlap.is_empty:
                        covered += abs(overlap.get_area())
                if covered > 0:
                    style = SpanStyle.from_span(span)
                    weights[style] = weights.get(style, 0.0) + covered
    if not weights:
        return SpanStyle()
    return max(weights.items(), key=lambda item: item[1])[0]


def _selection_char_range(block: BlockText, selection: TextSelection) -> Tuple[int, int]:
    """
    Maps a word selection onto a character index range within a block.

    :param block: Decomposed paragraph.
    :param selection: Highlighted words (already limited to this block).
    :return: ``(first_index, last_index)`` inclusive.
    :raises TextEditError: When the selection cannot be located in the block.
    """
    rects = [fitz.Rect(rect) for rect in selection.rects]
    for rect in rects:
        # Grow slightly: glyph boxes can stick out of the word rectangle.
        rect.x0 -= 0.4
        rect.y0 -= 0.4
        rect.x1 += 0.4
        rect.y1 += 0.4

    hits = [
        index
        for index, char in enumerate(block.chars)
        if any(rect.contains(_center(char.bbox)) for rect in rects)
    ]
    if not hits:
        raise TextEditError("The highlighted text could not be located in the paragraph.")
    return min(hits), max(hits)


def _center(rect: fitz.Rect) -> fitz.Point:
    """Returns the centre point of a rectangle."""
    return fitz.Point((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)


# ---------------------------------------------------------------------------
# Highlight annotations
# ---------------------------------------------------------------------------
def highlight_selection(
    page: fitz.Page,
    selection: TextSelection,
    color: Tuple[float, float, float] = (1.0, 0.92, 0.23),
) -> int:
    """
    Adds a real PDF highlight annotation over the selected text.

    :param page: Page being edited.
    :param selection: Words to highlight.
    :param color: RGB colour of the highlight (0.0-1.0 components).
    :return: Number of annotations created.
    :raises TextEditError: When nothing is selected.
    """
    if selection.is_empty:
        raise TextEditError("Select some text before highlighting.")
    created = 0
    for rect in selection.rects:
        annot = page.add_highlight_annot(rect)
        annot.set_colors(stroke=color)
        annot.update()
        created += 1
    return created


def remove_highlights_at(page: fitz.Page, point: fitz.Point) -> int:
    """
    Deletes highlight annotations located under a point.

    :param page: Page being edited.
    :param point: Point in unrotated PDF coordinates.
    :return: Number of annotations removed.
    """
    removed = 0
    target = fitz.Point(point)
    for annot in list(page.annots(types=(fitz.PDF_ANNOT_HIGHLIGHT,))):
        if annot.rect.contains(target):
            page.delete_annot(annot)
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Layout engine
# ---------------------------------------------------------------------------
def _chars_to_runs(chars: Sequence[CharBox], *, join_lines: bool) -> List[_StyledRun]:
    """
    Converts characters back into styled runs.

    :param chars: Characters in reading order.
    :param join_lines: When True, a line break becomes a space so the paragraph
        can be re-wrapped; when False the break is preserved.
    :return: Consecutive runs sharing one style.
    """
    runs: List[_StyledRun] = []
    previous_line: Optional[int] = None

    def push(text: str, style: SpanStyle) -> None:
        if not text:
            return
        if runs and runs[-1].style == style:
            runs[-1].text += text
        else:
            runs.append(_StyledRun(text, style))

    for char in chars:
        if previous_line is not None and char.line != previous_line:
            if join_lines:
                # Soft-hyphenated word: drop the hyphen instead of adding a space.
                if runs and runs[-1].text.endswith("-"):
                    runs[-1].text = runs[-1].text[:-1]
                elif runs and not runs[-1].text.endswith(" "):
                    push(" ", runs[-1].style)
            else:
                push("\n", char.style)
        push(char.char, char.style)
        previous_line = char.line
    return runs


def _tokenize(runs: Sequence[_StyledRun], resolver: FontResolver, page: fitz.Page) -> List[_Token]:
    """
    Splits styled runs into words and whitespace tokens with measured widths.

    :param runs: Styled runs to lay out.
    :param resolver: Font resolver used to obtain drawable fonts.
    :param page: Page whose embedded fonts may be reused.
    :return: Flat token list; widths are measured at the run's own font size.
    """
    tokens: List[_Token] = []
    for run in runs:
        if not run.text:
            continue
        font = resolver.resolve(run.style, run.text, page)
        buffer = ""
        buffer_is_space: Optional[bool] = None

        def flush() -> None:
            nonlocal buffer, buffer_is_space
            if buffer:
                tokens.append(
                    _Token(
                        text=buffer,
                        style=run.style,
                        font=font,
                        width=font.text_length(buffer, run.style.size),
                        is_space=bool(buffer_is_space),
                    )
                )
            buffer = ""
            buffer_is_space = None

        for char in run.text:
            if char == "\n":
                flush()
                tokens.append(_Token("\n", run.style, font, 0.0, True))
                continue
            is_space = char.isspace()
            if buffer_is_space is None or is_space == buffer_is_space:
                buffer += char
                buffer_is_space = is_space
            else:
                flush()
                buffer = char
                buffer_is_space = is_space
        flush()
    return tokens


def _wrap(tokens: Sequence[_Token], width: float, scale: float) -> List[List[_Token]]:
    """
    Greedy word wrapping.

    :param tokens: Tokens produced by :func:`_tokenize`.
    :param width: Available line width in points.
    :param scale: Font scale factor applied to every token width.
    :return: List of lines, each a list of tokens (trailing spaces included).
    """
    lines: List[List[_Token]] = [[]]
    used = 0.0
    for token in tokens:
        if token.text == "\n":
            lines.append([])
            used = 0.0
            continue
        token_width = token.width * scale
        if token.is_space:
            if not lines[-1]:
                continue  # Do not start a line with whitespace.
            lines[-1].append(token)
            used += token_width
            continue
        if lines[-1] and used + token_width > width + 0.01:
            # Drop the trailing space of the finished line, then start a new one.
            while lines[-1] and lines[-1][-1].is_space:
                lines[-1].pop()
            lines.append([token])
            used = token_width
        else:
            lines[-1].append(token)
            used += token_width
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _detect_alignment(block: BlockText) -> int:
    """
    Guesses the paragraph alignment from the geometry of its lines.

    :param block: Decomposed paragraph.
    :return: One of the ``ALIGN_*`` constants.
    """
    lines = [line for line in block.lines if not line.bbox.is_empty]
    if len(lines) < 2:
        return ALIGN_LEFT

    left_gaps = [line.bbox.x0 - block.bbox.x0 for line in lines]
    right_gaps = [block.bbox.x1 - line.bbox.x1 for line in lines]
    body_right = right_gaps[:-1]  # The last line of a paragraph is never full.

    left_aligned = max(left_gaps) < 1.5
    right_aligned = max(right_gaps) < 1.5

    # One full line proves nothing: left-aligned text often fills a line exactly.
    # Justification is only assumed when at least two body lines reach the margin.
    if left_aligned and len(body_right) >= 2 and max(body_right) < 1.5:
        return ALIGN_JUSTIFY
    if left_aligned:
        return ALIGN_LEFT
    if right_aligned:
        return ALIGN_RIGHT
    if all(abs(left - right) < 2.5 for left, right in zip(left_gaps, right_gaps)):
        return ALIGN_CENTER
    return ALIGN_LEFT


def _line_spacing(block: BlockText, fallback: float) -> float:
    """
    Determines the baseline-to-baseline distance of a paragraph.

    :param block: Decomposed paragraph.
    :param fallback: Value used when the block has fewer than two lines.
    :return: Line spacing in points.
    """
    baselines = [line.baseline for line in block.lines]
    deltas = [b - a for a, b in zip(baselines, baselines[1:]) if b - a > 0.1]
    if not deltas:
        return fallback
    deltas.sort()
    return deltas[len(deltas) // 2]


def _available_bottom(page: fitz.Page, block: BlockText) -> float:
    """
    Computes how far a paragraph may grow downwards before hitting other content.

    :param page: Page being edited.
    :param block: Paragraph that may need more room.
    :return: Maximum usable Y coordinate.
    """
    limit = page.rect.y1 - 18.0  # Keep a small bottom margin.
    horizontal = (block.bbox.x0 - 2.0, block.bbox.x1 + 2.0)

    candidates: List[float] = []
    for raw in page.get_text("dict").get("blocks", []):
        rect = fitz.Rect(raw["bbox"])
        if raw.get("number") == block.number and raw.get("type", 1) == 0:
            continue
        if rect.y0 <= block.bbox.y1 - 1.0:
            continue
        if rect.x1 < horizontal[0] or rect.x0 > horizontal[1]:
            continue  # Different column: does not block vertical growth.
        candidates.append(rect.y0 - 2.0)
    for info in page.get_image_info():
        rect = fitz.Rect(info["bbox"])
        if rect.y0 > block.bbox.y1 and not (rect.x1 < horizontal[0] or rect.x0 > horizontal[1]):
            candidates.append(rect.y0 - 2.0)

    if candidates:
        limit = min(limit, min(candidates))
    return max(limit, block.bbox.y1)


def _place_tokens(
    lines: Sequence[Sequence[_Token]],
    *,
    rect_left: float,
    rect_right: float,
    first_left: float,
    first_baseline: float,
    spacing: float,
    align: int,
    scale: float,
) -> Tuple[List[_Placed], float]:
    """
    Assigns absolute coordinates to wrapped tokens.

    :param lines: Wrapped token lines.
    :param rect_left: Left edge of the text column.
    :param rect_right: Right edge of the text column.
    :param first_left: Left edge of the first line (preserves paragraph indent).
    :param first_baseline: Baseline Y of the first line.
    :param spacing: Baseline-to-baseline distance.
    :param align: One of the ``ALIGN_*`` constants.
    :param scale: Font scale factor.
    :return: ``(placed tokens, baseline of the last line)``.
    """
    placed: List[_Placed] = []
    baseline = first_baseline

    for index, line in enumerate(lines):
        trimmed = list(line)
        while trimmed and trimmed[-1].is_space:
            trimmed.pop()
        if not trimmed:
            baseline += spacing
            continue

        left = first_left if index == 0 else rect_left
        available = rect_right - left
        natural = sum(token.width for token in trimmed) * scale
        extra_space = 0.0

        if align == ALIGN_RIGHT:
            left = rect_right - natural
        elif align == ALIGN_CENTER:
            left += max(0.0, (available - natural) / 2.0)
        elif align == ALIGN_JUSTIFY and index < len(lines) - 1:
            gaps = sum(1 for token in trimmed if token.is_space)
            if gaps and natural < available:
                extra_space = (available - natural) / gaps

        # Consecutive tokens sharing a style are written as one string.  Keeping
        # words together means the edited text still extracts, searches and
        # copies as normal lines instead of a pile of loose words.
        cursor = left
        run_text = ""
        run_left = cursor
        run_token: Optional[_Token] = None

        def flush_run() -> None:
            nonlocal run_text, run_token
            if run_text.strip() and run_token is not None:
                placed.append(
                    _Placed(
                        text=run_text,
                        x=run_left,
                        y=baseline,
                        style=run_token.style.scaled(scale),
                        font=run_token.font,
                    )
                )
            run_text = ""
            run_token = None

        for token in trimmed:
            starts_new_run = run_token is not None and token.style != run_token.style
            justified_gap = token.is_space and extra_space > 0.0
            if starts_new_run or justified_gap:
                flush_run()
                if justified_gap:
                    cursor += token.width * scale + extra_space
                    run_left = cursor
                    continue
                run_left = cursor

            if run_token is None:
                run_left = cursor
            run_text += token.text
            run_token = token
            cursor += token.width * scale
        flush_run()

        if index < len(lines) - 1:
            baseline += spacing
    return placed, baseline


def _write_placed(page: fitz.Page, placed: Sequence[_Placed]) -> None:
    """
    Draws positioned tokens onto the page, grouped by colour and opacity.

    :param page: Page to draw on.
    :param placed: Tokens with absolute coordinates.
    """
    groups: Dict[Tuple[Tuple[float, float, float], float], List[_Placed]] = {}
    for item in placed:
        standard = item.font.standard_name
        if standard:
            # Standard fonts are referenced by name: nothing gets embedded and the
            # page keeps using the font resource it already had.
            try:
                page.insert_text(
                    fitz.Point(item.x, item.y),
                    item.text,
                    fontname=standard,
                    fontsize=item.style.size,
                    color=item.style.color,
                    fill_opacity=item.style.alpha if item.style.alpha > 0 else 1.0,
                    overlay=True,
                )
                continue
            except Exception:
                pass  # Unsupported character for this encoding: embed instead.
        key = (tuple(item.style.color), round(item.style.alpha, 3))
        groups.setdefault(key, []).append(item)

    for (color, opacity), items in groups.items():
        writer = fitz.TextWriter(page.rect)
        for item in items:
            writer.append(
                fitz.Point(item.x, item.y),
                item.text,
                font=item.font.font,
                fontsize=item.style.size,
            )
        writer.write_text(page, color=color, opacity=opacity if opacity > 0 else 1.0, overlay=True)


def _apply_redactions(page: fitz.Page, rects: Sequence[fitz.Rect]) -> None:
    """
    Removes the text inside ``rects`` while preserving images and vector art.

    :param page: Page being edited.
    :param rects: Areas whose text should disappear.
    """
    if not rects:
        return
    for rect in rects:
        padded = fitz.Rect(rect)
        padded.x0 -= REDACT_PADDING
        padded.y0 -= REDACT_PADDING
        padded.x1 += REDACT_PADDING
        padded.y1 += REDACT_PADDING
        page.add_redact_annot(padded)
    page.apply_redactions(
        images=getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0),
        graphics=getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0),
        text=getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0),
    )


# ---------------------------------------------------------------------------
# Public editing operations
# ---------------------------------------------------------------------------
def replace_selection(
    page: fitz.Page,
    selection: TextSelection,
    new_text: str,
    *,
    reflow: bool = True,
    style: Optional[SpanStyle] = None,
    use_embedded_fonts: bool = True,
) -> EditResult:
    """
    Replaces the highlighted text with ``new_text``.

    With ``reflow=True`` (the default) the whole paragraph is re-wrapped using the
    original per-character styles, so the words after the edit move naturally.  The
    font size is reduced automatically (down to 62%) if the new text cannot fit in
    the space available; if the paragraph has free room underneath, that room is
    used before scaling down.

    With ``reflow=False`` only the highlighted run is rewritten in place, which is
    the right choice for form-like documents where nothing may move.

    :param page: Page being edited.
    :param selection: Words to replace.
    :param new_text: Replacement text (empty string deletes the selection).
    :param reflow: Whether the surrounding paragraph may be re-wrapped.
    :param style: Optional style override for the replacement run.
    :param use_embedded_fonts: Re-use fonts embedded in the PDF when possible.
    :return: An :class:`EditResult` describing what happened.
    :raises TextEditError: When the selection cannot be edited.
    """
    if selection.is_empty:
        raise TextEditError("Select some text before editing.")

    warnings: List[str] = []
    block_number = selection.block
    local = selection.limited_to_block(block_number)
    if len(selection.blocks) > 1:
        warnings.append(
            "The selection spanned several paragraphs; only the first one was edited."
        )

    block = extract_block(page, block_number)
    if block is None or not block.chars:
        raise TextEditError(
            "This page has no editable text here (it may be a scanned image)."
        )

    first, last = _selection_char_range(block, local)
    old_text = "".join(char.char for char in block.chars[first : last + 1])
    replacement_style = style or probe_style(page, local) or block.chars[first].style

    resolver = FontResolver(document=page.parent, use_embedded=use_embedded_fonts)

    if reflow:
        result = _replace_with_reflow(
            page,
            block,
            first,
            last,
            new_text,
            replacement_style,
            resolver,
        )
    else:
        result = _replace_in_place(
            page,
            block,
            first,
            last,
            new_text,
            replacement_style,
            resolver,
            local,
        )

    result.page_index = selection.page_index
    result.block_number = block_number
    result.old_text = old_text
    result.new_text = new_text
    result.warnings.extend(warnings)
    return result


def _replace_with_reflow(
    page: fitz.Page,
    block: BlockText,
    first: int,
    last: int,
    new_text: str,
    style: SpanStyle,
    resolver: FontResolver,
) -> EditResult:
    """Rewrites a whole paragraph so that the text after the edit reflows."""
    runs = _chars_to_runs(block.chars[:first], join_lines=True)
    if new_text:
        runs.append(_StyledRun(new_text, style))
    runs.extend(_chars_to_runs(block.chars[last + 1 :], join_lines=True))

    # Collapse duplicated whitespace created by the splice.
    merged: List[_StyledRun] = []
    for run in runs:
        if merged and merged[-1].text.endswith(" ") and run.text.startswith(" "):
            run = _StyledRun(run.text.lstrip(" "), run.style)
            if not run.text:
                continue
        merged.append(run)

    tokens = _tokenize(merged, resolver, page)
    plain = "".join(run.text for run in merged).strip()

    column_left = block.bbox.x0
    column_right = block.bbox.x1
    first_left = block.lines[0].origin_x if block.lines else column_left
    first_baseline = block.lines[0].baseline if block.lines else block.bbox.y1
    natural_spacing = _line_spacing(block, style.line_height * 1.16)
    align = _detect_alignment(block)
    bottom_limit = _available_bottom(page, block)
    original_bottom = block.bbox.y1
    max_descender = max((abs(char.style.descender) * char.style.size for char in block.chars), default=2.0)

    scale = 1.0
    placed: List[_Placed] = []
    last_baseline = first_baseline
    expanded = False

    if plain:
        while True:
            lines = _wrap(tokens, column_right - column_left, scale)
            spacing = natural_spacing * scale
            placed, last_baseline = _place_tokens(
                lines,
                rect_left=column_left,
                rect_right=column_right,
                first_left=first_left,
                first_baseline=first_baseline,
                spacing=spacing,
                align=align,
                scale=scale,
            )
            needed_bottom = last_baseline + max_descender * scale
            if needed_bottom <= bottom_limit + 0.5 or scale <= MIN_FONT_SCALE:
                expanded = needed_bottom > original_bottom + 0.5
                break
            scale = max(MIN_FONT_SCALE, scale - 0.04)

    redact_rects = [line.bbox for line in block.lines] or [block.bbox]
    _apply_redactions(page, redact_rects)
    if placed:
        _write_placed(page, placed)

    result = EditResult(
        page_index=0,
        block_number=block.number,
        old_text="",
        new_text=new_text,
        reflowed=True,
        font_scale=scale,
        expanded=expanded,
    )
    if scale <= MIN_FONT_SCALE + 1e-6 and plain:
        result.warnings.append(
            "The replacement text is much longer than the original; it was scaled to the minimum size."
        )
    return result


def _replace_in_place(
    page: fitz.Page,
    block: BlockText,
    first: int,
    last: int,
    new_text: str,
    style: SpanStyle,
    resolver: FontResolver,
    selection: TextSelection,
) -> EditResult:
    """Rewrites only the highlighted run, keeping every other word where it is."""
    start_char = block.chars[first]
    baseline = start_char.origin[1]
    left = start_char.bbox.x0

    # The replacement may use the gap up to the next character on the same line.
    following = next(
        (char for char in block.chars[last + 1 :] if char.line == start_char.line),
        None,
    )
    line_bbox = block.lines[start_char.line].bbox if start_char.line < len(block.lines) else block.bbox
    right = following.bbox.x0 - 0.5 if following is not None else line_bbox.x1

    available = max(4.0, right - left)
    scale = 1.0
    placed: List[_Placed] = []

    if new_text:
        font = resolver.resolve(style, new_text, page)
        width = font.text_length(new_text, style.size)
        if width > available:
            scale = max(MIN_FONT_SCALE, available / width) if width > 0 else 1.0
        placed = [
            _Placed(text=new_text, x=left, y=baseline, style=style.scaled(scale), font=font)
        ]

    _apply_redactions(page, selection.rects)
    if placed:
        _write_placed(page, placed)

    result = EditResult(
        page_index=0,
        block_number=block.number,
        old_text="",
        new_text=new_text,
        reflowed=False,
        font_scale=scale,
    )
    if scale < 1.0:
        result.warnings.append(
            "Replacement text was condensed to stay inside the original line."
        )
    return result


def delete_selection(page: fitz.Page, selection: TextSelection, *, reflow: bool = True) -> EditResult:
    """
    Deletes the highlighted text (the Backspace/Delete action of the editor).

    :param page: Page being edited.
    :param selection: Words to delete.
    :param reflow: Whether the remaining paragraph text should close the gap.
    :return: An :class:`EditResult` describing what happened.
    """
    return replace_selection(page, selection, "", reflow=reflow)


def insert_text_box(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    style: Optional[SpanStyle] = None,
    align: int = ALIGN_LEFT,
    use_embedded_fonts: bool = False,
) -> fitz.Rect:
    """
    Draws a new block of text inside ``rect`` (the "add text" tool).

    The text is wrapped with the same layout engine used for edits, so long text
    flows onto several lines and is scaled down if the box is too small.

    :param page: Page to draw on.
    :param rect: Target rectangle in unrotated PDF coordinates.
    :param text: Text to insert.
    :param style: Font/size/colour of the new text.
    :param align: One of the ``ALIGN_*`` constants.
    :param use_embedded_fonts: Re-use fonts embedded in the PDF when possible.
    :return: The rectangle actually occupied by the text.
    :raises TextEditError: When ``text`` is empty.
    """
    if not text.strip():
        raise TextEditError("Cannot insert empty text.")

    box = fitz.Rect(rect)
    box.normalize()
    if box.width < 4 or box.height < 4:
        box = fitz.Rect(box.x0, box.y0, box.x0 + max(box.width, 120.0), box.y0 + max(box.height, 24.0))

    active_style = style or SpanStyle()
    resolver = FontResolver(document=page.parent, use_embedded=use_embedded_fonts)
    tokens = _tokenize([_StyledRun(text, active_style)], resolver, page)
    spacing_base = active_style.line_height * 1.16

    scale = 1.0
    while True:
        lines = _wrap(tokens, box.width, scale)
        spacing = spacing_base * scale
        first_baseline = box.y0 + active_style.ascender * active_style.size * scale
        placed, last_baseline = _place_tokens(
            lines,
            rect_left=box.x0,
            rect_right=box.x1,
            first_left=box.x0,
            first_baseline=first_baseline,
            spacing=spacing,
            align=align,
            scale=scale,
        )
        bottom = last_baseline + abs(active_style.descender) * active_style.size * scale
        if bottom <= box.y1 + 0.5 or scale <= MIN_FONT_SCALE:
            break
        scale = max(MIN_FONT_SCALE, scale - 0.04)

    _write_placed(page, placed)
    used = fitz.Rect(box.x0, box.y0, box.x1, min(box.y1, bottom))
    return used


def redact_selection(
    page: fitz.Page,
    selection: TextSelection,
    fill: Optional[Tuple[float, float, float]] = (0.0, 0.0, 0.0),
) -> int:
    """
    Permanently removes the selected text and paints a box over it.

    :param page: Page being edited.
    :param selection: Words to redact.
    :param fill: Fill colour of the redaction box, or ``None`` for no box.
    :return: Number of rectangles redacted.
    :raises TextEditError: When nothing is selected.
    """
    if selection.is_empty:
        raise TextEditError("Select some text before redacting.")
    rects = selection.rects
    for rect in rects:
        page.add_redact_annot(rect, fill=fill)
    page.apply_redactions(
        images=getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0),
        graphics=getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0),
    )
    return len(rects)


def page_has_text(page: fitz.Page) -> bool:
    """
    Reports whether a page carries extractable text.

    :param page: Page to inspect.
    :return: True when at least one character can be extracted.
    """
    return bool(page.get_text("text").strip())


def estimate_text_width(text: str, style: SpanStyle) -> float:
    """
    Measures text width without touching a page (used by the UI for previews).

    :param text: Text to measure.
    :param style: Style the text would be drawn with.
    :return: Width in points.
    """
    resolver = FontResolver(use_embedded=False)
    return resolver.resolve(style, text).text_length(text, style.size)


__all__ = [
    "ALIGN_CENTER",
    "ALIGN_JUSTIFY",
    "ALIGN_LEFT",
    "ALIGN_RIGHT",
    "BlockText",
    "CharBox",
    "EditResult",
    "TextSelection",
    "Word",
    "delete_selection",
    "estimate_text_width",
    "extract_block",
    "find_block_at",
    "highlight_selection",
    "insert_text_box",
    "page_has_text",
    "page_words",
    "probe_style",
    "redact_selection",
    "remove_highlights_at",
    "replace_selection",
    "search_page",
    "select_line_at",
    "select_word_at",
    "select_words_in_rect",
]