#!/usr/bin/env python3
from __future__ import annotations

import hmac
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = os.getenv("JARVIS_CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_CODEX_BRIDGE_PORT", "8040"))
TOKEN_FILE = Path(os.getenv("JARVIS_CODEX_BRIDGE_TOKEN_FILE", str(Path.home() / ".config/jarvis/agent-bridge-token")))
STATE_DIR = Path(os.getenv("JARVIS_CODEX_BRIDGE_STATE_DIR", str(Path.home() / ".local/share/jarvis/pc-codex-bridge/tasks")))
CODEX_BIN = os.getenv("JARVIS_CODEX_BIN", "codex")
MAX_TASK_RUNTIME = int(os.getenv("JARVIS_CODEX_MAX_TASK_SECONDS", "480"))
WORKER_ID = os.getenv("JARVIS_CODEX_WORKER_ID", "pc-codex").strip() or "pc-codex"
WORKER_LABEL = "VPS Codex" if WORKER_ID == "vps-codex" else "PC Codex"
TERMINAL = {"completed", "failed", "cancelled"}
DEFAULT_WORKSPACES = {"workspace": str(Path.home())}
if WORKER_ID == "vps-codex":
    DEFAULT_WORKSPACES = {"vps-ops": "/home/jarvis-worker/workspaces/vps-ops"}
try:
    WORKSPACES = json.loads(os.getenv("JARVIS_CODEX_WORKSPACES_JSON", "{}")) or DEFAULT_WORKSPACES
except json.JSONDecodeError as exc:
    raise RuntimeError("invalid_workspace_configuration") from exc
if not isinstance(WORKSPACES, dict) or not WORKSPACES:
    raise RuntimeError("workspace_configuration_required")

DEVELOPER_INSTRUCTIONS = os.getenv("JARVIS_CODEX_DEVELOPER_INSTRUCTIONS", "") or """You are PC Codex working for Jarvis and Leo.
Give short, useful commentary updates at meaningful milestones while you work.
Do not narrate raw commands or internal reasoning. End with a standalone result.
Respect the selected sandbox. Ask a focused question only when genuinely blocked.
When Leo asks to open, show, or put a text document in Odysseus, finish with exactly one marker on its own line:
[[ODYSSEUS_ARTIFACT path="path inside the active workspace" title="Human title"]]
Only emit that marker for a file you verified exists inside the active workspace.
"""
ARTIFACT_PATTERN = re.compile(
    r'\[\[ODYSSEUS_ARTIFACT\s+path="([^"]+)"(?:\s+title="([^"]+)")?\s*\]\]'
)
ARTIFACT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".log"}
ARTIFACT_MAX_BYTES = 2_000_000


def _configured_hosts(value: str | None = None) -> tuple[str, ...]:
    configured = value if value is not None else os.getenv("JARVIS_CODEX_BRIDGE_HOSTS", HOST)
    hosts = tuple(dict.fromkeys(part.strip() for part in configured.split(",") if part.strip()))
    if not hosts or any(host in {"0.0.0.0", "::"} for host in hosts):
        raise RuntimeError("bridge_hosts_must_be_explicit")
    return hosts


class Task:
    def __init__(self, data: dict):
        self.data = data
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

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = STATE_DIR / f"{self.task_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def event(self, event_type: str, text: str, metadata: dict | None = None) -> dict:
        with self.changed:
            events = self.data.setdefault("events", [])
            if event_type == "progress" and events and events[-1].get("type") == "progress" and events[-1].get("text") == text:
                return events[-1]
            event = {
                "seq": len(events),
                "event_id": str(uuid.uuid4()),
                "task_id": self.task_id,
                "worker": WORKER_ID,
                "type": event_type,
                "text": text[:12000],
                "created_at": int(time.time()),
                "metadata": metadata or {},
            }
            events.append(event)
            self.data["updated_at"] = event["created_at"]
            if event_type == "result":
                self.data.update(status="completed", result=text)
            elif event_type == "error":
                self.data.update(status="failed", error=text)
            elif event_type == "cancelled":
                self.data["status"] = "cancelled"
            elif event_type == "question":
                self.data["status"] = "waiting"
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
                        raise TimeoutError(f"codex_response_timeout_{request_id}")
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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for path in STATE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") not in TERMINAL:
                data["status"] = "failed"
                data["error"] = "bridge_restarted_before_task_completed"
            TASKS[data["task_id"]] = Task(data)
        except Exception:
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


