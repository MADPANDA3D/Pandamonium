"""Owner-scoped Nextcloud connection and bounded, read-only WebDAV access (MAD-937).

The connection is an owner-scoped ``Integration`` row whose app password is
encrypted at rest. Browsing, searching, and reading go through WebDAV under
``/remote.php/dav/files/<username>/`` with a hard response ceiling and
timeouts; every request is read-only (PROPFIND/GET) and there is no upload or
write path in v1.

Safety contract:

* No credentials in payloads, errors, or citations. The app password is only
  decrypted to build the Authorization header.
* Secret-shaped paths are excluded from listings/search and refused on read.
* Paths are normalized and traversal-free before they reach the server URL.
* Responses are bounded; reads truncate at an explicit byte cap.
* Unreachable/auth-failure/server states map to honest copy and a persisted
  status, never to raw upstream bodies.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import socket
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from core.database import Integration, SessionLocal
from src.immich_gallery import _tailnet_devices, _tailscale_status
from src.secret_storage import decrypt, encrypt, is_encrypted
from src.url_safety import check_outbound_url

INTEGRATION_TYPE = "nextcloud_files"
CONNECTION_NAME = "primary"

REQUEST_TIMEOUT_SECONDS = 20
MAX_PROPFIND_BYTES = 1024 * 1024
MAX_STATUS_BYTES = 256 * 1024
MAX_TEXT_READ_BYTES = 64 * 1024
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_LIST_ENTRIES = 500
MAX_SEARCH_FOLDERS = 40
MAX_SEARCH_RESULTS = 50
MIN_SEARCH_QUERY_CHARS = 2
MAX_REMOTE_PATH_CHARS = 1024
MAX_USERNAME_CHARS = 255
MAX_PASSWORD_CHARS = 4096

_DISCOVERY_MAX_DEVICES = 16
_DISCOVERY_MAX_BYTES = 4096
_DISCOVERY_TIMEOUT = 1.5
_DISCOVERY_DEADLINE = 5.0

_UNSET = object()
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DAV = "{DAV:}"

TEXT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "application/sql",
        "application/x-sh",
    }
)
TEXT_EXTENSIONS = frozenset(
    {
        ".cfg",
        ".conf",
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".log",
        ".markdown",
        ".md",
        ".org",
        ".py",
        ".rst",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)
REDACTED_PATH_PARTS = frozenset(
    {
        ".aws",
        ".env",
        ".git",
        ".gnupg",
        ".htpasswd",
        ".kube",
        ".netrc",
        ".pgpass",
        ".ssh",
        "authorized_keys",
        "config.php",
        "credentials",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "passwords",
        "secrets",
        "shadow",
    }
)
REDACTED_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore")

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
    'xmlns:nc="http://nextcloud.org/ns">'
    "<d:prop>"
    "<d:displayname/>"
    "<d:getcontentlength/>"
    "<d:getcontenttype/>"
    "<d:getlastmodified/>"
    "<d:resourcetype/>"
    "<d:getetag/>"
    "</d:prop>"
    "</d:propfind>"
).encode("utf-8")


class NextcloudError(Exception):
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


# ── connection storage ───────────────────────────────────────────────────


def _owner_query(db: Any, owner: str | None):
    query = db.query(Integration).filter(
        Integration.type == INTEGRATION_TYPE,
        Integration.name == CONNECTION_NAME,
    )
    return query.filter(Integration.owner.is_(None) if owner is None else Integration.owner == owner)


def _normalize_server_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("Nextcloud server URL is required")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Nextcloud server URL must be HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("Nextcloud server URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Nextcloud server URL cannot contain a query or fragment")
    path = parsed.path.rstrip("/")
    for suffix in ("/remote.php/dav", "/remote.php", "/index.php"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    cleaned = urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", "")).rstrip("/")
    ok, reason = check_outbound_url(cleaned, block_private=False)
    if not ok:
        raise ValueError(f"Nextcloud server URL rejected: {reason}")
    return cleaned


def _validate_username(value: Any) -> str:
    username = str(value or "").strip()
    if not username or len(username) > MAX_USERNAME_CHARS or _CONTROL_RE.search(username):
        raise ValueError("Nextcloud username is required")
    return username


def get_connection(owner: str | None, *, require_enabled: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise NextcloudError("unconfigured", "Nextcloud is not connected", status_code=404)
        config = dict(row.config or {})
        result = {
            "id": row.id,
            "owner": row.owner,
            "enabled": bool(row.enabled),
            "base_url": str(config.get("base_url") or ""),
            "username": str(config.get("username") or ""),
            "app_password": decrypt(str(config.get("app_password") or "")),
            "status": str(config.get("status") or ("untested" if row.enabled else "disabled")),
            "last_error": config.get("last_error"),
            "last_checked_at": config.get("last_checked_at"),
            "last_synced_at": config.get("last_synced_at"),
        }
    finally:
        db.close()
    if require_enabled and not result["enabled"]:
        raise NextcloudError("disabled", "Nextcloud connection is disabled", status_code=409)
    if require_enabled and not result["app_password"]:
        raise NextcloudError(
            "missing_password", "Nextcloud app password is unavailable", status_code=401
        )
    return result


def connection_status(owner: str | None) -> dict[str, Any]:
    try:
        connection = get_connection(owner, require_enabled=False)
    except NextcloudError as exc:
        if exc.code == "unconfigured":
            return {
                "configured": False,
                "enabled": False,
                "status": "unconfigured",
                "app_password_configured": False,
            }
        raise
    return {
        "configured": True,
        "enabled": connection["enabled"],
        "server_url": connection["base_url"],
        "username": connection["username"],
        "status": connection["status"] if connection["enabled"] else "disabled",
        "app_password_configured": bool(connection["app_password"]),
        "last_error": connection["last_error"],
        "last_checked_at": connection["last_checked_at"],
        "last_synced_at": connection["last_synced_at"],
    }


def save_connection(
    owner: str | None,
    *,
    server_url: str | None = None,
    username: str | None = None,
    app_password: object = _UNSET,
    enabled: bool | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        config = dict(row.config or {}) if row else {}
        if server_url is not None:
            config["base_url"] = _normalize_server_url(server_url)
        if username is not None:
            config["username"] = _validate_username(username)
        if app_password is not _UNSET:
            value = str(app_password or "").strip()
            if not value:
                raise ValueError("Nextcloud app password cannot be blank")
            if len(value) > MAX_PASSWORD_CHARS or is_encrypted(value):
                raise ValueError("Invalid Nextcloud app password")
            config["app_password"] = encrypt(value)
        if not config.get("base_url"):
            raise ValueError("Nextcloud server URL is required")
        if not config.get("username"):
            raise ValueError("Nextcloud username is required")
        if not config.get("app_password"):
            raise ValueError("Nextcloud app password is required")
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


def remove_connection(owner: str | None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise NextcloudError("unconfigured", "Nextcloud is not connected", status_code=404)
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"ok": True}


def _record_status(
    owner: str | None,
    connection_id: str,
    status: str,
    error: str | None = None,
    *,
    synced: bool = False,
) -> None:
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


# ── paths and redaction ──────────────────────────────────────────────────


def is_redacted_path(path: Any) -> bool:
    value = str(path or "").replace("\\", "/")
    for raw_part in value.split("/"):
        part = raw_part.strip().casefold()
        if not part or part in {".", ".."}:
            continue
        if part in REDACTED_PATH_PARTS or part.startswith(".env"):
            return True
        if part.endswith(REDACTED_SUFFIXES):
            return True
    return False


def normalize_remote_path(value: Any) -> str:
    raw = str(value if value is not None else "").strip().replace("\\", "/")
    if raw in {"", "/"}:
        return ""
    if len(raw) > MAX_REMOTE_PATH_CHARS or _CONTROL_RE.search(raw):
        raise NextcloudError("invalid_path", "That path is not available.", status_code=400)
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise NextcloudError("invalid_path", "That path is not available.", status_code=400)
    return "/".join(parts)


def _encoded_path(path: str) -> str:
    return "/".join(quote(part, safe="") for part in str(path or "").split("/") if part)


def _file_ref(connection: dict[str, Any], path: str) -> str:
    return f"nextcloud:{connection['id']}:{path}"


def _citation(connection: dict[str, Any], *, path: str | None = None, query: str | None = None) -> str:
    node = f'Nextcloud "{connection["base_url"]}"'
    if query is not None:
        return f'{node} search "{query}"'
    if path:
        return f"{node}: /{path}"
    return node


def _source(
    connection: dict[str, Any],
    *,
    path: str | None = None,
    query: str | None = None,
    is_dir: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "nextcloud_file",
        "connection_id": connection["id"],
        "node": connection["base_url"],
        "username": connection["username"],
    }
    if path is not None:
        result["path"] = path
    if query is not None:
        result["query"] = query
    result["is_dir"] = bool(is_dir)
    return result


# ── HTTP / WebDAV ────────────────────────────────────────────────────────


def _auth_header(connection: dict[str, Any]) -> str:
    material = f"{connection['username']}:{connection['app_password']}".encode("utf-8")
    return "Basic " + base64.b64encode(material).decode("ascii")


def _dav_url(connection: dict[str, Any], path: str, *, collection: bool = False) -> str:
    root = (
        f"{connection['base_url']}/remote.php/dav/files/"
        f"{quote(connection['username'], safe='')}"
    )
    encoded = _encoded_path(path)
    url = f"{root}/{encoded}" if encoded else root
    if collection and not url.endswith("/"):
        url += "/"
    return url


def _raise_status(response: httpx.Response, *, missing_code: str = "not_found") -> None:
    if response.status_code < 300:
        return
    if response.status_code == 401:
        raise NextcloudError(
            "auth_failed", "Nextcloud rejected the app password", status_code=401
        )
    if response.status_code == 403:
        raise NextcloudError(
            "permission", "The app password lacks access to that path", status_code=403
        )
    if response.status_code == 404:
        raise NextcloudError("not_found", "Nextcloud item is not available", status_code=404)
    if response.status_code == 405:
        raise NextcloudError(
            "unsupported", "Nextcloud does not support that read operation", status_code=405
        )
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")[:32] or None
        raise NextcloudError(
            "rate_limited", "Nextcloud rate limit reached", status_code=429, retry_after=retry
        )
    if 300 <= response.status_code < 400:
        raise NextcloudError("redirect_blocked", "Nextcloud returned an unsafe redirect", status_code=502)
    raise NextcloudError("server_error", "Nextcloud is unavailable", status_code=502)


async def _request_bounded(
    connection: dict[str, Any],
    method: str,
    path: str,
    *,
    dav: bool = True,
    collection: bool = False,
    depth: int | None = None,
    body: bytes | None = None,
    accept: str = "application/xml",
    max_bytes: int,
    truncate: bool = False,
    missing_code: str = "not_found",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str, bool]:
    """One bounded request. Returns (content, media_type, truncated)."""
    url = _dav_url(connection, path, collection=collection) if dav else (
        f"{connection['base_url']}/{str(path or '').lstrip('/')}"
    )
    headers = {"Authorization": _auth_header(connection), "Accept": accept}
    if depth is not None:
        headers["Depth"] = str(int(depth))
    if body is not None:
        headers["Content-Type"] = "application/xml; charset=utf-8"
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream(method, url, headers=headers, content=body) as response:
                _raise_status(response, missing_code=missing_code)
                try:
                    declared = int(response.headers.get("content-length", "0") or 0)
                except ValueError:
                    declared = 0
                if declared > max_bytes and not truncate:
                    raise NextcloudError(
                        "response_too_large",
                        "Nextcloud response exceeded the safe limit",
                        status_code=413,
                    )
                chunks: list[bytes] = []
                received = 0
                truncated = False
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received >= max_bytes:
                        chunks.append(chunk[: max_bytes - (received - len(chunk))])
                        truncated = True
                        break
                    chunks.append(chunk)
                content_type = response.headers.get("content-type", "application/octet-stream")
                return b"".join(chunks), content_type.split(";", 1)[0].strip().lower(), truncated
    except NextcloudError:
        raise
    except httpx.RequestError as exc:
        raise NextcloudError("offline", "Nextcloud is unreachable") from exc


def _safe_media_type(media_type: str, path: str) -> str:
    if media_type.startswith("text/") or media_type in TEXT_MEDIA_TYPES:
        return "text"
    suffix = "." + path.rsplit(".", 1)[-1].casefold() if "." in path.rsplit("/", 1)[-1] else ""
    return "text" if suffix in TEXT_EXTENSIONS else "binary"


def _parse_multistatus(
    content: bytes,
    connection: dict[str, Any],
    requested_path: str,
) -> tuple[list[dict[str, Any]], bool]:
    head = content[:4096].upper()
    if b"<!DOCTYPE" in head:
        raise NextcloudError("invalid_response", "Nextcloud returned an unsafe XML document")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise NextcloudError("invalid_response", "Nextcloud returned invalid XML") from exc
    root_prefix = f"/remote.php/dav/files/{connection['username']}".casefold()
    requested = requested_path.strip("/").casefold()
    entries: list[dict[str, Any]] = []
    truncated = False
    for response in root.iter(f"{_DAV}response"):
        href_el = response.find(f"{_DAV}href")
        href = unquote(str(href_el.text or "")).strip() if href_el is not None else ""
        if not href:
            continue
        normalized_href = href.rstrip("/")
        lowered = normalized_href.casefold()
        if not lowered.startswith(root_prefix):
            continue
        relative = normalized_href[len(root_prefix):].strip("/")
        if relative.casefold() == requested:
            continue
        prop: ElementTree.Element | None = None
        for propstat in response.findall(f"{_DAV}propstat"):
            status_el = propstat.find(f"{_DAV}status")
            status_text = str(status_el.text or "") if status_el is not None else ""
            if "200" not in status_text:
                continue
            prop = propstat.find(f"{_DAV}prop")
            if prop is not None:
                break
        if prop is None:
            continue
        if is_redacted_path(relative):
            continue
        resourcetype = prop.find(f"{_DAV}resourcetype")
        is_dir = resourcetype is not None and resourcetype.find(f"{_DAV}collection") is not None
        display_el = prop.find(f"{_DAV}displayname")
        name = str(display_el.text or "").strip() if display_el is not None else ""
        if not name:
            name = relative.rsplit("/", 1)[-1]
        length_el = prop.find(f"{_DAV}getcontentlength")
        try:
            size = int(str(length_el.text)) if length_el is not None and length_el.text else None
        except ValueError:
            size = None
        content_type_el = prop.find(f"{_DAV}getcontenttype")
        content_type = (
            str(content_type_el.text or "").strip() if content_type_el is not None else ""
        )
        modified_el = prop.find(f"{_DAV}getlastmodified")
        modified = str(modified_el.text or "").strip() if modified_el is not None else ""
        etag_el = prop.find(f"{_DAV}getetag")
        etag = str(etag_el.text or "").strip() if etag_el is not None else ""
        entry: dict[str, Any] = {
            "name": name or relative,
            "path": relative,
            "is_dir": is_dir,
            "size": size if not is_dir else None,
            "content_type": content_type or None,
            "modified": modified or None,
            "etag": etag or None,
            "ref": _file_ref(connection, relative),
        }
        if not is_dir:
            entry["download_url"] = f"/api/nextcloud/download?path={quote(relative, safe='')}"
        entries.append(entry)
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
    entries.sort(key=lambda item: (not item["is_dir"], str(item["name"]).casefold()))
    return entries, truncated


# ── public read-only API ─────────────────────────────────────────────────


async def test_connection(
    owner: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    try:
        content, _media, _truncated = await _request_bounded(
            connection,
            "GET",
            "status.php",
            dav=False,
            accept="application/json",
            max_bytes=MAX_STATUS_BYTES,
            missing_code="not_found",
            transport=transport,
        )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NextcloudError("invalid_response", "Nextcloud returned invalid status") from exc
        if not isinstance(payload, dict) or payload.get("installed") is not True:
            raise NextcloudError(
                "invalid_response", "The server did not report an installed Nextcloud", status_code=502
            )
        await _request_bounded(
            connection,
            "PROPFIND",
            "",
            collection=True,
            depth=0,
            body=_PROPFIND_BODY,
            max_bytes=MAX_PROPFIND_BYTES,
            transport=transport,
        )
    except NextcloudError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    _record_status(owner, connection["id"], "healthy", None)
    return {"ok": True, "status": "healthy", "message": "Nextcloud files are readable"}


async def list_folder(
    owner: str | None,
    path: str = "",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    folder = normalize_remote_path(path)
    if folder and is_redacted_path(folder):
        raise NextcloudError(
            "redacted_path", "That path is excluded from read-only access.", status_code=403
        )
    try:
        content, _media, _truncated = await _request_bounded(
            connection,
            "PROPFIND",
            folder,
            collection=True,
            depth=1,
            body=_PROPFIND_BODY,
            max_bytes=MAX_PROPFIND_BYTES,
            transport=transport,
        )
        entries, truncated = _parse_multistatus(content, connection, folder)
    except NextcloudError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    _record_status(owner, connection["id"], "healthy", None, synced=True)
    return {
        "path": folder,
        "entries": entries,
        "truncated": truncated,
        "source_state": {"status": "healthy", "stale": False},
        "source": _source(connection, path=folder, is_dir=True),
        "citation": _citation(connection, path=folder),
    }


async def search_files(
    owner: str | None,
    query: str,
    *,
    path: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    needle = " ".join(str(query or "").split()).casefold()
    if len(needle) < MIN_SEARCH_QUERY_CHARS:
        raise NextcloudError(
            "invalid_query",
            f"Enter at least {MIN_SEARCH_QUERY_CHARS} characters to search files.",
            status_code=400,
        )
    start = normalize_remote_path(path)
    if start and is_redacted_path(start):
        raise NextcloudError(
            "redacted_path", "That path is excluded from read-only access.", status_code=403
        )
    queue = [start]
    visited: set[str] = set()
    seen_results: set[str] = set()
    results: list[dict[str, Any]] = []
    truncated = False
    try:
        while queue:
            folder = queue.pop(0)
            if folder in visited:
                continue
            if len(visited) >= MAX_SEARCH_FOLDERS:
                truncated = True
                break
            visited.add(folder)
            content, _media, _truncated = await _request_bounded(
                connection,
                "PROPFIND",
                folder,
                collection=True,
                depth=1,
                body=_PROPFIND_BODY,
                max_bytes=MAX_PROPFIND_BYTES,
                transport=transport,
            )
            entries, _list_truncated = _parse_multistatus(content, connection, folder)
            for entry in entries:
                if entry["is_dir"]:
                    if len(visited) + len(queue) < MAX_SEARCH_FOLDERS:
                        queue.append(entry["path"])
                    continue
                if needle in str(entry["name"]).casefold():
                    if entry["path"] in seen_results:
                        continue
                    seen_results.add(entry["path"])
                    results.append(entry)
                    if len(results) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break
            if len(results) >= MAX_SEARCH_RESULTS:
                break
    except NextcloudError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    _record_status(owner, connection["id"], "healthy", None, synced=True)
    return {
        "query": str(query or "").strip(),
        "results": results,
        "truncated": truncated,
        "folders_visited": len(visited),
        "source_state": {"status": "healthy", "stale": False},
        "source": _source(connection, path=start, query=str(query or "").strip()),
        "citation": _citation(connection, query=str(query or "").strip()),
    }


async def read_file(
    owner: str | None,
    path: str,
    *,
    max_bytes: int = MAX_TEXT_READ_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    connection = get_connection(owner)
    file_path = normalize_remote_path(path)
    if not file_path:
        raise NextcloudError("invalid_path", "Enter the file path to read.", status_code=400)
    if is_redacted_path(file_path):
        raise NextcloudError(
            "redacted_path", "That path is excluded from read-only access.", status_code=403
        )
    try:
        requested = max(1, min(int(max_bytes), MAX_TEXT_READ_BYTES))
    except (TypeError, ValueError):
        requested = MAX_TEXT_READ_BYTES
    try:
        content, media_type, truncated = await _request_bounded(
            connection,
            "GET",
            file_path,
            accept="*/*",
            max_bytes=requested,
            truncate=True,
            transport=transport,
        )
    except NextcloudError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    if _safe_media_type(media_type, file_path) != "text":
        raise NextcloudError(
            "not_text",
            f"That file is not a text file ({media_type or 'unknown type'}).",
            status_code=415,
        )
    text = content.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n... [truncated at {requested} bytes]"
    _record_status(owner, connection["id"], "healthy", None)
    return {
        "path": file_path,
        "content": text,
        "bytes": len(content),
        "truncated": truncated,
        "media_type": media_type,
        "file_ref": _file_ref(connection, file_path),
        "source": _source(connection, path=file_path),
        "citation": _citation(connection, path=file_path),
    }


async def download_file(
    owner: str | None,
    path: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    connection = get_connection(owner)
    file_path = normalize_remote_path(path)
    if not file_path:
        raise NextcloudError("invalid_path", "Enter the file path to download.", status_code=400)
    if is_redacted_path(file_path):
        raise NextcloudError(
            "redacted_path", "That path is excluded from read-only access.", status_code=403
        )
    try:
        content, media_type, _truncated = await _request_bounded(
            connection,
            "GET",
            file_path,
            accept="*/*",
            max_bytes=max(1, min(int(max_bytes), MAX_DOWNLOAD_BYTES)),
            missing_code="not_found",
            transport=transport,
        )
    except NextcloudError as exc:
        _record_status(owner, connection["id"], exc.code, exc.message)
        raise
    _record_status(owner, connection["id"], "healthy", None)
    return content, media_type or "application/octet-stream"


# ── tailnet discovery ────────────────────────────────────────────────────


def _nextcloud_probe_targets(device: dict[str, str]) -> list[str]:
    targets: list[str] = []
    dns_name = device.get("dns_name", "")
    address = device.get("address", "")
    if dns_name:
        targets.extend((f"https://{dns_name}", f"http://{dns_name}"))
    if address:
        targets.extend((f"http://{address}", f"http://{address}:8080"))
    deduped: list[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)
    return deduped


async def discover_tailnet_nextcloud(
    *,
    status: dict[str, Any] | None | object = _UNSET,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fingerprint Nextcloud on online tailnet devices without sending credentials."""
    snapshot = await asyncio.to_thread(_tailscale_status) if status is _UNSET else status
    fallback_name = socket.gethostname() or "This device"
    if not isinstance(snapshot, dict):
        return {
            "available": False,
            "self_name": fallback_name,
            "devices_checked": 0,
            "candidates": [],
            "message": "Tailscale is unavailable; manual server URLs still work.",
        }
    self_name, devices = _tailnet_devices(snapshot)
    probes = [
        (device, base_url)
        for device in devices
        for base_url in _nextcloud_probe_targets(device)
    ]
    semaphore = asyncio.Semaphore(16)

    async def probe(client: httpx.AsyncClient, device: dict[str, str], base_url: str):
        try:
            normalized = _normalize_server_url(base_url)
            async with semaphore:
                async with client.stream("GET", f"{normalized}/status.php") as response:
                    if response.status_code != 200:
                        return None
                    declared = int(response.headers.get("content-length", "0") or 0)
                    if declared > _DISCOVERY_MAX_BYTES:
                        return None
                    chunks: list[bytes] = []
                    received = 0
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > _DISCOVERY_MAX_BYTES:
                            return None
                        chunks.append(chunk)
            payload = json.loads(b"".join(chunks))
            if not isinstance(payload, dict) or payload.get("installed") is not True:
                return None
            return {
                "id": f"nextcloud:{hashlib.sha256(normalized.encode()).hexdigest()[:16]}",
                "kind": "nextcloud",
                "provider": "Nextcloud",
                "label": str(payload.get("productname") or "Nextcloud"),
                "device": device["name"],
                "location": normalized,
                "server_url": normalized,
                "state": "available",
                "connected": False,
                "connectable": True,
            }
        except (ValueError, json.JSONDecodeError, httpx.HTTPError):
            return None

    candidates: list[dict[str, Any]] = []
    if probes:
        async with httpx.AsyncClient(
            timeout=_DISCOVERY_TIMEOUT,
            follow_redirects=False,
            transport=transport,
        ) as client:
            tasks = [
                asyncio.create_task(probe(client, device, url))
                for device, url in probes
            ]
            _done, pending = await asyncio.wait(tasks, timeout=_DISCOVERY_DEADLINE)
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        seen_devices: set[str] = set()
        for candidate in results:
            if not isinstance(candidate, dict) or candidate["device"] in seen_devices:
                continue
            seen_devices.add(candidate["device"])
            candidates.append(candidate)
    count = len(devices)
    if candidates:
        noun = "instance" if len(candidates) == 1 else "instances"
        message = f"Found {len(candidates)} Nextcloud {noun} across {count} online tailnet devices."
    elif count:
        message = f"Scanned {count} online tailnet devices; no Nextcloud server answered."
    else:
        message = "No online tailnet devices were available to scan."
    return {
        "available": True,
        "self_name": self_name,
        "devices_checked": count,
        "candidates": candidates,
        "message": message,
    }
