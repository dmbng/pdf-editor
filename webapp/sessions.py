"""
Per-visitor state for the web front end.

Each visitor gets a :class:`WebSession`: a private temporary folder, an optional
open :class:`~pdf_editor.core.session.EditSession`, and a lock.  The lock matters
because PyMuPDF documents must not be touched from two threads at once, and a
web server happily serves two requests in parallel.

Sessions live in memory and are swept once they go idle, so a small container
shared by a few people cannot fill up with abandoned documents.
"""

from __future__ import annotations

import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from pdf_editor.core.session import EditSession
from webapp.config import Settings


class SessionLimitReached(RuntimeError):
    """Raised when the server already holds as many documents as it allows."""


@dataclass
class WebSession:
    """One visitor's workspace."""

    identifier: str
    workspace: Path
    settings: Settings
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    editor: Optional[EditSession] = None
    source_name: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    artifacts: Dict[str, Path] = field(default_factory=dict, repr=False)

    def touch(self) -> None:
        """Marks the session as used just now."""
        self.last_seen = time.time()

    def idle_seconds(self) -> float:
        """Seconds since the session was last used."""
        return time.time() - self.last_seen

    @property
    def has_document(self) -> bool:
        """True when a PDF is open in this session."""
        return self.editor is not None

    def adopt(self, editor: EditSession, source_name: str) -> None:
        """
        Replaces the open document with another one.

        :param editor: The newly opened document.
        :param source_name: Name shown to the visitor.
        """
        self.close_document()
        editor.max_history = self.settings.max_history
        editor.max_history_bytes = self.settings.max_history_bytes
        self.editor = editor
        self.source_name = source_name
        self.touch()

    def close_document(self) -> None:
        """Closes the open document, if any."""
        if self.editor is not None:
            try:
                self.editor.close()
            except Exception:
                pass
            self.editor = None
        self.source_name = ""

    def register_artifact(self, path: Path, label: str) -> str:
        """
        Remembers a produced file so it can be downloaded.

        :param path: File that was written inside the workspace.
        :param label: Short name used in the download link.
        :return: The token identifying the file.
        """
        token = f"{label}-{secrets.token_urlsafe(8)}"
        self.artifacts[token] = path
        # Keep only the few most recent products; older ones are deleted.
        while len(self.artifacts) > 6:
            old_token, old_path = next(iter(self.artifacts.items()))
            self.artifacts.pop(old_token, None)
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass
        return token

    def artifact(self, token: str) -> Optional[Path]:
        """
        Looks up a produced file.

        :param token: Token returned by :meth:`register_artifact`.
        :return: The path, or ``None`` when unknown or already removed.
        """
        path = self.artifacts.get(token)
        return path if path and path.exists() else None

    def new_file(self, name: str) -> Path:
        """
        Returns a fresh path inside the workspace.

        :param name: Desired file name; only its base name is used.
        :return: A path that does not exist yet.
        """
        safe = Path(name).name or "document"
        return self.workspace / f"{secrets.token_hex(4)}-{safe}"

    def close(self) -> None:
        """Closes the document and deletes the workspace."""
        self.close_document()
        shutil.rmtree(self.workspace, ignore_errors=True)


class SessionStore:
    """
    Keeps the live sessions and sweeps the idle ones.

    :param settings: Runtime configuration.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: Dict[str, WebSession] = {}
        self._guard = threading.Lock()

    def __len__(self) -> int:
        """Number of live sessions."""
        with self._guard:
            return len(self._sessions)

    def create(self) -> WebSession:
        """
        Starts a new session.

        :return: The created :class:`WebSession`.
        :raises SessionLimitReached: When the server is already full.
        """
        self.sweep()
        with self._guard:
            if len(self._sessions) >= self.settings.max_sessions:
                raise SessionLimitReached(
                    "The server is busy with other documents right now. "
                    "Please try again in a few minutes."
                )
            identifier = secrets.token_urlsafe(24)
            workspace = Path(tempfile.mkdtemp(prefix="pdfweb-"))
            session = WebSession(identifier=identifier, workspace=workspace, settings=self.settings)
            self._sessions[identifier] = session
            return session

    def get(self, identifier: Optional[str]) -> Optional[WebSession]:
        """
        Looks up a live session.

        :param identifier: Session identifier taken from the cookie.
        :return: The session, or ``None`` when unknown or expired.
        """
        if not identifier:
            return None
        with self._guard:
            session = self._sessions.get(identifier)
        if session is None:
            return None
        if session.idle_seconds() > self.settings.session_idle_seconds:
            self.drop(identifier)
            return None
        session.touch()
        return session

    def drop(self, identifier: str) -> None:
        """
        Ends one session and deletes its files.

        :param identifier: Session identifier.
        """
        with self._guard:
            session = self._sessions.pop(identifier, None)
        if session is not None:
            session.close()

    def sweep(self) -> int:
        """
        Ends every session that has been idle for too long.

        :return: Number of sessions removed.
        """
        limit = self.settings.session_idle_seconds
        with self._guard:
            stale: List[WebSession] = [
                session for session in self._sessions.values() if session.idle_seconds() > limit
            ]
            for session in stale:
                self._sessions.pop(session.identifier, None)
        for session in stale:
            session.close()
        return len(stale)

    def close_all(self) -> None:
        """Ends every session, used when the server shuts down."""
        with self._guard:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()


__all__ = ["SessionLimitReached", "SessionStore", "WebSession"]
