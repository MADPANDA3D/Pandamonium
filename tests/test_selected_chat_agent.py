from routes.chat_routes import (
    _retire_synthesized_worker_results,
    _selected_agent_context,
    _selected_worker_request,
)


def test_selected_friday_uses_native_app_tools_for_small_lookups():
    assert not _selected_worker_request("List all books in my library")
    assert not _selected_worker_request("Check my calendar for Friday")
    assert "native Pandamonium tools directly" in _selected_agent_context("Friday")


def test_selected_friday_delegates_explicit_project_work():
    assert _selected_worker_request("Inspect the active project's source configuration")
    assert _selected_worker_request("Run the repository tests")
    assert _selected_worker_request("Review the Books service source code")
    assert _selected_worker_request("Fix the calendar integration code")
    assert _selected_worker_request("Check the server configuration")
    assert _selected_worker_request("Read the repository file")
    assert _selected_worker_request("Fix the tests")
    assert _selected_worker_request("Review these files")
    assert _selected_worker_request("Inspect the containers")


def test_worker_result_is_retired_only_from_saved_response_metadata(monkeypatch):
    consumed = []
    monkeypatch.setattr(
        "src.jarvis_agent.consume_task_result",
        lambda task_id, *, owner: consumed.append((task_id, owner)),
    )

    _retire_synthesized_worker_results({
        "tool_events": [
            {"tool": "read_agent_task", "task_id": "running", "task_status": "running"},
            {"tool": "read_agent_task", "task_id": "complete", "task_status": "completed"},
        ],
    }, "leo")

    assert consumed == [("complete", "leo")]
