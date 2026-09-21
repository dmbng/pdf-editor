"""
Graphical interface of the PDF editor.

The window is assembled from four independent pieces:

* :mod:`pdf_editor.gui.app` - the main window and controller.
* :mod:`pdf_editor.gui.page_canvas` - the interactive page view.
* :mod:`pdf_editor.gui.thumbnail_bar` - the page sidebar.
* :mod:`pdf_editor.gui.dialogs` - modal dialogs.
"""

from pdf_editor.gui.app import PDFEditorStudio, launch_studio

__all__ = ["PDFEditorStudio", "launch_studio"]
