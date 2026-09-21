"""
Build script to compile PDF Editor desktop application into a standalone Windows executable (.exe).

By default the build stays small (around 170 MB) and Word export uses the
python-docx backend, which writes styled text and the page images.

Pass --with-layout-export to also bundle pdf2docx and OpenCV, which adds
layout-preserving Word export at the cost of roughly 130 MB extra:

    python build_exe.py --with-layout-export
"""

import sys
import subprocess
from pathlib import Path


def build(with_layout_export: bool = False):
    """
    Compiles the editor into dist/PDF_Editor.

    :param with_layout_export: Bundle pdf2docx and OpenCV so the packaged app can
        also export Word documents with the original page layout.
    """
    root_dir = Path(__file__).parent.resolve()
    main_script = root_dir / "pdf_editor" / "__main__.py"

    print("Building standalone executable PDF_Editor.exe...")
    if with_layout_export:
        print("  including pdf2docx and OpenCV for layout-preserving Word export")

    # PyInstaller command arguments
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=PDF_Editor",
        "--onedir",
        "--noconsole",
        "--clean",
        "-y",
        "--collect-data=customtkinter",
        "--collect-data=tkinterdnd2",
        "--hidden-import=tkinterdnd2",
        "--hidden-import=pdf_editor.gui",
        "--hidden-import=pdf_editor.gui.app",
        "--collect-data=docx",
        "--hidden-import=docx",
        "--hidden-import=pdf2docx",
        "--hidden-import=pdf_editor.core.word_export",
        "--hidden-import=pdf_editor.core.compress",
        "--hidden-import=pdf_editor.core.doc_import",
        "--collect-data=reportlab",
        "--hidden-import=reportlab",
        "--hidden-import=openpyxl",
        "--collect-data=pptx",
        "--hidden-import=pptx",
        "--hidden-import=pythoncom",
        "--hidden-import=pywintypes",
        "--hidden-import=win32com.client",
        "--hidden-import=pymupdf",
        "--hidden-import=fitz",
        "--hidden-import=pypdf",
        "--hidden-import=reportlab",
        "--hidden-import=PIL",
        "--hidden-import=PIL.Image",
        "--hidden-import=PIL.ImageTk",
        "--exclude-module=torch",
        "--exclude-module=torchvision",
        "--exclude-module=tensorflow",
        "--exclude-module=scipy",
        "--exclude-module=matplotlib",
        "--exclude-module=pandas",
        "--exclude-module=jupyter",
        "--exclude-module=notebook",
        str(main_script)
    ]

    if with_layout_export:
        cmd[-1:-1] = [
            "--collect-submodules=pdf2docx",
            "--hidden-import=cv2",
        ]
    else:
        # OpenCV alone is ~130 MB; leave it out unless layout export is wanted.
        cmd[-1:-1] = ["--exclude-module=cv2", "--exclude-module=pdf2docx"]

    print(f"Executing: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(root_dir))

    if res.returncode == 0:
        exe_path = root_dir / "dist" / "PDF_Editor.exe"
        print("\n" + "=" * 60)
        print(f"SUCCESS: Executable successfully created at:")
        print(f"-> {exe_path}")
        print("=" * 60 + "\n")
    else:
        print("\nBUILD FAILED with exit code", res.returncode)
        sys.exit(res.returncode)


if __name__ == "__main__":
    build(with_layout_export="--with-layout-export" in sys.argv)
