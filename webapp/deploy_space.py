"""
Deploy the web editor to a Hugging Face Space.

The Space is a Docker Space: Hugging Face builds the ``Dockerfile`` at the root of
the Space repository and serves the container on port 7860.  This script stages
exactly the files the server needs, writes the Space card, uploads everything and
sets the password as a repository secret.

Usage::

    python webapp/deploy_space.py --space yourname/pdf-editor --password "a good password"

The token is read from ``--token``, then ``HF_TOKEN``, then the cached login left
by ``huggingface-cli login``.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent

# Only these are copied into the Space: the engine, the web layer, nothing else.
INCLUDE = ("pdf_editor", "webapp")

SPACE_CARD = """---
title: {title}
emoji: {emoji}
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Private PDF editor for a small group
---

# {title}

A small, password protected PDF editor: edit the text that is already in a file,
add images, manage pages, merge documents, import Word, Excel and PowerPoint
files, compress, and export to Word.

It is the web front end of a desktop application; both share the same engine.

## Access

Everyone who visits needs the shared password. It is stored as the `APP_PASSWORD`
secret of this Space, never in the code.

## Privacy

Documents are held in memory and in a temporary folder for the length of a
session. Both are deleted when the session is signed out or has been idle for an
hour. Nothing is written to a database and nothing survives a restart.
"""


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Reads the command line.

    :param argv: Arguments to parse; defaults to ``sys.argv``.
    :return: The parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Deploy the web editor to a Hugging Face Space.")
    parser.add_argument("--space", required=True, help="Target Space, as 'username/space-name'")
    parser.add_argument("--password", default=None, help="Shared password for visitors")
    parser.add_argument("--token", default=None, help="Hugging Face access token with write scope")
    parser.add_argument("--title", default="PDF Editor", help="Title shown on the Space")
    parser.add_argument("--emoji", default="\U0001F4C4", help="Emoji shown on the Space card")
    parser.add_argument("--private", action="store_true", help="Create the Space as private")
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="Only assemble the upload folder and print its path, without uploading",
    )
    return parser.parse_args(argv)


def stage(target: Path, title: str, emoji: str) -> Path:
    """
    Copies the files the Space needs into a clean folder.

    :param target: Folder to fill; it is emptied first.
    :param title: Title for the Space card.
    :param emoji: Emoji for the Space card.
    :return: The staging folder.
    """
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    # The desktop windows are never imported by the server, and their packages
    # (customtkinter, tkinterdnd2) are not installed in the container.
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", "gui", "adobe_gui.py", "gui_app.py"
    )
    for name in INCLUDE:
        shutil.copytree(ROOT / name, target / name, ignore=ignore)

    # Hugging Face builds the Dockerfile at the root of the repository.
    shutil.copy2(ROOT / "webapp" / "Dockerfile", target / "Dockerfile")
    (target / "README.md").write_text(
        SPACE_CARD.format(title=title, emoji=emoji), encoding="utf-8"
    )
    (target / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    return target


def resolve_token(explicit: Optional[str]) -> str:
    """
    Finds a Hugging Face token.

    :param explicit: Token passed on the command line.
    :return: The token.
    :raises SystemExit: When no token can be found.
    """
    if explicit:
        return explicit.strip()
    for variable in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(variable, "").strip()
        if value:
            return value

    # The cached login, written by "hf auth login".
    try:
        from huggingface_hub import get_token

        cached = get_token()
        if cached:
            return cached
    except Exception:
        pass

    try:  # Older versions of the library keep it here instead.
        from huggingface_hub import HfFolder

        cached = HfFolder.get_token()
        if cached:
            return cached
    except Exception:
        pass

    sys.exit(
        "No Hugging Face token found.\n"
        "Create one at https://huggingface.co/settings/tokens with write access, then either\n"
        "  set HF_TOKEN=..., pass --token, or run: hf auth login"
    )


def deploy(arguments: argparse.Namespace) -> str:
    """
    Creates or updates the Space and uploads the application.

    :param arguments: Parsed command line.
    :return: The public URL of the Space.
    """
    from huggingface_hub import HfApi

    token = resolve_token(arguments.token)
    password = arguments.password or secrets.token_urlsafe(9)

    staging = Path(tempfile.gettempdir()) / "pdf-editor-space"
    stage(staging, arguments.title, arguments.emoji)

    api = HfApi(token=token)
    api.create_repo(
        repo_id=arguments.space,
        repo_type="space",
        space_sdk="docker",
        private=bool(arguments.private),
        exist_ok=True,
    )

    # The password lives only in the Space secrets, never in the uploaded files.
    api.add_space_secret(repo_id=arguments.space, key="APP_PASSWORD", value=password)
    api.add_space_secret(
        repo_id=arguments.space, key="APP_SECRET_KEY", value=secrets.token_hex(32)
    )

    api.upload_folder(
        repo_id=arguments.space,
        repo_type="space",
        folder_path=str(staging),
        commit_message="Deploy the PDF editor web front end",
    )

    url = "https://huggingface.co/spaces/" + arguments.space
    print("Space:     " + url)
    print("Password:  " + password)
    print("")
    print("The first build takes a few minutes. Watch it on the Space's Logs tab.")
    return url


def main(argv: Optional[List[str]] = None) -> int:
    """
    Command line entry point.

    :param argv: Arguments to parse; defaults to ``sys.argv``.
    :return: Process exit code.
    """
    arguments = parse_arguments(argv)
    if arguments.stage_only:
        folder = stage(
            Path(tempfile.gettempdir()) / "pdf-editor-space", arguments.title, arguments.emoji
        )
        print(folder)
        return 0
    deploy(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
