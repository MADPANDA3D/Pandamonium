from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from core.models import ChatMessage
from src.agent_identity import configured_agent_id, configured_agent_name
from src.agent_worker_adapters import adapters, require_worker_task_permission, worker_catalog
from src.voice_pcm import asks_read_all, result_speech, speech_text

TASKS_FILE = Path(DATA_DIR) / "agent_tasks.json"
KNOWLEDGE_MANIFEST_FILE = Path(DATA_DIR) / "jarvis_knowledge_manifest.json"
BRIDGE_TOKEN_FILE = Path(os.getenv("ODYSSEUS_AGENT_BRIDGE_TOKEN_FILE", "/etc/odysseus-agent-bridge-token"))
TERMINAL = {"completed", "failed", "cancelled", "blocked"}
TERMINAL_EVENTS = {"result", "error", "cancelled"}
STREAM_RETRY_LIMIT = 2
STREAM_RECONCILE_TIMEOUT_SECONDS = max(
    0.0, float(os.getenv("ODYSSEUS_WORKER_RECONCILE_TIMEOUT_SECONDS", "600"))
)
STREAM_RECONCILE_POLL_SECONDS = max(
    0.1, float(os.getenv("ODYSSEUS_WORKER_RECONCILE_POLL_SECONDS", "2"))
)
WORKERS = worker_catalog()
WORKER_LABELS = {
    worker_id: str(details.get("label") or worker_id)
    for worker_id, details in WORKERS.items()
}

_LOCK = threading.RLock()
_MIRRORS: dict[str, asyncio.Task] = {}
_SESSION_MANAGER = None


def configure(session_manager) -> None:
    global _SESSION_MANAGER
    _SESSION_MANAGER = session_manager


