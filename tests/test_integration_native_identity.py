import asyncio
from pathlib import Path
from types import SimpleNamespace

import core.database
import src.integrations as integrations


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return _Query(self.rows)

    def close(self):
        return None


def test_api_and_mcp_rows_with_same_origin_and_identity_are_one_connection(monkeypatch):
    server = SimpleNamespace(
        id="native-portal",
        name="Acme MCP Portal",
        url="https://portal.example.test/api/mcp",
        is_enabled=True,
    )
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db([server]))
    api = {
        "id": "legacy-api",
        "name": "Acme MCP Portal API",
        "base_url": "https://portal.example.test/api",
        "enabled": True,
    }

    matches = integrations.native_mcp_companions([api])

    assert matches == {
        "legacy-api": {
            "id": "native-portal",
            "name": "Acme MCP Portal",
            "is_enabled": True,
        }
    }


def test_identity_match_requires_both_name_and_origin(monkeypatch):
    servers = [
        SimpleNamespace(id="one", name="Acme MCP Portal", url="https://one.example.test/mcp", is_enabled=True),
        SimpleNamespace(id="two", name="Other MCP", url="https://portal.example.test/mcp", is_enabled=True),
    ]
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db(servers))
    api = {
        "id": "legacy-api",
        "name": "Acme MCP Portal API",
        "base_url": "https://portal.example.test/api",
    }

    assert integrations.native_mcp_companions([api]) == {}


def test_identity_match_fails_closed_for_invalid_ports(monkeypatch):
    server = SimpleNamespace(
        id="native-portal",
        name="Acme MCP Portal",
        url="https://portal.example.test:not-a-port/mcp",
        is_enabled=True,
    )
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db([server]))

    assert integrations.native_mcp_companions([{
        "id": "legacy-api",
        "name": "Acme MCP Portal API",
        "base_url": "https://portal.example.test/api",
    }]) == {}


def test_explicit_native_link_is_authoritative_across_different_origins(monkeypatch):
    server = SimpleNamespace(
        id="native-relay",
        name="Acme Relay",
        url="https://mcp.example.test/transport",
        is_enabled=True,
    )
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db([server]))
    api = {
        "id": "legacy-api",
        "name": "Acme Relay API",
        "base_url": "https://api.example.test/v1",
        "native_mcp_server_id": "native-relay",
    }

    assert integrations.native_mcp_companions([api]) == {
        "legacy-api": {
            "id": "native-relay",
            "name": "Acme Relay",
            "is_enabled": True,
        }
    }


def test_missing_explicit_native_target_does_not_rebind_by_name(monkeypatch):
    server = SimpleNamespace(
        id="replacement",
        name="Acme Relay",
        url="https://api.example.test/mcp",
        is_enabled=True,
    )
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db([server]))

    assert integrations.native_mcp_companions([{
        "id": "legacy-api",
        "name": "Acme Relay API",
        "base_url": "https://api.example.test/v1",
        "native_mcp_server_id": "deleted-server",
    }]) == {}


def test_unique_cross_origin_names_never_create_an_implicit_native_link(monkeypatch):
    server = SimpleNamespace(
        id="native-relay",
        name="Acme Relay MCP",
        url="https://mcp.example.test",
        is_enabled=True,
    )
    api = {
        "id": "legacy-relay",
        "name": "Acme Relay API",
        "base_url": "https://api.example.test",
    }
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db([server]))

    assert integrations.native_mcp_companions([api]) == {}
    assert "native_mcp_server_id" not in api


def test_proven_native_companion_links_exactly_one_cross_origin_row(monkeypatch):
    rows = [{
        "id": "legacy-relay",
        "name": "Acme Relay API",
        "base_url": "https://api.example.test",
        "auth_type": "header",
        "api_key": "Bearer shared-fixture-secret",
    }]
    saved = []
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    assert integrations.link_native_mcp_companion(
        "native-relay", "Acme Relay MCP", "shared-fixture-secret"
    ) == 1
    assert rows[0]["native_mcp_server_id"] == "native-relay"
    assert saved == [rows]


def test_native_companion_proof_fails_closed_for_mismatch_or_duplicate(monkeypatch):
    different_secret = [{
        "id": "wrong-secret",
        "name": "Acme Relay API",
        "api_key": "different-secret",
    }]
    saved = []
    monkeypatch.setattr(integrations, "load_integrations", lambda: different_secret)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    assert integrations.link_native_mcp_companion(
        "native-relay", "Acme Relay MCP", "shared-fixture-secret"
    ) == 0

    duplicate = [
        {"id": "one", "name": "Acme Relay API", "api_key": "shared-fixture-secret"},
        {"id": "two", "name": "Acme Relay Integration", "api_key": "shared-fixture-secret"},
    ]
    monkeypatch.setattr(integrations, "load_integrations", lambda: duplicate)

    assert integrations.link_native_mcp_companion(
        "native-relay", "Acme Relay MCP", "shared-fixture-secret"
    ) == 0
    assert all("native_mcp_server_id" not in row for row in duplicate)
    assert saved == []


