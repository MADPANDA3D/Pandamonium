from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.models import ChatMessage
from src.action_protocol import compose_capability_catalog, normalize_action_call, validate_action_call
from src.agent_identity import configured_agent_id
from src.auth_helpers import require_user
from src.agent_worker_adapters import WorkerUnavailable, adapters, require_worker_task_permission
from src.authority_protocol import authority_store, operator_identity
from src.jarvis_agent import (
    configure,
    internal_token_valid,
    list_session_tasks,
    refresh_task,
    require_task_owner,
    runtime_status,
    search_knowledge,
    start_task,
    stream_task_events,
    sync_knowledge,
    task_action,
    worker_statuses,
)
from routes.madpanda_knowledge_routes import router as madpanda_knowledge_router
from src.operational_protocol import record_operational_event


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker: str = "pc-codex"
    session_id: str
    workspace: str
    prompt: str = Field(min_length=1, max_length=50000)
    permission_mode: str = "read_only"
    approved: bool = False
    persist_prompt: bool = False
    codex_thread_id: str | None = None
    thread_title: str | None = Field(default=None, max_length=200)
    request_id: str | None = Field(default=None, max_length=200)


class TaskSteer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=50000)


class TaskReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, list[str] | str]


class TaskApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: str = Field(pattern="^(once|session|always|deny)$")
    spoken_text: str | None = Field(default=None, max_length=2000)


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    client: str
    mtime: int
    content_hash: str
    document_type: str
    text: str


