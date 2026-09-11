import base64
import io
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from starlette.applications import Starlette
from starlette.routing import Mount

from src import agent_gateway as gateway
from src import jarvis_agent
from src.agent_turn_context import build_agent_turn_context, contextual_prompt
from src.authority_protocol import AuthorityStore
from src.tool_policy import build_effective_tool_policy


def test_upload_context_checks_owner_and_delivers_actual_image_bytes(tmp_path):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    path = tmp_path / "photo.png"
    path.write_bytes(out.getvalue())
    handler = SimpleNamespace(
        resolve_upload=lambda value, owner, allow_admin: {"path": str(path), "name": "photo.png", "mime": "image/png"}
        if value == "upload" and owner == "leo" and not allow_admin else None,
        is_image_file=lambda name, mime: mime == "image/png",
        _inside_upload_dir=lambda value: value == str(path),
    )
    args = dict(session_id="session", presenter="Friday", workspace="project",
                attachment_ids=["upload"], upload_handler=handler, owner="leo")
    context = build_agent_turn_context(**args)
    assert base64.b64decode(context["images"][0]["url"].split(",", 1)[1]) == out.getvalue()
    prompt = contextual_prompt("Describe it", context)
    assert "Pandamonium" in prompt and "native configuration" in prompt
    assert "base64" not in prompt and str(tmp_path) not in prompt
    with pytest.raises(ValueError, match="not owned"):
        build_agent_turn_context(**{**args, "owner": "someone-else"})


@pytest.fixture
def context(monkeypatch, tmp_path):
    gateway._contexts.clear()
    manager = SimpleNamespace(get_session=lambda session: SimpleNamespace(owner="leo"))
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    monkeypatch.setattr(gateway, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(gateway, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(gateway, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(gateway, "authority_store", AuthorityStore(tmp_path / "authority.json"))
    key = gateway.register_context({"source": "Pandamonium", "session_id": "session", "presenter": "Friday"},
                                   owner="leo", workspace=str(tmp_path),
                                   tool_policy=build_effective_tool_policy(disabled_tools={"bash"}))
    return key, manager


@pytest.mark.asyncio
async def test_gateway_shares_validation_policy_execution_and_owner_checks(context, monkeypatch):
    key, manager = context
    invoked = []
    async def execute(block, **kwargs):
        invoked.append((block.tool_type, kwargs))
        return "Runtime", {"version": "fixture"}
    monkeypatch.setattr("src.tool_execution.execute_tool_block", execute)
    assert gateway.read_context(key)["source"] == "Pandamonium"
    assert all(tool["function"]["name"] != "bash" for tool in gateway.discover(key, "bash")["tools"])
    assert gateway.discover(key, "get_runtime_status")["tools"]
    assert (await gateway.execute(key, "bash", {"command": "echo no"}))["status"] == "denied"
    result = await gateway.execute(key, "get_runtime_status", {})
    assert result["result"]["version"] == "fixture"
    assert invoked[0][1]["owner"] == "leo" and invoked[0][1]["session_id"] == "session"
    assert (await gateway.read_tool(key, "get_runtime_status", {}))["result"]["version"] == "fixture"
    assert (await gateway.read_tool(key, "create_document", {"title": "No", "content": "No"}))["status"] == "denied"
    assert len(invoked) == 2
    manager.get_session = lambda session: SimpleNamespace(owner="someone-else")
    with pytest.raises(PermissionError):
        gateway.read_context(key)
    with pytest.raises(ValueError, match="unavailable"):
        gateway.discover("made-up-context")


@pytest.mark.asyncio
async def test_gateway_mcp_transport_requires_token_and_context(context, monkeypatch):
    key, _ = context
    monkeypatch.setattr(gateway, "internal_token_valid", lambda value: value == "Bearer fixture-token")
    app = Starlette(routes=[Mount("/mcp", app=gateway.authenticated_gateway)])
    async with gateway.gateway.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "read_context", "arguments": {"context_id": key}}}
            assert (await client.post("/mcp/", json=payload)).status_code == 401
            response = await client.post("/mcp/", json=payload, headers={
                "Authorization": "Bearer fixture-token", "Accept": "application/json, text/event-stream",
            })
            assert response.status_code == 200
            assert "Pandamonium" in response.text


def test_context_capacity_evicts_oldest_turn_without_blocking_new_messages(context):
    first, _ = context
    for _ in range(1024):
        latest = gateway.register_context({"session_id": "session", "presenter": "Friday"}, owner="leo")
    assert len(gateway._contexts) == 1024
    assert gateway.read_context(latest)["session_id"] == "session"
    with pytest.raises(ValueError):
        gateway.read_context(first)


@pytest.mark.asyncio
async def test_gateway_document_binding_reaches_real_dispatcher(context, monkeypatch):
    from src.agent_tools import TOOL_HANDLERS
    key, _ = context
    gateway._contexts[key]["public"]["active_document"] = {"id": "selected", "title": "Selected"}
    monkeypatch.setattr(gateway.authority_store, "decide", lambda *a, **k: {"decision": "allow"})
    seen = []
    async def handler(content, ctx):
        seen.append(ctx)
        return {"ok": True}
    monkeypatch.setitem(TOOL_HANDLERS, "update_document", handler)
    result = await gateway.execute(key, "update_document", {"content": "Updated"})
    assert result["result"]["ok"]
    assert seen == [{"session_id": "session", "owner": "leo", "doc_id": "selected"}]
    gateway._contexts[key]["public"]["active_document"] = None
    assert (await gateway.execute(key, "update_document", {"content": "Updated"}))["status"] == "denied"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_private_workstation_history_rejects_other_app_accounts(monkeypatch):
    from routes import agent_task_routes
    from fastapi import HTTPException
    monkeypatch.setattr(agent_task_routes, "owner_is_admin_or_single_user", lambda owner: owner == "leo")
    async def history(*args, **kwargs):
        return {"items": [{"images": [{"data_url": "private"}]}]}
    monkeypatch.setattr(agent_task_routes, "adapters", lambda: {"pc-codex": SimpleNamespace(catalog_projects=True, catalog_task_details=history)})
    router = agent_task_routes.setup_agent_task_routes(SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes if route.name == "codex_task_history")
    with pytest.raises(HTTPException) as exc:
        await endpoint("project", "thread", _owner="other")
    assert exc.value.status_code == 403
    assert (await endpoint("project", "thread", _owner="leo"))["items"]
