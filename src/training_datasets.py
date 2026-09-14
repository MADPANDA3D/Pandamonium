"""Reviewed dataset manifests for MAD-797 training jobs.

A dataset is a manifest, never a harvester. It starts empty and only gains
items through an explicit :func:`add_item` call that names one source:
``manual`` (operator-pasted text), ``file`` (a path under an operator-configured
allowlist), ``book`` (one book from the owner's Books catalog), ``conversation``
(explicit session/message ids), ``memory`` (explicit memory ids), or
``tool_trace`` (one scheduled-task run). Nothing in this module scans, walks, or
auto-selects, so existing memory, conversations, books, files, and tool traces
can never silently become training data.

Every item records source, source owner, consent/license attestation, review
state, a SHA-256 over the exact stored snapshot, and its exclusion/deletion
path. Content is screened at add time: credential material is rejected and the
item stores no content, PII/secret-shaped text is redacted and recorded in the
item's redaction report. The dataset fingerprint is computed only from
approved items, so a job preview/start pins exactly what was reviewed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import (
    ChatMessage,
    Memory,
    ScheduledTask,
    SessionLocal,
    Session as DbSession,
    TaskRun,
    TrainingDataset,
    TrainingDatasetItem,
)
from core.platform_compat import safe_chmod
from src.authority_protocol import redact_secret_text
from src.constants import DATA_DIR, PERSONAL_UPLOADS_DIR
from src.upload_handler import secure_filename

logger = logging.getLogger(__name__)

SOURCE_KINDS = ("manual", "file", "book", "conversation", "memory", "tool_trace")
REVIEW_STATES = ("pending", "approved", "rejected", "redacted", "excluded")
INCLUDED_REVIEW_STATES = ("approved", "redacted")
DATASET_STATUSES = ("draft", "reviewed", "retired")

MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 2000
MAX_LICENSE_CHARS = 500
MAX_ITEM_CHARS = 64_000
MAX_SOURCE_REF_CHARS = 2048
MAX_DATASET_ITEMS = 1000
MAX_MESSAGES_PER_ITEM = 200
MAX_MEMORIES_PER_ITEM = 200
MAX_FILE_BYTES = 64_000
MAX_BOOK_CHARS = 64_000
MAX_TRACE_CHARS = 32_000
FILE_ROOTS_ENV = "ODYSSEUS_TRAINING_FILE_ROOTS"
TEXT_FILE_SUFFIXES = frozenset(
    {".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv", ".rst", ".org"}
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Reject outright: the item stores no content, only the refusal reason. A
# conservative list — it is a floor, not a claim of completeness.
_REJECT_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"), "Slack token"),
    (
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"]?[^\s'\",;}]{4,}"
        ),
        "credential assignment",
    ),
    (re.compile(r"(?i)\bssn\b\s*[:=]?\s*\d{3}-\d{2}-\d{4}"), "government ID number"),
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"), "payment card number"),
)
# Redacted before storage. Emails and phone numbers are normal conversation
# content, so they are removed rather than disqualifying the item.
_PII_REDACT_PATTERNS = (
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[email]",
        "email_addresses",
    ),
    (
        re.compile(
            r"(?:\+\d{1,3}[ .-]?)?\(\d{3}\)[ .-]?\d{3}[ .-]?\d{4}(?!\d)"
        ),
        "[phone]",
        "phone_numbers",
    ),
)

EXCLUSION_COPY = (
    "Remove this item from the dataset with DELETE "
    "/api/training/datasets/{dataset_id}/items/{item_id}; the source {source_kind} "
    "is never modified."
)


class TrainingDataError(Exception):
    """Fail-closed dataset error; routes map ``code`` to honest copy."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = str(code or "failed")
        self.message = str(message or "")
        self.status_code = int(status_code)

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


# ── validation helpers ───────────────────────────────────────────────────


