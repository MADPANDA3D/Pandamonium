"""Durable, owner-scoped broker for fixed read-only workers."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from core.atomic_io import atomic_write_json
from src.agent_worker_adapters import (
    WORKER_IDS,
    WorkerUnavailable,
    adapters,
    require_read_only,
    worker_catalog,
)
from src.constants import DATA_DIR

TASKS_FILE = Path(DATA_DIR) / "voice_orb_worker_tasks.json"
TERMINAL = {"completed", "failed", "cancelled", "blocked"}
TERMINAL_EVENTS = {"result", "error", "cancelled"}
STREAM_RETRY_LIMIT = 2


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


RECONCILE_TIMEOUT_SECONDS = _float_env("ODYSSEUS_WORKER_RECONCILE_TIMEOUT_SECONDS", 600, 0)
RECONCILE_POLL_SECONDS = _float_env("ODYSSEUS_WORKER_RECONCILE_POLL_SECONDS", 2, 0.1)

_LOCK = threading.RLock()
_MIRRORS: dict[str, asyncio.Task] = {}
_SESSION_MANAGER = None


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"tasks": {}, "bindings": {}}
    if not isinstance(value, dict):
        return {"tasks": {}, "bindings": {}}
    value.setdefault("tasks", {})
    value.setdefault("bindings", {})
    return value


def _write_state(state: dict[str, Any]) -> None:
    atomic_write_json(str(TASKS_FILE), state, indent=2)
    try:
        TASKS_FILE.chmod(0o600)
    except OSError:
        pass


def configure(session_manager) -> None:
    """Attach owner lookup and backfill only tasks with an owned linked chat."""
    global _SESSION_MANAGER
    _SESSION_MANAGER = session_manager
    changed = False
    with _LOCK:
        state = _read_state()
        for task in (state.get("tasks") or {}).values():
            if not isinstance(task, dict) or task.get("owner") or not task.get("session_id"):
                continue
            try:
                owner = str(getattr(session_manager.get_session(task["session_id"]), "owner", "") or "").strip()
            except Exception:
                owner = ""
            if owner:
                task["owner"] = owner
                changed = True
        if changed:
            _write_state(state)


def _session_for_owner(session_id: str, owner: str):
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    if _SESSION_MANAGER is None:
        raise RuntimeError("session_manager_unavailable")
    try:
        session = _SESSION_MANAGER.get_session(session_id)
    except Exception as exc:
        raise KeyError(session_id) from exc
    if str(getattr(session, "owner", "") or "") != identity:
        raise PermissionError("session_owner_mismatch")
    return session


def get_task(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        task = (_read_state().get("tasks") or {}).get(task_id)
        return dict(task) if isinstance(task, dict) else None


def require_task_owner(task_id: str, owner: str | None) -> dict[str, Any]:
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    task = get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if str(task.get("owner") or "") != identity:
        raise PermissionError("task_owner_mismatch")
    _session_for_owner(str(task.get("session_id") or ""), identity)
    return task


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        key: task.get(key)
        for key in (
            "task_id",
            "worker",
            "session_id",
            "workspace",
            "permission_mode",
            "status",
            "result",
            "error",
            "codex_thread_id",
            "events",
            "created_at",
            "updated_at",
        )
    }


def _binding_key(owner: str, session_id: str, worker: str, workspace: str) -> str:
    return f"{owner}:{session_id}:{worker}:{workspace}"


def _save_binding(task: dict[str, Any], **updates: Any) -> None:
    values = {key: value for key, value in updates.items() if value}
    if not values:
        return
    with _LOCK:
        state = _read_state()
        key = _binding_key(task["owner"], task["session_id"], task["worker"], task["workspace"])
        binding = state.setdefault("bindings", {}).setdefault(key, {})
        binding.update(values)
        binding["updated_at"] = int(time.time())
        _write_state(state)


def _binding(owner: str, session_id: str, worker: str, workspace: str) -> dict[str, Any]:
    with _LOCK:
        value = (_read_state().get("bindings") or {}).get(
            _binding_key(owner, session_id, worker, workspace)
        )
        return dict(value) if isinstance(value, dict) else {}


def _save_task(task: dict[str, Any]) -> None:
    persisted = dict(task)
    persisted.pop("prompt", None)
    with _LOCK:
        state = _read_state()
        state.setdefault("tasks", {})[persisted["task_id"]] = persisted
        _write_state(state)


def _append_event(task_id: str, incoming: dict[str, Any]) -> bool:
    with _LOCK:
        state = _read_state()
        task = (state.get("tasks") or {}).get(task_id)
        if not isinstance(task, dict) or task.get("status") in TERMINAL:
            return False
        events = task.setdefault("events", [])
        event_id = str(incoming.get("event_id") or uuid.uuid4())[:200]
        if any(str(event.get("event_id") or "") == event_id for event in events):
            return False
        event_type = str(incoming.get("type") or "")
        if event_type not in {
            "accepted",
            "progress",
            "tool_activity",
            "question",
            "approval_required",
            "result",
            "error",
            "cancelled",
        }:
            return False
        event = {
            "seq": int(events[-1].get("seq", -1)) + 1 if events else 0,
            "event_id": event_id,
            "task_id": task_id,
            "worker": task["worker"],
            "type": event_type,
            "text": str(incoming.get("text") or "")[:12_000],
            "metadata": dict(incoming.get("metadata") or {}),
            "created_at": int(time.time()),
        }
        events.append(event)
        if event_type == "result":
            task.update(status="completed", result=event["text"], error=None)
        elif event_type == "error":
            task.update(status="failed", error=event["text"])
        elif event_type == "cancelled":
            task["status"] = "cancelled"
        elif event_type == "question":
            task["status"] = "waiting"
        elif event_type == "approval_required":
            task["status"] = "waiting_approval"
        elif event_type in {"accepted", "progress", "tool_activity"}:
            task["status"] = "running"
        thread_id = str((event.get("metadata") or {}).get("codex_thread_id") or "")
        if thread_id:
            task["codex_thread_id"] = thread_id
        task["updated_at"] = event["created_at"]
        state["tasks"][task_id] = task
        _write_state(state)
    if thread_id:
        _save_binding(task, codex_thread_id=thread_id)
    return True


def task_events(task_id: str, after: int = -1) -> list[dict[str, Any]]:
    task = get_task(task_id) or {}
    return [event for event in task.get("events") or [] if int(event.get("seq", -1)) > after]


def list_tasks(session_id: str, owner: str, *, active_only: bool = False) -> list[dict[str, Any]]:
    _session_for_owner(session_id, owner)
    with _LOCK:
        tasks = [
            task
            for task in (_read_state().get("tasks") or {}).values()
            if isinstance(task, dict)
            and task.get("session_id") == session_id
            and task.get("owner") == owner
            and (not active_only or task.get("status") not in TERMINAL)
        ]
    tasks.sort(key=lambda task: (task.get("updated_at", 0), task.get("created_at", 0)), reverse=True)
    return [_public_task(task) for task in tasks[:100]]


def find_active_task(session_id: str, worker: str, owner: str) -> dict[str, Any] | None:
    if worker not in WORKER_IDS:
        raise ValueError("unknown_worker")
    tasks = list_tasks(session_id, owner, active_only=True)
    return next((task for task in tasks if task.get("worker") == worker), None)


async def worker_statuses() -> dict[str, dict[str, Any]]:
    registry = adapters()
    catalog = worker_catalog(registry)
    health_rows = await asyncio.gather(*(adapter.health() for adapter in registry.values()))
    for (worker, adapter), health in zip(registry.items(), health_rows):
        state = str(health.get("state") or "unreachable")
        connection = {"state": state}
        if health.get("reason"):
            connection["reason"] = str(health["reason"])
        catalog[worker].update(
            configured=bool(adapter.enabled),
            ready=bool(adapter.enabled and state == "connected"),
            capabilities=list(health.get("capabilities") or catalog[worker]["capabilities"]),
            workspaces=list(health.get("workspaces") or catalog[worker]["workspaces"]),
            connection=connection,
        )
    return catalog


async def start_task(
    worker: str,
    session_id: str,
    workspace: str,
    prompt: str,
    *,
    permission_mode: str = "read_only",
    approved: bool = False,
    owner: str,
) -> dict[str, Any]:
    identity = str(owner or "").strip()
    _session_for_owner(session_id, identity)
    if worker not in WORKER_IDS:
        raise ValueError("unknown_worker")
    require_read_only(permission_mode, approved)
    registry = adapters()
    adapter = registry[worker]
    if not adapter.enabled:
        raise WorkerUnavailable("worker_not_configured")
    binding = _binding(identity, session_id, worker, workspace)
    now = int(time.time())
    task = {
        "task_id": str(uuid.uuid4()),
        "remote_task_id": None,
        "worker": worker,
        "session_id": session_id,
        "workspace": workspace,
        "permission_mode": "read_only",
        "approved": False,
        "codex_thread_id": binding.get("codex_thread_id"),
        "worker_session_key": binding.get("worker_session_key"),
        "status": "queued",
        "result": None,
        "error": None,
        "owner": identity,
        "events": [],
        "created_at": now,
        "updated_at": now,
    }
    _save_task(task)
    try:
        remote = await adapter.start({**task, "prompt": prompt})
    except Exception:
        _append_event(
            task["task_id"],
            {
                "event_id": f"start-failed:{task['task_id']}",
                "type": "error",
                "text": "The configured worker could not accept the task.",
                "metadata": {"source": "worker_start"},
            },
        )
        raise
    task.update(remote)
    _save_task(task)
    _append_event(
        task["task_id"],
        {
            "event_id": f"accepted:{task['task_id']}",
            "type": "accepted",
            "text": f"{adapter.label} accepted the task.",
            "metadata": {},
        },
    )
    if task.get("codex_thread_id") or task.get("worker_session_key"):
        _save_binding(
            task,
            codex_thread_id=task.get("codex_thread_id"),
            worker_session_key=task.get("worker_session_key"),
        )
    ensure_mirror(task["task_id"])
    return _public_task(get_task(task["task_id"]) or task)


def _terminal_event(task: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any] | None:
    status = str(remote.get("status") or "")
    if status not in {"completed", "failed", "cancelled"}:
        return None
    event_type = {"completed": "result", "failed": "error", "cancelled": "cancelled"}[status]
    if status == "completed":
        text = str(remote.get("output") or remote.get("result") or "Worker completed the task.")
    elif status == "cancelled":
        text = "Worker cancelled the task."
    else:
        text = "Worker could not complete the task."
    return {
        "event_id": f"reconciled:{task['task_id']}:{status}",
        "type": event_type,
        "text": text,
        "metadata": {"reconciled": True},
    }


async def refresh_task(
    task_id: str,
    *,
    owner: str,
    ensure_stream: bool = True,
) -> dict[str, Any]:
    task = require_task_owner(task_id, owner)
    if task.get("status") not in TERMINAL:
        try:
            remote = await adapters()[task["worker"]].status(task)
            event = _terminal_event(task, remote)
            if event:
                _append_event(task_id, event)
        except Exception:
            pass
    if ensure_stream:
        ensure_mirror(task_id)
    return _public_task(get_task(task_id) or task)


async def _mirror(task_id: str) -> None:
    try:
        for attempt in range(STREAM_RETRY_LIMIT + 1):
            task = get_task(task_id)
            if not task or task.get("status") in TERMINAL:
                return
            adapter = adapters()[task["worker"]]
            try:
                async for event in adapter.events(task):
                    if (get_task(task_id) or {}).get("status") in TERMINAL:
                        return
                    _append_event(task_id, event)
                if (get_task(task_id) or {}).get("status") in TERMINAL:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            if attempt < STREAM_RETRY_LIMIT:
                await asyncio.sleep(0)

        deadline = time.monotonic() + RECONCILE_TIMEOUT_SECONDS
        while True:
            task = get_task(task_id)
            if not task or task.get("status") in TERMINAL:
                return
            await refresh_task(task_id, owner=task.get("owner", ""), ensure_stream=False)
            task = get_task(task_id)
            if not task or task.get("status") in TERMINAL:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(RECONCILE_POLL_SECONDS, remaining))

        _append_event(
            task_id,
            {
                "event_id": f"stream-timeout:{task_id}",
                "type": "error",
                "text": "Worker status could not be confirmed after the event stream ended.",
                "metadata": {"source": "worker_stream", "reconciliation_timeout": True},
            },
        )
    finally:
        _MIRRORS.pop(task_id, None)


def ensure_mirror(task_id: str) -> None:
    task = get_task(task_id)
    if not task or task.get("status") in TERMINAL or task_id in _MIRRORS:
        return
    _MIRRORS[task_id] = asyncio.create_task(_mirror(task_id))


async def task_action(
    task_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    owner: str,
) -> dict[str, Any]:
    task = require_task_owner(task_id, owner)
    if task.get("status") in TERMINAL:
        return _public_task(task)
    adapter = adapters()[task["worker"]]
    payload = payload or {}
    if action == "steer":
        await adapter.steer(task, payload)
    elif action == "reply":
        await adapter.reply(task, payload)
    elif action == "approval":
        if payload.get("choice") != "deny":
            raise PermissionError("read_only_task_approval_must_deny")
        await adapter.approve(task, {"choice": "deny"})
    elif action == "cancel":
        await adapter.cancel(task)
        _append_event(
            task_id,
            {
                "event_id": f"cancel:{task_id}",
                "type": "cancelled",
                "text": "Worker task cancelled.",
                "metadata": {"source": "user"},
            },
        )
        return _public_task(get_task(task_id) or task)
    else:
        raise ValueError("unknown_task_action")
    ensure_mirror(task_id)
    return await refresh_task(task_id, owner=owner)


async def stream_task_events(
    task_id: str,
    after: int = -1,
    *,
    owner: str,
) -> AsyncGenerator[str, None]:
    require_task_owner(task_id, owner)
    ensure_mirror(task_id)
    cursor = after
    while True:
        for event in task_events(task_id, cursor):
            cursor = int(event.get("seq", cursor))
            yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        task = get_task(task_id)
        if not task or (task.get("status") in TERMINAL and not task_events(task_id, cursor)):
            break
        yield ": heartbeat\n\n"
        await asyncio.sleep(1)
