"""Admin-gated Unsloth Studio runtime routes (MAD-796).

Connection management plus read-only health/capability discovery. Every route
is admin-gated and owner-less (installation-level). The test route only reads:
it can never start, stop, or reset a training job, and no write route exists
for job control. Training job ownership is deferred to MAD-797.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src import unsloth_runtime as unsloth
from src.auth_helpers import get_current_user
from core.middleware import require_admin

logger = logging.getLogger(__name__)


class UnslothConnectionChange(BaseModel):
    base_url: str | None = Field(default=None, max_length=2048)
    token: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None
    model_endpoint_id: str | None = Field(default=None, max_length=128)

    model_config = {"extra": "forbid"}


def _actor(request: Request) -> str:
    return str(get_current_user(request) or "")


def _raise_unsloth(exc: unsloth.UnslothError) -> None:
    raise HTTPException(exc.status_code, exc.public())


def setup_unsloth_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/unsloth/connection")
    def unsloth_connection(request: Request):
        require_admin(request)
        return unsloth.connection_status(None)

    @router.put("/api/unsloth/connection")
    def save_unsloth_connection(request: Request, change: UnslothConnectionChange):
        require_admin(request)
        fields = change.model_fields_set
        kwargs: dict[str, Any] = {}
        if "base_url" in fields:
            kwargs["base_url"] = change.base_url
        if "token" in fields:
            kwargs["token"] = change.token or ""
        if "enabled" in fields:
            kwargs["enabled"] = change.enabled
        if "model_endpoint_id" in fields:
            kwargs["model_endpoint_id"] = change.model_endpoint_id or ""
        try:
            payload = unsloth.save_connection(None, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        logger.info("unsloth runtime configured by %s", _actor(request))
        return payload

    @router.delete("/api/unsloth/connection")
    def delete_unsloth_connection(request: Request):
        require_admin(request)
        try:
            payload = unsloth.remove_connection(None)
        except unsloth.UnslothError as exc:
            _raise_unsloth(exc)
        logger.info("unsloth runtime removed by %s", _actor(request))
        return payload

    @router.post("/api/unsloth/connection/test")
    async def test_unsloth_connection(request: Request):
        require_admin(request)
        try:
            payload = await unsloth.test_connection(None)
        except unsloth.UnslothError as exc:
            _raise_unsloth(exc)
        logger.info(
            "unsloth runtime test by %s state=%s", _actor(request), payload.get("state")
        )
        return payload

    @router.get("/api/unsloth/capabilities")
    def unsloth_capabilities(request: Request):
        require_admin(request)
        try:
            return unsloth.cached_capabilities(None)
        except unsloth.UnslothError as exc:
            _raise_unsloth(exc)

    return router