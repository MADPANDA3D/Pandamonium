"""Generic live-catalog web runtime adapter for installed JOS extensions."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse

import httpx

from src.extension_installer import ExtensionLifecycleError, validate_web_entrypoint
from src.extension_registry import EXTENSION_ID_PATTERN, MANIFEST_VERSION


MAX_CATALOG_BYTES = 2 * 1024 * 1024
CatalogFetcher = Callable[[str, int], Mapping[str, Any]]


def configured_extension_urls(value: str | None = None) -> dict[str, str]:
    """Read the public extension-id to runtime-URL map from one JSON setting."""
    raw = os.getenv("ODYSSEUS_EXTENSION_URLS", "{}") if value is None else value
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise ExtensionLifecycleError("extension_runtime_urls_invalid") from exc
    if not isinstance(parsed, dict) or len(parsed) > 32:
        raise ExtensionLifecycleError("extension_runtime_urls_invalid")
    if any(not EXTENSION_ID_PATTERN.fullmatch(str(extension_id)) for extension_id in parsed):
        raise ExtensionLifecycleError("extension_runtime_urls_invalid")
    return {str(extension_id): _runtime_base_url(url) for extension_id, url in parsed.items()}


def _runtime_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/") + "/"
    parsed = urlparse(text)
    try:
        parsed.port
    except ValueError as exc:
        raise ExtensionLifecycleError("extension_runtime_url_invalid") from exc
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and local))
    ):
        raise ExtensionLifecycleError("extension_runtime_url_invalid")
    return text


def _default_catalog_fetcher(url: str, timeout_seconds: int) -> Mapping[str, Any]:
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=timeout_seconds) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise ExtensionLifecycleError("extension_catalog_unavailable")
                chunks = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_CATALOG_BYTES:
                        raise ExtensionLifecycleError("extension_catalog_unavailable")
                    chunks.append(chunk)
        value = json.loads(b"".join(chunks))
    except ExtensionLifecycleError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ExtensionLifecycleError("extension_catalog_unavailable") from exc
    if not isinstance(value, Mapping):
        raise ExtensionLifecycleError("extension_catalog_invalid")
    return value


class ExtensionRuntimeHost:
    """Small process-local availability index; durable lifecycle stays in the installer."""

    def __init__(self, urls: Mapping[str, str] | None = None):
        self.urls = dict(urls if urls is not None else configured_extension_urls())
        self._available: set[str] = set()
        self._lock = threading.RLock()

    def catalog_url(self, manifest: Mapping[str, Any]) -> str:
        extension_id = str(manifest.get("extension_id") or "")
        base = self.urls.get(extension_id)
        if not base:
            raise ExtensionLifecycleError("extension_runtime_url_unconfigured")
        endpoint = str((((manifest.get("capabilities") or {}).get("descriptor") or {}).get("endpoint")) or "")
        target = urljoin(base, endpoint)
        target_url = urlparse(target)
        base_url = urlparse(base)
        if (target_url.scheme, target_url.hostname, target_url.port) != (
            base_url.scheme,
            base_url.hostname,
            base_url.port,
        ):
            raise ExtensionLifecycleError("extension_catalog_origin_mismatch")
        return target

    def activate(self, extension_id: str) -> None:
        with self._lock:
            self._available.add(extension_id)

    def deactivate(self, extension_id: str) -> None:
        with self._lock:
            self._available.discard(extension_id)

    def available(self, extension_id: str) -> bool:
        with self._lock:
            return extension_id in self._available


class LiveCatalogWebAdapter:
    """Adapt a configured web runtime's native catalog into JOS-EXT-1 metadata."""

    def __init__(
        self,
        host: ExtensionRuntimeHost | None = None,
        *,
        catalog_fetcher: CatalogFetcher = _default_catalog_fetcher,
    ):
        self.host = host or extension_runtime_host
        self.catalog_fetcher = catalog_fetcher

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        lifecycle = manifest.get("lifecycle") or {}
        descriptor = ((manifest.get("capabilities") or {}).get("descriptor") or {})
        return (
            (manifest.get("runtime") or {}).get("type") == "web"
            and descriptor.get("type") == "live_catalog"
            and all(not lifecycle.get(name) for name in ("install", "start", "stop", "remove"))
        )

    def validate(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        source_revision: str,
    ) -> tuple[Mapping[str, Any], bool]:
        validate_web_entrypoint(install_path, manifest)
        extension_id = str(manifest["extension_id"])
        timeout = int((manifest.get("health") or {}).get("timeout_seconds") or 5)
        try:
            raw = self.catalog_fetcher(self.host.catalog_url(manifest), timeout)
        except ExtensionLifecycleError:
            raise
        except TimeoutError as exc:
            raise ExtensionLifecycleError("extension_catalog_timeout") from exc
        except Exception as exc:
            raise ExtensionLifecycleError("extension_catalog_unavailable") from exc
        if raw.get("protocol") != extension_id or not isinstance(raw.get("tools"), list):
            raise ExtensionLifecycleError("extension_catalog_invalid")
        return {
            "protocol_version": MANIFEST_VERSION,
            "extension_id": extension_id,
            "version": str(raw.get("version") or manifest.get("version") or "")[:80],
            "source_revision": source_revision,
            "tools": raw["tools"],
        }, True

    def activate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        self.host.activate(str(manifest["extension_id"]))

    def deactivate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        self.host.deactivate(str(manifest["extension_id"]))


extension_runtime_host = ExtensionRuntimeHost()
live_catalog_web_adapter = LiveCatalogWebAdapter(extension_runtime_host)
