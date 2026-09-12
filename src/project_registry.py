"""Pandamonium agent workstation projects (MAD-902).

Projects are real directories on the machine Pandamonium runs on. The registry
lives in ``DATA_DIR/projects.json``; new projects are created under
``DATA_DIR/projects`` by default. Imported folders are vetted with the same
rules as the chat workspace picker, so filesystem roots and sensitive paths can
never be registered as projects.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from core.atomic_io import atomic_write_json

# A project name is a single path segment: no separators, no traversal, no
# NUL, and bounded so it cannot become an unreasonable directory name.
_NAME_RE = re.compile(r"^[^/\\\x00]{1,64}$")

_lock = threading.Lock()


def _store_path() -> Path:
    from core.constants import DATA_DIR

    return Path(DATA_DIR) / "projects.json"


def projects_root() -> Path:
    """Default parent for created projects (installation data directory)."""
    from core.constants import DATA_DIR

    return Path(DATA_DIR) / "projects"


def _read_store() -> List[dict]:
    try:
        raw = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = raw.get("projects") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    projects: List[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        project_id = str(entry.get("id") or "").strip()
        path = str(entry.get("path") or "").strip()
        if not project_id or not path:
            continue
        projects.append({
            "id": project_id,
            "name": str(entry.get("name") or Path(path).name or "Project")[:80],
            "path": path,
            "created_at": str(entry.get("created_at") or ""),
        })
    return projects


def _write_store(projects: List[dict]) -> None:
    atomic_write_json(str(_store_path()), {"projects": projects}, indent=2)


def _decorate(project: dict) -> dict:
    """Add live availability state without storing it."""
    resolved = os.path.realpath(os.path.expanduser(project["path"]))
    exists = os.path.isdir(resolved)
    readable = exists and os.access(resolved, os.R_OK | os.X_OK)
    reason = ""
    if not exists:
        reason = "folder no longer exists"
    elif not readable:
        reason = "folder is not readable"
    return {**project, "resolved_path": resolved, "available": readable, "reason": reason}


def list_projects() -> List[dict]:
    with _lock:
        return [_decorate(project) for project in _read_store()]


def add_project(name: str = "", path: str = "") -> dict:
    """Import an existing folder or create a new project directory.

    Raises ValueError with a user-facing message when the input is unusable.
    """
    from src.tool_execution import vet_workspace

    with _lock:
        projects = _read_store()
        if path:
            resolved = vet_workspace(path)
            if not resolved:
                raise ValueError("That folder cannot be used as a project")
            display = (name or "").strip() or Path(resolved).name
            if any(existing["path"] == resolved for existing in projects):
                raise ValueError("That folder is already a project")
        else:
            candidate = (name or "").strip()
            if not candidate or candidate in {".", ".."} or not _NAME_RE.match(candidate):
                raise ValueError("Enter a project name without slashes")
            root = os.path.realpath(projects_root())
            resolved = os.path.realpath(os.path.join(root, candidate))
            try:
                inside_root = os.path.commonpath((root, resolved)) == root
            except ValueError:
                inside_root = False
            if not inside_root:
                raise ValueError("Project name must stay inside the projects folder")
            try:
                os.makedirs(resolved, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"Could not create the project folder: {exc.strerror or exc}")
            display = candidate
            if any(existing["path"] == resolved for existing in projects):
                raise ValueError("That project already exists")
        project = {
            "id": uuid.uuid4().hex[:8],
            "name": display[:80],
            "path": resolved,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        projects.append(project)
        _write_store(projects)
        return _decorate(project)


def remove_project(project_id: str) -> bool:
    """Forget a project (never deletes the directory on disk)."""
    with _lock:
        projects = _read_store()
        remaining = [project for project in projects if project["id"] != project_id]
        if len(remaining) == len(projects):
            return False
        _write_store(remaining)
        return True
