"""
Modal dialogs used by the editor: merge, blank page, new text box and password.

Every dialog follows the same contract: construct it, call :meth:`ModalDialog.show`
and inspect the returned value, which is ``None`` when the user cancelled.
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from pdf_editor.core.compress import PROFILES
from pdf_editor.core.page_ops import PAPER_SIZES

PDF_FILETYPES = [("PDF documents", "*.pdf"), ("All files", "*.*")]
DOCX_FILETYPES = [("Word documents", "*.docx"), ("All files", "*.*")]


class ModalDialog(ctk.CTkToplevel):
    """
    Base class for the editor's modal dialogs.

    :param master: Parent window.
    :param title: Window title.
    :param size: ``(width, height)`` of the dialog.
    """

    def __init__(self, master, title: str, size: Tuple[int, int] = (460, 320)) -> None:
        super().__init__(master)
        self.title(title)
        self.geometry(f"{size[0]}x{size[1]}")
        self.resizable(False, False)
        self.result = None

        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _event: self._on_cancel())

    def show(self):
        """
        Displays the dialog and blocks until it is closed.

        :return: The dialog specific result, or ``None`` when cancelled.
        """
        self.update_idletasks()
        self._centre_on_parent()
        self.grab_set()
        self.wait_window()
        return self.result

    def _centre_on_parent(self) -> None:
        """Places the dialog in the middle of its parent window."""
        try:
            parent = self.master
            x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _on_cancel(self) -> None:
        """Closes the dialog without a result."""
        self.result = None
        self.destroy()

    def _button_row(self, ok_text: str = "OK") -> ctk.CTkFrame:
        """
        Adds the standard OK/Cancel button row at the bottom.

        :param ok_text: Caption of the confirming button.
        :return: The frame holding the buttons.
        """
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=14, pady=12)
        ctk.CTkButton(row, text="Cancel", width=90, fg_color="gray40", hover_color="gray32",
                      command=self._on_cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text=ok_text, width=120, command=self._on_confirm).pack(side="right")
        return row

    def _on_confirm(self) -> None:  # pragma: no cover - overridden by subclasses
        """Validates the input and stores the result."""
        raise NotImplementedError


class MergeDialog(ModalDialog):
    """
    Lets the user pick, order and position the PDFs that are merged in.

    :param master: Parent window.
    :param initial_files: Files pre-filled into the list (e.g. from a file drop).
    :param current_page: 1-based number of the page currently shown.
    """

    def __init__(self, master, initial_files: Optional[Sequence[Path]] = None, current_page: int = 1) -> None:
        super().__init__(master, "Merge PDF files", (560, 470))
        self.files: List[Path] = [Path(item) for item in (initial_files or [])]
        self.current_page = current_page

        ctk.CTkLabel(
            self,
            text="Files are merged into the open document in the order shown below.",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", padx=16, pady=(14, 8))

        list_frame = ctk.CTkFrame(self)
        list_frame.pack(fill="both", expand=True, padx=16)

        self.listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            background="#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#ffffff",
            foreground="#f2f2f2" if ctk.get_appearance_mode() == "Dark" else "#111111",
            selectbackground="#3b8ed0",
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)

        buttons = ctk.CTkFrame(list_frame, fg_color="transparent")
        buttons.pack(side="right", fill="y", padx=8, pady=8)
        ctk.CTkButton(buttons, text="Add files...", width=110, command=self._add_files).pack(pady=3)
        ctk.CTkButton(buttons, text="Remove", width=110, command=self._remove_selected).pack(pady=3)
        ctk.CTkButton(buttons, text="Move up", width=110, command=lambda: self._move(-1)).pack(pady=3)
        ctk.CTkButton(buttons, text="Move down", width=110, command=lambda: self._move(1)).pack(pady=3)

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.pack(fill="x", padx=16, pady=(10, 0))

        ctk.CTkLabel(options, text="Insert at:").grid(row=0, column=0, sticky="w", pady=4)
        self.position = ctk.CTkOptionMenu(
            options,
            width=240,
            values=[
                "End of document",
                f"Before page {current_page}",
                f"After page {current_page}",
                "Beginning of document",
            ],
        )
        self.position.grid(row=0, column=1, sticky="w", padx=10)

        self.bookmarks = ctk.CTkCheckBox(options, text="Add a bookmark for each merged file")
        self.bookmarks.select()
        self.bookmarks.grid(row=1, column=0, columnspan=2, sticky="w", pady=6)

        self._button_row("Merge")
        self._refresh_list()

    def _refresh_list(self) -> None:
        """Redraws the file list."""
        self.listbox.delete(0, tk.END)
        for path in self.files:
            self.listbox.insert(tk.END, f"  {path.name}   -   {path.parent}")

    def _add_files(self) -> None:
        """Asks for more PDF files."""
        chosen = filedialog.askopenfilenames(
            parent=self, title="Select PDF files to merge", filetypes=PDF_FILETYPES
        )
        for item in chosen:
            self.files.append(Path(item))
        self._refresh_list()

    def _remove_selected(self) -> None:
        """Removes the highlighted entries."""
        for index in sorted(self.listbox.curselection(), reverse=True):
            del self.files[index]
        self._refresh_list()

    def _move(self, delta: int) -> None:
        """Moves the highlighted entry up or down."""
        selection = list(self.listbox.curselection())
        if len(selection) != 1:
            return
        index = selection[0]
        target = index + delta
        if not 0 <= target < len(self.files):
            return
        self.files[index], self.files[target] = self.files[target], self.files[index]
        self._refresh_list()
        self.listbox.selection_set(target)

    def _on_confirm(self) -> None:
        """Validates the selection and returns the merge parameters."""
        if not self.files:
            self.bell()
            return
        self.result = {
            "files": list(self.files),
            "position": self.position.get(),
            "bookmarks": bool(self.bookmarks.get()),
        }
        self.destroy()


class BlankPageDialog(ModalDialog):
    """
    Collects the parameters for inserting blank pages.

    :param master: Parent window.
    :param current_page: 1-based number of the page currently shown.
    """

    def __init__(self, master, current_page: int = 1) -> None:
        super().__init__(master, "Add blank page", (430, 330))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(18, 0))

        ctk.CTkLabel(body, text="Page size:").grid(row=0, column=0, sticky="w", pady=6)
        self.size = ctk.CTkOptionMenu(
            body, width=210, values=["Same as current page", *PAPER_SIZES.keys()]
        )
        self.size.grid(row=0, column=1, sticky="w", padx=10)

        ctk.CTkLabel(body, text="Orientation:").grid(row=1, column=0, sticky="w", pady=6)
        self.orientation = ctk.CTkSegmentedButton(body, values=["Portrait", "Landscape"], width=210)
        self.orientation.set("Portrait")
        self.orientation.grid(row=1, column=1, sticky="w", padx=10)

        ctk.CTkLabel(body, text="Position:").grid(row=2, column=0, sticky="w", pady=6)
        self.position = ctk.CTkOptionMenu(
            body,
            width=210,
            values=[
                f"After page {current_page}",
                f"Before page {current_page}",
                "End of document",
                "Beginning of document",
            ],
        )
        self.position.grid(row=2, column=1, sticky="w", padx=10)

        ctk.CTkLabel(body, text="How many:").grid(row=3, column=0, sticky="w", pady=6)
        self.count = ctk.CTkEntry(body, width=80)
        self.count.insert(0, "1")
        self.count.grid(row=3, column=1, sticky="w", padx=10)

        self.error = ctk.CTkLabel(body, text="", text_color="#e05252")
        self.error.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._button_row("Add page")

    def _on_confirm(self) -> None:
        """Validates the input and returns the page parameters."""
        try:
            count = int(self.count.get().strip())
        except ValueError:
            self.error.configure(text="'How many' must be a whole number.")
            return
        if not 1 <= count <= 100:
            self.error.configure(text="Choose between 1 and 100 pages.")
            return

        self.result = {
            "size": self.size.get(),
            "landscape": self.orientation.get() == "Landscape",
            "position": self.position.get(),
            "count": count,
        }
        self.destroy()


class TextBoxDialog(ModalDialog):
    """
    Asks for the content and style of a new text box.

    :param master: Parent window.
    :param default_size: Font size pre-filled in the dialog.
    """

    FONTS = ["Helvetica", "Times", "Courier"]

    def __init__(self, master, default_size: float = 11.0) -> None:
        super().__init__(master, "Insert text", (470, 400))
        self.color: Tuple[float, float, float] = (0.0, 0.0, 0.0)

        ctk.CTkLabel(self, text="Text to insert:").pack(anchor="w", padx=18, pady=(16, 4))
        self.textbox = ctk.CTkTextbox(self, height=140)
        self.textbox.pack(fill="both", expand=True, padx=18)

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.pack(fill="x", padx=18, pady=12)

        ctk.CTkLabel(options, text="Font:").grid(row=0, column=0, sticky="w", pady=4)
        self.font = ctk.CTkOptionMenu(options, width=140, values=self.FONTS)
        self.font.grid(row=0, column=1, sticky="w", padx=8)

        ctk.CTkLabel(options, text="Size:").grid(row=0, column=2, sticky="w", padx=(14, 0))
        self.size = ctk.CTkEntry(options, width=60)
        self.size.insert(0, f"{default_size:.0f}")
        self.size.grid(row=0, column=3, sticky="w", padx=8)

        self.style = ctk.CTkSegmentedButton(options, values=["Regular", "Bold", "Italic"], width=220)
        self.style.set("Regular")
        self.style.grid(row=1, column=0, columnspan=3, sticky="w", pady=8)

        self.color_button = ctk.CTkButton(
            options, text="Colour", width=90, fg_color="#111111", hover_color="#333333",
            command=self._pick_colour,
        )
        self.color_button.grid(row=1, column=3, sticky="w", pady=8)

        self.error = ctk.CTkLabel(self, text="", text_color="#e05252")
        self.error.pack(anchor="w", padx=18)

        self._button_row("Insert text")
        self.textbox.focus_set()

    def _pick_colour(self) -> None:
        """Opens the system colour picker."""
        chosen = colorchooser.askcolor(parent=self, title="Text colour")
        if chosen and chosen[0]:
            red, green, blue = chosen[0]
            self.color = (red / 255.0, green / 255.0, blue / 255.0)
            self.color_button.configure(fg_color=chosen[1])

    def _on_confirm(self) -> None:
        """Validates the input and returns the text parameters."""
        text = self.textbox.get("1.0", tk.END).strip()
        if not text:
            self.error.configure(text="Type the text you want to insert.")
            return
        try:
            size = float(self.size.get().strip())
        except ValueError:
            self.error.configure(text="Font size must be a number.")
            return
        if not 4 <= size <= 144:
            self.error.configure(text="Font size must be between 4 and 144.")
            return

        self.result = {
            "text": text,
            "font": self.font.get(),
            "size": size,
            "bold": self.style.get() == "Bold",
            "italic": self.style.get() == "Italic",
            "color": self.color,
        }
        self.destroy()


class PasswordDialog(ModalDialog):
    """
    Asks for the password of an encrypted PDF.

    :param master: Parent window.
    :param file_name: Name of the file being opened.
    """

    def __init__(self, master, file_name: str) -> None:
        super().__init__(master, "Password required", (420, 200))

        ctk.CTkLabel(
            self,
            text=f"'{file_name}' is protected.\nEnter its password to open it.",
            justify="left",
        ).pack(anchor="w", padx=18, pady=(20, 10))

        self.entry = ctk.CTkEntry(self, show="*", width=360)
        self.entry.pack(padx=18)
        self.entry.bind("<Return>", lambda _event: self._on_confirm())
        self.entry.focus_set()

        self._button_row("Open")

    def _on_confirm(self) -> None:
        """Returns the typed password."""
        self.result = self.entry.get()
        self.destroy()


class ShortcutsDialog(ModalDialog):
    """Read-only list of keyboard shortcuts and interaction hints."""

    SHORTCUTS: Dict[str, str] = {
        "Ctrl + O": "Open a PDF",
        "Ctrl + S / Ctrl + Shift + S": "Save / Save as",
        "Ctrl + Z / Ctrl + Y": "Undo / Redo",
        "Ctrl + M": "Merge PDF files into this document",
        "Ctrl + I": "Import a Word, Excel or PowerPoint document",
        "Ctrl + + / Ctrl + -": "Zoom in / out",
        "Page Down / Page Up": "Next / previous page",
        "Drag on the page": "Highlight words with the text tool",
        "Double / triple click": "Select a word / a whole line",
        "Backspace or Delete": "Delete the highlighted text",
        "Type any character": "Replace the highlighted text",
        "Enter": "Edit the highlighted text, or place the pending image",
        "Escape": "Cancel the selection or the image placement",
        "Drag and drop": (
            "Drop PDFs to open or merge, Office documents to convert, images to insert"
        ),
    }

    def __init__(self, master) -> None:
        super().__init__(master, "Keyboard shortcuts", (520, 470))

        frame = ctk.CTkScrollableFrame(self, label_text="Shortcuts and gestures")
        frame.pack(fill="both", expand=True, padx=14, pady=14)

        for index, (keys, description) in enumerate(self.SHORTCUTS.items()):
            ctk.CTkLabel(frame, text=keys, font=ctk.CTkFont(weight="bold"), anchor="w").grid(
                row=index, column=0, sticky="w", padx=6, pady=5
            )
            ctk.CTkLabel(frame, text=description, anchor="w", wraplength=300, justify="left").grid(
                row=index, column=1, sticky="w", padx=10, pady=5
            )

        ctk.CTkButton(self, text="Close", width=110, command=self._on_cancel).pack(pady=(0, 14))

    def _on_confirm(self) -> None:
        """Nothing to confirm; the dialog is informational."""
        self._on_cancel()


class TaskDialog(ModalDialog):
    """
    Runs a slow operation in a worker thread and shows its progress.

    The worker runs off the Tk thread and reports through a queue, so the window
    keeps repainting while a long export or compression is running.

    :param master: Parent window.
    :param title: Window title.
    :param message: Line shown above the progress bar.
    """

    def __init__(self, master, title: str, message: str) -> None:
        super().__init__(master, title, (440, 180))
        self._queue: "queue.Queue" = queue.Queue()
        self._error: Optional[BaseException] = None
        self._running = False

        # The work cannot be interrupted safely, so closing is refused while busy.
        self.protocol("WM_DELETE_WINDOW", self._ignore_close)
        self.unbind("<Escape>")

        ctk.CTkLabel(self, text=message, wraplength=390, justify="left").pack(
            anchor="w", padx=20, pady=(24, 10)
        )
        self.bar = ctk.CTkProgressBar(self, width=390)
        self.bar.set(0.0)
        self.bar.pack(padx=20)
        self.detail = ctk.CTkLabel(self, text="Starting...", anchor="w", text_color="gray60")
        self.detail.pack(anchor="w", padx=20, pady=(10, 0))

    def _ignore_close(self) -> None:
        """Swallows close requests while the worker is running."""
        if not self._running:
            self.destroy()

    def run(self, worker: Callable[[Callable[[float, str], None]], Any]) -> Any:
        """
        Executes ``worker`` and blocks until it finishes.

        :param worker: Callable receiving a ``progress(fraction, message)`` function.
        :return: Whatever the worker returned.
        :raises BaseException: Whatever the worker raised.
        """
        self._running = True
        thread = threading.Thread(target=self._work, args=(worker,), daemon=True)
        thread.start()
        self.after(60, self._drain)
        super().show()
        if self._error is not None:
            raise self._error
        return self.result

    def _work(self, worker) -> None:
        """Thread body: runs the worker and posts the outcome to the queue."""

        def report(fraction: float, message: str) -> None:
            self._queue.put(("progress", float(fraction), str(message)))

        try:
            outcome = worker(report)
            self._queue.put(("done", outcome, ""))
        except BaseException as error:  # Re-raised on the Tk thread by run().
            self._queue.put(("failed", error, ""))

    def _drain(self) -> None:
        """Applies queued progress updates and closes the dialog when finished."""
        finished = False
        try:
            while True:
                kind, payload, message = self._queue.get_nowait()
                if kind == "progress":
                    self.bar.set(max(0.0, min(1.0, payload)))
                    self.detail.configure(text=message)
                elif kind == "done":
                    self.result = payload
                    finished = True
                else:
                    self._error = payload
                    finished = True
        except queue.Empty:
            pass
        except Exception as error:
            # Never leave the dialog spinning if the update itself fails.
            self._error = error
            finished = True

        if finished:
            self._running = False
            self.destroy()
            return
        self.after(60, self._drain)

    def _on_confirm(self) -> None:
        """The dialog has no confirm button."""

    def show(self):
        """Use :meth:`run` instead; showing without a worker would hang."""
        raise RuntimeError("TaskDialog must be started with run(worker).")


class WordExportDialog(ModalDialog):
    """
    Collects the options for exporting the document to Word.

    :param master: Parent window.
    :param page_count: Number of pages in the document.
    :param current_page: 1-based number of the page currently shown.
    :param methods: Export methods available in this installation.
    :param has_selection: Whether several pages are selected in the sidebar.
    """

    METHOD_LABELS = {
        "layout": "Keep layout (recommended)",
        "text": "Text and images only",
    }

    def __init__(
        self,
        master,
        page_count: int,
        current_page: int,
        methods: Sequence[str],
        has_selection: bool = False,
    ) -> None:
        super().__init__(master, "Export as Word document", (480, 330))
        self.methods = list(methods)

        ctk.CTkLabel(
            self,
            text="The current state of the document is exported, including edits you have not saved.",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(18, 12))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="x", padx=18)

        choices = [f"All {page_count} pages", f"Current page ({current_page})"]
        if has_selection:
            choices.append("Pages selected in the sidebar")
        ctk.CTkLabel(body, text="Pages:").grid(row=0, column=0, sticky="w", pady=6)
        self.pages = ctk.CTkOptionMenu(body, width=260, values=choices)
        self.pages.grid(row=0, column=1, sticky="w", padx=10)

        values = [self.METHOD_LABELS[name] for name in self.methods] or ["Not available"]
        ctk.CTkLabel(body, text="Fidelity:").grid(row=1, column=0, sticky="w", pady=6)
        self.method = ctk.CTkOptionMenu(body, width=260, values=values)
        self.method.grid(row=1, column=1, sticky="w", padx=10)
        self._method_by_label = {self.METHOD_LABELS[name]: name for name in self.methods}

        if "layout" in self.methods:
            note = (
                "Keeping the layout rebuilds paragraphs, tables and columns. "
                "The simpler option writes styled text and the pictures only."
            )
        elif getattr(sys, "frozen", False):
            note = (
                "This packaged build exports styled text and the page images. "
                "For layout-preserving export, run the editor from Python with "
                "pdf2docx installed, or rebuild it with: "
                "python build_exe.py --with-layout-export"
            )
        else:
            note = "Install the pdf2docx package to also keep the page layout."
        ctk.CTkLabel(self, text=note, wraplength=430, justify="left", text_color="gray60").pack(
            anchor="w", padx=18, pady=(14, 0)
        )

        self._button_row("Export")

    def _on_confirm(self) -> None:
        """Returns the chosen page range and method."""
        if not self.methods:
            self.bell()
            return
        self.result = {
            "pages": self.pages.get(),
            "method": self._method_by_label.get(self.method.get(), self.methods[0]),
        }
        self.destroy()


class CompressDialog(ModalDialog):
    """
    Lets the user pick a compression level.

    :param master: Parent window.
    :param current_size: Size of the document today, already formatted.
    """

    def __init__(self, master, current_size: str = "") -> None:
        super().__init__(master, "Compress PDF", (500, 430))
        self.level = tk.StringVar(value="medium")

        heading = "Choose how hard the document should be squeezed."
        if current_size:
            heading += f"  Current size: {current_size}."
        ctk.CTkLabel(self, text=heading, wraplength=450, justify="left").pack(
            anchor="w", padx=18, pady=(18, 10)
        )

        for key, profile in PROFILES.items():
            block = ctk.CTkFrame(self, fg_color="transparent")
            block.pack(fill="x", padx=18, pady=4)
            ctk.CTkRadioButton(
                block,
                text=profile.label,
                variable=self.level,
                value=key,
                font=ctk.CTkFont(weight="bold"),
            ).pack(anchor="w")
            ctk.CTkLabel(
                block,
                text=profile.description,
                wraplength=400,
                justify="left",
                anchor="w",
                text_color="gray60",
            ).pack(anchor="w", padx=26)

        self.lossless = ctk.CTkCheckBox(
            self, text="Never touch images (structure only, completely lossless)"
        )
        self.lossless.pack(anchor="w", padx=18, pady=(12, 0))

        ctk.CTkLabel(
            self,
            text="Text and vector graphics always stay sharp; only bitmap images are recompressed.",
            wraplength=450,
            justify="left",
            text_color="gray60",
        ).pack(anchor="w", padx=18, pady=(8, 0))

        self._button_row("Compress")

    def _on_confirm(self) -> None:
        """Returns the chosen level and lossless flag."""
        self.result = {
            "level": self.level.get(),
            "downsample_images": not bool(self.lossless.get()),
        }
        self.destroy()


class ImportDialog(ModalDialog):
    """
    Collects the options for converting an Office document into a PDF.

    :param master: Parent window.
    :param source: Document that will be converted.
    :param destination: Proposed output path.
    :param backends: Converters available for this file, best first.
    :param can_merge: Whether a document is open to merge the result into.
    """

    BACKEND_LABELS = {
        "office": "Microsoft Office (best quality)",
        "builtin": "Built-in converter (no Office needed)",
    }

    AFTER_OPEN = "Open the PDF in the editor"
    AFTER_MERGE = "Merge the PDF into the open document"
    AFTER_NOTHING = "Just save the PDF"

    def __init__(
        self,
        master,
        source: Path,
        destination: Path,
        backends: Sequence[str],
        can_merge: bool = False,
    ) -> None:
        super().__init__(master, "Import document", (560, 350))
        self.backends = list(backends)
        self.destination = Path(destination)

        ctk.CTkLabel(
            self,
            text=f"Converting '{source.name}' to PDF.",
            font=ctk.CTkFont(weight="bold"),
            wraplength=500,
            justify="left",
        ).pack(anchor="w", padx=18, pady=(18, 10))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="x", padx=18)

        ctk.CTkLabel(body, text="Converter:").grid(row=0, column=0, sticky="w", pady=6)
        values = [self.BACKEND_LABELS[name] for name in self.backends] or ["Not available"]
        self.backend = ctk.CTkOptionMenu(body, width=320, values=values)
        self.backend.grid(row=0, column=1, columnspan=2, sticky="w", padx=10)
        self._backend_by_label = {self.BACKEND_LABELS[name]: name for name in self.backends}

        ctk.CTkLabel(body, text="Save as:").grid(row=1, column=0, sticky="w", pady=6)
        self.path_entry = ctk.CTkEntry(body, width=230)
        self.path_entry.insert(0, str(self.destination))
        self.path_entry.grid(row=1, column=1, sticky="w", padx=10)
        ctk.CTkButton(body, text="Change...", width=80, command=self._browse).grid(
            row=1, column=2, sticky="w"
        )

        ctk.CTkLabel(body, text="Afterwards:").grid(row=2, column=0, sticky="w", pady=6)
        choices = [self.AFTER_OPEN]
        if can_merge:
            choices.append(self.AFTER_MERGE)
        choices.append(self.AFTER_NOTHING)
        self.after_action = ctk.CTkOptionMenu(body, width=320, values=choices)
        self.after_action.grid(row=2, column=1, columnspan=2, sticky="w", padx=10)

        note = (
            "Microsoft Office produces output identical to its own 'Save as PDF'. "
            "The built-in converter keeps the text, tables and pictures, and "
            "approximates the layout."
            if "office" in self.backends
            else "Microsoft Office was not found, so the built-in converter will be used."
        )
        ctk.CTkLabel(self, text=note, wraplength=500, justify="left", text_color="gray60").pack(
            anchor="w", padx=18, pady=(14, 0)
        )

        self.error = ctk.CTkLabel(self, text="", text_color="#e05252", wraplength=500, justify="left")
        self.error.pack(anchor="w", padx=18, pady=(6, 0))

        self._button_row("Convert")

    def _browse(self) -> None:
        """Asks for a different output location."""
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Save the converted PDF as",
            defaultextension=".pdf",
            initialfile=self.destination.name,
            initialdir=str(self.destination.parent),
            filetypes=PDF_FILETYPES,
        )
        if chosen:
            self.destination = Path(chosen)
            self.path_entry.delete(0, tk.END)
            self.path_entry.insert(0, chosen)

    def _on_confirm(self) -> None:
        """Validates the target path and returns the conversion options."""
        if not self.backends:
            self.bell()
            return

        text = self.path_entry.get().strip()
        if not text:
            self.error.configure(text="Choose where the PDF should be saved.")
            return
        target = Path(text)
        if not target.parent.exists():
            self.error.configure(text=f"The folder '{target.parent}' does not exist.")
            return

        self.result = {
            "destination": target,
            "backend": self._backend_by_label.get(self.backend.get(), self.backends[0]),
            "after": self.after_action.get(),
        }
        self.destroy()


__all__ = [
    "BlankPageDialog",
    "CompressDialog",
    "ImportDialog",
    "DOCX_FILETYPES",
    "MergeDialog",
    "ModalDialog",
    "PDF_FILETYPES",
    "PasswordDialog",
    "ShortcutsDialog",
    "TaskDialog",
    "TextBoxDialog",
    "WordExportDialog",
]
