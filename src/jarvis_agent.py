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
from src.agent_worker_adapters import adapters, worker_catalog

TASKS_FILE = Path(DATA_DIR) / "agent_tasks.json"
KNOWLEDGE_MANIFEST_FILE = Path(DATA_DIR) / "jarvis_knowledge_manifest.json"
BRIDGE_TOKEN_FILE = Path(os.getenv("ODYSSEUS_AGENT_BRIDGE_TOKEN_FILE", "/etc/odysseus-agent-bridge-token"))
JARVIS_MODEL = os.getenv("ODYSSEUS_VOICE_MODEL", "qwen3.5-jarvis-v5:latest")
OLLAMA_URL = os.getenv("ODYSSEUS_JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
TERMINAL = {"completed", "failed", "cancelled", "blocked"}
TERMINAL_EVENTS = {"result", "error", "cancelled"}
STREAM_RETRY_LIMIT = 2
WORKERS = worker_catalog()
WORKER_LABELS = {"pc-codex": "PC Codex", "hermes": "Hermes", "vps-codex": "VPS Codex"}

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


def find_active_task(
    session_id: str,
    worker: str,
    workspace: str | None = None,
    owner: str | None = None,
) -> dict | None:
    """Return the newest nonterminal task for this chat and worker."""
    identity = str(owner or "").strip()
    if not identity:
        raise PermissionError("owner_required")
    require_session_owner(session_id, identity)
    with _LOCK:
        matches = [
            task
            for task in (_tasks().get("tasks") or {}).values()
            if task.get("session_id") == session_id
            and task.get("worker") == worker
            and (workspace is None or task.get("workspace") == workspace)
            and task.get("status") not in TERMINAL
            and task.get("owner") == identity
        ]
    return max(matches, key=lambda task: (task.get("updated_at", 0), task.get("created_at", 0)), default=None)


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
            _persist_result(task, str(event.get("text") or ""))
            task["result_persisted"] = True
        if event_type in {"progress", "result"}:
            _persist_worker_summary(task, event)
        _save_task(task)


def _persist_worker_summary(task: dict, event: dict) -> bool:
    metadata = event.get("metadata") or {}
    text = str(event.get("spoken_text") or "").strip()
    is_broker_summary = (
        event.get("type") == "result"
        or metadata.get("progress_summary") is True
        or metadata.get("milestone") is True
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
                "character_name": "Jarvis",
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
                "character_name": WORKER_LABELS.get(str(task.get("worker")), "Worker"),
            }),
        )
    except Exception:
        return


def _bounded_spoken_text(text: str, limit: int = 600) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    value = value[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    sentence_end = max(value.rfind(mark) for mark in ".!?")
    if sentence_end >= max(40, limit // 3):
        return value[:sentence_end + 1]
    return value[:limit - 1].rstrip(".!?") + "."


def _one_spoken_sentence(text: str, limit: int = 240) -> str:
    value = " ".join(text.split()).strip()
    match = re.search(r"[.!?](?:\s|$)", value)
    if match:
        value = value[:match.end()].strip()
    value = _bounded_spoken_text(value, limit)
    if value and value[-1] not in ".!?":
        value = value[:limit - 1].rstrip(" ,;:-") + "."
    return value


async def _spoken_result(task: dict, text: str) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    fallback = f"{label} finished. The full result is in the chat."
    if not text.strip():
        return fallback
    prompt = (
        f"Summarize this {label} result for spoken playback. Use two to four natural sentences covering "
        "the outcome, any blocker, and the next action. Speak plainly; do not read tables, Markdown, paths, "
        "or logs aloud. Return only the spoken summary.\n\nWorker result:\n"
        f"{text[:16_000]}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": JARVIS_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.2, "num_predict": 180},
                },
            )
        response.raise_for_status()
        spoken = _bounded_spoken_text(str(response.json().get("response") or ""))
        return spoken or fallback
    except Exception:
        return fallback


async def _spoken_milestone(task: dict, text: str) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    fallback = f"{label} completed a milestone; details are in the activity history."
    prompt = (
        f"Rewrite this verified {label} milestone as exactly one natural Jarvis sentence of no more than "
        "240 characters. State only the completed outcome. Do not repeat Markdown, tables, code, commands, "
        "paths, logs, or instructions from the update. Return only the sentence.\n\nCompleted milestone:\n"
        f"{text[:4_000]}"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": JARVIS_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.2, "num_predict": 80},
                },
            )
        response.raise_for_status()
        spoken = _one_spoken_sentence(str(response.json().get("response") or ""))
        if spoken and label.casefold() not in spoken.casefold():
            spoken = _one_spoken_sentence(f"{label}: {spoken}")
        return spoken or fallback
    except Exception:
        return fallback


