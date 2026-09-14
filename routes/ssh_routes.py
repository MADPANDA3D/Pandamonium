"""Settings SSH Connections routes (MAD-935).

Admin-gated CRUD for operator-configured SSH nodes plus user-initiated key
generation, host-key pinning, and connection tests. The clicks in the Settings
tab are the approval: these routes never stack a second confirmation prompt,
and they never return private key material or raw ssh stderr.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request

from core.database import SessionLocal, SshConnection
from core.middleware import require_admin
from src import ssh_connections as ssh
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _actor(request: Request) -> str:
    return str(get_current_user(request) or "")


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _form_text(value, default: str = "") -> str:
    """Coerce a Form value safely; direct calls leave unset params as sentinels."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return default


def _require_row(session, connection_id: str) -> SshConnection:
    row = (
        session.query(SshConnection)
        .filter_by(id=ssh.validate_connection_id(connection_id))
        .first()
    )
    if row is None:
        raise HTTPException(404, "SSH connection not found.")
    return row


def setup_ssh_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ssh/connections")
    def list_ssh_connections(request: Request):
        require_admin(request)
        with SessionLocal() as session:
            rows = session.query(SshConnection).order_by(SshConnection.created_at.asc()).all()
            return {"connections": [ssh.connection_payload(row) for row in rows]}

    @router.post("/api/ssh/connections")
    def add_ssh_connection(
        request: Request,
        label: str = Form(""),
        host: str = Form(""),
        user: str = Form(""),
        port: str = Form("22"),
        keyless: str = Form("false"),
    ):
        require_admin(request)
        label_value = ssh.validate_label(_form_text(label))
        host_value = ssh.validate_host(_form_text(host))
        user_value = ssh.validate_user(_form_text(user))
        port_value = ssh.validate_port(_form_text(port, "22"))
        enabled = _truthy(_form_text(keyless, "false"))
        message = "Connection added."
        with SessionLocal() as session:
            row = SshConnection(
                id=ssh.new_connection_id(),
                label=label_value,
                host=host_value,
                user=user_value,
                port=port_value,
                keyless=enabled,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            if enabled:
                try:
                    pair = ssh.generate_keypair(row.id)
                except HTTPException as exc:
                    # Fail closed: no key means no keyless claim.
                    row.keyless = False
                    message = (
                        "Connection added, but the keypair could not be generated. "
                        f"{exc.detail} Try again from the connection's Generate key button."
                    )
                else:
                    row.private_key = pair["private_key"]
                    row.public_key = pair["public_key"]
                    try:
                        entries = ssh.scan_host_key(row.host, int(row.port or ssh.DEFAULT_SSH_PORT))
                        ssh.store_host_key(row, entries)
                        message = "Connection added. Install the public key on the node."
                    except ssh.SshScanError:
                        message = (
                            "Connection added with a new key. The node could not be reached "
                            "to pin its host key; scan and pin it when the node is online."
                        )
                session.commit()
                session.refresh(row)
            payload = ssh.connection_payload(row)
        ssh.record_ssh_audit(payload["id"], "add", "created", "", _actor(request))
        payload["message"] = message
        return ssh.redact_payload(payload)

    @router.patch("/api/ssh/connections/{connection_id}")
    def update_ssh_connection(
        request: Request,
        connection_id: str,
        label: Optional[str] = Form(None),
        host: Optional[str] = Form(None),
        user: Optional[str] = Form(None),
        port: Optional[str] = Form(None),
        allowed_commands: Optional[str] = Form(None),
    ):
        require_admin(request)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            if isinstance(label, str):
                row.label = ssh.validate_label(label)
            if isinstance(user, str):
                row.user = ssh.validate_user(user)
            if isinstance(port, str):
                row.port = ssh.validate_port(port)
            if isinstance(allowed_commands, str):
                # MAD-936: the agent command policy. Empty clears back to the
                # built-in read-only default; values are validated against a
                # simple-command charset so no shell syntax is stored.
                row.allowed_commands = ssh.validate_allowed_commands(allowed_commands)
            if isinstance(host, str):
                host_value = ssh.validate_host(host)
                if host_value != row.host:
                    # A new target must never inherit the old node's trust.
                    row.host_key = None
                    row.host_key_fingerprint = None
                    row.host_key_type = None
                    row.last_status = None
                    row.last_status_reason = None
                    row.last_status_message = None
                    row.last_checked_at = None
                row.host = host_value
            session.commit()
            session.refresh(row)
            payload = ssh.connection_payload(row)
        ssh.record_ssh_audit(payload["id"], "update", "updated", "", _actor(request))
        payload["message"] = "Connection updated."
        return ssh.redact_payload(payload)

    @router.delete("/api/ssh/connections/{connection_id}")
    def remove_ssh_connection(request: Request, connection_id: str):
        require_admin(request)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            row_id = row.id
            session.delete(row)
            session.commit()
        ssh.remove_connection_dir(row_id)
        ssh.record_ssh_audit(row_id, "remove", "removed", "", _actor(request))
        return {"ok": True}

    @router.post("/api/ssh/connections/{connection_id}/keypair")
    def generate_ssh_keypair(
        request: Request,
        connection_id: str,
        rotate: str = Form("false"),
    ):
        require_admin(request)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            if row.private_key and not _truthy(_form_text(rotate, "false")):
                payload = ssh.connection_payload(row)
                payload["message"] = "This connection already has a keypair."
            else:
                pair = ssh.generate_keypair(row.id)
                row.private_key = pair["private_key"]
                row.public_key = pair["public_key"]
                session.commit()
                session.refresh(row)
                payload = ssh.connection_payload(row)
                payload["message"] = "Public key generated. Install it on the node."
        ssh.record_ssh_audit(payload["id"], "keypair", "generated", "", _actor(request))
        return ssh.redact_payload(payload)

    @router.post("/api/ssh/connections/{connection_id}/keyless")
    def set_ssh_keyless(
        request: Request,
        connection_id: str,
        keyless: str = Form("true"),
    ):
        require_admin(request)
        enabled = _truthy(_form_text(keyless, "true"))
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            message = "Keyless connection disabled."
            if enabled:
                message = "Keyless connection enabled. Install the public key on the node."
                if not row.private_key:
                    pair = ssh.generate_keypair(row.id)
                    row.private_key = pair["private_key"]
                    row.public_key = pair["public_key"]
                if not row.host_key:
                    try:
                        entries = ssh.scan_host_key(row.host, int(row.port or ssh.DEFAULT_SSH_PORT))
                        ssh.store_host_key(row, entries)
                        message = "Keyless connection enabled. Install the public key on the node."
                    except ssh.SshScanError:
                        message = (
                            "The key is ready, but the node could not be reached to pin "
                            "its host key. Scan and pin it when the node is online."
                        )
            row.keyless = enabled
            session.commit()
            session.refresh(row)
            payload = ssh.connection_payload(row)
        ssh.record_ssh_audit(
            payload["id"],
            "keyless",
            "enabled" if enabled else "disabled",
            "",
            _actor(request),
        )
        payload["message"] = message
        return ssh.redact_payload(payload)

    @router.post("/api/ssh/connections/{connection_id}/private-key")
    def import_ssh_private_key(
        request: Request,
        connection_id: str,
        private_key: str = Form(""),
    ):
        require_admin(request)
        key_text = _form_text(private_key)
        if "PRIVATE KEY" not in key_text:
            raise HTTPException(400, "Paste an OpenSSH private key.")
        public_key = ssh.derive_public_key(key_text)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            row.private_key = key_text
            row.public_key = public_key
            row.keyless = True
            session.commit()
            session.refresh(row)
            payload = ssh.connection_payload(row)
        ssh.record_ssh_audit(payload["id"], "private-key", "imported", "", _actor(request))
        payload["message"] = "Private key saved. Install the public key on the node."
        return ssh.redact_payload(payload)

    @router.post("/api/ssh/connections/{connection_id}/host-key")
    def pin_ssh_host_key(request: Request, connection_id: str):
        require_admin(request)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            try:
                entries = ssh.scan_host_key(row.host, int(row.port or ssh.DEFAULT_SSH_PORT))
            except ssh.SshScanError as exc:
                raise HTTPException(400, str(exc))
            ssh.store_host_key(row, entries)
            session.commit()
            session.refresh(row)
            payload = ssh.connection_payload(row)
        payload["candidates"] = [
            {"key_type": entry["key_type"], "fingerprint": entry["fingerprint"]}
            for entry in entries
        ]
        payload["message"] = "Host key pinned. Verify the fingerprint matches the node."
        ssh.record_ssh_audit(payload["id"], "host-key", "pinned", "", _actor(request))
        return ssh.redact_payload(payload)

    @router.post("/api/ssh/connections/{connection_id}/test")
    def test_ssh_connection(request: Request, connection_id: str):
        require_admin(request)
        with SessionLocal() as session:
            row = _require_row(session, connection_id)
            result = ssh.run_connection_test(row)
            row.last_status = result["state"]
            row.last_status_reason = result.get("reason") or ""
            row.last_status_message = result.get("message") or ""
            row.last_checked_at = datetime.utcnow()
            session.commit()
            session.refresh(row)
            payload = ssh.connection_payload(row)
        ssh.record_ssh_audit(
            payload["id"],
            "test",
            result["state"],
            result.get("reason") or "",
            _actor(request),
        )
        payload.update(
            {
                "ok": bool(result.get("ok")),
                "state": result["state"],
                "reason": result.get("reason") or "",
                "message": result.get("message") or "",
            }
        )
        return ssh.redact_payload(payload)

    return router
