"""Real isolated service/API lifecycle, encrypted configuration and resource gates."""

import asyncio
import copy
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, Integration
from src import extension_configuration as configuration
from src import extension_resources as resources
from src.authority_protocol import AuthorityStore
from src.extension_cli_adapter import GeneratedCliAdapter
from src.extension_installer import ExtensionLifecycleManager
from src.extension_package import package_tree_digest
from src.extension_registry import ExtensionRegistry
from tests.test_extension_cli_adapter import package, preview_package
from tests.test_extension_installer import _approve_and_execute


def test_service_configuration_operation_restart_failure_and_remove(
    tmp_path, monkeypatch
):
    engine = create_engine("sqlite:///" + str(tmp_path / "setup.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(configuration, "SessionLocal", factory)
    path, manifest = package(tmp_path)
    manifest["configuration"] = [
        {
            "key": "API_TOKEN",
            "description": "API credential",
            "required": True,
            "secret": True,
        }
    ]
    manifest["data_boundaries"]["network"] = ["https://example.test"]
    (path / ".pandamonium/server.py").write_text("""import json
from http.server import BaseHTTPRequestHandler, HTTPServer
config=json.load(open('/run/pandamonium/config.json'))
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.headers.get('Authorization') != config['API_TOKEN']:
   self.send_response(401);self.end_headers();return
  self.send_response(200);self.end_headers();self.wfile.write(b'{"result":8}')
 def log_message(self,*args): pass
HTTPServer(('127.0.0.1',int(config['PANDAMONIUM_PORT'])),Handler).serve_forever()
""")
    (path / ".pandamonium/adapter.py").write_text("""import json,urllib.request
config=json.load(open('/run/pandamonium/config.json'))
request=urllib.request.Request('http://127.0.0.1:'+config['PANDAMONIUM_PORT'],headers={'Authorization':config['API_TOKEN']})
with urllib.request.urlopen(request,timeout=3) as response: print(response.read().decode())
""")
    data = json.loads((path / ".pandamonium/integration.json").read_text())
    data["manifest"] = manifest
    data["execution"]["service"] = ["python", "/package/.pandamonium/server.py"]
    (path / ".pandamonium/integration.json").write_text(json.dumps(data))
    (path / "jarvis-extension.json").write_text(json.dumps(manifest))
    root = tmp_path / "manager"
    adapter = GeneratedCliAdapter(root)
    registry = ExtensionRegistry(tmp_path / "registry.json")
    authority = AuthorityStore(tmp_path / "authority.json")
    manager = ExtensionLifecycleManager(
        root=root, registry=registry, authority=authority, adapters=[adapter]
    )
    with pytest.raises(Exception, match="needs_setup:API_TOKEN"):
        _approve_and_execute(
            manager, authority, preview_package(manager, path, manifest)
        )
    token = "enc:literal-owner-credential"
    result = configuration.save(manifest, "operator", {"API_TOKEN": token})
    assert token not in json.dumps(result)
    with factory() as db:
        assert token not in json.dumps(db.query(Integration).first().config)
    assert configuration.values(manifest, "operator") == {"API_TOKEN": token}
    with pytest.raises(Exception, match="needs_setup"):
        configuration.values(manifest, "another-owner")
    _approve_and_execute(manager, authority, preview_package(manager, path, manifest))
    record = registry.snapshot()["extensions"]["demo-tools"]
    name = "demo_tools__double_count"
    assert GeneratedCliAdapter(root).execute(
        record, name, {"count": 4}, "operator", threading.Event()
    ) == {"result": 8}
    state = next((root / "runtime-state").glob("*.json"))
    unit = json.loads(state.read_text())["unit"]
    resources.verify_scope(unit)
    assert adapter.readiness(record, "operator")["state"] == "ready"
    # A failed new revision leaves the original running; a successful replacement
    # stops it, and rollback restores that exact package at the same source commit.
    original = manager._read_state()["extensions"]["demo-tools"]["active_revision"]
    metadata = path / ".pandamonium/integration.json"
    revised = copy.deepcopy(data)
    revised["execution"]["checks"][0]["expected"] = {"result": 99}
    metadata.write_text(json.dumps(revised))
    with pytest.raises(Exception, match="operation_check_failed"):
        _approve_and_execute(
            manager, authority, preview_package(manager, path, manifest, "upgrade")
        )
    resources.verify_scope(unit)
    assert adapter.execute(
        record, name, {"count": 4}, "operator", threading.Event()
    ) == {"result": 8}
    assert (
        len(
            list(
                adapter._runtime(manifest, "placeholder", "operator").parent.glob(
                    "*/validated.json"
                )
            )
        )
        == 1
    )
    revised["execution"]["checks"][0]["expected"] = {"result": 8}
    revised["source_exclusions"] = ["unused-fixture.txt"]
    metadata.write_text(json.dumps(revised))
    _approve_and_execute(
        manager, authority, preview_package(manager, path, manifest, "upgrade")
    )
    with pytest.raises(Exception, match="resource limits"):
        resources.verify_scope(unit)
    rollback = manager.preview_lifecycle(
        "rollback", "demo-tools", operator_id="operator", target_revision=original
    )
    _approve_and_execute(manager, authority, rollback)
    restored = registry.snapshot()["extensions"]["demo-tools"]
    assert adapter.execute(
        restored, name, {"count": 4}, "operator", threading.Event()
    ) == {"result": 8}
    for operation in ("disable", "enable", "uninstall"):
        _approve_and_execute(
            manager,
            authority,
            manager.preview_lifecycle(operation, "demo-tools", operator_id="operator"),
        )
        if operation != "enable":
            assert not list((root / "runtime-state").glob("*.json"))
    assert registry.snapshot()["extensions"] == {}
    engine.dispose()


def test_resource_admission_fails_before_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_EXTENSION_RUNTIME_ROOT", str(tmp_path))
    with pytest.raises(Exception, match="enforced filesystem"):
        resources.storage_root()
    monkeypatch.delenv("ODYSSEUS_EXTENSION_RUNTIME_ROOT")
    with pytest.raises(Exception, match="dedicated filesystem"):
        resources.admit()


def test_setup_reruns_after_configuration_changes_and_failure(tmp_path, monkeypatch):
    path, manifest = package(tmp_path)
    config = {"BASE_URL": "first"}
    monkeypatch.setattr(configuration, "values", lambda *_: config)
    (path / ".pandamonium/setup.py").write_text("""import json
from pathlib import Path
value=json.load(open('/run/pandamonium/config.json'))['BASE_URL']
target=Path('/runtime/configured.txt')
target.write_text(value)
if value == 'broken': raise RuntimeError('setup failed')
count=Path('/runtime/setup-count.txt')
count.write_text(str(int(count.read_text() if count.exists() else '0')+1))
""")
    metadata = path / ".pandamonium/integration.json"
    data = json.loads(metadata.read_text())
    data["execution"]["install"] = [["python", "/package/.pandamonium/setup.py"]]
    metadata.write_text(json.dumps(data))
    adapter = GeneratedCliAdapter(tmp_path / "manager")
    runtime = adapter._runtime(manifest, package_tree_digest(path), "operator")

    def validate():
        adapter.validate_for_owner(
            path, manifest, manifest["source"]["revision"], "operator"
        )

    validate()
    validate()
    assert (runtime / "setup-count.txt").read_text() == "1"
    config["BASE_URL"] = "second"
    validate()
    assert (runtime / "configured.txt").read_text() == "second"
    assert (runtime / "setup-count.txt").read_text() == "2"
    config["BASE_URL"] = "broken"
    with pytest.raises(Exception, match="setup failed"):
        validate()
    assert not (runtime / "validated.json").exists()
    config["BASE_URL"] = "second"
    validate()
    assert (runtime / "configured.txt").read_text() == "second"
    assert (runtime / "setup-count.txt").read_text() == "3"


@pytest.mark.parametrize(
    "host,route",
    [
        ("github.com", "/blob/"),
        ("gitlab.com", "/-/blob/"),
        ("codeberg.org", "/src/commit/"),
    ],
)
def test_knowledge_citations_match_source_host_and_encode_path(tmp_path, host, route):
    from src import extension_knowledge as knowledge

    path, manifest = package(tmp_path)
    manifest["source"]["url"] = f"https://{host}/owner/repo.git"
    relative = "docs/a b#?.md"
    (path / "docs").mkdir()
    (path / relative).write_text("Searchable needle\n")
    runtime = tmp_path / "knowledge"
    spec = {"files": [relative], "vectorize": False, "graph_artifact": None}
    knowledge.index(path, runtime, manifest, spec)
    result = knowledge.execute(
        "knowledge.search", {"query": "needle"}, path, runtime, manifest, spec
    )
    assert result["results"][0]["source_url"] == (
        f"https://{host}/owner/repo{route}{manifest['source']['revision']}/docs/a%20b%23%3F.md"
    )


def test_interactive_prerequisite_is_needs_setup(tmp_path):
    path, manifest = package(tmp_path)
    metadata = path / ".pandamonium/integration.json"
    data = json.loads(metadata.read_text())
    data["execution"]["prerequisites"] = ["display", "audio"]
    metadata.write_text(json.dumps(data))
    with pytest.raises(Exception, match="external runtime providing display, audio"):
        GeneratedCliAdapter(tmp_path / "manager").validate_for_owner(
            path, manifest, manifest["source"]["revision"], "operator"
        )


def test_knowledge_index_source_refresh_vector_model_and_confinement(
    tmp_path, monkeypatch
):
    import numpy as np

    from src import extension_knowledge as knowledge

    path, manifest = package(tmp_path)
    runtime = tmp_path / "knowledge"
    runtime.mkdir()
    spec = {"files": ["README.md"], "vectorize": False, "graph_artifact": None}
    receipt = knowledge.index(path, runtime, manifest, spec)
    assert receipt["chunks"] > 0 and not receipt["vectorized"]
    assert not (runtime / "knowledge.sqlite").exists(), (
        "index stays outside the sandbox write mount"
    )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(Exception, match="knowledge_cancelled"):
        knowledge.execute(
            "knowledge.refresh", {}, path, runtime, manifest, spec, cancelled
        )
    result = knowledge.execute(
        "knowledge.search", {"query": "count"}, path, runtime, manifest, spec
    )
    assert result["results"][0]["source_revision"] == manifest["source"]["revision"]
    assert result["results"][0]["source_url"].endswith("/README.md")
    assert not list(path.glob(".installed*")), "collection text must never execute"
    (path / "README.md").write_text("Replacement needle\n")
    knowledge.execute("knowledge.refresh", {}, path, runtime, manifest, spec)
    assert (
        knowledge.execute(
            "knowledge.search", {"query": "count"}, path, runtime, manifest, spec
        )["results"]
        == []
    )

    class Embeddings:
        url, model = "fixture://deterministic", "test-model"

        def encode(self, texts, normalize_embeddings=True):
            return np.asarray([[1.0, 0.0] for _ in texts])

    client = Embeddings()
    monkeypatch.setattr(knowledge, "_client", lambda: client)
    spec["vectorize"] = True
    assert knowledge.index(path, runtime, manifest, spec)["vectorized"] is True
    assert knowledge.execute(
        "knowledge.search", {"query": "needle"}, path, runtime, manifest, spec
    )["results"]
    client.model = "changed-model"
    with pytest.raises(Exception, match="Embedding model changed"):
        knowledge.execute(
            "knowledge.search", {"query": "needle"}, path, runtime, manifest, spec
        )
    spec["files"] = ["../outside.txt"]
    with pytest.raises(Exception, match="relative source path"):
        knowledge.index(path, runtime, manifest, spec)


def test_setup_routes_bind_owner_and_invalidate_old_approval(tmp_path, monkeypatch):
    from routes.extension_routes import ConfigurationRequest, setup_extension_routes

    engine = create_engine("sqlite:///" + str(tmp_path / "setup.db"))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(configuration, "SessionLocal", sessionmaker(bind=engine))
    path, manifest = package(tmp_path)
    manifest["configuration"] = [
        {
            "key": "BASE_URL",
            "description": "Service address",
            "required": True,
            "secret": False,
        }
    ]
    (path / "jarvis-extension.json").write_text(json.dumps(manifest))
    root = tmp_path / "manager"
    manager = ExtensionLifecycleManager(
        root=root,
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"),
        adapters=[GeneratedCliAdapter(root)],
    )
    plan = preview_package(manager, path, manifest)
    routes = setup_extension_routes(manager).routes
    save = next(
        route.endpoint
        for route in routes
        if route.path.endswith("/configuration") and "PUT" in route.methods
    )
    payload = ConfigurationRequest(
        values={"BASE_URL": "https://example.test"}, plan_id=plan["plan_id"]
    )
    with pytest.raises(Exception, match="extension_plan_not_found"):
        asyncio.run(save("demo-tools", payload, owner="another-owner"))
    result = asyncio.run(save("demo-tools", payload, owner="operator"))
    assert result["state"] == "needs_validation"
    with pytest.raises(Exception, match="preview_again"):
        manager.execute_plan(plan["plan_id"], operator_id="operator")
    refreshed = preview_package(manager, path, manifest)
    assert refreshed["authority_decision"]["decision"] == "approval_required"
    # A connection edit outside the setup route cannot reuse a stale preview.
    configuration.save(
        manifest, "operator", {"BASE_URL": "https://changed.example.test"}
    )
    with pytest.raises(Exception, match="preview_again"):
        manager.execute_plan(refreshed["plan_id"], operator_id="operator")
    asyncio.run(save("demo-tools", payload, owner="operator"))
    refreshed = preview_package(manager, path, manifest)
    _approve_and_execute(manager, manager.authority, refreshed)
    engine.dispose()


def test_selected_voice_node_reuses_encrypted_endpoint_and_restores_on_failed_registration(
    tmp_path, monkeypatch
):
    from core.database import ModelEndpoint
    from src import settings

    engine = create_engine("sqlite:///" + str(tmp_path / "setup.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(configuration, "SessionLocal", factory)
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.save_settings({"tts_provider": "disabled"})
    with factory() as db:
        db.add(
            ModelEndpoint(
                id="speech-node",
                name="Speech node",
                owner="operator",
                base_url="https://speech.example.test/v1",
                api_key="owner-credential",
                model_type="tts",
                is_enabled=True,
            )
        )
        db.commit()
    path, manifest = package(tmp_path)
    manifest["configuration"] = [
        {
            "key": "ENDPOINT_ID",
            "description": "Choose the speech node",
            "required": True,
            "secret": False,
        }
    ]
    configuration.save(manifest, "operator", {"ENDPOINT_ID": "speech-node"})
    resolved = configuration.values(manifest, "operator")
    assert resolved["PANDAMONIUM_ENDPOINT_TOKEN"] == "owner-credential"
    assert "owner-credential" not in json.dumps(
        configuration.public(manifest, "operator")
    )
    configuration.save(manifest, "another-owner", {"ENDPOINT_ID": "speech-node"})
    with pytest.raises(Exception, match="owned by this account"):
        configuration.values(manifest, "another-owner")
    metadata = path / ".pandamonium/integration.json"
    data = json.loads(metadata.read_text())
    data["execution"]["voice_model"] = "source-backed-model"
    metadata.write_text(json.dumps(data))
    adapter = GeneratedCliAdapter(tmp_path / "manager")
    adapter.validate_for_owner(
        path, manifest, manifest["source"]["revision"], "operator"
    )
    adapter.activate_for_owner(
        path, manifest, None, manifest["source"]["revision"], owner_scope="operator"
    )
    assert settings.load_settings()["tts_provider"] == "endpoint:speech-node"
    adapter.rollback_activation(manifest)
    assert settings.load_settings()["tts_provider"] == "disabled"
    engine.dispose()


def test_kernel_task_limit_contains_many_children(tmp_path):
    from src.extension_cli_adapter import _run

    path, manifest = package(tmp_path)
    runtime = resources.storage_root() / tmp_path.name / "runtime"
    runtime.mkdir(parents=True)
    (path / ".pandamonium/limits.py").write_text("""import subprocess,json
children=[]
try:
 for _ in range(140): children.append(subprocess.Popen(['/usr/bin/sleep','5']))
except OSError:
 print(json.dumps({'limited':True}))
finally:
 for child in children: child.kill()
 for child in children: child.wait()
""")
    assert json.loads(
        _run(path, runtime, manifest, ["python", "/package/.pandamonium/limits.py"])
    ) == {"limited": True}
    (path / ".pandamonium/leak.py").write_text(
        "import json\nprint(json.dumps(json.load(open('/run/pandamonium/config.json'))))\n"
    )
    manifest["configuration"] = [{"key": "TOKEN", "secret": True}]
    with pytest.raises(Exception, match="output_contains_secret"):
        _run(
            path,
            runtime,
            manifest,
            ["python", "/package/.pandamonium/leak.py"],
            config={"TOKEN": 'test-credential"with-newline\n'},
        )
