from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.agent_identity import configured_agent_name

MILESTONE_MARKER = "[[ODYSSEUS_MILESTONE]]"
WORKER_IDS = ("pc-codex", "hermes", "vps-codex")
_WORKSPACE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class WorkerUnavailable(RuntimeError):
    """Stable, non-sensitive failure raised at the public broker boundary."""


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _worker_label(name: str, default: str) -> str:
    """Return a bounded installation-owned label for a fixed adapter slot."""
    value = str(os.getenv(name) or default).strip()
    value = " ".join(value.split())
    return value[:80] or default


def configured_worker_workspaces() -> dict[str, list[str]]:
    """Return installation-owned workspace aliases; public defaults are empty."""
    raw = os.getenv("ODYSSEUS_WORKER_WORKSPACES_JSON", "").strip()
    if not raw:
        return {worker: [] for worker in WORKER_IDS}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid_worker_workspace_configuration") from exc
    if not isinstance(payload, dict) or any(worker not in WORKER_IDS for worker in payload):
        raise RuntimeError("invalid_worker_workspace_configuration")
    configured = {worker: [] for worker in WORKER_IDS}
    for worker, values in payload.items():
        if (
            not isinstance(values, list)
            or len(values) > 32
            or any(not isinstance(value, str) or not _WORKSPACE_NAME.fullmatch(value) for value in values)
        ):
            raise RuntimeError("invalid_worker_workspace_configuration")
        configured[worker] = list(dict.fromkeys(values))
    return configured


