import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from core.database import Base, GalleryImage, GallerySource, GallerySourceFile
import routes.gallery_routes as gallery_routes
import src.gallery_sources as gallery_sources


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gallery.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_native_conventional_picture_folder_resolution(tmp_path):
    home = tmp_path / "home"
    pictures = home / "My Photos"
    pictures.mkdir(parents=True)
    config = home / ".config"
    config.mkdir()
    (config / "user-dirs.dirs").write_text(
        'XDG_PICTURES_DIR="$HOME/My Photos"\n', encoding="utf-8"
    )

    linux = gallery_sources.discover_gallery_roots(
        platform_name="linux",
        home=home,
        environ={},
        containerized=False,
    )
    mac = gallery_sources.discover_gallery_roots(
        platform_name="darwin",
        home=tmp_path,
        environ={},
        containerized=False,
    )
    windows = gallery_sources.discover_gallery_roots(
        platform_name="win32",
        home=tmp_path,
        environ={},
        containerized=False,
        windows_resolver=lambda: pictures,
    )

    assert linux["candidates"][0]["path"] == str(pictures)
    assert linux["candidates"][0]["available"] is True
    assert mac["candidates"][0]["path"] == str(tmp_path / "Pictures")
    assert windows["candidates"][0]["path"] == str(pictures)

    (config / "user-dirs.dirs").write_text(
        'XDG_PICTURES_DIR="$HOME/"\n', encoding="utf-8"
    )
    disabled = gallery_sources.discover_gallery_roots(
        platform_name="linux",
        home=home,
        environ={},
        containerized=False,
    )
    assert disabled["candidates"] == []


def test_docker_exposes_only_configured_mounted_roots(tmp_path):
    mounted = tmp_path / "mounted"
    unmounted = tmp_path / "ordinary-dir"
    mounted.mkdir()
    unmounted.mkdir()
    env = {
        gallery_sources.MEDIA_ROOTS_ENV: os.pathsep.join(
            (str(mounted), str(unmounted))
        )
    }

    result = gallery_sources.discover_gallery_roots(
        home=tmp_path,
        environ=env,
        containerized=True,
        is_mount=lambda path: path == str(mounted),
    )

    assert result["environment"] == "container"
    assert [item["available"] for item in result["candidates"]] == [True, False]
    assert "explicitly mounted" in result["message"]
    assert all(item["path"] != str(tmp_path / "Pictures") for item in result["candidates"])


def test_source_root_rejects_broad_or_traversing_paths(tmp_path):
    pictures = tmp_path / "Pictures"
    pictures.mkdir()

    with pytest.raises(ValueError, match="too broad"):
        gallery_sources.validate_source_root("/", containerized=False)
    with pytest.raises(ValueError, match="parent traversal"):
        gallery_sources.validate_source_root(
            str(pictures / ".." / "Pictures"), containerized=False
        )


def test_supported_media_contract():
    assert gallery_sources.SUPPORTED_MEDIA_EXTENSIONS == {
        ".gif",
        ".jpeg",
        ".jpg",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".png",
        ".webm",
        ".webp",
    }


