"""MAD-937: Nextcloud discover/connect/browse read-only.

Covers owner-scoped encrypted connection storage, tailnet discovery via
status.php, WebDAV list/search/read with bounds and path safety, redacted
paths, honest failure copy, and the agent tool's node+path citations.

No private hostnames in fixtures: cloud.example.test and 100.64.x.x only.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, Integration
from src import nextcloud_gallery as nc

BASE_URL = "https://cloud.example.test"
USERNAME = "alice"
APP_PASSWORD = "app-pass-secret"

MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/alice/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Reports/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Reports</d:displayname>
      <d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Mon, 08 Sep 2026 10:00:00 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Reports/q3.txt</d:href>
    <d:propstat><d:prop>
      <d:displayname>q3.txt</d:displayname>
      <d:getcontentlength>42</d:getcontentlength>
      <d:getcontenttype>text/plain</d:getcontenttype>
      <d:getlastmodified>Mon, 08 Sep 2026 10:00:00 GMT</d:getlastmodified>
      <d:getetag>&quot;abc&quot;</d:getetag>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/.env</d:href>
    <d:propstat><d:prop>
      <d:displayname>.env</d:displayname>
      <d:getcontentlength>10</d:getcontentlength>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/id_rsa</d:href>
    <d:propstat><d:prop>
      <d:displayname>id_rsa</d:displayname>
      <d:getcontentlength>10</d:getcontentlength>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""

STATUS_JSON = {
    "installed": True,
    "version": "29.0.4.2",
    "versionstring": "29.0.4",
    "productname": "Nextcloud",
}


@pytest.fixture
def nc_env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nextcloud.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(nc, "SessionLocal", factory)
    monkeypatch.setattr(nc, "encrypt", lambda value: f"enc:{value[::-1]}")
    monkeypatch.setattr(
        nc, "decrypt", lambda value: value[4:][::-1] if value.startswith("enc:") else value
    )
    monkeypatch.setattr(nc, "is_encrypted", lambda value: value.startswith("enc:"))
    # Fixtures use reserved placeholder hostnames (cloud.example.test) that do
    # not resolve in CI; DNS safety itself is covered by src/url_safety tests.
    monkeypatch.setattr(nc, "check_outbound_url", lambda value, block_private=False: (True, ""))
    yield factory
    engine.dispose()


def _connect(owner: str = "alice", password: str = APP_PASSWORD):
    return nc.save_connection(
        owner,
        server_url=BASE_URL,
        username=USERNAME,
        app_password=password,
    )


def _dav_handler(request: httpx.Request):
    if request.url.path.endswith("/status.php"):
        return httpx.Response(200, json=STATUS_JSON, request=request)
    if request.method == "PROPFIND":
        return httpx.Response(
            207,
            text=MULTISTATUS,
            headers={"content-type": "application/xml"},
            request=request,
        )
    if request.method == "GET":
        if request.url.path.endswith("/Reports/q3.txt"):
            return httpx.Response(
                200,
                text="quarterly numbers",
                headers={"content-type": "text/plain"},
                request=request,
            )
        return httpx.Response(404, request=request)
    return httpx.Response(405, request=request)


def _transport():
    return httpx.MockTransport(_dav_handler)


# ── connection storage ───────────────────────────────────────────────────


def test_connection_is_owner_scoped_encrypted_and_hides_app_password(nc_env):
    alice = _connect()
    bob = _connect("bob", "bob-pass-secret")

    assert alice["configured"] is True
    assert alice["server_url"] == BASE_URL
    assert alice["username"] == USERNAME
    assert alice["app_password_configured"] is True
    assert "app-pass-secret" not in json.dumps(alice)
    assert bob["configured"] is True

    db = nc_env()
    try:
        rows = db.query(Integration).order_by(Integration.owner).all()
        serialized = json.dumps([row.config for row in rows])
        assert "app-pass-secret" not in serialized
        assert "bob-pass-secret" not in serialized
        assert rows[0].config["app_password"].startswith("enc:")
    finally:
        db.close()

    nc.remove_connection("alice")
    assert nc.connection_status("alice")["configured"] is False
    assert nc.connection_status("bob")["configured"] is True


def test_connection_rejects_unsafe_urls_and_blank_secrets(nc_env):
    with pytest.raises(ValueError, match="credentials"):
        nc.save_connection(
            "alice", server_url="https://user:pass@cloud.example.test", username="a", app_password="x"
        )
    with pytest.raises(ValueError, match="HTTP"):
        nc.save_connection("alice", server_url="ftp://cloud.example.test", username="a", app_password="x")
    with pytest.raises(ValueError, match="username"):
        nc.save_connection("alice", server_url=BASE_URL, username="", app_password="x")

    _connect()
    with pytest.raises(ValueError, match="blank"):
        nc.save_connection("alice", app_password="")


def test_connection_status_never_leaks_password(nc_env):
    _connect()

    status = nc.connection_status("alice")
    assert status["app_password_configured"] is True
    assert APP_PASSWORD not in json.dumps(status)


# ── tailnet discovery ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tailnet_discovery_probes_status_php_without_credentials():
    status = {
        "Self": {"HostName": "pandamonium", "TailscaleIPs": ["100.64.0.1"]},
        "Peer": {
            "cloud": {
                "HostName": "cloud-node",
                "Online": True,
                "TailscaleIPs": ["100.64.0.2"],
                "DNSName": "cloud-node.example-tailnet.ts.net.",
            },
            "offline": {"HostName": "offline-pc", "Online": False, "TailscaleIPs": ["100.64.0.3"]},
        },
    }
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "100.64.0.2" and request.url.path.endswith("/status.php"):
            return httpx.Response(200, json=STATUS_JSON, request=request)
        return httpx.Response(404, request=request)

    result = await nc.discover_tailnet_nextcloud(
        status=status, transport=httpx.MockTransport(handler)
    )

    assert result["available"] is True
    assert result["devices_checked"] == 2
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["kind"] == "nextcloud"
    assert candidate["provider"] == "Nextcloud"
    assert candidate["device"] == "cloud-node"
    assert candidate["connectable"] is True
    assert all(auth is None for _url, auth in seen)
    assert all("100.64.0.3" not in url for url, _auth in seen)


@pytest.mark.asyncio
async def test_tailnet_discovery_degrades_when_tailscale_is_unavailable():
    result = await nc.discover_tailnet_nextcloud(status=None)
    assert result["available"] is False
    assert result["candidates"] == []
    assert "manual" in result["message"].lower() or "unavailable" in result["message"].lower()


# ── connection test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_test_reports_healthy_without_echoing_secrets(nc_env):
    _connect()
    result = await nc.test_connection("alice", transport=_transport())
    assert result["ok"] is True
    assert result["status"] == "healthy"
    assert APP_PASSWORD not in json.dumps(result)


@pytest.mark.asyncio
async def test_connection_test_maps_auth_failure_honestly(nc_env):
    _connect()

    def handler(request: httpx.Request):
        if request.url.path.endswith("/status.php"):
            return httpx.Response(200, json=STATUS_JSON, request=request)
        return httpx.Response(401, request=request)

    with pytest.raises(nc.NextcloudError) as caught:
        await nc.test_connection("alice", transport=httpx.MockTransport(handler))

    assert caught.value.code == "auth_failed"
    assert "app password" in caught.value.message.lower()
    assert APP_PASSWORD not in caught.value.message
    assert nc.connection_status("alice")["status"] == "auth_failed"


@pytest.mark.asyncio
async def test_connection_test_maps_unreachable_honestly(nc_env):
    _connect()

    def handler(request: httpx.Request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(nc.NextcloudError) as caught:
        await nc.test_connection("alice", transport=httpx.MockTransport(handler))

    assert caught.value.code == "offline"
    assert "unreachable" in caught.value.message.lower()


# ── browse / search / read ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_folder_returns_entries_with_refs_and_filters_redacted(nc_env):
    _connect()
    result = await nc.list_folder("alice", path="", transport=_transport())

    paths = [entry["path"] for entry in result["entries"]]
    assert paths == ["Reports", "Reports/q3.txt"]
    assert result["entries"][0]["is_dir"] is True
    file_entry = result["entries"][1]
    assert file_entry["size"] == 42
    assert file_entry["content_type"] == "text/plain"
    assert file_entry["ref"].startswith("nextcloud:")
    assert ".env" not in json.dumps(result)
    assert "id_rsa" not in json.dumps(result)


@pytest.mark.asyncio
async def test_list_folder_requires_a_connection(nc_env):
    with pytest.raises(nc.NextcloudError) as caught:
        await nc.list_folder("nobody", transport=_transport())

    assert caught.value.code == "unconfigured"


@pytest.mark.asyncio
async def test_list_folder_rejects_traversal_paths(nc_env):
    _connect()
    with pytest.raises(nc.NextcloudError) as caught:
        await nc.list_folder("alice", path="../secrets", transport=_transport())

    assert caught.value.code == "invalid_path"


@pytest.mark.asyncio
async def test_search_files_matches_names_and_excludes_redacted(nc_env):
    _connect()
    result = await nc.search_files("alice", "q3", transport=_transport())

    assert [item["path"] for item in result["results"]] == ["Reports/q3.txt"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_read_file_returns_text_with_node_and_path_citation(nc_env):
    _connect()
    result = await nc.read_file("alice", "Reports/q3.txt", transport=_transport())

    assert result["content"] == "quarterly numbers"
    assert result["truncated"] is False
    assert result["source"]["kind"] == "nextcloud_file"
    assert result["source"]["node"] == BASE_URL
    assert result["source"]["path"] == "Reports/q3.txt"
    assert BASE_URL in result["citation"]
    assert "Reports/q3.txt" in result["citation"]


@pytest.mark.asyncio
async def test_read_file_truncates_at_bound(nc_env):
    _connect()

    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            content=b"x" * 5000,
            headers={"content-type": "text/plain"},
            request=request,
        )

    result = await nc.read_file(
        "alice", "big.txt", max_bytes=1024, transport=httpx.MockTransport(handler)
    )

    assert result["truncated"] is True
    assert len(result["content"]) <= 1024 + 80
    assert "truncated at 1024 bytes" in result["content"]


@pytest.mark.asyncio
async def test_read_file_denies_redacted_paths(nc_env):
    _connect()
    with pytest.raises(nc.NextcloudError) as caught:
        await nc.read_file("alice", ".ssh/id_rsa", transport=_transport())

    assert caught.value.code == "redacted_path"
    assert "excluded" in caught.value.message.lower()


@pytest.mark.asyncio
async def test_read_file_maps_missing_file_honestly(nc_env):
    _connect()
    with pytest.raises(nc.NextcloudError) as caught:
        await nc.read_file("alice", "Missing/file.txt", transport=_transport())

    assert caught.value.code == "not_found"
    assert "not available" in caught.value.message.lower() or "not found" in caught.value.message.lower()


def test_redacted_paths_cover_secret_shapes():
    assert nc.is_redacted_path(".env")
    assert nc.is_redacted_path("config/id_rsa")
    assert nc.is_redacted_path("keys/server.pem")
    assert nc.is_redacted_path("private.key")
    assert not nc.is_redacted_path("Reports/q3.txt")


def test_routes_expose_owner_scoped_connection_and_validate(nc_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routes.nextcloud_routes as nextcloud_routes

    monkeypatch.setattr(nextcloud_routes, "require_user", lambda request: "alice")
    app = FastAPI()
    app.include_router(nextcloud_routes.setup_nextcloud_routes())
    client = TestClient(app)

    unconfigured = client.get("/api/nextcloud/connection").json()
    assert unconfigured["configured"] is False
    assert "app_password" not in unconfigured

    saved = client.put(
        "/api/nextcloud/connection",
        json={"server_url": BASE_URL, "username": USERNAME, "app_password": APP_PASSWORD},
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert APP_PASSWORD not in saved.text

    rejected = client.put(
        "/api/nextcloud/connection", json={"server_url": "ftp://cloud.example.test"}
    )
    assert rejected.status_code == 400
    assert "HTTP" in rejected.json()["detail"]
