# Python PDF Editor (Desktop Editor, Classic Windows & CLI)

A modular Python application for editing PDF documents: edit the text that is already
in the file, insert and arrange images, manage pages, merge documents, import Word,
Excel and PowerPoint files, compress the result and export it to Word - from a visual
desktop editor or from the command line.

---

## Quick start

```bash
pip install -r requirements.txt
python -m pdf_editor                 # opens the visual editor
python -m pdf_editor report.pdf      # opens the editor with a document loaded
python -m pdf_editor merge -i a.pdf b.pdf -o out.pdf    # command line mode
```

### Just open the app

Two ways to start it without typing anything:

* **`PDF Editor.bat`** in this folder. Double-click it and the editor opens; no console
  window appears. It needs Python and the packages from `requirements.txt`.
* **`dist/PDF_Editor/PDF_Editor.exe`**, the standalone build. Needs nothing installed.
  Rebuild it with `python build_exe.py` after changing the code, otherwise it keeps
  launching the version it was built from. The standard build exports Word documents
  with the text backend; `python build_exe.py --with-layout-export` also bundles
  pdf2docx and OpenCV for layout-preserving export, which adds about 130 MB.

Other entry points:

| Command | What it starts |
| --- | --- |
| `python -m pdf_editor` | **PDF Editor Studio**, the visual editor described below |
| `python -m pdf_editor --classic-gui` | The earlier Adobe-style window |
| `python -m pdf_editor --tabs-gui` | The earlier tabbed utility window |
| `python -m pdf_editor <command>` | The command line interface |
| `dist/PDF_Editor/PDF_Editor.exe` | Standalone Windows build (no Python needed) |

---

## The visual editor

The window is split into a page sidebar, the page view and a context panel, with a
menu bar and two toolbars above and a status bar below.

### 1. Text editing

* **Highlight** — drag across words with the *Select & Edit Text* tool. Double-click
  selects a word, triple-click selects a line.
* **Delete** — press `Backspace` or `Delete`, or use *Delete text* in the panel.
* **Replace** — just start typing: an inline editor opens over the selection. `Enter`
  applies, `Escape` cancels. Longer replacements can also be typed in the side panel.
* **Reflow** — the whole paragraph is re-laid out after the edit, so the words after
  the change move naturally instead of overlapping or leaving a hole. Switch the
  *Reflow paragraph* checkbox off for form-like documents where nothing may move.
* **Formatting is kept** — font family, size, weight, slant, colour and paragraph
  alignment (including justified text) are read from the original characters and
  re-applied. Bold or coloured runs inside the paragraph survive an edit of the text
  around them. When the new text does not fit, the paragraph first grows into the
  free space below and is only then scaled down (never below 62%).
* **Annotate** — *Highlight selection* adds a real PDF highlight annotation in one of
  four colours; *Redact selection* removes the text permanently.
* **Find** — the panel searches the whole document and selects the next match.

### 2. Images

* *Insert Image* (toolbar, menu, or drop a PNG/JPG on the window) places the picture
  in the middle of the page as a live preview.
* Drag it to move, pull any of the eight white grips to resize, or type exact
  coordinates in the panel. *Keep aspect ratio* locks the proportions.
* `Enter` or *Place image on page* commits it; `Escape` cancels.
* Already placed images can be picked up again: click one with the *Insert Image*
  tool to move, resize, replace or delete it.

### 3. Page management

Available from the *Page* menu, the toolbar, and the right-click menu of the sidebar:

* Delete the selected pages (`Ctrl`-click and `Shift`-click select several).
* Add blank pages — any standard paper size, portrait or landscape, or matching the
  current page — before, after, or at either end of the document.
* Duplicate pages, move pages up and down, rotate left and right.
* Merge other PDFs at a chosen position, keeping their bookmarks.
* Extract the selected pages into a new file.

### 4. Import documents and convert to PDF

*File > Import document* (Ctrl+I, or the **Import Doc** button) converts a Word,
Excel or PowerPoint file into a PDF and hands it straight to the editor. Dropping
one of those files on the window does the same thing.

Two converters, picked automatically:

* **Microsoft Office** is used when Office is installed. The result is identical to
  its own *Save as PDF*, and it also reads the legacy `.doc`, `.xls` and `.ppt`
  formats as well as `.rtf` and OpenDocument files.
* **The built-in converter** needs no Office at all. It reads the file with
  `python-docx`, `openpyxl` or `python-pptx` and draws the PDF with ReportLab:
  headings, bold, italic, colours, bullet lists, tables and pictures for Word; one
  page per sheet for Excel; one page per slide, at the slide's own size, for
  PowerPoint. Layout is a close approximation rather than a copy.

After converting you can open the PDF, merge it into the document you are already
editing, or just keep the file.

### 5. Export as Word

*File > Export as Word (.docx)* converts the document as it stands, including edits
that were never saved.

* **Keep layout** uses `pdf2docx` and rebuilds paragraphs, tables, columns and
  images, so the Word file looks like the PDF.
