"""
Integration tests that drive the real editor window.

The window is created once for the whole module and every test works on a freshly
opened document.  Dialogs and message boxes are replaced by stubs so the tests never
block.  If no display is available (headless CI), the whole module is skipped.
"""

from pathlib import Path
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from pdf_editor.core import image_ops, text_ops
from pdf_editor.core.utils import PDFEditorError

pytest.importorskip("customtkinter")

app_module = pytest.importorskip("pdf_editor.gui.app")


@pytest.fixture(scope="module")
def app():
    """Creates the editor window once for the whole module."""
    try:
        window = app_module.PDFEditorStudio()
        window.update()
    except Exception as error:  # pragma: no cover - headless environment
        pytest.skip(f"no usable display: {error}")
    yield window
    try:
        window.session = None  # Avoid the "unsaved changes" prompt on close.
        window.destroy()
    except Exception:
        pass


@pytest.fixture
def editor(app, paragraph_pdf, monkeypatch):
    """Opens a document and neutralises every blocking dialog."""
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **k: True)

    app.session = None  # Drop any document a previous test left behind.
    app.tool_selector.set("Select & Edit Text")
    app._on_tool_change("Select & Edit Text")
    app.open_document(paragraph_pdf)
    pump(app)
    assert app.session is not None
    return app


def pump(window, cycles: int = 6) -> None:
    """Lets Tk process pending events."""
    for _ in range(cycles):
        window.update()


def select_body_words(window, count: int = 3):
    """Highlights the first words of the wrapped body paragraph."""
    page = window.session.page(0)
    words = text_ops.page_words(page)
    body = [word for word in words if word.rect.y0 > 130 and word.rect.y1 < 270]
    block = body[0].block
    selection = text_ops.TextSelection(0, [w for w in body if w.block == block][:count])
    window.canvas_view.set_selection(selection)
    pump(window)
    return selection


# ---------------------------------------------------------------------------
# Document lifecycle
# ---------------------------------------------------------------------------
def test_open_updates_window(editor):
    """Opening a file fills the sidebar, the counters and the title."""
    assert editor.session.page_count == 2
    assert "paragraphs.pdf" in editor.title()
    assert editor.page_total_label.cget("text") == "of 2"
    assert editor.thumbnails.session is editor.session


def test_open_reports_broken_file(app, broken_pdf, monkeypatch):
    """A corrupt file produces an error message instead of a crash."""
    seen = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda title, msg, **k: seen.append(msg))
    app.session = None
    app.open_document(broken_pdf)
    pump(app)
    assert seen
    assert app.session is None


def test_navigation_and_zoom(editor):
    """Page navigation and zoom keep the view and the toolbar in step."""
    editor.goto_page(1)
    pump(editor)
    assert editor.canvas_view.page_index == 1
    assert editor.page_entry.get() == "2"

    editor.step_page(-1)
    pump(editor)
    assert editor.canvas_view.page_index == 0

    before = editor.canvas_view.zoom
    editor.zoom_in()
    pump(editor)
    assert editor.canvas_view.zoom > before
    assert "%" in editor.zoom_label.cget("text")

    editor.fit_page()
    pump(editor)
    assert editor.canvas_view.zoom > 0


# ---------------------------------------------------------------------------
# Text editing
# ---------------------------------------------------------------------------
def test_selection_fills_the_panel(editor):
    """Highlighting text shows it and its style in the side panel."""
    selection = select_body_words(editor)
    assert selection.text in editor.selected_text_box.get("1.0", "end")
    assert "pt" in editor.style_label.cget("text")


def test_replace_and_undo(editor):
    """Typing over a selection replaces it and can be undone."""
    selection = select_body_words(editor)
    original = selection.text

    editor.replace_selected_text("Rewritten opening words")
    pump(editor)
    text = editor.session.page(0).get_text("text")
    assert "Rewritten opening words" in text
    assert original not in text
    assert editor.session.dirty is True

    editor.undo()
    pump(editor)
    assert original in editor.session.page(0).get_text("text")
    assert "Rewritten opening words" not in editor.session.page(0).get_text("text")

    editor.redo()
    pump(editor)
    assert "Rewritten opening words" in editor.session.page(0).get_text("text")


