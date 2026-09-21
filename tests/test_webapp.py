"""
Tests for the hosted web front end.

The application is imported with a password set in the environment, so the tests
exercise the same authentication path real visitors use.
"""

import importlib
import os

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI is not installed")
from fastapi.testclient import TestClient  # noqa: E402  (import needs the skip above)

PASSWORD = "test-password"


@pytest.fixture(scope="module")
def application():
    """Imports the web application with a known password."""
    os.environ["APP_PASSWORD"] = PASSWORD
    os.environ["APP_SECRET_KEY"] = "unit-test-key"
    os.environ["MAX_UPLOAD_MB"] = "5"
    os.environ["MAX_SESSIONS"] = "4"

    import webapp.config
    import webapp.sessions
    import webapp.server

    importlib.reload(webapp.config)
    importlib.reload(webapp.sessions)
    module = importlib.reload(webapp.server)
    yield module
    module.STORE.close_all()


@pytest.fixture
def client(application):
    """A client that is not signed in."""
    with TestClient(application.app) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client):
    """A client holding a valid session."""
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def loaded(signed_in, paragraph_pdf):
    """A client with the two page sample document open."""
    with open(paragraph_pdf, "rb") as handle:
        response = signed_in.post(
            "/api/upload", files={"file": ("paragraphs.pdf", handle.read(), "application/pdf")}
        )
    assert response.status_code == 200
    assert response.json()["pages"] == 2
    return signed_in


def body_words(client, page=0):
    """Returns the word entries of the wrapped body paragraph."""
    words = client.get("/api/words/" + str(page)).json()["words"]
    return [word for word in words if word["y0"] > 130 and word["y1"] < 270]


# ---------------------------------------------------------------------------
# Health and configuration
# ---------------------------------------------------------------------------
def test_health_and_config(client):
    """The service reports that it is alive and password protected."""
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["configured"] is True

    config = client.get("/api/config").json()
    assert config["configured"] is True
    assert config["authenticated"] is False
    assert config["maxUploadMb"] == 5
    assert any(level["key"] == "medium" for level in config["compressionLevels"])
    assert ".docx" in config["importFormats"]


def test_front_end_is_served(client):
    """The single page interface and its assets are reachable."""
    page = client.get("/")
    assert page.status_code == 200
    assert "PDF Editor" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def test_wrong_password_is_refused(client):
    """A bad password hands out no session."""
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.get("/api/state").status_code == 401


def test_every_editing_route_needs_a_session(client):
    """Nothing can be done without signing in first."""
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/page/0").status_code == 401
    assert client.post("/api/undo").status_code == 401
    assert client.post("/api/compress", json={}).status_code == 401
    assert client.post(
        "/api/upload", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    ).status_code == 401


def test_forged_cookie_is_rejected(signed_in):
    """A cookie that was not signed by this server is worthless."""
    signed_in.cookies.set("pdf_editor_session", "someone-elses-id.deadbeef")
    assert signed_in.get("/api/state").status_code == 401


def test_sign_out_deletes_the_session(loaded):
    """Signing out ends the session and its files."""
    assert loaded.post("/api/logout").status_code == 200
    assert loaded.get("/api/state").status_code == 401


# ---------------------------------------------------------------------------
# Opening documents
# ---------------------------------------------------------------------------
def test_open_pdf_reports_state(loaded):
    """The state document describes the open file."""
    state = loaded.get("/api/state").json()
    assert state["open"] is True
    assert state["name"] == "paragraphs.pdf"
    assert state["pages"] == 2
    assert state["canUndo"] is False
    assert len(state["pageSizes"]) == 2


