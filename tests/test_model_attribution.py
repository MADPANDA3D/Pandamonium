"""Message model attribution must survive active-model switches (MAD-787).

The Stop handler used to backfill ``session.model`` onto the last assistant
message whenever its metadata had no ``model`` key. After the operator switches
the active model (PATCH /api/session/<id> rewrites ``session.model``), that
backfill silently rewrote the historical message's attribution to the *new*
model. This pins the storage rule: stopping a response may mark it stopped, but
must never invent or rewrite its model attribution.
"""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, ChatMessage as DbChatMessage, Session as DbSession
from routes.history import history_routes


class _FakeSessionManager:
    def __init__(self, session):
        self._session = session
        self.saved = False

    def get_session(self, session_id):
        if session_id != self._session.id:
            raise KeyError(session_id)
        return self._session

    def save_sessions(self):
        self.saved = True


def _client(monkeypatch, session):
    monkeypatch.setattr(history_routes, "_verify_session_owner", lambda request, session_id: None)
    manager = _FakeSessionManager(session)
    app = FastAPI()
    app.include_router(history_routes.setup_history_routes(manager))
    return TestClient(app), manager


def _memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(history_routes, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _seed_message(TestSessionLocal, *, meta):
    db = TestSessionLocal()
    try:
        db.add(DbSession(id="s1", name="t", endpoint_url="http://a/v1", model="model-b"))
        db.add(
            DbChatMessage(
                id="m1",
                session_id="s1",
                role="assistant",
                content="partial answer",
                meta_data=json.dumps(meta),
            )
        )
        db.commit()
    finally:
        db.close()


def _stored_meta(TestSessionLocal):
    db = TestSessionLocal()
    try:
        row = db.query(DbChatMessage).filter(DbChatMessage.id == "m1").first()
        return json.loads(row.meta_data or "{}")
    finally:
        db.close()


def test_mark_stopped_does_not_rewrite_attribution_after_model_switch(monkeypatch):
    TestSessionLocal = _memory_db(monkeypatch)
    # The response was produced by model-a; the operator has since switched the
    # active model to model-b (session.model). The stored message has not been
    # attributed yet (older save path / aborted stream).
    _seed_message(TestSessionLocal, meta={"_db_id": "m1"})
    session = SimpleNamespace(
        id="s1",
        model="model-b",
        history=[
            {"role": "assistant", "content": "partial answer", "metadata": {"_db_id": "m1"}},
        ],
    )
    client, manager = _client(monkeypatch, session)

    response = client.post("/api/session/s1/mark-stopped")

    assert response.status_code == 200
    assert manager.saved is True
    # Stopped state is applied...
    meta = _stored_meta(TestSessionLocal)
    assert meta["stopped"] is True
    # ...but the historical model attribution is not rewritten to model-b.
    assert "model" not in meta
    assert "model" not in session.history[0]["metadata"]


def test_mark_stopped_preserves_existing_attribution(monkeypatch):
    TestSessionLocal = _memory_db(monkeypatch)
    _seed_message(TestSessionLocal, meta={"_db_id": "m1", "model": "model-a"})
    session = SimpleNamespace(
        id="s1",
        model="model-b",
        history=[
            {
                "role": "assistant",
                "content": "partial answer",
                "metadata": {"_db_id": "m1", "model": "model-a"},
            },
        ],
    )
    client, _manager = _client(monkeypatch, session)

    assert client.post("/api/session/s1/mark-stopped").status_code == 200

    assert _stored_meta(TestSessionLocal)["model"] == "model-a"
    assert session.history[0]["metadata"]["model"] == "model-a"
