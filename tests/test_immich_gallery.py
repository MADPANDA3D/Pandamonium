import json
import base64
import hashlib

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, Integration
import routes.gallery_routes as gallery_routes
from src import immich_gallery


@pytest.fixture
def immich_env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'immich.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(immich_gallery, "SessionLocal", factory)
    monkeypatch.setattr(immich_gallery, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(immich_gallery, "encrypt", lambda value: f"enc:{value[::-1]}")
    monkeypatch.setattr(
        immich_gallery,
        "decrypt",
        lambda value: value[4:][::-1] if value.startswith("enc:") else value,
    )
    monkeypatch.setattr(
        immich_gallery, "is_encrypted", lambda value: value.startswith("enc:")
    )
    return factory


def _connect(owner="alice", key="alice-secret"):
    return immich_gallery.save_connection(
        owner,
        server_url="http://127.0.0.1:2283/api",
        api_key=key,
    )


def test_connection_is_owner_scoped_encrypted_and_never_returns_key(immich_env):
    alice = _connect()
    bob = _connect("bob", "bob-secret")

    assert alice["server_url"] == "http://127.0.0.1:2283"
    assert alice["api_key_configured"] is True
    assert "api_key" not in alice
    assert bob["configured"] is True

    db = immich_env()
    try:
        rows = db.query(Integration).order_by(Integration.owner).all()
        assert [row.owner for row in rows] == ["alice", "bob"]
        serialized = json.dumps([row.config for row in rows])
        assert "alice-secret" not in serialized
        assert "bob-secret" not in serialized
        assert rows[0].config["api_key"].startswith("enc:")
    finally:
        db.close()

    removed = immich_gallery.remove_connection("alice")
    assert removed == 0
    assert immich_gallery.connection_status("alice")["configured"] is False
    assert immich_gallery.connection_status("bob")["configured"] is True


def test_connection_rejects_unsafe_urls_and_blank_rotation(immich_env):
    with pytest.raises(ValueError, match="credentials"):
        immich_gallery.save_connection(
            "alice", server_url="https://user:pass@example.com", api_key="key"
        )
    with pytest.raises(ValueError, match="link-local"):
        immich_gallery.save_connection(
            "alice", server_url="http://169.254.169.254", api_key="key"
        )

    _connect()
    with pytest.raises(ValueError, match="cannot be blank"):
        immich_gallery.save_connection("alice", api_key="")


@pytest.mark.asyncio
async def test_assets_albums_thumbnails_and_offline_cache_are_bounded(immich_env):
    status = _connect()
    connection_id = status["configured"] and immich_gallery.get_connection("alice")["id"]
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.headers["x-api-key"] == "alice-secret"
        if request.url.path.endswith("/search/metadata"):
            payload = json.loads(request.content)
            assert payload["page"] == 1
            assert payload["size"] == 24
            return httpx.Response(
                200,
                json={
                    "assets": {
                        "items": [
                            {
                                "id": "asset-1",
                                "originalFileName": "Panda.jpg",
                                "checksum": "hash-one",
                                "fileCreatedAt": "2026-09-06T12:00:00Z",
                                "exifInfo": {
                                    "make": "Fuji",
                                    "model": "X100",
                                    "latitude": "<img src=x onerror=alert(1)>",
                                    "longitude": 12,
                                },
                            }
                        ],
                        "total": 1,
                    }
                },
            )
        if request.url.path.endswith("/albums"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "album-1",
                        "albumName": "Pandas",
                        "albumThumbnailAssetId": "asset-1",
                        "assetCount": 1,
                    }
                ],
            )
        if request.url.path.endswith("/thumbnail"):
            return httpx.Response(200, content=b"jpeg-data", headers={"content-type": "image/jpeg"})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    assets = await immich_gallery.list_assets("alice", transport=transport)
    albums = await immich_gallery.list_albums("alice", transport=transport)
    image, media_type, source_state = await immich_gallery.get_thumbnail(
        "alice", assets["items"][0]["id"], size="thumbnail", transport=transport
    )

    assert assets["items"][0]["id"] == f"immich:{connection_id}:asset-1"
    assert assets["items"][0]["source_hash"] == "hash-one"
    assert assets["items"][0]["gps"] is None
    assert albums["albums"][0]["id"] == f"immich:{connection_id}:album:album-1"
    assert image == b"jpeg-data"
    assert media_type == "image/jpeg"
    assert source_state == "healthy"
    assert len(seen) == 3

    def offline(_request: httpx.Request):
        raise httpx.ConnectError("offline")

    cached = await immich_gallery.list_assets(
        "alice", transport=httpx.MockTransport(offline)
    )
    cached_image = await immich_gallery.get_thumbnail(
        "alice",
        assets["items"][0]["id"],
        size="thumbnail",
        transport=httpx.MockTransport(offline),
    )
    assert cached["source_state"] == {
        "status": "offline",
        "message": "Immich is unreachable",
        "stale": True,
    }
    assert cached_image == (b"jpeg-data", "image/jpeg", "offline")
    assert immich_gallery.clear_cache("alice") >= 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "code"),
    [(401, "expired_key"), (403, "permission"), (429, "rate_limited")],
)
async def test_explicit_upstream_states(immich_env, status_code, code):
    _connect()

    def handler(request: httpx.Request):
        return httpx.Response(
            status_code,
            headers={"retry-after": "30"} if status_code == 429 else {},
            request=request,
        )

    with pytest.raises(immich_gallery.ImmichError) as caught:
        await immich_gallery.list_assets(
            "alice", transport=httpx.MockTransport(handler)
        )
    assert caught.value.code == code
    if code == "rate_limited":
        assert caught.value.public()["retry_after"] == "30"