def test_native_sync_is_incremental_deduplicated_bounded_and_read_only(
    tmp_path, session_factory, monkeypatch
):
    root = tmp_path / "Pictures"
    nested = root / "album"
    nested.mkdir(parents=True)
    first = root / "first.jpg"
    duplicate = nested / "duplicate.jpg"
    first.write_bytes(b"same-photo")
    duplicate.write_bytes(b"same-photo")
    (root / "ignore.txt").write_text("not media", encoding="utf-8")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    try:
        (root / "unsafe.jpg").symlink_to(outside)
    except OSError:
        pass
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (first, duplicate, outside)
    }
    discovery = {
        "environment": "native",
        "message": "fixture",
        "candidates": [
            {
                "path": str(root),
                "label": "Pictures",
                "available": True,
                "reason": None,
            }
        ],
    }
    db = session_factory()
    try:
        result = gallery_sources.sync_gallery_sources(
            db, "alice", discovery=discovery, limit=100
        )
        source = db.query(GallerySource).one()
        assert result["sources"][0]["indexed"] == 2
        assert db.query(GallerySourceFile).filter_by(active=True).count() == 2
        images = db.query(GalleryImage).filter_by(is_active=True).all()
        assert len(images) == 1
        assert images[0].model == gallery_sources.SOURCE_MODEL
        assert images[0].source_file_id
        assert images[0].owner == "alice"

        hash_calls = 0
        original_hash = gallery_sources._hash_file

        def counted_hash(path):
            nonlocal hash_calls
            hash_calls += 1
            return original_hash(path)

        monkeypatch.setattr(gallery_sources, "_hash_file", counted_hash)
        monkeypatch.setattr(
            gallery_sources,
            "_image_dimensions",
            lambda path: pytest.fail("unchanged images must not be decoded"),
        )
        again = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        assert again["unchanged"] == 2
        assert hash_calls == 0

        for path, expected in before.items():
            assert path.read_bytes() == expected[0]
            assert path.stat().st_mtime_ns == expected[1]
    finally:
        db.close()


def test_same_size_same_mtime_replacement_is_rehashed(
    tmp_path, session_factory, monkeypatch
):
    root = tmp_path / "Pictures"
    root.mkdir()
    photo = root / "photo.jpg"
    photo.write_bytes(b"first-photo")
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    try:
        gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        indexed = db.query(GallerySourceFile).one()
        old_hash = indexed.file_hash
        old_stat = photo.stat()

        replacement = root / "replacement.jpg"
        replacement.write_bytes(b"other-photo")
        os.utime(
            replacement,
            ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns),
        )
        replacement.replace(photo)
        assert photo.stat().st_size == old_stat.st_size
        assert photo.stat().st_mtime_ns == old_stat.st_mtime_ns

        hash_calls = 0
        original_hash = gallery_sources._hash_file

        def counted_hash(path):
            nonlocal hash_calls
            hash_calls += 1
            return original_hash(path)

        monkeypatch.setattr(gallery_sources, "_hash_file", counted_hash)
        result = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        db.refresh(indexed)

        assert result["indexed"] == 1
        assert hash_calls == 1
        assert indexed.file_hash != old_hash
    finally:
        db.close()


