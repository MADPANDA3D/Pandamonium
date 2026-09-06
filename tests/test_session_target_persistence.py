import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.database as cdb


def test_agent_target_migration_defaults_legacy_sessions_to_jarvis(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO sessions (id, name) VALUES ('legacy', 'Legacy chat')")
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")
    cdb._migrate_add_agent_target_column()

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        row = connection.execute(
            "SELECT agent_target FROM sessions WHERE id = 'legacy'"
        ).fetchone()
    finally:
        connection.close()
    assert "agent_target" in columns
    assert row == ("jarvis",)


def test_agent_target_validation_accepts_only_configured_identities(monkeypatch):
    import routes.session_routes as session_routes
    import src.agent_worker_adapters as adapters

    monkeypatch.setattr(
        adapters,
        "worker_catalog",
        lambda: {
            "hermes": {"configured": True},
            "pc-codex": {"configured": True},
            "offline": {"configured": False},
        },
    )

    assert session_routes._validated_session_agent_target(None) == "jarvis"
    assert session_routes._validated_session_agent_target("hermes") == "hermes"
    assert session_routes._validated_session_agent_target("pc-codex") == "pc-codex"
    with pytest.raises(HTTPException) as invalid:
        session_routes._validated_session_agent_target("../secret")
    assert invalid.value.status_code == 400
    with pytest.raises(HTTPException) as unconfigured:
        session_routes._validated_session_agent_target("offline")
    assert unconfigured.value.status_code == 400


def test_persisted_agent_target_wins_over_browser_routing_state():
    from routes.chat_routes import _authoritative_agent_target

    assert _authoritative_agent_target(
        SimpleNamespace(agent_target="hermes"),
        "pc-codex",
    ) == "hermes"
    assert _authoritative_agent_target(SimpleNamespace(), "pc-codex") == "pc-codex"