def test_open_office_document(signed_in, word_document):
    """A Word file is converted on upload and opened as a PDF."""
    pytest.importorskip("reportlab")
    with open(word_document, "rb") as handle:
        response = signed_in.post(
            "/api/upload", files={"file": ("report.docx", handle.read(), "application/octet-stream")}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["open"] is True
    assert body["name"].endswith(".pdf")
    assert body["pages"] >= 1


def test_unsupported_upload_is_refused(signed_in):
    """A file that is neither a PDF nor an Office document is rejected."""
    response = signed_in.post(
        "/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 400
    assert "Word, Excel or PowerPoint" in response.json()["detail"]


def test_oversized_upload_is_refused(signed_in):
    """The size limit is enforced while reading, not after."""
    big = b"%PDF-1.4\n" + b"0" * (6 * 1024 * 1024)
    response = signed_in.post("/api/upload", files={"file": ("big.pdf", big, "application/pdf")})
    assert response.status_code == 413
    assert "5 MB" in response.json()["detail"]


def test_close_document(loaded):
    """A document can be closed without ending the session."""
    assert loaded.post("/api/close").json()["open"] is False
    assert loaded.get("/api/state").status_code == 200


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_pages_render_as_png(loaded):
    """Pages and thumbnails come back as images."""
    page = loaded.get("/api/page/0?zoom=1.2")
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.content[:4] == b"\x89PNG"

    small = loaded.get("/api/thumbnail/0")
    assert small.status_code == 200
    assert len(small.content) < len(page.content)


def test_zoom_is_clamped(loaded):
    """A silly zoom factor cannot be used to exhaust the server."""
    huge = loaded.get("/api/page/0?zoom=99")
    assert huge.status_code == 200
    capped = loaded.get("/api/page/0?zoom=3")
    assert len(huge.content) == len(capped.content)


def test_words_carry_display_coordinates(loaded):
    """The word list matches the rendered page, which is what selection needs."""
    answer = loaded.get("/api/words/0").json()
    assert answer["width"] == pytest.approx(595, abs=1)
    assert answer["height"] == pytest.approx(842, abs=1)
    assert answer["words"]
    first = answer["words"][0]
    assert first["text"] == "Editing"
    assert first["x1"] > first["x0"] and first["y1"] > first["y0"]


def test_missing_page_is_reported(loaded):
    """Asking for a page that does not exist is an error, not a crash."""
    assert loaded.get("/api/page/99").status_code == 400


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def test_replace_text_through_the_api(loaded):
    """Replacing words changes the document and enables undo."""
    picks = [word["i"] for word in body_words(loaded)][:3]
    response = loaded.post(
        "/api/text/replace",
        json={"page": 0, "words": picks, "text": "Rewritten by the browser", "reflow": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Replaced" in body["result"]["summary"]
    assert body["canUndo"] is True
    assert body["dirty"] is True

    remaining = " ".join(word["text"] for word in loaded.get("/api/words/0").json()["words"])
    assert "Rewritten" in remaining


def test_delete_and_undo(loaded):
    """Deleting text can be undone again."""
    picks = [word["i"] for word in body_words(loaded)][:2]
    before = " ".join(word["text"] for word in loaded.get("/api/words/0").json()["words"])

    assert loaded.post("/api/text/delete", json={"page": 0, "words": picks}).status_code == 200
    undone = loaded.post("/api/undo").json()
    assert "Undone" in undone["result"]["summary"]

    after = " ".join(word["text"] for word in loaded.get("/api/words/0").json()["words"])
    assert after == before


def test_highlight_and_search(loaded):
    """Highlighting works, and search reports the words it matched."""
    picks = [word["i"] for word in body_words(loaded)][:2]
    assert loaded.post("/api/text/highlight", json={"page": 0, "words": picks}).status_code == 200

    found = loaded.post("/api/search", json={"query": "paragraph"}).json()["matches"]
    assert found
    assert found[0]["count"] >= 1
    assert found[0]["words"]


def test_editing_without_a_selection_is_refused(loaded):
    """An empty selection produces a readable error."""
    response = loaded.post("/api/text/replace", json={"page": 0, "words": [], "text": "x"})
    assert response.status_code == 400
    assert "Select some text" in response.json()["detail"]


def test_edits_need_an_open_document(signed_in):
    """Editing routes insist on a document."""
    assert signed_in.post("/api/text/delete", json={"page": 0, "words": [0]}).status_code == 409
    assert signed_in.get("/api/page/0").status_code == 409


# ---------------------------------------------------------------------------
# Images and pages
# ---------------------------------------------------------------------------
def test_insert_move_and_delete_image(loaded, png_file):
    """A picture can be placed, moved and removed through the API."""
    with open(png_file, "rb") as handle:
        response = loaded.post(
            "/api/image/insert",
            files={"file": ("logo.png", handle.read(), "image/png")},
            data={"page": "0", "x": "60", "y": "500", "width": "180", "height": "90"},
        )
    assert response.status_code == 200

    images = loaded.get("/api/images/0").json()["images"]
    assert len(images) == 1
    assert images[0]["x0"] == pytest.approx(60, abs=2)

    moved = loaded.post(
        "/api/image/move",
        json={
            "page": 0,
            "xref": images[0]["xref"],
            "rect": {"x": 200, "y": 300, "width": 150, "height": 75},
        },
    )
    assert moved.status_code == 200
    relocated = loaded.get("/api/images/0").json()["images"]
    assert relocated[0]["x0"] == pytest.approx(200, abs=2)

    assert loaded.post(
        "/api/image/delete", json={"page": 0, "xref": relocated[0]["xref"]}
    ).status_code == 200
    assert loaded.get("/api/images/0").json()["images"] == []


def test_page_operations(loaded):
    """Duplicate, rotate, blank, move and delete all work over HTTP."""
    assert loaded.post("/api/pages/duplicate", json={"pages": [0]}).json()["pages"] == 3
    assert loaded.post("/api/pages/rotate", json={"pages": [0], "angle": 90}).status_code == 200
    blank = loaded.post("/api/pages/blank", json={"position": 1, "count": 1, "paper": "A4"})
    assert blank.json()["pages"] == 4
    assert loaded.post("/api/pages/move", json={"page": 0, "destination": 3}).status_code == 200
    assert loaded.post("/api/pages/delete", json={"pages": [0]}).json()["pages"] == 3


def test_deleting_every_page_is_refused(loaded):
    """The document may never be emptied."""
    response = loaded.post("/api/pages/delete", json={"pages": [0, 1]})
    assert response.status_code == 400
    assert "at least one page" in response.json()["detail"]


def test_merge(loaded, simple_pdf):
    """Merging appends the uploaded pages."""
    with open(simple_pdf, "rb") as handle:
        response = loaded.post(
            "/api/pages/merge",
            files={"files": ("simple.pdf", handle.read(), "application/pdf")},
            data={"position": "-1"},
        )
    assert response.status_code == 200
    assert response.json()["pages"] == 6


# ---------------------------------------------------------------------------
# Producing files
# ---------------------------------------------------------------------------
def test_download_returns_the_edited_pdf(loaded):
    """The download carries the current state of the document."""
    picks = [word["i"] for word in body_words(loaded)][:2]
    loaded.post("/api/text/replace", json={"page": 0, "words": picks, "text": "Edited"})

    response = loaded.get("/api/download")
    assert response.status_code == 200
    assert response.content[:5] == b"%PDF-"
    assert "attachment" in response.headers["content-disposition"]


def test_compress_and_download_artifact(loaded):
    """Compression hands back a token that downloads the smaller file."""
    response = loaded.post("/api/compress", json={"level": "high"})
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert "KB" in body["before"]

    download = loaded.get("/api/artifact/" + body["token"])
    assert download.status_code == 200
    assert download.content[:5] == b"%PDF-"


def test_unknown_artifact_token(loaded):
    """A token nobody issued returns a clean 404."""
    assert loaded.get("/api/artifact/nope-123").status_code == 404


def test_export_word(loaded):
    """The Word export produces a downloadable document."""
    pytest.importorskip("docx")
    response = loaded.post("/api/export/word", json={"method": "text", "pages": [0]})
    assert response.status_code == 200
    body = response.json()
    assert body["token"]

    download = loaded.get("/api/artifact/" + body["token"])
    assert download.status_code == 200
    assert download.content[:2] == b"PK"  # A .docx file is a zip archive.


# ---------------------------------------------------------------------------
# Session hygiene
# ---------------------------------------------------------------------------
def test_sessions_are_isolated(application, paragraph_pdf):
    """One visitor cannot see another visitor's document."""
    with TestClient(application.app) as first, TestClient(application.app) as second:
        first.post("/api/login", json={"password": PASSWORD})
        second.post("/api/login", json={"password": PASSWORD})

        with open(paragraph_pdf, "rb") as handle:
            first.post("/api/upload", files={"file": ("mine.pdf", handle.read(), "application/pdf")})

        assert first.get("/api/state").json()["open"] is True
        assert second.get("/api/state").json()["open"] is False
        assert second.get("/api/page/0").status_code == 409


def test_web_sessions_use_the_small_history_limit(loaded, application):
    """A hosted session keeps far less undo history than the desktop app."""
    store = application.STORE
    session = [item for item in store._sessions.values() if item.has_document][0]
    assert session.editor.max_history == application.SETTINGS.max_history
    assert session.editor.max_history < 30
    assert session.editor.max_history_bytes < 512 * 1024 * 1024


def test_session_sweep_removes_idle_visitors(application):
    """Idle sessions are dropped and their files deleted."""
    store = application.STORE
    session = store.create()
    workspace = session.workspace
    assert workspace.exists()

    session.last_seen -= application.SETTINGS.session_idle_seconds + 10
    assert store.sweep() >= 1
    assert not workspace.exists()
    assert store.get(session.identifier) is None


def test_server_refuses_too_many_sessions(application):
    """The session limit protects a small container."""
    from webapp.sessions import SessionLimitReached

    store = application.STORE
    store.close_all()
    made = [store.create() for _ in range(application.SETTINGS.max_sessions)]
    try:
        with pytest.raises(SessionLimitReached):
            store.create()
    finally:
        for session in made:
            store.drop(session.identifier)
