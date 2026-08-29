from __future__ import annotations

import importlib.util
import http.client
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace


BRIDGE_PATH = Path(__file__).parents[1] / "services" / "pc-codex-bridge" / "jarvis_codex_bridge.py"
SPEC = importlib.util.spec_from_file_location("jarvis_codex_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge)


def test_bridge_standalone_bundle_loads_shared_atomic_writer(tmp_path):
    bundle = tmp_path / "bridge"
    bundle.mkdir()
    shutil.copy2(BRIDGE_PATH, bundle / "jarvis_codex_bridge.py")
    shutil.copy2(Path(__file__).parents[1] / "core" / "atomic_io.py", bundle / "atomic_io.py")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import jarvis_codex_bridge as bridge; bridge.self_check(); print(bridge.atomic_write_json.__module__)",
        ],
        cwd=bundle,
        env={"PATH": str(Path(sys.executable).parent)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "atomic_io"


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


def test_bridge_resumes_after_stable_event_id(tmp_path):
    task = _task(tmp_path)
    task.data["events"] = [
        {"seq": 0, "event_id": "event-1"},
        {"seq": 1, "event_id": "event-2"},
    ]

    assert bridge._resume_after(task, -1, "event-2") == 1
    assert bridge._resume_after(task, 0, "missing") == 0


def test_pc_bridge_routes_task_to_selected_workspace(tmp_path, monkeypatch):
    class IdleThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    source = tmp_path / "Home Lab"
    source.mkdir()
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge, "WORKSPACES", {"home-lab": str(source)})
    monkeypatch.setattr(bridge.threading, "Thread", IdleThread)

    task = bridge.create_task({
        "session_id": "session-1",
        "workspace": "home-lab",
        "prompt": "Inspect the current handoff.",
        "thread_title": "Discord | 2026-08-29 04:31 EDT | #dev-channel | LEO",
        "codex_thread_id": "019f5022-a520-7de0-9208-018cd2d4d222",
    })

    try:
        assert task.data["cwd"] == str(source.resolve())
        assert task.data["source_root"] == str(source.resolve())
        assert task.data["workspace"] == "home-lab"
        assert task.data["thread_title"] == "Discord | 2026-08-29 04:31 EDT | #dev-channel | LEO"
        assert task.data["codex_thread_id"] == "019f5022-a520-7de0-9208-018cd2d4d222"
        assert bridge._runtime_workspace_roots(task) == [str(source.resolve())]
    finally:
        bridge.TASKS.pop(task.task_id, None)


def test_pc_bridge_rejects_missing_routed_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "WORKSPACES", {"missing": str(tmp_path / "missing")})

    try:
        bridge.create_task({"workspace": "missing", "prompt": "Inspect only."})
    except ValueError as exc:
        assert str(exc) == "workspace_not_found"
    else:
        raise AssertionError("bridge accepted a missing routed workspace")


def test_bridge_never_adds_source_as_a_write_root(tmp_path):
    interaction = tmp_path / "interactions"
    source = tmp_path / "source"
    task = _task(interaction)
    task.data.update(
        permission_mode="read_only",
        source_root=str(source),
    )

    assert bridge._runtime_workspace_roots(task) == [str(interaction)]
    assert "Treat it as read-only" in bridge._task_developer_instructions(task)


def test_discord_tasks_receive_jarvis_identity_at_developer_priority(tmp_path):
    task = bridge.Task({
        "task_id": "discord-task",
        "worker": "pc-codex",
        "session_id": "discord:1:2:3:4",
        "workspace": "discord-mod",
        "cwd": str(tmp_path),
        "status": "running",
        "events": [],
    })

    instructions = bridge._task_developer_instructions(task)

    assert instructions.startswith("You are JARVIS, Leo's persistent AI assistant")
    assert "Codex is your execution engine" in instructions
    assert "You are PC Codex working for Jarvis and Leo" not in instructions


def test_bridge_rejects_write_and_caller_preapproval(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.delenv("JARVIS_CODEX_PRIVATE_WORKER_MUTATIONS", raising=False)
    monkeypatch.setattr(bridge, "WORKSPACES", {"home-lab": str(source)})

    for payload in (
        {"permission_mode": "workspace_write", "approved": True},
        {"permission_mode": "read_only", "approved": True},
    ):
        try:
            bridge.create_task({
                "session_id": "session-1",
                "workspace": "home-lab",
                "prompt": "Inspect only.",
                **payload,
            })
        except PermissionError as exc:
            assert str(exc) == "public_tasks_read_only"
        else:
            raise AssertionError("bridge accepted a write-capable task")


def test_bridge_private_profile_allows_only_preapproved_workspace_write(tmp_path, monkeypatch):
    class IdleThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("JARVIS_CODEX_PRIVATE_WORKER_MUTATIONS", "true")
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge, "WORKSPACES", {"home-lab": str(source)})
    monkeypatch.setattr(bridge.threading, "Thread", IdleThread)

    for payload, error in (
        ({"permission_mode": "workspace_write", "approved": False}, "approval_required"),
        ({"permission_mode": "read_only", "approved": True}, "public_tasks_read_only"),
    ):
        try:
            bridge.create_task({
                "session_id": "session-1",
                "workspace": "home-lab",
                "prompt": "Do not run this.",
                **payload,
            })
        except PermissionError as exc:
            assert str(exc) == error
        else:
            raise AssertionError("bridge accepted an invalid private task")

    task = bridge.create_task({
        "session_id": "session-1",
        "workspace": "home-lab",
        "prompt": "Apply the approved fix.",
        "permission_mode": "workspace_write",
        "approved": True,
    })

    try:
        assert task.data["approved"] is True
        assert task.data["cwd"] == str(source.resolve())
        assert bridge._runtime_workspace_roots(task) == [str(source.resolve())]
    finally:
        bridge.TASKS.pop(task.task_id, None)


