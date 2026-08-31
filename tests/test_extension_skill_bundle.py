import json
import os
import subprocess
from pathlib import Path

import pytest

from services.memory.skills import SkillsManager
from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleError, ExtensionLifecycleManager, GitSourceClient
from src.extension_registry import ExtensionRegistry
from src.extension_skill_adapter import SkillBundleAdapter


SOURCE_URL = "https://github.com/example/native-skill-fixture.git"


def _run(argv, *, cwd=None):
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


def _skill(name: str, description: str, *, toolsets=(), body="Follow the reviewed procedure.") -> str:
    toolset_line = f"requires_toolsets: [{', '.join(toolsets)}]\n" if toolsets else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 1.0.0\n"
        f"{toolset_line}"
        "---\n\n"
        f"## Procedure\n\n1. {body}\n"
    )


def _manifest(version: str, *, entrypoint: str, bundle_format: str, include: list[str]) -> dict:
    return {
        "protocol_version": "jos-extension.v1",
        "extension_id": "skill-fixture",
        "name": "Native Skill Fixture",
        "version": version,
        "source": {"url": SOURCE_URL, "revision": "self"},
        "runtime": {"type": "skills", "entrypoint": entrypoint},
        "capabilities": {
            "descriptor": {
                "type": "skill_bundle",
                "format": bundle_format,
                "include": include,
            }
        },
        "permissions": {
            "default": "read_only",
            "capabilities": {name: "bounded_write" for name in include if name == "beta-skill"},
        },
        "health": {"type": "catalog", "timeout_seconds": 3},
        "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {
            "read": ["project/input"],
            "write": ["project/output"],
            "network": [],
        },
        "removal": {"remove_paths": [], "preserve_paths": ["project/output"]},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 3},
    }


def _commit(repo: Path, manifest: dict, tag: str) -> str:
    (repo / "jarvis-extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-m", f"fixture {tag}"], cwd=repo)
    revision = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    _run(["git", "tag", tag], cwd=repo)
    return revision


@pytest.fixture
def skill_repo(tmp_path):
    repo = tmp_path / "skill-source"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "fixture@example.test"], cwd=repo)
    _run(["git", "config", "user.name", "Fixture"], cwd=repo)

    (repo / "SKILL.md").write_text(
        _skill("alpha-skill", "Run the alpha workflow", toolsets=("files",)),
        encoding="utf-8",
    )
    (repo / "references").mkdir()
    (repo / "references" / "guide.md").write_text("alpha reference", encoding="utf-8")
    v1 = _commit(
        repo,
        _manifest(
            "1.0.0", entrypoint="SKILL.md", bundle_format="agent_skill", include=["alpha-skill"]
        ),
        "v1",
    )

    plugin_dir = repo / ".codex-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "fixture", "skills": "skills"}), encoding="utf-8"
    )
    for name, description in (
        ("alpha-skill", "Run the upgraded alpha workflow"),
        ("beta-skill", "Run the beta workflow"),
        ("unselected-skill", "This remains outside the reviewed set"),
    ):
        skill_dir = repo / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_skill(name, description), encoding="utf-8")
    (repo / "skills" / "beta-skill" / "scripts").mkdir()
    (repo / "skills" / "beta-skill" / "scripts" / "run.py").write_text(
        "print('fixture')\n", encoding="utf-8"
    )
    v2 = _commit(
        repo,
        _manifest(
            "2.0.0",
            entrypoint=".codex-plugin/plugin.json",
            bundle_format="codex_plugin",
            include=["alpha-skill", "beta-skill"],
        ),
        "v2",
    )
    return repo, v1, v2


def _mapped_git(repo: Path) -> GitSourceClient:
    def runner(argv, cwd, timeout, environment):
        return subprocess.run(
            [str(repo) if item == SOURCE_URL else item for item in argv],
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


def _manager(tmp_path: Path, repo: Path):
    skills = SkillsManager(str(tmp_path / "data"))
    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed",
        registry=registry,
        authority=authority,
        git_client=_mapped_git(repo),
        adapters=[SkillBundleAdapter(skills)],
    )
    return manager, authority, registry, skills


def _execute(manager, authority, plan, *, operator="operator"):
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(
        decision["decision_id"], operator_id=operator, choice="approve", scope="once"
    )
    return manager.execute_plan(plan["plan_id"], operator_id=operator)


def _lifecycle(manager, authority, operation, *, target=None, operator="operator"):
    return _execute(
        manager,
        authority,
        manager.preview_lifecycle(
            operation,
            "skill-fixture",
            operator_id=operator,
            target_revision=target,
        ),
        operator=operator,
    )


