"""
Web front end for the PDF editor.

This module is only a transport layer: every operation is performed by the same
:mod:`pdf_editor.core` package the desktop application uses.  Requests are mapped
onto core calls, the document lives in a per-visitor session, and rendered pages
are sent to the browser as PNG images.

Run it locally with::

    uvicorn webapp.server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

import pymupdf as fitz
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pdf_editor.core import compress, doc_import, image_ops, page_ops, text_ops, word_export
from pdf_editor.core.fonts import SpanStyle
from pdf_editor.core.session import EditSession
from pdf_editor.core.text_ops import TextSelection
from pdf_editor.core.utils import EncryptedPDFError, PDFEditorError
from webapp.config import load_settings
from webapp.sessions import SessionLimitReached, SessionStore, WebSession

LOGGER = logging.getLogger("pdf_editor.web")

SETTINGS = load_settings()
STORE = SessionStore(SETTINGS)

COOKIE_NAME = "pdf_editor_session"
STATIC_DIR = Path(__file__).parent / "static"

# Rendering limits, so one visitor cannot ask for a gigantic bitmap.
MIN_ZOOM = 0.25
MAX_ZOOM = 3.0
THUMBNAIL_WIDTH = 150


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Starts the idle-session sweeper and shuts every session down on exit."""
    stop = asyncio.Event()

    async def sweeper() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=SETTINGS.cleanup_interval_seconds)
            except asyncio.TimeoutError:
                removed = await asyncio.to_thread(STORE.sweep)
                if removed:
                    LOGGER.info("Swept %s idle session(s)", removed)

    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        await asyncio.to_thread(STORE.close_all)


