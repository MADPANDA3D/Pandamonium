"""Admin API for approval-gated managed extension lifecycle plans."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.middleware import require_admin
from src.auth_helpers import require_user
from src.authority_protocol import operator_identity
from src.extension_host import live_catalog_web_adapter
from src.extension_mcp_adapter import mcp_extension_adapter
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    InlineWebAdapter,
)
from src.extension_skill_adapter import SkillBundleAdapter
from src.extension_registry import ExtensionContractError


class SourcePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str = Field(pattern=r"^(install|upgrade)$")
    source_url: str = Field(min_length=1, max_length=2_048)
    ref: str = Field(default="HEAD", min_length=1, max_length=200)


class LifecyclePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str = Field(pattern=r"^(enable|disable|rollback|uninstall)$")
    extension_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    target_revision: str | None = Field(default=None, max_length=64)


def setup_extension_routes(
    manager: ExtensionLifecycleManager | None = None, *, skills_manager=None
) -> APIRouter:
    manager = manager or ExtensionLifecycleManager(
        adapters=[
            InlineWebAdapter(),
            live_catalog_web_adapter,
            mcp_extension_adapter,
            *([SkillBundleAdapter(skills_manager)] if skills_manager is not None else []),
        ]
    )
    router = APIRouter(
        prefix="/api/extensions",
        tags=["extensions"],
        dependencies=[Depends(require_admin)],
    )

    def _operator(owner: str) -> str:
        identity = operator_identity(owner)
        if not identity:
            raise HTTPException(401, "Authenticated operator required")
        return identity

    def _http_error(exc: ExtensionLifecycleError | ExtensionContractError) -> HTTPException:
        status = 404 if exc.code in {"extension_plan_not_found", "extension_not_installed"} else 409
        if exc.code.startswith(("extension_git_url", "extension_git_ref", "extension_manifest")):
            status = 400
        return HTTPException(status, exc.code)

    def _bind_async_adapters() -> None:
        loop = asyncio.get_running_loop()
        for adapter in manager.adapters:
            binder = getattr(adapter, "bind_loop", None)
            if binder:
                binder(loop)

    @router.get("")
    async def list_extensions(owner: str = Depends(require_user)):
        _operator(owner)
        return await asyncio.to_thread(manager.snapshot)

    @router.post("/plans/source")
    async def preview_source_plan(payload: SourcePlanRequest, owner: str = Depends(require_user)):
        try:
            _bind_async_adapters()
            return await asyncio.to_thread(
                manager.preview_source,
                payload.operation,
                payload.source_url,
                payload.ref,
                operator_id=_operator(owner),
            )
        except (ExtensionLifecycleError, ExtensionContractError) as exc:
            raise _http_error(exc) from exc

    @router.post("/plans/lifecycle")
    async def preview_lifecycle_plan(payload: LifecyclePlanRequest, owner: str = Depends(require_user)):
        try:
            _bind_async_adapters()
            return await asyncio.to_thread(
                manager.preview_lifecycle,
                payload.operation,
                payload.extension_id,
                operator_id=_operator(owner),
                target_revision=payload.target_revision,
            )
        except (ExtensionLifecycleError, ExtensionContractError) as exc:
            raise _http_error(exc) from exc

    @router.post("/plans/{plan_id}/execute")
    async def execute_plan(plan_id: str, owner: str = Depends(require_user)):
        try:
            _bind_async_adapters()
            return await asyncio.to_thread(
                manager.execute_plan, plan_id, operator_id=_operator(owner)
            )
        except (ExtensionLifecycleError, ExtensionContractError) as exc:
            raise _http_error(exc) from exc

    return router
