import json
import subprocess
from pathlib import Path

import pytest

import src.extension_installer as installer
from routes.extension_routes import setup_extension_routes
from src.authority_protocol import AuthorityStore
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    GitSourceClient,
    InlineWebAdapter,
    normalize_git_source_url,
    validate_git_ref,
)
from src.extension_registry import ExtensionRegistry


SOURCE_URL = "https://github.com/example/jos-extension-fixture.git"


def _run(argv, *, cwd=None):
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


def _manifest(version: str, tool: str, *, runtime: str = "web", commands=None, revision="self"):
    return {
        "protocol_version": "jos-extension.v1",
        "extension_id": "fixture",
        "name": "Fixture Extension",
        "version": version,
        "source": {"url": SOURCE_URL, "revision": revision},
        "runtime": {"type": runtime, "entrypoint": "index.html"},
        "capabilities": {
            "descriptor": {"type": "inline"},
            "schemas": [{
                "name": tool,
                "description": f"Run {tool}",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            }],
        },
        "permissions": {"default": "read_only", "capabilities": {}},
        "health": {"type": "catalog", "timeout_seconds": 3},
        "lifecycle": commands or {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "removal": {"remove_paths": [], "preserve_paths": []},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 3},
    }


def _commit(repo: Path, manifest: dict, tag: str) -> str:
    (repo / "jarvis-extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "index.html").write_text(f"<h1>{manifest['version']}</h1>", encoding="utf-8")
    _run(["git", "add", "jarvis-extension.json", "index.html"], cwd=repo)
    _run(["git", "commit", "-m", f"fixture {manifest['version']}"], cwd=repo)
    revision = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    _run(["git", "tag", tag], cwd=repo)
    return revision


@pytest.fixture
def git_fixture(tmp_path):
    repo = tmp_path / "source-repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "fixture@example.test"], cwd=repo)
    _run(["git", "config", "user.name", "Fixture"], cwd=repo)
    v1 = _commit(repo, _manifest("1.0.0", "inspect_fixture"), "v1")
    v2 = _commit(repo, _manifest("2.0.0", "update_fixture"), "v2")
    return repo, v1, v2


def _mapped_git(repo: Path) -> GitSourceClient:
    def runner(argv, cwd, timeout, environment):
        mapped = [str(repo) if item == SOURCE_URL else item for item in argv]
        return subprocess.run(
            mapped,
            cwd=str(cwd) if cwd else None,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )

    return GitSourceClient(runner=runner, check_public_urls=False)


def _manager(tmp_path: Path, repo: Path, *, adapters=None):
    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed",
        registry=registry,
        authority=authority,
        git_client=_mapped_git(repo),
        adapters=adapters,
    )
    return manager, authority, registry


def _approve_and_execute(manager, authority, plan, *, operator="operator"):
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(decision["decision_id"], operator_id=operator, choice="approve", scope="once")
    return manager.execute_plan(plan["plan_id"], operator_id=operator)


def _lifecycle(manager, authority, operation, *, target=None):
    plan = manager.preview_lifecycle(
        operation, "fixture", operator_id="operator", target_revision=target
    )
    return _approve_and_execute(manager, authority, plan)


def test_supported_git_urls_and_refs_are_strict():
    assert normalize_git_source_url(
        "https://github.com/example/repo", check_public=False
    ) == "https://github.com/example/repo.git"
    assert validate_git_ref("release/v1.2.3") == "release/v1.2.3"
    for value in (
        "git@github.com:example/repo.git",
        "http://github.com/example/repo.git",
        "https://localhost/example/repo.git",
        "https://github.com/example/repo.git?token=secret",
        "https://github.com/example/%2e%2e/repo.git",
        "https://github.com/example\\repo.git",
    ):
        with pytest.raises(ExtensionLifecycleError):
            normalize_git_source_url(value, check_public=False)
    for ref in ("-main", "../main", "main..old", "main@{1}"):
        with pytest.raises(ExtensionLifecycleError, match="extension_git_ref_invalid"):
            validate_git_ref(ref)


