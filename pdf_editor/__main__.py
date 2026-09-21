"""
Package entry point.

* ``python -m pdf_editor``                 -> opens the visual editor.
* ``python -m pdf_editor document.pdf``    -> opens the editor with that file.
* ``python -m pdf_editor --classic-gui``   -> opens the earlier tabbed/Adobe-style windows.
* ``python -m pdf_editor <command> ...``   -> runs the command line interface.
"""

import sys
from pathlib import Path

from pdf_editor.cli import main as cli_main


def entry() -> None:
    """Chooses between the editor window, the classic windows and the CLI."""
    arguments = sys.argv[1:]

    if arguments and arguments[0] in ("--classic-gui", "--legacy-gui"):
        from pdf_editor.adobe_gui import launch_adobe_gui

        launch_adobe_gui()
        return

    if arguments and arguments[0] == "--tabs-gui":
        from pdf_editor.gui_app import launch_gui

        launch_gui()
        return

    # No arguments, or a single existing PDF path: start the visual editor.
    if not arguments or (len(arguments) == 1 and Path(arguments[0]).suffix.lower() == ".pdf"):
        from pdf_editor.gui import launch_studio

        launch_studio(arguments[0] if arguments else None)
        return

    sys.exit(cli_main())


if __name__ == "__main__":
    entry()
