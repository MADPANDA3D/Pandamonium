import base64
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.extension_installer import normalize_git_source_url, validate_git_ref
from src.marketplace_catalog import (
    MarketplaceCatalogError,
    marketplace_catalog_view,
    preview_catalog_install,
    validate_published_catalog,
    verify_catalog_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "tests" / "fixtures" / "extensions" / "atlas.manifest.json"
ARTIFACT = b"fixture marketplace package\n"


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _sign(key: Ed25519PrivateKey, value: bytes) -> str:
    return _b64(key.sign(value))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()


def _fixture_catalog():
    catalog_key = Ed25519PrivateKey.generate()
    publisher_key = Ed25519PrivateKey.generate()
    manifest = json.loads(ATLAS.read_text(encoding="utf-8"))
    manifest["source"] = {
        "url": "https://github.com/example/atlas-lab.git",
        "revision": "1" * 40,
    }
    digest = hashlib.sha256(ARTIFACT).hexdigest()
    catalog = {
        "schema_version": "pandamonium.extension-catalog.v1",
        "catalog_id": "pandamonium-community",
        "generated_at": "2026-09-06T07:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "entries": [
            {
                "package_type": "plugin",
                "manifest": manifest,
                "summary": "Reference-neutral geometry workspace plugin.",
                "categories": ["design", "developer-tools"],
                "license": "Apache-2.0",
                "publisher": {
                    "id": "example-labs",
                    "name": "Example Labs",
                    "url": "https://example.com/atlas",
                    "key_id": "publisher-example-2026",
                },
                "artifact": {
                    "url": "https://github.com/example/atlas-lab/releases/download/v2.0.0/atlas-2.0.0.tar.gz",
                    "sha256": digest,
                    "size_bytes": len(ARTIFACT),
                    "signature": {
                        "algorithm": "ed25519",
                        "key_id": "publisher-example-2026",
                        "value": _sign(publisher_key, f"sha256:{digest}".encode()),
                    },
                },
                "compatibility": {
                    "pandamonium_min": "1.0.0",
                    "pandamonium_max": "1.9.99",
                    "platforms": ["linux", "macos", "windows"],
                    "architectures": ["amd64", "arm64"],
                },
                "dependencies": [
                    {
                        "dependency_type": "plugin",
                        "id": "base-tools",
                        "minimum_version": "1.2.0",
                        "maximum_version": "1.9.9",
                        "optional": True,
                    }
                ],
                "configuration": [
                    {
                        "key": "ATLAS_API_TOKEN",
                        "description": "Owner-supplied API token reference",
                        "required": False,
                        "secret": True,
                    }
                ],
                "restart_required": "none",
                "review": {
                    "status": "active",
                    "reviewed_at": "2026-09-06T07:00:00Z",
                    "reviewer": "pandamonium-security",
                    "security_advisories": [],
                },
            }
        ],
    }
    catalog["signature"] = {
        "algorithm": "ed25519",
        "key_id": "catalog-root-2026",
        "value": _sign(catalog_key, _canonical(catalog)),
    }
    trusted = {
        "catalog-root-2026": _b64(
            catalog_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ),
        "publisher-example-2026": _b64(
            publisher_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ),
    }
    return catalog, trusted, catalog_key


def _resign(catalog: dict, catalog_key: Ed25519PrivateKey) -> None:
    catalog.pop("signature", None)
    catalog["signature"] = {
        "algorithm": "ed25519",
        "key_id": "catalog-root-2026",
        "value": _sign(catalog_key, _canonical(catalog)),
    }


