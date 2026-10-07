"""Signed installation waits remain bounded without extending normal requests."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

import src.marketplace_catalog as catalog


def timeout_namespace():
    tree = ast.parse((Path(__file__).parents[1] / "app.py").read_text())
    names = {"_is_timeout_exempt", "_request_hard_timeout", "_RequestTimeoutMiddleware"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    namespace = {
        "_TIMEOUT_EXEMPT_PREFIXES": (),
        "REQUEST_HARD_TIMEOUT": 0.025,
        "_EXTENSION_PREVIEW_TIMEOUT": 0.15,
        "_EXTENSION_EXECUTE_TIMEOUT": 0.2,
        "_BaseHTTPMiddleware": BaseHTTPMiddleware,
        "_JSONResponse": JSONResponse,
        "_asyncio": asyncio,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)  # noqa: S102 - Exercise local middleware without app startup side effects.
    return namespace


def test_only_exact_post_install_operations_get_their_bounded_deadline():
    timeout = timeout_namespace()["_request_hard_timeout"]
    assert timeout("/api/extensions/marketplace/plans", "POST") == 0.15
    assert timeout("/api/extensions/plans/plan-123/execute", "POST") == 0.2
    assert timeout("/api/extensions/marketplace/plans", "GET") == 0.025
    assert timeout("/api/extensions/marketplace/plans/extra", "POST") == 0.025
    assert timeout("/api/extensions/plans//execute", "POST") == 0.025
    assert timeout("/api/extensions/plans/plan-123/execute/extra", "POST") == 0.025
    assert timeout("/api/entertainment/ani-cli/search", "POST") == 0.025


@pytest.mark.asyncio
async def test_slow_signed_preview_can_finish_after_normal_request_deadline():
    async def preview(request):
        await asyncio.sleep(0.06)
        return JSONResponse({"preview": "verified"})

    namespace = timeout_namespace()
    app = Starlette(
        routes=[
            Route("/api/extensions/marketplace/plans", preview, methods=["POST", "GET"])
        ]
    )
    app.add_middleware(namespace["_RequestTimeoutMiddleware"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.post("/api/extensions/marketplace/plans")).json() == {
            "preview": "verified"
        }
        assert (
            await client.get("/api/extensions/marketplace/plans")
        ).status_code == 504


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/extensions/marketplace/plans", "/api/extensions/plans/plan-123/execute"],
)
async def test_explicit_install_operation_deadline_still_stops_a_stalled_response(path):
    async def stalled(request):
        await asyncio.sleep(0.4)
        return JSONResponse({"should_not_return": True})

    namespace = timeout_namespace()
    app = Starlette(routes=[Route(path, stalled, methods=["POST"])])
    app.add_middleware(namespace["_RequestTimeoutMiddleware"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(path)
        assert response.status_code == 504
        assert "timeout" in response.json()["detail"]


@pytest.mark.parametrize("elapsed,allowed", [(230, True), (241, False)])
def test_artifact_download_total_deadline_is_checked_during_transfer(
    monkeypatch, elapsed, allowed
):
    class Stream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b"reviewed package"

        def close(self):
            self.closed = True

    stream = Stream()
    times = iter([0, 0, elapsed])
    monkeypatch.setattr(catalog, "time", SimpleNamespace(monotonic=lambda: next(times)))
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    factory = lambda **kwargs: httpx.Client(transport=transport, **kwargs)
    artifact = {
        "url": "https://example.com/package.tar.gz",
        "size_bytes": len(b"reviewed package"),
    }
    if allowed:
        assert (
            catalog.download_catalog_artifact(artifact, client_factory=factory)
            == b"reviewed package"
        )
    else:
        with pytest.raises(
            catalog.MarketplaceCatalogError, match="marketplace_artifact_timeout"
        ):
            catalog.download_catalog_artifact(artifact, client_factory=factory)
    assert stream.closed
