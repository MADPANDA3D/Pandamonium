"""MAD-883: the active workspace must be settable and visible.

Covers the three acceptance surfaces:
  * the picker/`/workspace set` gate uses the real owner (effective_user) and
    returns actionable copy for 401/403 instead of a generic failure;
  * the agent has manage_workspace to set/clear/report the workspace, vetted
    with the same vet_workspace semantics as the chat bind path;
  * the setting persists per chat session and is re-vetted/dropped on load so
    the pill, get_workspace, and the stream stay consistent.
"""
import asyncio
import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import routes.chat_routes as cr
import routes.workspace_routes as wr
import src.workspace_store as ws_store
from core.database import Base
from core.database import Session as DbSession


def _block(tool, content=""):
    return SimpleNamespace(tool_type=tool, content=content)


class _FakeRequest:
    """Minimal Request stand-in for the workspace session endpoint."""

    def __init__(self, payload=None, json_error=False):
        self._payload = payload or {}
        self._json_error = json_error
        self.state = SimpleNamespace(current_user="leo", api_token=False)

    async def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload

    async def form(self):
        return {}


def _endpoint(router, path, method="GET"):
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not registered")


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


# ── gate: the owner is not blocked, others get honest copy ─────────────


def test_browse_gate_uses_effective_owner(monkeypatch):
    """A bearer-token client authenticated as the owner must pass the admin
    gate. The route used to read only get_current_user (the sandboxed "api"
    pseudo-user), so the real owner was 403'd (MAD-883)."""
    router = wr.setup_workspace_routes()
    browse = _endpoint(router, "/api/workspace/browse")
    seen = {}

    def _gate(owner):
        seen["owner"] = owner
        return owner == "leo"

    monkeypatch.setattr(wr, "effective_user", lambda req: "leo")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", _gate)
    out = browse(request=object(), path=os.path.expanduser("~"))
    assert seen["owner"] == "leo"
    assert "dirs" in out and "selectable" in out


def test_browse_and_vet_forbidden_copy_is_actionable(monkeypatch):
    from fastapi import HTTPException

    router = wr.setup_workspace_routes()
    browse = _endpoint(router, "/api/workspace/browse")
    vet = _endpoint(router, "/api/workspace/vet")
    monkeypatch.setattr(wr, "effective_user", lambda req: "bob")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: False)

    with pytest.raises(HTTPException) as be:
        browse(request=object(), path="/")
    assert be.value.status_code == 403
    assert "admin" in be.value.detail and "Sign in" in be.value.detail

    with pytest.raises(HTTPException) as ve:
        vet(request=object(), path="/tmp")
    assert ve.value.status_code == 403
    assert "admin" in ve.value.detail and "Sign in" in ve.value.detail


# ── POST /api/workspace/session persists per chat ──────────────────────


def test_session_workspace_endpoint_persists_and_clears(monkeypatch):
    router = wr.setup_workspace_routes()
    endpoint = _endpoint(router, "/api/workspace/session", "POST")
    saved = []
    monkeypatch.setattr(wr, "effective_user", lambda req: "leo")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(
        "routes.session_routes._verify_session_owner", lambda request, sid: None
    )
    monkeypatch.setattr(
        ws_store, "set_session_workspace", lambda sid, path: saved.append((sid, path)) or True
    )
    ws = tempfile.mkdtemp()

    ok = asyncio.run(endpoint(_FakeRequest({"session_id": "s1", "path": ws})))
    assert ok["ok"] is True and ok["path"] == os.path.realpath(ws)
    assert saved == [("s1", os.path.realpath(ws))]

    cleared = asyncio.run(endpoint(_FakeRequest({"session_id": "s1", "clear": True})))
    assert cleared["ok"] is True and cleared["path"] is None
    assert saved[-1] == ("s1", "")


def test_session_workspace_endpoint_rejects_unusable_path(monkeypatch):
    router = wr.setup_workspace_routes()
    endpoint = _endpoint(router, "/api/workspace/session", "POST")
    called = []
    monkeypatch.setattr(wr, "effective_user", lambda req: "leo")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    monkeypatch.setattr(
        "routes.session_routes._verify_session_owner", lambda request, sid: None
    )
    monkeypatch.setattr(
        ws_store, "set_session_workspace", lambda sid, path: called.append(path) or True
    )

    out = asyncio.run(endpoint(_FakeRequest({"session_id": "s1", "path": "/nonexistent/xyz"})))
    assert out["ok"] is False and out["path"] is None and "not a usable" in out["error"]
    assert called == []


def test_session_workspace_endpoint_requires_session(monkeypatch):
    from fastapi import HTTPException

    router = wr.setup_workspace_routes()
    endpoint = _endpoint(router, "/api/workspace/session", "POST")
    monkeypatch.setattr(wr, "effective_user", lambda req: "leo")
    monkeypatch.setattr(wr, "owner_is_admin_or_single_user", lambda owner: True)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(endpoint(_FakeRequest({})))
    assert ei.value.status_code == 400


# ── store round-trip + migration ───────────────────────────────────────


def test_store_round_trip(store_db):
    assert ws_store.read_session_workspace("s1") == ""
    ws = tempfile.mkdtemp()
    assert ws_store.set_session_workspace("s1", ws) is True
    assert ws_store.read_session_workspace("s1") == ws
    assert ws_store.clear_session_workspace("s1") is True
    assert ws_store.read_session_workspace("s1") == ""
    assert ws_store.set_session_workspace("missing", ws) is False


def test_session_workspace_migration_is_idempotent(monkeypatch, tmp_path):
    path = tmp_path / "migrate.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{path}")
    database._migrate_add_session_workspace_column()
    database._migrate_add_session_workspace_column()

    conn = sqlite3.connect(path)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
    conn.close()
    assert "workspace" in columns


# ── chat bind path: fallback, persist, drop deleted folders ────────────


def test_apply_session_workspace_persists_explicit_pick(monkeypatch, admin):
    saved = []
    monkeypatch.setattr(ws_store, "set_session_workspace", lambda sid, path: saved.append((sid, path)) or True)
    sess = SimpleNamespace(id="s1", workspace="")
    ws = tempfile.mkdtemp()
    out = cr._apply_session_workspace(sess, "leo", ws, "")
    assert out == (ws, "")
    assert saved == [("s1", ws)]
    assert sess.workspace == ws


def test_apply_session_workspace_falls_back_to_stored(monkeypatch, admin):
    ws = tempfile.mkdtemp()
    sess = SimpleNamespace(id="s1", workspace=ws)
    out = cr._apply_session_workspace(sess, "leo", "", "")
    assert out == (os.path.realpath(ws), "")


def test_apply_session_workspace_drops_deleted_folder(monkeypatch, admin):
    cleared = []
    monkeypatch.setattr(ws_store, "clear_session_workspace", lambda sid: cleared.append(sid) or True)
    sess = SimpleNamespace(id="s1", workspace="/nonexistent/xyz")
    workspace, rejected = cr._apply_session_workspace(sess, "leo", "", "")
    assert workspace == "" and rejected == "/nonexistent/xyz"
    assert cleared == ["s1"] and sess.workspace == ""


def test_apply_session_workspace_skips_non_admin(monkeypatch):
    """No vetting and no stored path for non-admins: the stored value must not
    become an existence oracle or leak through get_workspace."""
    import src.tool_security as ts
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: False)
    sess = SimpleNamespace(id="s1", workspace="/home/admin/private")
    assert cr._apply_session_workspace(sess, "bob", "", "") == ("", "")
    assert sess.workspace == "/home/admin/private"
