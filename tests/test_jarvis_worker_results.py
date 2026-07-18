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

    assert spoken == "PC Codex: The focused verification now passes cleanly."
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
                "metadata": {"progress_summary": True},
            }
            yield {
                "type": "progress",
                "text": "The verification subtask passed.",
                "spoken_text": "Worker supplied raw milestone narration.",
                "metadata": {"milestone": True},
            }
            yield {"type": "cancelled", "text": "Test stream complete."}

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
    assert "progress_summary" not in events[0]["metadata"]
    assert events[1]["spoken_text"] == "Jarvis confirms the verification subtask passed."
    assert events[1]["metadata"]["milestone"] is True


@pytest.mark.asyncio
async def test_every_third_progress_summary_is_per_task_and_milestone_resets_window(tmp_path, monkeypatch):
    updates = {
        "task-a": ["A one", "A two", "A three", "A four", "A milestone", "A five", "A six", "A seven"],
        "task-b": ["B one", "B two"],
    }

    class Adapter:
        async def events(self, task):
            for index, text in enumerate(updates[task["task_id"]]):
                metadata = {"milestone": True} if text == "A milestone" else {}
                yield {"type": "progress", "text": text, "metadata": metadata}
            yield {"type": "cancelled", "text": "Test stream complete."}

    calls = []

    async def progress(task, texts):
        calls.append((task["task_id"], list(texts)))
        return f"{task['worker']} summarized {texts[-1]}."

    async def milestone(_task, _text):
        return "PC Codex completed the milestone."

    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter(), "hermes": Adapter()})
    monkeypatch.setattr(jarvis_agent, "_spoken_progress", progress)
    monkeypatch.setattr(jarvis_agent, "_spoken_milestone", milestone)
    jarvis_agent._save_task(_task(task_id="task-a"))
    jarvis_agent._save_task(_task(task_id="task-b", worker="hermes"))

    await jarvis_agent._mirror("task-a")
    await jarvis_agent._mirror("task-b")

    assert calls == [
        ("task-a", ["A one", "A two", "A three"]),
        ("task-a", ["A five", "A six", "A seven"]),
    ]
    task_a = jarvis_agent.get_task("task-a")["events"]
    task_b = jarvis_agent.get_task("task-b")["events"]
    assert task_a[2]["metadata"]["progress_summary"] is True
    assert task_a[4]["spoken_text"] == "PC Codex completed the milestone."
    assert task_a[7]["metadata"]["progress_summary"] is True
    assert all("spoken_text" not in event for event in task_b)


@pytest.mark.asyncio
async def test_live_result_keeps_raw_chat_text_and_adds_spoken_summary(tmp_path, monkeypatch):
    raw = "| Item | Status |\n| --- | --- |\n| Mark 6 | complete |"

    class Adapter:
        async def events(self, _task):
            yield {"type": "result", "text": raw, "metadata": {}}

    class SessionManager:
        def __init__(self):
            self.messages = []

        def get_session(self, _session_id):
            return type("Session", (), {"history": [message for _, message in self.messages]})()

        def add_message(self, session_id, message):
            self.messages.append((session_id, message))
            message.metadata["_db_id"] = f"db-{len(self.messages)}"

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
    assert saved["events"][0]["metadata"]["result_summary"] is True
    assert manager.messages[0][1].content == raw
    assert manager.messages[0][1].metadata["character_name"] == "PC Codex"
    assert manager.messages[1][1].content == "PC Codex finished the Mark 6 check. The full table is in the chat."
    assert manager.messages[1][1].metadata["character_name"] == "Jarvis"