def _safe_tool_text(item: dict) -> str:
    kind = item.get("type")
    if kind == "commandExecution":
        command = str(item.get("command") or "").splitlines()[0][:240]
        return f"Command completed: {command}"
    if kind == "fileChange":
        paths = [str(change.get("path") or "") for change in item.get("changes") or []]
        return "Files changed: " + ", ".join(p for p in paths if p)[:500]
    if kind == "mcpToolCall":
        return f"Tool completed: {item.get('server', '')}/{item.get('tool', '')}"
    if kind == "webSearch":
        return f"Web search completed: {str(item.get('query') or '')[:240]}"
    return f"{kind or 'tool'} completed"


def _artifact_language(path: Path) -> str:
    return {
        ".md": "markdown",
        ".markdown": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".csv": "csv",
        ".log": "text",
        ".txt": "text",
    }.get(path.suffix.lower(), "text")


def _extract_artifacts(task: Task, text: str) -> str:
    workspace_root = Path(task.data["cwd"]).resolve()
    cleaned = text
    for match in ARTIFACT_PATTERN.finditer(text):
        requested = Path(match.group(1)).expanduser()
        candidate = (requested if requested.is_absolute() else workspace_root / requested).resolve()
        try:
            relative = candidate.relative_to(workspace_root)
        except ValueError:
            task.event("error", "Codex refused an artifact path outside the approved workspace.")
            cleaned = cleaned.replace(match.group(0), "")
            continue
        if (
            not candidate.is_file()
            or candidate.suffix.lower() not in ARTIFACT_SUFFIXES
            or candidate.stat().st_size > ARTIFACT_MAX_BYTES
        ):
            task.event("error", "Codex could not open that artifact as a supported Odysseus document.")
            cleaned = cleaned.replace(match.group(0), "")
            continue
        content = candidate.read_text(encoding="utf-8")
        fingerprint = hashlib.sha256(
            f"{candidate}|{candidate.stat().st_mtime_ns}|{len(content)}".encode()
        ).hexdigest()
        task.event(
            "artifact",
            f"Opened {match.group(2) or candidate.stem} in Odysseus.",
            {
                "artifact_key": fingerprint,
                "title": (match.group(2) or candidate.stem)[:240],
                "source_path": str(relative),
                "language": _artifact_language(candidate),
                "content": content,
                "codex_thread_id": task.data.get("codex_thread_id"),
                "workspace": task.data.get("workspace"),
            },
        )
        cleaned = cleaned.replace(match.group(0), "")
    return cleaned.strip()


def _handle_server_message(task: Task, message: dict) -> None:
    if task.resolve_response(message):
        return
    method = message.get("method")
    params = message.get("params") or {}
    if "id" in message and method == "item/tool/requestUserInput":
        task.pending_request_id = message["id"]
        questions = params.get("questions") or []
        text = " ".join(str(q.get("question") or "") for q in questions).strip() or "Codex needs input."
        task.event("question", text, {"questions": questions})
        return
    if method != "item/completed":
        return
    item = params.get("item") or {}
    kind = item.get("type")
    if kind == "agentMessage":
        text = str(item.get("text") or "").strip()
        if not text:
            return
        phase = item.get("phase")
        if phase != "commentary":
            text = _extract_artifacts(task, text)
            if not text:
                text = "The requested document is open in Odysseus."
        task.event("progress" if phase == "commentary" else "result", text, {"phase": phase})
    elif kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch"}:
        task.event("tool_activity", _safe_tool_text(item), {"item_type": kind})


