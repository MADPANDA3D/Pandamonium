"""Admin API for approval-gated managed extension lifecycle plans."""

from __future__ import annotations

import asyncio
import json
import os
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
from src.extension_capability_inventory import validate_scan_artifact
from src.extension_host import live_catalog_web_adapter
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    InlineWebAdapter,
    normalize_git_source_url,
)
from src.extension_mcp_adapter import mcp_extension_adapter
from src.extension_registry import ExtensionContractError
from src.extension_plugin_view import installed_plugin_detail, installed_plugin_rows
from src.extension_scan import ExtensionScanError, get_scan, start_scan
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
    scan_id: str | None = Field(default=None, min_length=8, max_length=64)


class SourceScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


def _configured_plugin_surfaces() -> list[dict[str, str]]:
    """Configured (non-registry) plugin surfaces for the operator UI."""
    surfaces: list[dict[str, str]] = []
    try:
        oracle_url = os.getenv("ODYSSEUS_ORACLE_URL", "").strip()
        if not oracle_url:
            from src.extension_host import extension_runtime_host

            oracle_url = str(extension_runtime_host.urls.get("oracle") or "").strip()
    except Exception:
        oracle_url = ""
    if oracle_url:
        surfaces.append({"id": "oracle", "name": "ORACLE", "runtime": "web"})
    return surfaces


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

    @router.get("/installed")
    async def list_installed_plugins(_owner: str = Depends(require_user)):
        return {
            "plugins": await asyncio.to_thread(
                installed_plugin_rows,
                manager.registry,
                configured_surfaces=_configured_plugin_surfaces(),
            )
        }

    @router.get("/installed/{extension_id}")
    async def installed_plugin(extension_id: str, _owner: str = Depends(require_user)):
        detail = await asyncio.to_thread(
            installed_plugin_detail,
            manager.registry,
            extension_id,
            configured_surfaces=_configured_plugin_surfaces(),
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="extension_plugin_not_found")
        return detail

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
            draft_manifest = None
            scan_revision = None
            if payload.scan_id:
                job = await asyncio.to_thread(get_scan, payload.scan_id)
                if job is None:
                    raise HTTPException(404, "extension_scan_not_found")
                if job.get("status") != "succeeded":
                    raise HTTPException(409, "extension_scan_unavailable")
                artifact = await asyncio.to_thread(
                    validate_scan_artifact, job.get("artifact"), require_complete=True
                )
                if normalize_git_source_url(
                    payload.source_url, check_public=False
                ) != normalize_git_source_url(
                    artifact["source_url"], check_public=False
                ):
                    raise HTTPException(400, "extension_scan_source_mismatch")
                scan_revision = artifact["source_revision"]
                draft_manifest = artifact.get("draft_manifest")
            return await asyncio.to_thread(
                manager.preview_source,
                payload.operation,
                payload.source_url,
                payload.ref,
                operator_id=_operator(owner),
                scan_id=payload.scan_id,
                scan_revision=scan_revision,
                draft_manifest=draft_manifest,
            )
        except (ExtensionLifecycleError, ExtensionContractError) as exc:
            raise _http_error(exc) from exc

    @router.post("/scans", dependencies=[Depends(require_admin)])
    async def start_source_scan(
        payload: SourceScanRequest, owner: str = Depends(require_user)
    ):
        try:
            return await asyncio.to_thread(
                start_scan,
                payload.source_url,
                payload.ref,
                operator_id=_operator(owner),
            )
        except ExtensionScanError as exc:
            raise HTTPException(status_code=400, detail=str(exc.code)) from exc

    @router.get("/scans/{scan_id}", dependencies=[Depends(require_admin)])
    async def get_source_scan(scan_id: str, owner: str = Depends(require_user)):
        job = await asyncio.to_thread(get_scan, scan_id)
        if job is None:
            raise HTTPException(status_code=404, detail="extension_scan_not_found")
        return job

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
