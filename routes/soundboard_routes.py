"""Authenticated soundboard settings and manual media, backed by the installed plugin."""

from __future__ import annotations

import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from src import soundboard
from src.auth_helpers import require_user
from src.authority_protocol import operator_identity
from src.extension_cli_adapter import _LOCK, GeneratedCliAdapter
from src.extension_installer import ExtensionLifecycleError
from src.extension_registry import ExtensionRegistry


class FavoriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class PlaybackPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    volume: float = Field(ge=0, le=1)
    muted: bool


def _context(owner: str, *, active: bool = True) -> tuple:
    identity = operator_identity(owner)
    adapter = GeneratedCliAdapter()
    record = ExtensionRegistry().snapshot()["extensions"].get(soundboard.EXTENSION_ID)
    try:
        installed = json.loads((adapter.root / "lifecycle.json").read_text())["extensions"].get(soundboard.EXTENSION_ID)
    except FileNotFoundError:
        installed = None
    if not identity or not installed or installed.get("owner_scope") != identity or not record:
        raise HTTPException(404, "Soundboard plugin is not installed for this account")
    runtime = adapter._runtime(record["manifest"], installed["active_revision"], identity)
    config = {}
    if active:
        _, runtime, _, contract, config = adapter._validated_context(record, identity, recover=True)
        if not any(i["binding"] == "soundboard.search" for i in contract["interfaces"].values()):
            raise HTTPException(409, "Update the Myinstants plugin to enable Soundboard")
    return record, runtime, config


def setup_soundboard_routes() -> APIRouter:
    router = APIRouter(prefix="/api/soundboard", tags=["soundboard"])

    def call(operation, owner: str):
        # Reuse the package lock so native tools and Settings cannot lose each other's writes.
        with _LOCK:
            try:
                return operation(*_context(owner))
            except (ExtensionLifecycleError, ValueError, TypeError, KeyError, OSError, httpx.HTTPError) as exc:
                raise HTTPException(503, "Soundboard unavailable; check plugin setup or try again") from exc

    @router.get("")
    def status(owner: str = Depends(require_user)):
        with _LOCK:
            try:
                record, runtime, _ = _context(owner, active=False)
                state = soundboard.read_state(runtime)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return {"installed": False, "enabled": False}
                raise
            except (ExtensionLifecycleError, ValueError, TypeError, KeyError, OSError) as exc:
                raise HTTPException(503, "Soundboard state unavailable; saved data was preserved") from exc
            return {"installed": True, "enabled": bool(record["enabled"]),
                    "favorites": [state["sounds"][key] for key in state["favorites"] if key in state["sounds"]],
                    "volume": state["volume"], "muted": state["muted"]}

    @router.get("/sounds")
    def sounds(query: str | None = Query(None, min_length=1, max_length=100), owner: str = Depends(require_user)):
        return call(lambda record, runtime, config: soundboard.execute(
            "soundboard.search" if query else "soundboard.recent",
            {"query": query} if query else {}, runtime, config), owner)

    @router.get("/sounds/{sound_id}")
    def detail(sound_id: str, owner: str = Depends(require_user)):
        if not re.fullmatch(soundboard.ID_PATTERN, sound_id):
            raise HTTPException(422, "Invalid sound identity")
        return call(lambda record, runtime, config: soundboard.execute(
            "soundboard.detail", {"id": sound_id}, runtime, config), owner)

    @router.get("/sounds/{sound_id}/audio")
    def audio(sound_id: str, owner: str = Depends(require_user)):
        content, content_type = call(lambda record, runtime, config: soundboard.audio(runtime, sound_id, config=config), owner)
        return Response(content, media_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.put("/favorites/{sound_id}")
    def favorite(sound_id: str, body: FavoriteRequest, owner: str = Depends(require_user)):
        call(lambda record, runtime, config: soundboard.favorite(runtime, sound_id, body.enabled), owner)
        return {"saved": True}

    @router.put("/preferences")
    def preferences(body: PlaybackPreferences, owner: str = Depends(require_user)):
        def save(record, runtime, config):
            state = soundboard.read_state(runtime)
            state.update(body.model_dump())
            soundboard.save_state(runtime, state)
            return {"saved": True}
        return call(save, owner)

    return router
