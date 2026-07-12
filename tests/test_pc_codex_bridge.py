from __future__ import annotations

import importlib.util
import http.client
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace


BRIDGE_PATH = Path(__file__).parents[1] / "services" / "pc-codex-bridge" / "jarvis_codex_bridge.py"
SPEC = importlib.util.spec_from_file_location("jarvis_codex_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge)


def _task(tmp_path: Path):
    bridge.STATE_DIR = tmp_path / "state"
    return bridge.Task({
        "task_id": "task-1",
        "worker": "pc-codex",
        "workspace": "home-lab",
        "cwd": str(tmp_path),
        "status": "running",
        "events": [],
    })


class _ReplyingStdin(io.StringIO):
    def __init__(self, task, error=None):
        super().__init__()
        self.task = task
        self.error = error
        self.sent = None

    def write(self, value):
        self.sent = json.loads(value)
        response = {"id": self.sent["id"]}
        if self.error:
            response["error"] = self.error
        else:
            response["result"] = {"turnId": self.task.data["codex_turn_id"]}
        bridge._handle_server_message(self.task, response)
        return len(value)


def _active_task(tmp_path: Path, error=None):
    task = _task(tmp_path)
    task.data.update(codex_thread_id="thread-1", codex_turn_id="turn-1")
    stdin = _ReplyingStdin(task, error)
    task.proc = SimpleNamespace(stdin=stdin, poll=lambda: None)
    return task, stdin


def test_bridge_hosts_require_explicit_interfaces():
    assert bridge._configured_hosts("127.0.0.1,100.64.0.1,127.0.0.1") == ("127.0.0.1", "100.64.0.1")
    for wildcard in ("0.0.0.0", "::"):
        try:
            bridge._configured_hosts(wildcard)
        except RuntimeError as exc:
            assert str(exc) == "bridge_hosts_must_be_explicit"
        else:
            raise AssertionError(f"wildcard host {wildcard} was accepted")


def test_artifact_marker_emits_validated_document_event(tmp_path):
    document = tmp_path / "Mark 5.md"
    document.write_text("# Mark 5\n\nSuccess, slow.", encoding="utf-8")
    task = _task(tmp_path)
    result = bridge._extract_artifacts(
        task,
        'Ready.\n[[ODYSSEUS_ARTIFACT path="Mark 5.md" title="Mark 5 Build"]]',
    )
    assert result == "Ready."
    event = task.data["events"][0]
    assert event["type"] == "artifact"
    assert event["metadata"]["title"] == "Mark 5 Build"
    assert event["metadata"]["content"].startswith("# Mark 5")


def test_artifact_marker_rejects_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    task = _task(tmp_path)
    bridge._extract_artifacts(task, f'[[ODYSSEUS_ARTIFACT path="{outside}"]]')
    assert task.data["events"][0]["type"] == "error"
    assert all(event["type"] != "artifact" for event in task.data["events"])


def test_artifact_marker_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-through-link.md"
    outside.write_text("private", encoding="utf-8")
    (tmp_path / "linked.md").symlink_to(outside)
    task = _task(tmp_path)
    bridge._extract_artifacts(task, '[[ODYSSEUS_ARTIFACT path="linked.md"]]')
    assert task.data["events"][0]["type"] == "error"
    assert all(event["type"] != "artifact" for event in task.data["events"])


def test_commentary_milestone_marker_is_stripped_and_tagged(tmp_path):
    task = _task(tmp_path)

    bridge._handle_server_message(task, {
        "method": "item/completed",
        "params": {"item": {
            "type": "agentMessage",
            "phase": "commentary",
            "text": "[[ODYSSEUS_MILESTONE]] The focused tests now pass.",
        }},
    })

    assert task.data["events"][0]["type"] == "progress"
    assert task.data["events"][0]["text"] == "The focused tests now pass."
    assert task.data["events"][0]["metadata"] == {"phase": "commentary", "milestone": True}


def test_unmarked_commentary_cannot_set_milestone(tmp_path):
    task = _task(tmp_path)

    bridge._handle_server_message(task, {
        "method": "item/completed",
        "params": {"item": {
            "type": "agentMessage",
            "phase": "commentary",
            "text": "I will mention [[ODYSSEUS_MILESTONE]] later.",
        }},
    })

    assert task.data["events"][0]["text"] == "I will mention [[ODYSSEUS_MILESTONE]] later."
    assert "milestone" not in task.data["events"][0]["metadata"]

    glued = _task(tmp_path / "glued")
    bridge._handle_server_message(glued, {
        "method": "item/completed",
        "params": {"item": {
            "type": "agentMessage",
            "phase": "commentary",
            "text": "[[ODYSSEUS_MILESTONE]]not-the-contract",
        }},
    })
    assert "milestone" not in glued.data["events"][0]["metadata"]


def test_matching_commentary_and_milestone_are_both_preserved(tmp_path):
    task = _task(tmp_path)

    for text in (
        "The focused tests now pass.",
        "[[ODYSSEUS_MILESTONE]] The focused tests now pass.",
    ):
        bridge._handle_server_message(task, {
            "method": "item/completed",
            "params": {"item": {
                "type": "agentMessage",
                "phase": "commentary",
                "text": text,
            }},
        })

    assert [event["text"] for event in task.data["events"]] == [
        "The focused tests now pass.",
        "The focused tests now pass.",
    ]
    assert "milestone" not in task.data["events"][0]["metadata"]
    assert task.data["events"][1]["metadata"]["milestone"] is True
    assert task.data["events"][0]["event_id"] != task.data["events"][1]["event_id"]


def test_steer_uses_active_turn_and_existing_stdout_dispatch(tmp_path):
    task, stdin = _active_task(tmp_path)
    before = json.dumps(task.data, sort_keys=True)

    result = bridge.steer_task(task, "Use the corrected client name.")

    assert result == {
        "ok": True,
        "task_id": "task-1",
        "codex_thread_id": "thread-1",
        "codex_turn_id": "turn-1",
    }
    assert stdin.sent == {
        "id": 1000,
        "method": "turn/steer",
        "params": {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "input": [{"type": "text", "text": "Use the corrected client name."}],
        },
    }
    assert json.dumps(task.data, sort_keys=True) == before
    assert task.pending_responses == {}


def test_steer_rejections_leave_task_nonterminal(tmp_path):
    inactive = _task(tmp_path)
    try:
        bridge.steer_task(inactive, "Follow up")
    except RuntimeError as exc:
        assert str(exc) == "codex_turn_not_active"
    else:
        raise AssertionError("inactive task accepted steering")
    assert inactive.data["status"] == "running"

    task, _stdin = _active_task(tmp_path, {"code": -32602, "message": "active turn not steerable"})
    try:
        bridge.steer_task(task, "Follow up")
    except RuntimeError as exc:
        assert str(exc) == "task_not_steerable"
    else:
        raise AssertionError("app-server rejection was accepted")
    assert task.data["status"] == "running"
    assert task.data["events"] == []


def test_server_request_id_collision_is_not_consumed_as_steer_response(tmp_path):
    task = _task(tmp_path)
    task.pending_responses[1000] = None

    bridge._handle_server_message(task, {
        "id": 1000,
        "method": "item/tool/requestUserInput",
        "params": {"questions": [{"question": "Which client?"}]},
    })

    assert task.pending_responses[1000] is None
    assert task.pending_request_id == 1000
    assert task.data["events"][0]["type"] == "question"


def test_steer_endpoint_requires_authentication(tmp_path):
    task, _stdin = _active_task(tmp_path)
    token_file = tmp_path / "token"
    token_file.write_text("secret-token", encoding="utf-8")
    original_token_file = bridge.TOKEN_FILE
    bridge.TOKEN_FILE = token_file
    bridge.TASKS[task.task_id] = task
    server = bridge.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = json.dumps({"prompt": "Keep the task focused."})
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        connection.request("POST", "/v1/tasks/task-1/steer", body=body)
        assert connection.getresponse().status == 401
        connection.close()

        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        connection.request(
            "POST",
            "/v1/tasks/task-1/steer",
            body=body,
            headers={"Authorization": "Bearer secret-token"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["codex_turn_id"] == "turn-1"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        bridge.TASKS.pop(task.task_id, None)
        bridge.TOKEN_FILE = original_token_file
