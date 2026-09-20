"""Owner-scoped native Entertainment UI backed by installed upstream CLIs."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response, StreamingResponse

from src.auth_helpers import require_user
from src.authority_protocol import operator_identity
from src.extension_cli_adapter import execute_cli_tool
from src.extension_registry import ExtensionRegistry
from src.entertainment import PROVIDERS
from src.external_agent_bridge import _StreamingPinnedTransport
from src.url_security import validate_public_http_url
from src.webhook_manager import _validated_public_ips


_ALLOWED_HEADERS = {"referer": "Referer", "user-agent": "User-Agent", "origin": "Origin", "cookie": "Cookie"}
_PLAYLIST_BYTES = 2 * 1024 * 1024
_STREAM_BYTES = 512 * 1024 * 1024
_TOKEN_TTL = 30 * 60


@dataclass(frozen=True)
class _ProxyTarget:
    owner: str
    url: str
    headers: dict[str, str]
    expires: float


# ponytail: process-local tokens are enough for one browser session; use shared
# storage only if Pandamonium gains multiple web workers.
_TOKENS: dict[str, _ProxyTarget] = {}
_TOKEN_LOCK = asyncio.Lock()
# A 309-segment manifest creates ~310 tokens; ABR switching across quality
# levels and watching several episodes quickly overflowed the old 512 cap and
# evicted the *active* playlist's tokens, so later segments 404'd and playback
# stalled. Keep a larger bound plus an O(1) reverse index for dedupe.
_TOKEN_INDEX: dict[tuple[str, str, tuple], str] = {}
_TOKEN_CAP = 16384

# Reuse pinned connections + resolved IPs across HLS segments. Creating a fresh
# transport/client and re-resolving DNS for every segment paid a full TCP/TLS
# handshake per chunk of video, which is what made proxied playback crawl.
_CLIENTS: dict[tuple[str, str], httpx.AsyncClient] = {}
_CLIENT_LOCK = asyncio.Lock()
_IP_CACHE: dict[str, tuple[float, list]] = {}
_IP_TTL = 120.0
_MAX_PINNED_CLIENTS = 16
_MAX_PINNED_CONNECTIONS = 32


def _headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for raw_name, raw_value in value.items():
        name = _ALLOWED_HEADERS.get(str(raw_name).lower())
        text = str(raw_value)
        if name and text and len(text) <= 4096 and "\r" not in text and "\n" not in text:
            result[name] = text
    return result


def _token_index_key(owner: str, url: str, headers: dict[str, str]) -> tuple[str, str, tuple]:
    return (owner, url, tuple(sorted(headers.items())))


async def _token(owner: str, url: str, headers: dict[str, str]) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if len(url) > 8192 or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid media URL")
    now = time.monotonic()
    index_key = _token_index_key(owner, url, headers)
    async with _TOKEN_LOCK:
        existing = _TOKEN_INDEX.get(index_key)
        if existing is not None and existing in _TOKENS and _TOKENS[existing].expires > now:
            return existing
        if existing is not None:
            _TOKEN_INDEX.pop(index_key, None)
            _TOKENS.pop(existing, None)
        for key in [key for key, value in _TOKENS.items() if value.expires <= now]:
            target = _TOKENS.pop(key)
            _TOKEN_INDEX.pop(_token_index_key(target.owner, target.url, target.headers), None)
        while len(_TOKENS) >= _TOKEN_CAP:
            oldest = next(iter(_TOKENS))
            target = _TOKENS.pop(oldest)
            _TOKEN_INDEX.pop(_token_index_key(target.owner, target.url, target.headers), None)
        key = secrets.token_urlsafe(24)
        _TOKENS[key] = _ProxyTarget(owner, url, headers, now + _TOKEN_TTL)
        _TOKEN_INDEX[index_key] = key
    return key


async def _prepare_playback(owner: str, result: dict) -> dict:
    url = validate_public_http_url(str(result.get("url") or ""), max_length=8192)
    headers = _headers(result.get("headers"))
    prepared = dict(result)
    prepared["url"] = f"/api/entertainment/proxy/{await _token(owner, url, headers)}" if headers else url
    prepared["proxied"] = bool(headers)
    prepared["format"] = "hls" if ".m3u8" in url.lower() else "file"
    subtitles = []
    for raw in result.get("subtitles", [])[:16] if isinstance(result.get("subtitles"), list) else []:
        if not isinstance(raw, dict):
            continue
        sub_url = validate_public_http_url(str(raw.get("url") or ""), max_length=8192)
        sub_headers = _headers(raw.get("headers")) or headers
        subtitles.append({
            "url": f"/api/entertainment/proxy/{await _token(owner, sub_url, sub_headers)}" if sub_headers else sub_url,
            "label": str(raw.get("label") or "Subtitles")[:80],
            "language": str(raw.get("language") or "en")[:16],
        })
    prepared["subtitles"] = subtitles
    prepared.pop("headers", None)
    return prepared


def _pinned_ips(url: str) -> list:
    host = (urlparse(url).hostname or "").lower()
    now = time.monotonic()
    cached = _IP_CACHE.get(host)
    if cached and cached[0] > now:
        return cached[1]
    ips = _validated_public_ips(url)
    _IP_CACHE[host] = (now + _IP_TTL, ips)
    return ips


async def _pinned_client(url: str) -> httpx.AsyncClient:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    key = (origin, str(_pinned_ips(url)[0]))
    async with _CLIENT_LOCK:
        client = _CLIENTS.get(key)
        if client is not None:
            return client
        client = httpx.AsyncClient(
            transport=_StreamingPinnedTransport(_pinned_ips(url)[0], _MAX_PINNED_CONNECTIONS),
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=False,
            trust_env=False,
        )
        evicted = None
        if len(_CLIENTS) >= _MAX_PINNED_CLIENTS:
            _, evicted = _CLIENTS.popitem()
        _CLIENTS[key] = client
    if evicted is not None:
        asyncio.create_task(evicted.aclose())
    return client


async def _open(target: _ProxyTarget, range_header: str | None):
    url = target.url
    headers = dict(target.headers)
    if range_header:
        if len(range_header) > 100 or not range_header.startswith("bytes="):
            raise HTTPException(416, "Invalid media range")
        headers["Range"] = range_header
    seen = {url}
    for hop in range(4):
        url = validate_public_http_url(url, max_length=8192)
        client = await _pinned_client(url)
        response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        if response.is_redirect:
            location = response.headers.get("location")
            await response.aclose()
            next_url = urljoin(url, location) if location else None
            if not next_url or hop == 3 or next_url in seen:
                raise HTTPException(502, "Media redirect limit exceeded")
            seen.add(next_url)
            url = next_url
            continue
        return client, response, url
    raise HTTPException(502, "Media unavailable")


async def _playlist(owner: str, base_url: str, content: str, headers: dict[str, str]) -> str:
    import re

    async def replace_uri(match: re.Match) -> str:
        url = urljoin(base_url, match.group(1))
        return 'URI="/api/entertainment/proxy/' + await _token(owner, url, headers) + '"'

    lines = []
    for line in content.splitlines():
        if line and not line.startswith("#"):
            line = "/api/entertainment/proxy/" + await _token(owner, urljoin(base_url, line), headers)
        else:
            start = 0
            parts = []
            for match in re.finditer(r'URI="([^"]+)"', line):
                parts.extend((line[start:match.start()], await replace_uri(match)))
                start = match.end()
            if parts:
                parts.append(line[start:])
                line = "".join(parts)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _installed(owner: str) -> tuple[dict, dict]:
    identity = operator_identity(owner)
    try:
        from src.extension_installer import default_extensions_root
        import json

        lifecycle = json.loads((default_extensions_root() / "lifecycle.json").read_text())["extensions"]
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        lifecycle = {}
    records = ExtensionRegistry().snapshot()["extensions"]
    return identity, {
        key: record for key, record in records.items()
        if key in PROVIDERS and lifecycle.get(key, {}).get("owner_scope") == identity
    }


def setup_entertainment_routes() -> APIRouter:
    router = APIRouter(prefix="/api/entertainment", tags=["entertainment"])

    @router.get("")
    def status(owner: str = Depends(require_user)):
        identity, records = _installed(owner)
        if identity is None:
            return {"providers": []}
        return {"providers": [{
            "id": key,
            "label": PROVIDERS[key]["label"],
            "enabled": bool(records[key]["enabled"]),
        } for key in PROVIDERS if key in records]}

    @router.post("/{provider_id}/{operation}")
    async def invoke(provider_id: str, operation: str, body: dict, owner: str = Depends(require_user)):
        identity, records = _installed(owner)
        provider = PROVIDERS.get(provider_id)
        record = records.get(provider_id)
        tool = provider and provider["tools"].get(operation, (None,))[0]
        if not identity or not record:
            raise HTTPException(404, "Entertainment provider is not installed for this account")
        if not tool:
            raise HTTPException(404, "Unknown Entertainment operation")
        result = await execute_cli_tool(record, tool, body, identity)
        if result.get("exit_code") != 0:
            raise HTTPException(503, "Entertainment provider unavailable; check plugin setup or try again")
        payload = result["result"]
        return await _prepare_playback(identity, payload) if operation in {"resolve", "continue"} else payload

    @router.get("/proxy/{token}")
    async def proxy(token: str, range: str | None = Header(None), owner: str = Depends(require_user)):
        identity = operator_identity(owner)
        async with _TOKEN_LOCK:
            target = _TOKENS.get(token)
        if not identity or not target or target.owner != identity or target.expires <= time.monotonic():
            raise HTTPException(404, "Media link expired")
        try:
            _client, response, final_url = await _open(target, range)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise HTTPException(502, "Media source unavailable") from exc
        if response.status_code >= 400:
            status = response.status_code
            await response.aclose()
            raise HTTPException(status if status in {404, 416} else 502, "Media source unavailable")
        content_type = response.headers.get("content-type", "application/octet-stream")
        if "mpegurl" in content_type.lower() or final_url.lower().split("?", 1)[0].endswith(".m3u8"):
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > _PLAYLIST_BYTES:
                    await response.aclose()
                    raise HTTPException(502, "Media playlist is too large")
            await response.aclose()
            rewritten = await _playlist(identity, final_url, content.decode("utf-8", "replace"), target.headers)
            return Response(rewritten, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "private, no-store"})

        async def stream():
            total = 0
            try:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _STREAM_BYTES:
                        break
                    yield chunk
            finally:
                await response.aclose()

        forwarded = {name: response.headers[name] for name in ("accept-ranges", "content-length", "content-range") if name in response.headers}
        forwarded.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        return StreamingResponse(stream(), status_code=response.status_code, media_type=content_type, headers=forwarded)

    return router
