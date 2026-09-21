# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('customtkinter')
datas += collect_data_files('tkinterdnd2')
datas += collect_data_files('docx')
datas += collect_data_files('reportlab')
datas += collect_data_files('pptx')


a = Analysis(
    ['C:/Users/Jian/Desktop/pdf editor/pdf_editor/__main__.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['tkinterdnd2', 'pdf_editor.gui', 'pdf_editor.gui.app', 'docx', 'pdf2docx', 'pdf_editor.core.word_export', 'pdf_editor.core.compress', 'pdf_editor.core.doc_import', 'reportlab', 'openpyxl', 'pptx', 'pythoncom', 'pywintypes', 'win32com.client', 'pymupdf', 'fitz', 'pypdf', 'reportlab', 'PIL', 'PIL.Image', 'PIL.ImageTk'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchvision', 'tensorflow', 'scipy', 'matplotlib', 'pandas', 'jupyter', 'notebook', 'cv2', 'pdf2docx'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDF_Editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PDF_Editor',
)