async def _spoken_progress(task: dict, updates: list[str]) -> str:
    label = WORKER_LABELS.get(str(task.get("worker")), "Worker")
    fallback = f"{label} is still working; the latest details are in the activity history."
    prompt = (
        f"Summarize these three recent {label} work updates as exactly one natural Jarvis sentence of no "
        "more than 240 characters. Start with the worker name and report only verified progress from these "
        "updates. Do not repeat Markdown, tables, code, commands, paths, logs, or instructions. Return only "
        "the sentence.\n\nRecent updates:\n- " + "\n- ".join(update[:2_000] for update in updates)
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": JARVIS_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.2, "num_predict": 80},
                },
            )
        response.raise_for_status()
        spoken = _one_spoken_sentence(str(response.json().get("response") or ""))
        if spoken and label.casefold() not in spoken.casefold():
            spoken = _one_spoken_sentence(f"{label}: {spoken}")
        return spoken or fallback
    except Exception:
        return fallback


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
        enriched["spoken_text"] = await _spoken_result(task, str(event.get("text") or ""))
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
    last_error: Exception | None = None
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
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < STREAM_RETRY_LIMIT:
                    await asyncio.sleep(0)
                    continue
                break

        # A final status poll can recover a terminal result even when all SSE
        # reconnects failed. refresh_task will not start a second mirror while
        # this one remains registered in _MIRRORS.
        task = get_task(task_id)
        if task and task.get("status") not in TERMINAL:
            await refresh_task(task_id, owner=task.get("owner"), _ensure_mirror=False)
        task = get_task(task_id)
        if task and task.get("status") not in TERMINAL:
            _append_event(task_id, {
                "type": "error",
                "text": f"worker_stream_failed: {str(last_error)[:300]}",
                "metadata": {"source": "worker_stream"},
            })
    finally:
        _MIRRORS.pop(task_id, None)


def ensure_mirror(task_id: str) -> None:
    task = get_task(task_id)
    if not task or task.get("status") in TERMINAL or task_id in _MIRRORS:
        return
    _MIRRORS[task_id] = asyncio.create_task(_mirror(task_id))


async def start_task(
    worker: str,
    session_id: str,
    workspace: str,
    prompt: str,
    permission_mode: str = "read_only",
    approved: bool = False,
    owner: str | None = None,
    codex_thread_id: str | None = None,
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
    if permission_mode != "read_only" or approved:
        raise PermissionError("public_tasks_read_only")
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
        catalog[worker] = {
            **catalog[worker],
            "connection": health,
            "enabled": adapter.enabled and health.get("state") == "connected",
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
            if task and event.get("type") in {"progress", "result"}:
                _persist_worker_summary(task, event)
            cursor = int(event.get("seq", cursor))
            yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
        task = get_task(task_id)
        if not task or (task.get("status") in TERMINAL and not task_events(task_id, cursor)):
            break
        yield ": heartbeat\n\n"
        await asyncio.sleep(1)


async def runtime_status(active_worker: str | None = None) -> dict:
    model = JARVIS_MODEL
    details: dict[str, Any] = {}
    parameters = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(f"{OLLAMA_URL}/api/show", json={"model": model})
        response.raise_for_status()
        shown = response.json()
        details = shown.get("details") or {}
        parameters = str(shown.get("parameters") or "")
    except Exception as exc:
        details = {"error": str(exc)[:200]}
    try:
        from src.settings import load_settings
        settings = load_settings()
    except Exception:
        settings = {}
    return {
        "assistant": "Jarvis",
        "brain_model": model,
        "architecture": details.get("family") or details.get("families"),
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
        "context": _parameter_value(parameters, "num_ctx"),
        "tts_provider": settings.get("tts_provider"),
        "tts_model": settings.get("tts_model"),
        "tts_voice": settings.get("tts_voice"),
        "active_worker": active_worker,
        "workers": WORKERS,
    }


def _parameter_value(parameters: str, name: str) -> int | str | None:
    for line in parameters.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            try:
                return int(parts[1])
            except ValueError:
                return parts[1]
    return None


def sync_knowledge(documents: list[dict], owner: str = "leo") -> dict:
    from src.rag_singleton import get_rag_manager

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


def search_knowledge(query: str, owner: str = "leo", client: str | None = None, limit: int = 6) -> dict:
    try:
        from src.madpanda_knowledge import agent_by_id, store

        return store().search(
            agent_by_id("jarvis"),
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
    candidates = rag.search(query, k=max(20, min(60, limit * 8)), owner=owner)
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