def test_backspace_deletes_selection(editor):
    """The Backspace key removes the highlighted words."""
    selection = select_body_words(editor, count=2)
    removed = selection.text

    editor.canvas_view._on_erase_key(SimpleNamespace())
    pump(editor)
    assert removed not in editor.session.page(0).get_text("text")


def test_typing_opens_the_inline_editor(editor):
    """Typing a character starts the inline editor prefilled with it."""
    select_body_words(editor, count=2)
    editor.canvas_view._on_key(SimpleNamespace(char="Z", state=0))
    pump(editor)

    inline = editor.canvas_view._inline_editor
    assert inline is not None
    assert inline.get() == "Z"

    inline.delete(0, "end")
    inline.insert(0, "Zebra crossing")
    editor.canvas_view._close_inline_editor(apply_text=True)
    pump(editor)
    assert "Zebra crossing" in editor.session.page(0).get_text("text")


def test_panel_replacement_and_style_override(editor):
    """The side panel replaces the selection using its font settings."""
    select_body_words(editor, count=2)
    editor.replacement_box.delete("1.0", "end")
    editor.replacement_box.insert("1.0", "Panel replacement")
    editor.font_selector.set("Courier")
    editor.size_entry.delete(0, "end")
    editor.size_entry.insert(0, "13")

    editor.apply_replacement_from_panel()
    pump(editor)

    spans = [
        span
        for block in editor.session.page(0).get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if "Panel replacement" in span["text"]
    ]
    assert spans
    assert "Courier" in spans[0]["font"]

    editor.font_selector.set("Keep original")
    editor.size_entry.delete(0, "end")


def test_highlight_and_redact(editor):
    """Highlighting adds an annotation; redaction removes the text."""
    select_body_words(editor, count=2)
    editor.highlight_selection()
    pump(editor)
    assert len(list(editor.session.page(0).annots())) >= 1

    selection = select_body_words(editor, count=2)
    removed = selection.text
    editor.redact_selection()
    pump(editor)
    assert removed not in editor.session.page(0).get_text("text")


def test_find_next_selects_a_match(editor):
    """Searching jumps to the page holding the match and selects it."""
    editor.search_entry.delete(0, "end")
    editor.search_entry.insert(0, "Reference")
    editor.find_next()
    pump(editor)

    assert editor.canvas_view.page_index == 1
    assert "Reference" in editor.canvas_view.selection.text

    editor.search_entry.delete(0, "end")
    editor.search_entry.insert(0, "nothing-like-this-exists")
    editor.find_next()
    pump(editor)
    assert "not found" in editor.status_label.cget("text")


def test_insert_text_box(editor, monkeypatch):
    """The add-text tool writes a new block of text on the page."""
    monkeypatch.setattr(
        app_module,
        "TextBoxDialog",
        lambda *a, **k: SimpleNamespace(
            show=lambda: {
                "text": "Brand new text box",
                "font": "Helvetica",
                "size": 12.0,
                "bold": True,
                "italic": False,
                "color": (0.0, 0.0, 0.0),
            }
        ),
    )
    editor.request_text_box(fitz.Rect(60, 500, 400, 560), 0)
    pump(editor)
    assert "Brand new text box" in editor.session.page(0).get_text("text")


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def test_insert_move_and_delete_image(editor, png_file):
    """An image can be placed, repositioned by the panel, and removed."""
    editor.insert_image(png_file)
    pump(editor)
    assert editor.canvas_view.pending_image is not None
    assert editor.image_entries["x"].get() != ""

    for key, value in (("x", "70"), ("y", "430"), ("w", "200"), ("h", "100")):
        editor.image_entries[key].delete(0, "end")
        editor.image_entries[key].insert(0, value)
    editor.apply_image_geometry()
    pump(editor)
    assert editor.canvas_view.pending_image.rect.x0 == pytest.approx(70, abs=1)

    editor.place_pending_image()
    pump(editor)
    images = image_ops.list_images(editor.session.page(0))
    assert len(images) == 1
    assert images[0].bbox.x0 == pytest.approx(70, abs=2)

    editor.canvas_view.set_mode(app_module.MODE_IMAGE)
    assert editor.canvas_view.select_image_at(fitz.Point(120, 460)) is True

    editor.move_existing_image(editor.canvas_view.selected_image, fitz.Rect(200, 600, 400, 700), 0)
    pump(editor)
    moved = image_ops.list_images(editor.session.page(0))
    assert len(moved) == 1
    assert moved[0].bbox.x0 == pytest.approx(200, abs=2)

    editor.canvas_view.selected_image = moved[0]
    editor.delete_selected_image()
    pump(editor)
    assert image_ops.list_images(editor.session.page(0)) == []


