"""
The interactive page view.

``PageCanvas`` renders one page of the open document and turns mouse and keyboard
input into editing intents.  It deliberately performs no document modification
itself: every action is forwarded to the controller (the main window), which wraps
it in an undoable :meth:`pdf_editor.core.session.EditSession.edit` block.

Coordinate systems
------------------
* **PDF space** - unrotated page coordinates; everything in the core modules uses
  these.
* **Display space** - PDF space with the page's ``/Rotate`` value applied.
* **Canvas space** - display space multiplied by the zoom factor plus the offset
  that centres the page inside the widget.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import customtkinter as ctk
import pymupdf as fitz
from PIL import Image, ImageTk

from pdf_editor.core import image_ops, text_ops
from pdf_editor.core.image_ops import ImageRef, PendingImage
from pdf_editor.core.text_ops import TextSelection

# Editing tools offered by the toolbar.
MODE_TEXT = "text"
MODE_HIGHLIGHT = "highlight"
MODE_IMAGE = "image"
MODE_ADD_TEXT = "addtext"

# Zoom limits and step.
MIN_ZOOM = 0.25
MAX_ZOOM = 5.0
ZOOM_STEP = 1.25

# Size of the square resize grips, in screen pixels.
HANDLE_SIZE = 9

# Names and relative positions of the eight resize grips.
_HANDLES: Dict[str, Tuple[float, float]] = {
    "nw": (0.0, 0.0),
    "n": (0.5, 0.0),
    "ne": (1.0, 0.0),
    "e": (1.0, 0.5),
    "se": (1.0, 1.0),
    "s": (0.5, 1.0),
    "sw": (0.0, 1.0),
    "w": (0.0, 0.5),
}

_CURSORS = {
    "nw": "size_nw_se",
    "se": "size_nw_se",
    "ne": "size_ne_sw",
    "sw": "size_ne_sw",
    "n": "sb_v_double_arrow",
    "s": "sb_v_double_arrow",
    "e": "sb_h_double_arrow",
    "w": "sb_h_double_arrow",
}


@dataclass
class _Drag:
    """Mutable state of an in-progress mouse drag."""

    kind: str
    start: Tuple[float, float]
    origin_rect: Optional[fitz.Rect] = None
    handle: Optional[str] = None
    offset: Tuple[float, float] = (0.0, 0.0)
    moved: bool = False


class PageCanvas(ctk.CTkFrame):
    """
    Scrollable, zoomable view of a single PDF page with editing interaction.

    :param master: Parent widget.
    :param controller: Object receiving the editing intents (the main window).
    """

    def __init__(self, master, controller) -> None:
        super().__init__(master, corner_radius=0, fg_color=("gray82", "gray17"))
        self.controller = controller

        self.session = None
        self.page_index = 0
        self.zoom = 1.35
        self.mode = MODE_TEXT

        self.selection: Optional[TextSelection] = None
        self.pending_image: Optional[PendingImage] = None
        self.selected_image: Optional[ImageRef] = None
        self.image_rect: Optional[fitz.Rect] = None  # Live rect of the selected image.

        self._photo: Optional[ImageTk.PhotoImage] = None
        self._pending_photo: Optional[ImageTk.PhotoImage] = None
        self._pending_source: Optional[Image.Image] = None
        self._offset = (0.0, 0.0)
        self._page_size = (0.0, 0.0)
        self._words_cache: Optional[List[text_ops.Word]] = None
        self._drag: Optional[_Drag] = None
        self._handle_boxes: Dict[str, Tuple[float, float, float, float]] = {}
        self._inline_editor: Optional[tk.Entry] = None
        self._inline_window: Optional[int] = None

        self._build_widgets()
        self._bind_events()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        """Creates the canvas and its scrollbars."""
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            background="#3a3d42",
            highlightthickness=0,
            takefocus=True,
            cursor="arrow",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.vbar = ctk.CTkScrollbar(self, orientation="vertical", command=self.canvas.yview)
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar = ctk.CTkScrollbar(self, orientation="horizontal", command=self.canvas.xview)
        self.hbar.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)

    def _bind_events(self) -> None:
        """Wires mouse and keyboard events."""
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Triple-Button-1>", self._on_triple_click)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self.canvas.bind("<Configure>", lambda _event: self._reposition())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)

        self.canvas.bind("<BackSpace>", self._on_erase_key)
        self.canvas.bind("<Delete>", self._on_erase_key)
        self.canvas.bind("<Return>", self._on_return_key)
        self.canvas.bind("<Escape>", self._on_escape_key)
        self.canvas.bind("<Key>", self._on_key)

    # ------------------------------------------------------------------
    # Document / view state
    # ------------------------------------------------------------------
    def attach(self, session, page_index: int = 0) -> None:
        """
        Shows a document in the view.

        :param session: The :class:`~pdf_editor.core.session.EditSession` to show.
        :param page_index: Page to display first.
        """
        self.session = session
        self.page_index = page_index
        self.clear_selection(notify=False)
        self.cancel_pending_image(notify=False)
        self.render()

    def detach(self) -> None:
        """Clears the view (used when the document is closed)."""
        self.session = None
        self.selection = None
        self.pending_image = None
        self.selected_image = None
        self._photo = None
        self.canvas.delete("all")

    def show_page(self, index: int) -> None:
        """
        Displays another page.

        :param index: 0-based page index; values outside the document are clamped.
        """
        if self.session is None:
            return
        index = max(0, min(index, self.session.page_count - 1))
        if index != self.page_index:
            self.commit_pending_image()
            self.clear_selection(notify=True)
            self.selected_image = None
        self.page_index = index
        self.render()
        self.canvas.yview_moveto(0.0)

    def set_mode(self, mode: str) -> None:
        """
        Switches the active tool.

        :param mode: One of the ``MODE_*`` constants.
        """
        if mode == self.mode:
            return
        self.mode = mode
        self.clear_selection(notify=True)
        if mode != MODE_IMAGE:
            self.selected_image = None
        self._draw_overlays()

    @property
    def page(self) -> Optional[fitz.Page]:
        """The page currently displayed, or ``None``."""
        if self.session is None:
            return None
        try:
            return self.session.page(self.page_index)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------
    def set_zoom(self, value: float) -> None:
        """
        Sets the zoom factor.

        :param value: Scale where 1.0 renders the page at 72 dpi.
        """
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(value)))
        self.render()

    def zoom_in(self) -> None:
        """Zooms in by one step."""
        self.set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        """Zooms out by one step."""
        self.set_zoom(self.zoom / ZOOM_STEP)

    def fit_width(self) -> None:
        """Scales the page so its width fills the view."""
        page = self.page
        if page is None:
            return
        available = max(80, self.canvas.winfo_width() - 28)
        display_width = page.rect.width if page.rotation % 180 == 0 else page.rect.height
        if display_width:
            self.set_zoom(available / display_width)

    def fit_page(self) -> None:
        """Scales the page so the whole page is visible."""
        page = self.page
        if page is None:
            return
        available_w = max(80, self.canvas.winfo_width() - 28)
        available_h = max(80, self.canvas.winfo_height() - 28)
        if page.rotation % 180 == 0:
            width, height = page.rect.width, page.rect.height
        else:
            width, height = page.rect.height, page.rect.width
        if width and height:
            self.set_zoom(min(available_w / width, available_h / height))

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------
    def to_pdf_point(self, canvas_x: float, canvas_y: float) -> fitz.Point:
        """
        Converts canvas coordinates into unrotated PDF coordinates.

        :param canvas_x: X coordinate inside the canvas (already scrolled).
        :param canvas_y: Y coordinate inside the canvas.
        :return: The corresponding point in PDF space.
        """
        page = self.page
        offset_x, offset_y = self._offset
        display = fitz.Point((canvas_x - offset_x) / self.zoom, (canvas_y - offset_y) / self.zoom)
        if page is not None and page.rotation:
            return display * page.derotation_matrix
        return display

    def to_canvas_rect(self, rect: fitz.Rect) -> Tuple[float, float, float, float]:
        """
        Converts a PDF rectangle into canvas coordinates.

        :param rect: Rectangle in unrotated PDF space.
        :return: ``(x0, y0, x1, y1)`` in canvas space.
        """
        page = self.page
        box = fitz.Rect(rect)
        if page is not None and page.rotation:
            box = box * page.rotation_matrix
        box.normalize()
        offset_x, offset_y = self._offset
        return (
            box.x0 * self.zoom + offset_x,
            box.y0 * self.zoom + offset_y,
            box.x1 * self.zoom + offset_x,
            box.y1 * self.zoom + offset_y,
        )

    def _canvas_event_point(self, event) -> Tuple[float, float]:
        """Returns the scrolled canvas coordinates of a mouse event."""
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self) -> None:
        """Rasterises the current page and redraws every overlay."""
        self._close_inline_editor(apply_text=False)
        if self.session is None:
            self.canvas.delete("all")
            return

        try:
            rendered = self.session.render(self.page_index, self.zoom)
        except Exception as error:
            self.canvas.delete("all")
            self.controller.set_status(f"Could not render page: {error}", error=True)
            return

        image = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.samples)
        self._photo = ImageTk.PhotoImage(image)
        self._page_size = (rendered.width, rendered.height)
        self._words_cache = None

        self.canvas.delete("all")
        self._reposition(redraw=False)
        self.canvas.create_image(
            self._offset[0], self._offset[1], anchor="nw", image=self._photo, tags="page"
        )
        self._draw_overlays()

    def _reposition(self, redraw: bool = True) -> None:
        """Centres the page bitmap inside the canvas and updates the scroll region."""
        if self._photo is None:
            return
        width, height = self._page_size
        view_w = max(self.canvas.winfo_width(), 1)
        view_h = max(self.canvas.winfo_height(), 1)
        offset_x = max(14.0, (view_w - width) / 2.0)
        offset_y = 14.0
        if offset_x != self._offset[0] or offset_y != self._offset[1]:
            self._offset = (offset_x, offset_y)
            redraw = True
        else:
            self._offset = (offset_x, offset_y)

        self.canvas.configure(
            scrollregion=(0, 0, max(view_w, width + 2 * offset_x), height + 2 * offset_y)
        )
        if redraw and self._photo is not None:
            self.canvas.delete("page")
            self.canvas.create_image(offset_x, offset_y, anchor="nw", image=self._photo, tags="page")
            self.canvas.tag_lower("page")
            self._draw_overlays()

    def _draw_overlays(self) -> None:
        """Redraws selection highlights, image frames and resize grips."""
        self.canvas.delete("overlay")
        self._handle_boxes.clear()

        if self.selection is not None and not self.selection.is_empty:
            color = "#f5c518" if self.mode == MODE_HIGHLIGHT else "#4a90e2"
            for rect in self.selection.rects:
                x0, y0, x1, y1 = self.to_canvas_rect(rect)
                self.canvas.create_rectangle(
                    x0, y0, x1, y1,
                    fill=color, stipple="gray50", outline=color, width=1, tags="overlay",
                )

        if self.pending_image is not None:
            self._draw_pending_image()
        elif self.selected_image is not None and self.image_rect is not None:
            x0, y0, x1, y1 = self.to_canvas_rect(self.image_rect)
            self.canvas.create_rectangle(
                x0, y0, x1, y1, outline="#00bcd4", width=2, dash=(5, 3), tags="overlay"
            )
            self._draw_handles(x0, y0, x1, y1)

    def _draw_pending_image(self) -> None:
        """Draws the preview of an image that is not committed yet."""
        pending = self.pending_image
        if pending is None:
            return
        x0, y0, x1, y1 = self.to_canvas_rect(pending.rect)
        width = max(1, int(x1 - x0))
        height = max(1, int(y1 - y0))

        if self._pending_source is not None:
            resample = Image.NEAREST if self._drag is not None else Image.LANCZOS
            preview = self._pending_source.resize((width, height), resample)
            self._pending_photo = ImageTk.PhotoImage(preview)
            self.canvas.create_image(x0, y0, anchor="nw", image=self._pending_photo, tags="overlay")

        self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#00bcd4", width=2, dash=(5, 3), tags="overlay"
        )
        self._draw_handles(x0, y0, x1, y1)

    def _draw_handles(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Draws the eight resize grips of a frame and records their hit boxes."""
        half = HANDLE_SIZE / 2.0
        for name, (fx, fy) in _HANDLES.items():
            cx = x0 + (x1 - x0) * fx
            cy = y0 + (y1 - y0) * fy
            box = (cx - half, cy - half, cx + half, cy + half)
            self._handle_boxes[name] = box
            self.canvas.create_rectangle(
                *box, fill="#ffffff", outline="#00bcd4", width=1, tags="overlay"
            )

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------
    def _words(self) -> List[text_ops.Word]:
        """Returns the cached word list of the current page."""
        if self._words_cache is None:
            page = self.page
            self._words_cache = text_ops.page_words(page) if page is not None else []
        return self._words_cache

    def set_selection(self, selection: Optional[TextSelection], notify: bool = True) -> None:
        """
        Replaces the current text selection.

        :param selection: New selection, or ``None`` to clear.
        :param notify: Inform the controller about the change.
        """
        self.selection = selection
        self._draw_overlays()
        if notify:
            self.controller.on_selection_changed(selection)

    def clear_selection(self, notify: bool = True) -> None:
        """
        Clears the text selection.

        :param notify: Inform the controller about the change.
        """
        self._close_inline_editor(apply_text=False)
        if self.selection is not None or notify:
            self.set_selection(None, notify=notify)

    def _select_rect(self, rect: fitz.Rect) -> None:
        """Selects every word intersecting a rubber-band rectangle."""
        page = self.page
        if page is None:
            return
        box = fitz.Rect(rect)
        box.normalize()
        words = [word for word in self._words() if box.intersects(word.rect)]
        self.set_selection(TextSelection(self.page_index, words), notify=True)

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------
    def begin_image_placement(self, path) -> None:
        """
        Starts placing an image on the current page.

        :param path: Image file to insert.
        :raises pdf_editor.core.utils.ImageOperationError: For unreadable images.
        """
        page = self.page
        if page is None:
            return
        self.commit_pending_image()
        info = image_ops.read_image_info(path)
        rect = image_ops.default_placement(page, info)
        self.pending_image = PendingImage(path=path, rect=rect, info=info)
        self._pending_source = Image.open(path).convert("RGB")
        self.selected_image = None
        self.clear_selection(notify=True)
        self._draw_overlays()
        self.controller.on_image_changed(self.pending_image)

    def cancel_pending_image(self, notify: bool = True) -> None:
        """
        Discards an image placement that was not committed.

        :param notify: Inform the controller about the change.
        """
        self.pending_image = None
        self._pending_source = None
        self._pending_photo = None
        self._draw_overlays()
        if notify:
            self.controller.on_image_changed(None)

    def commit_pending_image(self) -> bool:
        """
        Writes a pending image placement into the document.

        :return: True when an image was committed.
        """
        if self.pending_image is None:
            return False
        pending = self.pending_image
        self.pending_image = None
        self._pending_source = None
        committed = self.controller.commit_image(pending, self.page_index)
        self.controller.on_image_changed(None)
        return committed

    def set_pending_rect(self, rect: fitz.Rect) -> None:
        """
        Applies a rectangle typed into the properties panel.

        :param rect: New placement rectangle in PDF coordinates.
        """
        if self.pending_image is not None:
            self.pending_image.resize_to(rect)
            page = self.page
            if page is not None:
                self.pending_image.clamped(page.rect)
            self._draw_overlays()
            self.controller.on_image_changed(self.pending_image)
        elif self.selected_image is not None:
            self.controller.move_existing_image(self.selected_image, fitz.Rect(rect), self.page_index)

    def select_image_at(self, point: fitz.Point) -> bool:
        """
        Selects the image under a point so it can be moved or resized.

        :param point: Point in PDF coordinates.
        :return: True when an image was found.
        """
        page = self.page
        if page is None:
            return False
        ref = image_ops.find_image_at(page, point)
        self.selected_image = ref
        self.image_rect = fitz.Rect(ref.bbox) if ref else None
        self._draw_overlays()
        self.controller.on_image_changed(ref)
        return ref is not None

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _hit_handle(self, x: float, y: float) -> Optional[str]:
        """Returns the name of the resize grip under a canvas point."""
        for name, (hx0, hy0, hx1, hy1) in self._handle_boxes.items():
            if hx0 - 2 <= x <= hx1 + 2 and hy0 - 2 <= y <= hy1 + 2:
                return name
        return None

    def _active_frame(self) -> Optional[fitz.Rect]:
        """Rectangle of whichever image frame is currently active."""
        if self.pending_image is not None:
            return self.pending_image.rect
        if self.selected_image is not None:
            return self.image_rect
        return None

    def _on_press(self, event) -> None:
        """Starts a drag: move, resize, rubber-band selection or text box."""
        self.canvas.focus_set()
        self._close_inline_editor(apply_text=True)
        if self.session is None:
            return

        x, y = self._canvas_event_point(event)
        pdf_point = self.to_pdf_point(x, y)
        frame = self._active_frame()

        if frame is not None:
            handle = self._hit_handle(x, y)
            if handle:
                self._drag = _Drag(kind="resize", start=(x, y), origin_rect=fitz.Rect(frame), handle=handle)
                return
            if frame.contains(pdf_point):
                self._drag = _Drag(
                    kind="move",
                    start=(x, y),
                    origin_rect=fitz.Rect(frame),
                    offset=(pdf_point.x - frame.x0, pdf_point.y - frame.y0),
                )
                return
            # Clicking outside the frame drops the current image selection.
            if self.pending_image is not None:
                self.commit_pending_image()
            else:
                self.selected_image = None
                self.image_rect = None
                self.controller.on_image_changed(None)
                self._draw_overlays()

        if self.mode == MODE_IMAGE:
            if self.select_image_at(pdf_point):
                return
            self._drag = _Drag(kind="none", start=(x, y))
            return

        if self.mode == MODE_ADD_TEXT:
            self._drag = _Drag(kind="addtext", start=(x, y))
            return

        self.clear_selection(notify=True)
        self._drag = _Drag(kind="select", start=(x, y))

    def _on_motion(self, event) -> None:
        """Updates the drag in progress."""
        if self._drag is None or self.session is None:
            return
        x, y = self._canvas_event_point(event)
        start_x, start_y = self._drag.start
        if abs(x - start_x) > 2 or abs(y - start_y) > 2:
            self._drag.moved = True

        if self._drag.kind == "select":
            start = self.to_pdf_point(start_x, start_y)
            current = self.to_pdf_point(x, y)
            self._select_rect(fitz.Rect(start, current))
            self.canvas.delete("band")
            self.canvas.create_rectangle(
                start_x, start_y, x, y, outline="#4a90e2", dash=(3, 2), tags=("overlay", "band")
            )
            return

        if self._drag.kind == "addtext":
            self.canvas.delete("band")
            self.canvas.create_rectangle(
                start_x, start_y, x, y, outline="#7c4dff", width=2, dash=(4, 2), tags=("overlay", "band")
            )
            return

        if self._drag.kind in ("move", "resize"):
            self._update_frame_drag(x, y)

    def _update_frame_drag(self, x: float, y: float) -> None:
        """Recomputes the frame rectangle while moving or resizing an image."""
        drag = self._drag
        if drag is None or drag.origin_rect is None:
            return
        point = self.to_pdf_point(x, y)
        origin = drag.origin_rect

        if drag.kind == "move":
            new_rect = fitz.Rect(
                point.x - drag.offset[0],
                point.y - drag.offset[1],
                point.x - drag.offset[0] + origin.width,
                point.y - drag.offset[1] + origin.height,
            )
        else:
            new_rect = fitz.Rect(origin)
            handle = drag.handle or "se"
            if "n" in handle:
                new_rect.y0 = min(point.y, origin.y1 - image_ops.MIN_IMAGE_SIZE)
            if "s" in handle:
                new_rect.y1 = max(point.y, origin.y0 + image_ops.MIN_IMAGE_SIZE)
            if "w" in handle:
                new_rect.x0 = min(point.x, origin.x1 - image_ops.MIN_IMAGE_SIZE)
            if "e" in handle:
                new_rect.x1 = max(point.x, origin.x0 + image_ops.MIN_IMAGE_SIZE)

        if self.pending_image is not None:
            if drag.kind == "resize":
                anchor = (
                    origin.x1 if "w" in (drag.handle or "") else origin.x0,
                    origin.y1 if "n" in (drag.handle or "") else origin.y0,
                )
                self.pending_image.resize_to(new_rect, anchor=anchor)
            else:
                self.pending_image.rect = new_rect
            page = self.page
            if page is not None:
                self.pending_image.clamped(page.rect)
            self.controller.on_image_changed(self.pending_image)
        else:
            self.image_rect = new_rect
            self.controller.on_image_changed(self.selected_image, rect=new_rect)
        self._draw_overlays()

    def _on_release(self, event) -> None:
        """Finishes the current drag and applies its effect."""
        drag = self._drag
        self._drag = None
        self.canvas.delete("band")
        if drag is None or self.session is None:
            return

        x, y = self._canvas_event_point(event)

        if drag.kind == "select":
            if not drag.moved:
                self.clear_selection(notify=True)
            elif self.mode == MODE_HIGHLIGHT and self.selection and not self.selection.is_empty:
                self.controller.highlight_selection()
            return

        if drag.kind == "addtext":
            start = self.to_pdf_point(*drag.start)
            end = self.to_pdf_point(x, y)
            rect = fitz.Rect(start, end)
            rect.normalize()
            if rect.width < 24 or rect.height < 14:
                rect = fitz.Rect(start.x, start.y, start.x + 220, start.y + 60)
            self.controller.request_text_box(rect, self.page_index)
            return

        if drag.kind in ("move", "resize") and drag.moved:
            if self.pending_image is not None:
                self.controller.on_image_changed(self.pending_image)
            elif self.selected_image is not None and self.image_rect is not None:
                self.controller.move_existing_image(
                    self.selected_image, fitz.Rect(self.image_rect), self.page_index
                )

    def _on_hover(self, event) -> None:
        """Updates the mouse cursor so grips and frames feel clickable."""
        if self.session is None:
            return
        x, y = self._canvas_event_point(event)
        handle = self._hit_handle(x, y) if self._handle_boxes else None
        if handle:
            self.canvas.configure(cursor=_CURSORS.get(handle, "fleur"))
            return

        frame = self._active_frame()
        if frame is not None and frame.contains(self.to_pdf_point(x, y)):
            self.canvas.configure(cursor="fleur")
        elif self.mode in (MODE_TEXT, MODE_HIGHLIGHT):
            self.canvas.configure(cursor="xterm")
        elif self.mode == MODE_ADD_TEXT:
            self.canvas.configure(cursor="crosshair")
        else:
            self.canvas.configure(cursor="arrow")

    def _on_double_click(self, event) -> None:
        """Selects the word under the cursor."""
        page = self.page
        if page is None or self.mode not in (MODE_TEXT, MODE_HIGHLIGHT):
            return
        point = self.to_pdf_point(*self._canvas_event_point(event))
        for word in self._words():
            if word.rect.contains(point):
                self.set_selection(TextSelection(self.page_index, [word]))
                return

    def _on_triple_click(self, event) -> None:
        """Selects the whole line under the cursor."""
        page = self.page
        if page is None or self.mode not in (MODE_TEXT, MODE_HIGHLIGHT):
            return
        point = self.to_pdf_point(*self._canvas_event_point(event))
        for word in self._words():
            if word.rect.contains(point):
                line = [
                    other
                    for other in self._words()
                    if other.block == word.block and other.line == word.line
                ]
                self.set_selection(TextSelection(self.page_index, line))
                return

    def _on_right_click(self, event) -> None:
        """Opens the page context menu."""
        self.canvas.focus_set()
        point = self.to_pdf_point(*self._canvas_event_point(event))
        self.controller.show_canvas_menu(event, point)

    # ------------------------------------------------------------------
    # Wheel handling
    # ------------------------------------------------------------------
    def _on_wheel(self, event) -> None:
        """Scrolls vertically, or moves to the next page at the end of the view."""
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_shift_wheel(self, event) -> None:
        """Scrolls horizontally."""
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_ctrl_wheel(self, event) -> None:
        """Zooms in and out around the current view."""
        self.zoom_in() if event.delta > 0 else self.zoom_out()
        self.controller.refresh_zoom_label()

    # ------------------------------------------------------------------
    # Keyboard handling
    # ------------------------------------------------------------------
    def _on_erase_key(self, _event) -> str:
        """Handles Backspace/Delete on a selection."""
        if self.selection is not None and not self.selection.is_empty:
            self.controller.delete_selected_text()
            return "break"
        if self.pending_image is not None:
            self.cancel_pending_image()
            return "break"
        if self.selected_image is not None:
            self.controller.delete_selected_image()
            return "break"
        return ""

    def _on_return_key(self, _event) -> str:
        """Opens the inline editor for the current selection."""
        if self.selection is not None and not self.selection.is_empty:
            self.start_inline_edit(self.selection.text)
            return "break"
        if self.pending_image is not None:
            self.commit_pending_image()
            return "break"
        return ""

    def _on_escape_key(self, _event) -> str:
        """Cancels the current selection, placement or inline edit."""
        if self._inline_editor is not None:
            self._close_inline_editor(apply_text=False)
        elif self.pending_image is not None:
            self.cancel_pending_image()
        elif self.selected_image is not None:
            self.selected_image = None
            self.image_rect = None
            self.controller.on_image_changed(None)
            self._draw_overlays()
        else:
            self.clear_selection(notify=True)
        return "break"

    def _on_key(self, event) -> Optional[str]:
        """Starts inline editing as soon as the user types over a selection."""
        if not event.char or not event.char.isprintable():
            return None
        if event.state & 0x0004:  # Control held: leave it to the accelerators.
            return None
        if self.selection is None or self.selection.is_empty:
            return None
        self.start_inline_edit(event.char)
        return "break"

    # ------------------------------------------------------------------
    # Inline text editor
    # ------------------------------------------------------------------
    def start_inline_edit(self, initial_text: str = "") -> None:
        """
        Shows a one-line editor on top of the selection.

        :param initial_text: Text the editor starts with.
        """
        if self.selection is None or self.selection.is_empty:
            return
        self._close_inline_editor(apply_text=False)

        bbox = self.selection.bbox
        x0, y0, x1, y1 = self.to_canvas_rect(bbox)
        width = max(160, int(x1 - x0) + 40)
        height = max(24, int(y1 - y0) + 8)

        editor = tk.Entry(
            self.canvas,
            font=("Segoe UI", max(9, int(11 * min(self.zoom, 1.6)))),
            relief="solid",
            borderwidth=1,
            background="#fffbe6",
            foreground="#111111",
            insertbackground="#111111",
        )
        editor.insert(0, initial_text)
        editor.selection_range(0, tk.END)
        editor.icursor(tk.END)
        editor.bind("<Return>", lambda _event: self._close_inline_editor(apply_text=True))
        editor.bind("<Escape>", lambda _event: self._close_inline_editor(apply_text=False))
        editor.bind("<FocusOut>", lambda _event: self._close_inline_editor(apply_text=False))

        self._inline_editor = editor
        self._inline_window = self.canvas.create_window(
            x0 - 2, y0 - 3, anchor="nw", window=editor, width=width, height=height, tags="overlay"
        )
        editor.focus_set()

    def _close_inline_editor(self, apply_text: bool) -> str:
        """
        Closes the inline editor, optionally applying the typed text.

        :param apply_text: Apply the text as a replacement of the selection.
        :return: ``"break"`` so Tk stops processing the originating event.
        """
        editor, window = self._inline_editor, self._inline_window
        self._inline_editor = None
        self._inline_window = None
        if editor is None:
            return "break"

        text = editor.get()
        try:
            if window is not None:
                self.canvas.delete(window)
            editor.destroy()
        except Exception:
            pass

        if apply_text:
            self.controller.replace_selected_text(text)
        self.canvas.focus_set()
        return "break"


__all__ = [
    "MAX_ZOOM",
    "MIN_ZOOM",
    "MODE_ADD_TEXT",
    "MODE_HIGHLIGHT",
    "MODE_IMAGE",
    "MODE_TEXT",
    "PageCanvas",
]
