from routes.chat_routes import (
    _retire_synthesized_worker_results,
    _selected_agent_context,
    _selected_worker_request,
)
from src.worker_routing import selected_worker_workspace


def test_selected_friday_uses_native_app_tools_for_small_lookups():
    assert not _selected_worker_request("List all books in my library")
    assert not _selected_worker_request("Check my calendar for Friday")
    assert "native Pandamonium tools directly" in _selected_agent_context("Friday")


def test_selected_friday_defaults_to_home_lab_without_inheriting_business(monkeypatch):
    monkeypatch.setattr("src.worker_routing.worker_catalog", lambda: {
        "pc-codex": {"workspaces": ["business", "home-lab"]},
    })

    assert selected_worker_workspace("pc-codex", "Inspect the Pandamonium source") == "home-lab"
    assert selected_worker_workspace("pc-codex", "Inspect the Business workspace") == "business"
    context = _selected_agent_context("Friday", "home-lab")
    assert "server-selected `home-lab` workspace" in context


def test_selected_friday_delegates_explicit_project_work():
    assert _selected_worker_request("Inspect the active project's source configuration")
    assert _selected_worker_request("Run the repository tests")
    assert _selected_worker_request("Review the Books service source code")
    assert _selected_worker_request("Fix the calendar integration code")
    assert _selected_worker_request("Check the server configuration")
    assert _selected_worker_request("Search the repository for this symbol")
    assert _selected_worker_request("Find the configuration file")
    assert _selected_worker_request("Read the repository file")
    assert _selected_worker_request("Fix the tests")
    assert _selected_worker_request("Review these files")
    assert _selected_worker_request("Inspect the containers")
    assert _selected_worker_request("Create a repository script")
    assert _selected_worker_request("Start the project server")
    assert _selected_worker_request("Stop the project service")
    assert _selected_worker_request("Compare these files")
    assert _selected_worker_request("Fix the authentication bug")
    assert _selected_worker_request("Debug the API")
    assert not _selected_worker_request("Do not run the repository tests")
    assert not _selected_worker_request("Don't deploy the service")
    assert not _selected_worker_request("I don't want you to run the repository tests")
    assert not _selected_worker_request("Don't ever under any circumstances deploy the service")
    assert _selected_worker_request("I don't know why it failed, but fix the API bug")
    assert _selected_worker_request("Don't run tests, but fix the API bug")
    assert not _selected_worker_request("Read the book Clean Code")
    assert not _selected_worker_request("Find the book in my library that still needs OCR")
    assert not _selected_worker_request("Read the email about the API bug")


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


def test_selected_friday_routes_contextual_followup_only_with_active_task(monkeypatch):
    monkeypatch.setattr(
        "src.jarvis_agent.find_active_task",
        lambda session_id, worker, workspace, owner: {
            "task_id": "active",
        } if (session_id, worker, workspace, owner) == (
            "chat-1", "pc-codex", None, "leo",
        ) else None,
    )

    assert not _selected_worker_request("Fix that")
    assert not _selected_worker_request(
        "Do not fix that",
        session_id="chat-1",
        owner="leo",
    )
    assert _selected_worker_request(
        "Fix that",
        session_id="chat-1",
        owner="leo",
    )
    assert not _selected_worker_request(
        "Read this book",
        session_id="chat-1",
        owner="leo",
    )
    for app_request in (
        "Update this scheduled task",
        "Fix that todo",
        "Change this reminder",
    ):
        assert not _selected_worker_request(
            app_request,
            session_id="chat-1",
            owner="leo",
        )
