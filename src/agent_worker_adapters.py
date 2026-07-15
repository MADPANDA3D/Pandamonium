from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

MILESTONE_MARKER = "[[ODYSSEUS_MILESTONE]]"


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _hermes_run_features(features: dict[str, Any]) -> dict[str, bool]:
    """Normalize Hermes capability names across compatible API revisions."""
    return {
        "runs": bool(features.get("run_submission") and features.get("run_events_sse")),
        "stop": bool(features.get("run_stop")),
        "approvals": bool(features.get("run_approval_response") or features.get("run_approval")),
    }


def _validated_approval_choice(task: dict[str, Any], payload: dict[str, Any]) -> str:
    choice = str(payload.get("choice") or "deny")
    if choice not in {"once", "session", "always", "deny"}:
        raise ValueError("invalid_approval_choice")
    if str(task.get("permission_mode") or "read_only") == "read_only" and choice != "deny":
        raise PermissionError("read_only_task_approval_must_deny")
    return choice


def _require_read_only_task(task: dict[str, Any]) -> None:
    if str(task.get("permission_mode") or "") != "read_only" or bool(task.get("approved")):
        raise PermissionError("public_tasks_read_only")


def _last_remote_event_id(task: dict[str, Any]) -> str:
    for event in reversed(task.get("events") or []):
        metadata = event.get("metadata") or {}
        remote_event_id = str(metadata.get("remote_event_id") or "")
        if remote_event_id:
            return remote_event_id
    return ""


def _hermes_instructions(task: dict[str, Any]) -> str:
    base = (
        "You are Hermes working for Leo through Jarvis. Give factual milestone updates and a clear final result. "
        "Never claim an action completed without tool evidence. "
        "Only after a subtask is complete and verified by tool evidence, you may emit one reasoning update as "
        f"{MILESTONE_MARKER} <one completed-subtask update>. Do not use that marker for plans, activity, "
        "commands, estimates, or the final result. "
    )
    return base + (
        "This run is read-only. Do not attempt file changes, installs, deletes, service operations, or other side effects."
    )


class WorkerAdapter(Protocol):
    worker: str
    enabled: bool

    async def start(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def status(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...
    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def health(self) -> dict[str, Any]: ...


class CodexBridgeAdapter:
    def __init__(self, worker: str, url: str, token_file: Path, *, enabled: bool, machine: str):
        self.worker = worker
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = machine

    def _headers(self) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise RuntimeError(f"{self.worker}_token_missing")
        return {"Authorization": f"Bearer {token}"}

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_read_only_task(task)
        payload = {
            "session_id": task["session_id"],
            "workspace": task["workspace"],
            "prompt": task["prompt"],
            "permission_mode": task["permission_mode"],
            "approved": task.get("approved", False),
            "codex_thread_id": task.get("codex_thread_id"),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.url}/v1/tasks", json=payload, headers=self._headers())
        response.raise_for_status()
        remote = response.json()
        return {
            "remote_task_id": remote["task_id"],
            "status": remote.get("status", "queued"),
            "codex_thread_id": remote.get("codex_thread_id"),
        }

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/tasks/{task['remote_task_id']}",
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        headers = self._headers()
        last_event_id = _last_remote_event_id(task)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{self.url}/v1/tasks/{task['remote_task_id']}/events",
                params={"after": -1},
                headers=headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    event.pop("seq", None)
                    event["worker"] = self.worker
                    metadata = dict(event.get("metadata") or {})
                    remote_event_id = str(event.get("event_id") or "")
                    if remote_event_id:
                        metadata["remote_event_id"] = remote_event_id
                    thread_id = metadata.get("codex_thread_id")
                    if thread_id:
                        metadata["codex_deep_link"] = f"codex://threads/{thread_id}"
                    event["metadata"] = metadata
                    yield event

    async def _action(self, task: dict[str, Any], action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/tasks/{task['remote_task_id']}/{action}",
                json=payload or {},
                headers=self._headers(),
            )
        response.raise_for_status()
        return response.json()

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "reply", payload)

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "steer", payload)

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "choice": _validated_approval_choice(task, payload)}
        return await self._action(task, "approval", payload)

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "cancel")

    async def health(self) -> dict[str, Any]:
        try:
            self._headers()
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.url}/health")
            response.raise_for_status()
            return {"state": "connected", "machine": self.machine, **response.json()}
        except Exception as exc:
            state = "auth_required" if "token_missing" in str(exc) or "401" in str(exc) else "unreachable"
            return {"state": state, "machine": self.machine, "error": str(exc)[:160]}


