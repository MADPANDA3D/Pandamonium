"""MAD-936: governed agent SSH access to configured nodes.

Covers the per-connection allowlist, output/timeout bounds, redacted audit,
denied paths (unconfigured node, disallowed command, auth failure, unpinned
host key), authority classification, and the Settings policy endpoint.

The system ssh binary is replaced with a fake bounded runner, so nothing here
dials a network or touches a real node.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "core.database",
    "src.ssh_connections",
    "routes.ssh_routes",
    "src.agent_tools.ssh_tools",
):
    import core.database as database
    import routes.ssh_routes as ssh_routes
    import src.ssh_connections as ssh
    import src.agent_tools.ssh_tools as ssh_tools


NODE_ID = "conn01a2b3"
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "fake-agent-key-material-for-tests\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:conn01a2b3"
HOST_KEY_LINE = "203.0.113.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostKeyMaterialForTests"
HOST_KEY_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _process(returncode: int = 0, stdout: str = "", stderr: str = "", truncated: bool = False):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr, truncated=truncated)


@pytest.fixture
def env(tmp_path, monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(ssh_routes, "SessionLocal", SessionLocal)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    monkeypatch.setattr(ssh, "SSH_CONNECTIONS_DIR", str(tmp_path / "ssh_connections"))
    monkeypatch.setattr(ssh, "SSH_AUDIT_FILE", str(tmp_path / "ssh_audit.jsonl"))

    yield SimpleNamespace(
        SessionLocal=SessionLocal,
        engine=engine,
        tmpfile=tmpfile,
        tmp_path=tmp_path,
    )
    engine.dispose()
    tmpfile.close()


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
    with SessionLocal() as session:
        return session.query(database.SshConnection).filter_by(id=connection_id).one()


def _audit_lines(env):
    path = env.tmp_path / "ssh_audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _admin_request(user: str = "admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


# ── allowlist ────────────────────────────────────────────────────────────


def test_default_policy_is_read_only_and_excludes_dangerous_commands():
    assert "ls" in ssh.DEFAULT_ALLOWED_COMMANDS
    assert "cat" in ssh.DEFAULT_ALLOWED_COMMANDS
    assert "rm" not in ssh.DEFAULT_ALLOWED_COMMANDS
    assert "sudo" not in ssh.DEFAULT_ALLOWED_COMMANDS
    assert "systemctl" not in ssh.DEFAULT_ALLOWED_COMMANDS


def test_agent_run_rejects_command_outside_connection_policy(env):
    connection = _insert_connection(
        env.SessionLocal, allowed_commands=json.dumps(["uptime"])
    )

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "run", {"command": "df -h"})

    assert caught.value.code == "command_not_allowed"
    assert "not allowed" in caught.value.message.lower()


def test_agent_run_rejects_shell_metacharacters(env):
    connection = _insert_connection(
        env.SessionLocal, allowed_commands=json.dumps(["uptime"])
    )

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "run", {"command": "uptime; rm -rf /"})

    assert caught.value.code == "invalid_command"


def test_agent_run_executes_custom_allowlisted_command(env, monkeypatch):
    connection = _insert_connection(
        env.SessionLocal, allowed_commands=json.dumps(["uptime"])
    )
    seen = {}

    def _runner(argv, *, timeout, max_bytes):
        seen["argv"] = argv
        seen["timeout"] = timeout
        seen["max_bytes"] = max_bytes
        return _process(returncode=0, stdout=" 12:00 up 3 days\n")

    monkeypatch.setattr(ssh, "_run_command_bounded", _runner)

    result = ssh.execute_agent_operation(connection, "run", {"command": "uptime"})

    assert result["output"].startswith(" 12:00 up")
    assert seen["argv"][-1] == "uptime"
    assert seen["max_bytes"] == ssh.AGENT_RUN_MAX_BYTES
    assert seen["timeout"] == ssh.AGENT_COMMAND_TIMEOUT_SECONDS
    assert "BatchMode=yes" in " ".join(seen["argv"])
    assert "StrictHostKeyChecking=yes" in " ".join(seen["argv"])


def test_run_command_validation_normalizes_whitespace():
    command = ssh.validate_run_command("  df   -h  ", ["df"])
    assert command == "df -h"


# ── bounds ───────────────────────────────────────────────────────────────


def test_agent_read_truncates_at_bound(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    payload = "x" * (ssh.AGENT_READ_MAX_BYTES + 10)
    monkeypatch.setattr(
        ssh, "_run_command_bounded", lambda argv, *, timeout, max_bytes: _process(returncode=0, stdout=payload)
    )

    result = ssh.execute_agent_operation(connection, "read", {"path": "/var/log/app.log"})

    assert result["truncated"] is True
    assert "truncated" in result["output"]
    assert len(result["output"]) <= ssh.AGENT_READ_MAX_BYTES + 120
    assert result["limits"]["max_output_bytes"] == ssh.AGENT_READ_MAX_BYTES


def test_agent_read_applies_requested_smaller_bound(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    seen = {}

    def _runner(argv, *, timeout, max_bytes):
        seen["command"] = argv[-1]
        seen["max_bytes"] = max_bytes
        return _process(returncode=0, stdout="abcd")

    monkeypatch.setattr(ssh, "_run_command_bounded", _runner)

    result = ssh.execute_agent_operation(connection, "read", {"path": "/tmp/notes.txt", "max_bytes": 4})

    assert result["output"] == "abcd"
    assert "head -c 5 " in seen["command"]
    assert "/tmp/notes.txt" in seen["command"]
    assert seen["max_bytes"] == 5


def test_agent_list_bounds_output(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh,
        "_run_command_bounded",
        lambda argv, *, timeout, max_bytes: _process(
            returncode=0, stdout="file.txt\n" * 10, truncated=True
        ),
    )

    result = ssh.execute_agent_operation(connection, "list", {"path": "/srv"})

    assert result["truncated"] is True
    assert result["limits"]["max_output_bytes"] == ssh.AGENT_LIST_MAX_BYTES


def test_agent_read_requires_a_path(env):
    connection = _insert_connection(env.SessionLocal)

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "read", {})

    assert caught.value.code == "invalid_path"


def test_agent_path_rejects_control_characters(env):
    connection = _insert_connection(env.SessionLocal)

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "read", {"path": "/tmp/bad\npath"})

    assert caught.value.code == "invalid_path"


# ── denied / fail-closed paths ───────────────────────────────────────────


def test_unconfigured_connection_fails_closed_with_honest_copy(env):
    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.resolve_saved_connection("Main VPS")

    assert caught.value.code == "not_found"
    assert "Settings" in caught.value.message


def test_ambiguous_label_fails_closed(env):
    _insert_connection(env.SessionLocal, NODE_ID, label="VPS")
    _insert_connection(env.SessionLocal, "conn02c3d4", label="vps")

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.resolve_saved_connection("VPS")

    assert caught.value.code == "ambiguous"


def test_agent_requires_pinned_host_key(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal, host_key=None)
    called = {"runner": False}

    def _runner(*args, **kwargs):
        called["runner"] = True
        return _process(returncode=0)

    monkeypatch.setattr(ssh, "_run_command_bounded", _runner)

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "list", {"path": "/srv"})

    assert caught.value.code == "host_key_unknown"
    assert called["runner"] is False


def test_agent_auth_failure_is_classified_without_echoing_stderr(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh,
        "_run_command_bounded",
        lambda argv, *, timeout, max_bytes: _process(
            returncode=255, stderr="Permission denied (publickey).\n"
        ),
    )

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "list", {"path": "/srv"})

    assert caught.value.code == "auth_failed"
    assert "Permission denied" not in caught.value.message


def test_agent_timeout_fails_closed(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh, "_run_command_bounded", lambda argv, *, timeout, max_bytes: _process(returncode=124)
    )

    with pytest.raises(ssh.SshAgentError) as caught:
        ssh.execute_agent_operation(connection, "list", {"path": "/srv"})

    assert caught.value.code == "timeout"


# ── audit ────────────────────────────────────────────────────────────────


def test_agent_calls_are_audited_with_bounds_and_no_secrets(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh,
        "_run_command_bounded",
        lambda argv, *, timeout, max_bytes: _process(returncode=0, stdout="secret-looking-output\n"),
    )

    ssh.execute_agent_operation(connection, "list", {"path": "/srv"}, actor="admin")
    with pytest.raises(ssh.SshAgentError):
        ssh.execute_agent_operation(connection, "run", {"command": "rm -rf /"}, actor="admin")

    entries = _audit_lines(env)
    assert len(entries) == 2
    success, denied = entries
    assert success["connection_id"] == NODE_ID
    assert success["action"] == "agent-list"
    assert success["actor"] == "admin"
    assert success["detail"]["output_bytes"] > 0
    assert success["detail"]["timeout_seconds"] == ssh.AGENT_COMMAND_TIMEOUT_SECONDS
    assert denied["state"] == "command_not_allowed"
    raw = (env.tmp_path / "ssh_audit.jsonl").read_text(encoding="utf-8")
    assert "fake-agent-key-material" not in raw
    assert "secret-looking-output" not in raw


# ── citation / tool surface ──────────────────────────────────────────────


def test_agent_list_result_is_cited_as_a_node_scoped_source(env, monkeypatch):
    connection = _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh,
        "_run_command_bounded",
        lambda argv, *, timeout, max_bytes: _process(returncode=0, stdout="notes.txt\n"),
    )

    result = ssh.execute_agent_operation(connection, "list", {"path": "/srv"})

    assert result["source"]["kind"] == "ssh_node"
    assert result["source"]["connection_id"] == NODE_ID
    assert result["source"]["label"] == "Home VPS"
    assert result["source"]["path"] == "/srv"
    assert "Home VPS" in result["citation"]
    assert "/srv" in result["citation"]


def test_tool_returns_bounded_result_with_citation(env, monkeypatch):
    _insert_connection(env.SessionLocal)
    monkeypatch.setattr(
        ssh,
        "_run_command_bounded",
        lambda argv, *, timeout, max_bytes: _process(returncode=0, stdout="notes.txt\n"),
    )

    tool = ssh_tools.SshNodeTool()
    result = asyncio.run(
        tool.execute(
            json.dumps({"connection": "Home VPS", "action": "list", "path": "/srv"}),
            {"owner": "admin"},
        )
    )

    assert result["exit_code"] == 0
    assert result["source"]["connection_id"] == NODE_ID
    assert "notes.txt" in result["output"]


def test_tool_fails_closed_without_a_saved_connection(env):
    tool = ssh_tools.SshNodeTool()
    result = asyncio.run(
        tool.execute(json.dumps({"connection": "Nope", "action": "list"}), {"owner": "admin"})
    )

    assert result["exit_code"] == 1
    assert result["code"] == "not_found"
    assert "Settings" in result["error"]
    assert _audit_lines(env)[0]["action"] == "agent-list"


def test_tool_rejects_unknown_action(env):
    tool = ssh_tools.SshNodeTool()
    result = asyncio.run(
        tool.execute(json.dumps({"connection": "Home VPS", "action": "exec"}), {"owner": "admin"})
    )

    assert result["exit_code"] == 1
    assert result["code"] == "invalid_action"


# ── authority classification ─────────────────────────────────────────────


def _ssh_call(action: str, **arguments):
    return {
        "name": "ssh_node",
        "target": "tool",
        "arguments": {"action": action, **arguments},
    }


def test_authority_classifies_read_and_list_as_read():
    from src.authority_protocol import action_effect_for

    assert action_effect_for(_ssh_call("list", path="/srv")) == "read"
    assert action_effect_for(_ssh_call("read", path="/srv/x")) == "read"


def test_authority_classifies_destructive_run_as_gated():
    from src.authority_protocol import action_effect_for

    assert (
        action_effect_for(_ssh_call("run", command="rm -rf /"))
        == "destructive_or_difficult_to_recover"
    )


# ── settings policy route ────────────────────────────────────────────────


def test_patch_route_stores_allowed_commands(env, monkeypatch):
    _insert_connection(env.SessionLocal)
    monkeypatch.setattr(ssh_routes, "require_admin", lambda request: None)
    router = ssh_routes.setup_ssh_routes()

    endpoint = _route(router, "/api/ssh/connections/{connection_id}", "PATCH")
    result = endpoint(
        _admin_request(),
        connection_id=NODE_ID,
        allowed_commands='["uptime", "df -h"]',
    )

    assert result["allowed_commands"] == ["uptime", "df -h"]
    assert result["allowed_commands_custom"] is True


def test_patch_route_rejects_unsafe_allowed_commands(env, monkeypatch):
    _insert_connection(env.SessionLocal)
    monkeypatch.setattr(ssh_routes, "require_admin", lambda request: None)
    router = ssh_routes.setup_ssh_routes()

    endpoint = _route(router, "/api/ssh/connections/{connection_id}", "PATCH")
    with pytest.raises(HTTPException) as caught:
        endpoint(_admin_request(), connection_id=NODE_ID, allowed_commands="uptime; rm -rf /")

    assert caught.value.status_code == 400


def test_connection_payload_lists_effective_allowlist(env):
    connection = _insert_connection(env.SessionLocal, allowed_commands=None)
    payload = ssh.connection_payload(connection)

    assert payload["allowed_commands"] == list(ssh.DEFAULT_ALLOWED_COMMANDS)
    assert payload["allowed_commands_custom"] is False
    assert '"private_key"' not in json.dumps(payload)
