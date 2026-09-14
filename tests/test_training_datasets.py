"""MAD-797: reviewed dataset manifests.

Covers explicit opt-in item creation (nothing harvested), provenance fields,
screening (reject secrets / redact PII), review + fingerprint lifecycle,
exclusion/deletion paths, source resolvers, JSONL export, and admin-gated
routes. Fixtures use reserved placeholder data only; no real personal data.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "src.training_datasets",
    "routes.training_routes",
    "core.database",
):
    import core.database as database
    import routes.training_routes as training_routes
    import src.training_datasets as datasets

    # Snapshot the real ORM classes at collection time. Some unrelated tests
    # rebind core.database.TaskRun/ScheduledTask to local test classes and do
    # not restore them, which would otherwise leak a reduced schema into this
    # file's fixtures at run time.
    TaskRunModel = database.TaskRun
    ScheduledTaskModel = database.ScheduledTask


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(datasets, "SessionLocal", SessionLocal)
    monkeypatch.setattr(datasets, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(datasets, "PERSONAL_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv(datasets.FILE_ROOTS_ENV, raising=False)

    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)
    yield SimpleNamespace(
        SessionLocal=SessionLocal,
        engine=engine,
        tmpfile=tmpfile,
        tmp_path=tmp_path,
    )
    engine.dispose()
    tmpfile.close()


def _raw_content(data_env, item_id: str) -> str | None:
    conn = sqlite3.connect(data_env.tmpfile.name)
    try:
        row = conn.execute(
            "SELECT content FROM training_dataset_items WHERE id = ?", (item_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _make_dataset() -> dict:
    return datasets.create_dataset(
        "Fixture set", description="tiny fixture", license_summary="operator-owned"
    )


def _add(dataset_id: str, content: str, *, license_value: str = "operator-owned") -> dict:
    return datasets.add_item(
        dataset_id,
        source_kind="manual",
        content=content,
        consent_license=license_value,
        actor="admin",
    )


def _approve(dataset_id: str, item_id: str) -> dict:
    return datasets.review_item(dataset_id, item_id, decision="approve", actor="admin")


def _reviewed_dataset(*texts: str) -> dict:
    dataset = _make_dataset()
    for text in texts:
        _approve(dataset["id"], _add(dataset["id"], text)["id"])
    return datasets.review_dataset(dataset["id"], actor="admin")


# ── opt-in only ──────────────────────────────────────────────────────────


def test_dataset_starts_empty_and_never_harvests_existing_sources(data_env):
    with data_env.SessionLocal() as session:
        session.add(
            database.Session(
                id="sess-1", name="Private chat", endpoint_url="http://x", model="m"
            )
        )
        session.add(database.ChatMessage(id="msg-1", session_id="sess-1", role="user", content="private convo"))
        session.add(database.Memory(id="mem-1", text="private memory"))
        session.commit()

    dataset = _make_dataset()
    loaded = datasets.get_dataset(dataset["id"])

    assert loaded["item_count"] == 0
    assert loaded["items"] == []
    assert loaded["status"] == "draft"


def test_item_records_provenance_consent_hash_and_exclusion_path(data_env):
    dataset = _make_dataset()
    item = _add(dataset["id"], "A short reviewed note.")

    assert item["source_kind"] == "manual"
    assert item["owner"] == "admin"
    assert item["consent_license"] == "operator-owned"
    assert item["review_state"] == "pending"
    assert len(item["content_hash"]) == 64
    assert item["exclusion_path"].startswith("Remove this item")
    assert f"/datasets/{dataset['id']}/items/{item['id']}" in item["exclusion_path"]

    raw = _raw_content(data_env, item["id"])
    assert raw.startswith("enc:")
    assert "short reviewed note" not in raw


def test_consent_license_is_required(data_env):
    dataset = _make_dataset()
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.add_item(dataset["id"], source_kind="manual", content="x", consent_license="")
    assert excinfo.value.code == "invalid"


# ── screening ────────────────────────────────────────────────────────────


def test_secret_item_is_rejected_and_stores_no_content(data_env):
    dataset = _make_dataset()
    item = _add(dataset["id"], 'password = "hunter2-not-a-real-secret"')

    assert item["review_state"] == "rejected"
    assert item["content_hash"] is None
    assert item["review_reason"]
    assert "preview" not in item
    raw = _raw_content(data_env, item["id"])
    assert raw is None
    assert "hunter2" not in json.dumps(item)


def test_private_key_block_is_rejected(data_env):
    dataset = _make_dataset()
    item = _add(
        dataset["id"],
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfake-material\n-----END OPENSSH PRIVATE KEY-----",
    )
    assert item["review_state"] == "rejected"
    assert item["content_hash"] is None


def test_pii_is_redacted_and_reported(data_env):
    dataset = _make_dataset()
    item = _add(dataset["id"], "Reach me at alice@example.test or (555) 123-4567.")

    assert item["review_state"] == "redacted"
    assert item["redaction"]["redactions"]["email_addresses"] == 1
    assert item["redaction"]["redactions"]["phone_numbers"] == 1
    stored = datasets.get_dataset(dataset["id"])["items"][0]["preview"]
    assert "[email]" in stored
    assert "[phone]" in stored
    assert "alice@example.test" not in json.dumps(item)


def test_approve_rechecks_screening_and_refuses_tampered_content(data_env):
    dataset = _make_dataset()
    item = _add(dataset["id"], "Safe text.")
    with data_env.SessionLocal() as session:
        row = session.query(database.TrainingDatasetItem).filter_by(id=item["id"]).one()
        row.content = "api_key = abcdef123456"
        session.commit()

    with pytest.raises(datasets.TrainingDataError) as excinfo:
        _approve(dataset["id"], item["id"])
    assert excinfo.value.code == "rejected_item"

    refreshed = datasets.get_dataset(dataset["id"])["items"][0]
    assert refreshed["review_state"] == "rejected"
    assert refreshed["content_hash"] is None


# ── review lifecycle and fingerprint ─────────────────────────────────────


def test_review_requires_every_item_decided(data_env):
    dataset = _make_dataset()
    _add(dataset["id"], "First.")

    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.review_dataset(dataset["id"], actor="admin")
    assert excinfo.value.code == "unreviewed_items"


def test_fingerprint_tracks_approved_snapshot_and_resets_on_change(data_env):
    dataset = _make_dataset()
    first = _add(dataset["id"], "First approved item.")
    _approve(dataset["id"], first["id"])
    reviewed = datasets.review_dataset(dataset["id"], actor="admin")

    verified, items = datasets.verified_dataset(dataset["id"])
    assert verified.fingerprint == reviewed["fingerprint"]
    assert len(items) == 1

    second = _add(dataset["id"], "Second item arrives later.")
    after_add = datasets.get_dataset(dataset["id"])
    assert after_add["status"] == "draft"
    assert after_add["fingerprint"] is None

    _approve(dataset["id"], second["id"])
    re_reviewed = datasets.review_dataset(dataset["id"], actor="admin")
    assert re_reviewed["fingerprint"] != reviewed["fingerprint"]
    assert re_reviewed["item_count"] == 2


def test_rejected_and_excluded_items_are_not_in_the_fingerprint(data_env):
    dataset = _make_dataset()
    keep = _add(dataset["id"], "Keep this.")
    drop = _add(dataset["id"], "Drop this.")
    _approve(dataset["id"], keep["id"])
    datasets.review_item(dataset["id"], drop["id"], decision="exclude", actor="admin")
    reviewed = datasets.review_dataset(dataset["id"], actor="admin")

    assert reviewed["item_count"] == 1
    _, items = datasets.verified_dataset(dataset["id"])
    assert [item.id for item in items] == [keep["id"]]


def test_remove_item_is_the_deletion_path_and_leaves_source_untouched(data_env):
    dataset = _reviewed_dataset("Kept.")
    item = _add(dataset["id"], "Remove me.")
    _approve(dataset["id"], item["id"])
    datasets.review_dataset(dataset["id"], actor="admin")

    result = datasets.remove_item(dataset["id"], item["id"])
    assert result == {"ok": True, "removed_item_id": item["id"], "source_modified": False}
    after = datasets.get_dataset(dataset["id"])
    assert after["status"] == "draft"
    assert all(entry["id"] != item["id"] for entry in after["items"])


def test_retired_dataset_is_read_only(data_env):
    dataset = _reviewed_dataset("Kept.")
    retired = datasets.retire_dataset(dataset["id"], actor="admin")
    assert retired["status"] == "retired"
    assert datasets.get_dataset(dataset["id"])["status"] == "retired"
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        _add(dataset["id"], "More text.")
    assert excinfo.value.code == "retired"


# ── explicit source resolvers ────────────────────────────────────────────


def test_file_source_needs_configured_roots_and_stays_inside_them(data_env, monkeypatch):
    dataset = _make_dataset()
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.add_item(
            dataset["id"],
            source_kind="file",
            source_ref={"path": "/tmp/whatever.txt"},
            consent_license="operator-owned",
            actor="admin",
        )
    assert excinfo.value.code == "file_roots_unconfigured"

    root = data_env.tmp_path / "approved"
    root.mkdir()
    inside = root / "notes.txt"
    inside.write_text("Approved training text.")
    outside = data_env.tmp_path / "outside.txt"
    outside.write_text("Outside.")
    monkeypatch.setenv(datasets.FILE_ROOTS_ENV, json.dumps([str(root)]))

    item = datasets.add_item(
        dataset["id"],
        source_kind="file",
        source_ref={"path": str(inside)},
        consent_license="operator-owned",
        actor="admin",
    )
    assert item["source_ref"]["path"] == str(inside)

    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.add_item(
            dataset["id"],
            source_kind="file",
            source_ref={"path": str(outside)},
            consent_license="operator-owned",
            actor="admin",
        )
    assert excinfo.value.code == "file_outside_roots"


def test_conversation_memory_and_tool_trace_resolvers_use_explicit_ids(data_env):
    with data_env.SessionLocal() as session:
        session.add(
            database.Session(id="sess-9", name="Chat", endpoint_url="http://x", model="m")
        )
        session.add(
            database.ChatMessage(id="msg-9", session_id="sess-9", role="user", content="chosen message")
        )
        session.add(database.Memory(id="mem-9", text="chosen memory", owner="alice"))
        session.add(ScheduledTaskModel(id="task-9", name="Nightly", owner="alice"))
        session.add(
            TaskRunModel(id="run-9", task_id="task-9", status="success", steps='[{"tool":"ls"}]')
        )
        session.commit()

    dataset = _make_dataset()
    conversation = datasets.add_item(
        dataset["id"],
        source_kind="conversation",
        source_ref={"session_id": "sess-9", "message_ids": ["msg-9"]},
        consent_license="operator-owned",
        actor="admin",
    )
    memory = datasets.add_item(
        dataset["id"],
        source_kind="memory",
        source_ref={"memory_ids": ["mem-9"]},
        consent_license="operator-owned",
        actor="admin",
    )
    trace = datasets.add_item(
        dataset["id"],
        source_kind="tool_trace",
        source_ref={"run_id": "run-9"},
        consent_license="operator-owned",
        actor="admin",
    )
    assert conversation["source_ref"]["message_ids"] == ["msg-9"]
    assert memory["owner"] == "alice"
    assert trace["source_ref"]["run_id"] == "run-9"

    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.add_item(
            dataset["id"],
            source_kind="conversation",
            source_ref={"session_id": "missing"},
            consent_license="operator-owned",
            actor="admin",
        )
    assert excinfo.value.code == "session_not_found"


def test_unknown_source_kind_is_rejected(data_env):
    dataset = _make_dataset()
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.add_item(
            dataset["id"],
            source_kind="everything",
            content="x",
            consent_license="operator-owned",
            actor="admin",
        )
    assert excinfo.value.code == "invalid_source_kind"


# ── export ───────────────────────────────────────────────────────────────


def test_export_jsonl_requires_review_and_writes_bounded_file(data_env):
    draft = _make_dataset()
    _add(draft["id"], "Draft only.")
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        datasets.export_dataset_jsonl(draft["id"])
    assert excinfo.value.code == "not_reviewed"

    reviewed = _reviewed_dataset("First item.", "Second item.")
    result = datasets.export_dataset_jsonl(reviewed["id"])
    assert result["item_count"] == 2
    assert result["fingerprint"] == reviewed["fingerprint"]
    path = data_env.tmp_path / "data" / "training_datasets" / reviewed["id"] / "dataset.jsonl"
    assert result["path"] == str(path)
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["text"] for line in lines} == {"First item.", "Second item."}


# ── routes ───────────────────────────────────────────────────────────────


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


@pytest.fixture
def routes_env(data_env, monkeypatch):
    monkeypatch.setattr(training_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(training_routes, "get_current_user", lambda request: "admin")
    import src.training_jobs as jobs
    import src.unsloth_runtime as unsloth

    monkeypatch.setattr(jobs, "SessionLocal", data_env.SessionLocal, raising=False)
    monkeypatch.setattr(unsloth, "SessionLocal", data_env.SessionLocal, raising=False)
    monkeypatch.setattr(jobs, "datasets", datasets, raising=False)
    monkeypatch.setattr(training_routes, "datasets", datasets, raising=False)
    monkeypatch.setattr(training_routes, "jobs", jobs, raising=False)
    return training_routes.setup_training_routes()


def _request():
    return SimpleNamespace(
        state=SimpleNamespace(current_user="admin", api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def test_training_routes_are_admin_gated(data_env, monkeypatch):
    def _deny(request):
        raise HTTPException(403, "Admin access required")

    monkeypatch.setattr(training_routes, "require_admin", _deny)
    router = training_routes.setup_training_routes()

    with pytest.raises(HTTPException) as excinfo:
        _route(router, "/api/training/datasets", "GET")(_request())
    assert excinfo.value.status_code == 403
    with pytest.raises(HTTPException):
        _route(router, "/api/training/jobs", "GET")(_request())


def test_route_inventory_has_no_harvest_or_implicit_start(routes_env):
    by_path: dict[str, set[str]] = {}
    for route in routes_env.routes:
        path = getattr(route, "path", "")
        by_path.setdefault(path, set()).update(getattr(route, "methods", set()))
        assert "scan" not in path
        assert "harvest" not in path

    assert by_path["/api/training/datasets"] == {"GET", "POST"}
    assert by_path["/api/training/jobs"] == {"GET", "POST"}
    assert by_path["/api/training/jobs/preview"] == {"POST"}


def test_routes_create_review_and_start_require_explicit_payloads(routes_env, data_env):
    router = routes_env
    create = _route(router, "/api/training/datasets", "POST")
    add_item = _route(router, "/api/training/datasets/{dataset_id}/items", "POST")
    review = _route(router, "/api/training/datasets/{dataset_id}/review", "POST")
    preview = _route(router, "/api/training/jobs/preview", "POST")

    created = create(
        _request(),
        training_routes.DatasetCreate(name="Route set", license_summary="operator-owned"),
    )
    assert created["status"] == "draft"

    item = add_item(
        _request(),
        created["id"],
        training_routes.DatasetItemAdd(
            source_kind="manual", content="Route-reviewed text.", consent_license="operator-owned"
        ),
    )
    review_item = _route(
        router, "/api/training/datasets/{dataset_id}/items/{item_id}/review", "POST"
    )
    review_item(
        _request(),
        created["id"],
        item["id"],
        training_routes.DatasetItemReview(decision="approve"),
    )
    reviewed = review(_request(), created["id"])
    assert reviewed["status"] == "reviewed"

    with pytest.raises(HTTPException) as excinfo:
        preview(
            _request(),
            training_routes.JobPreview(
                dataset_id=created["id"],
                model="fixture-model",
                method="qlora",
                output_location="/runs/out",
                dataset_path="/data/dataset.jsonl",
            ),
        )
    assert excinfo.value.status_code == 404