@pytest.mark.asyncio
async def test_missing_and_oversized_thumbnail_states(immich_env, monkeypatch):
    _connect()
    asset_ref = f"immich:{immich_gallery.get_connection('alice')['id']}:asset-1"

    with pytest.raises(immich_gallery.ImmichError) as missing:
        await immich_gallery.get_thumbnail(
            "alice",
            asset_ref,
            size="thumbnail",
            transport=httpx.MockTransport(lambda request: httpx.Response(404, request=request)),
        )
    assert missing.value.code == "missing_thumbnail"

    monkeypatch.setattr(immich_gallery, "MAX_THUMBNAIL_BYTES", 3)
    with pytest.raises(immich_gallery.ImmichError) as oversized:
        await immich_gallery.get_thumbnail(
            "alice",
            asset_ref,
            size="thumbnail",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b"four",
                    headers={"content-type": "image/jpeg"},
                    request=request,
                )
            ),
        )
    assert oversized.value.code == "response_too_large"


@pytest.mark.asyncio
async def test_test_connection_checks_album_and_asset_permissions(immich_env):
    _connect()
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/albums"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"assets": {"items": [], "total": 0}})

    result = await immich_gallery.test_connection(
        "alice", transport=httpx.MockTransport(handler)
    )
    assert result["status"] == "healthy"
    assert calls == [
        ("GET", "/api/albums"),
        ("POST", "/api/search/metadata"),
    ]
    assert immich_gallery.connection_status("alice")["status"] == "healthy"


def test_connection_routes_never_return_key_and_remove_only_owner_cache(
    immich_env, monkeypatch
):
    monkeypatch.setattr(gallery_routes, "SessionLocal", immich_env)
    monkeypatch.setattr(gallery_routes, "require_user", lambda request: "alice")
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "alice")
    app = FastAPI()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = TestClient(app)

    saved = client.put(
        "/api/gallery/immich/connection",
        json={
            "server_url": "http://127.0.0.1:2283",
            "api_key": "route-secret",
            "enabled": True,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["api_key_configured"] is True
    assert "route-secret" not in saved.text
    assert "api_key" not in saved.json()
    assert client.put(
        "/api/gallery/immich/connection", json={"unexpected": True}
    ).status_code == 422

    fetched = client.get("/api/gallery/immich/connection")
    assert fetched.status_code == 200
    assert "route-secret" not in fetched.text

    disabled = client.put(
        "/api/gallery/immich/connection", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    removed = client.delete("/api/gallery/immich/connection")
    assert removed.status_code == 200
    assert client.get("/api/gallery/immich/connection").json()["configured"] is False


@pytest.mark.asyncio
async def test_export_uses_supported_upload_api_checksum_and_bounded_metadata(
    immich_env, tmp_path
):
    _connect()
    photo = tmp_path / "panda.jpg"
    photo.write_bytes(b"panda-photo")
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["key"] = request.headers["x-api-key"]
        seen["checksum"] = request.headers["x-immich-checksum"]
        seen["body"] = request.content
        return httpx.Response(
            201,
            json={"id": "remote-asset", "status": "created"},
            request=request,
        )

    result = await immich_gallery.upload_asset(
        "alice", photo, photo.name, transport=httpx.MockTransport(handler)
    )
    expected = base64.b64encode(hashlib.sha1(b"panda-photo").digest()).decode(
        "ascii"
    )
    assert result == {
        "ok": True,
        "status": "created",
        "asset_id": "remote-asset",
        "bytes": 11,
    }
    assert seen["path"] == "/api/assets"
    assert seen["key"] == "alice-secret"
    assert seen["checksum"] == expected
    assert b'name="assetData"; filename="panda.jpg"' in seen["body"]
    assert b'name="fileCreatedAt"' in seen["body"]
    assert b'name="fileModifiedAt"' in seen["body"]


@pytest.mark.asyncio
async def test_original_download_proxies_only_one_bounded_range(immich_env):
    _connect()
    asset_ref = f"immich:{immich_gallery.get_connection('alice')['id']}:asset-1"
    seen = {}

    def handler(request: httpx.Request):
        seen["range"] = request.headers.get("range")
        return httpx.Response(
            206,
            content=b"part",
            headers={"content-range": "bytes 0-3/10"},
            request=request,
        )

    client, response = await immich_gallery.open_original(
        "alice",
        asset_ref,
        range_header="bytes=0-3",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await response.aread() == b"part"
    finally:
        await response.aclose()
        await client.aclose()
    assert seen["range"] == "bytes=0-3"

    with pytest.raises(immich_gallery.ImmichError) as multi:
        await immich_gallery.open_original(
            "alice", asset_ref, range_header="bytes=0-3,5-7"
        )
    assert multi.value.code == "invalid_range"
