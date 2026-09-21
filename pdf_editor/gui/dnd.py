"""
Drag-and-drop support for the editor window.

Native file drops need the optional ``tkinterdnd2`` package.  The editor works
without it (files can always be opened through the toolbar or the File menu), so
every helper here degrades gracefully when the package is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

import customtkinter as ctk

try:  # pragma: no cover - depends on the local installation
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without tkinterdnd2
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


def dnd_available() -> bool:
    """
    Reports whether native drag-and-drop can be used.

    :return: True when ``tkinterdnd2`` was imported successfully.
    """
    return DND_AVAILABLE


class DragDropCTk(ctk.CTk):
    """
    A ``customtkinter`` root window with drag-and-drop enabled when possible.

    The class mixes the ``tkinterdnd2`` wrapper into the CustomTkinter root so
    that any child widget can become a drop target.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dnd_enabled = False
        if not DND_AVAILABLE:
            return
        try:
            # Load the tkdnd Tcl package into this interpreter.  Importing
            # tkinterdnd2 already teaches every child widget the drop-target
            # methods; the root window needs them bound explicitly.
            self.TkdndVersion = TkinterDnD._require(self)
            for name, member in vars(TkinterDnD.DnDWrapper).items():
                if name.startswith("__"):
                    continue
                setattr(self, name, member.__get__(self) if callable(member) else member)
            self.dnd_enabled = True
        except Exception:
            self.dnd_enabled = False


def _split_payload(data: str) -> List[str]:
    """
    Splits a drop payload into single entries.

    The payload looks like a Tcl list: entries are separated by spaces and any
    entry containing a space is wrapped in braces.  It is parsed by hand rather
    than with ``Tcl.splitlist`` because Windows paths are full of backslashes,
    which Tcl would read as escape sequences ("C:\\temp\\new.pdf" would lose its
    tab and newline).

    :param data: Raw event payload.
    :return: The entries, in drop order.
    """
    items: List[str] = []
    current = ""
    depth = 0

    for char in data:
        if char == "{":
            depth += 1
            if depth == 1:
                continue
        elif char == "}":
            depth -= 1
            if depth == 0:
                items.append(current)
                current = ""
                continue
        if depth == 0 and char.isspace():
            if current:
                items.append(current)
                current = ""
            continue
        current += char

    if current:
        items.append(current)
    return items


def parse_drop_data(widget, data: str) -> List[Path]:
    """
    Converts the raw payload of a ``<<Drop>>`` event into file paths.

    :param widget: Any Tk widget (used as a fallback Tcl interpreter).
    :param data: Raw event payload.
    :return: List of existing file paths, in drop order.
    """
    if not data:
        return []

    paths = [Path(item.strip()) for item in _split_payload(data) if item.strip()]
    existing = [path for path in paths if path.exists()]
    if existing:
        return existing

    # Nothing matched: let Tcl have a go, in case of an exotic payload format.
    try:
        return [Path(str(item)) for item in widget.tk.splitlist(data) if Path(str(item)).exists()]
    except Exception:
        return []


def register_drop_target(widget, callback: Callable[[List[Path]], None]) -> bool:
    """
    Makes a widget accept dropped files.

    :param widget: Widget that should receive drops.
    :param callback: Called with the list of dropped paths.
    :return: True when the widget was registered, False when DnD is unavailable.
    """
    if not DND_AVAILABLE:
        return False

    target = getattr(widget, "_canvas", widget)  # CTk widgets wrap a Tk canvas.
    try:
        target.drop_target_register(DND_FILES)
        target.dnd_bind("<<Drop>>", lambda event: callback(parse_drop_data(target, event.data)))
        return True
    except Exception:
        return False


def create_root(fallback_message: Optional[List[str]] = None) -> ctk.CTk:
    """
    Creates the application root window, with drag-and-drop when available.

    :param fallback_message: Optional list that receives a hint for the user when
        drag-and-drop could not be enabled.
    :return: The root window.
    """
    root = DragDropCTk()
    if not root.dnd_enabled and fallback_message is not None:
        fallback_message.append(
            "Drag-and-drop is disabled: install 'tkinterdnd2' to drop files onto the window."
        )
    return root


__all__ = [
    "DND_AVAILABLE",
    "DragDropCTk",
    "create_root",
    "dnd_available",
    "parse_drop_data",
    "register_drop_target",
]
