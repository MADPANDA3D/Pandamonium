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
