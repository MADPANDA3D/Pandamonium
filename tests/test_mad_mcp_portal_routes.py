import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mcp_routes as mcp_routes


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


class FakeDb:
    def __init__(self, server=None):
        self.server = server
        self.commits = 0
        self.rollbacks = 0

    def query(self, _model):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.server

    def add(self, server):
        self.server = server

    def delete(self, server):
        if self.server is server:
            self.server = None

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None


class FakeManager:
    def __init__(self, connect_results):
        self.connect_results = list(connect_results)
        self.connect_calls = []
        self.disconnect_calls = []

    async def connect_server(self, **kwargs):
        self.connect_calls.append(kwargs)
        return self.connect_results.pop(0)

    async def disconnect_server(self, server_id):
        self.disconnect_calls.append(server_id)

    async def call_tool(self, *_args, **_kwargs):
        return {
            "stdout": "Catalog ready",
            "structured_content": {
                "data": {
                    "items": [
                        {
                            "id": "calendar",
                            "name": "Calendar",
                            "configured": True,
                            "toolCount": 12,
                            "agentReadyToolCount": 8,
                            "state": "configured",
                        }
                    ]
                }
            },
            "exit_code": 0,
        }

    def get_server_status(self, _server_id):
        return {"status": "connected", "tool_count": 7}


def _endpoint(manager, path, method):
    mcp_routes.setup_mcp_routes(manager)
    matches = [
        route.endpoint
        for route in mcp_routes.router.routes
        if route.path == path and method in route.methods
    ]
    assert matches
    return matches[-1]


def test_portal_connect_proves_catalog_before_persisting_and_never_returns_key(monkeypatch):
    db = FakeDb()
    manager = FakeManager([True])
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    connect = _endpoint(manager, "/api/mcp/portal/connect", "POST")
    master_key = "fixture-master-key-123456789"

    response = asyncio.run(connect(FakeRequest({"master_key": master_key})))

    assert response["configured"] is True
    assert response["configured_service_count"] == 1
    assert response["catalog_tool_count"] == 12
    assert master_key not in json.dumps(response)
    assert json.loads(db.server.oauth_tokens) == {"static_bearer_token": master_key}
    assert manager.connect_calls[0]["headers"] == {
        "Authorization": f"Bearer {master_key}"
    }
    assert db.commits == 1


def test_portal_connect_failure_keeps_previous_encrypted_credential(monkeypatch):
    old_key = "fixture-old-master-key-123456"
    server = SimpleNamespace(
        id=mcp_routes.MAD_MCP_PORTAL_ID,
        name=mcp_routes.MAD_MCP_PORTAL_NAME,
        transport="http",
        command=None,
        args="[]",
        env="{}",
        url=mcp_routes.MAD_MCP_PORTAL_URL,
        is_enabled=True,
        oauth_tokens=json.dumps({"static_bearer_token": old_key}),
    )
    db = FakeDb(server)
    manager = FakeManager([False, True])
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    connect = _endpoint(manager, "/api/mcp/portal/connect", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            connect(FakeRequest({"master_key": "fixture-invalid-key-123456789"}))
        )

    assert exc.value.status_code == 502
    assert json.loads(server.oauth_tokens) == {"static_bearer_token": old_key}
    assert manager.connect_calls[-1]["headers"] == {
        "Authorization": f"Bearer {old_key}"
    }
    assert db.commits == 0
