"""Server-side persistence for the active workspace (MAD-883).

The workspace was previously a per-browser localStorage value sent on each
chat request, so other clients sharing a chat could not see it and the in-app
agent could not set it. These helpers store the vetted workspace on the
session row so it is shared by every surface that opens that chat.

Confinement is unchanged: callers must vet the path with ``vet_workspace``
before persisting, and every turn re-vets the stored value before binding it,
so a folder deleted after it was saved is dropped instead of trusted.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.database import SessionLocal
from core.database import Session as DbSession

logger = logging.getLogger(__name__)


def read_session_workspace(session_id: Optional[str]) -> str:
    """Return the stored workspace for a session ("" when none/unset)."""
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    db = SessionLocal()
    try:
        row = db.query(DbSession.workspace).filter(DbSession.id == sid).first()
        if row is None:
            return ""
        return str(row.workspace or "")
    except Exception as exc:
        logger.warning("Failed to read workspace for session %s: %s", sid, exc)
        return ""
    finally:
        db.close()


def set_session_workspace(session_id: Optional[str], path: str) -> bool:
    """Persist a (already vetted) workspace on the session row. Empty clears."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    value = str(path or "").strip() or None
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == sid).first()
        if row is None:
            return False
        row.workspace = value
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Failed to persist workspace for session %s: %s", sid, exc)
        return False
    finally:
        db.close()


def clear_session_workspace(session_id: Optional[str]) -> bool:
    """Remove any stored workspace for the session."""
    return set_session_workspace(session_id, "")