def _read_json(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def _write_json(path: Path, value: dict) -> None:
    atomic_write_json(str(path), value, indent=2)


def _token() -> str:
    try:
        return BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def internal_token_valid(authorization: str | None) -> bool:
    expected = _token()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _tasks() -> dict:
    return _read_json(TASKS_FILE, {"tasks": {}, "bindings": {}})


def get_task(task_id: str) -> dict | None:
    with _LOCK:
        return _tasks().get("tasks", {}).get(task_id)


def require_task_owner(task_id: str, owner: str | None) -> dict:
    """Return a task only when it belongs to the authenticated owner."""
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    task = get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if str(task.get("owner") or "") != identity:
        raise PermissionError("task_owner_mismatch")
    return task


def require_session_owner(session_id: str, owner: str) -> Any:
    """Fail closed unless the linked chat session belongs to the task owner."""
    if not _SESSION_MANAGER:
        raise RuntimeError("session_manager_unavailable")
    try:
        session = _SESSION_MANAGER.get_session(session_id)
    except Exception as exc:
        raise KeyError(session_id) from exc
    if getattr(session, "owner", None) != owner:
        raise PermissionError("session_owner_mismatch")
    return session


def list_active_tasks(
    session_id: str,
    owner: str,
    worker: str | None = None,
    workspace: str | None = None,
    statuses: set[str] | None = None,
) -> list[dict]:
    """Return broker-owned nonterminal tasks for one authenticated chat."""
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    require_session_owner(session_id, identity)
    with _LOCK:
        matches = [
            task
            for task in (_tasks().get("tasks") or {}).values()
            if task.get("session_id") == session_id
            and (worker is None or task.get("worker") == worker)
            and (workspace is None or task.get("workspace") == workspace)
            and task.get("status") not in TERMINAL
            and (statuses is None or task.get("status") in statuses)
            and task.get("owner") == identity
        ]
    return sorted(
        matches,
        key=lambda task: (task.get("updated_at", 0), task.get("created_at", 0)),
        reverse=True,
    )


def find_active_task(
    session_id: str,
    worker: str,
    workspace: str | None = None,
    owner: str | None = None,
) -> dict | None:
    """Return the newest nonterminal task for this chat and worker."""
    matches = list_active_tasks(session_id, str(owner or ""), worker, workspace)
    return matches[0] if matches else None


def task_events(task_id: str, after: int = -1) -> list[dict]:
    task = get_task(task_id) or {}
    return [event for event in task.get("events", []) if int(event.get("seq", -1)) > after]


def _binding_key(session_id: str, worker: str, workspace: str) -> str:
    return f"{session_id}:{worker}:{workspace}"


def get_worker_binding(session_id: str, worker: str, workspace: str) -> dict:
    with _LOCK:
        return dict((_tasks().get("bindings") or {}).get(_binding_key(session_id, worker, workspace)) or {})


def _save_worker_binding(task: dict, **values: Any) -> None:
    with _LOCK:
        state = _tasks()
        key = _binding_key(task["session_id"], task["worker"], task["workspace"])
        binding = state.setdefault("bindings", {}).setdefault(key, {})
        binding.update({k: v for k, v in values.items() if v})
        binding["updated_at"] = int(time.time())
        _write_json(TASKS_FILE, state)


def _save_task(task: dict) -> None:
    with _LOCK:
        state = _tasks()
        state.setdefault("tasks", {})[task["task_id"]] = task
        _write_json(TASKS_FILE, state)


def _persist_artifact(task: dict, event: dict) -> dict:
    metadata = dict(event.get("metadata") or {})
    content = str(metadata.pop("content", ""))
    if not content or len(content) > 2_000_000:
        return event
    source_path = str(metadata.get("source_path") or "")
    artifact_key = str(metadata.get("artifact_key") or hashlib.sha256(
        f"{task['worker']}|{source_path}|{content}".encode()
    ).hexdigest())
    existing = next((row for row in task.get("artifacts", []) if row.get("artifact_key") == artifact_key), None)
    if existing:
        metadata.update(existing)
        event["metadata"] = metadata
        return event

    from core.database import Document, DocumentVersion, SessionLocal

    doc_id = str(uuid.uuid4())
    title = str(metadata.get("title") or Path(source_path).name or "Worker Artifact")[:240]
    language = str(metadata.get("language") or "markdown")[:40]
    db = SessionLocal()
    try:
        document = Document(
            id=doc_id,
            session_id=task.get("session_id"),
            title=title,
            language=language,
            current_content=content,
            version_count=1,
            is_active=True,
            owner=task.get("owner"),
        )
        db.add(document)
        db.add(DocumentVersion(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            version_number=1,
            content=content,
            summary=f"Created by {task.get('worker')}",
            source="agent_worker",
        ))
        db.commit()
    finally:
        db.close()

    persisted = {
        "artifact_key": artifact_key,
        "document_id": doc_id,
        "title": title,
        "language": language,
        "source_path": source_path,
        "href": f"#document-{doc_id}",
    }
    task.setdefault("artifacts", []).append(persisted)
    metadata.update(persisted)
    event["metadata"] = metadata
    return event


def _append_event(task_id: str, event: dict) -> None:
    with _LOCK:
        task = get_task(task_id)
        if not task:
            return
        events = task.setdefault("events", [])
        event_id = str(event.get("event_id") or "")
        if event_id and any(str(existing.get("event_id") or "") == event_id for existing in events):
            return
        # A remote stream and a status reconciliation can observe completion at
        # the same time. Once one terminal event lands, all later events are
        # stale and must not change the outcome or resurrect the task.
        if task.get("status") in TERMINAL or any(
            existing.get("type") in TERMINAL_EVENTS for existing in events
        ):
            return
        event = dict(event)
        event["seq"] = len(events)
        event["task_id"] = task_id
        event["worker"] = task.get("worker")
        event["presenter"] = task.get("presenter") or configured_agent_name()
        event["event_id"] = event_id or str(uuid.uuid4())
        if event.get("type") == "artifact":
            event = _persist_artifact(task, event)
        events.append(event)
        event_type = event.get("type")
        if event_type == "result":
            task.update(status="completed", result=event.get("text"))
        elif event_type == "error":
            task.update(status="failed", error=event.get("text"))
        elif event_type == "cancelled":
            task["status"] = "cancelled"
        elif event_type == "question":
            task["status"] = "waiting"
        elif event_type == "approval_required":
            task["status"] = "waiting_approval"
        elif event_type in {"accepted", "progress", "tool_activity", "artifact"}:
            task["status"] = "running"
        metadata = event.get("metadata") or {}
        if metadata.get("codex_thread_id"):
            task["codex_thread_id"] = metadata["codex_thread_id"]
            _save_worker_binding(task, codex_thread_id=metadata["codex_thread_id"])
        task["updated_at"] = int(time.time())
        if event_type == "result" and not task.get("result_persisted"):
            if task.get("persist_result", True):
                _persist_result(task, str(event.get("text") or ""))
            task["result_persisted"] = True
        if event_type == "progress":
            _persist_worker_summary(task, event)
        _save_task(task)


def _persist_worker_summary(task: dict, event: dict) -> bool:
    metadata = event.get("metadata") or {}
    text = str(event.get("spoken_text") or "").strip()
    is_broker_summary = (
        event.get("type") == "progress"
        and (metadata.get("progress_summary") is True or metadata.get("milestone") is True)
    )
    if not _SESSION_MANAGER or not text or not is_broker_summary or not task.get("session_id"):
        return False
    event_id = str(event.get("event_id") or "")
    with _LOCK:
        try:
            session = _SESSION_MANAGER.get_session(task["session_id"])
            matching = [
                message for message in session.history
                if event_id
                and (message.metadata or {}).get("source") == "jarvis_worker_summary"
                and (message.metadata or {}).get("task_id") == task.get("task_id")
                and (message.metadata or {}).get("worker_event_id") == event_id
            ]
            if any((message.metadata or {}).get("_db_id") for message in matching):
                return True
            if matching:
                ghost_ids = {id(message) for message in matching}
                session.history[:] = [message for message in session.history if id(message) not in ghost_ids]
                session._history = session.history
                session.message_count = len(session.history)
            message = ChatMessage("assistant", text, metadata={
                "source": "jarvis_worker_summary",
                "worker": task.get("worker"),
                "task_id": task.get("task_id"),
                "worker_event_id": event_id,
                "character_name": task.get("presenter") or configured_agent_name(),
            })
            try:
                _SESSION_MANAGER.add_message(task["session_id"], message)
            except Exception:
                pass
            if not (message.metadata or {}).get("_db_id"):
                session.history[:] = [existing for existing in session.history if existing is not message]
                session._history = session.history
                session.message_count = len(session.history)
                return False
            return True
        except Exception:
            return False


def _persist_result(task: dict, text: str) -> None:
    if not _SESSION_MANAGER or not text or not task.get("session_id"):
        return
    try:
        _SESSION_MANAGER.add_message(
            task["session_id"],
            ChatMessage("assistant", text, metadata={
                "source": "agent_worker",
                "worker": task.get("worker"),
                "task_id": task.get("task_id"),
                "character_name": task.get("presenter") or configured_agent_name(),
            }),
        )
    except Exception:
        return


def consume_task_result(task_id: str, *, owner: str, session_id: str | None = None) -> dict:
    """Hand a completed worker result to the orchestrator without a raw duplicate."""
    with _LOCK:
        task = require_task_owner(task_id, owner)
        expected_session_id = str(session_id or "").strip()
        if expected_session_id and str(task.get("session_id") or "") != expected_session_id:
            return task
        if task.get("status") != "completed":
            return task

        session_id = str(task.get("session_id") or "")
        task["persist_result"] = False
        task["result_consumed"] = True
        if _SESSION_MANAGER and session_id:
            try:
                session = _SESSION_MANAGER.get_session(session_id)
                matching = [
                    message for message in session.history
                    if (message.metadata or {}).get("source") == "agent_worker"
                    and (message.metadata or {}).get("task_id") == task_id
                ]
                for message in matching:
                    message_id = str((message.metadata or {}).get("_db_id") or "")
                    if message_id and hasattr(_SESSION_MANAGER, "delete_message"):
                        _SESSION_MANAGER.delete_message(session_id, message_id)
                    elif message in session.history:
                        session.history.remove(message)
                session._history = session.history
                session.message_count = len(session.history)
            except Exception:
                pass
        _save_task(task)
        return get_task(task_id) or task


def _jarvis_runtime(task: dict | None = None) -> tuple[str, str, dict]:
    """Resolve the Jarvis brain from the linked chat, then the user's default."""
    owner = str((task or {}).get("owner") or "").strip() or None
    session_id = str((task or {}).get("session_id") or "").strip()
    if _SESSION_MANAGER and session_id:
        try:
            session = _SESSION_MANAGER.get_session(session_id)
            if (not owner or getattr(session, "owner", None) == owner) and session.endpoint_url and session.model:
                return session.endpoint_url, session.model, dict(session.headers or {})
        except Exception:
            pass

    from src.endpoint_resolver import resolve_endpoint

    endpoint_url, model, headers = resolve_endpoint("default", owner=owner)
    if not endpoint_url or not model:
        raise RuntimeError("jarvis_brain_not_configured")
    return endpoint_url, model, dict(headers or {})


async def _spoken_result(task: dict, text: str) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    return result_speech(
        text,
        kind="worker",
        label=label,
        explicit_read_all=asks_read_all(str(task.get("prompt") or "")),
    )["spoken_text"]


def _worker_result_speech(task: dict, event: dict) -> dict[str, str]:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    return result_speech(
        str(event.get("text") or ""),
        kind="worker",
        label=label,
        explicit_read_all=asks_read_all(str(task.get("prompt") or "")),
        provided_spoken_text=str(event.get("spoken_text") or "") or None,
        provided_speech_mode=str(event.get("speech_mode") or "") or None,
    )


async def _spoken_milestone(task: dict, text: str) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    cleaned = speech_text(text)
    if cleaned and len(cleaned.split()) <= 40:
        return cleaned
    return f"{label} completed a milestone; details are in the activity history."


async def _spoken_progress(task: dict, updates: list[str]) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    return f"{label} is still working; the latest details are in the activity history."


def _ordinary_progress_window(task: dict, text: str) -> list[str]:
    current = get_task(str(task.get("task_id") or "")) or task
    updates = []
    for prior in reversed(current.get("events") or []):
        if prior.get("type") != "progress":
            continue
        metadata = prior.get("metadata") or {}
        if metadata.get("milestone") is True or metadata.get("progress_summary") is True:
            break
        updates.append(str(prior.get("text") or ""))
    updates = list(reversed(updates)) + [text]
    return updates[-3:] if len(updates) >= 3 else []


async def _enrich_worker_event(task: dict, event: dict) -> dict:
    enriched = dict(event)
    if event.get("type") == "progress":
        enriched.pop("spoken_text", None)
        metadata = dict(event.get("metadata") or {})
        metadata.pop("progress_summary", None)
        enriched["metadata"] = metadata
        if metadata.get("milestone") is True:
            enriched["spoken_text"] = await _spoken_milestone(task, str(event.get("text") or ""))
        else:
            updates = _ordinary_progress_window(task, str(event.get("text") or ""))
            if updates:
                metadata["progress_summary"] = True
                enriched["spoken_text"] = await _spoken_progress(task, updates)
    elif event.get("type") == "result":
        metadata = dict(event.get("metadata") or {})
        metadata["result_summary"] = True
        enriched["metadata"] = metadata
        enriched.update(_worker_result_speech(task, event))
    elif event.get("type") in {"approval_required", "question"}:
        enriched["spoken_text"] = speech_text(str(event.get("text") or ""), preserve_code=True)
        enriched["speech_mode"] = "verbatim"
    elif event.get("type") == "error":
        label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
        enriched.update(result_speech(str(event.get("text") or ""), kind="failure", label=label))
    return enriched


def _persist_task_user_message(task: dict, text: str, source: str) -> None:
    if not _SESSION_MANAGER or not text or not task.get("session_id"):
        return
    try:
        _SESSION_MANAGER.add_message(
            task["session_id"],
            ChatMessage("user", text, metadata={
                "source": source,
                "worker": task.get("worker"),
                "task_id": task.get("task_id"),
            }),
        )
    except Exception:
        return


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
                    event = await _enrich_worker_event(task, event)
                    _append_event(task_id, event)
                if (get_task(task_id) or {}).get("status") in TERMINAL:
                    return
                if attempt < STREAM_RETRY_LIMIT:
                    await asyncio.sleep(0)
                    continue
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt < STREAM_RETRY_LIMIT:
                    await asyncio.sleep(0)
                    continue
                break

        # Some workers (notably Hermes) expose a one-consumer event queue and
        # cannot replay after a dropped connection. Treat status polling as one
        # bounded reconciliation phase rather than falsely failing a task that
        # is still running remotely.
        deadline = time.monotonic() + STREAM_RECONCILE_TIMEOUT_SECONDS
        while True:
            task = get_task(task_id)
            if not task or task.get("status") in TERMINAL:
                return
            await refresh_task(task_id, owner=task.get("owner"), _ensure_mirror=False)
            task = get_task(task_id)
            if not task or task.get("status") in TERMINAL:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(STREAM_RECONCILE_POLL_SECONDS, remaining))

        _append_event(task_id, {
            "type": "error",
            "text": (
                "worker_stream_failed: status reconciliation timed out; "
                "the remote task may still be running"
            ),
            "metadata": {"source": "worker_stream", "reconciliation_timeout": True},
        })
    finally:
        _MIRRORS.pop(task_id, None)