def test_cancel_image_placement(editor, png_file):
    """Escape drops a placement without touching the document."""
    editor.insert_image(png_file)
    pump(editor)
    editor.canvas_view._on_escape_key(SimpleNamespace())
    pump(editor)
    assert editor.canvas_view.pending_image is None
    assert image_ops.list_images(editor.session.page(0)) == []


def test_image_panel_rejects_bad_numbers(editor, png_file):
    """Non-numeric geometry is reported instead of raising."""
    editor.insert_image(png_file)
    pump(editor)
    editor.image_entries["w"].delete(0, "end")
    editor.image_entries["w"].insert(0, "wide")
    editor.apply_image_geometry()
    pump(editor)
    assert "numbers" in editor.status_label.cget("text")
    editor.canvas_view.cancel_pending_image()


# ---------------------------------------------------------------------------
# Page management
# ---------------------------------------------------------------------------
def test_add_blank_duplicate_delete(editor, monkeypatch):
    """Blank pages, duplicates and deletions update the document and sidebar."""
    monkeypatch.setattr(
        app_module,
        "BlankPageDialog",
        lambda *a, **k: SimpleNamespace(
            show=lambda: {
                "size": "A4",
                "landscape": False,
                "position": "After page 1",
                "count": 2,
            }
        ),
    )
    editor.add_blank_page("dialog")
    pump(editor)
    assert editor.session.page_count == 4
    assert editor.session.page(1).get_text("text").strip() == ""

    editor.goto_page(0)
    editor.duplicate_pages()
    pump(editor)
    assert editor.session.page_count == 5

    editor.thumbnails.selected = [1]
    editor.delete_pages()
    pump(editor)
    assert editor.session.page_count == 4


def test_rotate_and_move_page(editor):
    """Rotation and reordering work from the sidebar commands."""
    editor.goto_page(0)
    editor.thumbnails.selected = []
    editor.rotate_pages(90)
    pump(editor)
    assert editor.session.page(0).rotation == 90

    first_text = editor.session.page(0).get_text("text")
    editor.move_page(1)
    pump(editor)
    assert editor.session.page(1).get_text("text") == first_text


def test_merge_and_extract(editor, other_pdf, tmp_path, monkeypatch):
    """Merging inserts the pages; extraction writes a new file."""
    monkeypatch.setattr(
        app_module,
        "MergeDialog",
        lambda *a, **k: SimpleNamespace(
            show=lambda: {"files": [other_pdf], "position": "End of document", "bookmarks": True}
        ),
    )
    before = editor.session.page_count
    editor.merge_pdfs()
    pump(editor)
    assert editor.session.page_count == before + 2
    assert "Merged page 1" in editor.session.page(before).get_text("text")

    target = tmp_path / "extracted.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))
    editor.thumbnails.selected = [0, 1]
    editor.extract_pages()
    pump(editor)
    assert target.exists()
    with fitz.open(target) as extracted:
        assert extracted.page_count == 2


def test_save_and_save_as(editor, tmp_path, monkeypatch):
    """Both save paths write a readable file and clear the dirty flag."""
    select_body_words(editor, count=2)
    editor.delete_selected_text()
    pump(editor)
    assert editor.session.dirty is True

    editor.save_document()
    pump(editor)
    assert editor.session.dirty is False

    target = tmp_path / "exported.pdf"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))
    editor.save_document_as()
    pump(editor)
    assert target.exists()
    with fitz.open(target) as exported:
        assert exported.page_count == editor.session.page_count


