from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from core.constants import DATA_DIR
from core.models import ChatMessage

TASKS_FILE = Path(DATA_DIR) / "agent_tasks.json"
KNOWLEDGE_MANIFEST_FILE = Path(DATA_DIR) / "jarvis_knowledge_manifest.json"
PC_CODEX_URL = os.getenv("ODYSSEUS_PC_CODEX_URL", "http://192.168.1.50:8040")
BRIDGE_TOKEN_FILE = Path(os.getenv("ODYSSEUS_AGENT_BRIDGE_TOKEN_FILE", "/etc/odysseus-agent-bridge-token"))
JARVIS_MODEL = os.getenv("ODYSSEUS_VOICE_MODEL", "qwen3.5-jarvis-v5:latest")
OLLAMA_URL = os.getenv("ODYSSEUS_JARVIS_OLLAMA_URL", "http://192.168.1.247:11434")
TERMINAL = {"completed", "failed", "cancelled", "blocked"}
WORKERS = {
    "pc-codex": {"enabled": True, "capabilities": ["local_files", "business", "home_lab", "code"]},
    "hermes": {"enabled": False, "capabilities": ["remote_agent"]},
    "vps-codex": {"enabled": False, "capabilities": ["vps_code", "vps_operations"]},
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _token() -> str:
    try:
        return BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def internal_token_valid(authorization: str | None) -> bool:
    expected = _token()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _headers() -> dict[str, str]:
    token = _token()
    if not token:
        raise RuntimeError("agent_bridge_token_missing")
    return {"Authorization": f"Bearer {token}"}


def _tasks() -> dict:
    return _read_json(TASKS_FILE, {"tasks": {}})


def get_task(task_id: str) -> dict | None:
    with _LOCK:
        return _tasks().get("tasks", {}).get(task_id)


def task_events(task_id: str, after: int = -1) -> list[dict]:
    task = get_task(task_id) or {}
    return [event for event in task.get("events", []) if int(event.get("seq", -1)) > after]


def _save_task(task: dict) -> None:
    with _LOCK:
        state = _tasks()
        state.setdefault("tasks", {})[task["task_id"]] = task
        _write_json(TASKS_FILE, state)


def _append_event(task_id: str, event: dict) -> None:
    with _LOCK:
        task = get_task(task_id)
        if not task:
            return
        events = task.setdefault("events", [])
        seq = int(event.get("seq", len(events)))
        if any(int(existing.get("seq", -1)) == seq for existing in events):
            return
        events.append(event)
        events.sort(key=lambda row: int(row.get("seq", 0)))
        event_type = event.get("type")
        if event_type == "result":
            task.update(status="completed", result=event.get("text"))
        elif event_type == "error":
            task.update(status="failed", error=event.get("text"))
        elif event_type == "cancelled":
            task["status"] = "cancelled"
        elif event_type == "question":
            task["status"] = "waiting"
        elif event_type in {"accepted", "progress", "tool_activity"}:
            task["status"] = "running"
        task["updated_at"] = int(time.time())
        if event_type == "result" and not task.get("result_persisted"):
            _persist_result(task, str(event.get("text") or ""))
            task["result_persisted"] = True
        _save_task(task)


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
            }),
        )
    except Exception:
        return


async def _mirror(task_id: str) -> None:
    after = max((int(event.get("seq", -1)) for event in task_events(task_id)), default=-1)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{PC_CODEX_URL}/v1/tasks/{task_id}/events",
                params={"after": after},
                headers=_headers(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        _append_event(task_id, json.loads(line[6:]))
    except Exception as exc:
        task = get_task(task_id)
        if task and task.get("status") not in TERMINAL:
            task.update(status="failed", error=f"worker_stream_failed: {str(exc)[:300]}")
            _save_task(task)
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
    owner: str = "leo",
    codex_thread_id: str | None = None,
) -> dict:
    if worker not in WORKERS:
        raise ValueError("unknown_worker")
    if not WORKERS[worker]["enabled"]:
        now = int(time.time())
        task = {
            "task_id": f"blocked-{worker}-{now}",
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
    if permission_mode != "read_only" and not approved:
        raise PermissionError("approval_required")
    payload = {
        "session_id": session_id,
        "workspace": workspace,
        "prompt": prompt,
        "permission_mode": permission_mode,
        "approved": approved,
        "codex_thread_id": codex_thread_id,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{PC_CODEX_URL}/v1/tasks", json=payload, headers=_headers())
    response.raise_for_status()
    task = response.json()
    task["owner"] = owner
    task["events"] = []
    _save_task(task)
    ensure_mirror(task["task_id"])
    return task


async def refresh_task(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if task.get("worker") == "pc-codex":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{PC_CODEX_URL}/v1/tasks/{task_id}", headers=_headers())
            response.raise_for_status()
            remote = response.json()
            for event in remote.get("events", []):
                _append_event(task_id, event)
            task = get_task(task_id) or task
        except Exception:
            pass
    ensure_mirror(task_id)
    return task


async def task_action(task_id: str, action: str, payload: dict | None = None) -> dict:
    task = get_task(task_id)
    if not task:
        raise KeyError(task_id)
    if task.get("worker") != "pc-codex":
        raise RuntimeError("worker_not_connected")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{PC_CODEX_URL}/v1/tasks/{task_id}/{action}",
            json=payload or {},
            headers=_headers(),
        )
    response.raise_for_status()
    return await refresh_task(task_id)


async def stream_task_events(task_id: str, after: int = -1) -> AsyncGenerator[str, None]:
    ensure_mirror(task_id)
    cursor = after
    while True:
        for event in task_events(task_id, cursor):
            cursor = int(event.get("seq", cursor))
            yield f"data: {json.dumps(event)}\n\n"
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
                "client": str(doc.get("client") or "MADPANDA3D"),
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
