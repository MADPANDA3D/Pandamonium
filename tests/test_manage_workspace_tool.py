"""MAD-883: the agent can set/clear/report the active workspace.

Covers the manage_workspace tool (admin gate, vet_workspace semantics, session
persistence, clear) and the agent-loop hook that rebinds the rest of the
request and emits workspace_changed for the client pill.
"""
import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.workspace_store as ws_store
from core.database import Base
from core.database import Session as DbSession
from src.tool_execution import execute_tool_block


def _block(tool, content=""):
    return SimpleNamespace(tool_type=tool, content=content)


@pytest.fixture
def store_db(monkeypatch):
    """Isolate the per-session workspace store on an in-memory SQLite DB."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(ws_store, "SessionLocal", TestLocal)
    db = TestLocal()
    db.add(DbSession(id="s1", name="chat", endpoint_url="u", model="m"))
    db.commit()
    db.close()
    return TestLocal


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


# ── the agent tool ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manage_workspace_set_show_clear(store_db, admin):
    ws = tempfile.mkdtemp()
    _, r = await execute_tool_block(
        _block("manage_workspace", json.dumps({"action": "set", "path": ws})),
        owner="a",
        session_id="s1",
    )
    assert r["exit_code"] == 0 and r["workspace_changed"] is True
    assert r["workspace"] == os.path.realpath(ws)
    assert ws_store.read_session_workspace("s1") == os.path.realpath(ws)

    # show falls back to the persisted session workspace when no turn binding
    _, r = await execute_tool_block(
        _block("manage_workspace", json.dumps({"action": "show"})),
        owner="a",
        session_id="s1",
    )
    assert r["exit_code"] == 0 and r["workspace"] == os.path.realpath(ws)

    _, r = await execute_tool_block(
        _block("manage_workspace", json.dumps({"action": "clear"})),
        owner="a",
        session_id="s1",
    )
    assert r["exit_code"] == 0 and r["workspace_changed"] is True and r["workspace"] == ""
    assert ws_store.read_session_workspace("s1") == ""


@pytest.mark.asyncio
async def test_manage_workspace_rejects_unusable_path(store_db, admin):
    _, r = await execute_tool_block(
        _block("manage_workspace", json.dumps({"action": "set", "path": "/nonexistent/xyz"})),
        owner="a",
        session_id="s1",
    )
    assert r["exit_code"] == 1 and r["code"] == "invalid_workspace"
    assert ws_store.read_session_workspace("s1") == ""


@pytest.mark.asyncio
async def test_manage_workspace_accepts_bare_path_fence(store_db, admin):
    ws = tempfile.mkdtemp()
    _, r = await execute_tool_block(
        _block("manage_workspace", ws), owner="a", session_id="s1"
    )
    assert r["exit_code"] == 0 and r["workspace"] == os.path.realpath(ws)


@pytest.mark.asyncio
async def test_manage_workspace_non_admin_refused(store_db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: False)
    import src.tool_execution as te
    monkeypatch.setattr(te, "owner_is_admin_or_single_user", lambda owner: False)
    called = []
    monkeypatch.setattr(ws_store, "set_session_workspace", lambda sid, path: called.append(path) or True)

    ws = tempfile.mkdtemp()
    _, r = await execute_tool_block(
        _block("manage_workspace", json.dumps({"action": "set", "path": ws})),
        owner="bob",
        session_id="s1",
    )
    assert r["exit_code"] == 1
    assert called == []


@pytest.mark.asyncio
async def test_get_workspace_reports_persisted_session_value(store_db, admin):
    ws = tempfile.mkdtemp()
    ws_store.set_session_workspace("s1", ws)
    _, r = await execute_tool_block(_block("get_workspace", ""), owner="a", session_id="s1")
    assert r["exit_code"] == 0 and r["workspace"] == ws
    assert "manage_workspace" in r["output"] or ws in r["output"]




# ── agent loop: manage_workspace rebinds the rest of the request ───────


@pytest.mark.asyncio
async def test_agent_loop_rebinds_workspace_and_emits_event(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    new_ws = tempfile.mkdtemp()
    rounds = {"count": 0}
    seen = []

    async def fake_stream(*args, **kwargs):
        rounds["count"] += 1
        if rounds["count"] == 1:
            call = {
                "id": "ws-call-1",
                "name": "manage_workspace",
                "arguments": json.dumps({"action": "set", "path": new_ws}),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        elif rounds["count"] == 2:
            call = {"id": "ws-call-2", "name": "get_workspace", "arguments": "{}"}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield 'data: {"delta":"Workspace is set."}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(*args, **kwargs):
        seen.append(kwargs.get("workspace"))
        if len(seen) == 1:
            return "manage_workspace", {
                "output": "set",
                "workspace": new_ws,
                "workspace_changed": True,
                "exit_code": 0,
            }
        return "get_workspace", {"output": new_ws, "workspace": new_ws, "exit_code": 0}

    import src.agent_loop as al
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(al, "execute_tool_block", fake_execute)

    events = []
    async for chunk in al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{"role": "user", "content": f"work out of {new_ws}"}],
        relevant_tools={"manage_workspace", "get_workspace"},
        max_rounds=3,
    ):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            events.append(json.loads(chunk[6:]))

    changed = [e for e in events if e.get("type") == "workspace_changed"]
    assert changed and changed[0]["data"]["path"] == new_ws
    # The tool call after the set must already be bound to the new workspace.
    assert seen[0] is None and seen[1] == new_ws
