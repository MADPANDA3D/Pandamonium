from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.models import ChatMessage
from src.auth_helpers import require_user
from src.agent_worker_adapters import require_worker_task_permission
from src.jarvis_agent import (
    configure,
    internal_token_valid,
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
    router.include_router(madpanda_knowledge_router)

    @router.get("/api/agent-workers")
    async def workers(_owner: str = Depends(require_user)):
        return await worker_statuses()

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
        try:
            values = payload.model_dump()
            persist_prompt = bool(values.pop("persist_prompt", False))
            task = await start_task(**values, owner=owner)
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
            raise HTTPException(403, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"Worker request failed: {str(exc)[:300]}")

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

    @router.post("/api/agent-tasks/{task_id}/cancel")
    async def cancel(task_id: str, owner: str = Depends(require_user)):
        try:
            return await task_action(task_id, "cancel", owner=owner)
        except KeyError:
            raise HTTPException(404, "Task not found")
        except PermissionError:
            raise HTTPException(403, "Task does not belong to this user")
        except Exception as exc:
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

    return router
