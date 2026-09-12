"""Operator access-level matrix for the authority gate (MAD-885).

``AuthorityStore.decide`` honors the operator's stored access mode without
touching hard policy denies or the approval-card workflow. These tests pin the
per-mode decision matrix with a stubbed mode lookup.
"""
from datetime import datetime, timedelta, timezone

import pytest

import src.authority_protocol as authority_protocol
from src.authority_protocol import AuthorityStore


def _call(
    *,
    name="manage_calendar",
    arguments=None,
    request_id="request-1",
    call_id="call-1",
    target="tool",
):
    return {
        "request_id": request_id,
        "call_id": call_id,
        "agent_id": "jarvis",
        "name": name,
        "target": target,
        "arguments": arguments or {"action": "create", "title": "Review"},
    }


def _store(tmp_path):
    return AuthorityStore(tmp_path / "authority.json")


@pytest.fixture
def mode(monkeypatch):
    """Force access_mode_for to return a chosen mode for the test."""

    def _set(value):
        monkeypatch.setattr(
            authority_protocol, "access_mode_for", lambda operator: value
        )

    return _set


def test_ask_for_approval_is_legacy_behavior(tmp_path, mode):
    mode("ask_for_approval")
    store = _store(tmp_path)
    decision = store.decide(
        _call(name="send_email", arguments={"to": "a@example.test"}),
        operator_id="leo",
        session_id="session-1",
    )
    assert decision["decision"] == "approval_required"
    assert decision["access_mode"] == "ask_for_approval"
    assert decision["policy_basis"] == "exact_operator_approval"


def test_approve_for_me_auto_allows_routine_work(tmp_path, mode):
    mode("approve_for_me")
    store = _store(tmp_path)
    for call in [
        _call(name="read_file", arguments={"path": "README.md"}),
        _call(name="write_file", arguments={"path": "notes.md", "content": "ok"}),
        _call(
            name="api_call",
            arguments={"method": "POST", "path": "/jobs"},
        ),
        _call(
            name="read_file",
            arguments={"path": "../outside.txt"},
        ) | {"capability_policy": {"configured_workspace": "/srv/workspace"}},
    ]:
        decision = store.decide(call, operator_id="leo", session_id="session-1")
        assert decision["decision"] == "allow", (call["name"], decision["policy_basis"])
        assert decision["policy_basis"] in {
            "owner_scoped_read",
            "authenticated_explicit_request",
            "access_mode:approve_for_me",
        }


def test_approve_for_me_still_gates_potentially_unsafe(tmp_path, mode):
    mode("approve_for_me")
    store = _store(tmp_path)
    for index, call in enumerate([
        _call(name="delete_email", arguments={"id": "7"}),
        _call(name="manage_tokens", arguments={"action": "rotate_token"}),
        _call(name="bash", arguments={"command": "sudo systemctl restart demo"}),
        _call(name="app_api", arguments={"action": "purchase", "item": "seat"}),
    ]):
        decision = store.decide(call, operator_id="leo", session_id=f"session-{index}")
        assert decision["decision"] == "approval_required", call["name"]
    decision = store.decide(
        _call(name="new_plugin_mutation"), operator_id="leo", session_id="session-9"
    )
    assert decision["decision"] == "deny"
    assert decision["policy_basis"] == "unclassified_capability"


def test_full_access_allows_classified_effects_but_not_unclassified(tmp_path, mode):
    mode("full_access")
    store = _store(tmp_path)
    for call in [
        _call(name="delete_email", arguments={"id": "7"}),
        _call(name="manage_tokens", arguments={"action": "rotate_token"}),
        _call(name="bash", arguments={"command": "sudo systemctl restart demo"}),
        _call(name="app_api", arguments={"action": "purchase", "item": "seat"}),
        _call(name="send_email", arguments={"to": "a@example.test"}),
        _call(name="write_file", arguments={"path": "../outside.txt"}),
    ]:
        decision = store.decide(call, operator_id="leo", session_id="session-1")
        assert decision["decision"] == "allow", (call["name"], decision["policy_basis"])
    decision = store.decide(
        _call(name="new_plugin_mutation"), operator_id="leo", session_id="session-1"
    )
    assert decision["decision"] == "deny"
    assert decision["policy_basis"] == "unclassified_capability"


def test_hard_denies_never_bypassed_by_full_access(tmp_path, mode):
    mode("full_access")
    store = _store(tmp_path)
    no_operator = store.decide(
        _call(name="delete_email", arguments={"id": "7"}),
        operator_id=None,
        session_id="session-1",
    )
    assert no_operator["decision"] == "deny"
    assert no_operator["policy_basis"] == "authenticated_operator_required"
    disabled = store.decide(
        _call(name="delete_email", arguments={"id": "7"}),
        operator_id="leo",
        session_id="session-1",
        disabled_reason="Tool is disabled for this request.",
    )
    assert disabled["decision"] == "deny"
    assert disabled["policy_basis"] == "Tool is disabled for this request."


def test_approve_for_me_still_honors_denial_receipts(tmp_path, mode):
    mode("approve_for_me")
    store = _store(tmp_path)
    call = _call(name="manage_tokens", arguments={"action": "rotate_token"})
    decision = store.decide(call, operator_id="leo", session_id="session-1")
    decision_id = decision["decision_id"]
    now = datetime.now(timezone.utc)
    with store._lock:
        state = store._read()
        state["receipts"][decision_id] = {
            "receipt_id": decision_id,
            "decision_id": decision_id,
            "operator_id": "leo",
            "agent_id": "jarvis",
            "session_id": "session-receipt",
            "workspace": None,
            "connection": None,
            "capability": {"name": "manage_tokens"},
            "action_effect": "credential_or_auth_change",
            "argument_fingerprint": authority_protocol.argument_fingerprint(call),
            "request_id": "request-1",
            "execution": authority_protocol.AuthorityStore._runtime_binding(),
            "decision": "deny",
            "scope": "persistent",
            "status": "active",
            "issued_at": authority_protocol._iso(now),
            "created_at": authority_protocol._iso(now),
            "expires_at": authority_protocol._iso(now + timedelta(days=7)),
        }
        store._write(state)
    repeat = store.decide(call, operator_id="leo", session_id="session-receipt")
    assert repeat["decision"] == "deny"
    assert repeat["policy_basis"] == "denial_receipt"