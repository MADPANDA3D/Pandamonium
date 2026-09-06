from __future__ import annotations

import json

from src import selector_catalog


def _models():
    return {
        "items": [
            {
                "endpoint_id": "endpoint-owner",
                "endpoint_name": "Configured runtime",
                "url": "http://private-tailnet.invalid/v1/chat/completions",
                "model_type": "llm",
                "models": ["vendor/alpha"],
                "models_display": ["Alpha"],
                "models_extra": [],
                "models_extra_display": [],
            }
        ]
    }


def _workers():
    return {
        "pc-codex": {
            "label": "Friday",
            "configured": True,
            "ready": True,
            "installation_capabilities": ["codex"],
            "workspaces": ["test-project"],
            "connection": {"state": "connected", "url": "http://private.invalid"},
        },
        "hermes": {
            "label": "Gordon",
            "configured": True,
            "ready": False,
            "installation_capabilities": ["hermes"],
            "workspaces": ["test-project"],
            "connection": {"state": "unreachable", "reason": "connection_failed"},
        },
        "desktop-claude": {
            "label": "Scribe",
            "configured": True,
            "ready": True,
            "installation_capabilities": ["claude"],
            "workspaces": ["test-project"],
            "connection": {"state": "connected"},
        },
        "vps-codex": {
            "label": "Private installation label",
            "configured": False,
            "ready": False,
            "workspaces": [],
            "connection": {"state": "gated"},
        },
    }


def test_selector_catalog_preserves_taxonomy_and_same_health(monkeypatch):
    monkeypatch.setattr(
        selector_catalog,
        "agent_identity_status",
        lambda: {"agent_id": "jarvis", "display_name": "Jarvis", "status": "healthy"},
    )

    result = selector_catalog.build_selector_catalog(_models(), _workers())
    discovery = result["discovery"]
    entities = {entity["display_name"]: entity for entity in discovery["entities"]}
    selections = {item["entity_id"]: item for item in result["selections"]}

    assert discovery["schema_version"] == "pandamonium.discovery.v1"
    assert entities["Alpha"]["kind"] == "model"
    assert entities["Jarvis"]["kind"] == "agent"
    assert entities["Friday"]["kind"] == "worker"
    assert entities["Scribe"]["kind"] == "agent"
    assert "Gordon" not in entities
    assert selections[entities["Friday"]["id"]]["selectable"] is True
    assert selections[entities["Scribe"]["id"]] == {
        "entity_id": entities["Scribe"]["id"],
        "kind": "agent",
        "target": "desktop-claude",
        "capabilities": ["claude"],
        "selectable": True,
        "reason": None,
    }
    assert selections[entities["Friday"]["id"]]["capabilities"] == ["codex"]
    assert selections[entities["Alpha"]["id"]]["capabilities"] == ["model"]
    assert "Private installation label" not in entities


def test_selector_catalog_is_owner_safe_and_schema_shaped(monkeypatch):
    monkeypatch.setattr(
        selector_catalog,
        "agent_identity_status",
        lambda: {"agent_id": "jarvis", "display_name": "Jarvis", "status": "healthy"},
    )

    result = selector_catalog.build_selector_catalog(_models(), _workers())
    serialized = json.dumps(result)

    assert "private-tailnet" not in serialized
    assert "http://private.invalid" not in serialized
    assert "/home/" not in serialized
    required = {
        "kind", "id", "display_name", "availability", "ownership",
        "health", "permissions", "source", "actions",
    }
    for entity in result["discovery"]["entities"]:
        assert set(entity) == required
        assert entity["permissions"]["requires_authenticated_request"] is True
        assert entity["source"]["ref"] in {
            "src/agent_identity.py#agent_identity_status",
            "routes/model_routes.py#api_models",
            "src/jarvis_agent.py#worker_statuses",
        }


def test_selector_catalog_empty_install_still_exposes_configured_identity(monkeypatch):
    monkeypatch.setattr(
        selector_catalog,
        "agent_identity_status",
        lambda: {"agent_id": "assistant", "display_name": "Configured assistant", "status": "degraded"},
    )

    result = selector_catalog.build_selector_catalog({"items": []}, {})

    assert [entity["kind"] for entity in result["discovery"]["entities"]] == ["agent"]
    assert result["selections"][0]["target"] == "jarvis"
