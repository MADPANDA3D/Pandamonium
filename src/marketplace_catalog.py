"""Signed marketplace catalog validation over the existing extension contract."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.extension_installer import (
    ExtensionLifecycleError,
    normalize_git_source_url,
    validate_git_ref,
)
from src.extension_registry import (
    IMMUTABLE_REVISION_PATTERN,
    ExtensionContractError,
    validate_extension_manifest,
)
from src.url_security import validate_public_http_url

CATALOG_VERSION = "pandamonium.extension-catalog.v1"
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
KEY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
ID_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"
CONFIG_KEY_PATTERN = r"^[A-Z][A-Z0-9_]{0,127}$"
CATEGORY_PATTERN = r"^[a-z][a-z0-9-]{0,39}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_REDIRECTS = 3


class MarketplaceCatalogError(ValueError):
    """Stable fail-closed catalog, trust, compatibility, or artifact error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _https(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("HTTPS URL required")
    return value


def _semver(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(SEMVER_PATTERN, value)
    if not match:
        raise MarketplaceCatalogError("marketplace_version_invalid")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketplaceCatalogError("marketplace_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise MarketplaceCatalogError("marketplace_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


class Signature(_StrictModel):
    algorithm: Literal["ed25519"]
    key_id: str = Field(pattern=KEY_ID_PATTERN, max_length=128)
    value: str = Field(min_length=1, max_length=200)


class Publisher(_StrictModel):
    id: str = Field(pattern=ID_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2_048)
    key_id: str = Field(pattern=KEY_ID_PATTERN, max_length=128)

    _validate_url = field_validator("url")(_https)


class Artifact(_StrictModel):
    url: str = Field(min_length=1, max_length=2_048)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=1, le=MAX_ARTIFACT_BYTES)
    signature: Signature

    _validate_url = field_validator("url")(_https)


class Compatibility(_StrictModel):
    pandamonium_min: str = Field(pattern=SEMVER_PATTERN, max_length=40)
    pandamonium_max: str = Field(pattern=SEMVER_PATTERN, max_length=40)
    platforms: list[Literal["linux", "macos", "windows"]] = Field(
        min_length=1, max_length=3
    )
    architectures: list[Literal["amd64", "arm64"]] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def validate_range_and_sets(self) -> Compatibility:
        if _semver(self.pandamonium_min) > _semver(self.pandamonium_max):
            raise ValueError("invalid compatibility range")
        if len(self.platforms) != len(set(self.platforms)) or len(
            self.architectures
        ) != len(set(self.architectures)):
            raise ValueError("duplicate compatibility value")
        return self


class Dependency(_StrictModel):
    dependency_type: Literal["plugin", "optional_package"]
    id: str = Field(pattern=ID_PATTERN, max_length=64)
    minimum_version: str = Field(pattern=SEMVER_PATTERN, max_length=40)
    maximum_version: str = Field(pattern=SEMVER_PATTERN, max_length=40)
    optional: bool

    @model_validator(mode="after")
    def validate_range(self) -> Dependency:
        if _semver(self.minimum_version) > _semver(self.maximum_version):
            raise ValueError("invalid dependency range")
        return self


class Configuration(_StrictModel):
    key: str = Field(pattern=CONFIG_KEY_PATTERN, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    required: bool
    secret: bool


class Advisory(_StrictModel):
    id: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2_048)
    severity: Literal["low", "medium", "high", "critical"]
    summary: str = Field(min_length=1, max_length=500)

    _validate_url = field_validator("url")(_https)


class Review(_StrictModel):
    status: Literal["active", "deprecated", "revoked"]
    reviewed_at: str = Field(min_length=1, max_length=64)
    reviewer: str = Field(min_length=1, max_length=200)
    security_advisories: list[Advisory] = Field(max_length=64)

    @field_validator("reviewed_at")
    @classmethod
    def validate_reviewed_at(cls, value: str) -> str:
        _timestamp(value)
        return value


class CatalogEntry(_StrictModel):
    package_type: Literal["plugin"]
    manifest: dict[str, Any]
    summary: str = Field(min_length=1, max_length=500)
    categories: list[str] = Field(min_length=1, max_length=16)
    license: str = Field(min_length=1, max_length=100)
    publisher: Publisher
    artifact: Artifact
    compatibility: Compatibility
    dependencies: list[Dependency] = Field(max_length=64)
    configuration: list[Configuration] = Field(max_length=64)
    restart_required: Literal["none", "plugin", "pandamonium"]
    review: Review

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            not re.fullmatch(CATEGORY_PATTERN, value) for value in values
        ):
            raise ValueError("invalid category")
        return values


class PublishedCatalog(_StrictModel):
    schema_version: Literal["pandamonium.extension-catalog.v1"]
    catalog_id: str = Field(pattern=ID_PATTERN, max_length=64)
    generated_at: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    entries: list[CatalogEntry] = Field(max_length=2_048)
    signature: Signature


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _verify_signature(
    signature: Signature,
    payload: bytes,
    trusted_keys: Mapping[str, str | bytes],
    *,
    prefix: str,
) -> None:
    if signature.key_id not in trusted_keys:
        raise MarketplaceCatalogError("marketplace_signature_untrusted")
    try:
        public_bytes = trusted_keys[signature.key_id]
        raw_key = (
            public_bytes
            if isinstance(public_bytes, bytes)
            else base64.b64decode(public_bytes, validate=True)
        )
        raw_signature = base64.b64decode(signature.value, validate=True)
        if len(raw_key) != 32 or len(raw_signature) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(raw_key).verify(raw_signature, payload)
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise MarketplaceCatalogError(f"{prefix}_signature_invalid") from exc


def _normalize_entry(
    entry: CatalogEntry, trusted_keys: Mapping[str, str | bytes]
) -> dict[str, Any]:
    try:
        manifest = validate_extension_manifest(entry.manifest)
    except ExtensionContractError as exc:
        raise MarketplaceCatalogError(f"marketplace_manifest_{exc.code}") from exc
    revision = manifest["source"]["revision"]
    _semver(manifest["version"])
    if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
        raise MarketplaceCatalogError(
            "marketplace_manifest_extension_source_revision_invalid"
        )
    try:
        manifest["source"]["url"] = normalize_git_source_url(
            manifest["source"]["url"], check_public=False
        )
        validate_git_ref(revision)
    except ExtensionLifecycleError as exc:
        raise MarketplaceCatalogError("marketplace_manifest_source_invalid") from exc
    if entry.artifact.signature.key_id != entry.publisher.key_id:
        raise MarketplaceCatalogError("marketplace_artifact_publisher_mismatch")
    _verify_signature(
        entry.artifact.signature,
        f"sha256:{entry.artifact.sha256}".encode("ascii"),
        trusted_keys,
        prefix="marketplace_artifact",
    )
    dependency_keys = [(item.dependency_type, item.id) for item in entry.dependencies]
    if len(dependency_keys) != len(set(dependency_keys)) or any(
        item.dependency_type == "plugin" and item.id == manifest["extension_id"]
        for item in entry.dependencies
    ):
        raise MarketplaceCatalogError("marketplace_dependency_id_invalid")
    configuration_keys = [item.key for item in entry.configuration]
    if len(configuration_keys) != len(set(configuration_keys)):
        raise MarketplaceCatalogError("marketplace_configuration_key_invalid")
    normalized = entry.model_dump(mode="json")
    normalized["manifest"] = manifest
    return normalized


def validate_published_catalog(
    catalog: Any,
    *,
    trusted_keys: Mapping[str, str | bytes],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify trust and return a normalized catalog without network or writes."""
    if not isinstance(catalog, Mapping):
        raise MarketplaceCatalogError("marketplace_catalog_invalid")
    if "signature" not in catalog:
        raise MarketplaceCatalogError("marketplace_catalog_unsigned")
    try:
        document = PublishedCatalog.model_validate(catalog)
    except ValidationError as exc:
        raise MarketplaceCatalogError("marketplace_catalog_invalid") from exc
    unsigned = copy.deepcopy(dict(catalog))
    unsigned.pop("signature")
    _verify_signature(
        document.signature,
        _canonical(unsigned),
        trusted_keys,
        prefix="marketplace_catalog",
    )
    generated = _timestamp(document.generated_at)
    expires = _timestamp(document.expires_at)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= generated or expires <= current:
        raise MarketplaceCatalogError("marketplace_catalog_expired")
    entries = [_normalize_entry(entry, trusted_keys) for entry in document.entries]
    identities = [
        (entry["manifest"]["extension_id"], entry["manifest"]["version"])
        for entry in entries
    ]
    if len(identities) != len(set(identities)):
        raise MarketplaceCatalogError("marketplace_entry_duplicate")
    normalized = document.model_dump(mode="json")
    normalized["entries"] = entries
    return normalized


def preview_catalog_install(
    catalog: Any,
    extension_id: str,
    version: str,
    *,
    trusted_keys: Mapping[str, str | bytes],
    pandamonium_version: str,
    platform: str,
    architecture: str,
    online: bool,
    operation: str = "install",
) -> dict[str, Any]:
    """Build input for the existing source preview; never fetch or execute."""
    if operation not in {"install", "upgrade"}:
        raise MarketplaceCatalogError("marketplace_operation_invalid")
    normalized = validate_published_catalog(catalog, trusted_keys=trusted_keys)
    if not online:
        raise MarketplaceCatalogError("marketplace_catalog_offline")
    matches = [
        entry
        for entry in normalized["entries"]
        if entry["manifest"]["extension_id"] == extension_id
        and entry["manifest"]["version"] == version
    ]
    if len(matches) != 1:
        raise MarketplaceCatalogError("marketplace_package_not_found")
    entry = matches[0]
    compatibility = entry["compatibility"]
    current = _semver(pandamonium_version)
    if not (
        _semver(compatibility["pandamonium_min"])
        <= current
        <= _semver(compatibility["pandamonium_max"])
        and platform in compatibility["platforms"]
        and architecture in compatibility["architectures"]
    ):
        raise MarketplaceCatalogError("marketplace_package_incompatible")
    if entry["review"]["status"] == "revoked":
        raise MarketplaceCatalogError("marketplace_package_revoked")
    manifest = entry["manifest"]
    return {
        "catalog_id": normalized["catalog_id"],
        "operation": operation,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_url": manifest["source"]["url"],
        "requested_ref": manifest["source"]["revision"],
        "manifest": manifest,
        "artifact": entry["artifact"],
        "summary": entry["summary"],
        "categories": entry["categories"],
        "license": entry["license"],
        "publisher": entry["publisher"],
        "compatibility": compatibility,
        "requested_permissions": manifest["permissions"],
        "dependencies": entry["dependencies"],
        "configuration": entry["configuration"],
        "restart_required": entry["restart_required"],
        "health": manifest["health"],
        "rollback": manifest["rollback"],
        "removal": manifest["removal"],
        "review": entry["review"],
    }


def catalog_dependency_status(
    dependencies: list[Mapping[str, Any]], registry_snapshot: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Fail closed on required dependencies without installing host packages."""
    installed = registry_snapshot.get("extensions", {})
    if not isinstance(installed, Mapping):
        installed = {}
    result = []
    for dependency in dependencies:
        item = dict(dependency)
        if item["dependency_type"] == "optional_package":
            if not item["optional"]:
                raise MarketplaceCatalogError("marketplace_host_dependency_required")
            item["state"] = "declared_not_installed"
            result.append(item)
            continue
        record = installed.get(item["id"])
        manifest = record.get("manifest") if isinstance(record, Mapping) else None
        version = (
            str(manifest.get("version") or "") if isinstance(manifest, Mapping) else ""
        )
        try:
            version_compatible = bool(
                version
                and _semver(item["minimum_version"])
                <= _semver(version)
                <= _semver(item["maximum_version"])
            )
        except MarketplaceCatalogError:
            version_compatible = False
        satisfied = bool(
            isinstance(record, Mapping) and record.get("enabled") and version_compatible
        )
        if not satisfied and not item["optional"]:
            raise MarketplaceCatalogError("marketplace_plugin_dependency_unavailable")
        item["state"] = "satisfied" if satisfied else "optional_unavailable"
        result.append(item)
    return result


def marketplace_catalog_view(
    catalog: Any,
    *,
    trusted_keys: Mapping[str, str | bytes],
    registry_snapshot: Mapping[str, Any],
    pandamonium_version: str,
    platform: str,
    architecture: str,
    online: bool = True,
    lifecycle_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a verified catalog and existing registry into a read-only UI view."""
    if not online:
        return {
            "schema_version": "pandamonium.marketplace-view.v1",
            "status": "offline",
            "failure": "marketplace_catalog_offline",
            "plugins": [],
        }

    normalized = validate_published_catalog(catalog, trusted_keys=trusted_keys)
    installed = registry_snapshot.get("extensions", {})
    if not isinstance(installed, Mapping):
        installed = {}
    lifecycle_extensions = (lifecycle_snapshot or {}).get("extensions", {})
    if not isinstance(lifecycle_extensions, Mapping):
        lifecycle_extensions = {}

    latest: dict[str, dict[str, Any]] = {}
    for entry in normalized["entries"]:
        extension_id = entry["manifest"]["extension_id"]
        current = latest.get(extension_id)
        if current is None or _semver(entry["manifest"]["version"]) > _semver(
            current["manifest"]["version"]
        ):
            latest[extension_id] = entry

    plugins = []
    runtime_version = _semver(pandamonium_version)
    for entry in latest.values():
        manifest = entry["manifest"]
        extension_id = manifest["extension_id"]
        target_version = manifest["version"]
        compatibility = entry["compatibility"]
        compatible = (
            _semver(compatibility["pandamonium_min"])
            <= runtime_version
            <= _semver(compatibility["pandamonium_max"])
            and platform in compatibility["platforms"]
            and architecture in compatibility["architectures"]
        )
        review_status = entry["review"]["status"]
        availability = (
            "revoked"
            if review_status == "revoked"
            else "deprecated"
            if review_status == "deprecated"
            else "available"
            if compatible
            else "incompatible"
        )

        local = installed.get(extension_id)
        if not isinstance(local, Mapping):
            local = {}
        local_manifest = local.get("manifest")
        current_version = (
            str(local_manifest.get("version"))
            if isinstance(local_manifest, Mapping) and local_manifest.get("version")
            else None
        )
        try:
            update_available = bool(
                current_version and _semver(target_version) > _semver(current_version)
            )
        except MarketplaceCatalogError:
            update_available = False
        enabled = bool(local.get("enabled")) if current_version else False
        local_lifecycle = lifecycle_extensions.get(extension_id)
        history = (
            list(local_lifecycle.get("history") or [])
            if isinstance(local_lifecycle, Mapping)
            else []
        )
        installation_state = (
            "disabled"
            if current_version and not enabled
            else "update_available"
            if update_available
            else "installed"
            if current_version
            else "available"
        )

        plugins.append(
            {
                "id": extension_id,
                "name": manifest["name"],
                "version": target_version,
                "summary": entry["summary"],
                "categories": entry["categories"],
                "license": entry["license"],
                "availability": availability,
                "publisher": entry["publisher"],
                "provenance": {
                    "source_url": manifest["source"]["url"],
                    "source_revision": manifest["source"]["revision"],
                    "sha256": entry["artifact"]["sha256"],
                    "digest_state": "verified",
                    "signature_state": "verified",
                },
                "compatibility": {
                    **compatibility,
                    "state": "compatible" if compatible else "incompatible",
                },
                "permissions": {
                    **manifest["permissions"],
                    "data_boundaries": manifest["data_boundaries"],
                },
                "dependencies": entry["dependencies"],
                "configuration": entry["configuration"],
                "restart_required": entry["restart_required"],
                "removal": manifest["removal"],
                "rollback": {
                    **manifest["rollback"],
                    "available_revisions": history,
                },
                "review": entry["review"],
                "installation": {
                    "state": installation_state,
                    "current_version": current_version,
                    "target_version": target_version,
                    "enabled": enabled,
                    "update_available": update_available,
                },
            }
        )

    plugins.sort(key=lambda item: (item["name"].lower(), item["id"]))
    return {
        "schema_version": "pandamonium.marketplace-view.v1",
        "status": "ready" if plugins else "empty",
        "failure": None,
        "catalog": {
            "id": normalized["catalog_id"],
            "generated_at": normalized["generated_at"],
            "expires_at": normalized["expires_at"],
            "signature_state": "verified",
        },
        "runtime": {
            "pandamonium_version": pandamonium_version,
            "platform": platform,
            "architecture": architecture,
        },
        "plugins": plugins,
    }


def verify_catalog_artifact(artifact: Mapping[str, Any], content: bytes) -> bool:
    """Verify downloaded bytes against signed catalog size and SHA-256 metadata."""
    if len(content) != artifact.get("size_bytes"):
        raise MarketplaceCatalogError("marketplace_artifact_size_mismatch")
    observed = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(observed, str(artifact.get("sha256") or "")):
        raise MarketplaceCatalogError("marketplace_artifact_digest_mismatch")
    return True


def download_catalog_artifact(
    artifact: Mapping[str, Any],
    *,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> bytes:
    """Download one bounded artifact while revalidating every redirect target."""
    expected = artifact.get("size_bytes")
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or not 1 <= expected <= MAX_ARTIFACT_BYTES
    ):
        raise MarketplaceCatalogError("marketplace_artifact_size_invalid")
    url = str(artifact.get("url") or "")
    try:
        with client_factory(
            follow_redirects=False, trust_env=False, timeout=30
        ) as client:
            for redirect_count in range(MAX_ARTIFACT_REDIRECTS + 1):
                url = validate_public_http_url(url)
                if urlparse(url).scheme != "https":
                    raise MarketplaceCatalogError(
                        "marketplace_artifact_redirect_invalid"
                    )
                with client.stream(
                    "GET", url, headers={"Accept": "application/octet-stream"}
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location or redirect_count == MAX_ARTIFACT_REDIRECTS:
                            raise MarketplaceCatalogError(
                                "marketplace_artifact_redirect_invalid"
                            )
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise MarketplaceCatalogError(
                            "marketplace_artifact_unavailable"
                        )
                    declared = response.headers.get("content-length")
                    if declared is not None and (
                        not declared.isdigit() or int(declared) != expected
                    ):
                        raise MarketplaceCatalogError(
                            "marketplace_artifact_size_mismatch"
                        )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > expected:
                            raise MarketplaceCatalogError(
                                "marketplace_artifact_size_mismatch"
                            )
                    return bytes(content)
    except MarketplaceCatalogError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise MarketplaceCatalogError("marketplace_artifact_unavailable") from exc
    raise MarketplaceCatalogError("marketplace_artifact_redirect_invalid")
