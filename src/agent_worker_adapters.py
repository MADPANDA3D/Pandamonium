"""Fixed, read-only worker adapters for the public Voice Orb broker."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

WORKER_IDS = ("pc-codex", "hermes", "vps-codex")
EVENT_TYPES = {
    "accepted",
    "progress",
    "tool_activity",
    "question",
    "approval_required",
    "result",
    "error",
    "cancelled",
}


class WorkerUnavailable(RuntimeError):
    """A stable, non-sensitive worker failure code."""


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> list[str]:
    return _logical_names(os.getenv(name, "").split(","))


def _logical_names(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    names = []
    for value in values:
        name = str(value or "").strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            names.append(name)
    return sorted(set(names))


def _label(name: str, default: str) -> str:
    value = " ".join(os.getenv(name, default).split())[:80]
    return value or default


def _token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def require_read_only(permission_mode: str, approved: bool) -> None:
    """Reject mutation and caller-preapproval at every adapter boundary."""
    if permission_mode == "read_only" and approved is False:
        return
    raise PermissionError("public_tasks_read_only")


def _last_remote_event_id(task: dict[str, Any]) -> str:
    for event in reversed(task.get("events") or []):
        remote_id = str((event.get("metadata") or {}).get("remote_event_id") or "")
        if remote_id:
            return remote_id
    return ""


def _safe_failure(exc: Exception) -> dict[str, str]:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    code = str(exc) if isinstance(exc, WorkerUnavailable) else ""
    if code in {"token_missing", "authentication_failed"} or status in {401, 403}:
        return {"state": "auth_required", "reason": "authentication_failed"}
    if code in {"read_only_not_enforced", "protocol_incompatible", "workspace_required"}:
        return {"state": "incompatible", "reason": code}
    return {"state": "unreachable", "reason": "connection_failed"}


def _workspace_intersection(remote: Any, configured: list[str]) -> list[str]:
    advertised = _logical_names(remote)
    if configured and advertised:
        return sorted(set(configured).intersection(advertised))
    return configured or advertised


def _safe_event(raw: Any, worker: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    event_type = str(raw.get("type") or "")
    event_id = str(raw.get("event_id") or "").strip()
    if event_type not in EVENT_TYPES or not event_id:
        return None
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    safe_metadata = {
        key: metadata[key]
        for key in ("codex_thread_id", "milestone", "questions")
        if key in metadata
    }
    safe_metadata["remote_event_id"] = event_id[:200]
    return {
        "event_id": event_id[:200],
        "worker": worker,
        "type": event_type,
        "text": str(raw.get("text") or "")[:12_000],
        "metadata": safe_metadata,
    }


class WorkerAdapter(Protocol):
    worker: str
    enabled: bool
    adapter_name: str
    label: str
    configured_workspaces: list[str]

    async def start(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def status(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...
    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]: ...
    async def health(self) -> dict[str, Any]: ...


class CodexBridgeAdapter:
    adapter_name = "codex-bridge"

    def __init__(
        self,
        worker: str,
        url: str,
        token_file: Path,
        *,
        enabled: bool,
        label: str,
        workspaces: list[str],
    ):
        if worker not in {"pc-codex", "vps-codex"}:
            raise ValueError("unknown_worker")
        self.worker = worker
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.label = label
        self.configured_workspaces = workspaces

    def _headers(self) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise WorkerUnavailable("token_missing")
        return {"Authorization": f"Bearer {token}"}

    async def _profile(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{self.url}/health", headers=self._headers())
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("permission_profile") != "read_only_enforced":
            raise WorkerUnavailable("read_only_not_enforced")
        if payload.get("protocol") != "odysseus-worker-v1":
            raise WorkerUnavailable("protocol_incompatible")
        workspaces = _workspace_intersection(payload.get("workspaces"), self.configured_workspaces)
        if not workspaces:
            raise WorkerUnavailable("workspace_required")
        capabilities = _logical_names(payload.get("capabilities"))
        return {"workspaces": workspaces, "capabilities": capabilities}

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        require_read_only(str(task.get("permission_mode") or ""), task.get("approved") is True)
        profile = await self._profile()
        if task.get("workspace") not in profile["workspaces"]:
            raise ValueError("unknown_workspace")
        payload = {
            "session_id": task["session_id"],
            "workspace": task["workspace"],
            "prompt": task["prompt"],
            "permission_mode": "read_only",
            "approved": False,
            "codex_thread_id": task.get("codex_thread_id"),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.url}/v1/tasks", json=payload, headers=self._headers()
            )
        response.raise_for_status()
        remote = response.json()
        if not isinstance(remote, dict) or not remote.get("task_id"):
            raise WorkerUnavailable("protocol_incompatible")
        return {
            "remote_task_id": str(remote["task_id"]),
            "status": str(remote.get("status") or "queued"),
            "codex_thread_id": remote.get("codex_thread_id"),
        }

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/tasks/{task['remote_task_id']}", headers=self._headers()
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

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
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = _safe_event(json.loads(line[5:].strip()), self.worker)
                    except json.JSONDecodeError:
                        continue
                    if event:
                        yield event

    async def _action(
        self, task: dict[str, Any], action: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/tasks/{task['remote_task_id']}/{action}",
                json=payload or {},
                headers=self._headers(),
            )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "steer", payload)

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "reply", payload)

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("choice") != "deny":
            raise PermissionError("read_only_task_approval_must_deny")
        return await self._action(task, "approval", {"choice": "deny"})

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "cancel")

    async def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"state": "disabled", "reason": "not_configured"}
        try:
            profile = await self._profile()
            return {
                "state": "connected",
                "permission_profile": "read_only_enforced",
                **profile,
            }
        except Exception as exc:
            return _safe_failure(exc)


class HermesRunsAdapter:
    worker = "hermes"
    adapter_name = "hermes-runs"

    def __init__(
        self,
        url: str,
        token_file: Path,
        *,
        enabled: bool,
        label: str,
        workspaces: list[str],
    ):
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.label = label
        self.configured_workspaces = workspaces

    def _headers(self, task: dict[str, Any] | None = None) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise WorkerUnavailable("token_missing")
        headers = {"Authorization": f"Bearer {token}"}
        if task and task.get("worker_session_key"):
            headers["X-Hermes-Session-Key"] = str(task["worker_session_key"])
        return headers

    async def _profile(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{self.url}/v1/capabilities", headers=self._headers()
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise WorkerUnavailable("protocol_incompatible")
        features = payload.get("features")
        if not isinstance(features, dict):
            raise WorkerUnavailable("protocol_incompatible")
        permission_profile = payload.get("permission_profile")
        if permission_profile != "read_only_enforced" and features.get("read_only_enforced") is not True:
            raise WorkerUnavailable("read_only_not_enforced")
        if not (features.get("run_submission") and features.get("run_events_sse") and features.get("run_stop")):
            raise WorkerUnavailable("protocol_incompatible")
        workspaces = _workspace_intersection(payload.get("workspaces"), self.configured_workspaces)
        if not workspaces:
            raise WorkerUnavailable("workspace_required")
        capabilities = ["tasks", "events", "cancel", "read_only"]
        if features.get("run_steer"):
            capabilities.append("steer")
        if features.get("run_reply"):
            capabilities.append("reply")
        if features.get("run_approval_response"):
            capabilities.append("deny_approval")
        return {"workspaces": workspaces, "capabilities": capabilities}

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        require_read_only(str(task.get("permission_mode") or ""), task.get("approved") is True)
        profile = await self._profile()
        if task.get("workspace") not in profile["workspaces"]:
            raise ValueError("unknown_workspace")
        session_key = str(task.get("worker_session_key") or f"odysseus:{task['session_id']}:{task['workspace']}")[:256]
        payload = {
            "input": task["prompt"],
            "session_id": session_key,
            "instructions": (
                "This run is read-only. Report factual progress and one final result. "
                "Do not attempt file changes, installs, service operations, or other side effects."
            ),
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.url}/v1/runs", json=payload, headers=self._headers(task)
            )
        response.raise_for_status()
        remote = response.json()
        if not isinstance(remote, dict) or not remote.get("run_id"):
            raise WorkerUnavailable("protocol_incompatible")
        return {
            "remote_task_id": str(remote["run_id"]),
            "status": "queued",
            "worker_session_key": session_key,
        }

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.url}/v1/runs/{task['remote_task_id']}", headers=self._headers(task)
            )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize(raw: dict[str, Any], event_id: str) -> dict[str, Any] | None:
        kind = str(raw.get("event") or "")
        if kind == "tool.started":
            event_type, text = "tool_activity", "Hermes started a tool."
        elif kind == "tool.completed":
            event_type, text = "tool_activity", "Hermes completed a tool."
        elif kind == "reasoning.available" and raw.get("text"):
            event_type, text = "progress", str(raw["text"])
        elif kind == "approval.request":
            event_type = "approval_required"
            text = str(raw.get("description") or "Hermes requested approval.")
        elif kind == "run.completed":
            event_type, text = "result", str(raw.get("output") or "Hermes completed the run.")
        elif kind == "run.failed":
            event_type, text = "error", "Hermes could not complete the run."
        elif kind == "run.cancelled":
            event_type, text = "cancelled", "Hermes cancelled the run."
        else:
            return None
        return {
            "event_id": event_id,
            "worker": "hermes",
            "type": event_type,
            "text": text[:12_000],
            "metadata": {"remote_event_id": event_id},
        }

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        headers = self._headers(task)
        last_event_id = _last_remote_event_id(task)
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{self.url}/v1/runs/{task['remote_task_id']}/events", headers=headers
            ) as response:
                response.raise_for_status()
                event_id = ""
                async for line in response.aiter_lines():
                    if line.startswith("id:"):
                        event_id = line[3:].strip()[:200]
                    elif line.startswith("data:"):
                        try:
                            raw = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            raw = None
                        event = self._normalize(raw, event_id) if isinstance(raw, dict) and event_id else None
                        if event:
                            yield event
                        event_id = ""

    async def _action(
        self, task: dict[str, Any], action: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{self.url}/v1/runs/{task['remote_task_id']}/{action}",
                json=payload or {},
                headers=self._headers(task),
            )
        response.raise_for_status()
        value = response.json()
        return value if isinstance(value, dict) else {}

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "steer", payload)

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "reply", payload)

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("choice") != "deny":
            raise PermissionError("read_only_task_approval_must_deny")
        return await self._action(task, "approval", {"choice": "deny", "resolve_all": False})

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        return await self._action(task, "stop")

    async def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"state": "disabled", "reason": "not_configured"}
        try:
            profile = await self._profile()
            return {
                "state": "connected",
                "permission_profile": "read_only_enforced",
                **profile,
            }
        except Exception as exc:
            return _safe_failure(exc)


def adapters() -> dict[str, WorkerAdapter]:
    return {
        "pc-codex": CodexBridgeAdapter(
            "pc-codex",
            os.getenv("ODYSSEUS_PC_CODEX_URL", "http://127.0.0.1:8040"),
            Path(os.getenv("ODYSSEUS_PC_CODEX_TOKEN_FILE", "/run/secrets/odysseus_pc_codex_token")),
            enabled=_enabled("ODYSSEUS_PC_CODEX_ENABLED"),
            label=_label("ODYSSEUS_PC_CODEX_LABEL", "PC Codex"),
            workspaces=_csv("ODYSSEUS_PC_CODEX_WORKSPACES"),
        ),
        "hermes": HermesRunsAdapter(
            os.getenv("ODYSSEUS_HERMES_URL", "http://127.0.0.1:8642"),
            Path(os.getenv("ODYSSEUS_HERMES_TOKEN_FILE", "/run/secrets/odysseus_hermes_token")),
            enabled=_enabled("ODYSSEUS_HERMES_ENABLED"),
            label=_label("ODYSSEUS_HERMES_LABEL", "Hermes"),
            workspaces=_csv("ODYSSEUS_HERMES_WORKSPACES"),
        ),
        "vps-codex": CodexBridgeAdapter(
            "vps-codex",
            os.getenv("ODYSSEUS_VPS_CODEX_URL", "http://127.0.0.1:8650"),
            Path(os.getenv("ODYSSEUS_VPS_CODEX_TOKEN_FILE", "/run/secrets/odysseus_vps_codex_token")),
            enabled=_enabled("ODYSSEUS_VPS_CODEX_ENABLED"),
            label=_label("ODYSSEUS_VPS_CODEX_LABEL", "VPS Codex"),
            workspaces=_csv("ODYSSEUS_VPS_CODEX_WORKSPACES"),
        ),
    }


def worker_catalog(registry: dict[str, WorkerAdapter] | None = None) -> dict[str, dict[str, Any]]:
    registry = registry or adapters()
    return {
        worker: {
            "id": worker,
            "label": adapter.label,
            "configured": bool(adapter.enabled),
            "ready": False,
            "adapter": adapter.adapter_name,
            "capabilities": ["read_only"],
            "workspaces": list(adapter.configured_workspaces),
            "connection": {"state": "disabled", "reason": "not_configured"},
        }
        for worker, adapter in registry.items()
    }
