"""Authority-card continuations remain part of their originating assistant turn."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base
from core.database import ChatMessage as DBChatMessage
from core.database import Session as DBSession
from core.models import ChatMessage
from routes import chat_helpers


class _Session:
    def __init__(self, history):
        self.model = "selected-model"
        self.history = list(history)

    def add_message(self, message):
        self.history.append(message)


class _Manager:
    def __init__(self):
        self.save_calls = 0

    def save_sessions(self):
        self.save_calls += 1


@pytest.fixture
def isolated_messages(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(chat_helpers, "SessionLocal", factory)
    monkeypatch.setattr("core.database.update_session_last_accessed", lambda _session_id: None)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _pending_metadata(decision_id="decision-one", message_id="assistant-origin"):
    return {
        "_db_id": message_id,
        "timestamp": "2026-09-08T12:00:00Z",
        "model": "selected-model",
        "round_texts": ["I prepared the exact action."],
        "tool_events": [{
            "round": 1,
            "tool": "api_call",
            "command": '{"path":"/bounded"}',
            "output": "approval required",
            "exit_code": 1,
            "action_result": {"status": "denied"},
            "authority_decision": {
                "decision_id": decision_id,
                "decision": "approval_required",
            },
        }],
    }


def _seed_origin(factory, metadata, *, content="Approval is required."):
    original_timestamp = datetime(2026, 9, 8, 12, 0, 0)
    persisted_metadata = dict(metadata)
    persisted_metadata.pop("_db_id", None)
    db = factory()
    try:
        db.add(DBSession(
            id="session-one",
            name="Authority turn",
            endpoint_url="http://model.test/v1/chat/completions",
            model="selected-model",
        ))
        db.add(DBChatMessage(
            id=metadata["_db_id"],
            session_id="session-one",
            role="assistant",
            content=content,
            meta_data=json.dumps(persisted_metadata),
            timestamp=original_timestamp,
        ))
        db.commit()
    finally:
        db.close()
    return original_timestamp


def test_approved_continuation_updates_only_its_origin_and_preserves_db_identity(
    isolated_messages,
):
    metadata = _pending_metadata()
    original_timestamp = _seed_origin(isolated_messages, metadata)
    origin = ChatMessage("assistant", "Approval is required.", metadata=metadata)
    session = _Session([ChatMessage("user", "Run it"), origin])
    manager = _Manager()
    receipt = {
        "receipt_id": "receipt-one",
        "decision_id": "decision-one",
        "scope": "persistent",
        "status": "active",
        "decision": "allow",
    }

    message_id = chat_helpers.save_assistant_response(
        session,
        manager,
        "session-one",
        "The exact action completed.",
        {
            "model": "actual-model",
            "round_texts": ["The exact action completed."],
            "tool_events": [{
                "round": 1,
                "tool": "api_call",
                "output": "completed",
                "exit_code": 0,
                "action_result": {"status": "succeeded"},
            }],
        },
        authority_continuation={
            "decision_id": "decision-one",
            "choice": "approve",
            "scope": "persistent",
            "receipt": receipt,
        },
    )

    assert message_id == "assistant-origin"
    assert manager.save_calls == 1
    assert len(session.history) == 2
    assert session.history[-1] is origin
    assert origin.content == "The exact action completed."
    assert origin.metadata["_db_id"] == "assistant-origin"
    assert origin.metadata["timestamp"] == "2026-09-08T12:00:00Z"
    assert origin.metadata["round_texts"] == [
        "I prepared the exact action.",
        "The exact action completed.",
    ]
    assert [event["round"] for event in origin.metadata["tool_events"]] == [1, 2]
    pending = origin.metadata["tool_events"][0]
    assert pending["authority_decision"]["status"] == "resolved"
    assert pending["authority_decision"]["receipt_id"] == "receipt-one"
    assert pending["authority_resolution"] == {
        "decision_id": "decision-one",
        "choice": "approve",
        "scope": "persistent",
        "status": "resolved",
        "receipt": receipt,
        "receipt_id": "receipt-one",
    }

    db = isolated_messages()
    try:
        rows = db.query(DBChatMessage).filter_by(session_id="session-one").all()
        assert len(rows) == 1
        assert rows[0].id == "assistant-origin"
        assert rows[0].timestamp == original_timestamp
        assert rows[0].content == "The exact action completed."
        persisted = json.loads(rows[0].meta_data)
        assert "_db_id" not in persisted
        assert persisted["timestamp"] == "2026-09-08T12:00:00Z"
        assert persisted["tool_events"][0]["authority_resolution"]["receipt_id"] == "receipt-one"
    finally:
        db.close()


def test_denied_continuation_becomes_the_final_round_without_adding_a_message(
    isolated_messages,
):
    metadata = _pending_metadata()
    _seed_origin(isolated_messages, metadata)
    origin = ChatMessage("assistant", "Approval is required.", metadata=metadata)
    session = _Session([origin])

    message_id = chat_helpers.save_assistant_response(
        session,
        _Manager(),
        "session-one",
        "Denied: api_call. I will not run it.",
        {"model": "pandamonium-authority"},
        authority_continuation={
            "decision_id": "decision-one",
            "choice": "deny",
            "scope": "once",
            "receipt": {
                "receipt_id": "receipt-deny",
                "decision_id": "decision-one",
                "scope": "once",
                "status": "active",
                "decision": "deny",
            },
        },
    )

    assert message_id == "assistant-origin"
    assert len(session.history) == 1
    assert origin.content == "Denied: api_call. I will not run it."
    assert origin.metadata["round_texts"][-1] == "Denied: api_call. I will not run it."
    assert origin.metadata["tool_events"][0]["authority_resolution"]["choice"] == "deny"

    db = isolated_messages()
    try:
        assert db.query(DBChatMessage).filter_by(session_id="session-one").count() == 1
    finally:
        db.close()


def test_continuation_updates_exact_origin_even_when_it_is_not_last_assistant(
    isolated_messages,
):
    metadata = _pending_metadata()
    _seed_origin(isolated_messages, metadata)
    origin = ChatMessage("assistant", "Approval is required.", metadata=metadata)
    unrelated = ChatMessage(
        "assistant",
        "Do not replace this later response.",
        metadata={
            "_db_id": "assistant-unrelated",
            "timestamp": "2026-09-08T12:01:00Z",
            "tool_events": [],
        },
    )
    db = isolated_messages()
    try:
        db.add(DBChatMessage(
            id="assistant-unrelated",
            session_id="session-one",
            role="assistant",
            content=unrelated.content,
            meta_data=json.dumps({"timestamp": "2026-09-08T12:01:00Z", "tool_events": []}),
            timestamp=datetime(2026, 9, 8, 12, 1, 0),
        ))
        db.commit()
    finally:
        db.close()
    session = _Session([origin, unrelated])

    message_id = chat_helpers.save_assistant_response(
        session,
        _Manager(),
        "session-one",
        "Exact origin updated.",
        {"round_texts": ["Exact origin updated."], "model": "selected-model"},
        authority_continuation={
            "decision_id": "decision-one",
            "choice": "approve",
            "scope": "once",
            "receipt": {"receipt_id": "receipt-one", "scope": "once", "status": "consumed"},
        },
    )

    assert message_id == "assistant-origin"
    assert len(session.history) == 2
    assert origin.content == "Exact origin updated."
    assert unrelated.content == "Do not replace this later response."
    db = isolated_messages()
    try:
        assert db.get(DBChatMessage, "assistant-origin").content == "Exact origin updated."
        assert db.get(DBChatMessage, "assistant-unrelated").content == "Do not replace this later response."
    finally:
        db.close()


def test_unknown_decision_never_merges_an_arbitrary_assistant_message():
    origin = ChatMessage(
        "assistant",
        "Keep this response.",
        metadata=_pending_metadata(decision_id="decision-one", message_id="origin"),
    )
    session = _Session([origin])

    result = chat_helpers.save_assistant_response(
        session,
        session_manager=None,
        session_id="session-one",
        full_response="Unmatched continuation.",
        last_metrics={"model": "selected-model"},
        authority_continuation={
            "decision_id": "different-decision",
            "choice": "approve",
            "scope": "once",
            "receipt": {"receipt_id": "different-receipt"},
        },
        incognito=True,
    )

    assert result is None
    assert len(session.history) == 2
    assert session.history[0] is origin
    assert origin.content == "Keep this response."
    assert "authority_resolution" not in origin.metadata["tool_events"][0]
    assert session.history[1].content == "Unmatched continuation."
