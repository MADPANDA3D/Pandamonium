"""Pinned marketplace trust, bounded refresh and an atomic last-known-good cache."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from core.atomic_io import atomic_write_json
from src.marketplace_catalog import MarketplaceCatalogError, validate_published_catalog

REPOSITORY = "MADPANDA3D/Pandamonium"
CHANNEL_BRANCH = "marketplace"
CATALOG_PATH = "marketplace/catalog.json"
CATALOG_URL = (
    f"https://raw.githubusercontent.com/{REPOSITORY}/{CHANNEL_BRANCH}/{CATALOG_PATH}"
)
BUNDLED = Path(__file__).resolve().parent.parent / "marketplace"
MAX_CATALOG_BYTES = 900_000
_LOCK = threading.Lock()
_ATTEMPTS: dict[str, float] = {}
_STATUS: dict[str, dict] = {}


def channel_status(root: Path) -> dict:
    return dict(_STATUS.get(str(root), {}))


def fetch_catalog() -> dict:
    """The channel URL is fixed; remote responses can never replace trust keys."""
    try:
        with (
            httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client,
            client.stream("GET", CATALOG_URL, params={"refresh": str(time.time_ns())}) as response,
        ):
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_CATALOG_BYTES:
                    raise MarketplaceCatalogError("marketplace_catalog_too_large")
        return json.loads(content)
    except (httpx.HTTPError, ValueError) as exc:
        raise MarketplaceCatalogError("marketplace_refresh_unavailable") from exc


def load_channel(root: Path, *, refresh: bool = False) -> tuple[dict, dict]:
    """Keep explicit local catalogs compatible; default installations bootstrap."""
    with _LOCK:
        keys = json.loads((BUNDLED / "trusted_keys.json").read_text())
        local_keys = root / "trusted_keys.json"
        if local_keys.exists():
            try:
                local_trust = json.loads(local_keys.read_text())
                if local_trust != keys:
                    # Explicit operator-managed trust stores retain their own channel.
                    catalog = json.loads((root / "catalog.json").read_text())
                    validate_published_catalog(catalog, trusted_keys=local_trust)
                    _STATUS[str(root)] = {"state": "operator_managed"}
                    return catalog, local_trust
            except (OSError, ValueError, TypeError) as exc:
                raise MarketplaceCatalogError(
                    "marketplace_configuration_invalid"
                ) from exc
        cache = root / "catalog.json"
        candidates = [cache, BUNDLED / "catalog.json"]
        catalog = None
        for path in candidates:
            try:
                candidate = json.loads(path.read_text())
                validate_published_catalog(candidate, trusted_keys=keys)
                catalog = candidate
                _STATUS.setdefault(
                    str(root), {"state": "cached" if path == cache else "bundled"}
                )
                break
            except (OSError, ValueError, TypeError):
                continue
        now = time.monotonic()
        if refresh or now - _ATTEMPTS.get(str(root), -900) >= 900:
            _ATTEMPTS[str(root)] = now
            try:
                fresh = fetch_catalog()
                validate_published_catalog(fresh, trusted_keys=keys)
                # Rollback republishes old entries with a new signed timestamp.
                if catalog and datetime.fromisoformat(
                    fresh["generated_at"].replace("Z", "+00:00")
                ) < datetime.fromisoformat(
                    catalog["generated_at"].replace("Z", "+00:00")
                ):
                    raise MarketplaceCatalogError("marketplace_catalog_stale")
                atomic_write_json(str(cache), fresh)
                catalog = fresh
                _STATUS[str(root)] = {"state": "refreshed"}
            except (OSError, MarketplaceCatalogError):
                _STATUS[str(root)] = {
                    "state": "last_known_good",
                    "message": "Refresh unavailable; using the last verified catalog until its expiry.",
                }
                if catalog is None:
                    raise MarketplaceCatalogError(
                        "marketplace_catalog_offline"
                    ) from None
        if catalog is None:
            raise MarketplaceCatalogError("marketplace_catalog_offline")
        return catalog, keys
