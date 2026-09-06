#!/usr/bin/env python3
from __future__ import annotations

import hmac
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from core.atomic_io import atomic_write_json
except ModuleNotFoundError:
    # The PC/VPS bridges are deployed as small standalone bundles. Their
    # installer copies core/atomic_io.py beside this script so the exact same
    # durability helper remains available without the full Pandamonium package.
    from atomic_io import atomic_write_json

HOST = os.getenv("JARVIS_CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_CODEX_BRIDGE_PORT", "8040"))
TOKEN_FILE = Path(os.getenv("JARVIS_CODEX_BRIDGE_TOKEN_FILE", str(Path.home() / ".config/jarvis/agent-bridge-token")))
STATE_DIR = Path(os.getenv("JARVIS_CODEX_BRIDGE_STATE_DIR", str(Path.home() / ".local/share/jarvis/pc-codex-bridge/tasks")))
CODEX_BIN = os.getenv("JARVIS_CODEX_BIN", "codex")
MAX_TASK_RUNTIME = int(os.getenv("JARVIS_CODEX_MAX_TASK_SECONDS", "480"))
WORKER_ID = os.getenv("JARVIS_CODEX_WORKER_ID", "pc-codex").strip() or "pc-codex"
WORKER_LABEL = " ".join(os.getenv("JARVIS_CODEX_WORKER_LABEL", WORKER_ID).split())[:80] or WORKER_ID
BRIDGE_PROTOCOL_VERSION = "pandamonium.codex-bridge.v2"
CODEX_MODEL = os.getenv(
    "JARVIS_CODEX_MODEL",
    "gpt-5.6-terra" if WORKER_ID == "pc-codex" else "",
).strip()
CODEX_REASONING_EFFORT = os.getenv(
    "JARVIS_CODEX_REASONING_EFFORT",
    "high" if WORKER_ID == "pc-codex" else "",
).strip()
TERMINAL = {"completed", "failed", "cancelled"}
WORKSPACE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _workspace_configuration(raw: object) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(raw, dict):
        raise RuntimeError("invalid_workspace_configuration")
    paths: dict[str, str] = {}
    names: dict[str, str] = {}
    for workspace, value in raw.items():
        if not isinstance(workspace, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace):
            raise RuntimeError("invalid_workspace_configuration")
        if isinstance(value, str):
            path = value
            display_name = workspace.replace("-", " ").title()
        elif isinstance(value, dict) and set(value) <= {"path", "display_name"}:
            path = value.get("path")
            display_name = value.get("display_name") or workspace.replace("-", " ").title()
        else:
            raise RuntimeError("invalid_workspace_configuration")
        if not isinstance(path, str) or not Path(path).expanduser().is_absolute():
            raise RuntimeError("invalid_workspace_configuration")
        paths[workspace] = str(Path(path).expanduser().resolve())
        names[workspace] = " ".join(str(display_name).split())[:120] or workspace
    return paths, names


try:
    WORKSPACES, WORKSPACE_NAMES = _workspace_configuration(
        json.loads(os.getenv("JARVIS_CODEX_WORKSPACES_JSON", "{}"))
    )
except json.JSONDecodeError as exc:
    raise RuntimeError("invalid_workspace_configuration") from exc

DEVELOPER_INSTRUCTIONS = os.getenv("JARVIS_CODEX_DEVELOPER_INSTRUCTIONS", "") or """You are PC Codex working for the authenticated operator through Pandamonium.
Give short, useful commentary updates at meaningful milestones while you work.
Do not narrate raw commands or internal reasoning. End with a standalone result.
Only after a subtask is complete and verified by tool evidence, you may emit one commentary update as:
[[ODYSSEUS_MILESTONE]] <one completed-subtask update>
Do not use that marker for plans, activity, commands, estimates, or the final result.
Respect the selected sandbox. Ask a focused question only when genuinely blocked.
When the operator asks to open, show, or put a text document in Pandamonium, finish with exactly one marker on its own line:
[[ODYSSEUS_ARTIFACT path="path inside the active workspace" title="Human title"]]
Only emit that marker for a file you verified exists inside the active workspace.
"""
MILESTONE_MARKER = "[[ODYSSEUS_MILESTONE]]"
ARTIFACT_PATTERN = re.compile(
    r'\[\[ODYSSEUS_ARTIFACT\s+path="([^"]+)"(?:\s+title="([^"]+)")?\s*\]\]'
)
ARTIFACT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv", ".log"}
ARTIFACT_MAX_BYTES = 2_000_000