def ensure_mirror(task_id: str) -> None:
    task = get_task(task_id)
    if not task or task.get("status") in TERMINAL or task_id in _MIRRORS:
        return
    _MIRRORS[task_id] = asyncio.create_task(_mirror(task_id))


async def direct_hermes_turn(
    session_id: str,
    prompt: str,
    *,
    owner: str | None,
    workspace: str = "home-lab",
) -> str:
    """Talk to Gordon directly without creating a Jarvis broker task."""
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    require_session_owner(session_id, identity)
    catalog = worker_catalog()
    if workspace not in set(catalog["hermes"].get("workspaces") or []):
        raise ValueError("unknown_workspace")
    adapter = adapters()["hermes"]
    if not adapter.enabled:
        raise RuntimeError("hermes_not_connected")
    scope = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"odysseus:gordon:{identity}:{session_id}:{workspace}",
    )
    return await adapter.direct_chat(
        session_id=f"odysseus-gordon-{scope}",
        session_key=f"odysseus:gordon:{scope}",
        message=prompt,
    )


async def start_task(
    worker: str,
    session_id: str,
    workspace: str,
    prompt: str,
    permission_mode: str = "read_only",
    approved: bool = False,
    owner: str | None = None,
    codex_thread_id: str | None = None,
    presenter: str | None = None,
    persist_result: bool = True,
) -> dict:
    owner = str(owner or "").strip()
    if not owner:
        raise PermissionError("owner_required")
    require_session_owner(session_id, owner)
    catalog = worker_catalog()
    if worker not in catalog:
        raise ValueError("unknown_worker")
    if workspace not in set(catalog[worker].get("workspaces") or []):
        raise ValueError("unknown_workspace")
    require_worker_task_permission(permission_mode, approved)
    registry = adapters()
    if worker not in registry:
        raise ValueError("unknown_worker")
    adapter = registry[worker]
    if not adapter.enabled:
        now = int(time.time())
        task = {
            "task_id": f"blocked-{worker}-{uuid.uuid4()}",
            "worker": worker,
            "session_id": session_id,
            "workspace": workspace,
            "permission_mode": permission_mode,
            "status": "blocked",
            "reason": "worker_not_connected",
            "owner": owner,
            "presenter": str(presenter or configured_agent_name())[:80],
            "persist_result": persist_result is True,
            "events": [],
            "created_at": now,
            "updated_at": now,
        }
        _save_task(task)
        return task
    binding = get_worker_binding(session_id, worker, workspace)
    codex_thread_id = codex_thread_id or binding.get("codex_thread_id")
    now = int(time.time())
    task = {
        "task_id": str(uuid.uuid4()),
        "remote_task_id": None,
        "worker": worker,
        "session_id": session_id,
        "workspace": workspace,
        "prompt": prompt,
        "permission_mode": permission_mode,
        "approved": approved,
        "codex_thread_id": codex_thread_id,
        "worker_session_key": binding.get("worker_session_key"),
        "status": "queued",
        "result": None,
        "error": None,
        "owner": owner,
        "presenter": str(presenter or configured_agent_name())[:80],
        "persist_result": persist_result is True,
        "events": [],
        "artifacts": [],
        "created_at": now,
        "updated_at": now,
    }
    remote = await adapter.start(task)
    task.update(remote)
    _save_task(task)
    _append_event(task["task_id"], {
        "type": "accepted",
        "text": f"{worker_catalog()[worker]['machine']} accepted the task.",
        "metadata": {"remote_task_id": task.get("remote_task_id")},
    })
    if task.get("worker_session_key"):
        _save_worker_binding(task, worker_session_key=task["worker_session_key"])
    ensure_mirror(task["task_id"])
    return get_task(task["task_id"]) or task


