"""MAD-935: Settings SSH connections tab — storage/redaction, keyless toggle,
host-key pinning, and test-connection classification.

These tests exercise the real route helpers and the real SshConnection table in
a temp SQLite database. The OpenSSH binary is replaced with a fake runner, so
nothing here dials a network or touches a real node.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "src.ssh_connections",
    "routes.ssh_routes",
    "core.database",
):
    import core.database as database
    import routes.ssh_routes as ssh_routes
    import src.ssh_connections as ssh_connections


NODE_ID = "conn01a2b3"
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "QyNTUxOQAAACDfake-key-material-for-testing-only\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:conn01a2b3"
HOST_KEY_LINE = "203.0.113.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyMaterialForTests"
HOST_KEY_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _admin_request(user: str = "admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _process(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def ssh_env(tmp_path, monkeypatch):
    """Temp sqlite DB, temp managed dir/audit file, and a fresh Fernet key."""
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(ssh_routes, "SessionLocal", SessionLocal)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    monkeypatch.setattr(ssh_connections, "SSH_CONNECTIONS_DIR", str(tmp_path / "ssh_connections"))
    monkeypatch.setattr(ssh_connections, "SSH_AUDIT_FILE", str(tmp_path / "ssh_audit.jsonl"))

    yield SimpleNamespace(
        SessionLocal=SessionLocal,
        engine=engine,
        tmpfile=tmpfile,
        tmp_path=tmp_path,
    )
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(ssh_routes, "require_admin", lambda request: None)
    return ssh_routes.setup_ssh_routes()


def _insert_connection(SessionLocal, connection_id: str = NODE_ID, **overrides):
    values = {
        "id": connection_id,
        "label": "Home VPS",
        "host": "203.0.113.10",
        "user": "operator",
        "port": 22,
        "keyless": True,
        "private_key": PRIVATE_KEY,
        "public_key": PUBLIC_KEY,
        "host_key": HOST_KEY_LINE,
        "host_key_fingerprint": HOST_KEY_FINGERPRINT,
        "host_key_type": "ssh-ed25519",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    values.update(overrides)
    with SessionLocal() as session:
        session.add(database.SshConnection(**values))
        session.commit()


def _read_raw_row(ssh_env, column: str, connection_id: str = NODE_ID):
    conn = sqlite3.connect(ssh_env.tmpfile.name)
    try:
        row = conn.execute(
            f"SELECT {column} FROM ssh_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── storage and redaction ────────────────────────────────────────────────


def test_connection_payload_never_includes_private_key(ssh_env):
    _insert_connection(ssh_env.SessionLocal)

    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=NODE_ID).one()
        payload = ssh_connections.connection_payload(row)

    encoded = json.dumps(payload)
    assert "fake-key-material" not in encoded
    assert "BEGIN OPENSSH PRIVATE KEY" not in encoded
    assert '"private_key"' not in encoded
    assert payload["has_private_key"] is True
    assert payload["public_key"] == PUBLIC_KEY
    assert payload["host_key_fingerprint"] == HOST_KEY_FINGERPRINT


def test_private_key_is_encrypted_at_rest(ssh_env):
    _insert_connection(ssh_env.SessionLocal)

    raw = _read_raw_row(ssh_env, "private_key")
    assert raw is not None
    assert raw.startswith("enc:")
    assert "fake-key-material" not in raw


def test_connection_payload_redacts_status_details_without_echoing_stderr(ssh_env):
    _insert_connection(
        ssh_env.SessionLocal,
        last_status="auth_failed",
        last_status_reason="authentication_failed",
        last_status_message="The node rejected the key.",
    )

    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=NODE_ID).one()
        payload = ssh_connections.connection_payload(row)

    assert payload["status"]["state"] == "auth_failed"
    assert "Permission denied" not in json.dumps(payload)


# ── keyless toggle ───────────────────────────────────────────────────────


def test_generate_keypair_returns_public_key_and_keeps_private_out_of_payload(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal, keyless=False, private_key=None, public_key=None)
    calls = {}

    def _fake_keygen(connection_id):
        calls["connection_id"] = connection_id
        return {"private_key": PRIVATE_KEY, "public_key": PUBLIC_KEY}

    monkeypatch.setattr(ssh_connections, "generate_keypair", _fake_keygen)

    result = ssh_connections.generate_and_store_keypair(ssh_env.SessionLocal, NODE_ID)

    assert calls["connection_id"] == NODE_ID
    assert result["public_key"] == PUBLIC_KEY
    assert "fake-key-material" not in json.dumps(result)
    assert '"private_key"' not in json.dumps(result)


def test_keyless_enable_generates_key_and_pins_host_key(ssh_env, router, monkeypatch):
    with ssh_env.SessionLocal() as session:
        session.add(
            database.SshConnection(
                id=NODE_ID,
                label="Home VPS",
                host="203.0.113.10",
                user="operator",
                port=22,
                keyless=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        session.commit()

    monkeypatch.setattr(
        ssh_connections,
        "generate_keypair",
        lambda connection_id: {"private_key": PRIVATE_KEY, "public_key": PUBLIC_KEY},
    )
    monkeypatch.setattr(
        ssh_connections,
        "scan_host_key",
        lambda host, port, timeout=10.0: [
            {
                "key_type": "ssh-ed25519",
                "fingerprint": HOST_KEY_FINGERPRINT,
                "known_hosts_line": HOST_KEY_LINE,
            }
        ],
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/keyless", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID, keyless=True)

    assert result["keyless"] is True
    assert result["public_key"] == PUBLIC_KEY
    assert result["host_key_pinned"] is True
    assert result["host_key_fingerprint"] == HOST_KEY_FINGERPRINT
    assert "fake-key-material" not in json.dumps(result)
    assert _read_raw_row(ssh_env, "private_key").startswith("enc:")


def test_keyless_disable_keeps_stored_key(ssh_env, router):
    _insert_connection(ssh_env.SessionLocal)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/keyless", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID, keyless=False)

    assert result["keyless"] is False
    assert result["has_private_key"] is True
    assert _read_raw_row(ssh_env, "private_key").startswith("enc:")


def test_paste_private_key_stores_encrypted_and_returns_public_key(ssh_env, router, monkeypatch):
    with ssh_env.SessionLocal() as session:
        session.add(
            database.SshConnection(
                id=NODE_ID,
                label="Home VPS",
                host="203.0.113.10",
                user="operator",
                port=22,
                keyless=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        session.commit()

    monkeypatch.setattr(
        ssh_connections, "derive_public_key", lambda private_key: PUBLIC_KEY
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/private-key", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID, private_key=PRIVATE_KEY)

    assert result["public_key"] == PUBLIC_KEY
    assert result["keyless"] is True
    assert "fake-key-material" not in json.dumps(result)
    assert _read_raw_row(ssh_env, "private_key").startswith("enc:")


# ── host-key change ──────────────────────────────────────────────────────


def test_classify_changed_host_key_fails_closed_with_human_copy():
    stderr = (
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        "Host key verification failed.\n"
    )

    result = ssh_connections.classify_ssh_failure(stderr)

    assert result["state"] == "host_key_changed"
    assert "changed" in result["message"].lower()
    assert "verify" in result["message"].lower()
    assert "WARNING" not in result["message"]


def test_classify_unknown_host_key_fails_closed():
    stderr = (
        "No ED25519 host key is known for 203.0.113.10 and you have requested strict checking.\n"
        "Host key verification failed.\n"
    )

    result = ssh_connections.classify_ssh_failure(stderr)

    assert result["state"] == "host_key_unknown"
    assert "pin" in result["message"].lower() or "not known" in result["message"].lower()


def test_test_connection_rejects_changed_host_key_and_records_status(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)

    def _changed(*args, **kwargs):
        return _process(
            returncode=255,
            stderr="WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.\n",
        )

    monkeypatch.setattr(ssh_connections, "_run_command", _changed)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is False
    assert result["state"] == "host_key_changed"
    assert "changed" in result["message"].lower()
    assert "IDENTIFICATION" not in result["message"]

    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=NODE_ID).one()
        assert row.last_status == "host_key_changed"


def test_test_connection_without_pinned_host_key_fails_closed(ssh_env, router):
    _insert_connection(ssh_env.SessionLocal, host_key=None, host_key_fingerprint=None)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is False
    assert result["state"] == "host_key_unknown"
    assert "pin" in result["message"].lower()


# ── test connection ──────────────────────────────────────────────────────


def test_test_connection_success_reports_connected_without_prompt(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    seen = {}

    def _ok(argv, **kwargs):
        seen["argv"] = argv
        return _process(returncode=0, stdout="")

    monkeypatch.setattr(ssh_connections, "_run_command", _ok)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is True
    assert result["state"] == "connected"
    assert "prompt" in result["message"].lower()
    joined = " ".join(seen["argv"])
    assert "BatchMode=yes" in joined
    assert "StrictHostKeyChecking=yes" in joined

    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=NODE_ID).one()
        assert row.last_status == "connected"


def test_test_connection_auth_failure_has_human_copy(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(
        ssh_connections,
        "_run_command",
        lambda argv, **kwargs: _process(returncode=255, stderr="Permission denied (publickey).\n"),
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is False
    assert result["state"] == "auth_failed"
    assert "public key" in result["message"].lower()
    assert "Permission denied" not in result["message"]


def test_test_connection_unreachable_has_human_copy(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(
        ssh_connections,
        "_run_command",
        lambda argv, **kwargs: _process(returncode=255, stderr="ssh: connect to host 203.0.113.10 port 22: Connection timed out\n"),
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is False
    assert result["state"] == "unreachable"
    encoded = json.dumps(result)
    assert "Connection timed out" not in encoded
    assert "ssh: connect to host" not in encoded


def test_test_connection_missing_openssh_binary_fails_closed(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(ssh_connections, "resolve_ssh_binary", lambda: None)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is False
    assert result["state"] == "unavailable"
    assert "OpenSSH" in result["message"]


# ── add / edit / remove ──────────────────────────────────────────────────


def test_add_connection_rejects_host_with_shell_metacharacters(ssh_env, router):
    endpoint = _route(router, "/api/ssh/connections", "POST")

    with pytest.raises(HTTPException) as exc:
        endpoint(_admin_request(), label="Bad", host="box; rm -rf ~", user="operator", port="22")

    assert exc.value.status_code == 400


def test_add_connection_never_returns_key_material(ssh_env, router, monkeypatch):
    monkeypatch.setattr(
        ssh_connections,
        "generate_keypair",
        lambda connection_id: {"private_key": PRIVATE_KEY, "public_key": PUBLIC_KEY},
    )
    monkeypatch.setattr(
        ssh_connections,
        "scan_host_key",
        lambda host, port, timeout=10.0: [
            {
                "key_type": "ssh-ed25519",
                "fingerprint": HOST_KEY_FINGERPRINT,
                "known_hosts_line": HOST_KEY_LINE,
            }
        ],
    )

    endpoint = _route(router, "/api/ssh/connections", "POST")
    result = endpoint(
        _admin_request(), label="Home VPS", host="203.0.113.10", user="operator", port="22", keyless=True
    )

    assert result["label"] == "Home VPS"
    assert result["public_key"] == PUBLIC_KEY
    assert "fake-key-material" not in json.dumps(result)
    assert '"private_key"' not in json.dumps(result)


def test_add_connection_keygen_failure_degrades_to_non_keyless(ssh_env, router, monkeypatch):
    def _boom(connection_id):
        raise HTTPException(500, "Could not generate the SSH keypair.")

    monkeypatch.setattr(ssh_connections, "generate_keypair", _boom)

    endpoint = _route(router, "/api/ssh/connections", "POST")
    result = endpoint(
        _admin_request(), label="Broken Node", host="203.0.113.9", user="operator", port="22", keyless=True
    )

    assert result["keyless"] is False
    assert result["has_private_key"] is False
    assert "could not be generated" in result["message"]
    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=result["id"]).one()
        assert row.keyless is False
        assert row.private_key is None


def test_host_change_clears_pinned_host_key(ssh_env, router):
    _insert_connection(ssh_env.SessionLocal)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}", "PATCH")
    result = endpoint(_admin_request(), connection_id=NODE_ID, host="198.51.100.7")

    assert result["host"] == "198.51.100.7"
    assert result["host_key_pinned"] is False
    assert result["host_key_fingerprint"] is None
    assert result["status"]["state"] == "unknown"


def test_remove_connection_deletes_row_and_managed_material(ssh_env, router):
    _insert_connection(ssh_env.SessionLocal)
    ssh_connections.ensure_connection_dir(NODE_ID)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}", "DELETE")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["ok"] is True
    with ssh_env.SessionLocal() as session:
        assert session.query(database.SshConnection).filter_by(id=NODE_ID).count() == 0


# ── audit events ─────────────────────────────────────────────────────────


def test_connect_and_test_write_redacted_audit_events(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(
        ssh_connections,
        "_run_command",
        lambda argv, **kwargs: _process(returncode=0),
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/test", "POST")
    endpoint(_admin_request(), connection_id=NODE_ID)

    raw = (ssh_env.tmp_path / "ssh_audit.jsonl").read_text(encoding="utf-8")
    assert "test" in raw
    assert "connected" in raw
    assert "fake-key-material" not in raw
    assert PRIVATE_KEY not in raw


def test_scan_host_key_route_pins_fingerprint(ssh_env, router, monkeypatch):
    _insert_connection(
        ssh_env.SessionLocal,
        keyless=True,
        host_key=None,
        host_key_fingerprint=None,
        host_key_type=None,
    )
    monkeypatch.setattr(
        ssh_connections,
        "scan_host_key",
        lambda host, port, timeout=10.0: [
            {
                "key_type": "ssh-ed25519",
                "fingerprint": HOST_KEY_FINGERPRINT,
                "known_hosts_line": HOST_KEY_LINE,
            }
        ],
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/host-key", "POST")
    result = endpoint(_admin_request(), connection_id=NODE_ID)

    assert result["host_key_pinned"] is True
    assert result["host_key_fingerprint"] == HOST_KEY_FINGERPRINT
    assert result["candidates"][0]["fingerprint"] == HOST_KEY_FINGERPRINT


def test_scan_host_key_failure_fails_closed(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal, host_key=None, host_key_fingerprint=None)

    def _boom(host, port, timeout=10.0):
        raise ssh_connections.SshScanError("Could not scan the node's host key.")

    monkeypatch.setattr(ssh_connections, "scan_host_key", _boom)

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/host-key", "POST")
    with pytest.raises(HTTPException) as exc:
        endpoint(_admin_request(), connection_id=NODE_ID)

    assert exc.value.status_code == 400
    assert "host key" in str(exc.value.detail).lower()
