"""Bounded package knowledge with source attribution and shared embeddings.

Collections are data. Linked URLs are never fetched or installed by this index.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import numpy as np

from src.extension_installer import ExtensionLifecycleError

MAX_BYTES = 8 * 1024 * 1024
MAX_CHUNKS = 2048


def index_root(runtime: Path) -> Path:
    # Sibling of the sandbox mount: package code cannot modify the trusted index.
    return runtime.parent / (".knowledge-" + runtime.name)


def schemas(binding: str) -> tuple[dict, dict]:
    def obj(properties: dict, required: list[str] | None = None) -> dict:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False,
        }

    string = {"type": "string"}
    if binding in {"knowledge.status", "knowledge.refresh"}:
        return obj({}), obj(
            {
                "chunks": {"type": "integer"},
                "nodes": {"type": "integer"},
                "edges": {"type": "integer"},
                "vectorized": {"type": "boolean"},
                "source_url": string,
                "source_revision": string,
            }
        )
    if binding == "knowledge.search":
        return obj(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
        ), obj(
            {
                "results": {
                    "type": "array",
                    "items": obj(
                        {
                            "path": string,
                            "line": {"type": "integer"},
                            "text": string,
                            "source_url": string,
                            "source_revision": string,
                            "content_sha256": string,
                        }
                    ),
                },
                "vectorized": {"type": "boolean"},
            }
        )
    raise ExtensionLifecycleError("extension_knowledge_binding_invalid")


def _file(root: Path, relative: str, maximum: int = MAX_BYTES) -> Path:
    from src.extension_intake import safe_source_path

    safe_source_path(relative)
    path = root / relative
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
        or not path.is_file()
    ):
        raise ExtensionLifecycleError("extension_knowledge_source_unsafe")
    if path.stat().st_size > maximum:
        raise ExtensionLifecycleError("extension_knowledge_source_too_large")
    return path


def _client():
    from src.embeddings import get_embedding_client

    client = get_embedding_client()
    if client is None:
        raise ExtensionLifecycleError(
            "extension_needs_setup:Configure an embedding model in Settings."
        )
    return client


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ExtensionLifecycleError("extension_knowledge_cancelled")


def _vectors(
    client, texts: list[str], cancel: threading.Event | None = None
) -> np.ndarray:
    batches = []
    for start in range(0, len(texts), 64):
        _cancelled(cancel)
        batches.extend(
            client.encode(texts[start : start + 64], normalize_embeddings=True)
        )
    _cancelled(cancel)
    vectors = np.asarray(batches, dtype=np.float32)
    if (
        vectors.ndim != 2
        or vectors.shape[0] != len(texts)
        or not 1 <= vectors.shape[1] <= 4096
        or not np.isfinite(vectors).all()
    ):
        raise ExtensionLifecycleError("extension_knowledge_embeddings_invalid")
    return vectors


def index(
    path: Path,
    runtime: Path,
    manifest: dict,
    spec: dict,
    cancel: threading.Event | None = None,
) -> dict:
    """Build off to the side, then atomically replace the previous working index."""
    chunks: list[tuple[str, int, str]] = []
    total = 0
    for relative in spec["files"]:
        _cancelled(cancel)
        source = _file(path, relative)
        total += source.stat().st_size
        if total > MAX_BYTES:
            raise ExtensionLifecycleError("extension_knowledge_source_too_large")
        lines = source.read_text(encoding="utf-8").splitlines()
        text, start = "", 1
        for number, line in enumerate(lines, 1):
            if len(line) > 4000:
                raise ExtensionLifecycleError("extension_knowledge_line_too_large")
            if len(text) + len(line) > 1800 and text:
                chunks.append((relative, start, text))
                text, start = "", number
            text += line + "\n"
        if text.strip():
            chunks.append((relative, start, text))
    if not chunks or len(chunks) > MAX_CHUNKS:
        raise ExtensionLifecycleError("extension_knowledge_chunk_limit")
    nodes, edges = 0, 0
    if spec.get("graph_artifact"):
        graph = json.loads(_file(runtime, spec["graph_artifact"]).read_text())
        graph_nodes, graph_edges = (
            graph.get("nodes"),
            graph.get("edges", graph.get("links")),
        )
        if (
            not isinstance(graph_nodes, list)
            or not graph_nodes
            or not isinstance(graph_edges, list)
        ):
            raise ExtensionLifecycleError("extension_knowledge_graph_invalid")
        nodes, edges = len(graph_nodes), len(graph_edges)
    vectors, model = None, ""
    if spec["vectorize"]:
        client = _client()
        vectors = _vectors(client, [chunk[2] for chunk in chunks], cancel)
        model = str(client.url) + "\0" + str(client.model)
    receipt = {
        "chunks": len(chunks),
        "nodes": nodes,
        "edges": edges,
        "vectorized": vectors is not None,
        "source_url": manifest["source"]["url"],
        "source_revision": manifest["source"]["revision"],
        "model": model,
    }
    destination = index_root(runtime)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    staged = destination / "knowledge.new.sqlite"
    staged.unlink(missing_ok=True)
    try:
        with sqlite3.connect(staged) as db:
            db.execute(
                "CREATE VIRTUAL TABLE chunks USING fts5(path UNINDEXED, line UNINDEXED, text)"
            )
            db.execute("CREATE TABLE vectors (id INTEGER PRIMARY KEY, data BLOB)")
            db.execute("CREATE TABLE metadata (receipt TEXT)")
            db.executemany("INSERT INTO chunks VALUES (?, ?, ?)", chunks)
            if vectors is not None:
                db.executemany(
                    "INSERT INTO vectors VALUES (?, ?)",
                    [(i + 1, v.tobytes()) for i, v in enumerate(vectors)],
                )
            db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(receipt),))
        _cancelled(cancel)
        staged.replace(destination / "knowledge.sqlite")
    finally:
        staged.unlink(missing_ok=True)
    return {key: value for key, value in receipt.items() if key != "model"}


def execute(
    binding: str,
    arguments: dict,
    path: Path,
    runtime: Path,
    manifest: dict,
    spec: dict,
    cancel: threading.Event | None = None,
) -> dict:
    _cancelled(cancel)
    if binding == "knowledge.refresh":
        return index(path, runtime, manifest, spec, cancel)
    db_path = _file(index_root(runtime), "knowledge.sqlite", 64 * 1024 * 1024)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("PRAGMA trusted_schema=OFF")
        receipt = json.loads(db.execute("SELECT receipt FROM metadata").fetchone()[0])
        if binding == "knowledge.status":
            return {key: value for key, value in receipt.items() if key != "model"}
        if binding != "knowledge.search":
            raise ExtensionLifecycleError("extension_knowledge_binding_invalid")
        query = arguments.get("query", "")
        limit = arguments.get("limit", 5)
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 1000
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ExtensionLifecycleError("extension_knowledge_query_invalid")
        if receipt["vectorized"]:
            client = _client()
            if receipt["model"] != str(client.url) + "\0" + str(client.model):
                raise ExtensionLifecycleError(
                    "extension_needs_setup:Embedding model changed; refresh the index."
                )
            vector = _vectors(client, [query], cancel)[0]
            rows = db.execute(
                "SELECT id, data FROM vectors LIMIT ?", (MAX_CHUNKS,)
            ).fetchall()
            scored = []
            for row_id, blob in rows:
                stored = np.frombuffer(blob, dtype=np.float32)
                if stored.shape != vector.shape:
                    raise ExtensionLifecycleError(
                        "extension_knowledge_embeddings_changed"
                    )
                scored.append((float(np.dot(stored, vector)), row_id))
            selected = [row_id for _, row_id in sorted(scored, reverse=True)[:limit]]
            hits = [
                db.execute(
                    "SELECT path,line,text FROM chunks WHERE rowid=?", (row_id,)
                ).fetchone()
                for row_id in selected
            ]
        else:
            terms = re.findall(r"\w+", query)[:16]
            expression = " OR ".join('"' + term + '"' for term in terms)
            hits = (
                db.execute(
                    "SELECT path,line,text FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                    (expression, limit),
                ).fetchall()
                if terms
                else []
            )
    source = urlsplit(receipt["source_url"])
    base = urlunsplit(
        (
            source.scheme,
            source.netloc,
            quote(source.path.removesuffix(".git"), safe="/"),
            "",
            "",
        )
    )
    route = {
        "github.com": "/blob/",
        "gitlab.com": "/-/blob/",
        "codeberg.org": "/src/commit/",
    }[source.hostname]
    return {
        "results": [
            {
                "path": relative,
                "line": int(line),
                "text": text,
                "source_url": base
                + route
                + quote(receipt["source_revision"], safe="")
                + "/"
                + quote(relative, safe="/"),
                "source_revision": receipt["source_revision"],
                "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            for relative, line, text in hits
        ],
        "vectorized": receipt["vectorized"],
    }