# ---------------------------------------------------------------------------
# Drag and drop
# ---------------------------------------------------------------------------
def test_drop_pdf_merges_into_document(editor, other_pdf, monkeypatch):
    """Dropping a PDF onto an open document merges it after confirmation."""
    monkeypatch.setattr(
        app_module,
        "MergeDialog",
        lambda *a, **k: SimpleNamespace(
            show=lambda: {"files": [other_pdf], "position": "End of document", "bookmarks": False}
        ),
    )
    before = editor.session.page_count
    editor.handle_dropped_files([Path(other_pdf)])
    pump(editor)
    assert editor.session.page_count == before + 2


def test_drop_image_starts_placement(editor, png_file):
    """Dropping an image starts an image placement."""
    editor.handle_dropped_files([Path(png_file)])
    pump(editor)
    assert editor.canvas_view.pending_image is not None
    editor.canvas_view.cancel_pending_image()


def test_drop_unsupported_file_is_reported(editor, tmp_path):
    """Anything else is refused with a message."""
    other = tmp_path / "notes.txt"
    other.write_text("hello")
    editor.handle_dropped_files([other])
    pump(editor)
    assert "Office documents" in editor.status_label.cget("text")


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------
def test_commands_without_document_are_safe(app, monkeypatch):
    """Every command reports 'open a PDF first' instead of failing."""
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    app.session = None
    app.canvas_view.detach()
    app.thumbnails.detach()

    app.delete_pages()
    app.duplicate_pages()
    app.rotate_pages(90)
    app.move_page(1)
    app.save_document()
    app.find_next()
    app.insert_image(Path("nothing.png"))
    pump(app)
    assert "Open a PDF first" in app.status_label.cget("text")


def test_editing_without_selection_is_reported(editor):
    """Text commands need a selection and say so."""
    editor.canvas_view.clear_selection()
    editor.delete_selected_text()
    pump(editor)
    assert "Highlight some text first" in editor.status_label.cget("text")


# ---------------------------------------------------------------------------
# Direct canvas interaction
# ---------------------------------------------------------------------------
def _widget_point(window, pdf_point):
    """Converts a PDF point into an unscrolled canvas widget coordinate."""
    canvas = window.canvas_view
    canvas.canvas.xview_moveto(0.0)
    canvas.canvas.yview_moveto(0.0)
    rect = canvas.to_canvas_rect(fitz.Rect(pdf_point, pdf_point))
    return SimpleNamespace(x=rect[0], y=rect[1], state=0)


def test_drag_selects_words(editor):
    """A rubber-band drag over the page highlights the words underneath."""
    page = editor.session.page(0)
    words = [w for w in text_ops.page_words(page) if w.rect.y0 > 130 and w.rect.y1 < 200]
    start, end = words[0].rect.tl, words[2].rect.br

    view = editor.canvas_view
    view._on_press(_widget_point(editor, start))
    view._on_motion(_widget_point(editor, end))
    view._on_release(_widget_point(editor, end))
    pump(editor)

    assert view.selection is not None
    assert words[0].text in view.selection.text
    assert words[2].text in view.selection.text


def test_drag_moves_and_resizes_a_pending_image(editor, png_file):
    """The image frame follows the mouse and its grips resize it."""
    editor.insert_image(png_file)
    pump(editor)
    view = editor.canvas_view
    original = fitz.Rect(view.pending_image.rect)

    inside = fitz.Point(original.x0 + original.width / 2, original.y0 + original.height / 2)
    view._on_press(_widget_point(editor, inside))
    view._on_motion(_widget_point(editor, fitz.Point(inside.x + 60, inside.y + 40)))
    view._on_release(_widget_point(editor, fitz.Point(inside.x + 60, inside.y + 40)))
    pump(editor)

    moved = fitz.Rect(view.pending_image.rect)
    assert moved.x0 == pytest.approx(original.x0 + 60, abs=3)
    assert moved.width == pytest.approx(original.width, abs=0.5)

    corner = fitz.Point(moved.x1, moved.y1)
    view._on_press(_widget_point(editor, corner))
    assert view._drag is not None and view._drag.kind == "resize"
    view._on_motion(_widget_point(editor, fitz.Point(corner.x + 50, corner.y + 50)))
    view._on_release(_widget_point(editor, fitz.Point(corner.x + 50, corner.y + 50)))
    pump(editor)

    resized = view.pending_image.rect
    assert resized.width > moved.width
    assert resized.width / resized.height == pytest.approx(moved.width / moved.height, abs=0.05)
    view.cancel_pending_image()


