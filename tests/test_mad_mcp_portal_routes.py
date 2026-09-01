import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.mcp_routes as mcp_routes


REPO_ROOT = Path(__file__).resolve().parents[1]


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


class MailboxManager:
    def __init__(self, *, agentmail_result):
        self.agentmail_result = agentmail_result
        self.calls = []

    def get_server_status(self, _server_id):
        return {"status": "connected", "tool_count": 270}

    async def call_tool(self, name, arguments, **_kwargs):
        self.calls.append((name, arguments))
        if name.endswith("portal.list_services"):
            return {
                "exit_code": 0,
                "structured_content": {
                    "items": [
                        {"id": "google", "name": "Google", "configured": True},
                        {
                            "id": "agentmail",
                            "name": "AgentMail",
                            "configured": True,
                        },
                    ]
                },
            }
        if name.endswith("portal.list_service_profiles"):
            assert arguments == {"serviceId": "google"}
            return {
                "exit_code": 0,
                "structured_content": {
                    "ok": True,
                    "items": [
                        {
                            "id": "google-primary",
                            "label": "Main Google",
                            "email": "leo@example.test",
                            "slug": "main-google",
                            "identityStatus": "verified",
                            "isDefault": True,
                            "isConfigured": True,
                            "access_token": "must-not-leak",
                        }
                    ],
                },
            }
        if name.endswith("portal.call_read_tool"):
            assert arguments == {
                "serviceId": "agentmail",
                "toolName": "list_inboxes",
                "arguments": {"limit": 25},
            }
            return self.agentmail_result
        raise AssertionError(f"Unexpected tool call: {name}")


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


def test_portal_mailboxes_projects_google_and_agentmail_without_secrets(monkeypatch):
    db = FakeDb(
        SimpleNamespace(
            id=mcp_routes.MAD_MCP_PORTAL_ID,
            oauth_tokens=json.dumps({"static_bearer_token": "fixture-secret"}),
        )
    )
    manager = MailboxManager(
        agentmail_result={
            "exit_code": 0,
            "structured_content": {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "inbox_id": "agent-1",
                            "display_name": "Jarvis",
                            "email": "jarvis@example.test",
                            "api_key": "must-not-leak",
                        }
                    ]
                },
            },
        }
    )
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    mailboxes = _endpoint(manager, "/api/mcp/portal/mailboxes", "GET")

    response = asyncio.run(mailboxes(FakeRequest({})))

    assert response["configured"] is True
    assert response["my_email"] == {
        "configured": True,
        "status": "ready",
        "accounts": [
            {
                "id": "google-primary",
                "label": "Main Google",
                "email": "leo@example.test",
                "slug": "main-google",
                "default": True,
                "verification": "verified",
            }
        ],
    }
    assert response["agent_mail"] == {
        "configured": True,
        "status": "ready",
        "inboxes": [
            {
                "id": "agent-1",
                "label": "Jarvis",
                "email": "jarvis@example.test",
            }
        ],
    }
    assert "must-not-leak" not in json.dumps(response)
    assert "fixture-secret" not in json.dumps(response)


def test_portal_mailboxes_reports_agentmail_admission_failure_without_inventing_inboxes(
    monkeypatch,
):
    db = FakeDb(
        SimpleNamespace(
            id=mcp_routes.MAD_MCP_PORTAL_ID,
            oauth_tokens=json.dumps({"static_bearer_token": "fixture-secret"}),
        )
    )
    manager = MailboxManager(
        agentmail_result={
            "exit_code": 0,
            "structured_content": {
                "ok": False,
                "error": {
                    "code": "provider_not_admitted",
                    "message": "sensitive provider detail",
                },
            },
        }
    )
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda _request: None)
    mailboxes = _endpoint(manager, "/api/mcp/portal/mailboxes", "GET")

    response = asyncio.run(mailboxes(FakeRequest({})))

    assert response["my_email"]["status"] == "ready"
    assert response["agent_mail"] == {
        "configured": True,
        "status": "unavailable",
        "error_code": "provider_not_admitted",
        "inboxes": [],
    }
    assert "sensitive provider detail" not in json.dumps(response)


def test_email_library_exposes_large_keyboard_accessible_mailbox_tabs():
    source = (REPO_ROOT / "static/js/emailLibrary.js").read_text()
    css = (REPO_ROOT / "static/style.css").read_text()

    assert 'role="tablist"' in source
    assert '>My Email<' in source
    assert '>Agent Mail<' in source
    assert 'role="tabpanel"' in source
    assert "ArrowLeft" in source and "ArrowRight" in source
    assert "/api/mcp/portal/mailboxes" in source
    assert "width:min(1100px, 94vw)" in source
    assert "height:min(820px, 88vh)" in source
    assert ".email-mailbox-tabs" in css
    assert ".portal-mailbox-grid" in css
    assert mcp_routes.MAD_MCP_PORTAL_URL not in source
