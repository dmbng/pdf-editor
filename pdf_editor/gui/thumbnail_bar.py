"""
Page thumbnail sidebar.

Shows one preview per page, supports single and multi selection, and offers the
page management commands (delete, duplicate, insert, rotate, move, extract) in a
right-click menu.  Thumbnails are rendered in small batches on the Tk idle loop so
opening a large document never freezes the window.
"""

from __future__ import annotations

import tkinter as tk
from typing import Dict, List, Optional

import customtkinter as ctk
from PIL import Image

# Width of a thumbnail bitmap in pixels.
THUMBNAIL_WIDTH = 132
# Number of thumbnails rendered per idle slice.
BATCH_SIZE = 4


class ThumbnailBar(ctk.CTkScrollableFrame):
    """
    Scrollable list of page previews.

    :param master: Parent widget.
    :param controller: Object receiving page commands (the main window).
    """

    def __init__(self, master, controller) -> None:
        super().__init__(master, width=176, corner_radius=0, label_text="Pages")
        self.controller = controller
        self.session = None
        self.current_index = 0
        self.selected: List[int] = []

        self._cards: Dict[int, ctk.CTkFrame] = {}
        self._labels: Dict[int, ctk.CTkLabel] = {}
        self._images: Dict[int, ctk.CTkImage] = {}
        self._render_job: Optional[str] = None

        self.menu = self._build_menu()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_menu(self) -> tk.Menu:
        """Creates the right-click menu for page operations."""
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Duplicate page", command=lambda: self.controller.duplicate_pages())
        menu.add_command(label="Delete page", command=lambda: self.controller.delete_pages())
        menu.add_separator()
        menu.add_command(label="Insert blank page before", command=lambda: self.controller.add_blank_page("before"))
        menu.add_command(label="Insert blank page after", command=lambda: self.controller.add_blank_page("after"))
        menu.add_separator()
        menu.add_command(label="Rotate left", command=lambda: self.controller.rotate_pages(-90))
        menu.add_command(label="Rotate right", command=lambda: self.controller.rotate_pages(90))
        menu.add_separator()
        menu.add_command(label="Move up", command=lambda: self.controller.move_page(-1))
        menu.add_command(label="Move down", command=lambda: self.controller.move_page(1))
        menu.add_separator()
        menu.add_command(label="Extract to new PDF...", command=lambda: self.controller.extract_pages())
        return menu

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def attach(self, session) -> None:
        """
        Binds the sidebar to a document session.

        :param session: The open :class:`~pdf_editor.core.session.EditSession`.
        """
        self.session = session
        self.selected = []
        self.current_index = 0  # A newly opened document always starts at page 1.
        self.refresh()

    def detach(self) -> None:
        """Empties the sidebar."""
        self.session = None
        self.selected = []
        self._clear_cards()

    @property
    def selected_indices(self) -> List[int]:
        """Indices of the highlighted pages, defaulting to the current page."""
        if self.selected:
            return sorted(index for index in self.selected if self._is_valid(index))
        return [self.current_index] if self._is_valid(self.current_index) else []

    def refresh(self, current_index: Optional[int] = None) -> None:
        """
        Rebuilds the whole list (after a structural change).

        :param current_index: Page to mark as current; keeps the old one if omitted.
        """
        if current_index is not None:
            self.current_index = current_index
        self._clear_cards()
        if self.session is None:
            return

        self.current_index = max(0, min(self.current_index, self.session.page_count - 1))
        self.selected = [index for index in self.selected if self._is_valid(index)]

        for index in range(self.session.page_count):
            self._create_card(index)
        self._update_selection_styles()
        self._start_rendering()

    def refresh_page(self, index: int) -> None:
        """
        Re-renders a single thumbnail after the page content changed.

        :param index: 0-based page index.
        """
        if not self._is_valid(index) or index not in self._labels:
            return
        self._render_batch([index], 0)

    def set_current(self, index: int) -> None:
        """
        Marks a page as the current one and scrolls it into view.

        :param index: 0-based page index.
        """
        if not self._is_valid(index):
            return
        self.current_index = index
        self.selected = []
        self._update_selection_styles()
        self._scroll_into_view(index)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _is_valid(self, index: int) -> bool:
        """True when ``index`` addresses an existing page."""
        return self.session is not None and 0 <= index < self.session.page_count

    def _clear_cards(self) -> None:
        """Destroys every thumbnail widget and cancels pending rendering."""
        if self._render_job is not None:
            try:
                self.after_cancel(self._render_job)
            except Exception:
                pass
            self._render_job = None
        for card in self._cards.values():
            card.destroy()
        self._cards.clear()
        self._labels.clear()
        self._images.clear()

    def _create_card(self, index: int) -> None:
        """Creates the placeholder card for one page."""
        card = ctk.CTkFrame(self, corner_radius=6, border_width=2, border_color=("gray75", "gray28"))
        card.pack(fill="x", padx=6, pady=4)

        preview = ctk.CTkLabel(card, text="", width=THUMBNAIL_WIDTH, height=int(THUMBNAIL_WIDTH * 1.414))
        preview.pack(padx=6, pady=(6, 2))

        caption = ctk.CTkLabel(card, text=f"Page {index + 1}", font=ctk.CTkFont(size=11))
        caption.pack(pady=(0, 6))

        for widget in (card, preview, caption):
            widget.bind("<Button-1>", lambda event, i=index: self._on_click(event, i))
            widget.bind("<Control-Button-1>", lambda event, i=index: self._on_ctrl_click(event, i))
            widget.bind("<Shift-Button-1>", lambda event, i=index: self._on_shift_click(event, i))
            widget.bind("<Button-3>", lambda event, i=index: self._on_right_click(event, i))
            widget.bind("<Double-Button-1>", lambda event, i=index: self._on_click(event, i))

        self._cards[index] = card
        self._labels[index] = preview

    def _start_rendering(self) -> None:
        """Kicks off batched thumbnail rendering."""
        pending = sorted(self._labels)
        if pending:
            self._render_job = self.after(10, lambda: self._render_batch(pending, 0))

    def _render_batch(self, indices: List[int], position: int) -> None:
        """Renders a slice of thumbnails and reschedules itself for the rest."""
        self._render_job = None
        if self.session is None:
            return

        for index in indices[position : position + BATCH_SIZE]:
            label = self._labels.get(index)
            if label is None or not self._is_valid(index):
                continue
            try:
                rendered = self.session.thumbnail(index, THUMBNAIL_WIDTH)
                image = Image.frombytes(
                    "RGB", (rendered.width, rendered.height), rendered.samples
                )
                photo = ctk.CTkImage(light_image=image, dark_image=image, size=(rendered.width, rendered.height))
                self._images[index] = photo
                label.configure(image=photo, text="", height=rendered.height)
            except Exception:
                label.configure(text="preview\nunavailable")

        next_position = position + BATCH_SIZE
        if next_position < len(indices):
            self._render_job = self.after(10, lambda: self._render_batch(indices, next_position))

    def _update_selection_styles(self) -> None:
        """Repaints card borders to reflect current and multi selection."""
        for index, card in self._cards.items():
            if index in self.selected:
                card.configure(border_color=("#1f6aa5", "#3b8ed0"))
            elif index == self.current_index:
                card.configure(border_color=("#2fa572", "#2fa572"))
            else:
                card.configure(border_color=("gray75", "gray28"))

    def _scroll_into_view(self, index: int) -> None:
        """Scrolls the sidebar so a card becomes visible."""
        card = self._cards.get(index)
        if card is None or self.session is None or not self.session.page_count:
            return
        try:
            self._parent_canvas.yview_moveto(max(0.0, index / max(1, self.session.page_count)))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _on_click(self, _event, index: int) -> None:
        """Selects a page and shows it in the main view."""
        self.selected = []
        self.current_index = index
        self._update_selection_styles()
        self.controller.goto_page(index)

    def _on_ctrl_click(self, _event, index: int) -> str:
        """Adds or removes a page from the multi selection."""
        if index in self.selected:
            self.selected.remove(index)
        else:
            if self.current_index not in self.selected:
                self.selected.append(self.current_index)
            self.selected.append(index)
        self._update_selection_styles()
        self.controller.set_status(f"{len(self.selected)} page(s) selected")
        return "break"

    def _on_shift_click(self, _event, index: int) -> str:
        """Selects the range between the current page and the clicked one."""
        low, high = sorted((self.current_index, index))
        self.selected = list(range(low, high + 1))
        self._update_selection_styles()
        self.controller.set_status(f"{len(self.selected)} page(s) selected")
        return "break"

    def _on_right_click(self, event, index: int) -> None:
        """Opens the page context menu."""
        if index not in self.selected:
            self.selected = []
            self.current_index = index
            self._update_selection_styles()
            self.controller.goto_page(index)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()


__all__ = ["THUMBNAIL_WIDTH", "ThumbnailBar"]