def test_editing_works_on_a_rotated_page(editor):
    """Coordinates round-trip on rotated pages and editing still works."""
    editor.rotate_pages(90)
    pump(editor)
    page = editor.session.page(0)
    assert page.rotation == 90

    view = editor.canvas_view
    word = [w for w in text_ops.page_words(page) if w.rect.y0 > 130][0]
    centre = fitz.Point((word.rect.x0 + word.rect.x1) / 2, (word.rect.y0 + word.rect.y1) / 2)

    event = _widget_point(editor, centre)
    mapped = view.to_pdf_point(event.x, event.y)
    assert mapped.x == pytest.approx(centre.x, abs=1.0)
    assert mapped.y == pytest.approx(centre.y, abs=1.0)

    view._on_double_click(event)
    pump(editor)
    assert view.selection is not None
    assert view.selection.text == word.text

    editor.replace_selected_text("Turned")
    pump(editor)
    assert "Turned" in editor.session.page(0).get_text("text")


def test_drag_and_drop_targets_are_registered(app):
    """The window itself and the page view accept dropped files."""
    from pdf_editor.gui import dnd

    if not dnd.dnd_available():
        pytest.skip("tkinterdnd2 is not installed")
    assert app.dnd_enabled is True
    assert dnd.register_drop_target(app, lambda paths: None) is True
    assert dnd.register_drop_target(app.canvas_view.canvas, lambda paths: None) is True


def test_parse_drop_data(app, paragraph_pdf, tmp_path):
    """Dropped payloads are turned into existing file paths."""
    from pdf_editor.gui import dnd

    spaced = tmp_path / "a file with spaces.pdf"
    spaced.write_bytes(paragraph_pdf.read_bytes())

    payload = "{%s} %s" % (spaced, paragraph_pdf)
    parsed = dnd.parse_drop_data(app, payload)
    assert set(parsed) == {spaced, paragraph_pdf}

    # Windows style payload: backslashes must survive parsing.
    windows_payload = "{%s} %s" % (
        str(spaced).replace("/", "\\"),
        str(paragraph_pdf).replace("/", "\\"),
    )
    assert len(dnd.parse_drop_data(app, windows_payload)) == 2

    assert dnd.parse_drop_data(app, "") == []
    assert dnd.parse_drop_data(app, str(tmp_path / "missing.pdf")) == []


# ---------------------------------------------------------------------------
# Export as Word and compression
# ---------------------------------------------------------------------------
def _stub_dialog(values):
    """Builds a dialog stand-in whose show() returns fixed values."""
    return lambda *args, **kwargs: SimpleNamespace(show=lambda: values)


def test_export_as_word_from_the_window(editor, tmp_path, monkeypatch):
    """The Word export writes a document holding the page text."""
    docx = pytest.importorskip("docx")
    target = tmp_path / "from_gui.docx"

    monkeypatch.setattr(
        app_module, "WordExportDialog", _stub_dialog({"pages": "All 2 pages", "method": "text"})
    )
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: False)

    editor.export_as_word()
    pump(editor, 12)

    assert target.exists()
    text = "\n".join(p.text for p in docx.Document(str(target)).paragraphs)
    assert "Editing Engine Test Page" in text
    assert "Exported" in editor.status_label.cget("text")


def test_export_as_word_includes_unsaved_edits(editor, tmp_path, monkeypatch):
    """What is on screen is what gets exported."""
    docx = pytest.importorskip("docx")
    selection = select_body_words(editor, count=3)
    assert selection.text
    editor.replace_selected_text("Exported after editing")
    pump(editor)

    target = tmp_path / "edited.docx"
    monkeypatch.setattr(
        app_module, "WordExportDialog", _stub_dialog({"pages": "Current page (1)", "method": "text"})
    )
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: False)

    editor.export_as_word()
    pump(editor, 12)

    text = "\n".join(p.text for p in docx.Document(str(target)).paragraphs)
    assert "Exported after editing" in text


