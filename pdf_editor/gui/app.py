"""
Main window of the PDF editor.

The window owns the :class:`~pdf_editor.core.session.EditSession` and acts as the
controller for the page canvas and the thumbnail sidebar: those widgets report
what the user wants to do, and every document change goes through :meth:`_apply`,
which wraps it into one undoable step and refreshes the views.
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, List, Optional, Sequence

import customtkinter as ctk
import pymupdf as fitz

from pdf_editor.core import (
    compress,
    doc_import,
    image_ops,
    page_ops,
    text_ops,
    word_export,
)
from pdf_editor.core.fonts import SpanStyle
from pdf_editor.core.image_ops import ImageRef, PendingImage
from pdf_editor.core.session import EditSession
from pdf_editor.core.utils import EncryptedPDFError, PDFEditorError
from pdf_editor.gui import dnd
from pdf_editor.gui.dialogs import (
    DOCX_FILETYPES,
    PDF_FILETYPES,
    BlankPageDialog,
    CompressDialog,
    ImportDialog,
    MergeDialog,
    PasswordDialog,
    ShortcutsDialog,
    TaskDialog,
    TextBoxDialog,
    WordExportDialog,
)
from pdf_editor.gui.page_canvas import (
    MODE_ADD_TEXT,
    MODE_HIGHLIGHT,
    MODE_IMAGE,
    MODE_TEXT,
    PageCanvas,
)
from pdf_editor.gui.thumbnail_bar import ThumbnailBar

APP_NAME = "PDF Editor Studio"

IMPORT_FILETYPES = [
    ("Word, Excel and PowerPoint", "*.docx *.doc *.docm *.rtf *.odt *.xlsx *.xls *.xlsm *.ods *.csv *.pptx *.ppt *.pptm *.odp"),
    ("Word documents", "*.docx *.doc *.docm *.rtf *.odt"),
    ("Excel workbooks", "*.xlsx *.xls *.xlsm *.ods *.csv"),
    ("PowerPoint presentations", "*.pptx *.ppt *.pptm *.odp"),
    ("All files", "*.*"),
]

IMAGE_FILETYPES = [
    ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp"),
    ("PNG images", "*.png"),
    ("JPEG images", "*.jpg *.jpeg"),
    ("All files", "*.*"),
]

# Tool names shown in the toolbar, mapped to canvas modes.
TOOL_MODES = {
    "Select & Edit Text": MODE_TEXT,
    "Highlight": MODE_HIGHLIGHT,
    "Insert Image": MODE_IMAGE,
    "Add Text Box": MODE_ADD_TEXT,
}

HIGHLIGHT_COLOURS = {
    "Yellow": (1.0, 0.92, 0.23),
    "Green": (0.65, 0.93, 0.55),
    "Blue": (0.60, 0.83, 1.0),
    "Pink": (1.0, 0.68, 0.85),
}


class PDFEditorStudio(dnd.DragDropCTk):
    """
    The editor window.

    :param initial_pdf: Optional PDF opened at start-up.
    """

    def __init__(self, initial_pdf: Optional[str] = None) -> None:
        super().__init__()
        self.session: Optional[EditSession] = None
        self.startup_notes: List[str] = []

        self.title(APP_NAME)
        self.geometry("1380x880")
        self.minsize(1080, 700)

        self._build_menu()
        self._build_toolbars()
        self._build_body()
        self._build_status_bar()
        self._bind_shortcuts()
        self._setup_drag_and_drop()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._update_controls()

        if initial_pdf:
            self.after(120, lambda: self.open_document(Path(initial_pdf)))

    # ==================================================================
    # Construction
    # ==================================================================
    def _build_menu(self) -> None:
        """Creates the classic menu bar."""
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_document)
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_document)
        file_menu.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_document_as)
        file_menu.add_separator()
        file_menu.add_command(
            label="Import document (Word, Excel, PowerPoint)...",
            accelerator="Ctrl+I",
            command=self.import_document,
        )
        file_menu.add_separator()
        file_menu.add_command(label="Merge PDFs...", accelerator="Ctrl+M", command=self.merge_pdfs)
        file_menu.add_command(label="Extract selected pages...", command=self.extract_pages)
        file_menu.add_separator()
        file_menu.add_command(label="Export as Word (.docx)...", command=self.export_as_word)
        file_menu.add_command(label="Compress PDF...", command=self.compress_document)
        file_menu.add_separator()
        file_menu.add_command(label="Close document", command=self.close_document)
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="Replace highlighted text", command=self.apply_replacement_from_panel)
        edit_menu.add_command(label="Delete highlighted text", command=self.delete_selected_text)
        edit_menu.add_command(label="Highlight selection", command=self.highlight_selection)
        edit_menu.add_command(label="Redact selection", command=self.redact_selection)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        page_menu = tk.Menu(menubar, tearoff=0)
        page_menu.add_command(label="Add blank page...", command=lambda: self.add_blank_page("dialog"))
        page_menu.add_command(label="Duplicate page", command=self.duplicate_pages)
        page_menu.add_command(label="Delete page", command=self.delete_pages)
        page_menu.add_separator()
        page_menu.add_command(label="Rotate left", command=lambda: self.rotate_pages(-90))
        page_menu.add_command(label="Rotate right", command=lambda: self.rotate_pages(90))
        page_menu.add_separator()
        page_menu.add_command(label="Move page up", command=lambda: self.move_page(-1))
        page_menu.add_command(label="Move page down", command=lambda: self.move_page(1))
        menubar.add_cascade(label="Page", menu=page_menu)

        insert_menu = tk.Menu(menubar, tearoff=0)
        insert_menu.add_command(label="Image...", command=self.insert_image)
        insert_menu.add_command(label="Text box...", command=self.insert_text_box)
        menubar.add_cascade(label="Insert", menu=insert_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Zoom in", accelerator="Ctrl++", command=self.zoom_in)
        view_menu.add_command(label="Zoom out", accelerator="Ctrl+-", command=self.zoom_out)
        view_menu.add_command(label="Fit width", command=self.fit_width)
        view_menu.add_command(label="Fit page", command=self.fit_page)
        view_menu.add_separator()
        appearance = tk.Menu(view_menu, tearoff=0)
        for mode in ("System", "Light", "Dark"):
            appearance.add_command(label=mode, command=lambda m=mode: self.set_appearance(m))
        view_menu.add_cascade(label="Appearance", menu=appearance)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts", accelerator="F1", command=self.show_shortcuts)
        help_menu.add_command(label="Document properties", command=self.show_document_properties)
        menubar.add_cascade(label="Help", menu=help_menu)

        try:
            self.configure(menu=menubar)
        except Exception:
            # Some themes reject the option; the toolbars cover every command.
            self.startup_notes.append("The menu bar could not be created; use the toolbar buttons.")

    def _build_toolbars(self) -> None:
        """Creates the two toolbar rows."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, corner_radius=0, height=46)
        top.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(top, text="Open", width=78, command=self.open_document).pack(side="left", padx=(8, 3), pady=7)
        self.save_button = ctk.CTkButton(
            top, text="Save", width=70, fg_color="#2fa572", hover_color="#26855c", command=self.save_document
        )
        self.save_button.pack(side="left", padx=3)
        ctk.CTkButton(top, text="Save As", width=80, command=self.save_document_as).pack(side="left", padx=3)

        self._separator(top)

        self.undo_button = ctk.CTkButton(top, text="Undo", width=64, command=self.undo)
        self.undo_button.pack(side="left", padx=3)
        self.redo_button = ctk.CTkButton(top, text="Redo", width=64, command=self.redo)
        self.redo_button.pack(side="left", padx=3)

        self._separator(top)

        ctk.CTkButton(top, text="<", width=34, command=lambda: self.step_page(-1)).pack(side="left", padx=2)
        self.page_entry = ctk.CTkEntry(top, width=54, justify="center")
        self.page_entry.pack(side="left", padx=3)
        self.page_entry.bind("<Return>", self._on_page_entry)
        self.page_total_label = ctk.CTkLabel(top, text="of 0", width=44)
        self.page_total_label.pack(side="left")
        ctk.CTkButton(top, text=">", width=34, command=lambda: self.step_page(1)).pack(side="left", padx=2)

        self._separator(top)

        ctk.CTkButton(top, text="-", width=34, command=self.zoom_out).pack(side="left", padx=2)
        self.zoom_label = ctk.CTkLabel(top, text="135%", width=52)
        self.zoom_label.pack(side="left")
        ctk.CTkButton(top, text="+", width=34, command=self.zoom_in).pack(side="left", padx=2)
        ctk.CTkButton(top, text="Fit width", width=76, command=self.fit_width).pack(side="left", padx=3)

        ctk.CTkButton(top, text="Help", width=64, fg_color="gray40", hover_color="gray32",
                      command=self.show_shortcuts).pack(side="right", padx=8)

        tools = ctk.CTkFrame(self, corner_radius=0, height=44)
        tools.grid(row=1, column=0, sticky="ew")

        ctk.CTkLabel(tools, text="Tool:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(10, 6), pady=6)
        self.tool_selector = ctk.CTkSegmentedButton(
            tools, values=list(TOOL_MODES.keys()), command=self._on_tool_change, width=440
        )
        self.tool_selector.set("Select & Edit Text")
        self.tool_selector.pack(side="left", pady=6)

        self._separator(tools)

        ctk.CTkButton(tools, text="Insert Image...", width=112, command=self.insert_image).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="Add Page", width=92,
                      command=lambda: self.add_blank_page("dialog")).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="Duplicate Page", width=112, command=self.duplicate_pages).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="Delete Page", width=98, fg_color="#b4453c", hover_color="#93362f",
                      command=self.delete_pages).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="Merge PDFs", width=98, command=self.merge_pdfs).pack(side="left", padx=3)

        self._separator(tools)

        ctk.CTkButton(tools, text="Import Doc", width=98, fg_color="#1d7a4c", hover_color="#165f3b",
                      command=self.import_document).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="To Word", width=88, fg_color="#2b579a", hover_color="#1f4478",
                      command=self.export_as_word).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="Compress", width=92, fg_color="#6b4fbb", hover_color="#553f96",
                      command=self.compress_document).pack(side="left", padx=3)

    def _separator(self, parent) -> None:
        """Adds a thin vertical separator to a toolbar."""
        ctk.CTkLabel(parent, text="|", text_color="gray50", width=10).pack(side="left", padx=4)

    def _build_body(self) -> None:
        """Creates the sidebar, the page view and the properties panel."""
        body = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self.thumbnails = ThumbnailBar(body, self)
        self.thumbnails.grid(row=0, column=0, sticky="ns", padx=(6, 3), pady=6)

        self.canvas_view = PageCanvas(body, self)
        self.canvas_view.grid(row=0, column=1, sticky="nsew", padx=3, pady=6)

        self.properties = ctk.CTkFrame(body, width=270, corner_radius=8)
        self.properties.grid(row=0, column=2, sticky="ns", padx=(3, 6), pady=6)
        self.properties.grid_propagate(False)
        self._build_text_panel()
        self._build_image_panel()
        self._show_panel(MODE_TEXT)

    def _build_text_panel(self) -> None:
        """Builds the text editing side panel."""
        panel = ctk.CTkFrame(self.properties, fg_color="transparent", width=254)
        self.text_panel = panel

        ctk.CTkLabel(panel, text="Text editing", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(12, 6)
        )

        ctk.CTkLabel(panel, text="Highlighted text", anchor="w").pack(fill="x", padx=12)
        self.selected_text_box = ctk.CTkTextbox(panel, height=64, wrap="word")
        self.selected_text_box.pack(fill="x", padx=12, pady=(2, 8))
        self.selected_text_box.configure(state="disabled")

        self.style_label = ctk.CTkLabel(panel, text="No selection", anchor="w", text_color="gray60")
        self.style_label.pack(fill="x", padx=12)

        ctk.CTkLabel(panel, text="Replacement text", anchor="w").pack(fill="x", padx=12, pady=(8, 0))
        self.replacement_box = ctk.CTkTextbox(panel, height=76, wrap="word")
        self.replacement_box.pack(fill="x", padx=12, pady=(2, 8))

        options = ctk.CTkFrame(panel, fg_color="transparent")
        options.pack(fill="x", padx=12)

        ctk.CTkLabel(options, text="Font", width=44, anchor="w").grid(row=0, column=0, sticky="w", pady=3)
        self.font_selector = ctk.CTkOptionMenu(
            options, width=150, values=["Keep original", "Helvetica", "Times", "Courier"]
        )
        self.font_selector.grid(row=0, column=1, sticky="w", pady=3)

        ctk.CTkLabel(options, text="Size", width=44, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        self.size_entry = ctk.CTkEntry(options, width=150, placeholder_text="Keep original")
        self.size_entry.grid(row=1, column=1, sticky="w", pady=3)

        ctk.CTkLabel(options, text="Marker", width=44, anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        self.highlight_colour = ctk.CTkOptionMenu(options, width=150, values=list(HIGHLIGHT_COLOURS))
        self.highlight_colour.grid(row=2, column=1, sticky="w", pady=3)

        self.reflow_checkbox = ctk.CTkCheckBox(
            panel, text="Reflow paragraph after editing", onvalue=True, offvalue=False
        )
        self.reflow_checkbox.select()
        self.reflow_checkbox.pack(anchor="w", padx=12, pady=(10, 4))

        ctk.CTkButton(panel, text="Replace text", command=self.apply_replacement_from_panel).pack(
            fill="x", padx=12, pady=3
        )
        ctk.CTkButton(panel, text="Delete text (Backspace)", fg_color="#b4453c", hover_color="#93362f",
                      command=self.delete_selected_text).pack(fill="x", padx=12, pady=3)
        ctk.CTkButton(panel, text="Highlight selection", fg_color="#c9a227", hover_color="#a8871f",
                      command=self.highlight_selection).pack(fill="x", padx=12, pady=3)
        ctk.CTkButton(panel, text="Redact selection", fg_color="gray40", hover_color="gray32",
                      command=self.redact_selection).pack(fill="x", padx=12, pady=3)

        ctk.CTkLabel(panel, text="Find in document", anchor="w").pack(fill="x", padx=12, pady=(14, 0))
        finder = ctk.CTkFrame(panel, fg_color="transparent")
        finder.pack(fill="x", padx=12, pady=(2, 10))
        self.search_entry = ctk.CTkEntry(finder, width=160, placeholder_text="Search text")
        self.search_entry.pack(side="left")
        self.search_entry.bind("<Return>", lambda _event: self.find_next())
        ctk.CTkButton(finder, text="Find", width=64, command=self.find_next).pack(side="left", padx=6)

    def _build_image_panel(self) -> None:
        """Builds the image placement side panel."""
        panel = ctk.CTkFrame(self.properties, fg_color="transparent", width=254)
        self.image_panel = panel

        ctk.CTkLabel(panel, text="Image", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(12, 6)
        )
        self.image_name_label = ctk.CTkLabel(
            panel, text="No image selected", anchor="w", wraplength=230, justify="left", text_color="gray60"
        )
        self.image_name_label.pack(fill="x", padx=12, pady=(0, 8))

        grid = ctk.CTkFrame(panel, fg_color="transparent")
        grid.pack(fill="x", padx=12)
        self.image_entries = {}
        for row, (key, caption) in enumerate((("x", "X"), ("y", "Y"), ("w", "Width"), ("h", "Height"))):
            ctk.CTkLabel(grid, text=caption, width=52, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
            entry = ctk.CTkEntry(grid, width=140)
            entry.grid(row=row, column=1, sticky="w", pady=3)
            entry.bind("<Return>", lambda _event: self.apply_image_geometry())
            self.image_entries[key] = entry

        self.keep_ratio_checkbox = ctk.CTkCheckBox(panel, text="Keep aspect ratio", onvalue=True, offvalue=False)
        self.keep_ratio_checkbox.select()
        self.keep_ratio_checkbox.pack(anchor="w", padx=12, pady=(10, 6))

        ctk.CTkButton(panel, text="Apply size / position", command=self.apply_image_geometry).pack(
            fill="x", padx=12, pady=3
        )
        ctk.CTkButton(panel, text="Place image on page", fg_color="#2fa572", hover_color="#26855c",
                      command=self.place_pending_image).pack(fill="x", padx=12, pady=3)
        ctk.CTkButton(panel, text="Choose another image...", command=self.insert_image).pack(
            fill="x", padx=12, pady=3
        )
        ctk.CTkButton(panel, text="Delete image", fg_color="#b4453c", hover_color="#93362f",
                      command=self.delete_selected_image).pack(fill="x", padx=12, pady=3)

        ctk.CTkLabel(
            panel,
            text=(
                "Drag the picture to move it and pull the white grips to resize it. "
                "Press Enter to place it, Escape to cancel."
            ),
            wraplength=230,
            justify="left",
            text_color="gray60",
        ).pack(fill="x", padx=12, pady=12)

    def _build_status_bar(self) -> None:
        """Creates the status bar at the bottom of the window."""
        bar = ctk.CTkFrame(self, corner_radius=0, height=28)
        bar.grid(row=3, column=0, sticky="ew")

        self.status_label = ctk.CTkLabel(bar, text="Open a PDF to start editing.", anchor="w")
        self.status_label.pack(side="left", padx=12)
        self.info_label = ctk.CTkLabel(bar, text="", anchor="e", text_color="gray60")
        self.info_label.pack(side="right", padx=12)

    def _bind_shortcuts(self) -> None:
        """Binds the keyboard accelerators."""
        self.bind("<Control-o>", lambda _event: self.open_document())
        self.bind("<Control-s>", lambda _event: self.save_document())
        self.bind("<Control-S>", lambda _event: self.save_document_as())
        self.bind("<Control-Shift-S>", lambda _event: self.save_document_as())
        self.bind("<Control-z>", lambda _event: self.undo())
        self.bind("<Control-y>", lambda _event: self.redo())
        self.bind("<Control-m>", lambda _event: self.merge_pdfs())
        self.bind("<Control-i>", lambda _event: self.import_document())
        self.bind("<Control-plus>", lambda _event: self.zoom_in())
        self.bind("<Control-equal>", lambda _event: self.zoom_in())
        self.bind("<Control-minus>", lambda _event: self.zoom_out())
        self.bind("<Control-0>", lambda _event: self.fit_page())
        self.bind("<Next>", lambda _event: self.step_page(1))
        self.bind("<Prior>", lambda _event: self.step_page(-1))
        self.bind("<F1>", lambda _event: self.show_shortcuts())

    def _setup_drag_and_drop(self) -> None:
        """Registers the window and the page view as drop targets."""
        if not self.dnd_enabled:
            self.startup_notes.append(
                "Drag-and-drop needs the 'tkinterdnd2' package; use Open or Merge instead."
            )
            return
        targets = (self, self.canvas_view.canvas, self.thumbnails, self.properties)
        if not any(dnd.register_drop_target(widget, self.handle_dropped_files) for widget in targets):
            self.startup_notes.append("Drag-and-drop could not be enabled on this system.")

    # ==================================================================
    # Small helpers
    # ==================================================================
    def set_status(self, message: str, error: bool = False) -> None:
        """
        Shows a message in the status bar.

        :param message: Text to display.
        :param error: Render the message in the error colour.
        """
        self.status_label.configure(text=message, text_color="#e05252" if error else ("gray10", "gray90"))

    def show_error(self, title: str, message: str) -> None:
        """
        Reports a failed operation to the user.

        :param title: Short operation name.
        :param message: Explanation shown in the dialog and the status bar.
        """
        self.set_status(f"{title}: {message}", error=True)
        messagebox.showerror(title, message, parent=self)

    def _require_session(self) -> bool:
        """Returns True when a document is open, otherwise warns the user."""
        if self.session is None:
            self.set_status("Open a PDF first.", error=True)
            return False
        return True

    def _update_controls(self) -> None:
        """Refreshes the title, the counters and the enabled state of buttons."""
        has_document = self.session is not None
        dirty = bool(has_document and self.session.dirty)
        name = self.session.display_name if has_document else "no document"
        self.title(f"{'*' if dirty else ''}{name} - {APP_NAME}")

        self.undo_button.configure(state="normal" if has_document and self.session.can_undo else "disabled")
        self.redo_button.configure(state="normal" if has_document and self.session.can_redo else "disabled")
        self.save_button.configure(state="normal" if has_document else "disabled")

        if has_document:
            total = self.session.page_count
            index = self.canvas_view.page_index
            self.page_entry.delete(0, tk.END)
            self.page_entry.insert(0, str(index + 1))
            self.page_total_label.configure(text=f"of {total}")
            page = self.session.page(index)
            self.info_label.configure(
                text=(
                    f"{page.rect.width:.0f} x {page.rect.height:.0f} pt"
                    f"  |  rotation {page.rotation}deg"
                    f"  |  {'text' if text_ops.page_has_text(page) else 'no text layer'}"
                )
            )
        else:
            self.page_entry.delete(0, tk.END)
            self.page_total_label.configure(text="of 0")
            self.info_label.configure(text="")
        self.refresh_zoom_label()

    def refresh_zoom_label(self) -> None:
        """Updates the zoom percentage shown in the toolbar."""
        self.zoom_label.configure(text=f"{self.canvas_view.zoom * 100:.0f}%")

    def _show_panel(self, mode: str) -> None:
        """Shows the side panel matching the active tool."""
        self.text_panel.pack_forget()
        self.image_panel.pack_forget()
        if mode == MODE_IMAGE:
            self.image_panel.pack(fill="both", expand=True)
        else:
            self.text_panel.pack(fill="both", expand=True)

    def _apply(self, label: str, operation: Callable[[fitz.Document], object], *,
               page_index: Optional[int] = None, structural: bool = False):
        """
        Runs one undoable document modification and refreshes the views.

        :param label: Name of the operation, shown in the status bar and history.
        :param operation: Callable receiving the document and performing the change.
        :param page_index: Page to show afterwards; defaults to the current one.
        :param structural: True when the page count or order changed.
        :return: The operation's return value (``True`` when it returned ``None``),
            or ``None`` when the operation failed.
        """
        if not self._require_session():
            return None
        try:
            with self.session.edit(label, self.canvas_view.page_index):
                result = operation(self.session.document)
        except PDFEditorError as error:
            self.show_error(label, str(error))
            return None
        except Exception as error:  # Unexpected: still must not kill the editor.
            self.show_error(label, f"Unexpected problem: {error}")
            return None

        self._after_edit(page_index=page_index, structural=structural)
        return True if result is None else result

    def _after_edit(self, *, page_index: Optional[int] = None, structural: bool = False) -> None:
        """Re-renders the views after a document change."""
        if self.session is None:
            return
        target = self.canvas_view.page_index if page_index is None else page_index
        target = max(0, min(target, self.session.page_count - 1))

        self.canvas_view.selection = None
        self.canvas_view.selected_image = None
        self.canvas_view.image_rect = None
        self.canvas_view.page_index = target
        self.canvas_view.render()

        if structural:
            self.thumbnails.refresh(target)
        else:
            self.thumbnails.refresh_page(target)
            self.thumbnails.set_current(target)
        self.on_selection_changed(None)
        self._update_controls()

    # ==================================================================
    # Document lifecycle
    # ==================================================================
    def open_document(self, path: Optional[Path] = None) -> None:
        """
        Opens a PDF, asking for the file name and password when needed.

        :param path: File to open; a file dialog is shown when omitted.
        """
        if path is None:
            chosen = filedialog.askopenfilename(
                parent=self, title="Open PDF", filetypes=PDF_FILETYPES
            )
            if not chosen:
                return
            path = Path(chosen)

        if not self._confirm_discard_changes():
            return

        password: Optional[str] = None
        while True:
            try:
                session = EditSession.open(path, password)
                break
            except EncryptedPDFError:
                password = PasswordDialog(self, Path(path).name).show()
                if password is None:
                    self.set_status("Opening cancelled: the document is password protected.")
                    return
            except PDFEditorError as error:
                self.show_error("Open PDF", str(error))
                return

        if self.session is not None:
            self.session.close()
        self.session = session
        self.canvas_view.attach(session, 0)
        self.thumbnails.attach(session)
        self.canvas_view.fit_width()
        self._update_controls()
        self.set_status(f"Opened '{session.display_name}' ({session.page_count} page(s)).")
        for note in self.startup_notes:
            self.set_status(note)
        self.startup_notes.clear()

    def close_document(self) -> None:
        """Closes the current document after asking about unsaved changes."""
        if self.session is None:
            return
        if not self._confirm_discard_changes():
            return
        self.session.close()
        self.session = None
        self.canvas_view.detach()
        self.thumbnails.detach()
        self._update_controls()
        self.set_status("Document closed.")

    def save_document(self) -> None:
        """Saves the document to the file it was opened from."""
        if not self._require_session():
            return
        if self.session.path is None:
            self.save_document_as()
            return
        try:
            target = self.session.save()
        except PDFEditorError as error:
            self.show_error("Save", str(error))
            return
        self._update_controls()
        self.set_status(f"Saved to '{target}'.")

    def save_document_as(self) -> None:
        """Asks for a file name and exports the document there."""
        if not self._require_session():
            return
        suggestion = self.session.path.name if self.session.path else "edited.pdf"
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Export PDF as",
            defaultextension=".pdf",
            initialfile=suggestion,
            filetypes=PDF_FILETYPES,
        )
        if not chosen:
            return
        try:
            target = self.session.save(chosen)
        except PDFEditorError as error:
            self.show_error("Save As", str(error))
            return
        self._update_controls()
        self.set_status(f"Exported to '{target}'.")

    def _confirm_discard_changes(self) -> bool:
        """
        Asks the user what to do with unsaved changes.

        :return: True when it is safe to continue.
        """
        if self.session is None or not self.session.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"'{self.session.display_name}' has unsaved changes.\n\nSave them now?",
            parent=self,
        )
        if answer is None:
            return False
        if answer:
            self.save_document()
            return not self.session.dirty
        return True

    def on_close(self) -> None:
        """Handles the window close button."""
        if not self._confirm_discard_changes():
            return
        if self.session is not None:
            self.session.close()
        self.destroy()

    # ==================================================================
    # History and navigation
    # ==================================================================
    def undo(self) -> None:
        """Reverts the last edit."""
        if not self._require_session():
            return
        label, page_index = self.session.undo()
        if label is None:
            self.set_status("Nothing to undo.")
            return
        self._after_edit(page_index=page_index, structural=True)
        self.set_status(f"Undone: {label}")

    def redo(self) -> None:
        """Re-applies the last undone edit."""
        if not self._require_session():
            return
        label, page_index = self.session.redo()
        if label is None:
            self.set_status("Nothing to redo.")
            return
        self._after_edit(page_index=page_index, structural=True)
        self.set_status(f"Redone: {label}")

    def goto_page(self, index: int) -> None:
        """
        Shows a page in the main view.

        :param index: 0-based page index.
        """
        if not self._require_session():
            return
        self.canvas_view.show_page(index)
        self.thumbnails.set_current(self.canvas_view.page_index)
        self._update_controls()

    def step_page(self, delta: int) -> None:
        """
        Moves forward or backward by a number of pages.

        :param delta: Number of pages to move (negative goes back).
        """
        if self.session is None:
            return
        self.goto_page(self.canvas_view.page_index + delta)

    def _on_page_entry(self, _event) -> None:
        """Jumps to the page number typed in the toolbar."""
        try:
            number = int(self.page_entry.get().strip())
        except ValueError:
            self._update_controls()
            return
        self.goto_page(number - 1)

    def zoom_in(self) -> None:
        """Zooms the page view in."""
        self.canvas_view.zoom_in()
        self.refresh_zoom_label()

    def zoom_out(self) -> None:
        """Zooms the page view out."""
        self.canvas_view.zoom_out()
        self.refresh_zoom_label()

    def fit_width(self) -> None:
        """Fits the page width into the view."""
        self.canvas_view.fit_width()
        self.refresh_zoom_label()

    def fit_page(self) -> None:
        """Fits the whole page into the view."""
        self.canvas_view.fit_page()
        self.refresh_zoom_label()

    def set_appearance(self, mode: str) -> None:
        """
        Switches between light, dark and system appearance.

        :param mode: ``"System"``, ``"Light"`` or ``"Dark"``.
        """
        ctk.set_appearance_mode(mode)
        self.set_status(f"Appearance set to {mode.lower()}.")

    # ==================================================================
    # Tool handling
    # ==================================================================
    def _on_tool_change(self, value: str) -> None:
        """Applies the tool chosen in the toolbar."""
        mode = TOOL_MODES.get(value, MODE_TEXT)
        self.canvas_view.set_mode(mode)
        self._show_panel(mode)
        hints = {
            MODE_TEXT: "Drag across words to highlight them, then type, press Backspace, or use the panel.",
            MODE_HIGHLIGHT: "Drag across words to add a yellow highlight annotation.",
            MODE_IMAGE: "Click an existing image to move or resize it, or use 'Insert Image'.",
            MODE_ADD_TEXT: "Drag a rectangle where the new text should go.",
        }
        self.set_status(hints.get(mode, ""))

    # ==================================================================
    # Text editing
    # ==================================================================
    def on_selection_changed(self, selection: Optional[text_ops.TextSelection]) -> None:
        """
        Updates the side panel when the highlighted text changes.

        :param selection: The new selection, or ``None``.
        """
        self.selected_text_box.configure(state="normal")
        self.selected_text_box.delete("1.0", tk.END)

        if selection is None or selection.is_empty:
            self.selected_text_box.configure(state="disabled")
            self.style_label.configure(text="No selection")
            return

        self.selected_text_box.insert("1.0", selection.text)
        self.selected_text_box.configure(state="disabled")

        page = self.session.page(selection.page_index) if self.session else None
        if page is not None:
            style = text_ops.probe_style(page, selection)
            weight = " bold" if style.bold else ""
            slant = " italic" if style.italic else ""
            self.style_label.configure(
                text=f"{style.font} - {style.size:.1f} pt{weight}{slant} - {len(selection.words)} word(s)"
            )
        self.set_status(f"Selected: \"{_shorten(selection.text)}\"")

    def _style_override(self, selection: text_ops.TextSelection) -> Optional[SpanStyle]:
        """
        Builds the style for replacement text from the panel settings.

        :param selection: The selection being replaced.
        :return: A style, or ``None`` to keep the original one untouched.
        """
        if self.session is None:
            return None
        page = self.session.page(selection.page_index)
        base = text_ops.probe_style(page, selection)

        font_choice = self.font_selector.get()
        size_text = self.size_entry.get().strip()
        if font_choice == "Keep original" and not size_text:
            return None

        font_name = base.font if font_choice == "Keep original" else font_choice
        size = base.size
        if size_text:
            try:
                size = float(size_text)
            except ValueError:
                self.set_status("Font size must be a number; keeping the original size.", error=True)
        return base.with_overrides(font=font_name, size=size)

    def replace_selected_text(self, new_text: str) -> None:
        """
        Replaces the highlighted text (used by the inline editor).

        :param new_text: Text typed by the user.
        """
        selection = self.canvas_view.selection
        if selection is None or selection.is_empty:
            self.set_status("Highlight some text first.", error=True)
            return

        reflow = bool(self.reflow_checkbox.get())
        style = self._style_override(selection)

        def operation(document: fitz.Document):
            return text_ops.replace_selection(
                document[selection.page_index], selection, new_text, reflow=reflow, style=style
            )

        result = self._apply("Replace text", operation, page_index=selection.page_index)
        if result:
            self.replacement_box.delete("1.0", tk.END)
            message = result.summary
            if result.warnings:
                message += " - " + " ".join(result.warnings)
            self.set_status(message)

    def apply_replacement_from_panel(self) -> None:
        """Replaces the selection with the text typed in the side panel."""
        self.replace_selected_text(self.replacement_box.get("1.0", tk.END).strip())

    def delete_selected_text(self) -> None:
        """Deletes the highlighted text and closes the gap."""
        selection = self.canvas_view.selection
        if selection is None or selection.is_empty:
            self.set_status("Highlight some text first.", error=True)
            return
        reflow = bool(self.reflow_checkbox.get())

        def operation(document: fitz.Document):
            return text_ops.delete_selection(document[selection.page_index], selection, reflow=reflow)

        result = self._apply("Delete text", operation, page_index=selection.page_index)
        if result:
            self.set_status(result.summary)

    def highlight_selection(self) -> None:
        """Adds a highlight annotation over the selection."""
        selection = self.canvas_view.selection
        if selection is None or selection.is_empty:
            self.set_status("Highlight some text first.", error=True)
            return
        colour = HIGHLIGHT_COLOURS.get(self.highlight_colour.get(), (1.0, 0.92, 0.23))

        def operation(document: fitz.Document):
            return text_ops.highlight_selection(document[selection.page_index], selection, colour)

        if self._apply("Highlight text", operation, page_index=selection.page_index):
            self.set_status("Highlight added.")

    def redact_selection(self) -> None:
        """Permanently removes the selected text and paints a black box over it."""
        selection = self.canvas_view.selection
        if selection is None or selection.is_empty:
            self.set_status("Highlight some text first.", error=True)
            return
        if not messagebox.askyesno(
            "Redact text",
            "Redaction permanently removes the selected text. Continue?",
            parent=self,
        ):
            return

        def operation(document: fitz.Document):
            return text_ops.redact_selection(document[selection.page_index], selection)

        if self._apply("Redact text", operation, page_index=selection.page_index):
            self.set_status("Selection redacted.")

    def find_next(self) -> None:
        """Searches the document and highlights the next match."""
        if not self._require_session():
            return
        needle = self.search_entry.get().strip()
        if not needle:
            return

        total = self.session.page_count
        start = self.canvas_view.page_index
        for step in range(total):
            index = (start + step) % total
            page = self.session.page(index)
            hits = text_ops.search_page(page, needle)
            if not hits:
                continue
            self.goto_page(index)
            words = [
                word for word in text_ops.page_words(page)
                if any(hit.intersects(word.rect) for hit in hits)
            ]
            self.canvas_view.set_selection(text_ops.TextSelection(index, words))
            self.set_status(f"Found {len(hits)} match(es) for '{needle}' on page {index + 1}.")
            return
        self.set_status(f"'{needle}' was not found.", error=True)

    def request_text_box(self, rect: fitz.Rect, page_index: int) -> None:
        """
        Inserts a new text box after asking for its content.

        :param rect: Rectangle drawn by the user.
        :param page_index: Page the box belongs to.
        """
        if not self._require_session():
            return
        values = TextBoxDialog(self).show()
        if not values:
            return
        style = SpanStyle(
            font=values["font"], size=values["size"], color=values["color"]
        ).with_overrides(bold=values["bold"], italic=values["italic"])

        def operation(document: fitz.Document):
            return text_ops.insert_text_box(document[page_index], rect, values["text"], style=style)

        if self._apply("Insert text box", operation, page_index=page_index):
            self.set_status("Text box inserted.")

    def insert_text_box(self) -> None:
        """Starts the 'add text box' tool from the menu."""
        if not self._require_session():
            return
        self.tool_selector.set("Add Text Box")
        self._on_tool_change("Add Text Box")

    # ==================================================================
    # Images
    # ==================================================================
    def insert_image(self, path: Optional[Path] = None) -> None:
        """
        Starts placing an image on the current page.

        :param path: Image file; a file dialog is shown when omitted.
        """
        if not self._require_session():
            return
        if path is None:
            chosen = filedialog.askopenfilename(
                parent=self, title="Insert image", filetypes=IMAGE_FILETYPES
            )
            if not chosen:
                return
            path = Path(chosen)

        self.tool_selector.set("Insert Image")
        self.canvas_view.set_mode(MODE_IMAGE)
        self._show_panel(MODE_IMAGE)
        try:
            self.canvas_view.begin_image_placement(path)
        except PDFEditorError as error:
            self.show_error("Insert image", str(error))
            return
        self.set_status("Drag the image to position it, then press Enter or 'Place image on page'.")

    def on_image_changed(self, item, rect: Optional[fitz.Rect] = None) -> None:
        """
        Updates the image panel when the placement changes.

        :param item: The pending image, the selected image, or ``None``.
        :param rect: Live rectangle while an existing image is being dragged.
        """
        if item is None:
            self.image_name_label.configure(text="No image selected")
            for entry in self.image_entries.values():
                entry.delete(0, tk.END)
            return

        if isinstance(item, PendingImage):
            box = item.rect
            self.image_name_label.configure(
                text=f"{item.path.name}  ({item.info.width}x{item.info.height} px, not placed yet)"
            )
        elif isinstance(item, ImageRef):
            box = rect if rect is not None else item.bbox
            self.image_name_label.configure(
                text=f"Image on page {self.canvas_view.page_index + 1} ({item.width}x{item.height} px)"
            )
        else:
            return

        for key, value in (("x", box.x0), ("y", box.y0), ("w", box.width), ("h", box.height)):
            entry = self.image_entries[key]
            entry.delete(0, tk.END)
            entry.insert(0, f"{value:.1f}")

    def apply_image_geometry(self) -> None:
        """Applies the numbers typed into the image panel."""
        try:
            x = float(self.image_entries["x"].get())
            y = float(self.image_entries["y"].get())
            width = float(self.image_entries["w"].get())
            height = float(self.image_entries["h"].get())
        except ValueError:
            self.set_status("Position and size must be numbers.", error=True)
            return
        if width <= 0 or height <= 0:
            self.set_status("Width and height must be greater than zero.", error=True)
            return

        if self.canvas_view.pending_image is not None:
            self.canvas_view.pending_image.keep_ratio = bool(self.keep_ratio_checkbox.get())
        self.canvas_view.set_pending_rect(fitz.Rect(x, y, x + width, y + height))

    def place_pending_image(self) -> None:
        """Writes the pending image into the document."""
        if self.canvas_view.pending_image is None:
            self.set_status("No image is waiting to be placed.", error=True)
            return
        self.canvas_view.commit_pending_image()

    def commit_image(self, pending: PendingImage, page_index: int) -> bool:
        """
        Inserts a pending image into the document (called by the canvas).

        :param pending: The placement to commit.
        :param page_index: Page receiving the image.
        :return: True when the image was inserted.
        """
        pending.keep_ratio = bool(self.keep_ratio_checkbox.get())

        def operation(document: fitz.Document):
            return pending.commit(document[page_index])

        if self._apply("Insert image", operation, page_index=page_index):
            self.set_status(f"Placed '{pending.path.name}' on page {page_index + 1}.")
            return True
        return False

    def move_existing_image(self, ref: ImageRef, rect: fitz.Rect, page_index: int) -> None:
        """
        Moves or resizes an image that is already in the document.

        :param ref: Handle of the image.
        :param rect: New rectangle in PDF coordinates.
        :param page_index: Page holding the image.
        """
        keep_ratio = bool(self.keep_ratio_checkbox.get())

        def operation(document: fitz.Document):
            return image_ops.move_image(document[page_index], ref, rect, keep_ratio=keep_ratio)

        if self._apply("Move image", operation, page_index=page_index):
            self.set_status("Image updated.")

    def delete_selected_image(self) -> None:
        """Deletes the selected image from the page."""
        if self.canvas_view.pending_image is not None:
            self.canvas_view.cancel_pending_image()
            self.set_status("Image placement cancelled.")
            return

        ref = self.canvas_view.selected_image
        if ref is None:
            self.set_status("Click an image first (Insert Image tool).", error=True)
            return
        page_index = self.canvas_view.page_index

        def operation(document: fitz.Document):
            return image_ops.delete_image(document[page_index], ref)

        if self._apply("Delete image", operation, page_index=page_index):
            self.set_status("Image deleted.")

    # ==================================================================
    # Page management
    # ==================================================================
    def delete_pages(self) -> None:
        """Deletes the pages selected in the sidebar."""
        if not self._require_session():
            return
        indices = self.thumbnails.selected_indices
        if not indices:
            return
        names = ", ".join(str(index + 1) for index in indices)
        if not messagebox.askyesno("Delete pages", f"Delete page(s) {names}?", parent=self):
            return

        def operation(document: fitz.Document):
            return page_ops.delete_pages(document, indices)

        if self._apply("Delete pages", operation, page_index=max(0, indices[0] - 1), structural=True):
            self.set_status(f"Deleted page(s) {names}.")

    def duplicate_pages(self) -> None:
        """Duplicates the pages selected in the sidebar."""
        if not self._require_session():
            return
        indices = self.thumbnails.selected_indices
        if not indices:
            return

        def operation(document: fitz.Document):
            return page_ops.duplicate_pages(document, indices)

        created = self._apply("Duplicate pages", operation, page_index=indices[0] + 1, structural=True)
        if created:
            self.set_status(f"Duplicated {len(indices)} page(s).")

    def add_blank_page(self, where: str = "dialog") -> None:
        """
        Inserts one or more blank pages.

        :param where: ``"before"``, ``"after"`` or ``"dialog"`` to ask the user.
        """
        if not self._require_session():
            return
        current = self.canvas_view.page_index

        if where == "dialog":
            values = BlankPageDialog(self, current + 1).show()
            if not values:
                return
            position_text = values["position"]
            count = values["count"]
            size_choice = values["size"]
            landscape = values["landscape"]
        else:
            position_text = f"{'Before' if where == 'before' else 'After'} page {current + 1}"
            count, size_choice, landscape = 1, "Same as current page", False

        if position_text.startswith("Before"):
            position = current
        elif position_text.startswith("After"):
            position = current + 1
        elif position_text.startswith("Beginning"):
            position = 0
        else:
            position = self.session.page_count

        match_page = current if size_choice == "Same as current page" else None
        paper = "A4" if match_page is not None else size_choice

        def operation(document: fitz.Document):
            for offset in range(count):
                page_ops.insert_blank_page(
                    document,
                    position + offset,
                    paper=paper,
                    landscape=landscape,
                    match_page=match_page,
                )
            return count

        if self._apply("Add blank page", operation, page_index=position, structural=True):
            self.set_status(f"Inserted {count} blank page(s) at position {position + 1}.")

    def rotate_pages(self, angle: int) -> None:
        """
        Rotates the selected pages.

        :param angle: Rotation in degrees, positive is clockwise.
        """
        if not self._require_session():
            return
        indices = self.thumbnails.selected_indices
        if not indices:
            return

        def operation(document: fitz.Document):
            return page_ops.rotate_pages(document, indices, angle)

        if self._apply("Rotate pages", operation, structural=True):
            self.set_status(f"Rotated {len(indices)} page(s) by {angle} degrees.")

    def move_page(self, delta: int) -> None:
        """
        Moves the current page up or down in the document.

        :param delta: ``-1`` moves the page earlier, ``+1`` later.
        """
        if not self._require_session():
            return
        source = self.canvas_view.page_index
        destination = source + delta
        if not 0 <= destination < self.session.page_count:
            self.set_status("The page is already at the end of the document.")
            return

        def operation(document: fitz.Document):
            return page_ops.move_page(document, source, destination)

        if self._apply("Move page", operation, page_index=destination, structural=True):
            self.set_status(f"Moved page {source + 1} to position {destination + 1}.")

    def extract_pages(self) -> None:
        """Exports the selected pages into a new PDF file."""
        if not self._require_session():
            return
        indices = self.thumbnails.selected_indices
        if not indices:
            return
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Extract pages to",
            defaultextension=".pdf",
            initialfile="extracted.pdf",
            filetypes=PDF_FILETYPES,
        )
        if not chosen:
            return
        try:
            target = page_ops.extract_pages(self.session.document, indices, chosen)
        except PDFEditorError as error:
            self.show_error("Extract pages", str(error))
            return
        self.set_status(f"Extracted {len(indices)} page(s) to '{target}'.")

    def merge_pdfs(self, initial_files: Optional[Sequence[Path]] = None) -> None:
        """
        Merges other PDF files into the open document.

        :param initial_files: Files pre-filled in the dialog (e.g. dropped files).
        """
        if self.session is None:
            files = list(initial_files or [])
            if not files:
                chosen = filedialog.askopenfilenames(
                    parent=self, title="Select PDFs to merge", filetypes=PDF_FILETYPES
                )
                files = [Path(item) for item in chosen]
            if not files:
                return
            self.open_document(files[0])
            if self.session is None:
                return
            remaining = files[1:]
            if not remaining:
                return
            initial_files = remaining

        current = self.canvas_view.page_index
        values = MergeDialog(self, initial_files, current + 1).show()
        if not values:
            return

        position_text = values["position"]
        if position_text.startswith("Before"):
            position = current
        elif position_text.startswith("After"):
            position = current + 1
        elif position_text.startswith("Beginning"):
            position = 0
        else:
            position = self.session.page_count

        def operation(document: fitz.Document):
            return page_ops.merge_documents(
                document, values["files"], at=position, add_bookmarks=values["bookmarks"]
            )

        added = self._apply("Merge PDFs", operation, page_index=position, structural=True)
        if added:
            self.set_status(f"Merged {len(values['files'])} file(s): {added} page(s) added.")

    # ==================================================================
    # Export as Word and compression
    # ==================================================================
    def _snapshot_for_export(self) -> Optional[bytes]:
        """
        Serialises the open document so a worker thread can read it safely.

        PyMuPDF documents must not be shared between threads, so exports always
        work on a private copy of the bytes taken on the UI thread.

        :return: The PDF data, or ``None`` when the snapshot failed.
        """
        try:
            return self.session.export_bytes()
        except Exception as error:
            self.show_error("Export", f"Could not read the document: {error}")
            return None

    def _run_task(self, title: str, message: str, worker):
        """
        Runs a slow job behind a progress dialog.

        :param title: Title of the progress dialog.
        :param message: Line shown above the progress bar.
        :param worker: Callable receiving a ``progress(fraction, message)`` function.
        :return: The worker's result, or ``None`` when it failed.
        """
        try:
            return TaskDialog(self, title, message).run(worker)
        except PDFEditorError as error:
            self.show_error(title, str(error))
        except Exception as error:  # Unexpected: report instead of dying.
            self.show_error(title, f"Unexpected problem: {error}")
        return None

    def _open_externally(self, path: Path, question: str) -> None:
        """
        Offers to open a produced file in its default application.

        :param path: File that was just written.
        :param question: Question shown to the user.
        """
        if not hasattr(os, "startfile"):
            return
        if not messagebox.askyesno("Finished", question, parent=self):
            return
        try:
            os.startfile(str(path))  # noqa: S606 - opening the user's own output
        except Exception as error:
            self.set_status(f"Could not open '{path.name}': {error}", error=True)

    def export_as_word(self) -> None:
        """Exports the document, as it stands right now, to a .docx file."""
        if not self._require_session():
            return

        methods = word_export.available_methods()
        if not methods:
            self.show_error(
                "Export as Word",
                "Word export needs the 'pdf2docx' package.\n\n"
                "Install it with:  pip install pdf2docx",
            )
            return

        selected = self.thumbnails.selected
        values = WordExportDialog(
            self,
            self.session.page_count,
            self.canvas_view.page_index + 1,
            methods,
            has_selection=len(selected) > 1,
        ).show()
        if not values:
            return

        choice = values["pages"]
        if choice.startswith("Current page"):
            pages = [self.canvas_view.page_index]
        elif choice.startswith("Pages selected"):
            pages = self.thumbnails.selected_indices
        else:
            pages = None

        stem = self.session.path.stem if self.session.path else "document"
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Export as Word document",
            defaultextension=".docx",
            initialfile=f"{stem}.docx",
            filetypes=DOCX_FILETYPES,
        )
        if not chosen:
            return

        data = self._snapshot_for_export()
        if data is None:
            return

        def worker(progress):
            return word_export.export_to_docx(
                data, chosen, pages=pages, method=values["method"], progress=progress
            )

        report = self._run_task(
            "Export as Word", f"Writing '{Path(chosen).name}'...", worker
        )
        if report is None:
            return

        self.set_status(report.summary)
        for warning in report.warnings[:2]:
            self.set_status(warning, error=True)
        self._open_externally(report.destination, "Open the Word document now?")

    def compress_document(self) -> None:
        """Writes a smaller copy of the document."""
        if not self._require_session():
            return

        data = self._snapshot_for_export()
        if data is None:
            return

        values = CompressDialog(self, compress.format_size(len(data))).show()
        if not values:
            return

        stem = self.session.path.stem if self.session.path else "document"
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Save compressed PDF as",
            defaultextension=".pdf",
            initialfile=f"{stem}-compressed.pdf",
            filetypes=PDF_FILETYPES,
        )
        if not chosen:
            return

        def worker(progress):
            return compress.compress_pdf(
                data,
                chosen,
                level=values["level"],
                downsample_images=values["downsample_images"],
                progress=progress,
            )

        report = self._run_task("Compress PDF", "Compressing the document...", worker)
        if report is None:
            return

        self.set_status(f"Compressed: {report.summary}")
        lines = [
            f"Level: {compress.PROFILES[report.level].label}",
            f"Before: {compress.format_size(report.original_bytes)}",
            f"After:  {compress.format_size(report.compressed_bytes)}",
            f"Saved:  {compress.format_size(report.saved_bytes)} ({report.ratio * 100:.0f}%)",
            f"Images: {report.images}",
            "",
            f"Written to {report.destination}",
        ]
        lines.extend(report.warnings)
        messagebox.showinfo("Compression finished", "\n".join(lines), parent=self)

        if messagebox.askyesno(
            "Compressed file",
            "Open the compressed file in the editor now?",
            parent=self,
        ):
            self.open_document(report.destination)

    def import_document(self, path: Optional[Path] = None) -> None:
        """
        Converts a Word, Excel or PowerPoint document into a PDF.

        :param path: Document to convert; a file dialog is shown when omitted.
        """
        if path is None:
            chosen = filedialog.askopenfilename(
                parent=self, title="Import document", filetypes=IMPORT_FILETYPES
            )
            if not chosen:
                return
            path = Path(chosen)

        path = Path(path)
        if not doc_import.is_supported(path):
            self.show_error(
                "Import document",
                f"'{path.name}' is not a Word, Excel or PowerPoint document.",
            )
            return

        try:
            backends = doc_import.available_backends(path)
        except PDFEditorError as error:
            self.show_error("Import document", str(error))
            return
        if not backends:
            self.show_error(
                "Import document",
                f"'{path.name}' cannot be converted here.\n\n"
                "Install Microsoft Office for the best results, or install the "
                "python-docx, openpyxl, python-pptx and reportlab packages for the "
                "built-in converter.",
            )
            return

        values = ImportDialog(
            self,
            path,
            doc_import.default_destination(path),
            backends,
            can_merge=self.session is not None,
        ).show()
        if not values:
            return

        def worker(progress):
            return doc_import.convert_to_pdf(
                path, values["destination"], backend=values["backend"], progress=progress
            )

        report = self._run_task(
            "Import document", f"Converting '{path.name}' to PDF...", worker
        )
        if report is None:
            return

        self.set_status(report.summary)
        for warning in report.warnings[:2]:
            self.set_status(warning, error=True)

        action = values["after"]
        if action == ImportDialog.AFTER_OPEN:
            self.open_document(report.destination)
        elif action == ImportDialog.AFTER_MERGE and self.session is not None:
            position = self.session.page_count

            def operation(document: fitz.Document):
                return page_ops.merge_documents(
                    document, [report.destination], at=position, add_bookmarks=True
                )

            if self._apply("Merge imported document", operation, page_index=position, structural=True):
                self.set_status(
                    f"{report.summary}; {report.pages} page(s) merged into the open document."
                )

    # ==================================================================
    # Context menu and drag & drop
    # ==================================================================
    def show_canvas_menu(self, event, point: fitz.Point) -> None:
        """
        Opens the context menu of the page view.

        :param event: The originating Tk event (for the screen position).
        :param point: Click position in PDF coordinates.
        """
        if self.session is None:
            return
        menu = tk.Menu(self, tearoff=0)
        selection = self.canvas_view.selection
        has_selection = selection is not None and not selection.is_empty

        menu.add_command(label="Replace text...", state="normal" if has_selection else "disabled",
                         command=lambda: self.canvas_view.start_inline_edit(selection.text if has_selection else ""))
        menu.add_command(label="Delete text", state="normal" if has_selection else "disabled",
                         command=self.delete_selected_text)
        menu.add_command(label="Highlight", state="normal" if has_selection else "disabled",
                         command=self.highlight_selection)
        menu.add_separator()
        menu.add_command(label="Insert image here...", command=self.insert_image)
        menu.add_command(label="Remove highlight under cursor",
                         command=lambda: self.remove_highlight_at(point))
        menu.add_separator()
        menu.add_command(label="Duplicate this page", command=self.duplicate_pages)
        menu.add_command(label="Delete this page", command=self.delete_pages)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def remove_highlight_at(self, point: fitz.Point) -> None:
        """
        Deletes highlight annotations under a point.

        :param point: Location in PDF coordinates.
        """
        page_index = self.canvas_view.page_index

        def operation(document: fitz.Document):
            return text_ops.remove_highlights_at(document[page_index], point)

        removed = self._apply("Remove highlight", operation, page_index=page_index)
        if removed:
            self.set_status(f"Removed {removed} highlight(s)." if removed is not True else "Highlight removed.")

    def handle_dropped_files(self, paths: List[Path]) -> None:
        """
        Handles files dropped onto the window.

        PDFs are opened or merged, Word/Excel/PowerPoint files are converted, and
        images are placed on the current page.

        :param paths: Dropped file paths.
        """
        if not paths:
            return
        pdfs = [path for path in paths if path.suffix.lower() == ".pdf"]
        images = [
            path for path in paths
            if path.suffix.lower() in image_ops.SUPPORTED_IMAGE_EXTENSIONS
        ]
        documents = [path for path in paths if doc_import.is_supported(path)]
        ignored = len(paths) - len(pdfs) - len(images) - len(documents)

        if pdfs:
            if self.session is None:
                self.open_document(pdfs[0])
                if len(pdfs) > 1 and self.session is not None:
                    self.merge_pdfs(pdfs[1:])
            else:
                answer = messagebox.askyesnocancel(
                    "Dropped PDF files",
                    f"Merge {len(pdfs)} dropped file(s) into '{self.session.display_name}'?\n\n"
                    "Yes: merge into this document\n"
                    "No: open the first dropped file instead",
                    parent=self,
                )
                if answer is None:
                    return
                if answer:
                    self.merge_pdfs(pdfs)
                else:
                    self.open_document(pdfs[0])
        elif documents:
            self.import_document(documents[0])
            if len(documents) > 1:
                self.set_status(
                    f"Converting '{documents[0].name}'. "
                    f"Drop the other {len(documents) - 1} document(s) one at a time."
                )
        elif images:
            if self.session is None:
                self.set_status("Open a PDF before dropping images.", error=True)
            else:
                self.insert_image(images[0])
                if len(images) > 1:
                    self.set_status(
                        f"Placing '{images[0].name}'. Drop the other {len(images) - 1} image(s) one at a time."
                    )
        if ignored and not pdfs and not images and not documents:
            self.set_status(
                "Only PDFs, images and Office documents can be dropped here.", error=True
            )

    # ==================================================================
    # Info dialogs
    # ==================================================================
    def show_shortcuts(self) -> None:
        """Shows the keyboard shortcut reference."""
        ShortcutsDialog(self).show()

    def show_document_properties(self) -> None:
        """Shows metadata and page statistics of the open document."""
        if not self._require_session():
            return
        metadata = self.session.metadata
        summary = page_ops.page_summary(self.session.document, self.canvas_view.page_index)
        lines = [
            f"File: {self.session.path or 'not saved yet'}",
            f"Pages: {self.session.page_count}",
            f"Title: {metadata.get('title') or '-'}",
            f"Author: {metadata.get('author') or '-'}",
            f"Producer: {metadata.get('producer') or '-'}",
            f"Created: {metadata.get('creationDate') or '-'}",
            "",
            f"Current page: {summary['number']}",
            f"Size: {summary['width']} x {summary['height']} pt",
            f"Rotation: {summary['rotation']} degrees",
            f"Images: {summary['images']}   Annotations: {summary['annotations']}",
            f"Text layer: {'yes' if summary['has_text'] else 'no'}",
        ]
        messagebox.showinfo("Document properties", "\n".join(lines), parent=self)


def _shorten(text: str, limit: int = 60) -> str:
    """
    Shortens a string for status messages.

    :param text: Text to shorten.
    :param limit: Maximum number of characters.
    :return: The text, truncated with an ellipsis when needed.
    """
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def launch_studio(initial_pdf: Optional[str] = None) -> None:
    """
    Starts the editor window.

    :param initial_pdf: Optional PDF opened at start-up.
    """
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = PDFEditorStudio(initial_pdf)
    app.mainloop()


__all__ = ["APP_NAME", "PDFEditorStudio", "launch_studio"]