def test_install_preview_pins_revision_and_requires_explicit_approval(tmp_path, git_fixture):
    repo, v1, _v2 = git_fixture
    manager, authority, registry = _manager(tmp_path, repo)

    source, requested, resolved = manager.git.resolve_revision(SOURCE_URL, v1)
    assert (source, requested, resolved) == (SOURCE_URL, v1, v1)

    plan = manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator")

    assert plan["source_revision"] == v1
    assert plan["manifest"]["source"]["revision"] == "self"
    assert plan["requested_permissions"] == {"default": "read_only", "capabilities": {}}
    assert plan["lifecycle_commands"] == {"install": [], "start": [], "stop": [], "remove": []}
    with pytest.raises(ExtensionLifecycleError, match="extension_approval_required"):
        manager.execute_plan(plan["plan_id"], operator_id="operator")
    with pytest.raises(ExtensionLifecycleError, match="extension_plan_owner_mismatch"):
        manager.execute_plan(plan["plan_id"], operator_id="another-operator")
    assert registry.effective_capabilities() == {}

    result = _approve_and_execute(manager, authority, plan)

    assert result["result"]["status"] == "succeeded"
    assert manager.snapshot()["extensions"]["fixture"]["active_revision"] == v1
    assert registry.effective_capabilities()["inspect_fixture"]["extension_id"] == "fixture"
    install_path = manager.root / "installed" / "fixture" / "revisions" / v1
    assert install_path.is_dir()
    assert not install_path.is_relative_to(Path(installer.get_app_root()).resolve())
    assert manager.execute_plan(plan["plan_id"], operator_id="operator") == result


def test_upgrade_failure_keeps_previous_revision_and_catalog(tmp_path, git_fixture):
    repo, v1, v2 = git_fixture

    class FailV2(InlineWebAdapter):
        def validate(self, install_path, manifest, source_revision):
            if manifest["version"] == "2.0.0":
                raise ExtensionLifecycleError("fixture_health_failed")
            return super().validate(install_path, manifest, source_revision)

    manager, authority, registry = _manager(tmp_path, repo, adapters=[FailV2()])
    _approve_and_execute(
        manager, authority,
        manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator"),
    )
    upgrade = manager.preview_source("upgrade", SOURCE_URL, "v2", operator_id="operator")
    authority.resolve(
        upgrade["authority_decision"]["decision_id"],
        operator_id="operator",
        choice="approve",
        scope="once",
    )

    with pytest.raises(ExtensionLifecycleError, match="fixture_health_failed"):
        manager.execute_plan(upgrade["plan_id"], operator_id="operator")

    state = manager.snapshot()["extensions"]["fixture"]
    assert state["active_revision"] == v1
    assert state["enabled"] is True
    assert set(registry.effective_capabilities()) == {"inspect_fixture"}
    assert (manager.root / "installed" / "fixture" / "revisions" / v2).is_dir()


def test_failed_first_install_exposes_no_catalog(tmp_path, git_fixture):
    repo, _v1, _v2 = git_fixture

    class Unhealthy(InlineWebAdapter):
        def validate(self, install_path, manifest, source_revision):
            return None, False

    manager, authority, registry = _manager(tmp_path, repo, adapters=[Unhealthy()])
    plan = manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator")
    authority.resolve(
        plan["authority_decision"]["decision_id"],
        operator_id="operator",
        choice="approve",
        scope="once",
    )

    with pytest.raises(ExtensionLifecycleError, match="extension_health_unavailable"):
        manager.execute_plan(plan["plan_id"], operator_id="operator")

    assert manager.snapshot()["extensions"] == {}
    assert registry.snapshot()["extensions"] == {}