def test_export_as_word_needs_a_backend(editor, monkeypatch):
    """Without a converter the user is told what to install."""
    seen = []
    monkeypatch.setattr(app_module.word_export, "available_methods", lambda: [])
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda title, msg, **k: seen.append(msg))

    editor.export_as_word()
    pump(editor)
    assert seen
    assert "pdf2docx" in seen[0]


def test_export_as_word_can_be_cancelled(editor, monkeypatch, tmp_path):
    """Cancelling either dialog writes nothing."""
    asked = []
    monkeypatch.setattr(app_module, "WordExportDialog", _stub_dialog(None))
    monkeypatch.setattr(
        app_module.filedialog, "asksaveasfilename", lambda **k: asked.append(k) or ""
    )
    editor.export_as_word()
    pump(editor)
    assert asked == []

    monkeypatch.setattr(
        app_module, "WordExportDialog", _stub_dialog({"pages": "All 2 pages", "method": "text"})
    )
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: "")
    editor.export_as_word()
    pump(editor)
    assert not list(tmp_path.glob("*.docx"))


def test_compress_from_the_window(app, photo_pdf, tmp_path, monkeypatch):
    """Compressing writes a smaller file and reports the numbers."""
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **k: True)
    app.session = None
    app.open_document(photo_pdf)
    pump(app, 10)

    target = tmp_path / "compressed.pdf"
    monkeypatch.setattr(
        app_module, "CompressDialog", _stub_dialog({"level": "high", "downsample_images": True})
    )
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))

    shown = []
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda title, msg, **k: shown.append(msg))
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: False)

    app.compress_document()
    pump(app, 12)

    assert target.exists()
    assert target.stat().st_size < photo_pdf.stat().st_size
    assert shown and "Saved:" in shown[0]
    assert "Compressed:" in app.status_label.cget("text")

    with fitz.open(target) as compressed:
        assert compressed.page_count == 2
        assert "Photo page 1" in compressed[0].get_text("text")


def test_compress_can_reopen_the_result(app, photo_pdf, tmp_path, monkeypatch):
    """Answering yes loads the compressed file into the editor."""
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *a, **k: True)
    app.session = None
    app.open_document(photo_pdf)
    pump(app, 10)

    target = tmp_path / "reopened.pdf"
    monkeypatch.setattr(
        app_module, "CompressDialog", _stub_dialog({"level": "medium", "downsample_images": True})
    )
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **k: str(target))

    app.compress_document()
    pump(app, 12)

    assert app.session is not None
    assert app.session.path == target


def test_compress_can_be_cancelled(editor, monkeypatch):
    """Cancelling the level dialog never asks for a file name."""
    asked = []
    monkeypatch.setattr(app_module, "CompressDialog", _stub_dialog(None))
    monkeypatch.setattr(
        app_module.filedialog, "asksaveasfilename", lambda **k: asked.append(k) or ""
    )
    editor.compress_document()
    pump(editor)
    assert asked == []


def test_task_dialog_runs_a_worker(app):
    """The progress dialog returns the worker result and forwards progress."""
    seen = []

    def worker(progress):
        for step in range(1, 4):
            progress(step / 3.0, f"step {step}")
            seen.append(step)
        return "finished"

    dialog = app_module.TaskDialog(app, "Working", "Please wait...")
    assert dialog.run(worker) == "finished"
    assert seen == [1, 2, 3]


def test_task_dialog_propagates_errors(app):
    """A failing worker raises on the caller's side."""

    def worker(progress):
        raise ValueError("worker exploded")

    dialog = app_module.TaskDialog(app, "Working", "Please wait...")
    with pytest.raises(ValueError, match="worker exploded"):
        dialog.run(worker)