def test_signed_catalog_previews_only_existing_pinned_extension_contract():
    schema = json.loads(
        (
            ROOT / "specs" / "schemas" / "pandamonium-extension-catalog-v1.schema.json"
        ).read_text()
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert (
        schema["properties"]["entries"]["items"]["properties"]["package_type"]["const"]
        == "plugin"
    )

    catalog, trusted, _key = _fixture_catalog()
    normalized = validate_published_catalog(catalog, trusted_keys=trusted)
    plan = preview_catalog_install(
        normalized,
        "atlas",
        "2.0.0",
        trusted_keys=trusted,
        pandamonium_version="1.0.10",
        platform="linux",
        architecture="amd64",
        online=True,
    )

    assert plan["operation"] == "install"
    assert plan["source_url"] == normalize_git_source_url(
        "https://github.com/example/atlas-lab.git", check_public=False
    )
    assert plan["requested_ref"] == validate_git_ref("1" * 40)
    assert plan["requested_ref"] not in {"HEAD", "main", "master"}
    assert (
        plan["requested_permissions"]
        == catalog["entries"][0]["manifest"]["permissions"]
    )
    assert plan["categories"] == ["design", "developer-tools"]
    assert plan["restart_required"] == "none"
    assert plan["configuration"][0] == {
        "key": "ATLAS_API_TOKEN",
        "description": "Owner-supplied API token reference",
        "required": False,
        "secret": True,
    }
    assert verify_catalog_artifact(plan["artifact"], ARTIFACT) is True


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda catalog: catalog["entries"].append(
                copy.deepcopy(catalog["entries"][0])
            ),
            "marketplace_entry_duplicate",
        ),
        (
            lambda catalog: catalog["entries"][0].update(
                {"package_type": "connection"}
            ),
            "marketplace_catalog_invalid",
        ),
        (
            lambda catalog: catalog["entries"][0]["manifest"]["source"].update(
                {"revision": "main"}
            ),
            "marketplace_manifest_extension_source_revision_invalid",
        ),
        (
            lambda catalog: catalog["entries"][0]["configuration"][0].update(
                {"default": "secret"}
            ),
            "marketplace_catalog_invalid",
        ),
        (
            lambda catalog: catalog["entries"][0]["artifact"]["signature"].update(
                {"value": _b64(b"x" * 64)}
            ),
            "marketplace_artifact_signature_invalid",
        ),
    ],
)
def test_signed_catalog_rejects_contract_threats(mutation, error):
    catalog, trusted, catalog_key = _fixture_catalog()
    mutation(catalog)
    _resign(catalog, catalog_key)

    with pytest.raises(MarketplaceCatalogError, match=error):
        validate_published_catalog(catalog, trusted_keys=trusted)


def test_unsigned_untrusted_expired_and_bad_artifact_catalogs_fail_closed():
    catalog, trusted, catalog_key = _fixture_catalog()
    catalog.pop("signature")
    with pytest.raises(MarketplaceCatalogError, match="marketplace_catalog_unsigned"):
        validate_published_catalog(catalog, trusted_keys=trusted)

    catalog, trusted, _catalog_key = _fixture_catalog()
    with pytest.raises(
        MarketplaceCatalogError, match="marketplace_signature_untrusted"
    ):
        validate_published_catalog(catalog, trusted_keys={})

    catalog, trusted, catalog_key = _fixture_catalog()
    catalog["expires_at"] = "2026-09-06T06:59:59Z"
    _resign(catalog, catalog_key)
    with pytest.raises(MarketplaceCatalogError, match="marketplace_catalog_expired"):
        validate_published_catalog(
            catalog,
            trusted_keys=trusted,
            now=datetime(2026, 9, 6, 7, 0, tzinfo=timezone.utc),
        )

    catalog, trusted, _catalog_key = _fixture_catalog()
    entry = validate_published_catalog(catalog, trusted_keys=trusted)["entries"][0]
    with pytest.raises(
        MarketplaceCatalogError, match="marketplace_artifact_digest_mismatch"
    ):
        verify_catalog_artifact(entry["artifact"], b"x" * len(ARTIFACT))