class HermesRunsAdapter:
    worker = "hermes"

    def __init__(self, url: str, token_file: Path, *, enabled: bool):
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = "Hermes laptop"

    def _headers(self, task: dict[str, Any] | None = None) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise RuntimeError("hermes_token_missing")
        headers = {"Authorization": f"Bearer {token}"}
        if task and task.get("worker_session_key"):
            headers["X-Hermes-Session-Key"] = task["worker_session_key"]
        return headers

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_read_only_task(task)
        session_key = task.get("worker_session_key") or f"odysseus:{task['session_id']}:{task['workspace']}"
        task["worker_session_key"] = session_key[:256]
        payload = {
            "input": task["prompt"],
            "session_id": session_key,
            "instructions": _hermes_instructions(task),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{self.url}/v1/runs", json=payload, headers=self._headers(task))
        response.raise_for_status()
        remote = response.json()
        return {"remote_task_id": remote["run_id"], "status": "queued", "worker_session_key": session_key}

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/runs/{task['remote_task_id']}",
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        headers = self._headers(task)
        last_event_id = _last_remote_event_id(task)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET",
                f"{self.url}/v1/runs/{task['remote_task_id']}/events",
                headers=headers,
            ) as response:
                response.raise_for_status()
                sse_event_id = ""
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        sse_event_id = line[3:].strip()
                        continue
                    if not line.startswith("data: "):
                        continue
                    raw = json.loads(line[6:])
                    event = self._normalize(raw)
                    if event:
                        remote_event_id = str(
                            sse_event_id or raw.get("event_id") or raw.get("id") or ""
                        )
                        metadata = dict(event.get("metadata") or {})
                        if remote_event_id:
                            metadata["remote_event_id"] = remote_event_id
                        event["metadata"] = metadata
                        event["event_id"] = remote_event_id or str(uuid.uuid4())
                        yield event
                    sse_event_id = ""

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(raw.get("event") or "")
        if kind == "tool.started":
            return {"type": "tool_activity", "text": f"Hermes started {raw.get('tool') or 'a tool'}.", "metadata": raw}
        if kind == "tool.completed":
            return {"type": "tool_activity", "text": f"Hermes completed {raw.get('tool') or 'a tool'}.", "metadata": raw}
        if kind == "reasoning.available" and raw.get("text"):
            text = str(raw["text"]).strip()
            remainder = text[len(MILESTONE_MARKER):] if text.startswith(MILESTONE_MARKER) else None
            milestone = remainder is not None and (not remainder or remainder[0].isspace())
            if milestone:
                text = remainder.strip()
            if not text:
                return None
            metadata = {"source_event": kind}
            if milestone:
                metadata["milestone"] = True
            return {"type": "progress", "text": text, "metadata": metadata}
        if kind == "approval.request":
            text = str(raw.get("description") or "Hermes needs approval before continuing.")
            return {"type": "approval_required", "text": text, "metadata": raw}
        if kind == "run.completed":
            return {"type": "result", "text": str(raw.get("output") or "Hermes completed the run."), "metadata": {"usage": raw.get("usage")}}
        if kind == "run.failed":
            return {"type": "error", "text": str(raw.get("error") or "Hermes run failed."), "metadata": {"source_event": kind}}
        if kind == "run.cancelled":
            return {"type": "cancelled", "text": "Hermes run cancelled.", "metadata": {"source_event": kind}}
        return None

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("hermes_run_reply_not_supported")

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("hermes_run_steer_not_supported")

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        choice = _validated_approval_choice(task, payload)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/runs/{task['remote_task_id']}/approval",
                json={"choice": choice, "resolve_all": False},
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/runs/{task['remote_task_id']}/stop",
                json={},
                headers=self._headers(task),
            )
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                public = await client.get(f"{self.url}/health")
                public.raise_for_status()
                capabilities = await client.get(f"{self.url}/v1/capabilities", headers=self._headers())
            capabilities.raise_for_status()
            features = capabilities.json().get("features") or {}
            return {
                "state": "connected",
                "machine": self.machine,
                "version": public.json().get("version"),
                **_hermes_run_features(features),
            }
        except Exception as exc:
            state = "auth_required" if "token_missing" in str(exc) or "401" in str(exc) else "unreachable"
            return {"state": state, "machine": self.machine, "error": str(exc)[:160]}


PC_TOKEN_FILE = Path(os.getenv("ODYSSEUS_AGENT_BRIDGE_TOKEN_FILE", "/etc/odysseus-agent-bridge-token"))
HERMES_TOKEN_FILE = Path(os.getenv("ODYSSEUS_HERMES_TOKEN_FILE", "/etc/odysseus-hermes-token"))
VPS_TOKEN_FILE = Path(os.getenv("ODYSSEUS_VPS_WORKER_TOKEN_FILE", "/etc/odysseus-vps-worker-token"))


def adapters() -> dict[str, WorkerAdapter]:
    return {
        "pc-codex": CodexBridgeAdapter(
            "pc-codex",
            os.getenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:8040"),
            PC_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_PC_CODEX_ENABLED", False),
            machine="Local workstation",
        ),
        "hermes": HermesRunsAdapter(
            os.getenv("ODYSSEUS_HERMES_URL", "http://127.0.0.1:8642"),
            HERMES_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_HERMES_ENABLED", False),
        ),
        "vps-codex": CodexBridgeAdapter(
            "vps-codex",
            os.getenv("ODYSSEUS_VPS_CODEX_URL", "http://127.0.0.1:8650"),
            VPS_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_VPS_CODEX_ENABLED", False),
            machine="Remote server",
        ),
    }


def worker_catalog() -> dict[str, dict[str, Any]]:
    registry = adapters()
    return {
        "pc-codex": {
            "enabled": registry["pc-codex"].enabled,
            "machine": "Local workstation",
            "capabilities": ["local_files", "madpanda3d", "business", "home_lab", "code", "artifacts"],
            "workspaces": ["madpanda3d", "business", "home-lab", "project-linux"],
        },
        "hermes": {
            "enabled": registry["hermes"].enabled,
            "machine": "Hermes laptop",
            "capabilities": ["remote_agent", "approvals", "session_memory"],
            "workspaces": ["home-lab"],
        },
        "vps-codex": {
            "enabled": registry["vps-codex"].enabled,
            "machine": "Remote server",
            "capabilities": ["vps_code", "vps_observer", "vps_operations"],
            "workspaces": ["vps-ops"],
        },
    }
