"""Admin training routes (MAD-797): reviewed datasets and observable jobs.

Datasets are installation-level and admin-gated; every item is added one at a
time with explicit provenance. The start route requires the preview's
confirmation fingerprint plus an explicit acknowledgment of the destructive/
paid implications, so a health/discovery call can never begin a job.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.middleware import require_admin
from src import training_datasets as datasets
from src import training_jobs as jobs
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    license_summary: str | None = Field(default=None, max_length=500)


class DatasetItemAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: str = Field(max_length=32)
    source_ref: dict[str, Any] | None = None
    content: str | None = Field(default=None, max_length=64_000)
    consent_license: str = Field(min_length=1, max_length=500)


class DatasetItemReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(approve|reject|exclude)$")
    reason: str | None = Field(default=None, max_length=500)


class JobPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(max_length=64)
    model: str = Field(min_length=1, max_length=200)
    method: str = Field(pattern=r"^(qlora|lora|full)$")
    params: dict[str, Any] | None = None
    output_location: str = Field(min_length=1, max_length=512)
    dataset_path: str = Field(min_length=1, max_length=1024)


class JobStart(JobPreview):
    confirm_fingerprint: str = Field(min_length=8, max_length=128)
    acknowledge_implications: bool


class JobCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retain_checkpoint: bool = False


class JobResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str | None = Field(default=None, max_length=512)
    confirm_fingerprint: str = Field(min_length=8, max_length=128)
    acknowledge_implications: bool


def _actor(request: Request) -> str:
    return str(get_current_user(request) or "")


def _raise_training(exc: Any) -> None:
    raise HTTPException(exc.status_code, exc.public())


def setup_training_routes() -> APIRouter:
    router = APIRouter(prefix="/api/training")

    # ── datasets ─────────────────────────────────────────────────────────

    @router.get("/datasets")
    def list_datasets(request: Request):
        require_admin(request)
        return {"datasets": datasets.list_datasets()}

    @router.post("/datasets")
    def create_dataset(request: Request, change: DatasetCreate):
        require_admin(request)
        return datasets.create_dataset(
            change.name,
            description=change.description,
            license_summary=change.license_summary,
            actor=_actor(request),
        )

    @router.get("/datasets/{dataset_id}")
    def get_dataset(request: Request, dataset_id: str):
        require_admin(request)
        try:
            return datasets.get_dataset(dataset_id)
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.post("/datasets/{dataset_id}/items")
    def add_dataset_item(request: Request, dataset_id: str, change: DatasetItemAdd):
        require_admin(request)
        try:
            return datasets.add_item(
                dataset_id,
                source_kind=change.source_kind,
                source_ref=change.source_ref,
                content=change.content,
                consent_license=change.consent_license,
                actor=_actor(request),
            )
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.post("/datasets/{dataset_id}/items/{item_id}/review")
    def review_dataset_item(
        request: Request, dataset_id: str, item_id: str, change: DatasetItemReview
    ):
        require_admin(request)
        try:
            return datasets.review_item(
                dataset_id,
                item_id,
                decision=change.decision,
                reason=change.reason,
                actor=_actor(request),
            )
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.delete("/datasets/{dataset_id}/items/{item_id}")
    def remove_dataset_item(request: Request, dataset_id: str, item_id: str):
        require_admin(request)
        try:
            return datasets.remove_item(dataset_id, item_id)
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.post("/datasets/{dataset_id}/review")
    def review_dataset(request: Request, dataset_id: str):
        require_admin(request)
        try:
            return datasets.review_dataset(dataset_id, actor=_actor(request))
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.post("/datasets/{dataset_id}/retire")
    def retire_dataset(request: Request, dataset_id: str):
        require_admin(request)
        try:
            return datasets.retire_dataset(dataset_id, actor=_actor(request))
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    @router.post("/datasets/{dataset_id}/export")
    def export_dataset(request: Request, dataset_id: str):
        require_admin(request)
        try:
            return datasets.export_dataset_jsonl(dataset_id)
        except datasets.TrainingDataError as exc:
            _raise_training(exc)

    # ── jobs ─────────────────────────────────────────────────────────────

    @router.post("/jobs/preview")
    def preview_job(request: Request, change: JobPreview):
        require_admin(request)
        try:
            return jobs.build_preview(
                dataset_id=change.dataset_id,
                model=change.model,
                method=change.method,
                params=change.params,
                output_location=change.output_location,
                dataset_path=change.dataset_path,
            )
        except (jobs.TrainingJobError, datasets.TrainingDataError) as exc:
            _raise_training(exc)

    @router.post("/jobs")
    async def start_job(request: Request, change: JobStart):
        require_admin(request)
        try:
            return await jobs.start_job(
                dataset_id=change.dataset_id,
                model=change.model,
                method=change.method,
                params=change.params,
                output_location=change.output_location,
                dataset_path=change.dataset_path,
                confirm_fingerprint=change.confirm_fingerprint,
                acknowledge_implications=change.acknowledge_implications,
                actor=_actor(request),
            )
        except (jobs.TrainingJobError, datasets.TrainingDataError) as exc:
            _raise_training(exc)

    @router.get("/jobs")
    def list_jobs(request: Request):
        require_admin(request)
        return {"jobs": jobs.list_jobs()}

    @router.get("/jobs/{job_id}")
    def get_job(request: Request, job_id: str):
        require_admin(request)
        try:
            return jobs.get_job(job_id)
        except jobs.TrainingJobError as exc:
            _raise_training(exc)

    @router.post("/jobs/{job_id}/refresh")
    async def refresh_job(request: Request, job_id: str):
        require_admin(request)
        try:
            return await jobs.refresh_job(job_id)
        except (jobs.TrainingJobError, datasets.TrainingDataError) as exc:
            _raise_training(exc)

    @router.post("/jobs/reconnect")
    async def reconnect_jobs(request: Request):
        require_admin(request)
        return await jobs.reconnect_jobs()

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(request: Request, job_id: str, change: JobCancel):
        require_admin(request)
        try:
            return await jobs.cancel_job(
                job_id,
                retain_checkpoint=change.retain_checkpoint,
                actor=_actor(request),
            )
        except (jobs.TrainingJobError, datasets.TrainingDataError) as exc:
            _raise_training(exc)

    @router.post("/jobs/{job_id}/resume")
    async def resume_job(request: Request, job_id: str, change: JobResume):
        require_admin(request)
        try:
            return await jobs.resume_job(
                job_id=job_id,
                checkpoint_id=change.checkpoint_id,
                confirm_fingerprint=change.confirm_fingerprint,
                acknowledge_implications=change.acknowledge_implications,
                actor=_actor(request),
            )
        except (jobs.TrainingJobError, datasets.TrainingDataError) as exc:
            _raise_training(exc)

    return router