def test_offline_incompatible_and_revoked_install_previews_fail_closed():
    catalog, trusted, catalog_key = _fixture_catalog()
    with pytest.raises(MarketplaceCatalogError, match="marketplace_catalog_offline"):
        preview_catalog_install(
            catalog,
            "atlas",
            "2.0.0",
            trusted_keys=trusted,
            pandamonium_version="1.0.10",
            platform="linux",
            architecture="amd64",
            online=False,
        )

    catalog["entries"][0]["compatibility"]["pandamonium_max"] = "1.0.9"
    _resign(catalog, catalog_key)
    with pytest.raises(
        MarketplaceCatalogError, match="marketplace_package_incompatible"
    ):
        preview_catalog_install(
            catalog,
            "atlas",
            "2.0.0",
            trusted_keys=trusted,
            pandamonium_version="1.0.10",
            platform="linux",
            architecture="amd64",
            online=True,
        )

    catalog, trusted, catalog_key = _fixture_catalog()
    catalog["entries"][0]["review"]["status"] = "revoked"
    _resign(catalog, catalog_key)
    with pytest.raises(MarketplaceCatalogError, match="marketplace_package_revoked"):
        preview_catalog_install(
            catalog,
            "atlas",
            "2.0.0",
            trusted_keys=trusted,
            pandamonium_version="1.0.10",
            platform="linux",
            architecture="amd64",
            online=True,
        )


def test_marketplace_view_projects_catalog_and_registry_without_mutation():
    catalog, trusted, _catalog_key = _fixture_catalog()
    registry = {
        "extensions": {
            "atlas": {
                "enabled": True,
                "manifest": {"version": "1.0.0"},
            }
        }
    }

    view = marketplace_catalog_view(
        catalog,
        trusted_keys=trusted,
        registry_snapshot=registry,
        pandamonium_version="1.0.10",
        platform="linux",
        architecture="amd64",
    )

    assert view["status"] == "ready"
    assert view["catalog"]["signature_state"] == "verified"
    assert view["runtime"] == {
        "pandamonium_version": "1.0.10",
        "platform": "linux",
        "architecture": "amd64",
    }
    assert len(view["plugins"]) == 1
    plugin = view["plugins"][0]
    assert plugin["id"] == "atlas"
    assert plugin["availability"] == "available"
    assert plugin["compatibility"]["state"] == "compatible"
    assert plugin["installation"] == {
        "state": "update_available",
        "current_version": "1.0.0",
        "target_version": "2.0.0",
        "enabled": True,
        "update_available": True,
    }
    assert plugin["provenance"]["digest_state"] == "verified"
    assert plugin["provenance"]["signature_state"] == "verified"
    assert plugin["permissions"]["default"] == "read_only"
    assert plugin["dependencies"][0]["id"] == "base-tools"
    assert "artifact" not in plugin
    assert "value" not in plugin["provenance"]


def test_marketplace_view_keeps_incompatible_revoked_disabled_empty_and_offline_states_clear():
    catalog, trusted, catalog_key = _fixture_catalog()
    catalog["entries"][0]["compatibility"]["pandamonium_max"] = "1.0.9"
    catalog["entries"][0]["review"]["status"] = "revoked"
    _resign(catalog, catalog_key)
    registry = {
        "extensions": {
            "atlas": {
                "enabled": False,
                "manifest": {"version": "2.0.0"},
            }
        }
    }

    view = marketplace_catalog_view(
        catalog,
        trusted_keys=trusted,
        registry_snapshot=registry,
        pandamonium_version="1.0.10",
        platform="linux",
        architecture="amd64",
    )
    plugin = view["plugins"][0]
    assert plugin["availability"] == "revoked"
    assert plugin["compatibility"]["state"] == "incompatible"
    assert plugin["installation"]["state"] == "disabled"

    empty, empty_trusted, _key = _fixture_catalog()
    empty["entries"] = []
    _resign(empty, _key)
    assert marketplace_catalog_view(
        empty,
        trusted_keys=empty_trusted,
        registry_snapshot={},
        pandamonium_version="1.0.10",
        platform="linux",
        architecture="amd64",
    )["status"] == "empty"
    assert marketplace_catalog_view(
        None,
        trusted_keys={},
        registry_snapshot={},
        pandamonium_version="1.0.10",
        platform="linux",
        architecture="amd64",
        online=False,
    ) == {
        "schema_version": "pandamonium.marketplace-view.v1",
        "status": "offline",
        "failure": "marketplace_catalog_offline",
        "plugins": [],
    }
