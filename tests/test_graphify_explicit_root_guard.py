import json
import sys
import types
from pathlib import Path

import pytest

from mcp_servers.graphify_server import list_tools
from src.graphify_runtime import (
    GraphifyConfigurationError,
    build_command,
    configured_roots,
    query_graph,
    sanitize_graph_output,
)


def _config(repository_root: Path, output_root: Path) -> str:
    return json.dumps({
        "odysseus": {
            "repository_root": str(repository_root),
            "output_root": str(output_root),
        }
    })


def test_graphify_requires_a_named_explicit_repository_root(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "graphs" / "odysseus"

    roots = configured_roots(_config(repository, output))

    assert set(roots) == {"odysseus"}
    assert roots["odysseus"].repository_root == repository.resolve()
    assert roots["odysseus"].graph_path == output.resolve() / "graphify-out" / "graph.json"


@pytest.mark.parametrize(
    "value",
    [
        {"odysseus": {"repository_root": ".", "output_root": "/tmp/graph"}},
        {"odysseus": {"repository_root": "/", "output_root": "/tmp/graph"}},
        {"odysseus": {"repository_root": "/opt", "output_root": "/tmp/graph"}},
        {"../repo": {"repository_root": "/opt/repo", "output_root": "/tmp/graph"}},
        {"odysseus": {"repository_root": "/opt/repo", "output_root": "relative"}},
        {"odysseus": {"repository_root": "/opt/repo", "output_root": "/var"}},
        {"odysseus": {"repository_root": "/opt/repo", "output_root": "/tmp/graph", "scan": True}},
    ],
)
def test_graphify_configuration_rejects_broad_relative_or_unknown_values(value):
    with pytest.raises(GraphifyConfigurationError):
        configured_roots(json.dumps(value), require_repository=False)


def test_graphify_build_is_code_only_and_accepts_no_caller_path(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "graphs" / "odysseus"
    root = configured_roots(_config(repository, output))["odysseus"]

    command = build_command(root, python="/opt/graphify/bin/python")

    assert command == [
        "/opt/graphify/bin/python",
        "-m",
        "graphify",
        "extract",
        str(repository.resolve()),
        "--out",
        str(output.resolve()),
        "--code-only",
        "--no-cluster",
        "--no-dedup",
        "--max-workers",
        "1",
    ]


def test_graphify_output_is_bounded_and_redacts_configured_paths(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "graphs" / "odysseus"
    root = configured_roots(_config(repository, output))["odysseus"]
    raw = f"{repository}/src/app.py\n{output}/graphify-out/graph.json\n" + ("x" * 100_000)

    cleaned = sanitize_graph_output(raw, root, maximum_bytes=512)

    assert str(repository) not in cleaned
    assert str(output) not in cleaned
    assert "[root:odysseus]" in cleaned
    assert len(cleaned.encode("utf-8")) <= 512


async def test_graphify_mcp_catalog_exposes_root_ids_but_no_path_parameter(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "graphs" / "odysseus"
    monkeypatch.setenv("ODYSSEUS_GRAPHIFY_ROOTS", _config(repository, output))

    tools = await list_tools()

    assert [tool.name for tool in tools] == ["graphify_status", "graphify_query"]
    for tool in tools:
        properties = tool.inputSchema["properties"]
        assert properties["root_id"]["enum"] == ["odysseus"]
        assert "path" not in properties
        assert "project_path" not in properties


def test_graphify_query_accepts_current_single_graph_loader_result(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "graphs" / "odysseus"
    graph_path = output / "graphify-out" / "graph.json"
    graph_path.parent.mkdir(parents=True)
    graph_path.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    roots = configured_roots(_config(repository, output))
    loaded_graph = object()
    calls = {}

    serve = types.ModuleType("graphify.serve")
    serve._load_graph = lambda path: loaded_graph

    def fake_query(graph, question, **kwargs):
        calls.update(graph=graph, question=question, **kwargs)
        return f"{repository}/src/qdrant_projection.py"

    serve._query_graph_text = fake_query
    package = types.ModuleType("graphify")
    package.serve = serve
    monkeypatch.setitem(sys.modules, "graphify", package)
    monkeypatch.setitem(sys.modules, "graphify.serve", serve)

    result = query_graph("odysseus", "Where is QdrantProjection?", roots=roots)

    assert calls["graph"] is loaded_graph
    assert calls["question"] == "Where is QdrantProjection?"
    assert result == "[root:odysseus]/src/qdrant_projection.py"
