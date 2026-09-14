"""MAD-797: observable, cancellable training jobs.

Fixture jobs run through an ``httpx.MockTransport`` that speaks the ADR-0001
adapter contract: success, runtime rejection, resource failure, cancellation
(with and without retained checkpoints), reconnect after a restart, and
resume. Discovery is asserted to stay GET-only even after job control exists.

No real GPU job is submitted anywhere in this file.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "src.training_datasets",
    "src.training_jobs",
    "routes.training_routes",
    "core.database",
):
    import core.database as database
    import routes.training_routes as training_routes
    import src.training_datasets as datasets
    import src.training_jobs as jobs
    import src.unsloth_runtime as unsloth


BASE_URL = "https://studio.example.test"
TOKEN = "studio-token-fixture"
DATASET_PATH = "/mnt/train/dataset.jsonl"
OUTPUT_LOCATION = "/runs/fixture-out"

DEFAULT_STATUS = {
    "status": "training",
    "state": "training",
    "progress": 0.42,
    "step": 42,
    "total_steps": 100,
    "epoch": 1.2,
    "loss": 0.5,
    "checkpoints": [{"id": "ckpt-1", "step": 40, "path": "/runs/fixture-out/ckpt-1"}],
    "logs": "step 42 loss 0.5",
    "metrics": {"loss": 0.5, "grad_norm": 1.2},
}


class RuntimeStub:
    """Mock Studio runtime for job control. Records every request."""

    def __init__(
        self,
        *,
        start_status: int = 200,
        start_body: dict | None = None,
        status_status: int = 200,
        status_body: dict | None = None,
        stop_status: int = 200,
        stop_body: dict | None = None,
        metrics_body: dict | None = None,
        fail_status: int | None = None,
    ) -> None:
        self.start_status = start_status
        self.start_body = start_body if start_body is not None else {"job_id": "rt-1", "state": "running"}
        self.status_status = status_status
        self.status_body = status_body if status_body is not None else DEFAULT_STATUS
        self.stop_status = stop_status
        self.stop_body = stop_body if stop_body is not None else {"state": "stopped"}
        self.metrics_body = metrics_body if metrics_body is not None else {"metrics": {"loss": 0.25}}
        self.fail_status = fail_status
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8") if request.content else ""
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "authorization": request.headers.get("authorization", ""),
                "body": body,
            }
        )
        if request.method == "GET":
            if request.url.path == "/api/train/status":
                return httpx.Response(self.status_status, json=self.status_body, request=request)
            if request.url.path == "/api/train/metrics":
                return httpx.Response(200, json=self.metrics_body, request=request)
            if request.url.path == "/api/health":
                return httpx.Response(200, json={"status": "ok", "version": "1.0.0"}, request=request)
            if request.url.path == "/api/system":
                return httpx.Response(200, json={"gpus": [{"name": "GPU"}]}, request=request)
            if request.url.path == "/openapi.json":
                return httpx.Response(
                    200,
                    json={
                        "paths": {
                            "/api/train/start": {"post": {}},
                            "/api/train/status": {"get": {}},
                            "/api/inference/chat": {"post": {}},
                        }
                    },
                    request=request,
                )
            if request.url.path == "/api/models/":
                return httpx.Response(200, json={"models": [{"id": "fixture-model"}]}, request=request)
            return httpx.Response(404, json={"detail": "not found"}, request=request)
        if request.url.path == "/api/train/start":
            if self.fail_status is not None:
                return httpx.Response(self.fail_status, json={"detail": "fixture failure"}, request=request)
            return httpx.Response(self.start_status, json=self.start_body, request=request)
        if request.url.path == "/api/train/stop":
            return httpx.Response(self.stop_status, json=self.stop_body, request=request)
        return httpx.Response(404, json={"detail": "not found"}, request=request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def methods(self) -> set[str]:
        return {entry["method"] for entry in self.requests}

    def post_bodies(self, path: str) -> list[dict]:
        return [
            json.loads(entry["body"])
            for entry in self.requests
            if entry["method"] == "POST" and entry["path"] == path
        ]


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    SessionLocal, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(database, "SessionLocal", SessionLocal)
    for module in (datasets, jobs, unsloth, training_routes):
        monkeypatch.setattr(module, "SessionLocal", SessionLocal, raising=False)
    # Pin the exact module objects the code under test must share. Another
    # test file's import-state preservation can leave a second module object
    # in sys.modules, which would otherwise split the fixtures' patched
    # SessionLocal from the one the service modules hold.
    monkeypatch.setattr(jobs, "datasets", datasets, raising=False)
    monkeypatch.setattr(jobs, "unsloth", unsloth, raising=False)
    monkeypatch.setattr(training_routes, "datasets", datasets, raising=False)
    monkeypatch.setattr(training_routes, "jobs", jobs, raising=False)
    monkeypatch.setattr(datasets, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(datasets, "PERSONAL_UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv(datasets.FILE_ROOTS_ENV, raising=False)
    monkeypatch.setattr(unsloth, "check_outbound_url", lambda value, block_private=False: (True, ""))

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


def _configure_runtime(env, *, training_state: str = "supported") -> None:
    unsloth.save_connection(None, base_url=BASE_URL, token=TOKEN)
    with env.SessionLocal() as session:
        row = (
            session.query(database.Integration)
            .filter_by(type=unsloth.INTEGRATION_TYPE)
            .one()
        )
        config = dict(row.config or {})
        config.update(
            {
                "status": "online",
                "runtime_version": "1.0.0",
                "capabilities": {
                    "training": {"state": training_state, "detail": "fixture"},
                    "conversion": {"state": "unsupported", "detail": "fixture"},
                    "inference": {"state": "supported", "detail": "fixture"},
                },
                "resources": {"state": "available", "gpus": 1, "detail": "fixture gpu"},
                "job_state": "idle",
            }
        )
        row.config = config
        session.commit()


def _reviewed_dataset(*texts: str) -> dict:
    dataset = datasets.create_dataset("Job fixture set", license_summary="operator-owned")
    for text in texts:
        item = datasets.add_item(
            dataset["id"],
            source_kind="manual",
            content=text,
            consent_license="operator-owned",
            actor="admin",
        )
        datasets.review_item(dataset["id"], item["id"], decision="approve", actor="admin")
    return datasets.review_dataset(dataset["id"], actor="admin")


def _preview(dataset: dict, **overrides) -> dict:
    values = {
        "dataset_id": dataset["id"],
        "model": "unsloth/Qwen3-0.6B",
        "method": "qlora",
        "params": {"epochs": 1, "learning_rate": 0.0002, "lora_rank": 16},
        "output_location": OUTPUT_LOCATION,
        "dataset_path": DATASET_PATH,
    }
    values.update(overrides)
    return jobs.build_preview(**values)


def _args_from_preview(preview: dict, **overrides) -> dict:
    values = {
        "dataset_id": preview["dataset"]["id"],
        "model": preview["model"],
        "method": preview["method"],
        "params": preview["params"],
        "output_location": preview["output_location"],
        "dataset_path": preview["dataset_path"],
        "confirm_fingerprint": preview["confirm_fingerprint"],
        "acknowledge_implications": True,
        "actor": "admin",
    }
    values.update(overrides)
    return values


def _start(reviewed: dict, stub: RuntimeStub, **overrides):
    preview = _preview(reviewed)
    return asyncio.run(
        jobs.start_job(**_args_from_preview(preview, transport=stub.transport(), **overrides))
    )


# ── preview ──────────────────────────────────────────────────────────────


def test_preview_shows_model_method_fingerprint_output_resources_and_implications(job_env):
    _configure_runtime(job_env)
    dataset = _reviewed_dataset("First reviewed fixture line.", "Second line.")

    preview = _preview(dataset)

    assert preview["model"] == "unsloth/Qwen3-0.6B"
    assert preview["method"] == "qlora"
    assert preview["dataset"]["fingerprint"] == dataset["fingerprint"]
    assert preview["dataset"]["item_count"] == 2
    assert preview["output_location"] == OUTPUT_LOCATION
    assert preview["dataset_path"] == DATASET_PATH
    assert preview["resource_estimate"]["runtime_resources"]["state"] == "available"
    assert preview["resource_estimate"]["dataset_items"] == 2
    assert preview["implications"]["destructive"]
    assert preview["implications"]["paid"]
    assert dataset["fingerprint"][:12] in preview["implications"]["data_egress"][0]
    assert len(preview["confirm_fingerprint"]) == 64
    assert preview["runtime"]["training_supported"] is True


def test_preview_rejects_unreviewed_dataset_and_unknown_params(job_env):
    _configure_runtime(job_env)
    draft = datasets.create_dataset("Draft set", license_summary="operator-owned")
    with pytest.raises(datasets.TrainingDataError) as excinfo:
        _preview(draft)
    assert excinfo.value.code == "not_reviewed"

    reviewed = _reviewed_dataset("Reviewed line.")
    with pytest.raises(jobs.TrainingJobError) as excinfo:
        _preview(reviewed, params={"mystery_flag": 1})
    assert excinfo.value.code == "invalid_params"


def test_preview_rejects_out_of_bounds_params(job_env):
    _configured = _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    assert _configured is None
    with pytest.raises(jobs.TrainingJobError) as excinfo:
        _preview(reviewed, params={"learning_rate": 9000})
    assert excinfo.value.code == "invalid_params"


# ── start gates ──────────────────────────────────────────────────────────


def test_start_requires_acknowledgment_and_matching_fingerprint(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    preview = _preview(reviewed)
    stub = RuntimeStub()

    with pytest.raises(jobs.TrainingJobError) as excinfo:
        asyncio.run(
            jobs.start_job(
                **_args_from_preview(
                    preview, acknowledge_implications=False, transport=stub.transport()
                )
            )
        )
    assert excinfo.value.code == "implications_not_acknowledged"

    with pytest.raises(jobs.TrainingJobError) as excinfo:
        asyncio.run(
            jobs.start_job(
                **_args_from_preview(
                    preview, confirm_fingerprint="0" * 64, transport=stub.transport()
                )
            )
        )
    assert excinfo.value.code == "preview_mismatch"

    assert stub.requests == []
    assert jobs.list_jobs() == []


def test_start_refuses_when_runtime_lacks_training_capability(job_env):
    _configure_runtime(job_env, training_state="unavailable")
    reviewed = _reviewed_dataset("Reviewed line.")
    preview = _preview(reviewed)
    stub = RuntimeStub()

    with pytest.raises(jobs.TrainingJobError) as excinfo:
        asyncio.run(jobs.start_job(**_args_from_preview(preview, transport=stub.transport())))
    assert excinfo.value.code == "training_unavailable"
    assert stub.requests == []


# ── success / rejection / resource failure ───────────────────────────────


def test_start_success_posts_the_reviewed_spec_and_records_the_job(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line one.", "Reviewed line two.")
    stub = RuntimeStub()

    payload = _start(reviewed, stub)

    assert payload["state"] == "running"
    assert payload["runtime_job_id"] == "rt-1"
    assert payload["dataset_fingerprint"] == reviewed["fingerprint"]
    assert payload["preview"]["confirm_fingerprint"]

    starts = stub.post_bodies("/api/train/start")
    assert len(starts) == 1
    spec = starts[0]
    assert spec["model"] == "unsloth/Qwen3-0.6B"
    assert spec["method"] == "qlora"
    assert spec["dataset"]["fingerprint"] == reviewed["fingerprint"]
    assert spec["output_dir"] == OUTPUT_LOCATION
    assert "resume_from_checkpoint" not in spec
    assert stub.methods <= {"GET", "POST"}
    assert all(
        entry["authorization"] == f"Bearer {TOKEN}"
        for entry in stub.requests
        if entry["method"] == "POST"
    )

    stored = jobs.get_job(payload["id"])
    assert stored["state"] == "running"
    assert stored["dataset_id"] == reviewed["id"]


def test_start_rejected_by_runtime_maps_error_and_stores_no_job(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub(fail_status=422)

    with pytest.raises(unsloth.UnslothError) as excinfo:
        _start(reviewed, stub)
    assert excinfo.value.code == "error"
    assert jobs.list_jobs() == []


def test_resource_failure_from_runtime_is_explicit(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub(fail_status=507)

    with pytest.raises(unsloth.UnslothError) as excinfo:
        _start(reviewed, stub)
    assert excinfo.value.code == "insufficient_resources"
    assert jobs.list_jobs() == []


def test_second_start_is_blocked_while_a_job_is_active(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    _start(reviewed, stub)
    preview = _preview(reviewed)

    with pytest.raises(jobs.TrainingJobError) as excinfo:
        asyncio.run(jobs.start_job(**_args_from_preview(preview, transport=stub.transport())))
    assert excinfo.value.code == "job_active"


# ── observability ────────────────────────────────────────────────────────


def test_refresh_observes_progress_metrics_checkpoints_and_logs(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)

    refreshed = asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))

    assert refreshed["state"] == "running"
    assert refreshed["progress"]["percent"] == 42.0
    assert refreshed["progress"]["step"] == 42
    assert refreshed["progress"]["epoch"] == 1.2
    assert refreshed["metrics"]["loss"] == 0.25
    assert refreshed["checkpoints"][0]["id"] == "ckpt-1"
    assert "loss 0.5" in refreshed["logs"]


def test_refresh_maps_checkpointing_state(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub(
        status_body={"status": "checkpointing", "progress": 0.7, "checkpoints": []}
    )
    payload = _start(reviewed, stub)

    refreshed = asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))
    assert refreshed["state"] == "checkpointing"


# ── cancellation ─────────────────────────────────────────────────────────


def test_cancel_stops_without_retention_and_clears_artifacts(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)
    asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))

    canceled = asyncio.run(
        jobs.cancel_job(payload["id"], retain_checkpoint=False, actor="admin", transport=stub.transport())
    )

    assert canceled["state"] == "canceled"
    assert canceled["retained_artifacts"] == []
    stops = stub.post_bodies("/api/train/stop")
    assert stops == [{"save": False}]


def test_cancel_with_retention_preserves_only_checkpoints(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)
    asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))

    canceled = asyncio.run(
        jobs.cancel_job(payload["id"], retain_checkpoint=True, actor="admin", transport=stub.transport())
    )

    assert canceled["state"] == "canceled"
    assert [entry["id"] for entry in canceled["retained_artifacts"]] == ["ckpt-1"]
    assert stub.post_bodies("/api/train/stop") == [{"save": True}]


def test_cancel_is_idempotent_when_job_already_final(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)
    asyncio.run(jobs.cancel_job(payload["id"], actor="admin", transport=stub.transport()))

    again = asyncio.run(jobs.cancel_job(payload["id"], actor="admin", transport=stub.transport()))
    assert again["already_final"] is True
    assert len(stub.post_bodies("/api/train/stop")) == 1


# ── reconnect and resume ─────────────────────────────────────────────────


def test_reconnect_reads_runtime_state_after_restart(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)

    completed = RuntimeStub(status_body={"status": "completed", "progress": 1.0, "checkpoints": []})
    result = asyncio.run(jobs.reconnect_jobs(transport=completed.transport()))

    assert result["active_seen"] == 1
    assert result["errors"] == []
    assert result["refreshed"][0]["id"] == payload["id"]
    assert result["refreshed"][0]["state"] == "completed"
    assert result["refreshed"][0]["finished_at"]


def test_resume_requires_a_retained_checkpoint_and_starts_a_new_job(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)
    asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))
    asyncio.run(
        jobs.cancel_job(payload["id"], retain_checkpoint=True, actor="admin", transport=stub.transport())
    )

    preview = _preview(reviewed)
    resumed = asyncio.run(
        jobs.resume_job(
            job_id=payload["id"],
            confirm_fingerprint=preview["confirm_fingerprint"],
            acknowledge_implications=True,
            actor="admin",
            transport=stub.transport(),
        )
    )

    assert resumed["resume_of"] == payload["id"]
    starts = stub.post_bodies("/api/train/start")
    assert starts[-1]["resume_from_checkpoint"] == "/runs/fixture-out/ckpt-1"


def test_resume_without_retention_has_nothing_to_resume(job_env):
    _configure_runtime(job_env)
    reviewed = _reviewed_dataset("Reviewed line.")
    stub = RuntimeStub()
    payload = _start(reviewed, stub)
    asyncio.run(jobs.refresh_job(payload["id"], transport=stub.transport()))
    asyncio.run(
        jobs.cancel_job(payload["id"], retain_checkpoint=False, actor="admin", transport=stub.transport())
    )
    preview = _preview(reviewed)

    with pytest.raises(jobs.TrainingJobError) as excinfo:
        asyncio.run(
            jobs.resume_job(
                job_id=payload["id"],
                confirm_fingerprint=preview["confirm_fingerprint"],
                acknowledge_implications=True,
                actor="admin",
                transport=stub.transport(),
            )
        )
    assert excinfo.value.code == "no_checkpoint"


# ── no-accidental-training regression ────────────────────────────────────


def test_connection_test_remains_get_only_with_job_control_present(job_env):
    _configure_runtime(job_env)
    stub = RuntimeStub(status_body={"status": "idle", "progress": 0})

    result = asyncio.run(unsloth.test_connection(None, transport=stub.transport()))

    assert result["state"] == "online"
    assert stub.methods == {"GET"}
    assert stub.post_bodies("/api/train/start") == []


# ── routes ───────────────────────────────────────────────────────────────


def test_training_job_routes_are_admin_gated_and_explicit(job_env, monkeypatch):
    def _deny(request):
        raise HTTPException(403, "Admin access required")

    monkeypatch.setattr(training_routes, "require_admin", _deny)
    router = training_routes.setup_training_routes()
    request = SimpleNamespace(
        state=SimpleNamespace(current_user="admin", api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )

    def _route(path: str, method: str):
        for route in router.routes:
            if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
                return route.endpoint
        raise AssertionError(f"{method} {path} route not found")

    with pytest.raises(HTTPException) as excinfo:
        _route("/api/training/jobs", "GET")(request)
    assert excinfo.value.status_code == 403

    # The start route requires the confirmation fingerprint and the explicit
    # acknowledgment at the schema level.
    with pytest.raises(Exception):
        training_routes.JobStart(
            dataset_id="d",
            model="m",
            method="qlora",
            output_location="/out",
            dataset_path="/data",
        )