def test_startup_reconciliation_uses_only_connected_static_credential_proof(monkeypatch):
    servers = [
        SimpleNamespace(
            id="connected-relay",
            name="Acme Relay MCP",
            transport="http",
            oauth_tokens='{"static_bearer_token":"shared-fixture-secret"}',
        ),
        SimpleNamespace(
            id="disconnected-relay",
            name="Other Relay MCP",
            transport="http",
            oauth_tokens='{"static_bearer_token":"other-fixture-secret"}',
        ),
    ]
    rows = [
        {
            "id": "legacy-connected",
            "name": "Acme Relay API",
            "base_url": "https://different-origin.example.test",
            "api_key": "shared-fixture-secret",
        },
        {
            "id": "legacy-disconnected",
            "name": "Other Relay API",
            "api_key": "other-fixture-secret",
        },
    ]
    saved = []
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db(servers))
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    changed = integrations.reconcile_proven_native_mcp_companion_links({
        "connected-relay": {"status": "connected", "transport": "http"},
        "disconnected-relay": {"status": "error", "transport": "http"},
    })

    assert changed == 1
    assert rows[0]["native_mcp_server_id"] == "connected-relay"
    assert "native_mcp_server_id" not in rows[1]
    assert saved == [rows]


def test_startup_reconciliation_rejects_malformed_or_unproven_credentials(monkeypatch):
    servers = [
        SimpleNamespace(
            id="malformed",
            name="Acme Relay MCP",
            transport="http",
            oauth_tokens="not-json",
        ),
        SimpleNamespace(
            id="oauth-only",
            name="Acme Relay MCP",
            transport="http",
            oauth_tokens='{"access_token":"not-a-static-proof"}',
        ),
    ]
    rows = [{
        "id": "legacy-relay",
        "name": "Acme Relay API",
        "api_key": "shared-fixture-secret",
    }]
    saved = []
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db(servers))
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    assert integrations.reconcile_proven_native_mcp_companion_links({
        "malformed": {"status": "connected", "transport": "http"},
        "oauth-only": {"status": "connected", "transport": "http"},
    }) == 0
    assert "native_mcp_server_id" not in rows[0]
    assert saved == []


def test_startup_reconciliation_rejects_non_http_or_transport_drift(monkeypatch):
    servers = [
        SimpleNamespace(
            id="stdio-relay",
            name="Acme Relay MCP",
            transport="stdio",
            oauth_tokens='{"static_bearer_token":"shared-fixture-secret"}',
        ),
        SimpleNamespace(
            id="drifted-relay",
            name="Other Relay MCP",
            transport="http",
            oauth_tokens='{"static_bearer_token":"other-fixture-secret"}',
        ),
    ]
    rows = [
        {"id": "one", "name": "Acme Relay API", "api_key": "shared-fixture-secret"},
        {"id": "two", "name": "Other Relay API", "api_key": "other-fixture-secret"},
    ]
    saved = []
    monkeypatch.setattr(core.database, "SessionLocal", lambda: _Db(servers))
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    assert integrations.reconcile_proven_native_mcp_companion_links({
        "stdio-relay": {"status": "connected", "transport": "stdio"},
        "drifted-relay": {"status": "connected", "transport": "sse"},
    }) == 0
    assert all("native_mcp_server_id" not in row for row in rows)
    assert saved == []


def test_native_link_is_server_owned_and_unlink_preserves_api_row(monkeypatch):
    rows = [{
        "id": "legacy-relay",
        "name": "Acme Relay API",
        "base_url": "https://api.example.test",
        "api_key": "fixture-secret",
        "native_mcp_server_id": "native-relay",
    }]
    saved = []
    monkeypatch.setattr(integrations, "load_integrations", lambda: rows)
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    updated = integrations.update_integration(
        "legacy-relay",
        {"description": "still here", "native_mcp_server_id": "attacker-choice"},
    )
    assert updated["native_mcp_server_id"] == "native-relay"
    assert integrations.unlink_native_mcp_companions("native-relay") == 1
    assert len(rows) == 1
    assert rows[0]["api_key"] == "fixture-secret"
    assert "native_mcp_server_id" not in rows[0]
    assert saved == [rows, rows]


def test_add_integration_strips_client_supplied_native_link(monkeypatch):
    saved = []
    monkeypatch.setattr(integrations, "load_integrations", lambda: [])
    monkeypatch.setattr(integrations, "save_integrations", lambda value: saved.append(value))

    added = integrations.add_integration({
        "name": "Acme Relay API",
        "base_url": "https://api.example.test",
        "native_mcp_server_id": "attacker-choice",
    })

    assert "native_mcp_server_id" not in added
    assert saved == [[added]]


def test_native_companion_is_omitted_from_generic_prompt_and_api_execution(monkeypatch):
    api = {
        "id": "legacy-api",
        "name": "Acme MCP Portal API",
        "base_url": "https://portal.example.test/api",
        "enabled": True,
        "description": "Must not become a generic route",
    }
    monkeypatch.setattr(integrations, "load_integrations", lambda: [api])
    monkeypatch.setattr(
        integrations,
        "native_mcp_companions",
        lambda rows: {"legacy-api": {"id": "native-portal"}},
    )

    assert integrations.get_integrations_prompt() == ""
    result = asyncio.run(integrations.execute_api_call("legacy-api", "GET", "/health"))
    assert result["exit_code"] == 1
    assert "native MCP catalog" in result["error"]


def test_settings_groups_native_companion_instead_of_rendering_duplicate_cards():
    source = (Path(__file__).resolve().parents[1] / "static/js/settings.js").read_text()

    assert "nativeApiByMcpId" in source
    assert "if (intg.native_connection?.id) continue" in source
    assert "API credential grouped" in source