def test_unchanged_reconciliation_uses_bounded_gallery_queries(
    tmp_path, session_factory
):
    root = tmp_path / "Pictures"
    root.mkdir()
    for index in range(25):
        (root / f"{index}.jpg").write_bytes(f"photo-{index}".encode())
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    try:
        gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        gallery_selects = []

        def capture_gallery_selects(conn, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT") and "gallery_images" in statement:
                gallery_selects.append(statement)

        event.listen(db.bind, "before_cursor_execute", capture_gallery_selects)
        try:
            result = gallery_sources.scan_gallery_source(db, source, limit=100)
            db.commit()
        finally:
            event.remove(db.bind, "before_cursor_execute", capture_gallery_selects)

        assert result["unchanged"] == 25
        assert len(gallery_selects) == 2
    finally:
        db.close()


def test_rename_delete_and_removable_source_behavior(tmp_path, session_factory):
    root = tmp_path / "Pictures"
    root.mkdir()
    original = root / "original.jpg"
    duplicate = root / "duplicate.jpg"
    original.write_bytes(b"photo")
    duplicate.write_bytes(b"photo")
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    try:
        gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        image = db.query(GalleryImage).filter_by(is_active=True).one()

        renamed = root / "renamed.jpg"
        original.rename(renamed)
        duplicate.unlink()
        changed = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        db.refresh(image)
        assert changed["removed"] == 2
        assert image.is_active is True
        assert image.prompt == "renamed"

        root.rename(tmp_path / "unplugged")
        unavailable = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        db.refresh(image)
        assert unavailable["error"] == "Folder is unavailable"
        assert image.is_active is True

        (tmp_path / "unplugged").rename(root)
        renamed.unlink()
        removed = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        db.refresh(image)
        assert removed["removed"] == 1
        assert image.is_active is False
    finally:
        db.close()


def test_truncated_large_scan_does_not_remove_unseen_rows(tmp_path, session_factory):
    root = tmp_path / "Pictures"
    root.mkdir()
    for index in range(4):
        (root / f"{index}.jpg").write_bytes(str(index).encode())
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    try:
        gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        assert db.query(GallerySourceFile).filter_by(active=True).count() == 4

        (root / "3.jpg").unlink()
        result = gallery_sources.scan_gallery_source(db, source, limit=2)
        db.commit()
        assert result["truncated"] is True
        assert result["removed"] == 0
        assert db.query(GallerySourceFile).filter_by(active=True).count() == 4
    finally:
        db.close()


def test_permission_error_does_not_remove_unseen_rows(
    tmp_path, session_factory, monkeypatch
):
    root = tmp_path / "Pictures"
    nested = root / "private"
    nested.mkdir(parents=True)
    photo = nested / "photo.jpg"
    photo.write_bytes(b"photo")
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    try:
        gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()
        original_scandir = gallery_sources.os.scandir

        def restricted_scandir(path):
            if os.fspath(path) == str(nested):
                raise PermissionError(13, "Permission denied", str(nested))
            return original_scandir(path)

        monkeypatch.setattr(gallery_sources.os, "scandir", restricted_scandir)
        result = gallery_sources.scan_gallery_source(db, source, limit=100)
        db.commit()

        assert "Permission denied" in result["error"]
        assert result["removed"] == 0
        assert db.query(GallerySourceFile).filter_by(active=True).count() == 1
        assert db.query(GalleryImage).filter_by(is_active=True).count() == 1
    finally:
        db.close()


def test_source_route_is_owner_scoped_and_original_is_not_deletable(
    tmp_path, session_factory, monkeypatch
):
    root = tmp_path / "Pictures"
    root.mkdir()
    photo = root / "owner.jpg"
    photo.write_bytes(b"owner-photo")
    db = session_factory()
    source = GallerySource(
        id="source-1",
        owner="alice",
        path=str(root),
        label="Pictures",
        kind="native",
        enabled=True,
    )
    db.add(source)
    db.commit()
    gallery_sources.scan_gallery_source(db, source, limit=10)
    db.commit()
    image = db.query(GalleryImage).filter_by(is_active=True).one()
    image_id = image.id
    db.close()

    monkeypatch.setattr(gallery_routes, "SessionLocal", session_factory)
    monkeypatch.setattr(gallery_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(gallery_routes, "require_user", lambda request: "alice")
    app = FastAPI()
    app.state.auth_manager = type(
        "AdminFixture", (), {"is_admin": lambda self, user: user == "alice"}
    )()
    app.include_router(gallery_routes.setup_gallery_routes())
    client = TestClient(app)

    response = client.get(f"/api/gallery/source/{image_id}/{image.filename}")
    assert response.status_code == 200
    assert response.content == b"owner-photo"
    assert response.headers["cache-control"] == "private, no-cache"

    delete = client.delete(f"/api/gallery/{image_id}")
    assert delete.status_code == 409
    assert photo.read_bytes() == b"owner-photo"

    monkeypatch.setattr(gallery_routes, "require_user", lambda request: "bob")
    assert client.get(f"/api/gallery/source/{image_id}/{image.filename}").status_code == 404
    assert client.get("/api/gallery/sources").status_code == 403


def test_source_video_url_preserves_media_extension():
    image = GalleryImage(
        id="video-1",
        filename="source-video.mp4",
        source_file_id="file-1",
    )

    assert gallery_routes._image_url(image).endswith("/source-video.mp4")
