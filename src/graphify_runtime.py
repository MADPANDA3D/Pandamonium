"""Optional, explicit-root Graphify runtime guard.

Graph creation is operator-invoked through ``scripts/odysseus-graphify``.
The agent-facing MCP surface is read-only and accepts root IDs, never paths.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


GRAPHIFY_ROOTS_ENV = "ODYSSEUS_GRAPHIFY_ROOTS"
GRAPHIFY_MAX_OUTPUT_BYTES = 32 * 1024
GRAPHIFY_MAX_GRAPH_BYTES = 64 * 1024 * 1024
_ROOT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BROAD_ROOTS = frozenset({"/", "/home", "/opt", "/srv", "/var", "/var/lib"})


class GraphifyConfigurationError(ValueError):
    """Stable fail-closed configuration error."""


@dataclass(frozen=True)
class GraphifyRoot:
    root_id: str
    repository_root: Path
    output_root: Path

    @property
    def graph_path(self) -> Path:
        return self.output_root / "graphify-out" / "graph.json"


def _absolute_path(value: Any, *, code: str) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text or not path.is_absolute():
        raise GraphifyConfigurationError(code)
    return path.resolve()


def configured_roots(
    raw: str | None = None,
    *,
    require_repository: bool = True,
) -> dict[str, GraphifyRoot]:
    """Parse the operator-owned allowlist; no filesystem discovery occurs."""
    value = os.getenv(GRAPHIFY_ROOTS_ENV, "") if raw is None else raw
    if not str(value or "").strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise GraphifyConfigurationError("graphify_roots_malformed") from exc
    if not isinstance(parsed, Mapping) or not 1 <= len(parsed) <= 16:
        raise GraphifyConfigurationError("graphify_roots_malformed")

    roots: dict[str, GraphifyRoot] = {}
    for raw_id, raw_config in parsed.items():
        root_id = str(raw_id or "")
        if not _ROOT_ID.fullmatch(root_id) or not isinstance(raw_config, Mapping):
            raise GraphifyConfigurationError("graphify_root_invalid")
        if set(raw_config) != {"repository_root", "output_root"}:
            raise GraphifyConfigurationError("graphify_root_fields_invalid")
        repository = _absolute_path(
            raw_config.get("repository_root"), code="graphify_repository_root_invalid"
        )
        output = _absolute_path(
            raw_config.get("output_root"), code="graphify_output_root_invalid"
        )
        if repository.as_posix() in _BROAD_ROOTS:
            raise GraphifyConfigurationError("graphify_repository_root_too_broad")
        if require_repository and (not repository.is_dir() or repository.is_symlink()):
            raise GraphifyConfigurationError("graphify_repository_root_unavailable")
        if output.as_posix() in _BROAD_ROOTS:
            raise GraphifyConfigurationError("graphify_output_root_too_broad")
        if (
            output == repository
            or output.is_relative_to(repository)
            or repository.is_relative_to(output)
        ):
            raise GraphifyConfigurationError("graphify_output_must_be_isolated")
        roots[root_id] = GraphifyRoot(root_id, repository, output)
    return roots


def resolve_root(root_id: str, roots: Mapping[str, GraphifyRoot] | None = None) -> GraphifyRoot:
    selected_roots = configured_roots() if roots is None else roots
    selected = dict(selected_roots).get(str(root_id or ""))
    if selected is None:
        raise GraphifyConfigurationError("graphify_root_not_configured")
    return selected


def build_command(root: GraphifyRoot, *, python: str | None = None) -> list[str]:
    """Return the fixed offline/code-only build command for one admitted root."""
    return [
        python or sys.executable,
        "-m",
        "graphify",
        "extract",
        str(root.repository_root),
        "--out",
        str(root.output_root),
        "--code-only",
        "--no-cluster",
        "--no-dedup",
        "--max-workers",
        "1",
    ]


def sanitize_graph_output(
    value: Any,
    root: GraphifyRoot,
    *,
    maximum_bytes: int = GRAPHIFY_MAX_OUTPUT_BYTES,
) -> str:
    """Redact configured topology and cap one tool response by UTF-8 bytes."""
    text = str(value or "")
    for path in sorted(
        (str(root.output_root), str(root.repository_root)), key=len, reverse=True
    ):
        text = text.replace(path, f"[root:{root.root_id}]")
    encoded = text.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return text
    suffix = "\n[graphify output truncated]"
    budget = max(0, maximum_bytes - len(suffix.encode("utf-8")))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


def _ready_graph(root: GraphifyRoot) -> Path:
    path = root.graph_path
    try:
        stat = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GraphifyConfigurationError("graphify_graph_unavailable") from exc
    if path.is_symlink() or not path.is_file() or stat.st_size > GRAPHIFY_MAX_GRAPH_BYTES:
        raise GraphifyConfigurationError("graphify_graph_unsafe")
    expected = (root.output_root / "graphify-out" / "graph.json").resolve()
    if resolved != expected:
        raise GraphifyConfigurationError("graphify_graph_unsafe")
    return resolved


def graph_status(root_id: str, roots: Mapping[str, GraphifyRoot] | None = None) -> dict[str, Any]:
    root = resolve_root(root_id, roots)
    try:
        path = _ready_graph(root)
    except GraphifyConfigurationError:
        return {"root_id": root.root_id, "state": "not_built", "nodes": 0, "edges": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        nodes = payload.get("nodes", [])
        links = payload.get("links", payload.get("edges", []))
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise ValueError("invalid graph")
    except (OSError, TypeError, ValueError):
        return {"root_id": root.root_id, "state": "degraded", "nodes": 0, "edges": 0}
    return {
        "root_id": root.root_id,
        "state": "ready",
        "nodes": len(nodes),
        "edges": len(links),
    }


def query_graph(
    root_id: str,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = 3,
    token_budget: int = 2_000,
    roots: Mapping[str, GraphifyRoot] | None = None,
) -> str:
    root = resolve_root(root_id, roots)
    prompt = str(question or "").strip()
    if not prompt or len(prompt) > 2_000:
        raise GraphifyConfigurationError("graphify_question_invalid")
    if mode not in {"bfs", "dfs"}:
        raise GraphifyConfigurationError("graphify_mode_invalid")
    bounded_depth = min(max(int(depth), 1), 6)
    bounded_budget = min(max(int(token_budget), 100), 4_000)
    path = _ready_graph(root)
    try:
        from graphify.serve import _load_graph, _query_graph_text

        loaded_graph = _load_graph(str(path))
        # Graphify 0.9.53 returns the graph directly. Older builds returned a
        # ``(graph, communities)`` pair, so accept both without coupling the
        # guarded adapter to one private return shape.
        graph = loaded_graph[0] if isinstance(loaded_graph, tuple) else loaded_graph
        result = _query_graph_text(
            graph,
            prompt,
            mode=mode,
            depth=bounded_depth,
            token_budget=bounded_budget,
            graph_path=str(path),
        )
    except ImportError as exc:
        raise GraphifyConfigurationError("graphify_dependency_unavailable") from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise GraphifyConfigurationError("graphify_query_failed") from exc
    return sanitize_graph_output(result, root)
