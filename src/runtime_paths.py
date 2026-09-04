"""Helpers for resolving runtime paths in source and frozen builds."""

import os
import sys


def get_app_root() -> str:
    """Return the app root directory.

    In normal source runs, this is the repository root. In a frozen Windows
    build, it is the bundle content root (PyInstaller's internal directory)
    so bundled runtime folders like `static/`, `scripts/`, and `data/` stay
    together with the executable payload.
    """
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_default_data_dir() -> str:
    """Return the default path to the data directory.

    In normal runs, this is a 'data' subdirectory under the app root.
    In frozen builds, it is a persistent user directory (~/.odysseus/data)
    to prevent SQLite databases and other persistent files from being
    written to the ephemeral, temporary extraction bundle directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".odysseus", "data")
    return os.path.join(get_app_root(), "data")


def get_default_extensions_dir(data_dir: str) -> str:
    """Keep managed Git checkouts out of a source tree by default."""
    app_root = os.path.abspath(get_app_root())
    resolved_data = os.path.abspath(data_dir)
    try:
        data_is_in_source = os.path.commonpath((app_root, resolved_data)) == app_root
    except ValueError:
        data_is_in_source = False
    if os.path.ismount(resolved_data) or not data_is_in_source:
        return os.path.join(resolved_data, "extensions")
    xdg_data = os.getenv("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(xdg_data, "odysseus", "extensions")
