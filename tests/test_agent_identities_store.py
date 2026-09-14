"""MAD-929: multi-identity registry store, migration, and model profiles."""

import json
import sqlite3

import pytest

import core.database as cdb
import src.agent_identities as identities
from src.settings import DEFAULT_SETTINGS


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(identities, "_IDENTITIES_FILE", str(tmp_path / "agent_identities.json"))
    monkeypatch.setattr(identities, "load_settings", lambda: dict(DEFAULT_SETTINGS))
    return tmp_path / "agent_identities.json"


def _payload(**overrides):
    values = {
        "display_name": "Friday",
        "constitution": "Stay precise and report uncertainty honestly.",
        "constitution_version": "2026.1",
        "model_profile": {
            "chat": {"endpoint_id": "ep-1", "model": "qwen3-32b", "reasoning_level": "medium"},
            "lanes": {"utility": {"endpoint_id": "ep-local", "model": "llama-8b"}},
        },
    }
    values.update(overrides)
    return values


def test_reads_migrate_settings_identity_without_writing_a_file(store):
    entries = identities.list_identities()

    assert [entry["id"] for entry in entries] == ["assistant"]
    assert entries[0]["display_name"] == "Assistant"
    assert entries[0]["constitution"] == DEFAULT_SETTINGS["agent_constitution"]
    assert entries[0]["model_profile"]["chat"]["model"] == ""
    assert not store.exists()


def test_clean_install_never_materializes_private_names(store):
    entry = identities.migrated_identity()

    assert entry["display_name"] == "Assistant"
    assert "Friday" not in json.dumps(entry)
    assert "Jarvis" not in json.dumps(entry)


def test_ensure_migrated_persists_first_entry_and_is_idempotent(store, monkeypatch):
    monkeypatch.setattr(
        identities,
        "load_settings",
        lambda: {
            **DEFAULT_SETTINGS,
            "agent_id": "atlas",
            "agent_display_name": "Atlas",
            "agent_constitution_version": "7",
        },
    )
    identities.ensure_migrated()
    first = json.loads(store.read_text())

    identities.ensure_migrated()
    second = json.loads(store.read_text())

    assert first == second
    assert first["active_id"] == "atlas"
    assert [e["id"] for e in first["identities"]] == ["atlas"]


def test_create_list_update_duplicate_delete(store):
    created = identities.create_identity(_payload())
    assert created["id"] == "friday"
    assert identities.identity_prompt_values("friday")["agent_display_name"] == "Friday"

    updated = identities.update_identity(
        "friday", {"display_name": "Friday Ops", "model_profile": {"chat": {"model": "qwen3-14b"}}}
    )
    assert updated["display_name"] == "Friday Ops"
    assert updated["model_profile"]["chat"]["model"] == "qwen3-14b"

    duplicate = identities.duplicate_identity("friday")
    assert duplicate["id"] == "friday-copy"
    assert duplicate["display_name"] == "Friday Ops (copy)"
    assert len(identities.list_identities()) == 3

    result = identities.delete_identity("friday-copy")
    assert result["id"] == "friday-copy"
    assert [e["id"] for e in identities.list_identities()] == ["assistant", "friday"]


def test_delete_refuses_to_remove_the_last_identity(store):
    with pytest.raises(ValueError):
        identities.delete_identity("assistant")


def test_deleting_active_identity_moves_active_to_remaining_entry(store):
    identities.create_identity(_payload())
    identities.set_active_identity("friday")

    result = identities.delete_identity("friday")

    assert result["active_id"] == "assistant"
    assert identities.active_id() == "assistant"


def test_duplicate_ids_are_slugged_and_uniquified(store):
    first = identities.create_identity(_payload(display_name="Jarvis"))
    second = identities.create_identity(_payload(display_name="Jarvis"))

    assert first["id"] == "jarvis"
    assert second["id"] == "jarvis-2"


def test_store_round_trips_only_validated_entries(store):
    store.write_text(
        json.dumps(
            {
                "version": 1,
                "active_id": "ghost",
                "identities": [
                    {"id": "Ghost Name", "display_name": "Ghost", "constitution": "x"},
                    {
                        "id": "valid",
                        "display_name": "Valid",
                        "constitution": "Stay accurate.",
                        "constitution_version": "1",
                        "model_profile": {},
                    },
                ],
            }
        )
    )

    entries = identities.list_identities()

    assert [e["id"] for e in entries] == ["valid"]
    assert identities.active_id() == "valid"