* **Text and images only** uses `python-docx` and writes styled text plus the
  pictures. Fonts, sizes, bold, italics and colours are kept; the exact page
  geometry is not. This is the fallback when `pdf2docx` is not installed.
* You can export the whole document, the current page, or the pages selected in
  the sidebar. Progress is shown while the conversion runs.
* The packaged `.exe` ships with the text backend only, to keep it small. Run the
  editor from Python, or rebuild with `--with-layout-export`, for the layout one.

### 6. Compress PDF

*File > Compress PDF* writes a smaller copy. Text and vector graphics are never
rasterised, so they stay perfectly sharp; only bitmap images are recompressed.

| Level | What it does | Typical saving on a photo-heavy file |
| --- | --- | --- |
| Low | Recompresses images, keeps their resolution | around 20% |
| Medium | Resamples images above 200 dpi to 100 dpi, subsets fonts | around 70% |
| High | Resamples images down to screen resolution | around 80% |

Tick *Never touch images* for a completely lossless pass that only rebuilds the
file structure. An image is only replaced when the new version is genuinely
smaller, and if the whole document cannot be improved the original is kept and the
editor says so. Metadata, bookmarks and annotations survive every level.

### 7. Files

* Drag and drop: drop PDFs to open or merge them, drop images to insert them.
  (Needs `tkinterdnd2`; everything stays reachable from the menus without it.)
* Password-protected files prompt for their password.
* `Save` writes back to the original file, `Save As` exports anywhere. Writing goes
  through a temporary file, so an interrupted save cannot destroy the previous
  version. Metadata, bookmarks, annotations and fonts are preserved.
* Every change is undoable (`Ctrl+Z` / `Ctrl+Y`), up to 30 steps.

### Keyboard shortcuts

| Keys | Action |
| --- | --- |
| `Ctrl+O` / `Ctrl+S` / `Ctrl+Shift+S` | Open / Save / Save As |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+M` | Merge PDFs |
| `Ctrl+I` | Import a Word, Excel or PowerPoint document |
| `Ctrl+ +` / `Ctrl+ -` / `Ctrl+0` | Zoom in / out / fit page |
| `Page Down` / `Page Up` | Next / previous page |
| `Backspace`, `Delete` | Delete the highlighted text |
| any character | Replace the highlighted text |
| `Enter` / `Escape` | Apply / cancel the current action |
| `F1` | Shortcut reference |

---

## The hosted web version

`webapp/` is a browser front end for the same engine, meant for a small group of
people sharing one link. It runs anywhere Python runs, including a free container
host, and works on a phone.

```bash
pip install -r webapp/requirements.txt
APP_PASSWORD="a good password" uvicorn webapp.server:app --port 8000
```

Then open `http://localhost:8000` and sign in with that password.

* **Same features**: select text by dragging across words, replace, delete,
  highlight, redact, find, place and move images, manage and merge pages, import
  Office documents, compress, export to Word, undo and redo.
* **One password** for everybody, read from the `APP_PASSWORD` environment
  variable. Without it the service refuses to sign anyone in, so a misconfigured
  deployment cannot end up open to the internet.
* **Separate visitors**: each gets an isolated session with its own temporary
  folder. Documents are deleted on sign-out or after an hour of inactivity.
* **Small by design**: uploads are capped, the undo history is far shorter than on
  the desktop, and the number of concurrent documents is limited. All of it is
  tunable through environment variables listed in `webapp/config.py`.

### Deploying it

`webapp/Dockerfile` builds a container that serves the app on port 7860. To put it
on a free Hugging Face Space:

```bash
pip install huggingface_hub
huggingface-cli login
python webapp/deploy_space.py --space yourname/pdf-editor --password "a good password"
```

The script stages only the engine and the web layer, writes the Space card,
uploads them, and stores the password as a Space secret rather than in the code.

The container has no Microsoft Office, so importing Word, Excel and PowerPoint
uses the built-in converter, and Word export uses the python-docx backend.
Everything else behaves exactly as it does on the desktop.

## Project layout

```
pdf_editor/
  core/
    session.py     Open document + undo/redo history + safe saving
    text_ops.py    Selection, style probing, reflowing replace/delete, highlights
    fonts.py       Font analysis and resolution (embedded fonts, base-14 fallback)
    image_ops.py   Insert, move, resize, replace, delete images
    page_ops.py    Delete, add, duplicate, reorder, rotate, merge, extract pages
    doc_import.py  Import Word/Excel/PowerPoint and convert them to PDF
    word_export.py Export to .docx (pdf2docx layout, python-docx fallback)
    compress.py    Three-level compression with image resampling
    editor.py      File-to-file page operations used by the CLI
    merger.py      File-to-file merging used by the CLI
    splitter.py    File-to-file splitting used by the CLI
    text_editor.py File-to-file find/replace/redact used by the CLI
    utils.py       Validation, page range parsing, exception types
  gui/
    app.py         Main window and controller
    page_canvas.py Interactive page view (selection, inline editing, image grips)
    thumbnail_bar.py  Page sidebar with the page commands
    dialogs.py     Merge, blank page, text box and password dialogs
    dnd.py         Optional drag-and-drop support
  cli.py           Command line interface
webapp/
  server.py      FastAPI routes over the same core package
  sessions.py    One isolated workspace per visitor, swept when idle
  config.py      Limits and the shared password, from the environment
  static/        The browser interface (no build step)
  Dockerfile     Container image for hosting
  deploy_space.py  One command deploy to a Hugging Face Space
  adobe_gui.py     Earlier Adobe-style window
  gui_app.py       Earlier tabbed window
tests/             Unit tests for every core module
```

