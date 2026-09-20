"""Owner-scoped native Entertainment UI backed by installed upstream CLIs."""

from __future__ import annotations

import asyncio
import os
import re
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
_IMAGE_BYTES = 12 * 1024 * 1024
_TOKEN_TTL = 12 * 60 * 60

# Cover art comes from keyless public metadata sources. Results are cached for a
# day so a search grid resolves art once and library lists stay instant.
_ANILIST_URL = "https://graphql.anilist.co"
_WIKIPEDIA_SEARCH = "https://en.wikipedia.org/w/api.php"
_WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_TVMAZE_SEARCH = "https://api.tvmaze.com/search/shows"
_TMDB_SEARCH = "https://api.themoviedb.org/3"
_TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
_IMAGE_UA = "Pandamonium/1.0 (media proxy; +https://github.com/MADPANDA3D/Pandamonium)"
_METADATA_TTL = 24 * 60 * 60
_METADATA_CAP = 4096
_ANILIST_BATCH = 20
_METADATA_CONCURRENCY = 4


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

# Metadata cache: (provider, lowercased title) -> (expires, cover url or None).
_METADATA: dict[tuple[str, str], tuple[float, str | None]] = {}
_METADATA_LOCK = asyncio.Lock()
_META_CLIENT: httpx.AsyncClient | None = None
# None = not looked up yet; "" = looked up and unavailable; otherwise the key.
_TMDB_KEY_CACHE: str | None = None


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


async def _touch(token: str, target: _ProxyTarget) -> None:
    # Sliding expiry: an actively-fetched stream keeps its tokens alive, so a
    # long episode or a preloaded next episode cannot expire mid-playback.
    now = time.monotonic()
    if target.expires - now >= _TOKEN_TTL / 2:
        return
    async with _TOKEN_LOCK:
        if _TOKENS.get(token) is target:
            _TOKENS[token] = _ProxyTarget(target.owner, target.url, target.headers, now + _TOKEN_TTL)


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


def _meta_client() -> httpx.AsyncClient:
    global _META_CLIENT
    if _META_CLIENT is None or _META_CLIENT.is_closed:
        _META_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=10),
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "Pandamonium/1.0 (+https://github.com/MADPANDA3D/Pandamonium)"},
        )
    return _META_CLIENT


def _title_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1}


def _split_year(title: str) -> tuple[str, str]:
    match = re.search(r"\s*\((\d{4})\)\s*$", title)
    if match:
        return title[:match.start()].strip(), match.group(1)
    return title.strip(), ""


def _anilist_variants(title: str) -> list[str]:
    base = re.sub(r"[\(\[].*?[\)\]]", " ", title)
    base = re.sub(r"[^0-9A-Za-z]+", " ", base).strip()
    tokens = base.split()
    variants = [base]
    no_digits = [token for token in tokens if not token.isdigit()]
    if no_digits:
        variants.append(" ".join(no_digits))
    filler = {"the", "a", "an", "of", "and", "to", "movie", "special", "ova", "ona", "season", "part", "chapter", "dub", "sub"}
    core = [token for token in no_digits if token.lower() not in filler]
    if core:
        variants.append(" ".join(core))
        if len(core) > 3:
            variants.append(" ".join([*core[:3], core[-1]]))
    return list(dict.fromkeys(variant.strip() for variant in variants if variant.strip()))


async def _anilist_best_match(client: httpx.AsyncClient, title: str) -> str | None:
    query_tokens = _title_tokens(title)
    best = None
    best_score = 0.0
    for variant in _anilist_variants(title):
        try:
            response = await client.post(_ANILIST_URL, json={
                "query": "query($s:String){Page(perPage:5){media(search:$s,type:ANIME)"
                         "{title{romaji english native} coverImage{large}}}}",
                "variables": {"s": variant},
            })
            nodes = ((response.json().get("data") or {}).get("Page") or {}).get("media") or [] if response.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            continue
        for node in nodes:
            cover = str(((node or {}).get("coverImage") or {}).get("large") or "").strip()
            if not cover:
                continue
            names = node.get("title") or {}
            for name in [names.get("romaji"), names.get("english"), names.get("native")]:
                tokens = _title_tokens(str(name or ""))
                if not tokens:
                    continue
                score = len(query_tokens & tokens) / max(1, len(query_tokens))
                if score > best_score:
                    best_score = score
                    best = cover
        if best_score >= 0.6:
            break
    return best if best_score >= 0.6 else None