async def refresh_task(
    task_id: str,
    *,
    owner: str,
    _ensure_mirror: bool = True,
) -> dict:
    task = require_task_owner(task_id, owner)
    if not task:
        raise KeyError(task_id)
    try:
        adapter = adapters()[task["worker"]]
        remote = await adapter.status(task)
        remote_status = str(remote.get("status") or "")
        if remote.get("codex_thread_id"):
            task["codex_thread_id"] = remote["codex_thread_id"]
            _save_worker_binding(task, codex_thread_id=remote["codex_thread_id"])
        if remote_status in {"completed", "failed", "cancelled"} and task.get("status") not in TERMINAL:
            event_type = {"completed": "result", "failed": "error", "cancelled": "cancelled"}[remote_status]
            text = str(remote.get("output") or remote.get("result") or remote.get("error") or f"{task['worker']} {remote_status}.")
            event = await _enrich_worker_event(
                task,
                {"type": event_type, "text": text, "metadata": {"reconciled": True}},
            )
            _append_event(task_id, event)
        task = get_task(task_id) or task
    except Exception:
        pass
    if _ensure_mirror:
        ensure_mirror(task_id)
    return task


async def task_action(
    task_id: str,
    action: str,
    payload: dict | None = None,
    *,
    persist_user_message: bool = True,
    owner: str,
) -> dict:
    task = require_task_owner(task_id, owner)
    if not task:
        raise KeyError(task_id)
    adapter = adapters()[task["worker"]]
    if action == "steer":
        await adapter.steer(task, payload or {})
        if persist_user_message:
            _persist_task_user_message(task, str((payload or {}).get("prompt") or "").strip(), "agent_worker_steer")
    elif action == "reply":
        await adapter.reply(task, payload or {})
        answers = (payload or {}).get("answers") or {}
        text = " ".join(
            " ".join(str(item) for item in value) if isinstance(value, list) else str(value)
            for value in answers.values()
        ).strip()
        if persist_user_message:
            _persist_task_user_message(task, text, "agent_worker_reply")
    elif action == "approval":
        require_worker_task_permission(
            str(task.get("permission_mode") or "read_only"),
            task.get("approved") is True,
        )
        choice = str((payload or {}).get("choice") or "")
        if choice not in {"once", "session", "always", "deny"}:
            raise ValueError("invalid_approval_choice")
        if str(task.get("permission_mode") or "read_only") == "read_only" and choice != "deny":
            raise PermissionError("read_only_task_approval_must_deny")
        await adapter.approve(task, payload or {})
        if persist_user_message:
            _persist_task_user_message(task, str((payload or {}).get("spoken_text") or "").strip(), "agent_worker_approval")
    elif action == "cancel":
        await adapter.cancel(task)
    else:
        raise ValueError("unknown_task_action")
    return await refresh_task(task_id, owner=owner)


