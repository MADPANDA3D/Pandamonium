from __future__ import annotations

import pytest

import src.jarvis_agent as jarvis_agent


def _task(**updates):
    task = {
        "task_id": "task-1",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "home-lab",
        "status": "running",
        "owner": "leo",
        "events": [],
        "artifacts": [],
    }
    task.update(updates)
    return task


@pytest.mark.asyncio
async def test_spoken_result_caps_source_and_output(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "Outcome complete. No blocker remains. Next, review the result. " * 20}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured.update(url=url, payload=json)
            return Response()

    monkeypatch.setattr(jarvis_agent.httpx, "AsyncClient", Client)
    spoken = await jarvis_agent._spoken_result(_task(), "x" * 20_000)

    assert len(spoken) <= 600
    assert spoken.endswith(".")
    assert len(captured["payload"]["prompt"].split("Worker result:\n", 1)[1]) == 16_000
    assert captured["url"].endswith("/api/generate")


@pytest.mark.asyncio
async def test_spoken_result_falls_back_when_qwen_fails(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            raise RuntimeError("offline")

    monkeypatch.setattr(jarvis_agent.httpx, "AsyncClient", Client)
    assert await jarvis_agent._spoken_result(_task(), "full raw result") == (
        "PC Codex finished. The full result is in the chat."
    )


@pytest.mark.asyncio
async def test_spoken_milestone_is_one_bounded_sentence(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": "The focused verification now passes cleanly. A second sentence must not be spoken. "
                + ("Extra detail " * 40)
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            captured.update(url=url, payload=json)
            return Response()

    monkeypatch.setattr(jarvis_agent.httpx, "AsyncClient", Client)
    spoken = await jarvis_agent._spoken_milestone(_task(), "Tests completed.")

    assert spoken == "The focused verification now passes cleanly."
    assert len(spoken) <= 240
    assert captured["payload"]["options"]["num_predict"] == 80


@pytest.mark.asyncio
async def test_spoken_milestone_failure_uses_non_raw_fallback(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, json):
            raise RuntimeError("offline")

    monkeypatch.setattr(jarvis_agent.httpx, "AsyncClient", Client)
    spoken = await jarvis_agent._spoken_milestone(_task(), "SECRET RAW TABLE CONTENT")

    assert spoken == "PC Codex completed a milestone; details are in the activity history."
    assert "SECRET RAW TABLE CONTENT" not in spoken
    assert len(spoken) <= 240


@pytest.mark.asyncio
async def test_progress_speech_is_broker_owned_and_milestone_only(tmp_path, monkeypatch):
    class Adapter:
        async def events(self, _task):
            yield {
                "type": "progress",
                "text": "Ordinary progress remains visual.",
                "spoken_text": "Worker tried to narrate ordinary progress.",
                "metadata": {},
            }
            yield {
                "type": "progress",
                "text": "The verification subtask passed.",
                "spoken_text": "Worker supplied raw milestone narration.",
                "metadata": {"milestone": True},
            }

    async def milestone(_task, _text):
        return "Jarvis confirms the verification subtask passed."

    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(jarvis_agent, "_spoken_milestone", milestone)
    jarvis_agent._save_task(_task())

    await jarvis_agent._mirror("task-1")

    events = jarvis_agent.get_task("task-1")["events"]
    assert "spoken_text" not in events[0]
    assert events[1]["spoken_text"] == "Jarvis confirms the verification subtask passed."
    assert events[1]["metadata"]["milestone"] is True


@pytest.mark.asyncio
async def test_live_result_keeps_raw_chat_text_and_adds_spoken_summary(tmp_path, monkeypatch):
    raw = "| Item | Status |\n| --- | --- |\n| Mark 6 | complete |"

    class Adapter:
        async def events(self, _task):
            yield {"type": "result", "text": raw, "metadata": {}}

    class SessionManager:
        def __init__(self):
            self.messages = []

        def add_message(self, session_id, message):
            self.messages.append((session_id, message))

    async def summary(_task, _text):
        return "PC Codex finished the Mark 6 check. The full table is in the chat."

    manager = SessionManager()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(jarvis_agent, "_spoken_result", summary)
    jarvis_agent._save_task(_task())

    await jarvis_agent._mirror("task-1")

    saved = jarvis_agent.get_task("task-1")
    assert saved["result"] == raw
    assert saved["events"][0]["text"] == raw
    assert saved["events"][0]["spoken_text"].startswith("PC Codex finished")
    assert manager.messages[0][1].content == raw
    assert manager.messages[0][1].metadata["character_name"] == "PC Codex"


@pytest.mark.asyncio
async def test_reconciled_result_also_gets_spoken_summary(tmp_path, monkeypatch):
    class Adapter:
        async def status(self, _task):
            return {"status": "completed", "result": "Raw reconciled result"}

    async def summary(_task, _text):
        return "PC Codex completed the task. Review the full result in chat."

    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(jarvis_agent, "_spoken_result", summary)
    jarvis_agent._save_task(_task())

    saved = await jarvis_agent.refresh_task("task-1")

    assert saved["events"][0]["text"] == "Raw reconciled result"
    assert saved["events"][0]["spoken_text"].startswith("PC Codex completed")


@pytest.mark.asyncio
async def test_mirror_failure_appends_terminal_error_event(tmp_path, monkeypatch):
    class Adapter:
        async def events(self, _task):
            raise RuntimeError("stream broke")
            yield

    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    jarvis_agent._save_task(_task())

    await jarvis_agent._mirror("task-1")

    saved = jarvis_agent.get_task("task-1")
    assert saved["status"] == "failed"
    assert saved["events"][0]["type"] == "error"
    assert saved["events"][0]["text"] == "worker_stream_failed: stream broke"
