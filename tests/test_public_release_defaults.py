import asyncio
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import src.settings as settings
from src.agent_identity import resolve_agent_identity
from src.agent_tools.admin_tools import do_manage_endpoints
from src.agent_worker_adapters import configured_worker_workspaces, worker_catalog
from src.extension_registry import ExtensionRegistry


def test_public_worker_defaults_are_empty_and_private_topology_is_opt_in(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_WORKER_WORKSPACES_JSON", raising=False)

    catalog = worker_catalog()

    assert all(details["workspaces"] == [] for details in catalog.values())
    assert "madpanda" not in json.dumps(catalog).casefold()
    assert "home-lab" not in json.dumps(catalog).casefold()


@pytest.mark.parametrize(
    "value",
    ["not-json", "[]", '{"unknown":["project"]}', '{"pc-codex":["../project"]}'],
)
def test_worker_workspace_configuration_fails_closed(monkeypatch, value):
    monkeypatch.setenv("ODYSSEUS_WORKER_WORKSPACES_JSON", value)

    with pytest.raises(RuntimeError, match="invalid_worker_workspace_configuration"):
        configured_worker_workspaces()


def test_clean_room_configures_identity_two_endpoints_and_no_extensions(tmp_path, monkeypatch, request):
    request.addfinalizer(settings._invalidate_caches)
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_path))
    settings._invalidate_caches()
    configured = {
        **settings.DEFAULT_SETTINGS,
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Be accurate, preserve operator authority, and require evidence for outcomes.",
        "agent_constitution_version": "1",
    }
    settings.save_settings(configured)

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    database.Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    for name, base_url in (
        ("Local Engine", "http://127.0.0.1:11434/v1"),
        ("Backup Engine", "http://127.0.0.1:1234/v1"),
    ):
        result = asyncio.run(do_manage_endpoints(json.dumps({
            "action": "add",
            "name": name,
            "base_url": base_url,
        })))
        assert result["exit_code"] == 0

    with session_factory() as session:
        endpoints = [
            {"name": row.name, "base_url": row.base_url, "api_key": row.api_key}
            for row in session.query(database.ModelEndpoint).order_by(database.ModelEndpoint.name)
        ]
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    state = {
        "identity": resolve_agent_identity(settings.load_settings()),
        "endpoints": endpoints,
        "extensions": registry.snapshot()["extensions"],
    }
    serialized = json.dumps(state).casefold()

    assert state["identity"]["agent_id"] == "atlas"
    assert len(endpoints) == 2
    assert all(endpoint["api_key"] in {None, ""} for endpoint in endpoints)
    assert state["extensions"] == {}
    for forbidden in ("leo", "madpanda", "home lab", "/home/", "oracle"):
        assert forbidden not in serialized


def test_voice_workspace_defaults_do_not_materialize_private_profile():
    env = os.environ.copy()
    env.pop("ODYSSEUS_WORKER_WORKSPACES_JSON", None)
    env["DATABASE_URL"] = "sqlite:///:memory:"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from routes.voice_routes import VOICE_WORKSPACES; print(json.dumps(sorted(VOICE_WORKSPACES)))",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout) == []


def test_worker_tool_schema_projects_only_installation_configured_workspaces():
    env = os.environ.copy()
    env.pop("ODYSSEUS_WORKER_WORKSPACES_JSON", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from src.agent_tools import FUNCTION_TOOL_SCHEMAS; "
                "schema=next(item for item in FUNCTION_TOOL_SCHEMAS "
                "if item['function']['name']=='start_agent_task'); "
                "print(json.dumps(schema['function']['parameters']['properties']['workspace']['enum']))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout) == []


def test_public_release_docs_keep_claim_states_and_security_risks_separate():
    root = os.path.dirname(os.path.dirname(__file__))
    matrix = open(os.path.join(root, "docs", "jos-extension-compatibility-matrix.md"), encoding="utf-8").read()
    guide = open(os.path.join(root, "docs", "jos-public-operations.md"), encoding="utf-8").read()
    release = open(os.path.join(root, "docs", "jos-public-release-v1.0.0.md"), encoding="utf-8").read()

    assert "| Component | Exact source-tested revision | Package-installed | Live-accepted |" in matrix
    for risk in (
        "Untrusted repositories",
        "Lifecycle commands",
        "Credentials",
        "Capability escalation",
        "Update drift",
        "Rollback failure",
    ):
        assert risk in guide
    for value in (
        "https://github.com/MADPANDA3D/odysseus/releases/tag/jarvis-os-v1.0.0",
        "ee470206b669a119b6740a71c98ae9cba8c23237",
        "jarvis-os-v1.0.0",
        "jos-v0.1.0",
        "jos-v1.5.1-jos.2",
        "jos-v0.4.28-jos.2",
        "jos-v2.8.0-jos.2",
        "SHA256SUMS",
        "release-manifest.json",
        "5554b189e69b64e3be2e5b6d60093b03261f15be1951ef6d27ae1a86ffbea370",
    ):
        assert value in release
    assert "codex/" not in release
