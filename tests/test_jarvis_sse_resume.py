from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import routes.agent_task_routes as agent_task_routes
import src.jarvis_agent as jarvis_agent


@pytest.mark.asyncio
async def test_task_event_stream_emits_sse_ids_and_resumes_after_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    jarvis_agent._save_task({
        "task_id": "task-1",
        "worker": "pc-codex",
        "status": "completed",
        "events": [
            {"seq": 0, "type": "progress", "text": "Working."},
            {"seq": 1, "type": "result", "text": "Done."},
        ],
    })

    chunks = [chunk async for chunk in jarvis_agent.stream_task_events("task-1", after=0)]

    assert len(chunks) == 1
    assert chunks[0].startswith('id: 1\ndata: {"seq": 1,')


def _events_endpoint():
    router = agent_task_routes.setup_agent_task_routes(MagicMock())
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/api/agent-tasks/{task_id}/events"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "query_after", "expected"),
    [("7", None, 7), ("invalid", None, -1), (None, None, -1), ("7", 2, 2)],
)
async def test_task_event_route_resumes_from_header_unless_query_wins(
    monkeypatch,
    header,
    query_after,
    expected,
):
    captured = []

    async def body():
        yield ": done\n\n"

    def stream(_task_id, after):
        captured.append(after)
        return body()

    headers = {} if header is None else {"last-event-id": header}
    monkeypatch.setattr(agent_task_routes, "get_task", lambda _task_id: {"owner": "leo"})
    monkeypatch.setattr(agent_task_routes, "stream_task_events", stream)

    await _events_endpoint()(
        "task-1",
        SimpleNamespace(headers=headers),
        after=query_after,
        owner="leo",
    )

    assert captured == [expected]