def _private_worker_mutations_enabled() -> bool:
    return os.getenv("JARVIS_CODEX_PRIVATE_WORKER_MUTATIONS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _task_execution_enabled() -> bool:
    return os.getenv("JARVIS_CODEX_EXECUTION_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _validate_task_permission(permission_mode: str, approved: bool) -> None:
    if permission_mode == "read_only" and not approved:
        return
    if permission_mode == "workspace_write" and _private_worker_mutations_enabled():
        if not approved:
            raise PermissionError("approval_required")
        return
    raise PermissionError("public_tasks_read_only")


def _approved_workspace_write(task: "Task") -> bool:
    return (
        _private_worker_mutations_enabled()
        and task.data.get("permission_mode") == "workspace_write"
        and task.data.get("approved") is True
    )


def _codex_command() -> list[str]:
    command = [CODEX_BIN]
    if CODEX_MODEL:
        command += ["-c", f'model="{CODEX_MODEL}"']
    if CODEX_REASONING_EFFORT:
        command += ["-c", f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"']
    return command + ["app-server", "--stdio"]


def _task_developer_instructions(task: "Task") -> str:
    instructions = DEVELOPER_INSTRUCTIONS
    if (
        task.data.get("workspace") == "discord-mod"
        and str(task.data.get("session_id") or "").startswith("discord:")
    ):
        instructions = re.sub(
            r"^You are PC Codex working for Jarvis and Leo\.\s*",
            "",
            instructions,
            count=1,
        )
        instructions = (
            "You are JARVIS, Leo's persistent AI assistant and Discord server operations "
            "partner. Codex is your execution engine; do not describe JARVIS as separate "
            f"from yourself in Discord.\n{instructions.lstrip()}"
        )
    source_root = task.data.get("source_root")
    if not source_root or source_root == task.data["cwd"]:
        return instructions
    source_rule = (
        "You may modify it only within this explicitly approved workspace-write task."
        if _approved_workspace_write(task)
        else "Treat it as read-only."
    )
    return (
        f"{instructions.rstrip()}\n\n"
        f"The selected source workspace is {source_root}. Read it using absolute paths. "
        f"{source_rule}\n"
        f"Your dedicated Jarvis interaction workspace is {task.data['cwd']}. "
        "Keep generated reports and generated Pandamonium artifacts there so Jarvis tasks do not clutter the source project. "
        "Existing verified text documents in the selected source workspace may be emitted directly as Pandamonium artifacts.\n"
    )


def _runtime_workspace_roots(task: "Task") -> list[str]:
    roots = [task.data["cwd"]]
    source_root = task.data.get("source_root")
    if _approved_workspace_write(task) and source_root not in roots:
        roots.append(source_root)
    return roots


def _configured_hosts(value: str | None = None) -> tuple[str, ...]:
    configured = value if value is not None else os.getenv("JARVIS_CODEX_BRIDGE_HOSTS", HOST)
    hosts = tuple(dict.fromkeys(part.strip() for part in configured.split(",") if part.strip()))
    if not hosts or any(host in {"0.0.0.0", "::"} for host in hosts):
        raise RuntimeError("bridge_hosts_must_be_explicit")
    return hosts


def _app_server_call(method: str, params: dict, timeout: float = 20) -> dict:
    """Make one bounded request through Codex's supported stdio app-server."""
    proc = subprocess.Popen(
        _codex_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    messages: queue.Queue[str | None] = queue.Queue()

    def _read_stdout() -> None:
        assert proc.stdout
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    threading.Thread(target=_read_stdout, daemon=True).start()
    threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()

    def _send(message: dict) -> None:
        if not proc.stdin:
            raise RuntimeError("codex_app_server_closed")
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def _receive(request_id: int) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("codex_app_server_timeout")
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("codex_app_server_timeout") from exc
            if line is None:
                raise RuntimeError("codex_app_server_closed")
            message = json.loads(line)
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise RuntimeError("codex_app_server_request_failed")
            return message.get("result") or {}

    try:
        _send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "jarvis-codex-catalog", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        })
        _receive(1)
        _send({"method": "initialized", "params": {}})
        _send({"id": 2, "method": method, "params": params})
        return _receive(2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def _workspace_root(workspace: str) -> Path:
    configured = WORKSPACES.get(workspace)
    if not configured:
        raise ValueError("project_not_allowlisted")
    root = Path(configured).resolve()
    if not root.is_dir():
        raise ValueError("project_root_unavailable")
    return root


def _safe_thread(thread: dict, workspace: str, root: Path) -> dict | None:
    try:
        if Path(str(thread.get("cwd") or "")).resolve() != root:
            return None
    except (OSError, ValueError):
        return None
    thread_id = str(thread.get("id") or "").strip()
    if not thread_id or len(thread_id) > 100:
        return None
    status_value = thread.get("status")
    status = status_value.get("type") if isinstance(status_value, dict) else status_value
    status = re.sub(r"[^a-zA-Z0-9_-]", "", str(status or "unknown"))[:40] or "unknown"
    title = " ".join(str(thread.get("name") or "Untitled task").split())[:200] or "Untitled task"
    return {
        "task_id": thread_id,
        "project_id": workspace,
        "title": title,
        "status": status,
        "created_at": int(thread.get("createdAt") or 0),
        "updated_at": int(thread.get("updatedAt") or 0),
    }


def catalog_tasks(
    workspace: str,
    *,
    query: str = "",
    cursor: str | None = None,
    limit: int = 50,
) -> dict:
    root = _workspace_root(workspace)
    limit = max(1, min(int(limit), 100))
    query = " ".join(str(query or "").split())[:200]
    params: dict = {
        "cwd": str(root),
        "limit": limit,
        "useStateDbOnly": True,
    }
    if query:
        params["searchTerm"] = query
    if cursor:
        params["cursor"] = str(cursor)[:2000]
    result = _app_server_call("thread/list", params)
    tasks = [
        safe
        for item in result.get("data") or []
        if isinstance(item, dict)
        for safe in [_safe_thread(item, workspace, root)]
        if safe is not None
    ]
    return {
        "project_id": workspace,
        "items": tasks,
        "next_cursor": str(result.get("nextCursor") or "") or None,
    }


def catalog_projects(*, query: str = "", cursor: str | None = None, limit: int = 20) -> dict:
    query = " ".join(str(query or "").split()).casefold()[:200]
    limit = max(1, min(int(limit), 50))
    projects = [
        workspace for workspace in sorted(WORKSPACES)
        if not query or query in workspace.casefold() or query in WORKSPACE_NAMES[workspace].casefold()
    ]
    try:
        offset = max(0, int(cursor or 0))
    except ValueError as exc:
        raise ValueError("invalid_catalog_cursor") from exc
    selected = projects[offset:offset + limit]
    items = []
    for workspace in selected:
        root = Path(WORKSPACES[workspace]).resolve()
        project = {
            "project_id": workspace,
            "display_name": WORKSPACE_NAMES[workspace],
            "approved_root": f"workspace:{workspace}",
            "availability": "available" if root.is_dir() else "unavailable",
        }
        if not root.is_dir():
            project["reason"] = "project_root_unavailable"
        items.append(project)
    next_offset = offset + len(selected)
    return {
        "items": items,
        "next_cursor": str(next_offset) if next_offset < len(projects) else None,
    }


def _resume_after(task: "Task", after: int, last_event_id: str) -> int:
    if not last_event_id:
        return after
    with task.lock:
        matched = next((
            event for event in task.data.get("events", [])
            if str(event.get("event_id") or "") == last_event_id
        ), None)
    return int(matched.get("seq", after)) if matched is not None else after


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
        persisted = dict(self.data)
        persisted.pop("prompt", None)
        atomic_write_json(str(path), persisted, indent=2)

    def event(self, event_type: str, text: str, metadata: dict | None = None) -> dict:
        with self.changed:
            events = self.data.setdefault("events", [])
            if self.data.get("status") in TERMINAL and event_type in {"result", "error", "cancelled"}:
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


def _milestone_commentary(text: str) -> tuple[str, bool]:
    value = text.strip()
    if not value.startswith(MILESTONE_MARKER):
        return value, False
    remainder = value[len(MILESTONE_MARKER):]
    if remainder and not remainder[0].isspace():
        return value, False
    value = remainder.strip()
    return value, bool(value)


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
    roots = [Path(task.data["cwd"]).resolve()]
    source_root = task.data.get("source_root")
    if source_root and Path(source_root).resolve() not in roots:
        roots.append(Path(source_root).resolve())
    cleaned = text
    for match in ARTIFACT_PATTERN.finditer(text):
        requested = Path(match.group(1)).expanduser()
        candidates = (
            [requested.resolve()]
            if requested.is_absolute()
            else [(root / requested).resolve() for root in roots]
        )
        contained = []
        for candidate in candidates:
            for root in roots:
                try:
                    contained.append((candidate, candidate.relative_to(root)))
                    break
                except ValueError:
                    continue
        if not contained:
            task.event("error", "Codex refused an artifact path outside the approved workspace.")
            cleaned = cleaned.replace(match.group(0), "")
            continue
        valid = next((
            item for item in contained
            if item[0].is_file()
            and item[0].suffix.lower() in ARTIFACT_SUFFIXES
            and item[0].stat().st_size <= ARTIFACT_MAX_BYTES
        ), None)
        if not valid:
            task.event("error", "Codex could not open that artifact as a supported Pandamonium document.")
            cleaned = cleaned.replace(match.group(0), "")
            continue
        candidate, relative = valid
        content = candidate.read_text(encoding="utf-8")
        fingerprint = hashlib.sha256(
            f"{candidate}|{candidate.stat().st_mtime_ns}|{len(content)}".encode()
        ).hexdigest()
        task.event(
            "artifact",
            f"Opened {match.group(2) or candidate.stem} in Pandamonium.",
            {
                "artifact_key": fingerprint,
                "title": (match.group(2) or candidate.stem)[:240],
                "source_path": str(relative),
                "language": _artifact_language(candidate),
                "content": content,
                "codex_thread_id": task.data.get("codex_thread_id"),
                "workspace": task.data.get("workspace"),
                "review_mode": (
                    "reversible_edit"
                    if _approved_workspace_write(task)
                    else "read_only_citation"
                ),
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
        metadata = {"phase": phase}
        if phase == "commentary":
            text, milestone = _milestone_commentary(text)
            if not milestone:
                return
            metadata["milestone"] = True
        else:
            text = _extract_artifacts(task, text)
            if not text:
                text = "The requested document is open in Pandamonium."
        task.event("progress" if phase == "commentary" else "result", text, metadata)
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


def _validate_resume_thread(task: Task, thread_id: str) -> None:
    """Bind resume to the exact allowlisted root before starting a new turn."""
    task.send({
        "id": 19,
        "method": "thread/read",
        "params": {"threadId": thread_id, "includeTurns": False},
    })
    try:
        result = _read_until(task, 19)
    except Exception as exc:
        raise RuntimeError("codex_thread_unavailable") from exc
    thread = result.get("thread") if isinstance(result, dict) else None
    if not isinstance(thread, dict) or str(thread.get("id") or "") != thread_id:
        raise RuntimeError("codex_thread_identity_mismatch")
    try:
        thread_root = Path(str(thread.get("cwd") or "")).expanduser().resolve(strict=True)
        approved_root = Path(task.data["source_root"]).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError("codex_thread_project_unavailable") from exc
    if thread_root != approved_root:
        raise RuntimeError("codex_thread_project_mismatch")


def _run_task(task: Task) -> None:
    try:
        _validate_task_permission(
            str(task.data.get("permission_mode") or "read_only"),
            task.data.get("approved") is True,
        )
        task.proc = subprocess.Popen(
            _codex_command(),
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
        sandbox = "workspace-write" if _approved_workspace_write(task) else "read-only"
        developer_instructions = _task_developer_instructions(task)
        resume_thread_id = task.data.get("codex_thread_id")
        if resume_thread_id:
            _validate_resume_thread(task, resume_thread_id)
            task.send({
                "id": 2,
                "method": "thread/resume",
                "params": {
                    "threadId": resume_thread_id,
                    "cwd": task.data["cwd"],
                    "runtimeWorkspaceRoots": _runtime_workspace_roots(task),
                    "sandbox": sandbox,
                    "approvalPolicy": "never",
                    "developerInstructions": developer_instructions,
                },
            })
        else:
            task.send({
                "id": 2,
                "method": "thread/start",
                "params": {
                "cwd": task.data["cwd"],
                "runtimeWorkspaceRoots": _runtime_workspace_roots(task),
                "sandbox": sandbox,
                "approvalPolicy": "never",
                "ephemeral": False,
                "developerInstructions": developer_instructions,
                },
            })
        started = _read_until(task, 2)
        thread_id = started["thread"]["id"]
        if task.data.get("thread_title"):
            task.send({
                "id": 20,
                "method": "thread/name/set",
                "params": {"threadId": thread_id, "name": task.data["thread_title"]},
            })
            _read_until(task, 20)
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
        while True:
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
    if not _task_execution_enabled():
        raise RuntimeError("codex_task_execution_disabled")
    workspace = str(payload.get("workspace") or "").strip()
    source_root = WORKSPACES.get(workspace)
    if not source_root:
        raise ValueError("unknown_workspace")
    source_path = Path(source_root).expanduser().resolve()
    if not source_path.is_dir():
        raise ValueError("workspace_not_found")
    source_root = str(source_path)
    cwd = source_root
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt_required")
    permission = str(payload.get("permission_mode") or "read_only")
    approved = payload.get("approved") is True
    _validate_task_permission(permission, approved)
    codex_thread_id = str(payload.get("codex_thread_id") or "").strip() or None
    if codex_thread_id:
        try:
            codex_thread_id = str(uuid.UUID(codex_thread_id))
        except ValueError as exc:
            raise ValueError("invalid_codex_thread_id") from exc
    now = int(time.time())
    thread_title = " ".join(str(payload.get("thread_title") or "").split())[:200] or None
    request_id = " ".join(str(payload.get("request_id") or "").split())[:200] or None
    data = {
        "task_id": str(uuid.uuid4()),
        "worker": WORKER_ID,
        "session_id": str(payload.get("session_id") or ""),
        "workspace": workspace,
        "cwd": cwd,
        "source_root": source_root,
        "permission_mode": permission,
        "approved": approved,
        "prompt": prompt[:50000],
        "thread_title": thread_title,
        "request_id": request_id,
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
    server_version = "JarvisCodexBridge/1.2"

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
            _json(self, 200, {
                "ok": True,
                "worker": WORKER_ID,
                "app_server": True,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "features": {
                    "project_catalog": True,
                    "task_control": True,
                },
                "installation": {
                    "display_name": WORKER_LABEL,
                    "capabilities": ["codex"],
                },
                "task_execution_enabled": _task_execution_enabled(),
                "workspaces": sorted(WORKSPACES),
            })
            return
        if not self._check_auth():
            return
        parts = parsed.path.strip("/").split("/")
        if parts == ["v1", "catalog", "projects"]:
            params = parse_qs(parsed.query)
            try:
                payload = catalog_projects(
                    query=str((params.get("query") or [""])[0]),
                    cursor=str((params.get("cursor") or [""])[0]) or None,
                    limit=int((params.get("limit") or ["20"])[0]),
                )
                _json(self, 200, payload)
            except ValueError as exc:
                _json(self, 400, {"error": str(exc)})
            except Exception:
                _json(self, 503, {"error": "codex_catalog_unavailable"})
            return
        if len(parts) == 5 and parts[:3] == ["v1", "catalog", "projects"] and parts[4] == "tasks":
            params = parse_qs(parsed.query)
            try:
                payload = catalog_tasks(
                    parts[3],
                    query=str((params.get("query") or [""])[0]),
                    cursor=str((params.get("cursor") or [""])[0]) or None,
                    limit=int((params.get("limit") or ["50"])[0]),
                )
                _json(self, 200, payload)
            except ValueError as exc:
                status = 404 if str(exc) in {"project_not_allowlisted", "project_root_unavailable"} else 400
                _json(self, status, {"error": str(exc)})
            except Exception:
                _json(self, 503, {"error": "codex_catalog_unavailable"})
            return
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
        last_event_id = str(self.headers.get("Last-Event-ID") or "").strip()
        after = _resume_after(task, after, last_event_id)
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
        except RuntimeError as exc:
            status = 503 if str(exc) == "codex_task_execution_disabled" else 500
            _json(self, status, {"error": str(exc)[:500]})
        except Exception as exc:
            _json(self, 500, {"error": str(exc)[:500]})


def self_check() -> None:
    assert WORKER_ID in {"pc-codex", "vps-codex"}
    assert all(Path(path).is_absolute() for path in WORKSPACES.values())
    assert _codex_command()[-2:] == ["app-server", "--stdio"]
    assert _safe_tool_text({"type": "webSearch", "query": "test"}) == "Web search completed: test"
    assert MAX_TASK_RUNTIME >= 60
    assert ARTIFACT_PATTERN.search('[[ODYSSEUS_ARTIFACT path="notes/Mark 5.md" title="Mark 5"]]')
    assert _configured_hosts("127.0.0.1,100.64.0.1,127.0.0.1") == ("127.0.0.1", "100.64.0.1")
    assert _workspace_configuration({}) == ({}, {})


if __name__ == "__main__":
    self_check()
    _load_tasks()
    servers = [ThreadingHTTPServer((host, PORT), Handler) for host in _configured_hosts()]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    servers[0].serve_forever()