def test_enable_disable_upgrade_rollback_and_uninstall_are_reversible(tmp_path, git_fixture, monkeypatch):
    repo, v1, v2 = git_fixture
    manager, authority, registry = _manager(tmp_path, repo)
    events = []
    monkeypatch.setattr(
        installer,
        "record_operational_event",
        lambda **values: events.append(values) or {"event_id": str(len(events))},
    )
    _approve_and_execute(
        manager, authority,
        manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator"),
    )

    disabled = _lifecycle(manager, authority, "disable")
    assert disabled["result"]["structured"]["enabled"] is False
    assert registry.effective_capabilities() == {}
    disabled_again = _lifecycle(manager, authority, "disable")
    assert disabled_again["result"]["structured"]["idempotent"] is True

    _lifecycle(manager, authority, "enable")
    assert set(registry.effective_capabilities()) == {"inspect_fixture"}

    _approve_and_execute(
        manager, authority,
        manager.preview_source("upgrade", SOURCE_URL, "v2", operator_id="operator"),
    )
    assert manager.snapshot()["extensions"]["fixture"]["active_revision"] == v2
    assert set(registry.effective_capabilities()) == {"update_fixture"}

    _lifecycle(manager, authority, "rollback", target=v1)
    assert manager.snapshot()["extensions"]["fixture"]["active_revision"] == v1
    assert set(registry.effective_capabilities()) == {"inspect_fixture"}
    rollback_again = _lifecycle(manager, authority, "rollback", target=v1)
    assert rollback_again["result"]["structured"]["idempotent"] is True

    uninstalled = _lifecycle(manager, authority, "uninstall")
    assert uninstalled["result"]["structured"]["recoverable"] is True
    assert manager.snapshot()["extensions"] == {}
    assert registry.snapshot()["extensions"] == {}
    assert list((manager.root / "removed" / "fixture").iterdir())
    uninstalled_again = _lifecycle(manager, authority, "uninstall")
    assert uninstalled_again["result"]["structured"]["idempotent"] is True

    approvals = [event for event in events if event.get("event_type") == "approval"]
    results = [event for event in events if event.get("event_type") == "result"]
    assert approvals
    assert any(event.get("status") == "succeeded" for event in results)
    assert all(event.get("request_id") for event in approvals + results)


def test_unsupported_runtime_requires_adapter_and_never_creates_plan(tmp_path, git_fixture):
    repo, _v1, _v2 = git_fixture
    commands = {"install": ["npm", "ci"], "start": ["npm", "start"], "stop": [], "remove": []}
    _commit(repo, _manifest("3.0.0", "service_tool", runtime="service", commands=commands), "v3")
    manager, _authority, registry = _manager(tmp_path, repo)

    with pytest.raises(ExtensionLifecycleError, match="extension_adapter_required:service:inline"):
        manager.preview_source("install", SOURCE_URL, "v3", operator_id="operator")

    assert manager.snapshot()["plans"] == {}
    assert registry.snapshot()["extensions"] == {}
    assert list((manager.root / "staging").iterdir()) == []


def test_manifest_source_and_revision_mismatch_fail_before_approval(tmp_path, git_fixture):
    repo, _v1, _v2 = git_fixture
    wrong_source = _manifest("3.0.0", "wrong_source")
    wrong_source["source"]["url"] = "https://github.com/example/another-repo.git"
    _commit(repo, wrong_source, "wrong-source")
    wrong_revision = _manifest("4.0.0", "wrong_revision", revision="0" * 40)
    _commit(repo, wrong_revision, "wrong-revision")
    manager, _authority, _registry = _manager(tmp_path, repo)

    with pytest.raises(ExtensionLifecycleError, match="extension_manifest_source_mismatch"):
        manager.preview_source("install", SOURCE_URL, "wrong-source", operator_id="operator")
    with pytest.raises(ExtensionLifecycleError, match="extension_manifest_revision_mismatch"):
        manager.preview_source("install", SOURCE_URL, "wrong-revision", operator_id="operator")
    assert manager.snapshot()["plans"] == {}


def test_extension_routes_expose_preview_execute_and_readback(tmp_path, git_fixture):
    repo, _v1, _v2 = git_fixture
    manager, _authority, _registry = _manager(tmp_path, repo)
    paths = {route.path for route in setup_extension_routes(manager).routes}

    assert paths == {
        "/api/extensions",
        "/api/extensions/plans/source",
        "/api/extensions/plans/lifecycle",
        "/api/extensions/plans/{plan_id}/execute",
    }