app = FastAPI(title=SETTINGS.app_title, lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def _sign(identifier: str) -> str:
    """
    Signs a session identifier so the cookie cannot be forged.

    :param identifier: Raw session identifier.
    :return: ``identifier.signature``.
    """
    digest = hmac.new(
        SETTINGS.secret_key.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{identifier}.{digest}"


def _unsign(value: Optional[str]) -> Optional[str]:
    """
    Checks a cookie value and returns the identifier it carries.

    :param value: Cookie value, or ``None``.
    :return: The identifier, or ``None`` when the signature does not match.
    """
    if not value or "." not in value:
        return None
    identifier, _, signature = value.rpartition(".")
    if not identifier or not hmac.compare_digest(_sign(identifier), f"{identifier}.{signature}"):
        return None
    return identifier


def current_session(request: Request) -> WebSession:
    """
    Resolves the session of the caller.

    :param request: Incoming request.
    :return: The caller's :class:`WebSession`.
    :raises HTTPException: 401 when there is no valid session.
    """
    session = STORE.get(_unsign(request.cookies.get(COOKIE_NAME)))
    if session is None:
        raise HTTPException(status_code=401, detail="Your session has ended. Sign in again.")
    return session


def require_document(session: WebSession = Depends(current_session)) -> WebSession:
    """
    Resolves the session and insists that a document is open.

    :param session: The caller's session.
    :return: The same session.
    :raises HTTPException: 409 when no document has been opened yet.
    """
    if not session.has_document:
        raise HTTPException(status_code=409, detail="Open a PDF first.")
    return session


@contextmanager
def document_of(session: WebSession) -> Iterator[EditSession]:
    """
    Locks the session and yields its open document.

    :param session: The caller's session.
    :yield: The open :class:`EditSession`.
    :raises HTTPException: 409 when the document was closed meanwhile.
    """
    with session.lock:
        editor = session.editor
        if editor is None:
            raise HTTPException(status_code=409, detail="Open a PDF first.")
        yield editor


@app.exception_handler(PDFEditorError)
async def _editor_error(_request: Request, error: PDFEditorError) -> JSONResponse:
    """Turns an editing failure into a readable message for the browser."""
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(SessionLimitReached)
async def _busy(_request: Request, error: SessionLimitReached) -> JSONResponse:
    """Reports that the server is holding as many documents as it allows."""
    return JSONResponse(status_code=503, content={"detail": str(error)})


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------
@app.get("/healthz")
def health() -> dict:
    """
    Liveness probe.

    :return: A small status document.
    """
    return {"status": "ok", "sessions": len(STORE), "configured": SETTINGS.locked}


@app.get("/api/config")
def configuration(request: Request) -> dict:
    """
    Describes the service to the browser before anybody signs in.

    :param request: Incoming request.
    :return: Title, limits, and whether the caller already has a session.
    """
    return {
        "title": SETTINGS.app_title,
        "configured": SETTINGS.locked,
        "maxUploadMb": SETTINGS.max_upload_mb,
        "idleMinutes": SETTINGS.session_idle_seconds // 60,
        "authenticated": STORE.get(_unsign(request.cookies.get(COOKIE_NAME))) is not None,
        "importFormats": sorted(doc_import.SUPPORTED_EXTENSIONS),
        "compressionLevels": [
            {"key": profile.key, "label": profile.label, "description": profile.description}
            for profile in compress.PROFILES.values()
        ],
        "wordMethods": word_export.available_methods(),
        "importBackends": doc_import.available_backends(),
    }


def _is_secure(request: Request) -> bool:
    """
    Reports whether the caller reached us over HTTPS.

    Hosts such as Hugging Face Spaces terminate TLS in front of the application
    and say so in a header, so the scheme alone is not enough.

    :param request: Incoming request.
    :return: True when the connection is encrypted end to end.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


@app.post("/api/login")
def login(request: Request, response: Response, payload: dict) -> dict:
    """
    Exchanges the shared password for a session cookie.

    :param request: Incoming request.
    :param response: Response whose cookie is set.
    :param payload: JSON body holding ``password``.
    :return: A small acknowledgement.
    :raises HTTPException: 403 when no password is configured, 401 when it is wrong.
    """
    if not SETTINGS.locked:
        raise HTTPException(
            status_code=403,
            detail=(
                "This deployment has no password set. The operator must set the "
                "APP_PASSWORD secret before anybody can sign in."
            ),
        )

    supplied = str(payload.get("password", ""))
    if not hmac.compare_digest(supplied, SETTINGS.password):
        raise HTTPException(status_code=401, detail="That password is not right.")

    session = STORE.create()
    response.set_cookie(
        COOKIE_NAME,
        _sign(session.identifier),
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        max_age=SETTINGS.session_idle_seconds,
    )
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response) -> dict:
    """
    Ends the session and deletes every file it holds.

    :param request: Incoming request.
    :param response: Response whose cookie is cleared.
    :return: A small acknowledgement.
    """
    identifier = _unsign(request.cookies.get(COOKIE_NAME))
    if identifier:
        STORE.drop(identifier)
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Opening documents
# ---------------------------------------------------------------------------
def _read_upload(upload: UploadFile) -> bytes:
    """
    Reads an uploaded file while enforcing the size limit.

    :param upload: The uploaded file.
    :return: Its contents.
    :raises HTTPException: 413 when the file is larger than allowed.
    """
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1024 * 256)
        if not chunk:
            break
        total += len(chunk)
        if total > SETTINGS.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"That file is larger than the {SETTINGS.max_upload_mb} MB limit.",
            )
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return b"".join(chunks)


def _state_of(session: WebSession) -> dict:
    """
    Builds the state document the browser keeps in sync.

    :param session: The caller's session.
    :return: Page count, page sizes and history flags.
    """
    if session.editor is None:
        return {"open": False, "name": "", "pages": 0}

    editor = session.editor
    pages = []
    for index in range(editor.page_count):
        page = editor.page(index)
        width = page.rect.width if page.rotation % 180 == 0 else page.rect.height
        height = page.rect.height if page.rotation % 180 == 0 else page.rect.width
        pages.append({"index": index, "width": round(width, 1), "height": round(height, 1)})

    return {
        "open": True,
        "name": session.source_name or editor.display_name,
        "pages": editor.page_count,
        "dirty": editor.dirty,
        "canUndo": editor.can_undo,
        "canRedo": editor.can_redo,
        "undoLabel": editor.undo_label,
        "redoLabel": editor.redo_label,
        "pageSizes": pages,
    }


@app.post("/api/upload")
def upload(
    file: UploadFile = File(...),
    password: str = Form(""),
    session: WebSession = Depends(current_session),
) -> dict:
    """
    Opens a PDF, or converts an Office document and opens the result.

    :param file: The uploaded document.
    :param password: Password for an encrypted PDF.
    :param session: The caller's session.
    :return: The new document state.
    :raises HTTPException: For unsupported or unreadable files.
    """
    data = _read_upload(file)
    name = Path(file.filename or "document").name
    suffix = Path(name).suffix.lower()

    with session.lock:
        target = session.new_file(name)
        target.write_bytes(data)

        if suffix != ".pdf":
            if not doc_import.is_supported(target):
                raise HTTPException(
                    status_code=400,
                    detail="Upload a PDF, or a Word, Excel or PowerPoint document.",
                )
            if not doc_import.available_backends(target):
                raise HTTPException(
                    status_code=400, detail=f"'{name}' cannot be converted on this server."
                )
            report = doc_import.convert_to_pdf(target, session.new_file(f"{Path(name).stem}.pdf"))
            target = report.destination
            name = f"{Path(name).stem}.pdf"

        try:
            editor = EditSession.open(target, password or None)
        except EncryptedPDFError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error

        session.adopt(editor, name)
        return _state_of(session)


@app.get("/api/state")
def state(session: WebSession = Depends(current_session)) -> dict:
    """
    Returns the current document state.

    :param session: The caller's session.
    :return: The state document.
    """
    with session.lock:
        return _state_of(session)


@app.post("/api/close")
def close_document(session: WebSession = Depends(current_session)) -> dict:
    """
    Closes the open document without ending the session.

    :param session: The caller's session.
    :return: The emptied state.
    """
    with session.lock:
        session.close_document()
        return _state_of(session)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _png_response(pixmap: fitz.Pixmap) -> Response:
    """
    Wraps a rendered page in a PNG response.

    :param pixmap: The rendered bitmap.
    :return: A response the browser can display.
    """
    return Response(
        content=pixmap.tobytes("png"),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/page/{index}")
def page_image(
    index: int, zoom: float = 1.4, session: WebSession = Depends(require_document)
) -> Response:
    """
    Renders one page for the main view.

    :param index: 0-based page index.
    :param zoom: Scale where 1.0 is 72 dpi.
    :param session: The caller's session.
    :return: A PNG image.
    """
    scale = max(MIN_ZOOM, min(MAX_ZOOM, float(zoom)))
    with document_of(session) as editor:
        return _png_response(editor.render(index, scale).pixmap)


@app.get("/api/thumbnail/{index}")
def thumbnail(index: int, session: WebSession = Depends(require_document)) -> Response:
    """
    Renders one page small, for the sidebar.

    :param index: 0-based page index.
    :param session: The caller's session.
    :return: A PNG image.
    """
    with document_of(session) as editor:
        return _png_response(editor.thumbnail(index, THUMBNAIL_WIDTH).pixmap)


@app.get("/api/words/{index}")
def words(index: int, session: WebSession = Depends(require_document)) -> dict:
    """
    Returns the word boxes of a page so the browser can offer selection.

    Coordinates are in display space, which is what the rendered image shows.

    :param index: 0-based page index.
    :param session: The caller's session.
    :return: The page size and its words.
    """
    with document_of(session) as editor:
        page = editor.page(index)
        matrix = page.rotation_matrix
        entries = []
        for position, word in enumerate(text_ops.page_words(page)):
            rect = fitz.Rect(word.rect) * matrix if page.rotation else fitz.Rect(word.rect)
            rect.normalize()
            entries.append(
                {
                    "i": position,
                    "text": word.text,
                    "x0": round(rect.x0, 2),
                    "y0": round(rect.y0, 2),
                    "x1": round(rect.x1, 2),
                    "y1": round(rect.y1, 2),
                    "block": word.block,
                    "line": word.line,
                }
            )
        width = page.rect.width if page.rotation % 180 == 0 else page.rect.height
        height = page.rect.height if page.rotation % 180 == 0 else page.rect.width
        return {"width": round(width, 2), "height": round(height, 2), "words": entries}


@app.get("/api/images/{index}")
def page_images(index: int, session: WebSession = Depends(require_document)) -> dict:
    """
    Lists the pictures on a page so they can be selected in the browser.

    :param index: 0-based page index.
    :param session: The caller's session.
    :return: One entry per image, in display coordinates.
    """
    with document_of(session) as editor:
        page = editor.page(index)
        matrix = page.rotation_matrix
        entries = []
        for reference in image_ops.list_images(page):
            rect = fitz.Rect(reference.bbox) * matrix if page.rotation else fitz.Rect(reference.bbox)
            rect.normalize()
            entries.append(
                {
                    "xref": reference.xref,
                    "x0": round(rect.x0, 2),
                    "y0": round(rect.y0, 2),
                    "x1": round(rect.x1, 2),
                    "y1": round(rect.y1, 2),
                }
            )
        return {"images": entries}


# ---------------------------------------------------------------------------
# Editing helpers
# ---------------------------------------------------------------------------
def _selection(editor: EditSession, index: int, indices: List[int]) -> TextSelection:
    """
    Rebuilds a text selection from word positions sent by the browser.

    :param editor: The open document.
    :param index: 0-based page index.
    :param indices: Positions in the page's word list.
    :return: The selection.
    :raises HTTPException: 400 when nothing valid was selected.
    """
    page = editor.page(index)
    available = text_ops.page_words(page)
    picked = [available[position] for position in indices if 0 <= position < len(available)]
    if not picked:
        raise HTTPException(status_code=400, detail="Select some text first.")
    return TextSelection(index, picked)


def _to_pdf_rect(editor: EditSession, index: int, box: dict) -> fitz.Rect:
    """
    Converts a rectangle sent in display space into PDF space.

    :param editor: The open document.
    :param index: 0-based page index.
    :param box: Mapping with ``x``, ``y``, ``width`` and ``height``.
    :return: The rectangle in unrotated PDF coordinates.
    """
    page = editor.page(index)
    left, top = float(box.get("x", 0)), float(box.get("y", 0))
    rect = fitz.Rect(left, top, left + float(box.get("width", 0)), top + float(box.get("height", 0)))
    rect.normalize()
    if page.rotation:
        rect = rect * page.derotation_matrix
        rect.normalize()
    return rect


def _apply(session: WebSession, label: str, index: int, action) -> dict:
    """
    Runs one undoable change and returns the refreshed state.

    :param session: The caller's session.
    :param label: Name of the operation, kept in the undo history.
    :param index: Page the visitor was looking at.
    :param action: Callable receiving the open document.
    :return: The state document, with the operation's own result under ``result``.
    """
    with document_of(session) as editor:
        with editor.edit(label, index):
            outcome = action(editor)
        document = _state_of(session)
        document["result"] = outcome
        return document


# ---------------------------------------------------------------------------
# Text editing
# ---------------------------------------------------------------------------
@app.post("/api/text/replace")
def replace_text(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Replaces the selected words with new text.

    :param payload: ``page``, ``words``, ``text`` and optional ``reflow``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    reflow = bool(payload.get("reflow", True))
    replacement = str(payload.get("text", ""))
    indices = [int(value) for value in payload.get("words", [])]

    def action(editor: EditSession):
        selection = _selection(editor, index, indices)
        result = text_ops.replace_selection(
            editor.page(index), selection, replacement, reflow=reflow
        )
        return {"summary": result.summary, "warnings": result.warnings}

    return _apply(session, "Replace text", index, action)


@app.post("/api/text/delete")
def delete_text(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Deletes the selected words.

    :param payload: ``page``, ``words`` and optional ``reflow``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    reflow = bool(payload.get("reflow", True))
    indices = [int(value) for value in payload.get("words", [])]

    def action(editor: EditSession):
        selection = _selection(editor, index, indices)
        result = text_ops.delete_selection(editor.page(index), selection, reflow=reflow)
        return {"summary": result.summary, "warnings": result.warnings}

    return _apply(session, "Delete text", index, action)


@app.post("/api/text/highlight")
def highlight_text(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Adds a highlight annotation over the selected words.

    :param payload: ``page``, ``words`` and optional ``colour`` as an RGB list.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    indices = [int(value) for value in payload.get("words", [])]
    colour = payload.get("colour") or [1.0, 0.92, 0.23]

    def action(editor: EditSession):
        selection = _selection(editor, index, indices)
        count = text_ops.highlight_selection(
            editor.page(index), selection, tuple(float(part) for part in colour[:3])
        )
        return {"summary": f"Highlighted {count} line(s)."}

    return _apply(session, "Highlight text", index, action)


@app.post("/api/text/redact")
def redact_text(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Permanently removes the selected words.

    :param payload: ``page`` and ``words``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    indices = [int(value) for value in payload.get("words", [])]

    def action(editor: EditSession):
        selection = _selection(editor, index, indices)
        count = text_ops.redact_selection(editor.page(index), selection)
        return {"summary": f"Redacted {count} area(s)."}

    return _apply(session, "Redact text", index, action)


@app.post("/api/text/box")
def insert_text_box(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Draws a new block of text on the page.

    :param payload: ``page``, ``rect``, ``text`` and optional font settings.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    content = str(payload.get("text", "")).strip()
    colour = payload.get("colour") or [0.0, 0.0, 0.0]

    def action(editor: EditSession):
        rect = _to_pdf_rect(editor, index, payload.get("rect", {}))
        style = SpanStyle(
            font=str(payload.get("font", "Helvetica")),
            size=float(payload.get("size", 12)),
            color=tuple(float(part) for part in colour[:3]),
        ).with_overrides(bold=bool(payload.get("bold")), italic=bool(payload.get("italic")))
        text_ops.insert_text_box(editor.page(index), rect, content, style=style)
        return {"summary": "Text box added."}

    return _apply(session, "Insert text box", index, action)


@app.post("/api/search")
def search(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Searches every page for a phrase.

    :param payload: ``query``.
    :param session: The caller's session.
    :return: One entry per page holding matches.
    """
    query = str(payload.get("query", "")).strip()
    if not query:
        return {"matches": []}

    with document_of(session) as editor:
        matches = []
        for index in range(editor.page_count):
            page = editor.page(index)
            hits = text_ops.search_page(page, query)
            if not hits:
                continue
            available = text_ops.page_words(page)
            positions = [
                position
                for position, word in enumerate(available)
                if any(hit.intersects(word.rect) for hit in hits)
            ]
            matches.append({"page": index, "count": len(hits), "words": positions})
        return {"matches": matches}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
@app.post("/api/image/insert")
def insert_image(
    file: UploadFile = File(...),
    page: int = Form(0),
    x: float = Form(0.0),
    y: float = Form(0.0),
    width: float = Form(0.0),
    height: float = Form(0.0),
    session: WebSession = Depends(require_document),
) -> dict:
    """
    Places an uploaded picture on a page.

    :param file: The image to insert.
    :param page: 0-based page index.
    :param x: Left edge in display coordinates.
    :param y: Top edge in display coordinates.
    :param width: Width in display coordinates.
    :param height: Height in display coordinates.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    data = _read_upload(file)
    stored = session.new_file(Path(file.filename or "image.png").name)
    stored.write_bytes(data)
    box = {"x": x, "y": y, "width": width, "height": height}

    def action(editor: EditSession):
        rect = _to_pdf_rect(editor, page, box)
        if rect.width < image_ops.MIN_IMAGE_SIZE or rect.height < image_ops.MIN_IMAGE_SIZE:
            info = image_ops.read_image_info(stored)
            rect = image_ops.default_placement(editor.page(page), info)
        image_ops.insert_image(editor.page(page), rect, stored)
        return {"summary": "Image placed."}

    return _apply(session, "Insert image", page, action)


@app.post("/api/image/move")
def move_image(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Moves or resizes a picture that is already on the page.

    :param payload: ``page``, ``xref`` and ``rect``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    xref = int(payload.get("xref", 0))

    def action(editor: EditSession):
        page = editor.page(index)
        reference = next((item for item in image_ops.list_images(page) if item.xref == xref), None)
        if reference is None:
            raise HTTPException(status_code=404, detail="That image is no longer on the page.")
        image_ops.move_image(page, reference, _to_pdf_rect(editor, index, payload.get("rect", {})))
        return {"summary": "Image updated."}

    return _apply(session, "Move image", index, action)


@app.post("/api/image/delete")
def delete_image(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Removes a picture from the page.

    :param payload: ``page`` and ``xref``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    index = int(payload.get("page", 0))
    xref = int(payload.get("xref", 0))

    def action(editor: EditSession):
        page = editor.page(index)
        reference = next((item for item in image_ops.list_images(page) if item.xref == xref), None)
        if reference is None:
            raise HTTPException(status_code=404, detail="That image is no longer on the page.")
        image_ops.delete_image(page, reference)
        return {"summary": "Image deleted."}

    return _apply(session, "Delete image", index, action)


# ---------------------------------------------------------------------------
# Page management
# ---------------------------------------------------------------------------
@app.post("/api/pages/delete")
def delete_pages(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Deletes the given pages.

    :param payload: ``pages`` as a list of 0-based indices.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    indices = [int(value) for value in payload.get("pages", [])]
    first = min(indices) if indices else 0

    def action(editor: EditSession):
        removed = page_ops.delete_pages(editor.document, indices)
        return {"summary": f"Deleted {removed} page(s)."}

    return _apply(session, "Delete pages", max(0, first - 1), action)


@app.post("/api/pages/duplicate")
def duplicate_pages(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Duplicates the given pages.

    :param payload: ``pages`` as a list of 0-based indices.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    indices = [int(value) for value in payload.get("pages", [])]

    def action(editor: EditSession):
        created = page_ops.duplicate_pages(editor.document, indices)
        return {"summary": f"Duplicated {len(created)} page(s)."}

    return _apply(session, "Duplicate pages", min(indices) if indices else 0, action)


@app.post("/api/pages/rotate")
def rotate_pages(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Rotates the given pages.

    :param payload: ``pages`` and ``angle``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    indices = [int(value) for value in payload.get("pages", [])]
    angle = int(payload.get("angle", 90))

    def action(editor: EditSession):
        count = page_ops.rotate_pages(editor.document, indices, angle)
        return {"summary": f"Rotated {count} page(s)."}

    return _apply(session, "Rotate pages", min(indices) if indices else 0, action)


@app.post("/api/pages/move")
def move_page(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Moves one page to another position.

    :param payload: ``page`` and ``destination``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    source = int(payload.get("page", 0))
    destination = int(payload.get("destination", source))

    def action(editor: EditSession):
        page_ops.move_page(editor.document, source, destination)
        return {"summary": f"Moved page {source + 1} to position {destination + 1}."}

    return _apply(session, "Move page", destination, action)


@app.post("/api/pages/blank")
def add_blank_page(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Inserts blank pages.

    :param payload: ``position``, ``paper``, ``landscape``, ``count`` and ``match``.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    position = int(payload.get("position", 0))
    count = max(1, min(50, int(payload.get("count", 1))))
    paper = str(payload.get("paper", "A4"))
    landscape = bool(payload.get("landscape"))
    match = payload.get("match")

    def action(editor: EditSession):
        reference = int(match) if match is not None else None
        for offset in range(count):
            page_ops.insert_blank_page(
                editor.document,
                position + offset,
                paper=paper,
                landscape=landscape,
                match_page=reference,
            )
        return {"summary": f"Inserted {count} blank page(s)."}

    return _apply(session, "Add blank page", position, action)


@app.post("/api/pages/merge")
def merge_pdfs(
    files: List[UploadFile] = File(...),
    position: int = Form(-1),
    session: WebSession = Depends(require_document),
) -> dict:
    """
    Merges uploaded PDFs into the open document.

    :param files: PDFs to merge.
    :param position: Insert position, or ``-1`` to append.
    :param session: The caller's session.
    :return: The refreshed state.
    """
    stored: List[Path] = []
    for upload_file in files:
        data = _read_upload(upload_file)
        target = session.new_file(Path(upload_file.filename or "merge.pdf").name)
        target.write_bytes(data)
        stored.append(target)

    with document_of(session) as editor:
        at = editor.page_count if position < 0 else max(0, min(position, editor.page_count))

    def action(editor: EditSession):
        added = page_ops.merge_documents(editor.document, stored, at=at, add_bookmarks=True)
        return {"summary": f"Merged {len(stored)} file(s): {added} page(s) added."}

    return _apply(session, "Merge PDFs", at, action)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
@app.post("/api/undo")
def undo(session: WebSession = Depends(require_document)) -> dict:
    """
    Reverts the last change.

    :param session: The caller's session.
    :return: The refreshed state.
    """
    with document_of(session) as editor:
        label, index = editor.undo()
        document = _state_of(session)
        document["result"] = {"summary": f"Undone: {label}" if label else "Nothing to undo."}
        document["focus"] = index
        return document


@app.post("/api/redo")
def redo(session: WebSession = Depends(require_document)) -> dict:
    """
    Re-applies the last undone change.

    :param session: The caller's session.
    :return: The refreshed state.
    """
    with document_of(session) as editor:
        label, index = editor.redo()
        document = _state_of(session)
        document["result"] = {"summary": f"Redone: {label}" if label else "Nothing to redo."}
        document["focus"] = index
        return document


# ---------------------------------------------------------------------------
# Producing files
# ---------------------------------------------------------------------------
@app.get("/api/download")
def download(session: WebSession = Depends(require_document)) -> StreamingResponse:
    """
    Sends the edited PDF to the browser.

    :param session: The caller's session.
    :return: The PDF as an attachment.
    """
    with document_of(session) as editor:
        data = editor.export_bytes()
        name = Path(session.source_name or "document.pdf").stem
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}-edited.pdf"'},
    )


@app.post("/api/compress")
def compress_document(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Writes a smaller copy and offers it for download.

    :param payload: ``level`` and optional ``lossless``.
    :param session: The caller's session.
    :return: Sizes, ratio and a download token.
    """
    level = str(payload.get("level", "medium"))
    lossless = bool(payload.get("lossless"))

    with document_of(session) as editor:
        stem = Path(session.source_name or "document.pdf").stem
        target = session.new_file(f"{stem}-compressed.pdf")
        report = compress.compress_pdf(
            editor.export_bytes(), target, level=level, downsample_images=not lossless
        )

    token = session.register_artifact(report.destination, "compressed")
    return {
        "summary": report.summary,
        "before": compress.format_size(report.original_bytes),
        "after": compress.format_size(report.compressed_bytes),
        "ratio": round(report.ratio * 100),
        "warnings": report.warnings,
        "token": token,
        "filename": report.destination.name,
    }


@app.post("/api/export/word")
def export_word(payload: dict, session: WebSession = Depends(require_document)) -> dict:
    """
    Exports the document to Word and offers it for download.

    :param payload: ``method`` and optional ``pages``.
    :param session: The caller's session.
    :return: A summary and a download token.
    :raises HTTPException: 400 when no converter is installed.
    """
    if not word_export.available_methods():
        raise HTTPException(status_code=400, detail="Word export is not available on this server.")

    method = str(payload.get("method", "auto"))
    pages = payload.get("pages")
    selected = [int(value) for value in pages] if pages else None

    with document_of(session) as editor:
        stem = Path(session.source_name or "document.pdf").stem
        target = session.new_file(f"{stem}.docx")
        report = word_export.export_to_docx(
            editor.export_bytes(), target, pages=selected, method=method
        )

    token = session.register_artifact(report.destination, "word")
    return {
        "summary": report.summary,
        "warnings": report.warnings,
        "token": token,
        "filename": report.destination.name,
    }


@app.get("/api/artifact/{token}")
def artifact(token: str, session: WebSession = Depends(current_session)) -> FileResponse:
    """
    Sends a produced file to the browser.

    :param token: Token returned by the operation that produced the file.
    :param session: The caller's session.
    :return: The file as an attachment.
    :raises HTTPException: 404 when the file has already been cleaned up.
    """
    path = session.artifact(token)
    if path is None:
        raise HTTPException(status_code=404, detail="That file is no longer available.")
    return FileResponse(path, filename=path.name.split("-", 1)[-1])


# ---------------------------------------------------------------------------
# Static front end
# ---------------------------------------------------------------------------
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    """
    Serves the single page front end.

    :return: The HTML document.
    :raises HTTPException: 500 when the static files are missing.
    """
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail="The front end files are missing.")
    return FileResponse(page, media_type="text/html")


__all__ = ["app"]
