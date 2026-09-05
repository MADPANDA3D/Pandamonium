from routes.chat_routes import _selected_agent_context, _selected_worker_request


def test_selected_friday_uses_native_app_tools_for_small_lookups():
    assert not _selected_worker_request("List all books in my library")
    assert not _selected_worker_request("Check my calendar for Friday")
    assert "native Pandamonium tools directly" in _selected_agent_context("Friday")


def test_selected_friday_delegates_explicit_project_work():
    assert _selected_worker_request("Inspect the active project's source configuration")
    assert _selected_worker_request("Run the repository tests")
    assert _selected_worker_request("Review the Books service source code")
    assert _selected_worker_request("Fix the calendar integration code")