async def worker_statuses() -> dict[str, dict[str, Any]]:
    catalog = worker_catalog()
    registry = adapters()
    results = await asyncio.gather(*(adapter.health() for adapter in registry.values()))
    for (worker, adapter), health in zip(registry.items(), results):
        configured = bool(adapter.enabled)
        ready = configured and health.get("state") == "connected"
        catalog[worker] = {
            **catalog[worker],
            "connection": health,
            "configured": configured,
            "ready": ready,
            "enabled": ready,
        }
    return catalog


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
            task = get_task(task_id)
            if task and event.get("type") == "progress":
                _persist_worker_summary(task, event)
            cursor = int(event.get("seq", cursor))
            yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
        task = get_task(task_id)
        if not task or (task.get("status") in TERMINAL and not task_events(task_id, cursor)):
            break
        yield ": heartbeat\n\n"
        await asyncio.sleep(1)


async def runtime_status(active_worker: str | None = None, owner: str | None = None) -> dict:
    endpoint_url = ""
    model = ""
    details: dict[str, Any] = {}
    context: int | str | None = None
    try:
        endpoint_url, model, headers = _jarvis_runtime({"owner": owner} if owner else None)
        from src.endpoint_resolver import build_models_url

        models_url = build_models_url(endpoint_url)
        if models_url:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            entries = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(entries, list):
                details = next(
                    (entry for entry in entries if isinstance(entry, dict) and entry.get("id") == model),
                    {},
                )
            context = details.get("max_model_len") or details.get("context_length")
    except Exception as exc:
        details = {"error": str(exc)[:200]}
    if not context and endpoint_url and model:
        try:
            from src.model_context import get_context_length

            context = get_context_length(endpoint_url, model)
        except Exception:
            pass
    try:
        from src.settings import load_settings
        settings = load_settings()
    except Exception:
        settings = {}
    from core.constants import APP_VERSION

    memory_evidence = {
        key: details[key]
        for key in (
            "kv_cache", "kv_cache_type", "cache_type_k", "cache_type_v",
            "swa_cache", "sliding_window", "moe_cache", "n_cpu_moe",
        )
        if key in details and isinstance(details[key], (str, int, float, bool))
    }
    return {
        "application_version": APP_VERSION,
        "assistant": configured_agent_name(),
        "brain_model": model,
        "architecture": details.get("architecture") or details.get("owned_by"),
        "parameter_size": details.get("parameter_size") or details.get("parameters"),
        "quantization": details.get("quantization") or details.get("quantization_level"),
        "context": context,
        "model_memory_evidence": memory_evidence or None,
        "tts_provider": settings.get("tts_provider"),
        "tts_model": settings.get("tts_model"),
        "tts_voice": settings.get("tts_voice"),
        "active_worker": active_worker,
        "workers": WORKERS,
    }


