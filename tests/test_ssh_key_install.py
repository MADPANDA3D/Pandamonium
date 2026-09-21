"""MAD-976: password-assisted key install for managed SSH connections.

The node password is used once to append this connection's public key to the
node's authorized_keys. These tests prove the password never reaches argv, the
askpass helper, the audit, or any payload, and that the path fails closed
without a pinned host key or a password.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
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
PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:conn01a2b3"
HOST_KEY_LINE = "100.115.46.123 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyMaterialForTests"
PASSWORD = "correct horse battery staple"


def _process(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def ssh_env(tmp_path, monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(ssh_routes, "SessionLocal", SessionLocal)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    monkeypatch.setattr(ssh_connections, "SSH_CONNECTIONS_DIR", str(tmp_path / "ssh_connections"))
    monkeypatch.setattr(ssh_connections, "SSH_AUDIT_FILE", str(tmp_path / "ssh_audit.jsonl"))

    yield SimpleNamespace(SessionLocal=SessionLocal, engine=engine, tmpfile=tmpfile, tmp_path=tmp_path)
    engine.dispose()
    tmpfile.close()


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(ssh_routes, "require_admin", lambda request: None)
    return ssh_routes.setup_ssh_routes()


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


def _insert_connection(SessionLocal, connection_id: str = NODE_ID, **overrides):
    values = {
        "id": connection_id,
        "label": "madpanda-observability",
        "host": "100.115.46.123",
        "user": "leo",
        "port": 22,
        "keyless": True,
        "private_key": PRIVATE_KEY,
        "public_key": PUBLIC_KEY,
        "host_key": HOST_KEY_LINE,
        "host_key_fingerprint": "SHA256:TEST",
        "host_key_type": "ssh-ed25519",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    values.update(overrides)
    with SessionLocal() as session:
        session.add(database.SshConnection(**values))
        session.commit()


def _row(ssh_env):
    with ssh_env.SessionLocal() as session:
        return session.query(database.SshConnection).filter_by(id=NODE_ID).one()


# ── install primitive ────────────────────────────────────────────────────


def test_install_fails_closed_without_pinned_host_key(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal, host_key=None)
    called = {"ran": False}

    def _fake(argv, timeout=None, env=None):
        called["ran"] = True
        return _process(0)

    monkeypatch.setattr(ssh_connections, "_run_command_env", _fake)

    result = ssh_connections.install_public_key(_row(ssh_env), PASSWORD)

    assert result["ok"] is False
    assert result["state"] == "host_key_unknown"
    assert called["ran"] is False


def test_install_requires_a_password(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(ssh_connections, "_run_command_env", lambda *a, **k: _process(0))

    result = ssh_connections.install_public_key(_row(ssh_env), "")

    assert result["ok"] is False
    assert result["reason"] == "missing_password"


def test_install_requires_a_public_key(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal, public_key=None)
    monkeypatch.setattr(ssh_connections, "_run_command_env", lambda *a, **k: _process(0))

    result = ssh_connections.install_public_key(_row(ssh_env), PASSWORD)

    assert result["ok"] is False
    assert result["reason"] == "missing_public_key"


def test_install_appends_key_and_never_exposes_the_password(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    captured = {}

    def _fake(argv, timeout=None, env=None):
        captured["argv"] = argv
        captured["env"] = env
        captured["askpass"] = Path(env["SSH_ASKPASS"]).read_text(encoding="utf-8")
        return _process(0, "installed\n", "")

    monkeypatch.setattr(ssh_connections, "_run_command_env", _fake)

    result = ssh_connections.install_public_key(_row(ssh_env), PASSWORD)

    assert result["ok"] is True
    assert result["state"] == "connected"
    joined = " ".join(captured["argv"])
    assert PUBLIC_KEY in joined
    assert "authorized_keys" in joined
    assert PASSWORD not in joined  # never in argv
    assert PASSWORD not in captured["askpass"]  # never baked into the helper
    assert captured["env"]["SSH_ASKPASS_REQUIRE"] == "force"
    assert captured["env"]["PANDAMONIUM_SSH_PASSWORD"] == PASSWORD
    assert PASSWORD not in json.dumps(result)


def test_install_classifies_auth_failure_without_echoing_stderr(ssh_env, monkeypatch):
    _insert_connection(ssh_env.SessionLocal)
    monkeypatch.setattr(
        ssh_connections,
        "_run_command_env",
        lambda *a, **k: _process(255, "", "leo@host: Permission denied (publickey,password)."),
    )

    result = ssh_connections.install_public_key(_row(ssh_env), "wrong-password")

    assert result["ok"] is False
    assert result["state"] == "auth_failed"
    encoded = json.dumps(result)
    assert "Permission denied" not in encoded
    assert "wrong-password" not in encoded


# ── route ────────────────────────────────────────────────────────────────


def test_install_route_requires_admin(monkeypatch, router):
    def _deny(request):
        raise HTTPException(403, "admin required")

    monkeypatch.setattr(ssh_routes, "require_admin", _deny)
    endpoint = _route(router, "/api/ssh/connections/{connection_id}/install-key", "POST")
    with pytest.raises(HTTPException) as excinfo:
        endpoint(_admin_request(), connection_id=NODE_ID, password=PASSWORD)
    assert excinfo.value.status_code == 403


def test_install_route_rejects_empty_password(ssh_env, router):
    _insert_connection(ssh_env.SessionLocal)
    endpoint = _route(router, "/api/ssh/connections/{connection_id}/install-key", "POST")
    with pytest.raises(HTTPException) as excinfo:
        endpoint(_admin_request(), connection_id=NODE_ID, password="")
    assert excinfo.value.status_code == 400


def test_install_route_confirms_connection_after_install(ssh_env, router, monkeypatch):
    _insert_connection(ssh_env.SessionLocal, last_status="auth_failed")
    monkeypatch.setattr(
        ssh_connections,
        "install_public_key",
        lambda connection, password: {"ok": True, "state": "connected", "reason": "", "message": "installed"},
    )
    monkeypatch.setattr(
        ssh_connections,
        "run_connection_test",
        lambda connection: {"ok": True, "state": "connected", "reason": "", "message": ssh_connections.CONNECTED_MESSAGE},
    )

    endpoint = _route(router, "/api/ssh/connections/{connection_id}/install-key", "POST")
    payload = endpoint(_admin_request(), connection_id=NODE_ID, password=PASSWORD)

    assert payload["ok"] is True
    assert payload["status"]["state"] == "connected"
    assert PASSWORD not in json.dumps(payload)

    with ssh_env.SessionLocal() as session:
        row = session.query(database.SshConnection).filter_by(id=NODE_ID).one()
        assert row.last_status == "connected"

    audit = Path(ssh_connections.SSH_AUDIT_FILE).read_text(encoding="utf-8")
    assert "install-key" in audit
    assert PASSWORD not in audit
