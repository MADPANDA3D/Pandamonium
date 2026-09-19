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


async def _token(owner: str, url: str, headers: dict[str, str]) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if len(url) > 8192 or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Invalid media URL")
    now = time.monotonic()
    async with _TOKEN_LOCK:
        for key in [key for key, value in _TOKENS.items() if value.expires <= now]:
            _TOKENS.pop(key, None)
        # ponytail: bounded 512-entry scan; add a reverse index only if the cap grows.
        for key, value in _TOKENS.items():
            if value.owner == owner and value.url == url and value.headers == headers:
                return key
        while len(_TOKENS) >= 512:
            _TOKENS.pop(next(iter(_TOKENS)))
        key = secrets.token_urlsafe(24)
        _TOKENS[key] = _ProxyTarget(owner, url, headers, now + _TOKEN_TTL)
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


async def _open(target: _ProxyTarget, range_header: str | None):
    url = target.url
    headers = dict(target.headers)
    if range_header:
        if len(range_header) > 100 or not range_header.startswith("bytes="):
            raise HTTPException(416, "Invalid media range")
        headers["Range"] = range_header
    for hop in range(4):
        url = validate_public_http_url(url, max_length=8192)
        ips = _validated_public_ips(url)
        client = httpx.AsyncClient(
            transport=_StreamingPinnedTransport(ips[0]),
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        except Exception:
            await client.aclose()
            raise
        if response.is_redirect:
            location = response.headers.get("location")
            await response.aclose()
            await client.aclose()
            if not location or hop == 3:
                raise HTTPException(502, "Media redirect limit exceeded")
            url = urljoin(url, location)
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
            client, response, final_url = await _open(target, range)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise HTTPException(502, "Media source unavailable") from exc
        if response.status_code >= 400:
            status = response.status_code
            await response.aclose()
            await client.aclose()
            raise HTTPException(status if status in {404, 416} else 502, "Media source unavailable")
        content_type = response.headers.get("content-type", "application/octet-stream")
        if "mpegurl" in content_type.lower() or final_url.lower().split("?", 1)[0].endswith(".m3u8"):
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > _PLAYLIST_BYTES:
                    await response.aclose(); await client.aclose()
                    raise HTTPException(502, "Media playlist is too large")
            await response.aclose(); await client.aclose()
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
                await client.aclose()

        forwarded = {name: response.headers[name] for name in ("accept-ranges", "content-length", "content-range") if name in response.headers}
        forwarded.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        return StreamingResponse(stream(), status_code=response.status_code, media_type=content_type, headers=forwarded)

    return router