def sync_knowledge(documents: list[dict], owner: str | None = None) -> dict:
    from src.rag_singleton import get_rag_manager

    owner = str(owner or os.environ.get("ODYSSEUS_FALLBACK_OWNER") or "owner@localhost").strip()

    rag = get_rag_manager()
    if not rag or not rag.healthy:
        raise RuntimeError("rag_unavailable")
    manifest = _read_json(KNOWLEDGE_MANIFEST_FILE, {"sources": {}})
    incoming = {str(doc.get("source") or "") for doc in documents if doc.get("source")}
    removed = 0
    for source in set(manifest.get("sources", {})) - incoming:
        removed += rag.delete_by_source(source)
    added = 0
    unchanged = 0
    next_sources: dict[str, dict] = {}
    for doc in documents:
        source = str(doc.get("source") or "")
        text = str(doc.get("text") or "").strip()
        content_hash = str(doc.get("content_hash") or "")
        if not source or not text or len(text) > 2_000_000:
            continue
        previous = manifest.get("sources", {}).get(source) or {}
        next_sources[source] = {"content_hash": content_hash, "mtime": doc.get("mtime"), "client": doc.get("client")}
        if previous.get("content_hash") == content_hash:
            unchanged += 1
            continue
        rag.delete_by_source(source)
        rows = []
        for index, chunk in enumerate(rag._split_into_chunks(text)):
            rows.append((chunk, {
                "owner": owner,
                "source": source,
                "filename": Path(source).name,
                "client": str(doc.get("client") or "unscoped"),
                "mtime": int(doc.get("mtime") or 0),
                "content_hash": content_hash,
                "document_type": str(doc.get("document_type") or "text"),
                "chunk_id": index,
            }))
        result = rag.add_documents_batch(rows)
        if result.get("success"):
            added += int(result.get("added_count") or 0)
    manifest = {"sources": next_sources, "updated_at": int(time.time())}
    _write_json(KNOWLEDGE_MANIFEST_FILE, manifest)
    return {"ok": True, "sources": len(next_sources), "chunks_added": added, "chunks_removed": removed, "unchanged": unchanged}


