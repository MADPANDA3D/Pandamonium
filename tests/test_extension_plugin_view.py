"""MAD-940: installed/configured plugin list and detail projections."""

import json
from pathlib import Path

from src.extension_plugin_view import installed_plugin_detail, installed_plugin_rows
from src.extension_registry import MANIFEST_VERSION, ExtensionRegistry


FIXTURES = Path(__file__).parent / "fixtures" / "extensions"


def _manifest(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.manifest.json").read_text(encoding="utf-8"))


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"Run {name}",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
    }


def _registry_with_oracle(tmp_path: Path, *, disabled: bool = True) -> ExtensionRegistry:
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    oracle = _manifest("oracle")
    oracle["configuration"] = [
        {
            "key": "ORACLE_API_TOKEN",
            "description": "Owner-supplied token",
            "required": True,
            "secret": True,
        }
    ]
    revision = oracle["source"]["revision"]
    registry.register(
        oracle,
        {
            "protocol_version": MANIFEST_VERSION,
            "extension_id": "oracle",
            "version": oracle["version"],
            "source_revision": revision,
            "tools": [_tool("inspect_globe")],
        },
        source_revision=revision,
        health_available=True,
    )
    if disabled:
        registry.disable("oracle")
    return registry


CONFIGURED = [{"id": "oracle", "name": "ORACLE", "runtime": "web"}]


def test_rows_include_registry_and_configured_and_registry_wins(tmp_path):
    registry = _registry_with_oracle(tmp_path)
    rows = installed_plugin_rows(
        registry,
        configured_surfaces=[*CONFIGURED, {"id": "browser-tools", "name": "Browser Tools", "runtime": "web"}],
    )
    by_id = {row["id"]: row for row in rows}

    assert by_id["oracle"]["origin"] == "registry"
    assert by_id["oracle"]["state"] == "disabled"
    assert by_id["oracle"]["capability_count"] == 1
    assert by_id["browser-tools"]["origin"] == "configured"
    assert by_id["browser-tools"]["state"] == "configured"
    assert [row["id"] for row in rows] == sorted(by_id, key=lambda key: (by_id[key]["name"].lower(), key))


def test_enabled_web_surface_reports_available_not_needs_setup(tmp_path):
    registry = _registry_with_oracle(tmp_path, disabled=False)

    rows = installed_plugin_rows(registry)
    oracle = next(row for row in rows if row["id"] == "oracle")

    assert oracle["state"] == "enabled"
    assert oracle["readiness"]["state"] == "available"
    assert "surface" in oracle["readiness"]["message"].lower()


def test_registry_detail_exposes_capabilities_with_descriptions_and_no_secrets(tmp_path):
    registry = _registry_with_oracle(tmp_path)
    detail = installed_plugin_detail(registry, "oracle", configured_surfaces=CONFIGURED)

    assert detail["id"] == "oracle"
    assert detail["state"] == "disabled"
    assert detail["origin"] == "registry"
    assert detail["source_revision"]
    assert detail["capabilities"] == [
        {
            "name": "inspect_globe",
            "kind": "tool",
            "permission_mode": "read_only",
            "descriptor": "live_catalog",
            "description": "Run inspect_globe",
        }
    ]
    assert detail["permissions"]["default"] == "bounded_write"
    assert detail["permissions"]["capabilities"] == {"inspect_globe": "read_only"}
    assert detail["configuration"] == [
        {
            "key": "ORACLE_API_TOKEN",
            "description": "Owner-supplied token",
            "required": True,
            "secret": True,
        }
    ]
    assert detail["notes"] == [
        "Browser-surface extension: its tools become available when the surface is engaged."
    ]
    dumped = json.dumps(detail)
    assert "source" not in detail
    assert "https://github.com/" not in dumped
    assert "/home/" not in dumped


def test_configured_detail_is_honest_and_unknown_returns_none(tmp_path):
    registry = _registry_with_oracle(tmp_path)
    detail = installed_plugin_detail(registry, "browser-tools", configured_surfaces=[
        {"id": "browser-tools", "name": "Browser Tools", "runtime": "web", "url": "http://10.0.0.5:9999"},
    ])

    assert detail["origin"] == "configured"
    assert detail["state"] == "configured"
    assert detail["capabilities"] == []
    assert detail["configuration"] == []
    assert detail["notes"] and "Configured surface" in detail["notes"][0]
    dumped = json.dumps(detail)
    assert "10.0.0.5" not in dumped
    assert "url" not in detail

    assert installed_plugin_detail(registry, "missing", configured_surfaces=CONFIGURED) is None
