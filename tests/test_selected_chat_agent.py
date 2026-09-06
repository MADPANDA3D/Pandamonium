import pytest

from routes.chat_routes import (
    _direct_selected_identity_turn,
    _retire_synthesized_worker_results,
    _selected_agent_context,
)
from src.worker_routing import selected_worker_workspace


def test_only_jarvis_uses_the_configured_reasoning_model_context():
    context = _selected_agent_context("Jarvis")

    assert "configured reasoning model" in context
    assert "Present every response as Friday" not in context


def test_selected_friday_defaults_to_home_lab_without_inheriting_business(monkeypatch):
    monkeypatch.setattr("src.worker_routing.worker_catalog", lambda: {
        "pc-codex": {"workspaces": ["business", "home-lab"]},
    })

    assert selected_worker_workspace("pc-codex", "Inspect the Pandamonium source") == "home-lab"
    assert selected_worker_workspace("pc-codex", "Inspect the Business workspace") == "business"


@pytest.mark.asyncio
async def test_selected_gordon_routes_directly_through_hermes(monkeypatch):
    captured = {}

    async def direct(session_id, message, **kwargs):
        captured.update(session_id=session_id, message=message, **kwargs)
        return "Gordon response."

    monkeypatch.setattr("src.jarvis_agent.direct_hermes_turn", direct)

    result = await _direct_selected_identity_turn(
        "hermes",
        session_id="chat-1",
        message="Inspect this.",
        owner="leo",
        workspace="home-lab",
        presenter="Gordon",
    )

    assert result == ("response", "Gordon response.", "completed")
    assert captured == {
        "session_id": "chat-1",
        "message": "Inspect this.",
        "owner": "leo",
        "workspace": "home-lab",
    }


@pytest.mark.asyncio
async def test_selected_friday_routes_directly_through_codex(monkeypatch):
    captured = {}

    async def direct(session_id, message, **kwargs):
        captured.update(session_id=session_id, message=message, **kwargs)
        return {"task_id": "task-1", "presenter": "Friday"}, "steered"

    monkeypatch.setattr("src.jarvis_agent.direct_codex_turn", direct)

    result = await _direct_selected_identity_turn(
        "pc-codex",
        session_id="chat-1",
        message="Continue the task.",
        owner="leo",
        workspace="home-lab",
        presenter="Friday",
    )

    assert result == (
        "task",
        {"task_id": "task-1", "presenter": "Friday"},
        "steered",
    )
    assert captured == {
        "session_id": "chat-1",
        "message": "Continue the task.",
        "owner": "leo",
        "workspace": "home-lab",
        "presenter": "Friday",
    }


def test_worker_result_is_retired_only_from_saved_response_metadata(monkeypatch):
    consumed = []
    monkeypatch.setattr(
        "src.jarvis_agent.consume_task_result",
        lambda task_id, *, owner, session_id: consumed.append((task_id, owner, session_id)),
    )

    _retire_synthesized_worker_results({
        "tool_events": [
            {"tool": "read_agent_task", "task_id": "running", "task_status": "running"},
            {"tool": "read_agent_task", "task_id": "complete", "task_status": "completed"},
        ],
    }, "leo", "chat-1")

    assert consumed == [("complete", "leo", "chat-1")]