def search_knowledge(query: str, owner: str | None = None, client: str | None = None, limit: int = 6) -> dict:
    if not owner:
        return {"error": "owner_required", "results": []}
    try:
        from src.madpanda_knowledge import agent_by_id, store

        return store().search(
            agent_by_id(configured_agent_id()),
            query,
            domain="business_client" if client else None,
            client=client,
            limit=limit,
        )
    except Exception:
        # Keep the proven Business index as the rollback path until V1 is accepted.
        pass

    from src.rag_singleton import get_rag_manager

    rag = get_rag_manager()
    if not rag or not rag.healthy:
        return {"error": "rag_unavailable", "results": []}
    candidates = rag.search(query, k=max(20, min(60, limit * 8)), owner=str(owner))
    if client:
        wanted = client.casefold()
        candidates = [row for row in candidates if str((row.get("metadata") or {}).get("client") or "").casefold() == wanted]
    results = []
    for row in candidates[: max(1, min(limit, 12))]:
        meta = row.get("metadata") or {}
        results.append({
            "text": row.get("document"),
            "source": meta.get("source"),
            "client": meta.get("client"),
            "mtime": meta.get("mtime"),
            "score": row.get("similarity"),
        })
    return {"query": query, "client": client, "results": results}


def self_check() -> None:
    assert _parameter_value("num_ctx 32768\ntemperature 0.3", "num_ctx") == 32768
    assert internal_token_valid(None) is False