class KnowledgeSync(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[KnowledgeDocument]


class KnowledgeSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    client: str | None = None
    limit: int = Field(default=6, ge=1, le=12)


def setup_agent_task_routes(session_manager):
    configure(session_manager)
    router = APIRouter(tags=["jarvis-agent"])

    def _authorize_task_control(
        *,
        action: str,
        session_id: str,
        owner: str,
        worker: str,
        workspace: str,
        permission_mode: str,
        request_id: str | None = None,
        task_id: str | None = None,
        codex_thread_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = str(request_id or uuid.uuid4())[:200]
        call = normalize_action_call(
            request_id=request_id,
            call_id=str(uuid.uuid4()),
            agent_id=configured_agent_id(),
            actor="odysseus:codex-workspace",
            capability_version="",
            name="start_agent_task",
            arguments={
                "action": action,
                "worker": worker,
                "workspace": workspace,
                "permission_mode": permission_mode,
                **({"task_id": task_id} if task_id else {}),
                **({"codex_thread_id": codex_thread_id} if codex_thread_id else {}),
            },
            target="worker",
            authority_ref=None,
        )
        call["session_id"] = session_id
        call["operator_id"] = operator_identity(owner)
        catalog = compose_capability_catalog(fallback_names={"start_agent_task"})
        call["capability_version"] = catalog["version"]
        record_operational_event(
            request_id=request_id,
            session_id=session_id,
            task_id=task_id,
            call_id=call["call_id"],
            operator_id=operator_identity(owner),
            actor=call["actor"],
            component="worker",
            event_type="started",
            status="requested",
            metadata={"capability": call["name"], "action": action},
        )
        validation_error = validate_action_call(call, catalog)
        if validation_error:
            record_operational_event(
                request_id=request_id,
                session_id=session_id,
                task_id=task_id,
                call_id=call["call_id"],
                operator_id=operator_identity(owner),
                actor="odysseus:authority",
                component="control_plane",
                event_type="approval",
                status="denied",
                error=validation_error,
            )
            raise PermissionError(validation_error["category"])
        decision = authority_store.decide(
            call,
            operator_id=operator_identity(owner),
            session_id=session_id,
        )
        call["authority_ref"] = decision.get("approval_decision_id") or decision["decision_id"]
        record_operational_event(
            request_id=request_id,
            session_id=session_id,
            task_id=task_id,
            call_id=call["call_id"],
            operator_id=operator_identity(owner),
            actor="odysseus:authority",
            component="control_plane",
            event_type="approval",
            status={"allow": "authorized", "deny": "denied"}.get(
                decision["decision"], "approval_required"
            ),
            evidence_refs=[{"decision_id": decision["decision_id"]}],
            metadata={
                "permission_mode": decision["permission_mode"],
                "action_effect": decision["action_effect"],
                "policy_basis": decision["policy_basis"],
            },
        )
        if decision["decision"] != "allow":
            raise PermissionError("worker_task_not_authorized")
        record_operational_event(
            request_id=request_id,
            session_id=session_id,
            task_id=task_id,
            call_id=call["call_id"],
            operator_id=operator_identity(owner),
            actor=call["actor"],
            component="worker",
            event_type="progress",
            status="executed",
            evidence_refs=[{"decision_id": decision["decision_id"]}],
            metadata={"capability": call["name"], "action": action},
        )
        return call

    def _record_task_control_result(
        trace: dict[str, Any],
        *,
        task: dict[str, Any] | None = None,
        status: str = "succeeded",
        error: Any = None,
    ) -> None:
        arguments = trace.get("arguments") or {}
        task = task or {}
        workspace = str(task.get("workspace") or arguments.get("workspace") or "")
        record_operational_event(
            request_id=trace.get("request_id"),
            session_id=task.get("session_id") or trace.get("session_id"),
            task_id=task.get("task_id") or arguments.get("task_id"),
            call_id=trace.get("call_id"),
            operator_id=operator_identity(task.get("owner")) or trace.get("operator_id"),
            actor=trace.get("actor") or "odysseus:codex-workspace",
            component="worker",
            event_type="result",
            status=status,
            evidence_refs=[{
                "task_id": task.get("task_id") or arguments.get("task_id"),
                "codex_thread_id": task.get("codex_thread_id") or arguments.get("codex_thread_id"),
                "requested_project": workspace or None,
                "approved_root": f"workspace:{workspace}" if workspace and task else None,
                "artifact_ids": [row.get("document_id") for row in task.get("artifacts") or [] if row.get("document_id")],
            }],
            error=error,
            metadata={"capability": trace.get("name"), "action": arguments.get("action")},
        )

    @router.get("/api/agent-workers")
    async def workers(_owner: str = Depends(require_user)):
        return await worker_statuses()

    def _codex_catalog_adapter():
        adapter = adapters().get("pc-codex")
        if not adapter or not hasattr(adapter, "catalog_projects"):
            raise HTTPException(503, "Codex project catalog is unavailable")
        return adapter

    @router.get("/api/codex/projects")
    async def codex_projects(
        query: str = Query(default="", max_length=200),
        cursor: str | None = Query(default=None, max_length=2000),
        limit: int = Query(default=20, ge=1, le=50),
        _owner: str = Depends(require_user),
    ):
        try:
            return await _codex_catalog_adapter().catalog_projects(
                query=query,
                cursor=cursor,
                limit=limit,
            )
        except WorkerUnavailable:
            raise HTTPException(503, "Codex workstation is not configured")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                raise HTTPException(404, "Codex project catalog was not found")
            raise HTTPException(503, "Codex project catalog is unavailable")
        except Exception:
            raise HTTPException(503, "Codex project catalog is unavailable")

    @router.get("/api/codex/projects/{project_id}/tasks")
    async def codex_project_tasks(
        project_id: str,
        query: str = Query(default="", max_length=200),
        cursor: str | None = Query(default=None, max_length=2000),
        limit: int = Query(default=50, ge=1, le=100),
        _owner: str = Depends(require_user),
    ):
        try:
            return await _codex_catalog_adapter().catalog_tasks(
                project_id,
                query=query,
                cursor=cursor,
                limit=limit,
            )
        except ValueError:
            raise HTTPException(400, "Invalid Codex project")
        except WorkerUnavailable:
            raise HTTPException(503, "Codex workstation is not configured")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(404, "Codex project is unavailable or not allowlisted")
            raise HTTPException(503, "Codex task catalog is unavailable")
        except Exception:
            raise HTTPException(503, "Codex task catalog is unavailable")

    @router.post("/api/agent-tasks")
    async def create(payload: TaskCreate, _request: Request, owner: str = Depends(require_user)):
        try:
            require_worker_task_permission(payload.permission_mode, payload.approved)
        except PermissionError as exc:
            detail = (
                "Public worker tasks are read-only"
                if str(exc) == "public_tasks_read_only"
                else str(exc)
            )
            raise HTTPException(403, detail)
        try:
            session = session_manager.get_session(payload.session_id)
        except Exception:
            raise HTTPException(404, "Session not found")
        if getattr(session, "owner", None) != owner:
            raise HTTPException(403, "Session does not belong to this user")
        trace = None
        try:
            values = payload.model_dump()
            persist_prompt = bool(values.pop("persist_prompt", False))
            trace = _authorize_task_control(
                action="resume" if payload.codex_thread_id else "create",
                session_id=payload.session_id,
                owner=owner,
                worker=payload.worker,
                workspace=payload.workspace,
                permission_mode=payload.permission_mode,
                request_id=payload.request_id,
                codex_thread_id=payload.codex_thread_id,
            )
            values.update(
                request_id=trace["request_id"],
                call_id=trace["call_id"],
                authority_ref=trace["authority_ref"],
            )
            task = await start_task(**values, owner=owner)
            _record_task_control_result(trace, task=task)
            if persist_prompt:
                session_manager.add_message(
                    payload.session_id,
                    ChatMessage("user", payload.prompt, metadata={
                        "source": "jarvis_voice",
                        "target": payload.worker,
                    }),
                )
            return task
        except PermissionError as exc:
            if trace:
                _record_task_control_result(trace, status="denied", error=exc)
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            if str(exc) in {"conversation_task_conflict", "conversation_project_mismatch"}:
                raise HTTPException(409, str(exc))
            if str(exc) == "codex_task_execution_disabled":
                raise HTTPException(503, "Codex task execution is disabled")
            raise HTTPException(502, f"Worker request failed: {str(exc)[:300]}")
        except Exception as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            raise HTTPException(502, f"Worker request failed: {str(exc)[:300]}")

    @router.get("/api/agent-tasks")
    async def list_tasks(
        session_id: str = Query(min_length=1, max_length=200),
        limit: int = Query(default=100, ge=1, le=100),
        owner: str = Depends(require_user),
    ):
        try:
            return {"tasks": list_session_tasks(session_id, owner, limit)}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except PermissionError:
            raise HTTPException(403, "Session does not belong to this user")

    @router.get("/api/agent-tasks/{task_id}")
    async def read(task_id: str, owner: str = Depends(require_user)):
        try:
            return await refresh_task(task_id, owner=owner)
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError:
            raise HTTPException(403, "Task does not belong to this user")

    @router.get("/api/agent-tasks/{task_id}/events")
    async def events(
        task_id: str,
        request: Request,
        after: int | None = Query(None),
        owner: str = Depends(require_user),
    ):
        try:
            require_task_owner(task_id, owner)
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError:
            raise HTTPException(403, "Task does not belong to this user")
        if after is None:
            try:
                after = int(request.headers.get("last-event-id", "-1"))
            except ValueError:
                after = -1
        return StreamingResponse(
            stream_task_events(task_id, after, owner=owner),
            media_type="text/event-stream",
        )

    @router.post("/api/agent-tasks/{task_id}/reply")
    async def reply(task_id: str, payload: TaskReply, owner: str = Depends(require_user)):
        try:
            return await task_action(task_id, "reply", payload.model_dump(), owner=owner)
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError:
            raise HTTPException(403, "Task does not belong to this user")
        except Exception as exc:
            raise HTTPException(502, str(exc)[:300])

    @router.post("/api/agent-tasks/{task_id}/steer")
    async def steer(task_id: str, payload: TaskSteer, owner: str = Depends(require_user)):
        trace = None
        try:
            task = require_task_owner(task_id, owner)
            trace = _authorize_task_control(
                action="steer",
                session_id=task["session_id"],
                owner=owner,
                worker=task["worker"],
                workspace=task["workspace"],
                permission_mode=str(task.get("permission_mode") or "read_only"),
                task_id=task_id,
                codex_thread_id=task.get("codex_thread_id"),
            )
            result = await task_action(task_id, "steer", payload.model_dump(), owner=owner)
            _record_task_control_result(trace, task=result)
            return result
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError as exc:
            if trace:
                _record_task_control_result(trace, status="denied", error="permission_denied")
            detail = (
                "Task does not belong to this user"
                if str(exc) in {"owner_required", "task_owner_mismatch"}
                else str(exc)
            )
            raise HTTPException(403, detail)
        except ValueError as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            raise HTTPException(400, str(exc))
        except httpx.HTTPStatusError as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            status = 409 if exc.response.status_code == 409 else 502
            raise HTTPException(status, "Task is not currently steerable")
        except Exception as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            if str(exc) in {"task_not_active", "codex_turn_not_active", "task_not_steerable"}:
                raise HTTPException(409, str(exc))
            raise HTTPException(502, str(exc)[:300])

    @router.post("/api/agent-tasks/{task_id}/cancel")
    async def cancel(task_id: str, owner: str = Depends(require_user)):
        trace = None
        try:
            task = require_task_owner(task_id, owner)
            trace = _authorize_task_control(
                action="cancel",
                session_id=task["session_id"],
                owner=owner,
                worker=task["worker"],
                workspace=task["workspace"],
                permission_mode=str(task.get("permission_mode") or "read_only"),
                task_id=task_id,
                codex_thread_id=task.get("codex_thread_id"),
            )
            result = await task_action(task_id, "cancel", owner=owner)
            _record_task_control_result(trace, task=result, status="cancelled")
            return result
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError as exc:
            if trace:
                _record_task_control_result(trace, status="denied", error="permission_denied")
            detail = (
                "Task does not belong to this user"
                if str(exc) in {"owner_required", "task_owner_mismatch"}
                else str(exc)
            )
            raise HTTPException(403, detail)
        except Exception as exc:
            if trace:
                _record_task_control_result(trace, status="failed", error=exc)
            raise HTTPException(502, str(exc)[:300])

    @router.post("/api/agent-tasks/{task_id}/approval")
    async def approval(task_id: str, payload: TaskApproval, owner: str = Depends(require_user)):
        try:
            return await task_action(task_id, "approval", payload.model_dump(), owner=owner)
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError as exc:
            if str(exc) == "read_only_task_approval_must_deny":
                raise HTTPException(403, "Read-only tasks can only deny approval requests")
            raise HTTPException(403, "Task does not belong to this user")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, str(exc)[:300])

    @router.get("/api/runtime/status")
    async def status(_owner: str = Depends(require_user)):
        return await runtime_status()

    @router.post("/api/knowledge/search")
    async def knowledge_search(payload: KnowledgeSearch, owner: str = Depends(require_user)):
        return search_knowledge(payload.query, owner=owner, client=payload.client, limit=payload.limit)

    @router.post("/api/knowledge/sync")
    async def knowledge_sync(
        payload: KnowledgeSync,
        authorization: str | None = Header(default=None),
    ):
        if not internal_token_valid(authorization):
            raise HTTPException(401, "Unauthorized")
        total = sum(len(doc.text) for doc in payload.documents)
        if total > 30_000_000:
            raise HTTPException(413, "Knowledge sync payload too large")
        try:
            return sync_knowledge([doc.model_dump() for doc in payload.documents])
        except Exception as exc:
            raise HTTPException(503, str(exc)[:300])

    router.include_router(madpanda_knowledge_router)
    return router
