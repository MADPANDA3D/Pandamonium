"""Observable, cancellable training jobs for MAD-797.

Jobs run on the external Unsloth Studio runtime through the MAD-796 adapter.
This module owns the whole governed path: a preview that pins model, method,
dataset fingerprint, output location, resources, and destructive/paid
implications; a start that requires the preview's confirmation fingerprint, an
explicit acknowledgment, a reviewed dataset, and admin authorization; and a
lifecycle (queued | running | checkpointing | completed | failed | canceled)
with progress, metrics, checkpoints, a bounded redacted log tail, cancellation
that stops new work, and resume from an explicitly retained checkpoint.

Discovery can never start a job: start is only reachable here, with a matching
confirmation fingerprint, and the adapter's POST helpers are called solely by
this module.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.database import SessionLocal, TrainingJob
from src import training_datasets as datasets
from src import unsloth_runtime as unsloth
from src.authority_protocol import argument_fingerprint

logger = logging.getLogger(__name__)

JOB_STATES = ("queued", "running", "checkpointing", "completed", "failed", "canceled")
ACTIVE_STATES = ("queued", "running", "checkpointing")
TERMINAL_STATES = ("completed", "failed", "canceled")
TRAINING_METHODS = ("qlora", "lora", "full")
METHOD_VRAM_CLASS = {
    "qlora": "lowest VRAM (4-bit base + adapter)",
    "lora": "medium VRAM (full-precision base + adapter)",
    "full": "highest VRAM (all weights trained)",
}

MAX_MODEL_CHARS = 200
MAX_OUTPUT_CHARS = 512
MAX_DATASET_PATH_CHARS = 1024
MAX_PARAM_KEYS = 40
MAX_PREVIEW_ITEMS = 20
PREVIEW_TTL_SECONDS = 1800
MAX_ERROR_CHARS = 1000

RUNTIME_TO_LIFECYCLE = {
    "running": "running",
    "done": "completed",
    "failed": "failed",
    "stopped": "canceled",
}

# Only these keys reach the runtime. Unknown keys are rejected rather than
# silently forwarded, so a typo cannot become an unintended training flag.
PARAM_BOUNDS: dict[str, tuple[type, tuple[float, float]]] = {
    "max_steps": (int, (0, 1_000_000)),
    "epochs": ((int, float), (0.0, 100.0)),
    "num_train_epochs": ((int, float), (0.0, 100.0)),
    "learning_rate": ((int, float), (1e-7, 1.0)),
    "per_device_train_batch_size": (int, (1, 1024)),
    "gradient_accumulation_steps": (int, (1, 1024)),
    "lora_rank": (int, (4, 256)),
    "r": (int, (4, 256)),
    "lora_alpha": (int, (4, 1024)),
    "lora_dropout": ((int, float), (0.0, 0.9)),
    "max_seq_length": (int, (128, 131_072)),
    "warmup_steps": (int, (0, 100_000)),
    "save_steps": (int, (0, 1_000_000)),
    "eval_steps": (int, (0, 1_000_000)),
    "weight_decay": ((int, float), (0.0, 1.0)),
    "seed": (int, (0, 2_147_483_647)),
    "packing": (bool, ()),
    "train_on_completions": (bool, ()),
}

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_RE = re.compile(r"^[A-Za-z0-9 ._/\\:@+=,~-]+$")


class TrainingJobError(Exception):
    """Fail-closed job error; routes map ``code`` to honest copy."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = str(code or "failed")
        self.message = str(message or "")
        self.status_code = int(status_code)

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


# ── validation ───────────────────────────────────────────────────────────


