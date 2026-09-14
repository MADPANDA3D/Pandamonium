"""MAD-796: Unsloth Studio external runtime adapter.

Covers storage/redaction of the encrypted access token, versioned capability
discovery over mocked HTTP transports, the explicit error taxonomy
(offline/unauthorized/incompatible/busy/insufficient), the no-accidental-
training guarantee (discovery is GET-only and never touches
``/api/train/start``), and the admin-gated configure/test/disable/remove routes.

No private hostnames or real runtimes in fixtures: studio.example.test only,
and every HTTP call goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "src.unsloth_runtime",
    "routes.unsloth_routes",
    "core.database",
):
    import core.database as database
    import routes.unsloth_routes as unsloth_routes
    import src.unsloth_runtime as unsloth


BASE_URL = "https://studio.example.test"
TOKEN = "studio-token-secret"
GOOD_AUTH = f"Bearer {TOKEN}"

OPENAPI_PATHS = {
    "/api/health": {"get": {}},
    "/api/system": {"get": {}},
    "/api/models/": {"get": {}},
    "/api/train/start": {"post": {}},
    "/api/train/status": {"get": {}},
    "/api/train/stop": {"post": {}},
    "/api/inference/chat": {"post": {}},
}

HEALTH = {"status": "ok", "version": "1.0.0"}

_STUB_UNSET = object()
NOT_JSON = object()


class StudioStub:
    """Mock Studio endpoint that records every request and serves JSON."""

    def __init__(
        self,
        *,
        health=_STUB_UNSET,
        health_status: int = 200,
        system=None,
        system_status: int = 200,
        openapi=_STUB_UNSET,
        openapi_status: int = 200,
        train_status=None,
        train_status_status: int = 200,
        models=None,
        models_status: int = 200,
        require_auth: bool = True,
        expected_auth: str = GOOD_AUTH,
        explode=False,
    ) -> None:
        self.health = HEALTH if health is _STUB_UNSET else health
        self.health_status = health_status
        self.system = {"gpus": [{"name": "GPU", "vram_total_gb": 24}]} if system is None else system
        self.system_status = system_status
        self.openapi = {"paths": OPENAPI_PATHS} if openapi is _STUB_UNSET else openapi
        self.openapi_status = openapi_status
        self.train_status = {"status": "idle", "state": "idle"} if train_status is None else train_status
        self.train_status_status = train_status_status
        self.models = {"models": [{"id": "model-a"}, {"id": "model-b"}]} if models is None else models
        self.models_status = models_status
        self.require_auth = require_auth
        self.expected_auth = expected_auth
        self.explode = explode
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "authorization": request.headers.get("authorization", ""),
                "body": request.content,
            }
        )
        if self.explode:
            raise httpx.ConnectError("connection refused", request=request)
        if request.method != "GET":
            return httpx.Response(405, json={"detail": "read-only stub"}, request=request)
        if self.require_auth and request.url.path != "/api/health":
            if request.headers.get("authorization", "") != self.expected_auth:
                return httpx.Response(401, json={"detail": "unauthorized"}, request=request)
        table = {
            "/api/health": (self.health_status, self.health),
            "/api/system": (self.system_status, self.system),
            "/openapi.json": (self.openapi_status, self.openapi),
            "/api/train/status": (self.train_status_status, self.train_status),
            "/api/models/": (self.models_status, self.models),
        }
        status_code, payload = table.get(request.url.path, (404, {"detail": "not found"}))
        if payload is NOT_JSON:
            return httpx.Response(status_code, text="not json", request=request)
        return httpx.Response(status_code, json=payload, request=request)

    @property
    def methods(self) -> set[str]:
        return {entry["method"] for entry in self.requests}

    @property
    def paths(self) -> list[str]:
        return [entry["path"] for entry in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.fixture
def runtime_env(tmp_path, monkeypatch):
    """Temp sqlite DB, temp Fernet key, DNS-free validation for .test hosts."""
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(unsloth, "SessionLocal", SessionLocal)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    monkeypatch.setattr(
        unsloth, "check_outbound_url", lambda value, block_private=False: (True, "")
    )
    yield SimpleNamespace(
        SessionLocal=SessionLocal,
        engine=engine,
        tmpfile=tmpfile,
        tmp_path=tmp_path,
    )
    engine.dispose()
    tmpfile.close()


def _actor_request(user: str = "admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _save(**overrides):
    values = {"base_url": BASE_URL, "token": TOKEN}
    values.update(overrides)
    return unsloth.save_connection(None, **values)


def _raw_config(runtime_env) -> dict:
    conn = sqlite3.connect(runtime_env.tmpfile.name)
    try:
        row = conn.execute(
            "SELECT config FROM integrations WHERE type = ?", (unsloth.INTEGRATION_TYPE,)
        ).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        conn.close()


def _insert_model_endpoint(runtime_env, endpoint_id: str = "ep-test-1") -> str:
    with runtime_env.SessionLocal() as session:
        session.add(
            database.ModelEndpoint(
                id=endpoint_id,
                name="Studio Export",
                base_url="http://127.0.0.1:8002/v1",
                model_type="llm",
            )
        )
        session.commit()
    return endpoint_id


# ── storage and redaction ────────────────────────────────────────────────


def test_connection_token_is_encrypted_at_rest_and_never_returned(runtime_env):
    status = _save()

    raw = _raw_config(runtime_env)
    assert raw["token"].startswith("enc:")
    assert TOKEN not in json.dumps(raw)
    assert status["configured"] is True
    assert status["token_configured"] is True
    assert TOKEN not in json.dumps(status)
    assert "token" not in status


def test_base_url_normalization_strips_endpoint_suffixes(runtime_env):
    status = _save(base_url="https://studio.example.test/api/health")
    assert status["base_url"] == "https://studio.example.test"


def test_base_url_validation_rejects_credentials_and_non_http(runtime_env):
    with pytest.raises(ValueError):
        _save(base_url="https://user:pass@studio.example.test")
    with pytest.raises(ValueError):
        _save(base_url="ftp://studio.example.test")
    with pytest.raises(ValueError):
        _save(base_url="https://studio.example.test?token=leak")
    with pytest.raises(ValueError):
        _save(token="")


def test_model_endpoint_binding_is_validated_and_reported(runtime_env):
    endpoint_id = _insert_model_endpoint(runtime_env)
    status = _save(model_endpoint_id=endpoint_id)
    assert status["model_endpoint"]["id"] == endpoint_id
    assert status["model_endpoint"]["name"] == "Studio Export"

    with pytest.raises(ValueError):
        _save(model_endpoint_id="missing-endpoint")
    assert runtime_env is not None


def test_remove_connection_clears_row(runtime_env):
    _save()
    assert unsloth.remove_connection(None) == {"ok": True}
    assert unsloth.connection_status(None)["configured"] is False
    with pytest.raises(unsloth.UnslothError):
        unsloth.remove_connection(None)


# ── capability discovery ─────────────────────────────────────────────────


def _discover(stub: StudioStub):
    return asyncio.run(
        unsloth.test_connection(None, transport=stub.transport())
    )


def test_discovery_classifies_training_conversion_and_inference(runtime_env):
    _save()
    result = _discover(StudioStub())

    assert result["ok"] is True
    assert result["state"] == "online"
    assert result["contract"] == unsloth.CONTRACT_ID
    assert result["runtime"]["version"] == "1.0.0"
    capabilities = result["capabilities"]
    assert capabilities["training"]["state"] == "supported"
    assert capabilities["inference"]["state"] == "supported"
    assert capabilities["conversion"]["state"] == "unsupported"
    assert result["unsupported"] == ["conversion"]
    assert result["job"] == {"state": "idle", "normalized": "idle", "active": False}
    assert result["models"] == {"count": 2, "sample": ["model-a", "model-b"]}


def test_discovery_is_get_only_and_never_touches_the_training_start_route(runtime_env):
    _save()
    stub = StudioStub()
    result = _discover(stub)

    assert result["state"] == "online"
    assert stub.methods == {"GET"}
    assert unsloth.ROUTE_TRAIN_START not in stub.paths
    assert all(entry["body"] == b"" for entry in stub.requests)
    assert "/api/train/status" in stub.paths


def test_discovery_reports_unsupported_when_schema_lacks_routes(runtime_env):
    _save()
    stub = StudioStub(
        openapi={
            "paths": {
                "/api/health": {"get": {}},
                "/api/system": {"get": {}},
            }
        }
    )
    result = _discover(stub)

    assert result["state"] == "online"
    assert result["capabilities"]["training"]["state"] == "unsupported"
    assert result["capabilities"]["inference"]["state"] == "unsupported"
    assert result["capabilities"]["conversion"]["state"] == "unsupported"
    assert set(result["unsupported"]) == {"training", "conversion", "inference"}


def test_discovery_without_openapi_falls_back_to_read_only_probes(runtime_env):
    _save()
    stub = StudioStub(openapi_status=404)
    result = _discover(stub)

    assert result["state"] == "online"
    assert result["capabilities"]["training"]["state"] == "supported"
    conversion = result["capabilities"]["conversion"]
    assert conversion["state"] == "unknown"
    assert "OpenAPI" in conversion["detail"]
    assert result["unsupported"] == []
    assert stub.methods == {"GET"}


def test_offline_state_when_transport_fails(runtime_env):
    _save()
    result = _discover(StudioStub(explode=True))

    assert result["ok"] is False
    assert result["state"] == "offline"
    assert "did not answer" in result["message"]
    status = unsloth.connection_status(None)
    assert status["status"] == "offline"


def test_unauthorized_state_when_token_rejected(runtime_env):
    _save(token="stale-token")
    result = _discover(StudioStub())

    assert result["ok"] is False
    assert result["state"] == "unauthorized"
    assert "rejected the access token" in result["message"]
    assert unsloth.connection_status(None)["status"] == "unauthorized"


def test_unauthorized_without_token_never_dials_authenticated_routes(runtime_env):
    _save()
    with runtime_env.SessionLocal() as session:
        row = session.query(database.Integration).filter_by(
            type=unsloth.INTEGRATION_TYPE
        ).one()
        config = dict(row.config or {})
        config["token"] = ""
        row.config = config
        session.commit()

    stub = StudioStub()
    result = asyncio.run(unsloth.test_connection(None, transport=stub.transport()))

    assert result["state"] == "unauthorized"
    assert stub.paths == ["/api/health"]
    assert stub.methods == {"GET"}


def test_incompatible_state_when_health_route_is_missing(runtime_env):
    _save()
    result = _discover(StudioStub(health_status=404))

    assert result["ok"] is False
    assert result["state"] == "incompatible"
    assert "does not expose" in result["message"]


def test_incompatible_state_when_health_is_not_json(runtime_env):
    _save()
    result = _discover(StudioStub(health=NOT_JSON))

    assert result["state"] == "incompatible"


def test_malformed_stored_url_fails_closed_without_dialing(runtime_env):
    stub = StudioStub()
    with pytest.raises(unsloth.UnslothError) as excinfo:
        asyncio.run(
            unsloth.discover_runtime(
                {"base_url": "file:///etc/passwd", "token": "token"},
                transport=stub.transport(),
            )
        )

    assert excinfo.value.code == "invalid_response"
    assert stub.requests == []


def test_busy_state_when_a_training_job_is_running(runtime_env):
    _save()
    result = _discover(StudioStub(train_status={"status": "training"}))

    assert result["state"] == "busy"
    assert "must wait" in result["message"]
    training = result["capabilities"]["training"]
    assert training["state"] == "unavailable"
    assert training["reason"] == "busy"
    assert result["job"] == {"state": "training", "normalized": "running", "active": True}


def test_insufficient_resources_state_when_runtime_reports_no_gpu(runtime_env):
    _save()
    result = _discover(StudioStub(system={"cuda_available": False}))

    assert result["state"] == "insufficient_resources"
    assert "insufficient resources" in result["message"]
    training = result["capabilities"]["training"]
    assert training["state"] == "unavailable"
    assert training["reason"] == "insufficient_resources"


def test_insufficient_resources_state_when_gpu_list_is_empty(runtime_env):
    _save()
    result = _discover(StudioStub(system={"gpus": []}))

    assert result["state"] == "insufficient_resources"


def test_unknown_resources_do_not_claim_insufficiency(runtime_env):
    _save()
    result = _discover(StudioStub(system={"cpu": {"cores": 8}}))

    assert result["state"] == "online"
    assert result["resources"]["state"] == "unknown"


def test_disabled_runtime_fails_closed_without_any_request(runtime_env):
    _save(enabled=False)
    stub = StudioStub()
    result = asyncio.run(unsloth.test_connection(None, transport=stub.transport()))

    assert result["ok"] is False
    assert result["state"] == "disabled"
    assert "disabled" in result["message"]
    assert stub.requests == []
    assert unsloth.connection_status(None)["status"] == "disabled"


def test_saving_a_new_target_resets_cached_capabilities(runtime_env):
    _save()
    _discover(StudioStub())
    assert unsloth.connection_status(None)["capabilities"] is not None

    status = _save(base_url="https://studio-2.example.test")
    assert status["status"] == "untested"
    assert status["capabilities"] is None
    assert status["runtime_version"] is None


def test_cached_capabilities_reads_without_dialing(runtime_env):
    _save()
    _discover(StudioStub())
    cached = unsloth.cached_capabilities(None)

    assert cached["configured"] is True
    assert cached["status"] == "online"
    assert cached["capabilities"]["training"]["state"] == "supported"


# ── routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def routes_env(runtime_env, monkeypatch):
    monkeypatch.setattr(unsloth_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(unsloth_routes, "get_current_user", lambda request: "admin")
    return unsloth_routes.setup_unsloth_routes()


def test_routes_are_admin_gated(runtime_env, monkeypatch):
    def _deny(request):
        raise HTTPException(403, "Admin access required")

    monkeypatch.setattr(unsloth_routes, "require_admin", _deny)
    router = unsloth_routes.setup_unsloth_routes()
    request = _actor_request()
    change = unsloth_routes.UnslothConnectionChange(base_url=BASE_URL, token=TOKEN)

    with pytest.raises(HTTPException) as excinfo:
        _route(router, "/api/unsloth/connection", "GET")(request)
    assert excinfo.value.status_code == 403
    with pytest.raises(HTTPException):
        _route(router, "/api/unsloth/connection", "PUT")(request, change)
    with pytest.raises(HTTPException):
        _route(router, "/api/unsloth/connection", "DELETE")(request)
    with pytest.raises(HTTPException):
        asyncio.run(_route(router, "/api/unsloth/connection/test", "POST")(request))
    with pytest.raises(HTTPException):
        _route(router, "/api/unsloth/capabilities", "GET")(request)


def test_route_inventory_has_no_training_endpoint(routes_env):
    by_path: dict[str, set[str]] = {}
    for route in routes_env.routes:
        path = getattr(route, "path", "")
        by_path.setdefault(path, set()).update(getattr(route, "methods", set()))
        assert "/train" not in path
        assert "/inference" not in path

    assert by_path["/api/unsloth/connection/test"] == {"POST"}
    assert by_path["/api/unsloth/connection"] == {"GET", "PUT", "DELETE"}
    assert by_path["/api/unsloth/capabilities"] == {"GET"}


def test_routes_configure_test_disable_and_remove(routes_env, runtime_env, monkeypatch):
    router = routes_env
    request = _actor_request()
    get_connection = _route(router, "/api/unsloth/connection", "GET")
    put_connection = _route(router, "/api/unsloth/connection", "PUT")
    delete_connection = _route(router, "/api/unsloth/connection", "DELETE")
    test_connection = _route(router, "/api/unsloth/connection/test", "POST")

    assert get_connection(request)["configured"] is False

    saved = put_connection(
        request,
        unsloth_routes.UnslothConnectionChange(
            base_url=BASE_URL,
            token=TOKEN,
        ),
    )
    assert saved["configured"] is True
    assert saved["status"] == "untested"
    assert TOKEN not in json.dumps(saved)

    calls: list[str] = []

    async def _fake_test(owner, *, transport=None):
        calls.append("test")
        return {
            "ok": True,
            "state": "online",
            "reason": "",
            "message": unsloth.ONLINE_MESSAGE,
            "capabilities": {"training": {"state": "supported"}},
        }

    monkeypatch.setattr(unsloth, "test_connection", _fake_test)
    tested = asyncio.run(test_connection(request))
    assert calls == ["test"]
    assert tested["state"] == "online"

    disabled = put_connection(
        request,
        unsloth_routes.UnslothConnectionChange(enabled=False),
    )
    assert disabled["enabled"] is False
    assert disabled["status"] == "disabled"

    assert delete_connection(request) == {"ok": True}
    assert get_connection(request)["configured"] is False


def test_route_capabilities_returns_cached_payload(routes_env, runtime_env):
    _save()
    _discover(StudioStub())
    payload = _route(routes_env, "/api/unsloth/capabilities", "GET")(_actor_request())

    assert payload["status"] == "online"
    assert payload["capabilities"]["training"]["state"] == "supported"
    assert payload["contract"] == unsloth.CONTRACT_ID
    assert datetime.fromisoformat(payload["checked_at"]) is not None