def test_failed_export_is_reported(editor, monkeypatch, tmp_path):
    """An error inside the worker reaches the user as a message."""
    seen = []
    monkeypatch.setattr(
        app_module, "WordExportDialog", _stub_dialog({"pages": "All 2 pages", "method": "text"})
    )
    monkeypatch.setattr(
        app_module.filedialog, "asksaveasfilename", lambda **k: str(tmp_path / "boom.docx")
    )
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda title, msg, **k: seen.append(msg))

    def explode(*args, **kwargs):
        raise PDFEditorError("conversion went wrong")

    monkeypatch.setattr(app_module.word_export, "export_to_docx", explode)

    editor.export_as_word()
    pump(editor, 10)
    assert seen
    assert "conversion went wrong" in seen[0]


# ---------------------------------------------------------------------------
# Importing Office documents
# ---------------------------------------------------------------------------
def _stub_import_dialog(values):
    """Builds an import dialog stand-in that keeps the real constants."""
    real = app_module.ImportDialog

    class Stub:
        AFTER_OPEN = real.AFTER_OPEN
        AFTER_MERGE = real.AFTER_MERGE
        AFTER_NOTHING = real.AFTER_NOTHING

        def __init__(self, *args, **kwargs):
            pass

        def show(self):
            return values

    return Stub


def test_import_word_document_opens_the_pdf(app, word_document, tmp_path, monkeypatch):
    """Importing a .docx converts it and loads the result into the editor."""
    pytest.importorskip("reportlab")
    target = tmp_path / "imported.pdf"
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(
        app_module,
        "ImportDialog",
        _stub_import_dialog(
            {
                "destination": target,
                "backend": "builtin",
                "after": app_module.ImportDialog.AFTER_OPEN,
            }
        ),
    )

    app.session = None
    app.import_document(word_document)
    pump(app, 14)

    assert target.exists()
    assert app.session is not None
    assert app.session.path == target
    assert "Imported Report" in app.session.page(0).get_text("text")
    assert "imported.pdf" in app.status_label.cget("text")


def test_import_can_merge_into_the_open_document(editor, powerpoint_deck, tmp_path, monkeypatch):
    """The converted pages can be appended to the document being edited."""
    pytest.importorskip("reportlab")
    before = editor.session.page_count
    target = tmp_path / "deck.pdf"
    monkeypatch.setattr(
        app_module,
        "ImportDialog",
        _stub_import_dialog(
            {
                "destination": target,
                "backend": "builtin",
                "after": app_module.ImportDialog.AFTER_MERGE,
            }
        ),
    )

    editor.import_document(powerpoint_deck)
    pump(editor, 14)

    assert target.exists()
    assert editor.session.page_count == before + 2
    assert "Deck Title" in editor.session.page(before).get_text("text")


def test_import_rejects_other_files(editor, tmp_path, monkeypatch):
    """A file that is not an Office document is refused."""
    seen = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda title, msg, **k: seen.append(msg))
    other = tmp_path / "notes.txt"
    other.write_text("hello")

    editor.import_document(other)
    pump(editor)
    assert seen
    assert "not a Word, Excel or PowerPoint" in seen[0]


def test_import_can_be_cancelled(editor, excel_workbook, monkeypatch):
    """Cancelling the dialog converts nothing."""
    monkeypatch.setattr(app_module, "ImportDialog", _stub_import_dialog(None))
    editor.import_document(excel_workbook)
    pump(editor)
    assert not excel_workbook.with_suffix(".pdf").exists()


def test_dropping_an_office_document_imports_it(app, excel_workbook, tmp_path, monkeypatch):
    """Dropping a workbook on the window starts the conversion."""
    pytest.importorskip("reportlab")
    target = tmp_path / "dropped.pdf"
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_module.messagebox, "askyesnocancel", lambda *a, **k: True)
    monkeypatch.setattr(
        app_module,
        "ImportDialog",
        _stub_import_dialog(
            {
                "destination": target,
                "backend": "builtin",
                "after": app_module.ImportDialog.AFTER_OPEN,
            }
        ),
    )

    app.session = None
    app.handle_dropped_files([excel_workbook])
    pump(app, 14)

    assert target.exists()
    assert app.session is not None
    assert "Sales" in app.session.page(0).get_text("text")