def _clean_text(value: Any, *, limit: int, field: str, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise TrainingDataError("invalid", f"{field} is required.")
        return None
    if len(text) > limit or _CONTROL_RE.search(text):
        raise TrainingDataError("invalid", f"{field} is too long or contains control characters.")
    return text


def _owner_query(db: Any, owner: str | None):
    return db.query(TrainingDataset).filter(
        TrainingDataset.owner.is_(None) if owner is None else TrainingDataset.owner == owner
    )


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


# ── screening ────────────────────────────────────────────────────────────


def screen_content(text: Any) -> tuple[str | None, dict[str, Any]]:
    """Return ``(screened_text, report)`` or refuse the item.

    Refusal returns ``(None, {"rejected": True, "reason": ...})`` and the caller
    stores no content. Redaction returns the cleaned text plus per-category
    counts. The report never contains the offending values.
    """
    value = str(text or "")
    if not value.strip():
        raise TrainingDataError("empty_content", "The selected item has no text to review.")
    if len(value) > MAX_ITEM_CHARS:
        raise TrainingDataError(
            "item_too_large",
            f"The selected item is larger than the {MAX_ITEM_CHARS} character review limit.",
        )
    for pattern, reason in _REJECT_PATTERNS:
        if pattern.search(value):
            return None, {"rejected": True, "reason": reason, "redactions": {}}
    redactions: dict[str, int] = {}
    cleaned = value
    for pattern, replacement, key in _PII_REDACT_PATTERNS:
        counter = {"n": 0}

        def _replace(match, _counter=counter, _replacement=replacement):
            _counter["n"] += 1
            return _replacement

        cleaned = pattern.sub(_replace, cleaned)
        if counter["n"]:
            redactions[key] = counter["n"]
    secret_cleaned = redact_secret_text(cleaned)
    if secret_cleaned != cleaned:
        redactions["secrets"] = 1
        cleaned = secret_cleaned
    return cleaned, {"rejected": False, "reason": "", "redactions": redactions}


def _parse_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ── source resolution (explicit ids only) ────────────────────────────────


def _configured_file_roots() -> list[str]:
    raw = os.getenv(FILE_ROOTS_ENV, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("%s is not valid JSON; file sources stay unavailable", FILE_ROOTS_ENV)
        return []
    if not isinstance(parsed, list) or not 1 <= len(parsed) <= 16:
        return []
    roots: list[str] = []
    for entry in parsed:
        text = str(entry or "").strip()
        if text and os.path.isabs(text):
            roots.append(os.path.realpath(text))
    return roots


def _resolve_file(source_ref: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    raw_path = str(source_ref.get("path") or "").strip()
    if not raw_path or len(raw_path) > MAX_SOURCE_REF_CHARS or _CONTROL_RE.search(raw_path):
        raise TrainingDataError("invalid_source", "A file path is required.")
    roots = _configured_file_roots()
    if not roots:
        raise TrainingDataError(
            "file_roots_unconfigured",
            "File sources are unavailable: set ODYSSEUS_TRAINING_FILE_ROOTS to the "
            "operator-approved folders before selecting files.",
            status_code=409,
        )
    resolved = os.path.realpath(os.path.expanduser(raw_path))
    allowed = any(
        os.path.commonpath([resolved, root]) == root for root in roots
    )
    if not allowed:
        raise TrainingDataError(
            "file_outside_roots",
            "That file is outside the operator-approved training file folders.",
            status_code=403,
        )
    if os.path.islink(raw_path) or not os.path.isfile(resolved):
        raise TrainingDataError("file_unavailable", "That file is not a readable regular file.")
    suffix = Path(resolved).suffix.lower()
    if suffix not in TEXT_FILE_SUFFIXES:
        raise TrainingDataError(
            "file_unsupported",
            "Only plain-text training files are supported here. Export the document "
            "to .txt/.jsonl first (PDF/Office extraction is a follow-up).",
        )
    try:
        with open(resolved, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise TrainingDataError("file_unavailable", "That file could not be read.") from exc
    if len(content) > MAX_FILE_BYTES:
        raise TrainingDataError(
            "item_too_large",
            f"The selected file is larger than the {MAX_FILE_BYTES} byte review limit.",
        )
    return content, None, {"path": resolved, "bytes": len(content.encode("utf-8"))}


def _resolve_book(source_ref: dict[str, Any], actor: str | None) -> tuple[str, str | None, dict[str, Any]]:
    book_id = str(source_ref.get("book_id") or "").strip()
    if not book_id or len(book_id) > 128 or _CONTROL_RE.search(book_id):
        raise TrainingDataError("invalid_source", "A book id is required.")
    owner_segment = secure_filename((actor or "local").strip())[:80] or "local"
    books_dir = os.path.realpath(os.path.join(PERSONAL_UPLOADS_DIR, owner_segment, "books"))
    catalog_path = os.path.join(books_dir, "catalog.json")
    try:
        catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TrainingDataError("book_unavailable", "The Books catalog is not available.")
    books = catalog.get("books", []) if isinstance(catalog, dict) else []
    book = next((entry for entry in books if isinstance(entry, dict) and str(entry.get("id")) == book_id), None)
    if book is None:
        raise TrainingDataError("book_not_found", "No book with that id is in your library.")
    source = os.path.realpath(str(book.get("source") or ""))
    try:
        allowed = source != books_dir and os.path.commonpath([source, books_dir]) == books_dir
    except ValueError:
        allowed = False
    if not allowed or not os.path.isfile(source):
        raise TrainingDataError("book_unavailable", "That book's source file is not available.")
    suffix = Path(source).suffix.lower()
    if suffix == ".pdf":
        from src.personal_docs import extract_pdf_text

        content = extract_pdf_text(source)[: MAX_BOOK_CHARS + 1]
    elif suffix in TEXT_FILE_SUFFIXES:
        with open(source, "r", encoding="utf-8", errors="ignore") as handle:
            content = handle.read(MAX_BOOK_CHARS + 1)
    else:
        from src.markitdown_runtime import convert_to_markdown

        content = (convert_to_markdown(source) or "")[: MAX_BOOK_CHARS + 1]
    if not content.strip():
        raise TrainingDataError(
            "book_empty",
            "No text could be extracted from that book. Check its indexing status first.",
        )
    if len(content) > MAX_BOOK_CHARS:
        raise TrainingDataError(
            "item_too_large",
            f"The extracted book text exceeds the {MAX_BOOK_CHARS} character review limit.",
        )
    title = str(book.get("title") or book.get("filename") or "Untitled")
    return content, actor, {"book_id": book_id, "title": title[:200], "filename": str(book.get("filename") or "")[:200]}


def _resolve_conversation(
    source_ref: dict[str, Any], actor: str | None
) -> tuple[str, str | None, dict[str, Any]]:
    session_id = str(source_ref.get("session_id") or "").strip()
    if not session_id or len(session_id) > 128:
        raise TrainingDataError("invalid_source", "A conversation session id is required.")
    requested = source_ref.get("message_ids")
    message_ids = [str(item) for item in requested] if isinstance(requested, list) else []
    if len(message_ids) > MAX_MESSAGES_PER_ITEM:
        raise TrainingDataError("invalid_source", "Select at most 200 messages per item.")
    db = SessionLocal()
    try:
        session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if session is None:
            raise TrainingDataError("session_not_found", "That conversation does not exist.")
        query = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
        if message_ids:
            query = query.filter(ChatMessage.id.in_(message_ids))
        messages = query.order_by(ChatMessage.timestamp.asc()).limit(MAX_MESSAGES_PER_ITEM).all()
        if not messages:
            raise TrainingDataError("empty_content", "That selection has no messages to review.")
        lines = [f"{str(row.role or 'user')}: {str(row.content or '')}" for row in messages]
        owner = str(session.owner or "") or None
    finally:
        db.close()
    return "\n\n".join(lines), owner, {
        "session_id": session_id,
        "message_ids": [row.id for row in messages],
        "message_count": len(messages),
    }


def _resolve_memory(
    source_ref: dict[str, Any], actor: str | None
) -> tuple[str, str | None, dict[str, Any]]:
    requested = source_ref.get("memory_ids")
    memory_ids = [str(item) for item in requested] if isinstance(requested, list) else []
    if not memory_ids or len(memory_ids) > MAX_MEMORIES_PER_ITEM:
        raise TrainingDataError("invalid_source", "Select between 1 and 200 memory ids.")
    db = SessionLocal()
    try:
        rows = db.query(Memory).filter(Memory.id.in_(memory_ids)).all()
        if not rows:
            raise TrainingDataError("memory_not_found", "Those memories do not exist.")
        owner = str(rows[0].owner or "") or None
        texts = [str(row.text or "") for row in rows if str(row.text or "").strip()]
    finally:
        db.close()
    if not texts:
        raise TrainingDataError("empty_content", "Those memories have no text to review.")
    return "\n\n".join(texts), owner, {"memory_ids": [row.id for row in rows], "memory_count": len(rows)}


def _resolve_tool_trace(
    source_ref: dict[str, Any], actor: str | None
) -> tuple[str, str | None, dict[str, Any]]:
    run_id = str(source_ref.get("run_id") or "").strip()
    if not run_id or len(run_id) > 128:
        raise TrainingDataError("invalid_source", "A task run id is required.")
    db = SessionLocal()
    try:
        run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
        if run is None:
            raise TrainingDataError("run_not_found", "That task run does not exist.")
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first()
        owner = str(getattr(task, "owner", "") or "") or None
        content = str(run.steps or "")
        name = str(getattr(task, "name", "") or "")
    finally:
        db.close()
    if not content.strip():
        raise TrainingDataError("empty_content", "That run has no tool-trace steps to review.")
    return content[:MAX_TRACE_CHARS], owner, {"run_id": run_id, "task": name[:200]}


def resolve_source(
    source_kind: str,
    source_ref: dict[str, Any] | None,
    *,
    content: str | None,
    actor: str | None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Resolve exactly one explicitly named source. Never scans or searches."""
    kind = str(source_kind or "").strip().lower()
    ref = dict(source_ref or {})
    if kind == "manual":
        return str(content or ""), actor, {"kind": "manual"}
    if kind == "file":
        return _resolve_file(ref)
    if kind == "book":
        return _resolve_book(ref, actor)
    if kind == "conversation":
        return _resolve_conversation(ref, actor)
    if kind == "memory":
        return _resolve_memory(ref, actor)
    if kind == "tool_trace":
        return _resolve_tool_trace(ref, actor)
    raise TrainingDataError(
        "invalid_source_kind",
        "Choose one of: manual, file, book, conversation, memory, tool_trace.",
    )


# ── payloads ─────────────────────────────────────────────────────────────


def _item_payload(item: TrainingDatasetItem, *, preview_chars: int = 0) -> dict[str, Any]:
    payload = {
        "id": item.id,
        "dataset_id": item.dataset_id,
        "source_kind": item.source_kind,
        "source_ref": _parse_json(item.source_ref, {}),
        "owner": item.owner,
        "consent_license": item.consent_license,
        "consent_attested_by": item.consent_attested_by,
        "review_state": item.review_state,
        "review_reason": item.review_reason,
        "content_hash": item.content_hash,
        "redaction": _parse_json(item.redaction, {}),
        "exclusion_path": item.exclusion_path,
        "reviewed_by": item.reviewed_by,
        "reviewed_at": _iso(item.reviewed_at),
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }
    if preview_chars and item.review_state != "rejected":
        text = str(item.content or "")
        payload["preview"] = text[:preview_chars]
    return payload


def _dataset_payload(dataset: TrainingDataset, *, items: list[TrainingDatasetItem] | None = None) -> dict[str, Any]:
    payload = {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "status": dataset.status,
        "license_summary": dataset.license_summary,
        "item_count": int(dataset.item_count or 0),
        "fingerprint": dataset.fingerprint,
        "reviewed_by": dataset.reviewed_by,
        "reviewed_at": _iso(dataset.reviewed_at),
        "created_at": _iso(dataset.created_at),
        "updated_at": _iso(dataset.updated_at),
        "counts": {},
    }
    if items is not None:
        counts: dict[str, int] = {}
        for item in items:
            counts[item.review_state] = counts.get(item.review_state, 0) + 1
        payload["counts"] = counts
        payload["items"] = [_item_payload(item, preview_chars=240) for item in items]
    return payload


def _get_dataset_row(db: Any, dataset_id: str) -> TrainingDataset:
    row = _owner_query(db, None).filter(TrainingDataset.id == str(dataset_id or "")).first()
    if row is None:
        raise TrainingDataError("not_found", "That dataset does not exist.", status_code=404)
    if row.status == "retired":
        raise TrainingDataError("retired", "That dataset is retired and read-only.", status_code=409)
    return row


# ── dataset CRUD ─────────────────────────────────────────────────────────


def list_datasets() -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = _owner_query(db, None).order_by(TrainingDataset.created_at.desc()).all()
        return [_dataset_payload(row) for row in rows]
    finally:
        db.close()


def create_dataset(
    name: Any,
    *,
    description: Any = None,
    license_summary: Any = None,
    actor: str | None = None,
) -> dict[str, Any]:
    name_value = _clean_text(name, limit=MAX_NAME_CHARS, field="Dataset name", required=True)
    db = SessionLocal()
    try:
        row = TrainingDataset(
            id=uuid.uuid4().hex,
            name=name_value,
            description=_clean_text(description, limit=MAX_DESCRIPTION_CHARS, field="Description"),
            license_summary=_clean_text(
                license_summary, limit=MAX_LICENSE_CHARS, field="License summary"
            ),
            owner=None,
            status="draft",
            item_count=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _dataset_payload(row)
    finally:
        db.close()


def get_dataset(dataset_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, None).filter(TrainingDataset.id == str(dataset_id or "")).first()
        if row is None:
            raise TrainingDataError("not_found", "That dataset does not exist.", status_code=404)
        items = (
            db.query(TrainingDatasetItem)
            .filter(TrainingDatasetItem.dataset_id == row.id)
            .order_by(TrainingDatasetItem.created_at.asc())
            .all()
        )
        return _dataset_payload(row, items=items)
    finally:
        db.close()


def _reset_review_state(db: Any, row: TrainingDataset) -> None:
    row.status = "draft"
    row.fingerprint = None
    row.reviewed_by = None
    row.reviewed_at = None
    row.item_count = 0


def add_item(
    dataset_id: str,
    *,
    source_kind: Any,
    source_ref: Any = None,
    content: Any = None,
    consent_license: Any = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Add one explicitly selected item. There is no scan/harvest path."""
    kind = str(source_kind or "").strip().lower()
    if kind not in SOURCE_KINDS:
        raise TrainingDataError(
            "invalid_source_kind",
            "Choose one of: manual, file, book, conversation, memory, tool_trace.",
        )
    license_value = _clean_text(
        consent_license, limit=MAX_LICENSE_CHARS, field="Consent/license", required=True
    )
    db = SessionLocal()
    try:
        dataset = _get_dataset_row(db, dataset_id)
        existing = (
            db.query(TrainingDatasetItem)
            .filter(TrainingDatasetItem.dataset_id == dataset.id)
            .count()
        )
        if existing >= MAX_DATASET_ITEMS:
            raise TrainingDataError(
                "dataset_full", f"A dataset holds at most {MAX_DATASET_ITEMS} items."
            )
    finally:
        db.close()

    resolved_content, resolved_owner, ref_out = resolve_source(
        kind, source_ref, content=content, actor=actor
    )
    screened, report = screen_content(resolved_content)

    db = SessionLocal()
    try:
        dataset = _get_dataset_row(db, dataset_id)
        item = TrainingDatasetItem(
            id=uuid.uuid4().hex,
            dataset_id=dataset.id,
            source_kind=kind,
            source_ref=json.dumps(ref_out, sort_keys=True),
            owner=resolved_owner or actor,
            consent_license=license_value,
            consent_attested_by=actor,
            review_state="rejected" if report.get("rejected") else ("redacted" if report.get("redactions") else "pending"),
            review_reason=str(report.get("reason") or "") or None,
            content_hash=None if report.get("rejected") else content_hash(screened or ""),
            content=None if report.get("rejected") else screened,
            redaction=json.dumps(report, sort_keys=True),
            exclusion_path=EXCLUSION_COPY.format(
                dataset_id=dataset.id, item_id="pending", source_kind=kind
            ),
        )
        item.exclusion_path = EXCLUSION_COPY.format(
            dataset_id=dataset.id, item_id=item.id, source_kind=kind
        )
        db.add(item)
        _reset_review_state(db, dataset)
        db.commit()
        db.refresh(item)
        if report.get("rejected"):
            logger.info(
                "training dataset item rejected dataset=%s kind=%s reason=%s",
                dataset.id,
                kind,
                report.get("reason"),
            )
        return _item_payload(item, preview_chars=240)
    finally:
        db.close()


def review_item(
    dataset_id: str,
    item_id: str,
    *,
    decision: Any,
    reason: Any = None,
    actor: str | None = None,
) -> dict[str, Any]:
    decision_value = str(decision or "").strip().lower()
    if decision_value not in {"approve", "reject", "exclude"}:
        raise TrainingDataError("invalid_decision", "The review decision must be approve, reject, or exclude.")
    db = SessionLocal()
    try:
        dataset = _get_dataset_row(db, dataset_id)
        item = (
            db.query(TrainingDatasetItem)
            .filter(
                TrainingDatasetItem.id == str(item_id or ""),
                TrainingDatasetItem.dataset_id == dataset.id,
            )
            .first()
        )
        if item is None:
            raise TrainingDataError("not_found", "That dataset item does not exist.", status_code=404)
        if decision_value == "approve":
            if not item.content:
                raise TrainingDataError(
                    "rejected_item",
                    "This item was rejected at screening and cannot be approved. Add a cleaned copy.",
                    status_code=409,
                )
            screened, report = screen_content(item.content)
            if report.get("rejected") or screened is None:
                item.review_state = "rejected"
                item.review_reason = str(report.get("reason") or "failed screening")
                item.content = None
                item.content_hash = None
                _reset_review_state(db, dataset)
                db.commit()
                raise TrainingDataError(
                    "rejected_item",
                    "This item still fails screening and cannot be approved.",
                    status_code=409,
                )
            item.content = screened
            item.content_hash = content_hash(screened)
            item.redaction = json.dumps(report, sort_keys=True)
            item.review_state = "redacted" if report.get("redactions") else "approved"
            item.review_reason = None
        elif decision_value == "reject":
            item.review_state = "rejected"
            item.review_reason = _clean_text(reason, limit=500, field="Reason") or "rejected by operator"
        else:
            item.review_state = "excluded"
            item.review_reason = _clean_text(reason, limit=500, field="Reason") or "excluded by operator"
        item.reviewed_by = actor
        item.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        _reset_review_state(db, dataset)
        db.commit()
        db.refresh(item)
        return _item_payload(item, preview_chars=240)
    finally:
        db.close()


def remove_item(dataset_id: str, item_id: str) -> dict[str, Any]:
    """Exclusion/deletion path: removes the manifest item; source untouched."""
    db = SessionLocal()
    try:
        dataset = _get_dataset_row(db, dataset_id)
        item = (
            db.query(TrainingDatasetItem)
            .filter(
                TrainingDatasetItem.id == str(item_id or ""),
                TrainingDatasetItem.dataset_id == dataset.id,
            )
            .first()
        )
        if item is None:
            raise TrainingDataError("not_found", "That dataset item does not exist.", status_code=404)
        db.delete(item)
        _reset_review_state(db, dataset)
        db.commit()
        return {"ok": True, "removed_item_id": item_id, "source_modified": False}
    finally:
        db.close()


def _included_items(db: Any, dataset_id: str) -> list[TrainingDatasetItem]:
    return (
        db.query(TrainingDatasetItem)
        .filter(
            TrainingDatasetItem.dataset_id == dataset_id,
            TrainingDatasetItem.review_state.in_(INCLUDED_REVIEW_STATES),
        )
        .order_by(TrainingDatasetItem.id.asc())
        .all()
    )


def compute_fingerprint(items: list[TrainingDatasetItem]) -> str:
    material = "\n".join(f"{item.id}:{item.content_hash}" for item in items)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def review_dataset(dataset_id: str, *, actor: str | None = None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        dataset = _get_dataset_row(db, dataset_id)
        items = (
            db.query(TrainingDatasetItem)
            .filter(TrainingDatasetItem.dataset_id == dataset.id)
            .all()
        )
        pending = [item for item in items if item.review_state == "pending"]
        if pending:
            raise TrainingDataError(
                "unreviewed_items",
                f"{len(pending)} item(s) still need a review decision before the dataset can be reviewed.",
                status_code=409,
            )
        included = _included_items(db, dataset.id)
        if not included:
            raise TrainingDataError(
                "empty_dataset",
                "At least one approved item is required before a dataset can be reviewed.",
                status_code=409,
            )
        dataset.status = "reviewed"
        dataset.fingerprint = compute_fingerprint(included)
        dataset.item_count = len(included)
        dataset.reviewed_by = actor
        dataset.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(dataset)
        return _dataset_payload(dataset)
    finally:
        db.close()


def retire_dataset(dataset_id: str, *, actor: str | None = None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, None).filter(TrainingDataset.id == str(dataset_id or "")).first()
        if row is None:
            raise TrainingDataError("not_found", "That dataset does not exist.", status_code=404)
        row.status = "retired"
        row.fingerprint = None
        db.commit()
        db.refresh(row)
        return _dataset_payload(row)
    finally:
        db.close()


def verified_dataset(dataset_id: str) -> tuple[TrainingDataset, list[TrainingDatasetItem]]:
    """Load a reviewed dataset and re-verify its fingerprint still matches."""
    db = SessionLocal()
    try:
        row = _owner_query(db, None).filter(TrainingDataset.id == str(dataset_id or "")).first()
        if row is None:
            raise TrainingDataError("not_found", "That dataset does not exist.", status_code=404)
        if row.status != "reviewed" or not row.fingerprint:
            raise TrainingDataError(
                "not_reviewed",
                "The dataset must be reviewed before it can be used for a job.",
                status_code=409,
            )
        items = _included_items(db, row.id)
        if compute_fingerprint(items) != row.fingerprint:
            raise TrainingDataError(
                "dataset_changed",
                "The dataset changed after review. Review it again before starting a job.",
                status_code=409,
            )
        db.expunge(row)
        for item in items:
            db.expunge(item)
        return row, items
    finally:
        db.close()


def export_dataset_jsonl(dataset_id: str) -> dict[str, Any]:
    """Write the approved manifest to a managed local JSONL file.

    The file is a bounded local artifact the operator can copy to the runtime
    host (or a shared mount). Nothing here uploads or starts a job.
    """
    dataset, items = verified_dataset(dataset_id)
    out_dir = Path(DATA_DIR) / "training_datasets" / dataset.id
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_chmod(out_dir, 0o700)
    except OSError as exc:
        raise TrainingDataError(
            "storage_unavailable", "Could not prepare the dataset export folder.", status_code=500
        ) from exc
    path = out_dir / "dataset.jsonl"
    lines = []
    for item in items:
        record = {
            "text": str(item.content or ""),
            "source_kind": item.source_kind,
            "content_hash": item.content_hash,
            "license": item.consent_license,
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        safe_chmod(path, 0o600)
    except OSError as exc:
        raise TrainingDataError(
            "storage_unavailable", "Could not write the dataset export file.", status_code=500
        ) from exc
    return {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "item_count": len(items),
        "bytes": len(payload),
        "fingerprint": dataset.fingerprint,
    }