def _read_until(task: Task, wanted_id: int, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    assert task.proc and task.proc.stdout
    while time.time() < deadline:
        line = task.proc.stdout.readline()
        if not line:
            raise RuntimeError("codex_app_server_closed")
        message = json.loads(line)
        if message.get("id") == wanted_id:
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message.get("result") or {}
        _handle_server_message(task, message)
    raise TimeoutError(f"codex_response_timeout_{wanted_id}")


def _drain_stderr(proc: subprocess.Popen[str]) -> None:
    if proc.stderr:
        for _line in proc.stderr:
            pass


def _run_task(task: Task) -> None:
    try:
        task.proc = subprocess.Popen(
            [CODEX_BIN, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=_drain_stderr, args=(task.proc,), daemon=True).start()
        task.send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "jarvis-codex-bridge", "version": "1.0"}, "capabilities": {"experimentalApi": True}}})
        _read_until(task, 1)
        task.send({"method": "initialized", "params": {}})
        sandbox = "workspace-write" if task.data["permission_mode"] == "workspace_write" else "read-only"
        resume_thread_id = task.data.get("codex_thread_id")
        if resume_thread_id:
            task.send({
                "id": 2,
                "method": "thread/resume",
                "params": {
                    "threadId": resume_thread_id,
                    "cwd": task.data["cwd"],
                    "sandbox": sandbox,
                    "approvalPolicy": "never",
                    "developerInstructions": DEVELOPER_INSTRUCTIONS,
                },
            })
        else:
            task.send({
                "id": 2,
                "method": "thread/start",
                "params": {
                "cwd": task.data["cwd"],
                "runtimeWorkspaceRoots": [task.data["cwd"]],
                "sandbox": sandbox,
                "approvalPolicy": "never",
                "ephemeral": False,
                "developerInstructions": DEVELOPER_INSTRUCTIONS,
                },
            })
        started = _read_until(task, 2)
        thread_id = started["thread"]["id"]
        task.data["codex_thread_id"] = thread_id
        task.data["status"] = "running"
        task.save()
        task.event(
            "tool_activity",
            f"Codex task {thread_id} opened in {task.data['cwd']}",
            {"codex_thread_id": thread_id, "workspace": task.data["workspace"], "cwd": task.data["cwd"]},
        )
        task.send({"id": 3, "method": "turn/start", "params": {"threadId": thread_id, "input": [{"type": "text", "text": task.data["prompt"]}]}})
        turn = _read_until(task, 3)
        task.data["codex_turn_id"] = turn["turn"]["id"]
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
                task.event("error", "Codex task failed.", message.get("params") or {})
                break
    except Exception as exc:
        if task.data.get("status") not in TERMINAL:
            task.event("error", str(exc)[:1000])
    finally:
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
        if task.data.get("codex_thread_id") and task.data.get("codex_turn_id"):
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
    task.event("error", f"{WORKER_LABEL} exceeded the {MAX_TASK_RUNTIME // 60}-minute task limit. The task was stopped without making changes.")


def create_task(payload: dict) -> Task:
    workspace = str(payload.get("workspace") or "").strip()
    cwd = WORKSPACES.get(workspace)
    if not cwd:
        raise ValueError("unknown_workspace")
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt_required")
    permission = str(payload.get("permission_mode") or "read_only")
    if permission not in {"read_only", "workspace_write"}:
        raise ValueError("invalid_permission_mode")
    if permission == "workspace_write" and payload.get("approved") is not True:
        raise PermissionError("approval_required")
    codex_thread_id = str(payload.get("codex_thread_id") or "").strip() or None
    if codex_thread_id:
        try:
            codex_thread_id = str(uuid.UUID(codex_thread_id))
        except ValueError as exc:
            raise ValueError("invalid_codex_thread_id") from exc
    now = int(time.time())
    data = {
        "task_id": str(uuid.uuid4()),
        "worker": WORKER_ID,
        "session_id": str(payload.get("session_id") or ""),
        "workspace": workspace,
        "cwd": cwd,
        "permission_mode": permission,
        "prompt": prompt[:50000],
        "codex_thread_id": codex_thread_id,
        "status": "queued",
        "result": None,
        "error": None,
        "events": [],
        "created_at": now,
        "updated_at": now,
    }
    task = Task(data)
    with TASKS_LOCK:
        TASKS[task.task_id] = task
    task.save()
    task.event("accepted", f"{WORKER_LABEL} accepted the task.")
    threading.Thread(target=_run_task, args=(task,), daemon=True).start()
    threading.Thread(target=_watch_task, args=(task,), daemon=True).start()
    return task


