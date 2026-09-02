"""Public Brain surfaces must expose state without importing remote authority."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import routes.mcp_routes as mcp_routes
import routes.memory_routes as memory_routes


REPO_ROOT = Path(__file__).resolve().parents[1]


class _Db:
    def __init__(self, server):
        self.server = server

    def query(self, _model):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.server

    def close(self):
        return None


class _PortalManager:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_server_status(self, _server_id):
        return {"status": "connected", "tool_count": 65}

    async def call_tool(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        return self.result


def _endpoint(router, path, method):
    matches = [
        route.endpoint
        for route in router.routes
        if route.path == path and method in getattr(route, "methods", set())
    ]
    assert matches, path
    return matches[-1]


def test_portal_skills_are_bounded_read_only_metadata(monkeypatch):
    secret = "fixture-portal-secret"
    manager = _PortalManager(
        {
            "exit_code": 0,
            "structured_content": {
                "ok": True,
                "items": [
                    {
                        "id": "skill-1",
                        "slug": "academic-copilot",
                        "name": "Academic Copilot",
                        "description": "Guided academic workflow",
                        "scope": "global",
                        "category": "education",
                        "content": "must-not-leak",
                        "installCommand": "must-not-leak",
                        "api_key": "must-not-leak",
                    }
                ],
            },
        }
    )
    server = SimpleNamespace(
        id=mcp_routes.MAD_MCP_PORTAL_ID,
        oauth_tokens=json.dumps({"static_bearer_token": secret}),
    )
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: _Db(server))
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    mcp_routes.setup_mcp_routes(manager)
    endpoint = _endpoint(mcp_routes.router, "/api/mcp/portal/skills", "GET")

    response = asyncio.run(endpoint(SimpleNamespace()))

    assert response == {
        "configured": True,
        "status": "ready",
        "skills": [
            {
                "id": "skill-1",
                "slug": "academic-copilot",
                "name": "Academic Copilot",
                "description": "Guided academic workflow",
                "scope": "global",
                "category": "education",
                "source": "mad-mcp-portal",
            }
        ],
        "count": 1,
    }
    assert manager.calls == [
        (
            f"mcp__{mcp_routes.MAD_MCP_PORTAL_ID}__portal.list_skills",
            {"limit": 200, "offset": 0},
        )
    ]
    serialized = json.dumps(response)
    assert secret not in serialized
    assert "must-not-leak" not in serialized


def test_portal_skills_fail_closed_when_catalog_is_unavailable(monkeypatch):
    manager = _PortalManager(
        {
            "exit_code": 0,
            "structured_content": {
                "ok": False,
                "error": {"message": "sensitive provider failure"},
            },
        }
    )
    server = SimpleNamespace(
        id=mcp_routes.MAD_MCP_PORTAL_ID,
        oauth_tokens=json.dumps({"static_bearer_token": "fixture-secret"}),
    )
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: _Db(server))
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    mcp_routes.setup_mcp_routes(manager)
    endpoint = _endpoint(mcp_routes.router, "/api/mcp/portal/skills", "GET")

    response = asyncio.run(endpoint(SimpleNamespace()))

    assert response == {
        "configured": True,
        "status": "unavailable",
        "skills": [],
        "count": 0,
    }
    assert "sensitive provider failure" not in json.dumps(response)


def test_brain_status_is_owner_scoped_and_secret_free(monkeypatch):
    monkeypatch.setattr(
        memory_routes,
        "get_current_user",
        lambda _request: "alice",
        raising=False,
    )
    memory_manager = MagicMock()
    memory_manager.load.return_value = [{"id": "a"}, {"id": "b"}]
    vector = SimpleNamespace(
        healthy=True,
        count=lambda: 2,
        _qdrant=SimpleNamespace(
            enabled=True,
            read_enabled=False,
            healthy=True,
            url="http://secret-qdrant.internal",
            last_error="must-not-leak",
        ),
    )
    router = memory_routes.setup_memory_routes(
        memory_manager,
        MagicMock(),
        vector,
    )
    endpoint = _endpoint(router, "/api/memory/status", "GET")

    response = endpoint(SimpleNamespace())

    assert response == {
        "canonical": {"state": "ready", "count": 2},
        "keyword_recall": {"state": "ready"},
        "semantic_recall": {"state": "ready", "count": 2},
        "qdrant_projection": {
            "state": "write_only",
            "configured": True,
            "read_enabled": False,
        },
    }
    memory_manager.load.assert_called_once_with(owner="alice")
    assert "secret-qdrant" not in json.dumps(response)
    assert "must-not-leak" not in json.dumps(response)


def test_brain_ui_distinguishes_native_and_portal_skills_and_uses_red_icons():
    skills_js = (REPO_ROOT / "static/js/skills.js").read_text()
    memory_js = (REPO_ROOT / "static/js/memory.js").read_text()
    index = (REPO_ROOT / "static/index.html").read_text()
    css = (REPO_ROOT / "static/style.css").read_text()

    assert "/api/mcp/portal/skills" in skills_js
    assert "MAD MCP Skills Hub" in skills_js
    assert "Read-only catalog" in skills_js
    assert "/api/memory/status" in memory_js
    assert "Brain storage" in index
    assert "#tools-section .list-item > svg" in css
    assert "color: var(--red)" in css
