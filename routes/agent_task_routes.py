"""Authenticated public API for fixed read-only Voice Orb workers."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from src.agent_worker_adapters import WorkerUnavailable, require_read_only
from src.agent_worker_broker import (
    configure,
    list_tasks,
    refresh_task,
    require_task_owner,
    start_task,
    stream_task_events,
    task_action,
    worker_statuses,
)
from src.auth_helpers import require_user


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(_StrictModel):
    worker: Literal["pc-codex", "hermes", "vps-codex"]
    session_id: str = Field(min_length=1, max_length=128)
    workspace: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    prompt: str = Field(min_length=1, max_length=50_000)
    permission_mode: str = "read_only"
    approved: bool = False


class TaskSteer(_StrictModel):
    prompt: str = Field(min_length=1, max_length=50_000)


class TaskReply(_StrictModel):
    answers: dict[str, list[str] | str]


class TaskApproval(_StrictModel):
    choice: Literal["once", "session", "always", "deny"]


def _interactive_user(request: Request) -> str:
    owner = require_user(request)
    if not owner:
        raise HTTPException(401, "Interactive sign-in is required for worker orchestration")
    return owner


def _same_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(403, "Cross-site worker requests are not allowed")


def _answers(payload: TaskReply) -> dict[str, list[str] | str]:
    if len(payload.answers) > 20:
        raise HTTPException(422, "Too many worker answers")
    total = 0
    normalized: dict[str, list[str] | str] = {}
    for raw_key, raw_value in payload.answers.items():
        key = str(raw_key).strip()
        if not key or len(key) > 128:
            raise HTTPException(422, "Invalid worker answer key")
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        if len(values) > 20 or any(not isinstance(value, str) or len(value) > 4_000 for value in values):
            raise HTTPException(422, "Worker answer is too large")
        total += sum(len(value) for value in values)
        normalized[key] = values if isinstance(raw_value, list) else values[0]
    if total > 20_000:
        raise HTTPException(422, "Worker answers are too large")
    return normalized


def _task_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, "Worker task not found")
    if isinstance(exc, PermissionError):
        if str(exc) == "read_only_task_approval_must_deny":
            return HTTPException(403, "Read-only tasks can only deny approval requests")
        if str(exc) == "public_tasks_read_only":
            return HTTPException(403, "Public worker tasks are read-only")
        return HTTPException(403, "Worker task does not belong to this user")
    if isinstance(exc, WorkerUnavailable):
        if str(exc) == "worker_not_configured":
            return HTTPException(409, "Worker is not configured")
        return HTTPException(503, "Worker is not ready")
    if isinstance(exc, ValueError):
        return HTTPException(400, "Worker request is invalid")
    return HTTPException(502, "Worker request failed")


def setup_agent_task_routes(session_manager) -> APIRouter:
    configure(session_manager)
    router = APIRouter(tags=["voice-orb-workers"])

    @router.get("/api/agent-workers")
    async def workers(_owner: str = Depends(_interactive_user)):
        return await worker_statuses()

    @router.get("/api/agent-tasks")
    async def tasks(
        session_id: str = Query(min_length=1, max_length=128),
        active_only: bool = False,
        owner: str = Depends(_interactive_user),
    ):
        try:
            return {"tasks": list_tasks(session_id, owner, active_only=active_only)}
        except Exception as exc:
            raise _task_error(exc) from None

    @router.post("/api/agent-tasks")
    async def create(
        payload: TaskCreate,
        request: Request,
        owner: str = Depends(_interactive_user),
    ):
        _same_origin(request)
        try:
            require_read_only(payload.permission_mode, payload.approved)
            return await start_task(**payload.model_dump(), owner=owner)
        except Exception as exc:
            raise _task_error(exc) from None

    @router.get("/api/agent-tasks/{task_id}")
    async def read(task_id: str, owner: str = Depends(_interactive_user)):
        try:
            return await refresh_task(task_id, owner=owner)
        except Exception as exc:
            raise _task_error(exc) from None

    @router.get("/api/agent-tasks/{task_id}/events")
    async def events(
        task_id: str,
        request: Request,
        after: int | None = Query(default=None, ge=-1),
        owner: str = Depends(_interactive_user),
    ):
        try:
            require_task_owner(task_id, owner)
            if after is None:
                try:
                    after = max(-1, int(request.headers.get("last-event-id", "-1")))
                except ValueError:
                    after = -1
        except Exception as exc:
            raise _task_error(exc) from None
        return StreamingResponse(
            stream_task_events(task_id, after, owner=owner),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/agent-tasks/{task_id}/steer")
    async def steer(
        task_id: str,
        payload: TaskSteer,
        request: Request,
        owner: str = Depends(_interactive_user),
    ):
        _same_origin(request)
        try:
            return await task_action(task_id, "steer", payload.model_dump(), owner=owner)
        except Exception as exc:
            raise _task_error(exc) from None

    @router.post("/api/agent-tasks/{task_id}/reply")
    async def reply(
        task_id: str,
        payload: TaskReply,
        request: Request,
        owner: str = Depends(_interactive_user),
    ):
        _same_origin(request)
        try:
            return await task_action(task_id, "reply", {"answers": _answers(payload)}, owner=owner)
        except HTTPException:
            raise
        except Exception as exc:
            raise _task_error(exc) from None

    @router.post("/api/agent-tasks/{task_id}/approval")
    async def approval(
        task_id: str,
        payload: TaskApproval,
        request: Request,
        owner: str = Depends(_interactive_user),
    ):
        _same_origin(request)
        try:
            return await task_action(task_id, "approval", payload.model_dump(), owner=owner)
        except Exception as exc:
            raise _task_error(exc) from None

    @router.post("/api/agent-tasks/{task_id}/cancel")
    async def cancel(
        task_id: str,
        request: Request,
        owner: str = Depends(_interactive_user),
    ):
        _same_origin(request)
        try:
            return await task_action(task_id, "cancel", owner=owner)
        except Exception as exc:
            raise _task_error(exc) from None

    return router