def test_direct_and_partial_plugin_bundles_use_native_skill_lifecycle(tmp_path, skill_repo):
    repo, v1, v2 = skill_repo
    manager, authority, registry, skills = _manager(tmp_path, repo)

    preview = manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator")
    assert preview["admitted_skills"] == [{
        "id": "alpha-skill",
        "source_path": ".",
        "owner_scope": "operator",
        "platforms": [],
        "requires_toolsets": ["files"],
    }]
    _execute(manager, authority, preview)

    alpha = skills.load("operator")[0]
    assert alpha["name"] == "alpha-skill"
    assert alpha["status"] == "published"
    assert alpha["source"] == f"extension:skill-fixture@{v1}"
    assert skills.index_for("operator", active_toolsets=[]) == []
    assert [row["name"] for row in skills.index_for("operator", active_toolsets=["files"])] == [
        "alpha-skill"
    ]
    assert skills.read_skill_reference("alpha-skill", "references/guide.md", "operator") == "alpha reference"
    record = registry.snapshot()["extensions"]["skill-fixture"]
    assert record["effective_capabilities"] == []
    assert record["admitted_skills"][0]["permission_mode"] == "read_only"
    assert "Follow the reviewed procedure" not in json.dumps(record)
    assert registry.context_extensions({"skill-fixture"})["skill-fixture"] == {
        "engaged": True,
        "state_mounted": True,
        "tool_count": 0,
        "skill_count": 1,
    }

    _lifecycle(manager, authority, "disable")
    assert skills.load("operator") == []
    assert registry.context_extensions({"skill-fixture"}) == {}
    _lifecycle(manager, authority, "enable")
    assert [row["name"] for row in skills.load("operator")] == ["alpha-skill"]

    upgrade = manager.preview_source("upgrade", SOURCE_URL, "v2", operator_id="operator")
    assert [row["id"] for row in upgrade["admitted_skills"]] == ["alpha-skill", "beta-skill"]
    _execute(manager, authority, upgrade)
    assert {row["name"] for row in skills.load("operator")} == {"alpha-skill", "beta-skill"}
    assert "unselected-skill" not in {row["name"] for row in skills.load("operator")}
    assert skills.read_skill_reference("beta-skill", "scripts/run.py", "operator") == "print('fixture')\n"
    beta = next(row for row in registry.snapshot()["extensions"]["skill-fixture"]["admitted_skills"] if row["id"] == "beta-skill")
    assert beta["permission_mode"] == "bounded_write"

    _lifecycle(manager, authority, "rollback", target=v1)
    assert [row["name"] for row in skills.load("operator")] == ["alpha-skill"]
    assert manager.snapshot()["extensions"]["skill-fixture"]["active_revision"] == v1
    _lifecycle(manager, authority, "uninstall")
    assert skills.load("operator") == []
    assert registry.snapshot()["extensions"] == {}
    assert list((manager.root / "removed" / "skill-fixture").iterdir())

    _execute(
        manager,
        authority,
        manager.preview_source("install", SOURCE_URL, "v2", operator_id="operator"),
    )
    assert {row["name"] for row in skills.load("operator")} == {"alpha-skill", "beta-skill"}


def test_collision_and_owner_scope_fail_before_mutation(tmp_path, skill_repo):
    repo, _v1, _v2 = skill_repo
    manager, _authority, registry, skills = _manager(tmp_path, repo)
    skills.add_skill(
        name="alpha-skill",
        description="Existing native skill",
        procedure=["Keep it"],
        status="published",
        source="user",
        owner="operator",
    )

    with pytest.raises(ExtensionLifecycleError, match="extension_skill_name_collision"):
        manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator")

    assert skills.load("operator")[0]["source"] == "user"
    assert registry.snapshot()["extensions"] == {}
    assert manager.snapshot()["plans"] == {}


def test_malformed_or_unsafe_skill_metadata_never_reaches_approval(tmp_path, skill_repo):
    repo, _v1, _v2 = skill_repo
    malformed = repo / "skills" / "alpha-skill" / "SKILL.md"
    malformed.write_text(
        _skill("alpha-skill", "Unsafe authority metadata").replace(
            "version: 1.0.0\n", "version: 1.0.0\nowner: another-user\n"
        ),
        encoding="utf-8",
    )
    _commit(
        repo,
        _manifest(
            "3.0.0",
            entrypoint=".codex-plugin/plugin.json",
            bundle_format="codex_plugin",
            include=["alpha-skill"],
        ),
        "malformed",
    )
    manager, _authority, registry, skills = _manager(tmp_path, repo)

    with pytest.raises(ExtensionLifecycleError, match="extension_skill_frontmatter_unsupported"):
        manager.preview_source("install", SOURCE_URL, "malformed", operator_id="operator")

    assert skills.load("operator") == []
    assert registry.snapshot()["extensions"] == {}
    assert manager.snapshot()["plans"] == {}


def test_registry_failure_restores_previous_native_bundle(tmp_path, skill_repo, monkeypatch):
    repo, _v1, _v2 = skill_repo
    manager, authority, registry, skills = _manager(tmp_path, repo)
    _execute(
        manager,
        authority,
        manager.preview_source("install", SOURCE_URL, "v1", operator_id="operator"),
    )
    preview = manager.preview_source("upgrade", SOURCE_URL, "v2", operator_id="operator")
    decision = preview["authority_decision"]
    authority.resolve(
        decision["decision_id"], operator_id="operator", choice="approve", scope="once"
    )
    original_register = registry.register

    def fail_register(*args, **kwargs):
        raise OSError("fixture registry failure")

    monkeypatch.setattr(registry, "register", fail_register)
    with pytest.raises(OSError, match="fixture registry failure"):
        manager.execute_plan(preview["plan_id"], operator_id="operator")
    monkeypatch.setattr(registry, "register", original_register)

    current = skills.load("operator")
    assert [row["name"] for row in current] == ["alpha-skill"]
    assert current[0]["description"] == "Run the alpha workflow"
    assert registry.snapshot()["extensions"]["skill-fixture"]["catalog_version"] == "1.0.0"


def test_symlinked_selected_asset_fails_closed(tmp_path, skill_repo):
    repo, _v1, _v2 = skill_repo
    outside = repo / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = repo / "skills" / "beta-skill" / "references.txt"
    os.symlink("../../outside.txt", link)
    _commit(
        repo,
        _manifest(
            "4.0.0",
            entrypoint=".codex-plugin/plugin.json",
            bundle_format="codex_plugin",
            include=["beta-skill"],
        ),
        "symlink",
    )
    manager, _authority, registry, skills = _manager(tmp_path, repo)

    with pytest.raises(ExtensionLifecycleError, match="extension_skill_asset_unsafe"):
        manager.preview_source("install", SOURCE_URL, "symlink", operator_id="operator")

    assert skills.load("operator") == []
    assert registry.snapshot()["extensions"] == {}
