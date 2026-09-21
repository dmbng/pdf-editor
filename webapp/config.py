"""
Settings for the web front end, all read from environment variables.

The defaults are sized for a small free container shared by a handful of people.
Nothing here affects the desktop application.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    """
    Reads a whole number from the environment.

    :param name: Variable name.
    :param default: Value used when unset or unreadable.
    :return: The configured number.
    """
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration of the web front end."""

    password: str
    secret_key: str
    max_upload_bytes: int
    session_idle_seconds: int
    max_sessions: int
    max_history: int
    max_history_bytes: int
    cleanup_interval_seconds: int
    app_title: str

    @property
    def locked(self) -> bool:
        """True when a password is configured, which is the expected setup."""
        return bool(self.password)

    @property
    def max_upload_mb(self) -> int:
        """Upload limit in whole megabytes, for messages shown to people."""
        return max(1, self.max_upload_bytes // (1024 * 1024))


def load_settings() -> Settings:
    """
    Builds the settings from the environment.

    ``APP_PASSWORD`` is the only variable that really matters: without it the
    service refuses to hand out sessions, so an unconfigured deployment cannot
    quietly end up open to the whole internet.

    :return: The :class:`Settings` for this process.
    """
    return Settings(
        password=os.environ.get("APP_PASSWORD", "").strip(),
        # A generated key signs people out on restart, which is fine here.
        secret_key=os.environ.get("APP_SECRET_KEY", "").strip() or secrets.token_hex(32),
        max_upload_bytes=_int("MAX_UPLOAD_MB", 25) * 1024 * 1024,
        session_idle_seconds=_int("SESSION_IDLE_MINUTES", 60) * 60,
        max_sessions=_int("MAX_SESSIONS", 12),
        max_history=_int("MAX_HISTORY_STEPS", 5),
        max_history_bytes=_int("MAX_HISTORY_MB", 48) * 1024 * 1024,
        cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", 120),
        app_title=os.environ.get("APP_TITLE", "PDF Editor").strip() or "PDF Editor",
    )


__all__ = ["Settings", "load_settings"]
