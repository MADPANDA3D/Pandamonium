"""Admin API for approval-gated managed extension lifecycle plans."""

from __future__ import annotations

import asyncio
import json
import platform
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.constants import APP_VERSION, DATA_DIR
from core.middleware import require_admin
from src.auth_helpers import require_user
from src.authority_protocol import operator_identity
from src.extension_host import live_catalog_web_adapter
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    InlineWebAdapter,
)
from src.extension_mcp_adapter import mcp_extension_adapter
from src.extension_registry import ExtensionContractError
from src.extension_skill_adapter import SkillBundleAdapter
from src.marketplace_catalog import (
    MarketplaceCatalogError,
    catalog_dependency_status,
    download_catalog_artifact,
    marketplace_catalog_view,
    preview_catalog_install,
    verify_catalog_artifact,
)

MARKETPLACE_DIR = Path(DATA_DIR) / "marketplace"


def _marketplace_files() -> tuple[Any, Mapping[str, str | bytes]] | None:
    catalog_path = MARKETPLACE_DIR / "catalog.json"
    keys_path = MARKETPLACE_DIR / "trusted_keys.json"
    if not catalog_path.exists() and not keys_path.exists():
        return None
    if not catalog_path.is_file() or not keys_path.is_file():
        raise MarketplaceCatalogError("marketplace_configuration_incomplete")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        keys = json.loads(keys_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MarketplaceCatalogError("marketplace_configuration_invalid") from exc
    if not isinstance(keys, Mapping):
        raise MarketplaceCatalogError("marketplace_trust_store_invalid")
    return catalog, keys


def _runtime_platform() -> tuple[str, str]:
    system = {"darwin": "macos", "win32": "windows"}.get(
        platform.system().lower(), platform.system().lower()
    )
    machine = platform.machine().lower()
    architecture = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine, machine)
    return system, architecture


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


class MarketplacePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str = Field(
        pattern=r"^(install|upgrade|enable|disable|rollback|uninstall)$"
    )
    extension_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    version: str | None = Field(default=None, max_length=80)
    target_revision: str | None = Field(default=None, max_length=64)


def public_extension_catalog(registry) -> dict[str, list[dict[str, str]]]:
    """Project installed extension metadata without source or host details."""
    plugins = []
    for extension_id, record in registry.snapshot().get("extensions", {}).items():
        manifest = record.get("manifest") if isinstance(record, dict) else None
        if not isinstance(manifest, dict):
            continue
        plugins.append(
            {
                "id": str(extension_id),
                "name": str(manifest.get("name") or extension_id)[:200],
                "state": "enabled" if record.get("enabled") else "disabled",
                "runtime": str(
                    (manifest.get("runtime") or {}).get("type") or "unknown"
                )[:40],
            }
        )
    plugins.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return {"plugins": plugins}