def test_model_profile_validation_and_lane_defaults(store):
    profile = identities.sanitize_model_profile(
        {
            "chat": {"model": "  qwen3-32b  ", "reasoning_level": "HIGH"},
            "lanes": {"vision": {"model": "qwen-vl"}, "utility": {"endpoint_id": "", "model": ""}},
        }
    )

    assert profile["chat"] == {"endpoint_id": "", "model": "qwen3-32b", "reasoning_level": "high"}
    assert profile["lanes"]["vision"] == {"endpoint_id": "", "model": "qwen-vl"}
    assert profile["lanes"]["utility"] is None

    with pytest.raises(ValueError):
        identities.sanitize_model_profile({"chat": {"reasoning_level": "extreme"}})
    with pytest.raises(ValueError):
        identities.sanitize_model_profile({"lanes": "not-a-map"})


def test_lane_override_defaults_to_chat_lane(store):
    entry = identities.create_identity(_payload())
    expected_chat = {"endpoint_id": "ep-1", "model": "qwen3-32b"}

    assert identities.resolve_lane_profile(entry["id"], "utility") == {
        "endpoint_id": "ep-local",
        "model": "llama-8b",
    }
    assert identities.resolve_lane_profile(entry["id"], "image") == expected_chat

    empty = identities.create_identity(_payload(display_name="Plain", model_profile={}))
    assert identities.resolve_lane_profile(empty["id"], "research") is None
    with pytest.raises(ValueError):
        identities.resolve_lane_profile(empty["id"], "unknown-lane")


def test_public_identity_redacts_constitution_unless_admin(store):
    entry = identities.create_identity(_payload())

    redacted = identities.public_identity(entry)
    assert "constitution" not in redacted
    assert redacted["constitution_present"] is True
    assert "Stay precise" not in json.dumps(redacted)

    full = identities.public_identity(entry, include_constitution=True)
    assert full["constitution"] == entry["constitution"]


def test_sync_installation_identity_updates_active_entry_only_when_store_exists(store):
    identities.sync_installation_identity({"agent_id": "atlas"})
    assert not store.exists()

    identities.ensure_migrated()
    identities.sync_installation_identity(
        {"agent_display_name": "Atlas", "agent_constitution_version": "9"}
    )
    entry = identities.active_identity_entry()
    assert entry["display_name"] == "Atlas"
    assert entry["constitution_version"] == "9"


def test_migration_adds_session_identity_columns_idempotently(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("INSERT INTO sessions (id, name) VALUES ('legacy', 'Legacy chat')")
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(cdb, "DATABASE_URL", f"sqlite:///{db_path}")
    cdb._migrate_add_session_identity_columns()
    cdb._migrate_add_session_identity_columns()

    connection = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        row = connection.execute(
            "SELECT identity_id, reasoning_level FROM sessions WHERE id = 'legacy'"
        ).fetchone()
    finally:
        connection.close()
    assert {"identity_id", "reasoning_level"} <= columns
    assert row == (None, None)


def test_session_identity_helpers_read_the_session_row(monkeypatch):
    class _Query:
        def __init__(self, row):
            self._row = row

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self._row

    class _DB:
        def __init__(self, row):
            self._row = row

        def query(self, *args, **kwargs):
            return _Query(self._row)

        def close(self):
            pass

    monkeypatch.setattr(
        cdb,
        "Session",
        type(
            "FakeSession",
            (),
            {"identity_id": "identity_id", "reasoning_level": "reasoning_level", "id": "id"},
        ),
    )
    monkeypatch.setattr(cdb, "SessionLocal", lambda: _DB(("friday",)))
    assert identities.identity_id_for_session("s1") == "friday"

    monkeypatch.setattr(cdb, "SessionLocal", lambda: _DB(("high",)))
    assert identities.session_reasoning_level("s1") == "high"

    monkeypatch.setattr(cdb, "SessionLocal", lambda: _DB(("",)))
    assert identities.identity_id_for_session("s1") == ""

    monkeypatch.setattr(cdb, "SessionLocal", lambda: _DB(("nonsense",)))
    assert identities.session_reasoning_level("s1") == ""


def test_unknown_session_helper_rows_fail_safe(monkeypatch):
    class _DB:
        def query(self, *args, **kwargs):
            raise RuntimeError("no table")

        def close(self):
            pass

    monkeypatch.setattr(cdb, "SessionLocal", lambda: _DB())

    assert identities.identity_id_for_session("s1") == ""
    assert identities.session_reasoning_level("s1") == ""