def steer_task(task: Task, prompt: str, timeout: float = 10) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt_required")
    with task.lock:
        if task.data.get("status") != "running":
            raise RuntimeError("task_not_active")
        thread_id = task.data.get("codex_thread_id")
        turn_id = task.data.get("codex_turn_id")
        if not thread_id or not turn_id or not task.proc or task.proc.poll() is not None:
            raise RuntimeError("codex_turn_not_active")
    response = task.request(
        "turn/steer",
        {
            "threadId": thread_id,
            "expectedTurnId": turn_id,
            "input": [{"type": "text", "text": prompt[:50000]}],
        },
        timeout,
    )
    if response.get("error"):
        raise RuntimeError("task_not_steerable")
    result = response.get("result") or {}
    if result.get("turnId") != turn_id:
        raise RuntimeError("invalid_steer_response")
    return {
        "ok": True,
        "task_id": task.task_id,
        "codex_thread_id": thread_id,
        "codex_turn_id": turn_id,
    }


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisCodexBridge/1.1"

    def log_message(self, _fmt: str, *_args) -> None:
        return

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_000_000:
            raise ValueError("request_too_large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _task(self, task_id: str) -> Task | None:
        with TASKS_LOCK:
            return TASKS.get(task_id)

    def _check_auth(self) -> bool:
        if _authorized(self.headers.get("Authorization")):
            return True
        _json(self, 401, {"error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json(self, 200, {"ok": True, "worker": WORKER_ID, "app_server": True, "workspaces": sorted(WORKSPACES)})
            return
        if not self._check_auth():
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
        after = int((parse_qs(parsed.query).get("after") or ["-1"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        next_seq = after + 1
        try:
            while True:
                with task.changed:
                    events = task.data.get("events", [])
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
        if not self._check_auth():
            return
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        try:
            payload = self._body()
            if parts == ["v1", "tasks"]:
                task = create_task(payload)
                _json(self, 202, task.data)
                return
            if len(parts) != 4 or parts[:2] != ["v1", "tasks"]:
                _json(self, 404, {"error": "not_found"})
                return
            task = self._task(parts[2])
            if not task:
                _json(self, 404, {"error": "task_not_found"})
                return
            action = parts[3]
            if action == "steer":
                try:
                    result = steer_task(task, str(payload.get("prompt") or ""))
                except TimeoutError:
                    _json(self, 409, {"error": "steer_timeout"})
                    return
                except RuntimeError as exc:
                    _json(self, 409, {"error": str(exc)})
                    return
                _json(self, 200, result)
                return
            if action == "cancel":
                if task.data.get("status") not in TERMINAL:
                    if task.data.get("codex_thread_id") and task.data.get("codex_turn_id"):
                        task.send({"id": 90, "method": "turn/interrupt", "params": {"threadId": task.data["codex_thread_id"], "turnId": task.data["codex_turn_id"]}})
                    task.event("cancelled", "Codex task cancelled.")
                _json(self, 200, task.data)
                return
            if action == "reply":
                if task.pending_request_id is None:
                    _json(self, 409, {"error": "task_not_waiting_for_input"})
                    return
                answers = payload.get("answers") or {}
                normalized = {str(key): {"answers": value if isinstance(value, list) else [str(value)]} for key, value in answers.items()}
                task.send({"id": task.pending_request_id, "result": {"answers": normalized}})
                task.pending_request_id = None
                task.data["status"] = "running"
                task.save()
                _json(self, 200, task.data)
                return
            _json(self, 404, {"error": "not_found"})
        except PermissionError as exc:
            _json(self, 403, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            _json(self, 400, {"error": str(exc)})
        except Exception as exc:
            _json(self, 500, {"error": str(exc)[:500]})


def self_check() -> None:
    assert WORKER_ID in {"pc-codex", "vps-codex"}
    assert all(Path(path).is_absolute() for path in WORKSPACES.values())
    assert _safe_tool_text({"type": "webSearch", "query": "test"}) == "Web search completed: test"
    assert MAX_TASK_RUNTIME >= 60
    assert ARTIFACT_PATTERN.search('[[ODYSSEUS_ARTIFACT path="notes/Mark 5.md" title="Mark 5"]]')
    assert _configured_hosts("127.0.0.1,100.64.0.1,127.0.0.1") == ("127.0.0.1", "100.64.0.1")


if __name__ == "__main__":
    self_check()
    _load_tasks()
    servers = [ThreadingHTTPServer((host, PORT), Handler) for host in _configured_hosts()]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()
