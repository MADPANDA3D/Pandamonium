from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

import src.jarvis_agent as jarvis_agent
from routes.agent_task_routes import TaskApproval
from routes.voice_routes import _delegation_route, _target_switch
from src.agent_worker_adapters import HermesRunsAdapter, _hermes_run_features


def test_voice_intent_separates_foreground_switch_from_background_delegation():
    assert _target_switch("Talk to PC Codex") == "pc-codex"
    assert _target_switch("Please switch me back to Jarvis") == "jarvis"
    assert _target_switch("Ask PC Codex to inspect Mark 5") is None
    assert _delegation_route("Ask PC Codex to inspect Mark 5") == ("pc-codex", "home-lab")


def test_worker_approval_choices_are_narrow():
    assert TaskApproval(choice="once", spoken_text="Yes, approve that once.").choice == "once"
    with pytest.raises(ValidationError):
        TaskApproval(choice="everything")


def test_hermes_native_events_are_normalized_without_speaking_tools(tmp_path):
    adapter = HermesRunsAdapter("http://hermes", tmp_path / "token", enabled=False)
    tool = adapter._normalize({"event": "tool.started", "tool": "terminal"})
    approval = adapter._normalize({"event": "approval.request", "description": "Restart service?"})
    result = adapter._normalize({"event": "run.completed", "output": "Done."})
    assert tool["type"] == "tool_activity"
    assert approval == {
        "type": "approval_required",
        "text": "Restart service?",
        "metadata": {"event": "approval.request", "description": "Restart service?"},
    }
    assert result["type"] == "result"


def test_hermes_capability_names_match_current_runs_contract():
    assert _hermes_run_features({
        "run_submission": True,
        "run_events_sse": True,
        "run_stop": True,
        "run_approval_response": True,
    }) == {"runs": True, "stop": True, "approvals": True}
    assert _hermes_run_features({
        "run_submission": True,
        "run_events_sse": True,
        "run_stop": True,
        "run_approval": True,
    })["approvals"] is True


@pytest.mark.asyncio
async def test_codex_thread_binding_is_scoped_to_session_and_workspace(tmp_path, monkeypatch):
    class FakeAdapter:
        worker = "pc-codex"
        enabled = True

        def __init__(self):
            self.started_with = []

        async def start(self, task):
            self.started_with.append(task.get("codex_thread_id"))
            return {"remote_task_id": f"remote-{len(self.started_with)}", "status": "queued"}

        async def events(self, _task):
            yield {
                "type": "tool_activity",
                "text": "Thread opened.",
                "metadata": {"codex_thread_id": "019f5022-a520-7de0-9208-018cd2d4d222"},
            }
            yield {"type": "result", "text": "Done.", "metadata": {}}

        async def status(self, _task):
            return {"status": "completed", "result": "Done."}

        async def reply(self, _task, _payload):
            return {}

        async def approve(self, _task, _payload):
            return {}

        async def cancel(self, _task):
            return {}

        async def health(self):
            return {"state": "connected"}

    adapter = FakeAdapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {"pc-codex": {"enabled": True, "machine": "workstation"}},
    )

    await jarvis_agent.start_task("pc-codex", "session-a", "home-lab", "first")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await jarvis_agent.start_task("pc-codex", "session-a", "home-lab", "second")
    assert adapter.started_with == [None, "019f5022-a520-7de0-9208-018cd2d4d222"]