def test_broker_progress_summaries_persist_once_with_jarvis_attribution(tmp_path, monkeypatch):
    class SessionManager:
        def __init__(self):
            self.session = type("Session", (), {"history": [], "message_count": 0})()

        def get_session(self, _session_id):
            return self.session

        def add_message(self, _session_id, message):
            self.session.history.append(message)
            self.session.message_count = len(self.session.history)
            message.metadata["_db_id"] = f"db-{len(self.session.history)}"

    manager = SessionManager()
    manager.session.history.append(jarvis_agent.ChatMessage("assistant", "Other task summary.", metadata={
        "source": "jarvis_worker_summary",
        "task_id": "other-task",
        "worker_event_id": "progress-summary-1",
        "_db_id": "db-other-task",
    }))
    manager.session.message_count = 1
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    jarvis_agent._save_task(_task())

    progress = {
        "event_id": "progress-summary-1",
        "type": "progress",
        "text": "Three raw worker updates remain in activity history.",
        "spoken_text": "PC Codex has verified the three highest-priority client items.",
        "metadata": {"progress_summary": True},
    }
    milestone = {
        "event_id": "milestone-1",
        "type": "progress",
        "text": "The current ledger review is complete.",
        "spoken_text": "PC Codex finished reviewing the current Business ledger.",
        "metadata": {"milestone": True},
    }
    jarvis_agent._append_event("task-1", progress)
    jarvis_agent._append_event("task-1", progress)
    jarvis_agent._append_event("task-1", milestone)
    jarvis_agent._append_event("task-1", {
        "event_id": "result-1",
        "type": "result",
        "text": "| Client | Status |\n| --- | --- |\n| Acme | Ready |",
        "spoken_text": "PC Codex finished. The full report is in chat.",
        "metadata": {},
    })

    saved = jarvis_agent.get_task("task-1")
    assert [event["event_id"] for event in saved["events"]] == [
        "progress-summary-1", "milestone-1", "result-1",
    ]
    assert saved["events"][0]["text"].startswith("Three raw worker updates")
    assert [message.content for message in manager.session.history] == [
        "Other task summary.",
        "PC Codex has verified the three highest-priority client items.",
        "PC Codex finished reviewing the current Business ledger.",
        "| Client | Status |\n| --- | --- |\n| Acme | Ready |",
        "PC Codex finished. The full report is in chat.",
    ]
    summary_metadata = manager.session.history[1].metadata
    assert {key: value for key, value in summary_metadata.items() if key != "_db_id"} == {
        "source": "jarvis_worker_summary",
        "worker": "pc-codex",
        "task_id": "task-1",
        "worker_event_id": "progress-summary-1",
        "character_name": "Jarvis",
    }
    assert manager.session.history[-2].metadata["source"] == "agent_worker"
    assert manager.session.history[-1].metadata["source"] == "jarvis_worker_summary"
    assert manager.session.history[-1].metadata["worker_event_id"] == "result-1"


def test_progress_summary_retries_after_transient_persistence_failure(tmp_path, monkeypatch):
    class SessionManager:
        def __init__(self):
            self.session = type("Session", (), {"history": [], "message_count": 0})()
            self.failures = 1

        def get_session(self, _session_id):
            return self.session

        def add_message(self, _session_id, message):
            self.session.history.append(message)
            self.session.message_count = len(self.session.history)
            if self.failures:
                self.failures -= 1
                return
            message.metadata["_db_id"] = "db-summary-retry-1"

    manager = SessionManager()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    jarvis_agent._save_task(_task())
    event = {
        "event_id": "summary-retry-1",
        "type": "progress",
        "text": "Raw progress.",
        "spoken_text": "PC Codex verified the current priority.",
        "metadata": {"progress_summary": True},
    }

    jarvis_agent._append_event("task-1", event)
    assert manager.session.history == []
    saved = jarvis_agent.get_task("task-1")
    assert jarvis_agent._persist_worker_summary(saved, saved["events"][0]) is True
    assert jarvis_agent._persist_worker_summary(saved, saved["events"][0]) is True
    assert len(manager.session.history) == 1
    assert manager.session.history[0].metadata["worker_event_id"] == "summary-retry-1"


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

    saved = await jarvis_agent.refresh_task("task-1", owner="leo")

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
    monkeypatch.setattr(jarvis_agent, "STREAM_RECONCILE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    jarvis_agent._save_task(_task())

    await jarvis_agent._mirror("task-1")

    saved = jarvis_agent.get_task("task-1")
    assert saved["status"] == "failed"
    assert saved["events"][0]["type"] == "error"
    assert saved["events"][0]["text"].startswith(
        "worker_stream_failed: status reconciliation timed out"
    )