def test_bridge_private_profile_uses_workspace_write_sandbox(tmp_path, monkeypatch):
    class Process:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO()
            self.stderr = io.StringIO()

        def poll(self):
            return 0

    process = Process()
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("JARVIS_CODEX_PRIVATE_WORKER_MUTATIONS", "true")
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *_args, **_kwargs: process)

    def read_until(task, request_id, timeout=60):
        if request_id == 1:
            return {}
        if request_id == 2:
            return {"thread": {"id": "thread-1"}}
        task.data["status"] = "completed"
        return {"turn": {"id": "turn-1"}}

    monkeypatch.setattr(bridge, "_read_until", read_until)
    task = bridge.Task({
        "task_id": "task-private",
        "worker": "pc-codex",
        "workspace": "home-lab",
        "cwd": str(source),
        "source_root": str(source),
        "permission_mode": "workspace_write",
        "approved": True,
        "prompt": "Apply the approved fix.",
        "status": "queued",
        "events": [],
    })

    bridge._run_task(task)

    messages = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    started = next(message for message in messages if message.get("id") == 2)
    assert started["params"]["sandbox"] == "workspace-write"
    assert started["params"]["runtimeWorkspaceRoots"] == [str(source)]


def test_bridge_waits_for_turn_completed_after_final_answer(tmp_path, monkeypatch):
    messages = [
        {"method": "item/completed", "params": {"item": {
            "type": "agentMessage",
            "phase": "final_answer",
            "text": "ROUTED_CWD_OK",
        }}},
        {"method": "turn/completed", "params": {}},
    ]

    class Process:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("".join(json.dumps(message) + "\n" for message in messages))
            self.stderr = io.StringIO()

        def poll(self):
            return 0

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge.subprocess, "Popen", lambda *_args, **_kwargs: Process())

    def read_until(_task, request_id, timeout=60):
        if request_id == 1:
            return {}
        if request_id == 2:
            return {"thread": {"id": "thread-1"}}
        if request_id == 20:
            return {}
        return {"turn": {"id": "turn-1"}}

    monkeypatch.setattr(bridge, "_read_until", read_until)
    task = bridge.Task({
        "task_id": "task-completion",
        "worker": "pc-codex",
        "workspace": "home-lab",
        "cwd": str(source),
        "source_root": str(source),
        "permission_mode": "read_only",
        "approved": False,
        "prompt": "Reply exactly.",
        "thread_title": "Discord | 2026-08-29 04:31 EDT | #dev-channel | LEO",
        "status": "queued",
        "events": [],
    })

    bridge._run_task(task)

    assert task.data["status"] == "completed"
    assert task.data["result"] == "ROUTED_CWD_OK"
    assert any(
        message == {
            "id": 20,
            "method": "thread/name/set",
            "params": {
                "threadId": "thread-1",
                "name": "Discord | 2026-08-29 04:31 EDT | #dev-channel | LEO",
            },
        }
        for message in map(json.loads, task.proc.stdin.getvalue().splitlines())
    )
    assert task.proc.stdout.tell() == len(task.proc.stdout.getvalue())


def test_codex_command_applies_explicit_model_defaults(monkeypatch):
    monkeypatch.setattr(bridge, "CODEX_BIN", "/usr/bin/codex")
    monkeypatch.setattr(bridge, "CODEX_MODEL", "gpt-5.6-terra")
    monkeypatch.setattr(bridge, "CODEX_REASONING_EFFORT", "high")

    assert bridge._codex_command() == [
        "/usr/bin/codex",
        "-c",
        'model="gpt-5.6-terra"',
        "-c",
        'model_reasoning_effort="high"',
        "app-server",
        "--stdio",
    ]


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


def test_artifact_marker_accepts_document_from_read_only_source_root(tmp_path):
    interaction = tmp_path / "interactions"
    interaction.mkdir()
    source = tmp_path / "Home Lab"
    document = source / "Jarvis Build Folder" / "Mark 6.md"
    document.parent.mkdir(parents=True)
    document.write_text("# Mark 6\n\nVoice baseline.", encoding="utf-8")
    task = _task(interaction)
    task.data["source_root"] = str(source)

    result = bridge._extract_artifacts(
        task,
        'Ready.\n[[ODYSSEUS_ARTIFACT path="Jarvis Build Folder/Mark 6.md" title="Mark 6 Build"]]',
    )

    assert result == "Ready."
    event = task.data["events"][0]
    assert event["type"] == "artifact"
    assert event["metadata"]["title"] == "Mark 6 Build"
    assert event["metadata"]["content"].startswith("# Mark 6")


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


def test_invalid_artifact_cannot_be_overwritten_by_success_result(tmp_path):
    task = _task(tmp_path)

    bridge._handle_server_message(task, {
        "method": "item/completed",
        "params": {"item": {
            "type": "agentMessage",
            "phase": "final_answer",
            "text": 'Opened it.\n[[ODYSSEUS_ARTIFACT path="missing.md"]]',
        }},
    })

    assert task.data["status"] == "failed"
    assert task.data.get("result") is None
    assert [event["type"] for event in task.data["events"]] == ["error"]


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
