from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.jarvis_agent import internal_token_valid
from src.madpanda_knowledge import authenticate, create_proposal, latest_sync_state, list_proposals, store, sync_in_worker

router = APIRouter(tags=["madpanda-knowledge"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    domain: str | None = None
    client: str | None = None
    limit: int = Field(default=6, ge=1, le=10)
    include_secondary: bool = False


class FetchRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=100)
    chunk_id: int = Field(ge=0)


class ProposalRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=12000)
    domain: str
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    suggested_path: str = Field(default="", max_length=1000)


class SyncDocument(BaseModel):
    source_id: str
    source: str
    title: str
    heading: str
    domain: str
    client: str = ""
    sensitivity: str
    authority: str
    source_type: str
    status: str
    mtime: int
    content_hash: str
    source_links: list[str] = Field(default_factory=list, max_length=100)
    generation_version: str = Field(default="", max_length=100)
    text: str = Field(min_length=1, max_length=2_000_000)


class SyncRequest(BaseModel):
    sync_id: str
    index_version: str
    batch: int = Field(ge=0)
    final: bool = False
    documents: list[SyncDocument] = Field(max_length=100)
    source_ids: list[str] | None = Field(default=None, max_length=100_000)


def _agent(authorization: str | None):
    try:
        return authenticate(authorization)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


def _call(callback):
    try:
        return callback()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)[:300]) from exc


async def _call_async(callback):
    try:
        return await asyncio.to_thread(callback)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)[:300]) from exc


@router.post("/api/knowledge/v1/sync")
async def sync_v1(payload: SyncRequest, authorization: str | None = Header(default=None)):
    if not internal_token_valid(authorization):
        raise HTTPException(401, "Unauthorized")
    documents = [row.model_dump() for row in payload.documents]
    return await _call_async(lambda: sync_in_worker({
        "sync_id": payload.sync_id,
        "index_version": payload.index_version,
        "batch": payload.batch,
        "documents": documents,
        "source_ids": payload.source_ids,
        "final": payload.final,
    }))


@router.get("/api/knowledge/v1/sync-state")
async def sync_state_v1(authorization: str | None = Header(default=None)):
    if not internal_token_valid(authorization):
        raise HTTPException(401, "Unauthorized")
    return latest_sync_state()


@router.post("/api/knowledge/v1/search")
async def search_v1(payload: SearchRequest, authorization: str | None = Header(default=None)):
    agent = _agent(authorization)
    return _call(lambda: store().search(
        agent,
        payload.query,
        domain=payload.domain,
        client=payload.client,
        limit=payload.limit,
        include_secondary=payload.include_secondary,
    ))


@router.post("/api/knowledge/v1/fetch")
async def fetch_v1(payload: FetchRequest, authorization: str | None = Header(default=None)):
    agent = _agent(authorization)
    return _call(lambda: store().fetch(agent, payload.source_id, payload.chunk_id))


@router.get("/api/knowledge/v1/status")
async def status_v1(authorization: str | None = Header(default=None)):
    agent = _agent(authorization)
    return _call(lambda: store().status(agent))


@router.post("/api/knowledge/v1/proposals")
async def propose_v1(payload: ProposalRequest, authorization: str | None = Header(default=None)):
    agent = _agent(authorization)
    return _call(lambda: create_proposal(agent, payload.model_dump()))


@router.get("/api/knowledge/v1/proposals")
async def proposals_v1(authorization: str | None = Header(default=None)):
    agent = _agent(authorization)
    return _call(lambda: list_proposals(agent))
