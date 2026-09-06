"""Owner-scoped Immich connection and bounded Gallery proxy/cache."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.atomic_io import atomic_write_json, atomic_write_text
from core.database import Integration, SessionLocal
from core.platform_compat import safe_chmod
from src.constants import DATA_DIR
from src.secret_storage import decrypt, encrypt, is_encrypted
from src.url_safety import check_outbound_url


INTEGRATION_TYPE = "immich_gallery"
CONNECTION_NAME = "primary"
CACHE_ROOT = Path(DATA_DIR) / "immich_cache"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
SAFE_THUMBNAIL_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_UNSET = object()

# ponytail: one lock is enough for a small owner-operated cache; use per-owner
# locks only if concurrent Immich traffic becomes measurable.
_CACHE_LOCK = threading.Lock()


class ImmichError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.retry_after:
            result["retry_after"] = self.retry_after
        return result


def _owner_query(db: Any, owner: str | None):
    query = db.query(Integration).filter(
        Integration.type == INTEGRATION_TYPE,
        Integration.name == CONNECTION_NAME,
    )
    return query.filter(Integration.owner.is_(None) if owner is None else Integration.owner == owner)


def _normalize_server_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("Immich server URL is required")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Immich server URL must be HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("Immich server URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Immich server URL cannot contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    cleaned = urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", "")).rstrip("/")
    ok, reason = check_outbound_url(cleaned, block_private=False)
    if not ok:
        raise ValueError(f"Immich server URL rejected: {reason}")
    return cleaned


def _scope_token(owner: str | None) -> str:
    return hashlib.sha256((owner or "__single_user__").encode("utf-8")).hexdigest()[:24]


def _cache_dir(owner: str | None, connection_id: str, *, create: bool = False) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", connection_id):
        raise ValueError("Invalid Immich connection id")
    target = CACHE_ROOT / _scope_token(owner) / connection_id
    if create:
        target.mkdir(parents=True, exist_ok=True)
        safe_chmod(CACHE_ROOT, 0o700)
        safe_chmod(target.parent, 0o700)
        safe_chmod(target, 0o700)
    return target


def _cache_key(kind: str, values: dict[str, Any]) -> str:
    raw = json.dumps([kind, values], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_json_cache(owner: str | None, connection_id: str, key: str, value: Any) -> None:
    directory = _cache_dir(owner, connection_id, create=True) / "metadata"
    with _CACHE_LOCK:
        directory.mkdir(parents=True, exist_ok=True)
        safe_chmod(directory, 0o700)
        target = directory / f"{key}.json"
        atomic_write_json(str(target), value)
        safe_chmod(target, 0o600)


def _read_json_cache(owner: str | None, connection_id: str, key: str) -> Any | None:
    target = _cache_dir(owner, connection_id) / "metadata" / f"{key}.json"
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _media_cache_paths(
    owner: str | None,
    connection_id: str,
    asset_id: str,
    size: str,
    *,
    create: bool = False,
) -> tuple[Path, Path]:
    token = hashlib.sha256(f"{asset_id}\0{size}".encode()).hexdigest()
    directory = _cache_dir(owner, connection_id, create=create) / "media"
    if create:
        directory.mkdir(parents=True, exist_ok=True)
        safe_chmod(directory, 0o700)
    return directory / f"{token}.bin", directory / f"{token}.type"


def _write_media_cache(
    owner: str | None,
    connection_id: str,
    asset_id: str,
    size: str,
    content: bytes,
    media_type: str,
) -> None:
    target, type_target = _media_cache_paths(
        owner, connection_id, asset_id, size, create=True
    )
    tmp = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex}")
    with _CACHE_LOCK:
        try:
            with tmp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            atomic_write_text(str(type_target), media_type)
            safe_chmod(target, 0o600)
            safe_chmod(type_target, 0o600)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def _read_media_cache(
    owner: str | None, connection_id: str, asset_id: str, size: str
) -> tuple[bytes, str] | None:
    target, type_target = _media_cache_paths(owner, connection_id, asset_id, size)
    try:
        return target.read_bytes(), type_target.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def clear_cache(owner: str | None, connection_id: str | None = None) -> int:
    if connection_id is None:
        connection = get_connection(owner, require_enabled=False)
        connection_id = connection["id"]
    target = _cache_dir(owner, connection_id)
    with _CACHE_LOCK:
        count = (
            sum(1 for item in target.rglob("*") if item.is_file())
            if target.is_dir()
            else 0
        )
        if target.is_dir():
            shutil.rmtree(target)
    return count


def _cache_file_count(owner: str | None, connection_id: str) -> int:
    target = _cache_dir(owner, connection_id)
    return sum(1 for item in target.rglob("*") if item.is_file()) if target.is_dir() else 0


def get_connection(owner: str | None, *, require_enabled: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise ImmichError("unconfigured", "Immich is not connected", status_code=404)
        config = dict(row.config or {})
        result = {
            "id": row.id,
            "owner": row.owner,
            "enabled": bool(row.enabled),
            "base_url": str(config.get("base_url") or ""),
            "api_key": decrypt(str(config.get("api_key") or "")),
            "status": str(config.get("status") or ("untested" if row.enabled else "disabled")),
            "last_error": config.get("last_error"),
            "last_checked_at": config.get("last_checked_at"),
            "last_synced_at": config.get("last_synced_at"),
        }
    finally:
        db.close()
    if require_enabled and not result["enabled"]:
        raise ImmichError("disabled", "Immich connection is disabled", status_code=409)
    if require_enabled and not result["api_key"]:
        raise ImmichError("expired_key", "Immich API key is unavailable", status_code=401)
    return result


def connection_status(owner: str | None) -> dict[str, Any]:
    try:
        connection = get_connection(owner, require_enabled=False)
    except ImmichError as exc:
        if exc.code == "unconfigured":
            return {
                "configured": False,
                "enabled": False,
                "status": "unconfigured",
                "api_key_configured": False,
                "cached_files": 0,
            }
        raise
    return {
        "configured": True,
        "enabled": connection["enabled"],
        "server_url": connection["base_url"],
        "status": connection["status"] if connection["enabled"] else "disabled",
        "api_key_configured": bool(connection["api_key"]),
        "last_error": connection["last_error"],
        "last_checked_at": connection["last_checked_at"],
        "last_synced_at": connection["last_synced_at"],
        "cached_files": _cache_file_count(owner, connection["id"]),
    }


def save_connection(
    owner: str | None,
    *,
    server_url: str | None = None,
    api_key: object = _UNSET,
    enabled: bool | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        config = dict(row.config or {}) if row else {}
        if server_url is not None:
            config["base_url"] = _normalize_server_url(server_url)
        if api_key is not _UNSET:
            value = str(api_key or "").strip()
            if not value:
                raise ValueError("Immich API key cannot be blank")
            if len(value) > 4096 or is_encrypted(value):
                raise ValueError("Invalid Immich API key")
            config["api_key"] = encrypt(value)
        if not config.get("base_url"):
            raise ValueError("Immich server URL is required")
        if not config.get("api_key"):
            raise ValueError("Immich API key is required")
        config.update({"status": "untested", "last_error": None, "last_checked_at": None})
        if row is None:
            row = Integration(
                id=uuid.uuid4().hex,
                owner=owner,
                name=CONNECTION_NAME,
                type=INTEGRATION_TYPE,
                config=config,
                enabled=True if enabled is None else enabled,
            )
            db.add(row)
        else:
            row.config = config
            if enabled is not None:
                row.enabled = enabled
        if not row.enabled:
            config["status"] = "disabled"
            row.config = config
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return connection_status(owner)


def remove_connection(owner: str | None) -> int:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise ImmichError("unconfigured", "Immich is not connected", status_code=404)
        connection_id = row.id
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return clear_cache(owner, connection_id)


def _record_status(owner: str | None, connection_id: str, status: str, error: str | None = None, *, synced: bool = False) -> None:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).filter(Integration.id == connection_id).first()
        if row is None:
            return
        config = dict(row.config or {})
        now = datetime.now(timezone.utc).isoformat()
        config.update({"status": status, "last_error": error, "last_checked_at": now})
        if synced:
            config["last_synced_at"] = now
        row.config = config
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _url(connection: dict[str, Any], path: str) -> str:
    return f"{connection['base_url']}/api/{path.lstrip('/')}"


def _headers(connection: dict[str, Any], *, accept: str = "application/json") -> dict[str, str]:
    return {"x-api-key": connection["api_key"], "Accept": accept}


def _raise_status(response: httpx.Response, *, missing_code: str = "not_found") -> None:
    if response.status_code < 300:
        return
    if response.status_code == 401:
        raise ImmichError("expired_key", "Immich rejected the API key", status_code=401)
    if response.status_code == 403:
        raise ImmichError("permission", "Immich API key lacks a required permission", status_code=403)
    if response.status_code == 404:
        raise ImmichError(missing_code, "Immich item is unavailable", status_code=404)
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")[:32] or None
        raise ImmichError("rate_limited", "Immich rate limit reached", status_code=429, retry_after=retry)
    if 300 <= response.status_code < 400:
        raise ImmichError("redirect_blocked", "Immich returned an unsafe redirect")
    raise ImmichError("offline", "Immich is unavailable")


async def _request_bytes(
    connection: dict[str, Any],
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    max_bytes: int,
    accept: str,
    missing_code: str = "not_found",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport) as client:
            async with client.stream(
                method,
                _url(connection, path),
                headers=_headers(connection, accept=accept),
                json=json_body,
            ) as response:
                _raise_status(response, missing_code=missing_code)
                try:
                    declared = int(response.headers.get("content-length", "0") or 0)
                except ValueError:
                    declared = 0
                if declared > max_bytes:
                    raise ImmichError("response_too_large", "Immich response exceeded the safe limit", status_code=413)
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise ImmichError("response_too_large", "Immich response exceeded the safe limit", status_code=413)
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
    except ImmichError:
        raise
    except httpx.RequestError as exc:
        raise ImmichError("offline", "Immich is unreachable") from exc


async def _request_json(
    connection: dict[str, Any],
    method: str,
    path: str,
    *,
    body: Any | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    content, _ = await _request_bytes(
        connection,
        method,
        path,
        json_body=body,
        max_bytes=MAX_JSON_BYTES,
        accept="application/json",
        transport=transport,
    )
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImmichError("invalid_response", "Immich returned invalid JSON") from exc


async def test_connection(
    owner: str | None, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    connection = get_connection(owner)
    try:
        albums = await _request_json(connection, "GET", "/albums", transport=transport)
        assets = await _request_json(
            connection,
            "POST",
            "/search/metadata",
            body={"page": 1, "size": 1, "withExif": True},
            transport=transport,
        )
        if not isinstance(albums, list) or not isinstance(assets, dict):
            raise ImmichError("invalid_response", "Immich returned an unexpected response")
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    _record_status(owner, connection["id"], "healthy", None)
    return {"ok": True, "status": "healthy", "message": "Immich assets and albums are readable"}


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coordinate(value: Any, *, latitude: bool) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    limit = 90 if latitude else 180
    return coordinate if -limit <= coordinate <= limit else None


def _asset_ref(connection_id: str, asset_id: str) -> str:
    return f"immich:{connection_id}:{asset_id}"


def _album_ref(connection_id: str, album_id: str) -> str:
    return f"immich:{connection_id}:album:{album_id}"


def _external_id(value: Any) -> str:
    external_id = str(value or "")
    if not _EXTERNAL_ID.fullmatch(external_id):
        raise ImmichError("invalid_response", "Immich returned an invalid item id")
    return external_id


def _parse_asset_ref(connection: dict[str, Any], value: str) -> str:
    prefix = f"immich:{connection['id']}:"
    if not value.startswith(prefix):
        raise ImmichError("not_found", "Immich asset is unavailable", status_code=404)
    return _external_id(value[len(prefix):])


def _parse_album_ref(connection: dict[str, Any], value: str) -> str:
    prefix = f"immich:{connection['id']}:album:"
    if not value.startswith(prefix):
        raise ImmichError("not_found", "Immich album is unavailable", status_code=404)
    return _external_id(value[len(prefix):])


def _serialize_asset(connection_id: str, raw: dict[str, Any], album_ref: str | None = None) -> dict[str, Any]:
    asset_id = _external_id(raw.get("id"))
    filename = Path(str(raw.get("originalFileName") or f"immich-{asset_id}")).name[:255]
    exif = raw.get("exifInfo") if isinstance(raw.get("exifInfo"), dict) else {}
    camera = " ".join(str(exif.get(key) or "").strip() for key in ("make", "model")).strip() or None
    latitude = _coordinate(exif.get("latitude"), latitude=True)
    longitude = _coordinate(exif.get("longitude"), latitude=False)
    description = str(exif.get("description") or raw.get("description") or "")
    reference = _asset_ref(connection_id, asset_id)
    return {
        "id": reference,
        "filename": filename,
        "url": f"/api/gallery/immich/assets/{reference}/thumbnail?size=preview",
        "download_url": f"/api/gallery/immich/assets/{reference}/download",
        "prompt": Path(filename).stem,
        "caption": description,
        "model": "Immich",
        "source_type": "immich",
        "source_asset_id": asset_id,
        "source_hash": raw.get("checksum"),
        "mime_type": raw.get("originalMimeType"),
        "read_only": True,
        "remote": True,
        "offline": bool(raw.get("isOffline")),
        "thumbnail_ready": bool(raw.get("resized", True)),
        "favorite": bool(raw.get("isFavorite")),
        "tags": "",
        "ai_tags": "",
        "user_tags": "",
        "album_id": album_ref,
        "session_id": None,
        "session_name": None,
        "taken_at": raw.get("fileCreatedAt") or raw.get("localDateTime"),
        "created_at": raw.get("createdAt") or raw.get("fileCreatedAt"),
        "updated_at": raw.get("updatedAt") or raw.get("fileModifiedAt"),
        "camera": camera,
        "gps": {"lat": latitude, "lng": longitude} if latitude is not None and longitude is not None else None,
        "width": _integer(raw.get("width") or exif.get("exifImageWidth")),
        "height": _integer(raw.get("height") or exif.get("exifImageHeight")),
        "file_size": _integer(exif.get("fileSizeInByte")),
    }


def _serialize_album(connection_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    album_id = _external_id(raw.get("id"))
    cover_id = raw.get("albumThumbnailAssetId")
    cover_ref = _asset_ref(connection_id, _external_id(cover_id)) if cover_id else None
    return {
        "id": _album_ref(connection_id, album_id),
        "name": str(raw.get("albumName") or "Immich album")[:255],
        "description": str(raw.get("description") or ""),
        "cover_url": f"/api/gallery/immich/assets/{cover_ref}/thumbnail?size=thumbnail" if cover_ref else None,
        "count": max(0, _integer(raw.get("assetCount")) or 0),
        "created_at": raw.get("createdAt"),
        "source_type": "immich",
        "read_only": True,
    }


async def list_assets(
    owner: str | None,
    *,
    page: int = 1,
    size: int = 24,
    search: str | None = None,
    album: str | None = None,
    sort: str = "recent",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    page, size = max(1, int(page)), min(100, max(1, int(size)))
    album_id = _parse_album_ref(connection, album) if album else None
    values = {"page": page, "size": size, "search": search or "", "album": album_id or "", "sort": sort}
    key = _cache_key("assets", values)
    body: dict[str, Any] = {
        "page": page,
        "size": size,
        "withExif": True,
        "order": "asc" if sort == "oldest" else "desc",
    }
    if search:
        body["originalFileName"] = str(search)[:255]
    if album_id:
        body["albumIds"] = [album_id]
    try:
        response = await _request_json(
            connection, "POST", "/search/metadata", body=body, transport=transport
        )
        assets = response.get("assets") if isinstance(response, dict) else None
        if not isinstance(assets, dict) or not isinstance(assets.get("items"), list):
            raise ImmichError("invalid_response", "Immich returned an unexpected asset page")
        album_ref = album if album else None
        items = [
            _serialize_asset(connection["id"], item, album_ref)
            for item in assets["items"]
            if isinstance(item, dict)
        ]
        total = _integer(assets.get("total"))
        if total is None:
            total = (page - 1) * size + len(items) + (1 if assets.get("nextPage") or assets.get("nextCursor") else 0)
        result = {
            "items": items,
            "total": max(0, total),
            "source_state": {"status": "healthy", "stale": False},
        }
        _write_json_cache(owner, connection["id"], key, result)
        _record_status(owner, connection["id"], "healthy", None, synced=True)
        return result
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        cached = _read_json_cache(owner, connection["id"], key)
        if isinstance(cached, dict):
            cached["source_state"] = {"status": exc.code, "message": exc.message, "stale": True}
            return cached
        raise


async def list_albums(
    owner: str | None, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    connection = get_connection(owner)
    key = _cache_key("albums", {})
    try:
        response = await _request_json(connection, "GET", "/albums", transport=transport)
        if not isinstance(response, list):
            raise ImmichError("invalid_response", "Immich returned an unexpected album list")
        result = {
            "albums": [
                _serialize_album(connection["id"], item)
                for item in response
                if isinstance(item, dict)
            ],
            "source_state": {"status": "healthy", "stale": False},
        }
        _write_json_cache(owner, connection["id"], key, result)
        _record_status(owner, connection["id"], "healthy", None, synced=True)
        return result
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        cached = _read_json_cache(owner, connection["id"], key)
        if isinstance(cached, dict):
            cached["source_state"] = {"status": exc.code, "message": exc.message, "stale": True}
            return cached
        raise


async def get_thumbnail(
    owner: str | None,
    asset_ref: str,
    *,
    size: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str, str]:
    if size not in {"thumbnail", "preview"}:
        raise ImmichError("invalid_size", "Immich image size must be thumbnail or preview", status_code=400)
    connection = get_connection(owner)
    asset_id = _parse_asset_ref(connection, asset_ref)
    cached = _read_media_cache(owner, connection["id"], asset_id, size)
    try:
        content, media_type = await _request_bytes(
            connection,
            "GET",
            f"/assets/{asset_id}/thumbnail?size={size}&edited=true",
            max_bytes=MAX_THUMBNAIL_BYTES if size == "thumbnail" else MAX_PREVIEW_BYTES,
            accept="image/*",
            missing_code="missing_thumbnail",
            transport=transport,
        )
        if media_type.lower() not in SAFE_THUMBNAIL_TYPES:
            raise ImmichError("invalid_response", "Immich thumbnail format is unsafe")
        _write_media_cache(owner, connection["id"], asset_id, size, content, media_type)
        _record_status(owner, connection["id"], "healthy", None)
        return content, media_type, "healthy"
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        if exc.code == "offline" and cached:
            return cached[0], cached[1], "offline"
        raise


async def get_asset(
    owner: str | None,
    asset_ref: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    connection = get_connection(owner)
    asset_id = _parse_asset_ref(connection, asset_ref)
    try:
        response = await _request_json(
            connection, "GET", f"/assets/{asset_id}", transport=transport
        )
        if not isinstance(response, dict):
            raise ImmichError("invalid_response", "Immich returned invalid asset metadata")
        _record_status(owner, connection["id"], "healthy", None)
        return connection, response
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise


async def download_original_bounded(
    owner: str | None,
    asset_ref: str,
    *,
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    connection = get_connection(owner)
    asset_id = _parse_asset_ref(connection, asset_ref)
    try:
        result = await _request_bytes(
            connection,
            "GET",
            f"/assets/{asset_id}/original?edited=true",
            max_bytes=max_bytes,
            accept="application/octet-stream",
            transport=transport,
        )
        _record_status(owner, connection["id"], "healthy", None)
        return result
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise


async def open_original(
    owner: str | None,
    asset_ref: str,
    *,
    range_header: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[httpx.AsyncClient, httpx.Response]:
    connection = get_connection(owner)
    asset_id = _parse_asset_ref(connection, asset_ref)
    headers = _headers(connection, accept="application/octet-stream")
    if range_header:
        requested_range = range_header.strip()
        if (
            len(requested_range) > 64
            or not re.fullmatch(r"bytes=\d{0,20}-\d{0,20}", requested_range)
            or requested_range == "bytes=-"
        ):
            raise ImmichError("invalid_range", "Invalid download range", status_code=400)
        headers["Range"] = requested_range
    client = httpx.AsyncClient(timeout=60, follow_redirects=False, transport=transport)
    try:
        response = await client.send(
            client.build_request("GET", _url(connection, f"/assets/{asset_id}/original?edited=true"), headers=headers),
            stream=True,
        )
        _raise_status(response)
        return client, response
    except httpx.RequestError as exc:
        await client.aclose()
        raise ImmichError("offline", "Immich is unreachable") from exc
    except Exception:
        await client.aclose()
        raise


def _iso(value: datetime | None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def upload_asset(
    owner: str | None,
    path: Path,
    filename: str,
    *,
    created_at: datetime | None = None,
    modified_at: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    sha1 = hashlib.sha1()  # noqa: S324 - Immich's duplicate protocol requires SHA-1.
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha1.update(chunk)
    checksum = base64.b64encode(sha1.digest()).decode("ascii")
    headers = _headers(connection)
    headers["x-immich-checksum"] = checksum
    try:
        with path.open("rb") as source:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False, transport=transport) as client:
                response = await client.post(
                    _url(connection, "/assets"),
                    headers=headers,
                    data={"fileCreatedAt": _iso(created_at), "fileModifiedAt": _iso(modified_at or created_at)},
                    files={"assetData": (Path(filename).name, source, mimetypes.guess_type(filename)[0] or "application/octet-stream")},
                )
        _raise_status(response)
        if len(response.content) > MAX_JSON_BYTES:
            raise ImmichError("response_too_large", "Immich response exceeded the safe limit", status_code=413)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ImmichError("invalid_response", "Immich returned invalid upload metadata")
        status = str(payload.get("status") or ("duplicate" if response.status_code == 200 else "uploaded")).lower()
        _record_status(owner, connection["id"], "healthy", None)
        return {
            "ok": True,
            "status": status,
            "asset_id": payload.get("id"),
            "bytes": path.stat().st_size,
        }
    except ImmichError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    except (httpx.RequestError, json.JSONDecodeError, ValueError) as exc:
        _record_status(owner, connection["id"], "offline", "Immich is unreachable")
        raise ImmichError("offline", "Immich is unreachable") from exc
