"""Server-only CalDAV credential storage.

CalDAV account metadata remains in user preferences, but secrets live in the
existing owner-scoped ``integrations`` table.  Purpose-specific fields prevent
provider changes from reinterpreting (for example) a Google refresh token as a
generic CalDAV password.
"""

from __future__ import annotations

import uuid
from typing import Any

from core.database import Integration, SessionLocal


INTEGRATION_TYPE = "caldav_credentials"
BASIC_PASSWORD = "basic_password"
GOOGLE_ACCESS_TOKEN = "google_access_token"
GOOGLE_REFRESH_TOKEN = "google_refresh_token"
_PURPOSES = frozenset({BASIC_PASSWORD, GOOGLE_ACCESS_TOKEN, GOOGLE_REFRESH_TOKEN})
_UNSET = object()


def _query(db: Any, owner: str | None, account_id: str):
    query = db.query(Integration).filter(
        Integration.type == INTEGRATION_TYPE,
        Integration.name == account_id,
    )
    if owner is None:
        return query.filter(Integration.owner.is_(None))
    return query.filter(Integration.owner == owner)


def set_credentials(
    owner: str | None,
    account_id: str,
    *,
    basic_password: object = _UNSET,
    google_access_token: object = _UNSET,
    google_refresh_token: object = _UNSET,
) -> None:
    """Atomically update only the explicitly supplied credential purposes."""
    if not account_id:
        raise ValueError("CalDAV account id is required")

    updates = {
        BASIC_PASSWORD: basic_password,
        GOOGLE_ACCESS_TOKEN: google_access_token,
        GOOGLE_REFRESH_TOKEN: google_refresh_token,
    }
    if all(value is _UNSET for value in updates.values()):
        return

    from src.secret_storage import encrypt

    db = SessionLocal()
    try:
        row = _query(db, owner, account_id).first()
        config = dict(row.config or {}) if row else {}
        for purpose, value in updates.items():
            if value is _UNSET:
                continue
            if value:
                config[purpose] = encrypt(str(value))
            else:
                config.pop(purpose, None)

        if config:
            if row is None:
                row = Integration(
                    id=uuid.uuid4().hex,
                    owner=owner,
                    name=account_id,
                    type=INTEGRATION_TYPE,
                    config=config,
                    enabled=True,
                )
                db.add(row)
            else:
                row.config = config
                row.enabled = True
        elif row is not None:
            db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_secret(owner: str | None, account_id: str, purpose: str) -> str:
    """Return one decrypted purpose, never falling back to another purpose."""
    if purpose not in _PURPOSES or not account_id:
        return ""
    db = SessionLocal()
    try:
        row = _query(db, owner, account_id).first()
        encrypted = (row.config or {}).get(purpose, "") if row else ""
    finally:
        db.close()
    if not encrypted:
        return ""
    from src.secret_storage import decrypt

    return decrypt(str(encrypted))


def has_secret(owner: str | None, account_id: str, purpose: str) -> bool:
    return bool(get_secret(owner, account_id, purpose))


def retain_accounts(owner: str | None, account_ids: set[str]) -> None:
    """Delete credential rows whose account metadata was removed."""
    db = SessionLocal()
    try:
        query = db.query(Integration).filter(Integration.type == INTEGRATION_TYPE)
        query = query.filter(
            Integration.owner.is_(None) if owner is None else Integration.owner == owner
        )
        for row in query.all():
            if row.name not in account_ids:
                db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