def _token(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _health_failure(exc: Exception) -> dict[str, str]:
    """Return a stable, non-sensitive connection failure classification."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    error_code = str(exc) if isinstance(exc, RuntimeError) else ""
    if error_code.endswith("_token_missing"):
        return {"state": "auth_required", "reason": "token_missing"}
    if status_code in {401, 403}:
        return {"state": "auth_required", "reason": "authentication_failed"}
    return {"state": "unreachable", "reason": "connection_failed"}


def _hermes_run_features(features: dict[str, Any]) -> dict[str, bool]:
    """Normalize Hermes capability names across compatible API revisions."""
    return {
        "runs": bool(features.get("run_submission") and features.get("run_events_sse")),
        "stop": bool(features.get("run_stop")),
        "approvals": bool(features.get("run_approval_response") or features.get("run_approval")),
    }


def require_worker_task_permission(permission_mode: str, approved: bool) -> None:
    is_approved = approved is True
    if permission_mode == "read_only" and not is_approved:
        return
    if permission_mode == "workspace_write" and _enabled(
        "ODYSSEUS_PRIVATE_WORKER_MUTATIONS", False
    ):
        if not is_approved:
            raise PermissionError("approval_required")
        return
    raise PermissionError("public_tasks_read_only")


def require_read_only(permission_mode: str, approved: bool) -> None:
    """Reject mutation and caller pre-approval on the public Voice Orb API."""
    if permission_mode == "read_only" and approved is False:
        return
    raise PermissionError("public_tasks_read_only")


def _validated_approval_choice(task: dict[str, Any], payload: dict[str, Any]) -> str:
    permission_mode = str(task.get("permission_mode") or "read_only")
    require_worker_task_permission(permission_mode, task.get("approved") is True)
    choice = str(payload.get("choice") or "deny")
    if choice not in {"once", "session", "always", "deny"}:
        raise ValueError("invalid_approval_choice")
    if permission_mode == "read_only" and choice != "deny":
        raise PermissionError("read_only_task_approval_must_deny")
    return choice


def _require_worker_task_permission(task: dict[str, Any]) -> None:
    require_worker_task_permission(
        str(task.get("permission_mode") or ""),
        task.get("approved") is True,
    )


def _last_remote_event_id(task: dict[str, Any]) -> str:
    for event in reversed(task.get("events") or []):
        metadata = event.get("metadata") or {}
        remote_event_id = str(metadata.get("remote_event_id") or "")
        if remote_event_id:
            return remote_event_id
    return ""


def _hermes_instructions(task: dict[str, Any]) -> str:
    operator = str(task.get("owner") or "the authenticated operator")
    base = (
        f"You are the selected Hermes worker operating for {operator} through {configured_agent_name()} and "
        "Pandamonium. Give factual milestone updates and a clear final result. "
        "Never claim an action completed without tool evidence. "
        "Only after a subtask is complete and verified by tool evidence, you may emit one reasoning update as "
        f"{MILESTONE_MARKER} <one completed-subtask update>. Do not use that marker for plans, activity, "
        "commands, estimates, or the final result. "
    )
    if (
        _enabled("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", False)
        and task.get("permission_mode") == "workspace_write"
        and task.get("approved") is True
    ):
        return base + (
            "Pandamonium approved this task at the broker level. You may attempt only the specifically requested "
            "mutation using normal Hermes tools. Do not bypass or suppress Hermes' native tool approval gate; "
            "no other side effects are authorized."
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
    adapter_name = "codex-bridge"

    def __init__(
        self,
        worker: str,
        url: str,
        token_file: Path,
        *,
        enabled: bool,
        machine: str,
        label: str | None = None,
        workspaces: list[str] | None = None,
    ):
        self.worker = worker
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = machine
        self.label = label or ("PC Codex" if worker == "pc-codex" else "VPS Codex")
        self.configured_workspaces = list(workspaces or [])

    def _headers(self) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise RuntimeError(f"{self.worker}_token_missing")
        return {"Authorization": f"Bearer {token}"}

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_worker_task_permission(task)
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
            payload = response.json()
            payload = payload if isinstance(payload, dict) else {}
            return {
                "state": "connected",
                "machine": self.machine,
                "protocol": "codex-bridge",
                "protocol_ready": bool(payload.get("app_server")),
            }
        except Exception as exc:
            return {"machine": self.machine, **_health_failure(exc)}


class HermesRunsAdapter:
    worker = "hermes"
    adapter_name = "hermes-runs"

    def __init__(
        self,
        url: str,
        token_file: Path,
        *,
        enabled: bool,
        label: str = "Hermes",
        workspaces: list[str] | None = None,
    ):
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.enabled = enabled
        self.machine = "Hermes laptop"
        self.label = label
        self.configured_workspaces = list(workspaces or [])

    def _headers(self, task: dict[str, Any] | None = None) -> dict[str, str]:
        token = _token(self.token_file)
        if not token:
            raise RuntimeError("hermes_token_missing")
        headers = {"Authorization": f"Bearer {token}"}
        if task and task.get("worker_session_key"):
            headers["X-Hermes-Session-Key"] = task["worker_session_key"]
        return headers

    async def direct_chat(
        self,
        *,
        session_id: str,
        session_key: str,
        message: str,
    ) -> str:
        """Run one persistent foreground turn through Gordon's native agent."""
        if not self.enabled:
            raise RuntimeError("hermes_not_connected")
        headers = self._headers()
        headers["X-Hermes-Session-Id"] = session_id[:256]
        headers["X-Hermes-Session-Key"] = session_key[:256]
        payload = {
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                f"{self.url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        content = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else ""
        )
        reply = re.sub(
            r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>",
            "",
            str(content or ""),
            flags=re.IGNORECASE,
        ).strip()
        if not reply:
            raise RuntimeError("hermes_direct_chat_empty")
        return reply

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        _require_worker_task_permission(task)
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
            capabilities_payload = capabilities.json()
            features = (
                capabilities_payload.get("features") or {}
                if isinstance(capabilities_payload, dict)
                else {}
            )
            permission_profile = (
                capabilities_payload.get("permission_profile")
                if isinstance(capabilities_payload, dict)
                else None
            )
            if permission_profile != "read_only_enforced" and features.get("read_only_enforced") is not True:
                return {"state": "incompatible", "reason": "read_only_not_enforced"}
            public_payload = public.json()
            public_payload = public_payload if isinstance(public_payload, dict) else {}
            result = {
                "state": "connected",
                "machine": self.machine,
                "protocol": "hermes-runs",
                **_hermes_run_features(features),
            }
            version = str(public_payload.get("version") or "").strip()
            if version:
                result["version"] = version[:80]
            return result
        except Exception as exc:
            return {"machine": self.machine, **_health_failure(exc)}


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
            label=_worker_label("ODYSSEUS_PC_CODEX_LABEL", "PC Codex"),
        ),
        "hermes": HermesRunsAdapter(
            os.getenv("ODYSSEUS_HERMES_URL", "http://127.0.0.1:8642"),
            HERMES_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_HERMES_ENABLED", False),
            label=_worker_label("ODYSSEUS_HERMES_LABEL", "Hermes"),
        ),
        "vps-codex": CodexBridgeAdapter(
            "vps-codex",
            os.getenv("ODYSSEUS_VPS_CODEX_URL", "http://127.0.0.1:8650"),
            VPS_TOKEN_FILE,
            enabled=_enabled("ODYSSEUS_VPS_CODEX_ENABLED", False),
            machine="Remote server",
            label=_worker_label("ODYSSEUS_VPS_CODEX_LABEL", "VPS Codex"),
        ),
    }


def worker_catalog(
    registry: dict[str, WorkerAdapter] | None = None,
) -> dict[str, dict[str, Any]]:
    registry = registry or adapters()
    workspaces = configured_worker_workspaces()
    metadata = {
        "pc-codex": ("codex-bridge", "Local workstation", ["read_only_inspection", "code", "artifacts"]),
        "hermes": ("hermes-runs", "Remote agent", ["remote_agent", "approvals", "session_memory"]),
        "vps-codex": ("codex-bridge", "Remote server", ["read_only_inspection"]),
    }
    result: dict[str, dict[str, Any]] = {}
    for worker, adapter in registry.items():
        adapter_name, machine, capabilities = metadata.get(
            worker,
            (getattr(adapter, "adapter_name", "worker"), "Configured worker", ["read_only"]),
        )
        result[worker] = {
            "id": worker,
            "label": getattr(adapter, "label", worker),
            "enabled": bool(adapter.enabled),
            "configured": bool(adapter.enabled),
            "ready": False,
            "adapter": getattr(adapter, "adapter_name", adapter_name),
            "machine": machine,
            "capabilities": capabilities,
            "workspaces": list(
                getattr(adapter, "configured_workspaces", None)
                or workspaces.get(worker, [])
            ),
        }
    return result
