"""Owner-scoped Nextcloud routes (MAD-937).

Connection management, tailnet discovery, and read-only file access. All
routes are owner-scoped; no response ever contains the app password, and
writes/uploads do not exist in v1.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src import nextcloud_gallery as nextcloud
from src.auth_helpers import require_user

logger = logging.getLogger(__name__)


class NextcloudConnectionChange(BaseModel):
    server_url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=255)
    app_password: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = None

    model_config = {"extra": "forbid"}


def _owner(request: Request) -> str | None:
    return require_user(request) or None


def _raise_nextcloud(exc: nextcloud.NextcloudError) -> None:
    raise HTTPException(exc.status_code, exc.public())


def setup_nextcloud_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/api/nextcloud/connection")
    def nextcloud_connection(request: Request):
        return nextcloud.connection_status(_owner(request))

    @router.put("/api/nextcloud/connection")
    async def save_nextcloud_connection(request: Request, change: NextcloudConnectionChange):
        owner = _owner(request)
        kwargs: dict[str, Any] = {}
        fields = change.model_fields_set
        if "server_url" in fields:
            kwargs["server_url"] = change.server_url
        if "username" in fields:
            kwargs["username"] = change.username
        if change.app_password and change.app_password.strip():
            kwargs["app_password"] = change.app_password
        if "enabled" in fields:
            kwargs["enabled"] = change.enabled
        try:
            return nextcloud.save_connection(owner, **kwargs)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.delete("/api/nextcloud/connection")
    def delete_nextcloud_connection(request: Request):
        try:
            return nextcloud.remove_connection(_owner(request))
        except nextcloud.NextcloudError as exc:
            _raise_nextcloud(exc)

    @router.post("/api/nextcloud/connection/test")
    async def test_nextcloud_connection(request: Request):
        try:
            return await nextcloud.test_connection(_owner(request))
        except nextcloud.NextcloudError as exc:
            _raise_nextcloud(exc)

    @router.get("/api/nextcloud/discover")
    async def discover_nextcloud(request: Request):
        return await nextcloud.discover_tailnet_nextcloud()

    @router.get("/api/nextcloud/files")
    async def list_nextcloud_files(request: Request, path: str = Query("")):
        try:
            return await nextcloud.list_folder(_owner(request), path)
        except nextcloud.NextcloudError as exc:
            _raise_nextcloud(exc)

    @router.get("/api/nextcloud/search")
    async def search_nextcloud_files(
        request: Request,
        q: str = Query("", max_length=120),
        path: str = Query(""),
    ):
        try:
            return await nextcloud.search_files(_owner(request), q, path=path)
        except nextcloud.NextcloudError as exc:
            _raise_nextcloud(exc)

    @router.get("/api/nextcloud/download")
    async def download_nextcloud_file(request: Request, path: str = Query("")):
        try:
            content, media_type = await nextcloud.download_file(_owner(request), path)
        except nextcloud.NextcloudError as exc:
            _raise_nextcloud(exc)
        filename = path.rsplit("/", 1)[-1] or "nextcloud-file"
        return Response(
            content,
            media_type=media_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