### How a text edit works

1. The affected paragraph is read down to character level, keeping the style of
   every character (`text_ops.extract_block`).
2. The replacement is spliced into that character stream, which is then re-wrapped
   with the real metrics of each font (`_tokenize`, `_wrap`, `_place_tokens`).
3. The original text is removed with a redaction that leaves images and vector
   graphics untouched, and the new layout is drawn — standard fonts by name, so
   nothing extra is embedded, and embedded fonts through `TextWriter`.

---

## Using the core API

```python
from pdf_editor.core.session import EditSession
from pdf_editor.core import page_ops, text_ops

session = EditSession.open("report.pdf")

page = session.page(0)
selection = text_ops.select_words_in_rect(page, fitz.Rect(50, 170, 200, 190))

with session.edit("Replace heading"):          # one undoable step
    text_ops.replace_selection(page, selection, "Quarterly Review")

with session.edit("Merge appendix"):
    page_ops.merge_documents(session.document, ["appendix.pdf"])

session.save("report-edited.pdf")
session.close()
```

Every operation raises a subclass of `PDFEditorError` (`FileNotFoundPDFError`,
`CorruptedPDFError`, `EncryptedPDFError`, `InvalidPageRangeError`,
`UnsupportedOperationError`, `ImageOperationError`, `TextEditError`) with a message
meant to be shown to a user, and `session.edit` rolls the document back if the
operation fails halfway.

---

## CLI reference

```bash
python -m pdf_editor info -i document.pdf
python -m pdf_editor merge -i file1.pdf file2.pdf -o merged.pdf
python -m pdf_editor split -i doc.pdf -o out_dir --ranges "1-3,4-6"
python -m pdf_editor split -i doc.pdf -o out_dir --by-bookmarks
python -m pdf_editor rotate -i doc.pdf -o output.pdf --angle 90 --pages "1,3"
python -m pdf_editor reorder -i doc.pdf -o output.pdf --order "3,1,2,4"
python -m pdf_editor delete -i doc.pdf -o output.pdf --pages "2,4"
python -m pdf_editor extract -i doc.pdf -o output.pdf --pages "1-3"
python -m pdf_editor replace-text -i report.pdf -o approved.pdf --old "DRAFT" --new "APPROVED"
python -m pdf_editor add-text -i report.pdf -o stamped.pdf --text "APPROVED" --page 1 -x 100 -y 50
python -m pdf_editor redact-text -i report.pdf -o redacted.pdf --target "SECRET-KEY"
python -m pdf_editor import-doc -i report.docx -o report.pdf
python -m pdf_editor import-doc -i budget.xlsx --backend builtin
python -m pdf_editor to-word -i report.pdf -o report.docx --pages "1-3"
python -m pdf_editor compress -i scan.pdf -o scan-small.pdf --level high
python -m pdf_editor compress -i scan.pdf -o scan-small.pdf --level low --keep-images
```

---

## Tests and packaging

```bash
python -m pytest -q          # unit tests for the core modules
python build_exe.py          # rebuild dist/PDF_Editor.exe
python generate_samples.py   # regenerate the files in samples/
```

---

## Known limits

* Editing needs a text layer. Scanned pages are images; the editor says so instead of
  guessing, and OCR is out of scope.
* Reflow works paragraph by paragraph. A selection spanning several paragraphs edits
  the first one and reports it; the rest of the page is never touched.
* Text inside tables and text boxes reflows within its own block, so a very long
  replacement in a narrow table cell is scaled down rather than widening the cell.
* Deleting an image removes every placement of that same picture on the page.
* Documents whose fonts are embedded as subsets and are not licensed for editing fall
  back to the closest standard font; the editor never fails because of it.
* Word export is a conversion, not a round trip. Complex multi-column pages,
  footnotes and form fields will not come out identical, and a scanned page becomes
  a picture in the Word file because there is no text to carry over.
* Compression cannot shrink a file that is already tight, and pages that are pure
  text usually gain little because text is already stored compactly.
* The built-in Office converter approximates the layout. Headers and footers,
  floating text boxes, charts, SmartArt and slide animations are not reproduced.
  Install Microsoft Office for output identical to its own PDF export.
* Legacy `.doc`, `.xls` and `.ppt` files can only be converted through Office; the
  Python readers only understand the modern zipped formats.
* In files whose fonts PyMuPDF embeds itself, extracted spaces can come back as
  non-breaking spaces. That is how those files already behave before editing.