def _clean_text(value: Any, *, limit: int, field: str, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise TrainingJobError("invalid", f"{field} is required.")
        return None
    if len(text) > limit or _CONTROL_RE.search(text):
        raise TrainingJobError("invalid", f"{field} is too long or contains control characters.")
    return text


def _clean_path(value: Any, *, limit: int, field: str) -> str:
    text = _clean_text(value, limit=limit, field=field, required=True)
    if not _PATH_RE.match(text):
        raise TrainingJobError(
            "invalid_path",
            f"{field} may use letters, numbers, spaces, and . _ / \\ : @ + = , ~ - only.",
        )
    return text


def _clean_params(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise TrainingJobError("invalid_params", "Training parameters must be an object.")
    if len(params) > MAX_PARAM_KEYS:
        raise TrainingJobError("invalid_params", f"Use at most {MAX_PARAM_KEYS} parameters.")
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        name = str(key)
        if name not in PARAM_BOUNDS:
            raise TrainingJobError(
                "invalid_params",
                f"Unsupported training parameter '{name[:60]}'. Remove it and preview again.",
            )
        expected, bounds = PARAM_BOUNDS[name]
        if isinstance(value, bool):
            if expected is not bool:
                raise TrainingJobError("invalid_params", f"'{name}' must be a number.")
            cleaned[name] = value
            continue
        if expected is bool or not isinstance(value, (int, float)):
            raise TrainingJobError("invalid_params", f"'{name}' must be a number.")
        if isinstance(value, int) and expected is int:
            number: float = value
        else:
            number = float(value)
        low, high = bounds
        if number < low or number > high:
            raise TrainingJobError(
                "invalid_params", f"'{name}' must be between {low} and {high}."
            )
        cleaned[name] = int(number) if isinstance(value, int) and expected is int else number
    return cleaned


# ── preview ──────────────────────────────────────────────────────────────


def _runtime_context() -> dict[str, Any]:
    try:
        connection = unsloth.get_connection(None)
    except unsloth.UnslothError as exc:
        raise TrainingJobError(
            "runtime_unconfigured" if exc.code == "unconfigured" else exc.code,
            exc.message,
            status_code=exc.status_code,
        ) from exc
    capabilities = connection.get("capabilities") or {}
    training = capabilities.get("training") if isinstance(capabilities, dict) else {}
    training = training if isinstance(training, dict) else {}
    return {
        "id": connection["id"],
        "enabled": connection["enabled"],
        "base_url": connection["base_url"],
        "status": connection["status"],
        "version": connection["runtime_version"],
        "training_supported": training.get("state") == "supported",
        "training_state": training.get("state") or "unknown",
        "resources": connection.get("resources") or {"state": "unknown"},
    }


def _dataset_summary(dataset: Any, items: list[Any]) -> dict[str, Any]:
    total_chars = sum(len(str(item.content or "")) for item in items)
    return {
        "id": dataset.id,
        "name": dataset.name,
        "fingerprint": dataset.fingerprint,
        "item_count": int(dataset.item_count or 0),
        "total_chars": total_chars,
        "license_summary": dataset.license_summary,
        "items": [
            {
                "id": item.id,
                "source_kind": item.source_kind,
                "owner": item.owner,
                "consent_license": item.consent_license,
                "review_state": item.review_state,
                "content_hash": item.content_hash,
            }
            for item in items[:MAX_PREVIEW_ITEMS]
        ],
    }


def build_implications(
    *,
    dataset: Any,
    items: list[Any],
    output_location: str,
    dataset_path: str,
) -> dict[str, Any]:
    fingerprint = str(dataset.fingerprint or "")
    return {
        "destructive": [
            f"Writes checkpoints and adapters under '{output_location}' on the runtime host; "
            "existing files at that location may be replaced.",
            "Stopping without a retained checkpoint discards partial progress on the runtime host.",
        ],
        "paid": [
            "Compute and any provider billing are set by the runtime operator; "
            "Pandamonium does not meter, cap, or reimburse charges.",
        ],
        "data_egress": [
            f"{len(items)} reviewed item(s) (fingerprint {fingerprint[:12]}...) become "
            f"readable by the runtime at '{dataset_path}'.",
        ],
        "retention": "Only checkpoints you explicitly retain survive a cancellation.",
    }


def build_preview(
    *,
    dataset_id: str,
    model: Any,
    method: Any,
    params: Any = None,
    output_location: Any,
    dataset_path: Any,
) -> dict[str, Any]:
    """Build the exact spec an operator must confirm. Makes no HTTP call."""
    dataset, items = datasets.verified_dataset(dataset_id)
    model_value = _clean_text(model, limit=MAX_MODEL_CHARS, field="Model", required=True)
    method_value = str(method or "").strip().lower()
    if method_value not in TRAINING_METHODS:
        raise TrainingJobError("invalid_method", "Method must be qlora, lora, or full.")
    params_clean = _clean_params(params)
    output_value = _clean_path(output_location, limit=MAX_OUTPUT_CHARS, field="Output location")
    dataset_path_value = _clean_path(dataset_path, limit=MAX_DATASET_PATH_CHARS, field="Dataset path")
    runtime = _runtime_context()

    job_spec = {
        "model": model_value,
        "method": method_value,
        "params": params_clean,
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "fingerprint": dataset.fingerprint,
            "item_count": int(dataset.item_count or 0),
            "path": dataset_path_value,
        },
        "output_dir": output_value,
    }
    fingerprint = argument_fingerprint(
        {"name": "training_start", "target": dataset.id, "arguments": job_spec}
    )
    now = datetime.now(timezone.utc)
    return {
        "contract": unsloth.CONTRACT_ID,
        "dataset": _dataset_summary(dataset, items),
        "model": model_value,
        "method": method_value,
        "method_vram_class": METHOD_VRAM_CLASS[method_value],
        "params": params_clean,
        "output_location": output_value,
        "dataset_path": dataset_path_value,
        "resource_estimate": {
            "dataset_items": int(dataset.item_count or 0),
            "dataset_chars": sum(len(str(item.content or "")) for item in items),
            "runtime_resources": runtime["resources"],
            "note": "The runtime decides schedulability; Pandamonium does not measure GPU memory.",
        },
        "runtime": runtime,
        "implications": build_implications(
            dataset=dataset,
            items=items,
            output_location=output_value,
            dataset_path=dataset_path_value,
        ),
        "job_spec": job_spec,
        "confirm_fingerprint": fingerprint,
        "previewed_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=PREVIEW_TTL_SECONDS)).isoformat(),
    }


# ── storage helpers ──────────────────────────────────────────────────────


def _job_query(db: Any):
    return db.query(TrainingJob).filter(TrainingJob.owner.is_(None))


def _get_job_row(db: Any, job_id: str) -> TrainingJob:
    row = _job_query(db).filter(TrainingJob.id == str(job_id or "")).first()
    if row is None:
        raise TrainingJobError("not_found", "That training job does not exist.", status_code=404)
    return row


def _parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def job_payload(row: TrainingJob) -> dict[str, Any]:
    preview = _parse_json(row.preview, {})
    return {
        "id": row.id,
        "state": row.state,
        "active": row.state in ACTIVE_STATES,
        "dataset_id": row.dataset_id,
        "dataset_fingerprint": row.dataset_fingerprint,
        "model": row.model,
        "method": row.method,
        "params": _parse_json(row.params, {}),
        "output_location": row.output_location,
        "dataset_path": row.dataset_path,
        "runtime_job_id": row.runtime_job_id,
        "progress": _parse_json(row.progress, {}),
        "metrics": _parse_json(row.metrics, {}),
        "checkpoints": _parse_json(row.checkpoints, []),
        "logs": str(row.logs or "")[-unsloth.MAX_LOG_CHARS:],
        "retained_artifacts": _parse_json(row.retained_artifacts, []),
        "cancel_requested_at": row.cancel_requested_at.isoformat() if row.cancel_requested_at else None,
        "cancel_pending": bool(row.cancel_requested_at) and row.state in ACTIVE_STATES,
        "resume_of": row.resume_of,
        "error": row.error,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "preview": {
            "implications": preview.get("implications"),
            "confirm_fingerprint": preview.get("confirm_fingerprint"),
        },
    }


def list_jobs() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = _job_query(db).order_by(TrainingJob.created_at.desc()).limit(100).all()
        return [job_payload(row) for row in rows]
    finally:
        db.close()


def get_job(job_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return job_payload(_get_job_row(db, job_id))
    finally:
        db.close()


def _active_job_id(db: Any) -> str | None:
    row = (
        _job_query(db)
        .filter(TrainingJob.state.in_(ACTIVE_STATES))
        .order_by(TrainingJob.created_at.desc())
        .first()
    )
    return row.id if row else None


# ── start ────────────────────────────────────────────────────────────────


async def start_job(
    *,
    dataset_id: str,
    model: Any,
    method: Any,
    params: Any = None,
    output_location: Any,
    dataset_path: Any,
    confirm_fingerprint: Any,
    acknowledge_implications: Any,
    actor: str | None = None,
    resume_of: str | None = None,
    resume_checkpoint: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Start one training job after every explicit gate passes."""
    if acknowledge_implications is not True:
        raise TrainingJobError(
            "implications_not_acknowledged",
            "Preview the job and acknowledge its destructive/paid implications before starting.",
            status_code=409,
        )
    preview = build_preview(
        dataset_id=dataset_id,
        model=model,
        method=method,
        params=params,
        output_location=output_location,
        dataset_path=dataset_path,
    )
    supplied = str(confirm_fingerprint or "").strip()
    if not supplied or supplied != preview["confirm_fingerprint"]:
        raise TrainingJobError(
            "preview_mismatch",
            "The job spec changed since the preview. Preview again before starting.",
            status_code=409,
        )
    if not preview["runtime"]["training_supported"]:
        raise TrainingJobError(
            "training_unavailable",
            "The runtime does not currently advertise a usable training capability. "
            "Run Test in Settings → Training Runtime and try again.",
            status_code=409,
        )
    db = SessionLocal()
    try:
        active = _active_job_id(db)
        if active:
            raise TrainingJobError(
                "job_active",
                "Another training job is still active. Wait for it to finish or cancel it first.",
                status_code=409,
            )
    finally:
        db.close()

    connection = unsloth.get_connection(None, require_enabled=True)
    spec = dict(preview["job_spec"])
    if resume_checkpoint:
        spec["resume_from_checkpoint"] = str(resume_checkpoint)[:unsloth.MAX_CHECKPOINT_PATH_CHARS]
    result = await unsloth.start_training_job(connection, spec, transport=transport)
    state = RUNTIME_TO_LIFECYCLE.get(str(result.get("state") or ""), "running")

    db = SessionLocal()
    try:
        row = TrainingJob(
            id=uuid.uuid4().hex,
            owner=None,
            dataset_id=preview["dataset"]["id"],
            dataset_fingerprint=preview["dataset"]["fingerprint"],
            state=state,
            model=preview["model"],
            method=preview["method"],
            params=json.dumps(preview["params"], sort_keys=True),
            output_location=preview["output_location"],
            dataset_path=preview["dataset_path"],
            runtime_job_id=result.get("runtime_job_id"),
            preview=json.dumps(preview, sort_keys=True),
            confirm_fingerprint=preview["confirm_fingerprint"],
            progress=json.dumps({}),
            metrics=json.dumps({}),
            checkpoints=json.dumps([]),
            retained_artifacts=json.dumps([]),
            resume_of=resume_of or None,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(
            "training job started id=%s state=%s dataset=%s actor=%s",
            row.id,
            state,
            row.dataset_id,
            actor or "",
        )
        return job_payload(row)
    finally:
        db.close()


async def resume_job(
    *,
    job_id: str,
    checkpoint_id: Any = None,
    confirm_fingerprint: Any,
    acknowledge_implications: Any,
    actor: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Resume a canceled/failed job from an explicitly retained checkpoint."""
    db = SessionLocal()
    try:
        source = _get_job_row(db, job_id)
        if source.state not in ("canceled", "failed"):
            raise TrainingJobError(
                "not_resumable",
                "Only a canceled or failed job can be resumed.",
                status_code=409,
            )
        retained = _parse_json(source.retained_artifacts, [])
        checkpoints = _parse_json(source.checkpoints, [])
        payload = job_payload(source)
    finally:
        db.close()
    if source.state == "canceled":
        # Cancellation preserves only the explicitly retained artifacts; a
        # canceled job's stale checkpoint list is not resumable.
        candidates = retained
    else:
        candidates = checkpoints or retained
    if not candidates:
        raise TrainingJobError(
            "no_checkpoint",
            "This job kept no checkpoint. Cancellation preserved only explicitly retained "
            "artifacts, so there is nothing to resume from.",
            status_code=409,
        )
    wanted = str(checkpoint_id or "").strip()
    selected = None
    if wanted:
        selected = next(
            (
                entry
                for entry in candidates
                if str(entry.get("id") or entry.get("path") or "") == wanted
            ),
            None,
        )
        if selected is None:
            raise TrainingJobError(
                "checkpoint_not_found",
                "That checkpoint is not among this job's retained artifacts.",
                status_code=404,
            )
    else:
        selected = candidates[0]
    checkpoint_ref = str(selected.get("path") or selected.get("id") or "")
    return await start_job(
        dataset_id=payload["dataset_id"],
        model=payload["model"],
        method=payload["method"],
        params=payload["params"],
        output_location=payload["output_location"],
        dataset_path=payload["dataset_path"],
        confirm_fingerprint=confirm_fingerprint,
        acknowledge_implications=acknowledge_implications,
        actor=actor,
        resume_of=job_id,
        resume_checkpoint=checkpoint_ref,
        transport=transport,
    )


# ── observe / refresh ────────────────────────────────────────────────────


def _merge_metrics(previous: Any, update: Any) -> dict[str, Any]:
    merged = dict(previous) if isinstance(previous, dict) else {}
    if isinstance(update, dict):
        for key, value in list(update.items())[:unsloth.MAX_METRICS_FIELDS]:
            merged[str(key)[:60]] = value
    return dict(list(merged.items())[:unsloth.MAX_METRICS_FIELDS])


def _lifecycle_from_snapshot(snapshot: dict[str, Any], current: str) -> str:
    raw = str(snapshot.get("raw_state") or "").lower()
    if "checkpoint" in raw:
        return "checkpointing"
    mapped = RUNTIME_TO_LIFECYCLE.get(str(snapshot.get("state") or ""))
    if mapped:
        return mapped
    return current if current in JOB_STATES else "running"


async def refresh_job(job_id: str, *, transport: Any = None) -> dict[str, Any]:
    """Read the runtime and update one job's observable state."""
    db = SessionLocal()
    try:
        row = _get_job_row(db, job_id)
        runtime_job_id = row.runtime_job_id
        previous_logs = str(row.logs or "")
        current_state = row.state
        previous_metrics = _parse_json(row.metrics, {})
        previous_checkpoints = _parse_json(row.checkpoints, [])
        cancel_requested = bool(row.cancel_requested_at)
    finally:
        db.close()

    connection = unsloth.get_connection(None)
    if not connection["enabled"]:
        raise TrainingJobError(
            "runtime_disabled",
            "The training runtime is disabled, so job state cannot be refreshed.",
            status_code=409,
        )
    snapshot = await unsloth.fetch_training_status(
        connection, transport=transport, previous_logs=previous_logs
    )
    metrics_route = await unsloth.fetch_training_metrics(connection, transport=transport)
    state = _lifecycle_from_snapshot(snapshot, current_state)
    if cancel_requested and state == "running":
        state = current_state if current_state in ("checkpointing", "running") else "running"
    finished = state in TERMINAL_STATES
    checkpoints = snapshot.get("checkpoints") or previous_checkpoints
    retained_artifacts = []
    if state == "canceled":
        retained_artifacts = [
            entry for entry in checkpoints if isinstance(entry, dict)
        ]

    db = SessionLocal()
    try:
        row = _get_job_row(db, job_id)
        row.state = state
        row.runtime_job_id = runtime_job_id or row.runtime_job_id
        row.progress = json.dumps(snapshot.get("progress") or {}, sort_keys=True)
        row.metrics = json.dumps(
            _merge_metrics(previous_metrics, {**snapshot.get("metrics", {}), **metrics_route}),
            sort_keys=True,
        )
        row.checkpoints = json.dumps(checkpoints, sort_keys=True)
        row.logs = str(snapshot.get("logs") or previous_logs)[-unsloth.MAX_LOG_CHARS:]
        if state == "canceled":
            row.retained_artifacts = json.dumps(
                retained_artifacts if cancel_requested else [], sort_keys=True
            )
        if finished:
            row.finished_at = row.finished_at or datetime.now(timezone.utc).replace(tzinfo=None)
            if state == "failed":
                row.error = row.error or "The runtime reported the job failed."
        db.commit()
        db.refresh(row)
        return job_payload(row)
    finally:
        db.close()


async def reconnect_jobs(*, transport: Any = None) -> dict[str, Any]:
    """Re-read every active job's runtime state (reconnect after a restart)."""
    db = SessionLocal()
    try:
        ids = [
            row.id
            for row in _job_query(db).filter(TrainingJob.state.in_(ACTIVE_STATES)).all()
        ]
    finally:
        db.close()
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for job_id in ids:
        try:
            refreshed.append(await refresh_job(job_id, transport=transport))
        except (TrainingJobError, unsloth.UnslothError) as exc:
            code = getattr(exc, "code", "failed")
            message = getattr(exc, "message", str(exc))
            errors.append({"id": job_id, "code": code, "message": message})
    return {"refreshed": refreshed, "errors": errors, "active_seen": len(ids)}


# ── cancel ───────────────────────────────────────────────────────────────


async def cancel_job(
    job_id: str,
    *,
    retain_checkpoint: bool = False,
    actor: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    """Stop new work and keep only explicitly retained artifacts."""
    db = SessionLocal()
    try:
        row = _get_job_row(db, job_id)
        if row.state in TERMINAL_STATES:
            payload = job_payload(row)
            payload["already_final"] = True
            return payload
        previous_checkpoints = _parse_json(row.checkpoints, [])
    finally:
        db.close()

    connection = unsloth.get_connection(None, require_enabled=True)
    try:
        result = await unsloth.stop_training_job(
            connection, save=bool(retain_checkpoint), transport=transport
        )
    except unsloth.UnslothError as exc:
        if exc.code == "no_active_job":
            return await refresh_job(job_id, transport=transport)
        raise
    stop_state = RUNTIME_TO_LIFECYCLE.get(str(result.get("state") or ""), "canceled")
    if stop_state == "completed":
        state = "completed"
    elif stop_state == "canceled":
        state = "canceled"
    else:
        state = "checkpointing"

    db = SessionLocal()
    try:
        row = _get_job_row(db, job_id)
        row.cancel_requested_at = row.cancel_requested_at or datetime.now(timezone.utc).replace(tzinfo=None)
        row.state = state
        if state in TERMINAL_STATES:
            row.finished_at = row.finished_at or datetime.now(timezone.utc).replace(tzinfo=None)
            row.retained_artifacts = json.dumps(
                previous_checkpoints if retain_checkpoint else [], sort_keys=True
            )
        db.commit()
        db.refresh(row)
        logger.info(
            "training job cancel id=%s state=%s retain=%s actor=%s",
            row.id,
            state,
            bool(retain_checkpoint),
            actor or "",
        )
        return job_payload(row)
    finally:
        db.close()