def setup_extension_routes(
    manager: ExtensionLifecycleManager | None = None,
    *,
    skills_manager=None,
    marketplace_loader: Callable[
        [], tuple[Any, Mapping[str, str | bytes]] | None
    ] = _marketplace_files,
    artifact_loader: Callable[[Mapping[str, Any]], bytes] = download_catalog_artifact,
) -> APIRouter:
    manager = manager or ExtensionLifecycleManager(
        adapters=[
            InlineWebAdapter(),
            live_catalog_web_adapter,
            mcp_extension_adapter,
            *(
                [SkillBundleAdapter(skills_manager)]
                if skills_manager is not None
                else []
            ),
        ]
    )
    router = APIRouter(
        prefix="/api/extensions",
        tags=["extensions"],
    )

    def _operator(owner: str) -> str:
        identity = operator_identity(owner)
        if not identity:
            raise HTTPException(401, "Authenticated operator required")
        return identity

    def _http_error(
        exc: ExtensionLifecycleError | ExtensionContractError | MarketplaceCatalogError,
    ) -> HTTPException:
        status = (
            404
            if exc.code
            in {
                "extension_plan_not_found",
                "extension_not_installed",
                "marketplace_package_not_found",
            }
            else 409
        )
        if exc.code.startswith(
            (
                "extension_git_url",
                "extension_git_ref",
                "extension_manifest",
                "marketplace_catalog_invalid",
                "marketplace_operation_invalid",
            )
        ):
            status = 400
        return HTTPException(status, exc.code)

    def _bind_async_adapters() -> None:
        loop = asyncio.get_running_loop()
        for adapter in manager.adapters:
            binder = getattr(adapter, "bind_loop", None)
            if binder:
                binder(loop)

    @router.get("", dependencies=[Depends(require_admin)])
    async def list_extensions(owner: str = Depends(require_user)):
        _operator(owner)
        return await asyncio.to_thread(manager.snapshot)

    @router.get("/catalog")
    async def list_public_extensions(_owner: str = Depends(require_user)):
        return await asyncio.to_thread(public_extension_catalog, manager.registry)

    @router.get("/marketplace")
    async def list_marketplace(_owner: str = Depends(require_user)):
        system, architecture = _runtime_platform()
        try:
            loaded = await asyncio.to_thread(marketplace_loader)
            if loaded is None:
                return marketplace_catalog_view(
                    None,
                    trusted_keys={},
                    registry_snapshot={},
                    pandamonium_version=APP_VERSION,
                    platform=system,
                    architecture=architecture,
                    online=False,
                )
            catalog, trusted_keys = loaded
            return await asyncio.to_thread(
                marketplace_catalog_view,
                catalog,
                trusted_keys=trusted_keys,
                registry_snapshot=manager.registry.snapshot(),
                lifecycle_snapshot=manager.snapshot(),
                pandamonium_version=APP_VERSION,
                platform=system,
                architecture=architecture,
            )
        except MarketplaceCatalogError as exc:
            return {
                "schema_version": "pandamonium.marketplace-view.v1",
                "status": "error",
                "failure": exc.code,
                "plugins": [],
            }

    @router.post("/marketplace/plans", dependencies=[Depends(require_admin)])
    async def preview_marketplace_plan(
        payload: MarketplacePlanRequest, owner: str = Depends(require_user)
    ):
        try:
            _bind_async_adapters()
            operator_id = _operator(owner)
            if payload.operation not in {"install", "upgrade"}:
                return await asyncio.to_thread(
                    manager.preview_lifecycle,
                    payload.operation,
                    payload.extension_id,
                    operator_id=operator_id,
                    target_revision=payload.target_revision,
                )
            if not payload.version:
                raise MarketplaceCatalogError("marketplace_version_required")
            loaded = await asyncio.to_thread(marketplace_loader)
            if loaded is None:
                raise MarketplaceCatalogError("marketplace_catalog_offline")
            catalog, trusted_keys = loaded
            system, architecture = _runtime_platform()
            preview = await asyncio.to_thread(
                preview_catalog_install,
                catalog,
                payload.extension_id,
                payload.version,
                trusted_keys=trusted_keys,
                pandamonium_version=APP_VERSION,
                platform=system,
                architecture=architecture,
                online=True,
                operation=payload.operation,
            )
            registry_snapshot = manager.registry.snapshot()
            preview["dependencies"] = catalog_dependency_status(
                preview["dependencies"], registry_snapshot
            )
            artifact_content = await asyncio.to_thread(
                artifact_loader, preview["artifact"]
            )
            verify_catalog_artifact(preview["artifact"], artifact_content)
            distribution = {
                key: preview[key]
                for key in (
                    "catalog_id",
                    "version",
                    "summary",
                    "categories",
                    "license",
                    "publisher",
                    "compatibility",
                    "dependencies",
                    "configuration",
                    "restart_required",
                    "review",
                    "rollback",
                    "removal",
                )
            }
            distribution["artifact"] = {
                "sha256": preview["artifact"]["sha256"],
                "size_bytes": preview["artifact"]["size_bytes"],
                "digest_state": "verified",
                "signature_state": "verified",
            }
            installed = registry_snapshot.get("extensions", {})
            current = (
                installed.get(payload.extension_id)
                if isinstance(installed, Mapping)
                else None
            )
            current_manifest = (
                current.get("manifest") if isinstance(current, Mapping) else None
            )
            distribution["current_version"] = (
                str(current_manifest.get("version"))
                if isinstance(current_manifest, Mapping)
                and current_manifest.get("version")
                else None
            )
            distribution["target_version"] = preview["version"]
            return await asyncio.to_thread(
                manager.preview_source,
                payload.operation,
                preview["source_url"],
                preview["requested_ref"],
                operator_id=operator_id,
                expected_manifest=preview["manifest"],
                distribution=distribution,
            )
        except (
            ExtensionLifecycleError,
            ExtensionContractError,
            MarketplaceCatalogError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.post("/plans/source", dependencies=[Depends(require_admin)])
    async def preview_source_plan(
        payload: SourcePlanRequest, owner: str = Depends(require_user)
    ):
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

    @router.post("/plans/lifecycle", dependencies=[Depends(require_admin)])
    async def preview_lifecycle_plan(
        payload: LifecyclePlanRequest, owner: str = Depends(require_user)
    ):
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

    @router.post("/plans/{plan_id}/execute", dependencies=[Depends(require_admin)])
    async def execute_plan(plan_id: str, owner: str = Depends(require_user)):
        try:
            _bind_async_adapters()
            return await asyncio.to_thread(
                manager.execute_plan, plan_id, operator_id=_operator(owner)
            )
        except (ExtensionLifecycleError, ExtensionContractError) as exc:
            raise _http_error(exc) from exc

    return router