async def _anilist_covers(titles: list[str]) -> dict[str, str]:
    covers: dict[str, str] = {}
    client = _meta_client()
    pending = list(dict.fromkeys(titles))
    for start in range(0, len(pending), _ANILIST_BATCH):
        batch = pending[start:start + _ANILIST_BATCH]
        variables = {f"t{index}": title for index, title in enumerate(batch)}
        # Page.media returns a list, so a title with no match yields an empty
        # list instead of a top-level null that makes AniList 404 the whole batch.
        query = "query(" + ",".join(f"$t{index}:String" for index in range(len(batch))) + "){" + " ".join(
            f'a{index}:Page(perPage:1){{media(search:$t{index},type:ANIME,sort:SEARCH_MATCH){{coverImage{{large}}}}}}'
            for index in range(len(batch))
        ) + "}"
        try:
            response = await client.post(_ANILIST_URL, json={"query": query, "variables": variables})
            data = response.json().get("data") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            data = None
        if not isinstance(data, dict):
            continue
        for index, title in enumerate(batch):
            media = (data.get(f"a{index}") or {}).get("media") or []
            cover = str(((media[0] if media else {}).get("coverImage") or {}).get("large") or "").strip()
            if cover:
                covers[title] = cover
    for title in [value for value in pending if value not in covers]:
        cover = await _anilist_best_match(client, title)
        if cover:
            covers[title] = cover
    return covers


async def _wikipedia_cover(title: str, kind: str) -> str | None:
    client = _meta_client()
    base, _year = _split_year(title)
    query = base if kind == "series" else f"{base} film"
    query_tokens = _title_tokens(base)
    try:
        search = await client.get(_WIKIPEDIA_SEARCH, params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": 5, "redirects": 1,
        })
        hits = search.json().get("query", {}).get("search", []) if search.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        hits = []
    best = None
    best_score = 0.0
    for hit in hits:
        page = str((hit or {}).get("title") or "").strip()
        if not page:
            continue
        try:
            summary = await client.get(_WIKIPEDIA_SUMMARY + page.replace(" ", "_"))
            payload = summary.json() if summary.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            continue
        cover = str(((payload or {}).get("thumbnail") or {}).get("source") or "").strip()
        if not cover:
            continue
        score = len(query_tokens & _title_tokens(page)) / max(1, len(query_tokens))
        if score > best_score:
            best_score = score
            best = cover
    if best_score >= 0.75:
        return best
    # Exact-title fallback only; never accept a loosely-related search hit.
    try:
        summary = await client.get(_WIKIPEDIA_SUMMARY + base.replace(" ", "_"))
        payload = summary.json() if summary.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    cover = str(((payload or {}).get("thumbnail") or {}).get("source") or "").strip()
    return cover or None


async def _wikipedia_covers(pairs: list[tuple[str, str]]) -> dict[str, str]:
    semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)

    async def one(title: str, kind: str):
        async with semaphore:
            return title, await _wikipedia_cover(title, kind)

    resolved = await asyncio.gather(*(one(title, kind) for title, kind in pairs))
    return {title: cover for title, cover in resolved if cover}


async def _tvmaze_cover(title: str) -> str | None:
    base, _year = _split_year(title)
    try:
        response = await _meta_client().get(_TVMAZE_SEARCH, params={"q": base})
        results = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    query_tokens = _title_tokens(base)
    best = None
    best_score = 0.0
    for entry in results or []:
        show = (entry or {}).get("show") or {}
        image = str(((show.get("image") or {}).get("original") or "")).strip()
        if not image:
            continue
        score = len(query_tokens & _title_tokens(str(show.get("name") or ""))) / max(1, len(query_tokens))
        if score > best_score:
            best_score = score
            best = image
    return best if best_score >= 0.6 else None


async def _tvmaze_covers(titles: list[str]) -> dict[str, str]:
    semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)

    async def one(title: str):
        async with semaphore:
            return title, await _tvmaze_cover(title)

    resolved = await asyncio.gather(*(one(title) for title in titles))
    return {title: cover for title, cover in resolved if cover}


def _tmdb_key() -> str | None:
    global _TMDB_KEY_CACHE
    if _TMDB_KEY_CACHE is not None:
        return _TMDB_KEY_CACHE or None
    key = (os.getenv("PANDAMONIUM_TMDB_API_KEY") or os.getenv("TMDB_API_KEY") or "").strip()
    if not key:
        try:
            from src.extension_installer import default_extensions_root

            for path in default_extensions_root().glob("installed/pandaflix/revisions/*/core/tmdb.go"):
                match = re.search(r'TMDB_API_KEY\s*=\s*"([0-9a-fA-F]{16,})"', path.read_text(errors="replace"))
                if match:
                    key = match.group(1)
                    break
        except (OSError, ImportError):
            key = ""
    _TMDB_KEY_CACHE = key
    return key or None


