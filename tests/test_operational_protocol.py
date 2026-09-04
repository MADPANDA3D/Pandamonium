import json

import pytest

import src.operational_protocol as operational
import src.agent_identity as agent_identity
from src.operational_protocol import (
    AUDIT_STATES,
    OUTCOME_STATES,
    ProtocolEventStore,
    RollbackRegistry,
    backup_status,
)


def test_one_request_trace_correlates_engine_approval_action_and_response(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    common = {"request_id": "request-1", "session_id": "session-1", "operator_id": "leo"}
    store.record(**common, actor="engine:gpt", component="engine", event_type="started", status="running")
    store.record(
        **common,
        call_id="call-1",
        actor="odysseus:authority",
        component="control_plane",
        event_type="approval",
        status="succeeded",
        evidence_refs=[{"decision_id": "decision-1"}],
    )
    store.record(
        **common,
        call_id="call-1",
        actor="engine:gpt",
        component="extension:oracle",
        event_type="result",
        status="succeeded",
        evidence_refs=[{"event_id": "oracle-7"}],
    )
    store.record(
        **common,
        actor="engine:gpt",
        component="control_plane",
        event_type="response",
        status="succeeded",
        usage={"input_tokens": 100, "output_tokens": 20},
    )
    trace = store.query(request_id="request-1")
    assert [row["event_type"] for row in trace] == ["started", "approval", "result", "response"]
    assert {row["request_id"] for row in trace} == {"request-1"}
    assert trace[2]["call_id"] == "call-1"


def test_all_outcome_states_remain_distinct_in_storage(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    for status in sorted(OUTCOME_STATES):
        store.record(actor="test", component="tool", event_type="result", status=status)
    assert [row["status"] for row in store.query(limit=20)] == sorted(OUTCOME_STATES)


def test_authority_audit_states_remain_distinct_before_terminal_outcomes(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    for status in ("requested", "authorized", "executed"):
        store.record(actor="test", component="tool", event_type="progress", status=status)
    assert AUDIT_STATES == {"requested", "authorized", "executed"}
    assert [row["status"] for row in store.query(limit=10)] == ["requested", "authorized", "executed"]


def test_event_envelope_redacts_secrets_and_controls_exceptions(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    event = store.record(
        actor="provider",
        component="engine",
        event_type="result",
        status="failed",
        evidence_refs=[{"api_key": "sk-abcdefghijklmnop"}],
        metadata={"url": "https://example.test/?token=abcdefghijklmnop"},
        error=RuntimeError("Bearer abcdefghijklmnop"),
    )
    encoded = json.dumps(event)
    assert "abcdefghijklmnop" not in encoded
    assert event["error"] == {"category": "RuntimeError", "detail": "operation failed"}


def test_restart_reconstructs_trace_and_rollback_registry_from_canonical_files(tmp_path):
    event_path = tmp_path / "events.jsonl"
    first = ProtocolEventStore(event_path)
    first.record(request_id="r", actor="engine", component="engine", event_type="started", status="running")
    assert ProtocolEventStore(event_path).query(request_id="r")[0]["request_id"] == "r"

    registry_path = tmp_path / "rollbacks.json"
    registry = RollbackRegistry(registry_path)
    registry.promote("engine", "v1", configuration={"endpoint": "local"})
    restored = RollbackRegistry(registry_path).snapshot()
    assert restored["components"]["engine"]["active"]["version"] == "v1"


def test_rollback_restores_smallest_unit_without_reverting_unrelated_component(tmp_path):
    registry = RollbackRegistry(tmp_path / "rollbacks.json")
    engine_v1 = registry.promote("engine", "v1", configuration={"model": "one"})
    registry.promote("extension:oracle", "v1", configuration={"host": "1"})
    registry.promote("engine", "v2", configuration={"model": "two"})
    rolled_back = registry.rollback("engine", engine_v1["record_id"])
    state = registry.snapshot()["components"]
    assert rolled_back["version"] == "v1"
    assert rolled_back["configuration"] == {"model": "one"}
    assert state["extension:oracle"]["active"]["version"] == "v1"


def test_backup_status_requires_verification_sidecar(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    archive = backups / "odysseus-backup-1.tar.gz"
    archive.write_bytes(b"archive")
    assert backup_status(tmp_path)["status"] == "degraded"
    sidecar = tmp_path / "backups" / "odysseus-backup-1.tar.gz.verified.json"
    sidecar.write_text(json.dumps({"ok": True, "sha256": "abc", "verified_at": "now"}))
    status = backup_status(tmp_path)
    assert status["verified"] is True
    assert status["rollback_available"] is True


def test_observability_failure_is_fail_soft(monkeypatch):
    def broken(**kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(operational.events, "record", broken)
    assert operational.record_operational_event(
        actor="engine", component="control_plane", event_type="started", status="running"
    ) is None


def test_operational_events_and_diagnostics_use_configured_agent_id(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Keep operator authority explicit.",
        "agent_constitution_version": "2",
    })
    event = ProtocolEventStore(tmp_path / "events.jsonl").record(
        actor="engine:test", component="control_plane", event_type="started", status="running"
    )

    assert event["agent_id"] == "atlas"
    assert operational.protocol_status()["identity"] == {
        "agent_id": "atlas",
        "display_name": "Atlas",
        "constitution_version": "2",
        "status": "healthy",
        "source": "configured",
        "fallback_reasons": [],
    }


def test_invalid_event_cannot_invent_success_state(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        store.record(actor="tool", component="tool", event_type="result", status="looks_good")


def test_unknown_external_outcome_remains_unresolved_until_evidence_is_recorded(tmp_path):
    store = ProtocolEventStore(tmp_path / "events.jsonl")
    store.record(
        request_id="request-1",
        call_id="call-1",
        operator_id="leo",
        actor="tool",
        component="mcp",
        event_type="result",
        status="unknown",
    )
    assert [row["call_id"] for row in store.unresolved_unknowns(operator_id="leo")] == ["call-1"]
    store.reconcile_unknown(
        request_id="request-1",
        call_id="call-1",
        operator_id="leo",
        actor="odysseus:reconciler",
        status="failed",
        evidence_refs=[{"readback": "not_applied"}],
    )
    assert store.unresolved_unknowns(operator_id="leo") == []
