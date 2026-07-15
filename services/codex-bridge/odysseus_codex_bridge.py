#!/usr/bin/env python3
"""Token-authenticated, read-only bridge for Codex app-server workers."""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.atomic_io import atomic_write_json

HOST = os.getenv("ODYSSEUS_CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("ODYSSEUS_CODEX_BRIDGE_PORT", "8040"))
TOKEN_FILE = Path(os.getenv("ODYSSEUS_CODEX_BRIDGE_TOKEN_FILE", "odysseus-codex-token"))
STATE_DIR = Path(os.getenv("ODYSSEUS_CODEX_BRIDGE_STATE_DIR", "data/codex-bridge-tasks"))
CODEX_BIN = os.getenv("ODYSSEUS_CODEX_BIN", "codex")
CODEX_MODEL = os.getenv("ODYSSEUS_CODEX_MODEL", "").strip()
CODEX_REASONING_EFFORT = os.getenv("ODYSSEUS_CODEX_REASONING_EFFORT", "").strip()
MAX_TASK_RUNTIME = max(60, int(os.getenv("ODYSSEUS_CODEX_MAX_TASK_SECONDS", "480")))
WORKER_ID = os.getenv("ODYSSEUS_CODEX_WORKER_ID", "pc-codex").strip()
TERMINAL = {"completed", "failed", "cancelled"}


def _load_workspaces() -> dict[str, str]:
    try:
        raw = json.loads(os.getenv("ODYSSEUS_CODEX_WORKSPACES_JSON", "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for name, path in raw.items():
        logical = str(name or "").strip()
        resolved = Path(str(path or "")).expanduser().resolve()
        if logical and resolved.is_absolute() and resolved.is_dir():
            result[logical] = str(resolved)
    return result


WORKSPACES = _load_workspaces()
DEVELOPER_INSTRUCTIONS = os.getenv(
    "ODYSSEUS_CODEX_DEVELOPER_INSTRUCTIONS",
    "You are a read-only Codex worker for Odysseus. Inspect and explain the requested workspace. "
    "Do not modify files, install software, change services, or perform other side effects. "
    "Give concise progress updates and finish with one clear final result.",
).strip()


def _hosts(value: str | None = None) -> tuple[str, ...]:
    configured = value if value is not None else os.getenv("ODYSSEUS_CODEX_BRIDGE_HOSTS", HOST)
    hosts = tuple(dict.fromkeys(part.strip() for part in configured.split(",") if part.strip()))
    if not hosts or any(host in {"0.0.0.0", "::"} for host in hosts):
        raise RuntimeError("bridge_hosts_must_be_explicit")
    return hosts


def _codex_command() -> list[str]:
    command = [CODEX_BIN]
    if CODEX_MODEL:
        command += ["-c", f'model="{CODEX_MODEL}"']
    if CODEX_REASONING_EFFORT:
        command += ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']
    return command + ["app-server", "--stdio"]


class Task:
    def __init__(self, data: dict, prompt: str = ""):
        self.data = data
        self.prompt = prompt
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.proc: subprocess.Popen[str] | None = None
        self.stdin_lock = threading.Lock()
        self.pending_request_id: int | str | None = None
        self.pending_responses: dict[int, dict | None] = {}
        self.next_request_id = 1000

    @property
    def task_id(self) -> str:
        return self.data["task_id"]

    @property
    def cwd(self) -> str:
        return WORKSPACES[self.data["workspace"]]

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = STATE_DIR / f"{self.task_id}.json"
        atomic_write_json(str(path), self.data, indent=2)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def event(self, event_type: str, text: str, metadata: dict | None = None) -> dict | None:
        with self.changed:
            if self.data.get("status") in TERMINAL:
                return None
            events = self.data.setdefault("events", [])
            event = {
                "seq": int(events[-1].get("seq", -1)) + 1 if events else 0,
                "event_id": str(uuid.uuid4()),
                "task_id": self.task_id,
                "worker": WORKER_ID,
                "type": event_type,
                "text": str(text or "")[:12_000],
                "metadata": metadata or {},
                "created_at": int(time.time()),
            }
            events.append(event)
            if event_type == "result":
                self.data.update(status="completed", result=event["text"], error=None)
            elif event_type == "error":
                self.data.update(status="failed", error=event["text"])
            elif event_type == "cancelled":
                self.data["status"] = "cancelled"
            elif event_type == "question":
                self.data["status"] = "waiting"
            else:
                self.data["status"] = "running"
            self.data["updated_at"] = event["created_at"]
            self.save()
            self.changed.notify_all()
            return event

    def send(self, message: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("codex_process_not_running")
        with self.stdin_lock:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 10) -> dict:
        with self.changed:
            request_id = self.next_request_id
            self.next_request_id += 1
            self.pending_responses[request_id] = None
        try:
            self.send({"id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            with self.changed:
                while self.pending_responses[request_id] is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("codex_response_timeout")
                    self.changed.wait(remaining)
                return self.pending_responses[request_id] or {}
        finally:
            with self.changed:
                self.pending_responses.pop(request_id, None)

    def resolve_response(self, message: dict) -> bool:
        if message.get("method"):
            return False
        request_id = message.get("id")
        with self.changed:
            if request_id not in self.pending_responses:
                return False
            self.pending_responses[request_id] = message
            self.changed.notify_all()
            return True


TASKS: dict[str, Task] = {}
TASKS_LOCK = threading.RLock()


def _load_tasks() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in STATE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data.get("task_id"):
                continue
            task = Task(data)
            TASKS[task.task_id] = task
            if data.get("status") not in TERMINAL:
                task.event("error", "The bridge restarted before this task completed.")
        except (OSError, json.JSONDecodeError, KeyError):
            continue


def _token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _authorized(header: str | None) -> bool:
    expected = _token()
    supplied = (header or "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _handle_server_message(task: Task, message: dict) -> None:
    if task.resolve_response(message):
        return
    method = message.get("method")
    params = message.get("params") or {}
    if "id" in message and method == "item/tool/requestUserInput":
        task.pending_request_id = message["id"]
        questions = params.get("questions") or []
        safe_questions = [
            {"id": str(row.get("id") or "")[:128], "question": str(row.get("question") or "")[:2_000]}
            for row in questions[:20]
            if isinstance(row, dict)
        ]
        text = " ".join(row["question"] for row in safe_questions).strip() or "Codex needs input."
        task.event("question", text, {"questions": safe_questions})
        return
    if method != "item/completed":
        return
    item = params.get("item") or {}
    kind = item.get("type")
    if kind == "agentMessage":
        text = str(item.get("text") or "").strip()
        if text:
            phase = str(item.get("phase") or "")
            task.event("progress" if phase == "commentary" else "result", text)
    elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
        task.event("tool_activity", "Codex completed a read-only tool step.", {"item_type": kind})


def _read_until(task: Task, wanted_id: int, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    assert task.proc and task.proc.stdout
    while time.monotonic() < deadline:
        line = task.proc.stdout.readline()
        if not line:
            raise RuntimeError("codex_app_server_closed")
        message = json.loads(line)
        if message.get("id") == wanted_id:
            if "error" in message:
                raise RuntimeError("codex_request_failed")
            result = message.get("result")
            return result if isinstance(result, dict) else {}
        _handle_server_message(task, message)
    raise TimeoutError("codex_response_timeout")


def _drain_stderr(proc: subprocess.Popen[str]) -> None:
    if proc.stderr:
        for _line in proc.stderr:
            pass


def _run_task(task: Task) -> None:
    try:
        task.proc = subprocess.Popen(
            _codex_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=_drain_stderr, args=(task.proc,), daemon=True).start()
        task.send({
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "odysseus-codex-bridge", "version": "1.0"}},
        })
        _read_until(task, 1)
        task.send({"method": "initialized", "params": {}})
        thread_id = task.data.get("codex_thread_id")
        if thread_id:
            task.send({
                "id": 2,
                "method": "thread/resume",
                "params": {
                    "threadId": thread_id,
                    "cwd": task.cwd,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "developerInstructions": DEVELOPER_INSTRUCTIONS,
                },
            })
        else:
            task.send({
                "id": 2,
                "method": "thread/start",
                "params": {
                    "cwd": task.cwd,
                    "runtimeWorkspaceRoots": [task.cwd],
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "ephemeral": False,
                    "developerInstructions": DEVELOPER_INSTRUCTIONS,
                },
            })
        started = _read_until(task, 2)
        thread_id = str((started.get("thread") or {}).get("id") or "")
        if not thread_id:
            raise RuntimeError("codex_thread_missing")
        task.data["codex_thread_id"] = thread_id
        task.save()
        task.event("progress", "Codex opened the read-only task.", {"codex_thread_id": thread_id})
        task.send({
            "id": 3,
            "method": "turn/start",
            "params": {"threadId": thread_id, "input": [{"type": "text", "text": task.prompt}]},
        })
        task.prompt = ""
        turn = _read_until(task, 3)
        turn_id = str((turn.get("turn") or {}).get("id") or "")
        if not turn_id:
            raise RuntimeError("codex_turn_missing")
        task.data["codex_turn_id"] = turn_id
        task.save()
        assert task.proc.stdout
        while task.data.get("status") not in TERMINAL:
            line = task.proc.stdout.readline()
            if not line:
                break
            message = json.loads(line)
            _handle_server_message(task, message)
            if message.get("method") == "turn/completed":
                if task.data.get("status") != "completed":
                    task.event("error", "Codex completed without a final result.")
                break
            if message.get("method") == "turn/failed":
                task.event("error", "Codex could not complete the task.")
                break
    except Exception:
        if task.data.get("status") not in TERMINAL:
            task.event("error", "Codex bridge could not complete the task.")
    finally:
        task.prompt = ""
        if task.proc and task.proc.poll() is None:
            task.proc.terminate()
            try:
                task.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                task.proc.kill()


def _watch_task(task: Task) -> None:
    time.sleep(MAX_TASK_RUNTIME)
    if task.data.get("status") in TERMINAL:
        return
    try:
        task.send({
            "id": 91,
            "method": "turn/interrupt",
            "params": {
                "threadId": task.data["codex_thread_id"],
                "turnId": task.data["codex_turn_id"],
            },
        })
    except Exception:
        pass
    task.event("error", "Codex exceeded the configured task time limit.")


def create_task(payload: dict) -> Task:
    if str(payload.get("permission_mode") or "") != "read_only" or payload.get("approved") is True:
        raise PermissionError("public_tasks_read_only")
    workspace = str(payload.get("workspace") or "").strip()
    if workspace not in WORKSPACES:
        raise ValueError("unknown_workspace")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt or len(prompt) > 50_000:
        raise ValueError("invalid_prompt")
    thread_id = str(payload.get("codex_thread_id") or "").strip() or None
    if thread_id:
        try:
            thread_id = str(uuid.UUID(thread_id))
        except ValueError as exc:
            raise ValueError("invalid_codex_thread_id") from exc
    now = int(time.time())
    task = Task({
        "task_id": str(uuid.uuid4()),
        "worker": WORKER_ID,
        "session_id": str(payload.get("session_id") or "")[:128],
        "workspace": workspace,
        "permission_mode": "read_only",
        "approved": False,
        "codex_thread_id": thread_id,
        "status": "queued",
        "result": None,
        "error": None,
        "events": [],
        "created_at": now,
        "updated_at": now,
    }, prompt=prompt)
    with TASKS_LOCK:
        TASKS[task.task_id] = task
    task.save()
    task.event("accepted", "Codex accepted the read-only task.")
    threading.Thread(target=_run_task, args=(task,), daemon=True).start()
    threading.Thread(target=_watch_task, args=(task,), daemon=True).start()
    return task


def _resume_after(task: Task, after: int, event_id: str) -> int:
    if not event_id:
        return after
    with task.lock:
        event = next(
            (row for row in task.data.get("events") or [] if str(row.get("event_id") or "") == event_id),
            None,
        )
    return int(event.get("seq", after)) if event else after


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "OdysseusCodexBridge/1.0"

    def log_message(self, _fmt: str, *_args) -> None:
        return

    def _auth(self) -> bool:
        if _authorized(self.headers.get("Authorization")):
            return True
        _json(self, 401, {"error": "unauthorized"})
        return False

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 64_000:
            raise ValueError("request_too_large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("invalid_request")
        return payload

    @staticmethod
    def _task(task_id: str) -> Task | None:
        with TASKS_LOCK:
            return TASKS.get(task_id)

    def do_GET(self) -> None:
        if not self._auth():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json(self, 200, {
                "ok": True,
                "worker": WORKER_ID,
                "protocol": "odysseus-worker-v1",
                "permission_profile": "read_only_enforced",
                "capabilities": ["tasks", "events", "cancel", "steer", "reply", "read_only"],
                "workspaces": sorted(WORKSPACES),
            })
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) not in {3, 4} or parts[:2] != ["v1", "tasks"]:
            _json(self, 404, {"error": "not_found"})
            return
        task = self._task(parts[2])
        if not task:
            _json(self, 404, {"error": "task_not_found"})
            return
        if len(parts) == 3:
            with task.lock:
                _json(self, 200, task.data)
            return
        if parts[3] != "events":
            _json(self, 404, {"error": "not_found"})
            return
        try:
            after = int((parse_qs(parsed.query).get("after") or ["-1"])[0])
        except ValueError:
            after = -1
        after = _resume_after(task, after, str(self.headers.get("Last-Event-ID") or ""))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        next_seq = after + 1
        try:
            while True:
                with task.changed:
                    events = task.data.get("events") or []
                    while next_seq < len(events):
                        self.wfile.write(f"data: {json.dumps(events[next_seq])}\n\n".encode())
                        self.wfile.flush()
                        next_seq += 1
                    if task.data.get("status") in TERMINAL:
                        break
                    task.changed.wait(timeout=10)
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:
        if not self._auth():
            return
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            payload = self._body()
            if parts == ["v1", "tasks"]:
                _json(self, 202, create_task(payload).data)
                return
            if len(parts) != 4 or parts[:2] != ["v1", "tasks"]:
                _json(self, 404, {"error": "not_found"})
                return
            task = self._task(parts[2])
            if not task:
                _json(self, 404, {"error": "task_not_found"})
                return
            action = parts[3]
            if action == "cancel":
                if task.data.get("status") not in TERMINAL:
                    try:
                        task.send({
                            "id": 90,
                            "method": "turn/interrupt",
                            "params": {
                                "threadId": task.data["codex_thread_id"],
                                "turnId": task.data["codex_turn_id"],
                            },
                        })
                    except Exception:
                        pass
                    task.event("cancelled", "Codex task cancelled.")
                _json(self, 200, task.data)
                return
            if action == "steer":
                prompt = str(payload.get("prompt") or "").strip()
                if not prompt or len(prompt) > 50_000 or task.data.get("status") != "running":
                    raise ValueError("invalid_steer")
                response = task.request("turn/steer", {
                    "threadId": task.data["codex_thread_id"],
                    "expectedTurnId": task.data["codex_turn_id"],
                    "input": [{"type": "text", "text": prompt}],
                })
                if response.get("error"):
                    raise RuntimeError("task_not_steerable")
                _json(self, 200, {"ok": True, "task_id": task.task_id})
                return
            if action == "reply":
                if task.pending_request_id is None:
                    raise RuntimeError("task_not_waiting_for_input")
                answers = payload.get("answers") or {}
                normalized = {
                    str(key): {"answers": value if isinstance(value, list) else [str(value)]}
                    for key, value in answers.items()
                }
                task.send({"id": task.pending_request_id, "result": {"answers": normalized}})
                task.pending_request_id = None
                task.data["status"] = "running"
                task.save()
                _json(self, 200, {"ok": True, "task_id": task.task_id})
                return
            if action == "approval" and payload.get("choice") == "deny":
                _json(self, 200, {"ok": True, "choice": "deny"})
                return
            _json(self, 404, {"error": "not_found"})
        except PermissionError:
            _json(self, 403, {"error": "public_tasks_read_only"})
        except (ValueError, json.JSONDecodeError):
            _json(self, 400, {"error": "invalid_request"})
        except Exception:
            _json(self, 409, {"error": "task_action_unavailable"})


def self_check() -> None:
    assert WORKER_ID in {"pc-codex", "vps-codex"}
    assert _codex_command()[-2:] == ["app-server", "--stdio"]
    assert _hosts("127.0.0.1,127.0.0.1") == ("127.0.0.1",)
    if not WORKSPACES:
        raise RuntimeError("workspace_configuration_required")
    if not _token():
        raise RuntimeError("bridge_token_required")


if __name__ == "__main__":
    self_check()
    _load_tasks()
    servers = [ThreadingHTTPServer((host, PORT), Handler) for host in _hosts()]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()