async def _tmdb_cover(client: httpx.AsyncClient, title: str, kind: str, key: str) -> str | None:
    base, year = _split_year(title)
    if not base:
        return None
    path = "/search/tv" if kind == "series" else ("/search/movie" if kind == "movie" else "/search/multi")
    params = {"api_key": key, "language": "en-US", "query": base}
    if year and kind in {"movie", "series"}:
        params["first_air_date_year" if kind == "series" else "year"] = year
    try:
        response = await client.get(_TMDB_SEARCH + path, params=params)
        results = response.json().get("results") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    query_tokens = _title_tokens(base)
    best = None
    best_score = 0.0
    for result in results or []:
        poster = str((result or {}).get("poster_path") or "").strip()
        if not poster:
            continue
        name = str((result or {}).get("name") or (result or {}).get("title") or "")
        score = len(query_tokens & _title_tokens(name)) / max(1, len(query_tokens))
        if score > best_score:
            best_score = score
            best = _TMDB_IMAGE + poster
    return best if best_score >= 0.6 else None


async def _tmdb_covers(pairs: list[tuple[str, str]], key: str) -> dict[str, str]:
    semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)
    client = _meta_client()

    async def one(title: str, kind: str):
        async with semaphore:
            return title, await _tmdb_cover(client, title, kind, key)

    resolved = await asyncio.gather(*(one(title, kind) for title, kind in pairs))
    return {title: cover for title, cover in resolved if cover}


async def _metadata(provider: str, items: list[dict]) -> dict[str, str]:
    now = time.monotonic()
    covers: dict[str, str] = {}
    wanted: list[str] = []
    kinds: dict[str, str] = {}
    async with _METADATA_LOCK:
        for item in items:
            title = str(item.get("title") or "").strip()[:300]
            if not title:
                continue
            kinds.setdefault(title, str(item.get("kind") or ""))
            cached = _METADATA.get((provider, title.lower()))
            if cached and cached[0] > now:
                if cached[1]:
                    covers[title] = cached[1]
                continue
            if title not in wanted:
                wanted.append(title)
    if not wanted:
        return covers
    if provider == "ani-cli":
        resolved = await _anilist_covers(wanted)
    elif provider == "pandaflix":
        pairs = [(title, kinds.get(title, "")) for title in wanted]
        resolved = {}
        key = _tmdb_key()
        if key:
            # PandaFlix is TMDB-backed; use the same source it uses natively.
            resolved.update(await _tmdb_covers(pairs, key))
        missing = [(title, kind) for title, kind in pairs if title not in resolved]
        series = [title for title, kind in missing if kind == "series"]
        movies = [title for title, kind in missing if kind != "series"]
        if series:
            resolved.update(await _tvmaze_covers(series))
            still = [title for title in series if title not in resolved]
            if still:
                resolved.update(await _wikipedia_covers([(title, "series") for title in still]))
        if movies:
            resolved.update(await _wikipedia_covers([(title, "movie") for title in movies]))
    else:
        resolved = {}
    async with _METADATA_LOCK:
        for title in wanted:
            _METADATA[(provider, title.lower())] = (now + _METADATA_TTL, resolved.get(title))
        while len(_METADATA) > _METADATA_CAP:
            _METADATA.pop(next(iter(_METADATA)))
    covers.update(resolved)
    return covers


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

    @router.post("/metadata")
    async def metadata(body: dict, owner: str = Depends(require_user)):
        identity = operator_identity(owner)
        provider = str(body.get("provider") or "")
        raw_items = body.get("items")
        if not identity or provider not in PROVIDERS or not isinstance(raw_items, list):
            raise HTTPException(400, "Invalid metadata request")
        items = [item for item in raw_items[:50] if isinstance(item, dict)]
        covers = await _metadata(provider, items)
        result = {}
        for title, url in covers.items():
            try:
                result[title] = f"/api/entertainment/image/{await _token(identity, url, {'User-Agent': _IMAGE_UA})}"
            except ValueError:
                continue
        return {"covers": result}

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
        await _touch(token, target)
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

    @router.get("/image/{token}")
    async def image(token: str, owner: str = Depends(require_user)):
        identity = operator_identity(owner)
        async with _TOKEN_LOCK:
            target = _TOKENS.get(token)
        if not identity or not target or target.owner != identity or target.expires <= time.monotonic():
            raise HTTPException(404, "Media link expired")
        await _touch(token, target)
        try:
            _client, response, _final = await _open(target, None)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            raise HTTPException(502, "Media source unavailable") from exc
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if response.status_code >= 400 or not content_type.startswith("image/"):
            await response.aclose()
            raise HTTPException(502, "Cover art unavailable")

        async def stream():
            total = 0
            try:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > _IMAGE_BYTES:
                        break
                    yield chunk
            finally:
                await response.aclose()

        return StreamingResponse(stream(), media_type=content_type, headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        })

    return router
