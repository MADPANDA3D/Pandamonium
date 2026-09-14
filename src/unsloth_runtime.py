"""Versioned adapter for an operator-provided Unsloth Studio runtime (MAD-796).

Pandamonium does not bundle a trainer. Training is an optional external
runtime: the operator runs Unsloth Studio on a machine they own and points
this adapter at its HTTP API. The adapter owns connection storage, read-only
health/capability discovery, and the explicit job-control calls that
``src/training_jobs.py`` invokes only after a previewed, operator-confirmed
spec. It never decides to train anything by itself.

Contract
--------
Adapter contract id: ``pandamonium-unsloth-runtime-v1`` (``ADAPTER_VERSION=1``).

The adapter reads the documented Unsloth Studio HTTP surface:

* ``GET /api/health`` — health, no auth.
* ``GET /api/system`` — GPU/CPU/memory summary, bearer auth.
* ``GET /openapi.json`` — FastAPI schema used for capability discovery.
* ``GET /api/train/status`` — current job state, bearer auth.
* ``GET /api/models/`` — model identities, bearer auth.

Capabilities are classified as training, conversion, and inference from what
the runtime advertises (OpenAPI paths first, then GET-only probes). The
documented Studio HTTP API has no export/convert route — export is CLI-only —
so conversion is reported ``unsupported`` unless the runtime exposes its own
``/export``/``/convert`` path.

No-accidental-training guarantee
--------------------------------
Discovery is still structurally GET-only: every read goes through
:func:`_get`, which has no method parameter, and ``POST /api/train/start`` is
only ever *observed* in the OpenAPI schema while testing a connection. Job
control is a separate, explicit surface (MAD-797): :func:`start_training_job`
and :func:`stop_training_job` are the only functions that POST, they are never
called by discovery, and their only caller is the reviewed jobs module, which
requires a preview fingerprint match, an acknowledgment of the destructive/
paid implications, a reviewed dataset fingerprint, and an admin-authorized
route. A health/discovery/test call can therefore never begin a job.

Security contract
-----------------
* The access token is stored Fernet-encrypted at rest (``EncryptedText`` /
  ``src.secret_storage``) and never returned, logged, or echoed in an error.
* The runtime URL may be loopback/LAN (the normal self-hosted case); link-local
  metadata targets and non-HTTP(S) schemes stay blocked by ``url_safety``.
* Redirects are refused so a bearer token can never be bounced to another
  origin, and response bodies are bounded.
* Capability claims come from what the runtime advertises, never from
  optimistic defaults.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.database import Integration, ModelEndpoint, SessionLocal
from src.secret_storage import decrypt, encrypt, is_encrypted
from src.url_safety import check_outbound_url

logger = logging.getLogger(__name__)

INTEGRATION_TYPE = "unsloth_runtime"
CONNECTION_NAME = "primary"

ADAPTER_VERSION = 1
CONTRACT_ID = "pandamonium-unsloth-runtime-v1"

MAX_BASE_URL_CHARS = 2048
MAX_TOKEN_CHARS = 4096
MAX_MODEL_ENDPOINT_ID_CHARS = 128
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 512 * 1024
MAX_MODEL_SAMPLE = 10
MAX_MODEL_ID_CHARS = 200

# ── states ───────────────────────────────────────────────────────────────
#
# Connection states are explicit and fail closed. ``online`` is the only
# state that means "usable"; everything else carries honest operator copy.

STATE_UNCONFIGURED = "unconfigured"
STATE_DISABLED = "disabled"
STATE_UNTESTED = "untested"
STATE_ONLINE = "online"
STATE_OFFLINE = "offline"
STATE_UNAUTHORIZED = "unauthorized"
STATE_INCOMPATIBLE = "incompatible"
STATE_BUSY = "busy"
STATE_INSUFFICIENT = "insufficient_resources"
STATE_INVALID = "invalid_response"
STATE_ERROR = "error"

CONNECTION_STATES = (
    STATE_UNCONFIGURED,
    STATE_DISABLED,
    STATE_UNTESTED,
    STATE_ONLINE,
    STATE_OFFLINE,
    STATE_UNAUTHORIZED,
    STATE_INCOMPATIBLE,
    STATE_BUSY,
    STATE_INSUFFICIENT,
    STATE_INVALID,
    STATE_ERROR,
)

CAPABILITY_TRAINING = "training"
CAPABILITY_CONVERSION = "conversion"
CAPABILITY_INFERENCE = "inference"
CAPABILITY_ORDER = (CAPABILITY_TRAINING, CAPABILITY_CONVERSION, CAPABILITY_INFERENCE)

CAPABILITY_SUPPORTED = "supported"
CAPABILITY_UNSUPPORTED = "unsupported"
CAPABILITY_UNAVAILABLE = "unavailable"
CAPABILITY_UNKNOWN = "unknown"

UNCONFIGURED_MESSAGE = "No Unsloth Studio runtime is configured."
DISABLED_MESSAGE = (
    "The Unsloth Studio runtime is disabled. Enable it to test the connection."
)
UNTESTED_MESSAGE = "Not tested yet. Run Test to discover what this runtime supports."
ONLINE_MESSAGE = "The runtime answered and accepted the access token."
OFFLINE_MESSAGE = (
    "The runtime did not answer. Check the address and that Unsloth Studio is running."
)
TIMEOUT_MESSAGE = "The runtime did not answer in time. Check the address and try again."
UNAUTHORIZED_MESSAGE = (
    "The runtime rejected the access token. Create a fresh token in Unsloth Studio "
    "and save it here."
)
INCOMPATIBLE_MESSAGE = (
    "The endpoint answered, but it does not expose the Unsloth Studio API this "
    "adapter supports. Point it at a supported Studio build."
)
BUSY_MESSAGE = (
    "The runtime is training right now. New training must wait until the current "
    "job finishes."
)
INSUFFICIENT_MESSAGE = (
    "The runtime reports insufficient resources for training (no usable GPU or "
    "not enough memory)."
)
INVALID_MESSAGE = (
    "The endpoint returned a response this adapter could not read safely."
)
ERROR_MESSAGE = "The runtime reported an internal error while answering a read-only request."

# The documented Studio HTTP API surface. Method-less entries are reads the
# adapter probes; the train/inference entries are only ever inspected in the
# OpenAPI schema, never called.
ROUTE_HEALTH = "/api/health"
ROUTE_SYSTEM = "/api/system"
ROUTE_OPENAPI = "/openapi.json"
ROUTE_TRAIN_START = "/api/train/start"
ROUTE_TRAIN_STATUS = "/api/train/status"
ROUTE_MODELS = "/api/models/"
ROUTE_INFERENCE_CHAT = "/api/inference/chat"
CONVERSION_PATH_MARKERS = ("/export", "/convert", "/merge-model")

# Known path suffixes an operator may paste instead of the runtime root.
_BASE_URL_SUFFIXES = ("/api/health", "/api", "/health")

# Studio job vocabulary mapped onto the repository's job-state vocabulary
# (running | done | failed | stopped, as used by src/bg_jobs.py and
# src/agent_runs.py). Unknown raw states normalize to "unknown" rather than
# guessing.
JOB_STATE_MAP = {
    "running": "running",
    "training": "running",
    "in_progress": "running",
    "in-progress": "running",
    "busy": "running",
    "idle": "idle",
    "not_started": "idle",
    "not-started": "idle",
    "pending": "idle",
    "queued": "idle",
    "stopped": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "completed": "done",
    "complete": "done",
    "success": "done",
    "succeeded": "done",
    "done": "done",
    "failed": "failed",
    "error": "failed",
}

# Resource flags that explicitly mean "this host cannot train". The adapter
# only claims insufficient resources when the runtime says so; a missing field
# is "unknown", never a guess.
INSUFFICIENT_FLAG_KEYS = (
    "cuda_available",
    "can_train",
    "training_available",
    "gpu_available",
    "has_gpu",
)
RESOURCE_LIST_KEYS = ("gpus", "devices", "accelerators")
RESOURCE_TOTAL_KEYS = (
    "vram_total",
    "vram_total_bytes",
    "vram_total_mb",
    "vram_total_gb",
    "gpu_memory_total",
)

_UNSET = object()
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class UnslothError(Exception):
    """Fail-closed adapter error; callers map ``code`` to honest copy."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
    ) -> None:
        super().__init__(message)
        self.code = str(code or STATE_ERROR)
        self.message = str(message or "")
        self.status_code = int(status_code)

    def public(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


# ── connection storage ───────────────────────────────────────────────────


def _owner_query(db: Any, owner: str | None):
    query = db.query(Integration).filter(
        Integration.type == INTEGRATION_TYPE,
        Integration.name == CONNECTION_NAME,
    )
    return query.filter(
        Integration.owner.is_(None) if owner is None else Integration.owner == owner
    )


def _normalize_base_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > MAX_BASE_URL_CHARS:
        raise ValueError("The Unsloth Studio URL is required.")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("The Unsloth Studio URL must be HTTP(S).")
    if parsed.username or parsed.password:
        raise ValueError("The Unsloth Studio URL cannot contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("The Unsloth Studio URL cannot contain a query or fragment.")
    path = parsed.path.rstrip("/")
    for suffix in _BASE_URL_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    cleaned = urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", "")).rstrip("/")
    # Pandamonium is local-first: loopback/LAN runtimes are the normal case, so
    # private targets stay allowed while link-local metadata targets do not.
    ok, reason = check_outbound_url(cleaned, block_private=False)
    if not ok:
        raise ValueError(f"Unsloth Studio URL rejected: {reason}")
    return cleaned


def _validate_token(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("An Unsloth Studio access token is required.")
    if len(token) > MAX_TOKEN_CHARS or _CONTROL_RE.search(token) or is_encrypted(token):
        raise ValueError("Invalid Unsloth Studio access token.")
    return token


def _validate_model_endpoint_id(value: Any) -> str | None:
    reference = str(value or "").strip()
    if not reference:
        return None
    if len(reference) > MAX_MODEL_ENDPOINT_ID_CHARS or _CONTROL_RE.search(reference):
        raise ValueError("Invalid model endpoint reference.")
    return reference


def _resolve_model_endpoint(reference: str | None) -> dict[str, Any] | None:
    """Resolve an optional model-identity binding against the canonical table."""
    if not reference:
        return None
    db = SessionLocal()
    try:
        row = db.query(ModelEndpoint).filter(ModelEndpoint.id == reference).first()
    finally:
        db.close()
    if row is None:
        raise ValueError(
            "The referenced model endpoint does not exist. Choose an added model."
        )
    return {
        "id": row.id,
        "name": str(row.name or ""),
        "base_url": str(row.base_url or ""),
        "model_type": str(row.model_type or "llm"),
    }


def get_connection(owner: str | None, *, require_enabled: bool = False) -> dict[str, Any]:
    """Load one saved runtime connection (token decrypted for internal use only)."""
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise UnslothError(STATE_UNCONFIGURED, UNCONFIGURED_MESSAGE, status_code=404)
        config = dict(row.config or {})
        result = {
            "id": row.id,
            "owner": row.owner,
            "enabled": bool(row.enabled),
            "base_url": str(config.get("base_url") or ""),
            "token": decrypt(str(config.get("token") or "")),
            "model_endpoint_id": config.get("model_endpoint_id"),
            "status": str(config.get("status") or ("untested" if row.enabled else STATE_DISABLED)),
            "last_message": config.get("last_message"),
            "last_reason": config.get("last_reason"),
            "last_checked_at": config.get("last_checked_at"),
            "runtime_version": config.get("runtime_version"),
            "job_state": config.get("job_state"),
            "resources": config.get("resources"),
            "capabilities": config.get("capabilities"),
            "contract": config.get("contract"),
        }
    finally:
        db.close()
    if require_enabled and not result["enabled"]:
        raise UnslothError(STATE_DISABLED, DISABLED_MESSAGE, status_code=409)
    return result


def connection_status(owner: str | None) -> dict[str, Any]:
    """Redacted payload: never includes the access token."""
    try:
        connection = get_connection(owner)
    except UnslothError as exc:
        if exc.code == STATE_UNCONFIGURED:
            return {
                "configured": False,
                "enabled": False,
                "base_url": "",
                "token_configured": False,
                "model_endpoint": None,
                "status": STATE_UNCONFIGURED,
                "last_message": UNCONFIGURED_MESSAGE,
                "last_reason": "",
                "last_checked_at": None,
                "runtime_version": None,
                "job_state": None,
                "resources": None,
                "capabilities": None,
                "contract": CONTRACT_ID,
                "adapter_version": ADAPTER_VERSION,
            }
        raise
    return {
        "configured": True,
        "enabled": connection["enabled"],
        "base_url": connection["base_url"],
        "token_configured": bool(connection["token"]),
        "model_endpoint": _resolve_model_endpoint_safe(connection["model_endpoint_id"]),
        "status": connection["status"] if connection["enabled"] else STATE_DISABLED,
        "last_message": connection["last_message"]
        or (
            UNTESTED_MESSAGE
            if connection["enabled"] and connection["status"] == STATE_UNTESTED
            else None
        ),
        "last_reason": connection["last_reason"],
        "last_checked_at": connection["last_checked_at"],
        "runtime_version": connection["runtime_version"],
        "job_state": connection["job_state"],
        "resources": connection["resources"],
        "capabilities": connection["capabilities"],
        "contract": connection["contract"] or CONTRACT_ID,
        "adapter_version": ADAPTER_VERSION,
    }


def _resolve_model_endpoint_safe(reference: str | None) -> dict[str, Any] | None:
    try:
        return _resolve_model_endpoint(reference)
    except ValueError:
        return None


def save_connection(
    owner: str | None,
    *,
    base_url: str | None = None,
    token: object = _UNSET,
    enabled: bool | None = None,
    model_endpoint_id: object = _UNSET,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        config = dict(row.config or {}) if row else {}
        trust_reset = False
        if base_url is not None:
            normalized = _normalize_base_url(base_url)
            if normalized != config.get("base_url"):
                trust_reset = True
            config["base_url"] = normalized
        if token is not _UNSET:
            config["token"] = encrypt(_validate_token(token))
            trust_reset = True
        if model_endpoint_id is not _UNSET:
            reference = _validate_model_endpoint_id(model_endpoint_id)
            _resolve_model_endpoint(reference)
            config["model_endpoint_id"] = reference
        if not config.get("base_url"):
            raise ValueError("The Unsloth Studio URL is required.")
        if not config.get("token"):
            raise ValueError("An Unsloth Studio access token is required.")
        if trust_reset:
            config.update(
                {
                    "status": STATE_UNTESTED,
                    "last_message": None,
                    "last_reason": None,
                    "last_checked_at": None,
                    "runtime_version": None,
                    "job_state": None,
                    "capabilities": None,
                }
            )
        if row is None:
            row = Integration(
                id=uuid.uuid4().hex,
                owner=owner,
                name=CONNECTION_NAME,
                type=INTEGRATION_TYPE,
                config=config,
                enabled=True if enabled is None else enabled,
            )
            db.add(row)
        else:
            row.config = config
            if enabled is not None:
                row.enabled = enabled
        if not row.enabled:
            disabled_config = dict(row.config or {})
            disabled_config["status"] = STATE_DISABLED
            row.config = disabled_config
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return connection_status(owner)


def remove_connection(owner: str | None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).first()
        if row is None:
            raise UnslothError(STATE_UNCONFIGURED, UNCONFIGURED_MESSAGE, status_code=404)
        db.delete(row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"ok": True}


def _record_discovery(
    owner: str | None,
    connection_id: str,
    *,
    status: str,
    reason: str,
    message: str,
    capabilities: dict[str, Any] | None,
    runtime_version: str | None,
    job_state: str | None,
    resources: dict[str, Any] | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = _owner_query(db, owner).filter(Integration.id == connection_id).first()
        if row is None:
            return
        config = dict(row.config or {})
        config.update(
            {
                "status": status,
                "last_reason": reason or "",
                "last_message": message or "",
                "last_checked_at": datetime.now(timezone.utc).isoformat(),
                "capabilities": capabilities,
                "runtime_version": runtime_version,
                "job_state": job_state,
                "resources": resources,
                "contract": CONTRACT_ID,
            }
        )
        row.config = config
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── HTTP (GET-only) ──────────────────────────────────────────────────────


class _ReadResponse:
    __slots__ = ("status_code", "payload", "media_type", "truncated")

    def __init__(self, *, status_code: int, payload: Any, media_type: str, truncated: bool):
        self.status_code = int(status_code)
        self.payload = payload
        self.media_type = str(media_type or "")
        self.truncated = bool(truncated)


async def _get(
    connection: dict[str, Any],
    path: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    authenticated: bool = True,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> _ReadResponse:
    """One bounded, redirect-free GET. This is the only request path in the module."""
    url = f"{connection['base_url']}{path}"
    headers = {"Accept": "application/json"}
    token = connection.get("token") if authenticated else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if 300 <= response.status_code < 400:
                    raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_RESPONSE_BYTES:
                        raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
                    chunks.append(chunk)
                body = b"".join(chunks)
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                status_code = response.status_code
    except UnslothError:
        raise
    except httpx.TimeoutException as exc:
        raise UnslothError(STATE_OFFLINE, TIMEOUT_MESSAGE) from exc
    except httpx.RequestError as exc:
        raise UnslothError(STATE_OFFLINE, OFFLINE_MESSAGE) from exc
    payload = None
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
    return _ReadResponse(
        status_code=status_code,
        payload=payload,
        media_type=media_type,
        truncated=False,
    )


def _first_text(payload: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None


def _job_state(payload: Any) -> tuple[str, str, bool]:
    """Return (raw, normalized, active) for one training-status payload."""
    raw = _first_text(payload, ("state", "status", "phase"))
    if raw is None:
        return "unknown", "unknown", False
    normalized = JOB_STATE_MAP.get(raw.strip().lower(), "unknown")
    return raw, normalized, normalized == "running"


def _walk_bounded(payload: Any, depth: int = 2):
    """Yield scalar entries from a bounded-depth mapping tree."""
    if depth < 0 or not isinstance(payload, dict):
        return
    for key, value in payload.items():
        yield str(key), value
        if isinstance(value, dict):
            yield from _walk_bounded(value, depth - 1)


def _resource_summary(system_payload: Any) -> dict[str, Any]:
    """Tolerant resource read. Unknown shapes stay ``unknown``, never guessed."""
    summary: dict[str, Any] = {"state": "unknown", "gpus": None, "detail": ""}
    if not isinstance(system_payload, dict):
        return summary
    found_positive = False
    for key, value in _walk_bounded(system_payload):
        lowered = key.strip().lower()
        if lowered in INSUFFICIENT_FLAG_KEYS and value is False:
            summary.update(
                {
                    "state": STATE_INSUFFICIENT,
                    "gpus": 0,
                    "detail": f"the runtime reports {lowered}=false",
                }
            )
            return summary
        if lowered in RESOURCE_LIST_KEYS and isinstance(value, list):
            if not value:
                summary.update(
                    {
                        "state": STATE_INSUFFICIENT,
                        "gpus": 0,
                        "detail": f"the runtime reports no {lowered}",
                    }
                )
                return summary
            summary["gpus"] = len(value)
            found_positive = True
        elif lowered in RESOURCE_TOTAL_KEYS and isinstance(value, (int, float)):
            if value > 0:
                found_positive = True
            else:
                summary.update(
                    {
                        "state": STATE_INSUFFICIENT,
                        "gpus": 0,
                        "detail": f"the runtime reports {lowered}=0",
                    }
                )
                return summary
    if found_positive:
        summary["state"] = "available"
        summary["detail"] = "the runtime reports a usable accelerator"
    return summary


def _path_methods(paths: Any, path: str) -> set[str]:
    if not isinstance(paths, dict):
        return set()
    entry = paths.get(path)
    if not isinstance(entry, dict):
        return set()
    return {str(method).strip().lower() for method in entry}


def _capabilities_from_schema(paths: Any) -> dict[str, dict[str, Any]]:
    capabilities = _blank_capabilities()
    if not isinstance(paths, dict):
        return capabilities
    if "post" in _path_methods(paths, ROUTE_TRAIN_START):
        capabilities[CAPABILITY_TRAINING].update(
            {"state": CAPABILITY_SUPPORTED, "detail": f"{ROUTE_TRAIN_START} is advertised"}
        )
    else:
        capabilities[CAPABILITY_TRAINING].update(
            {"state": CAPABILITY_UNSUPPORTED, "detail": "the runtime exposes no training route"}
        )
    if "post" in _path_methods(paths, ROUTE_INFERENCE_CHAT):
        capabilities[CAPABILITY_INFERENCE].update(
            {"state": CAPABILITY_SUPPORTED, "detail": f"{ROUTE_INFERENCE_CHAT} is advertised"}
        )
    else:
        capabilities[CAPABILITY_INFERENCE].update(
            {"state": CAPABILITY_UNSUPPORTED, "detail": "the runtime exposes no inference route"}
        )
    conversion_path = None
    for raw_path in paths:
        candidate = str(raw_path or "").lower()
        if any(marker in candidate for marker in CONVERSION_PATH_MARKERS):
            conversion_path = str(raw_path)
            break
    if conversion_path:
        capabilities[CAPABILITY_CONVERSION].update(
            {"state": CAPABILITY_SUPPORTED, "detail": f"{conversion_path} is advertised"}
        )
    else:
        capabilities[CAPABILITY_CONVERSION].update(
            {
                "state": CAPABILITY_UNSUPPORTED,
                "detail": "the runtime exposes no export or convert route; "
                "Studio export is CLI-only in the documented HTTP API",
            }
        )
    return capabilities


def _blank_capabilities() -> dict[str, dict[str, Any]]:
    return {
        CAPABILITY_TRAINING: {"state": CAPABILITY_UNKNOWN, "detail": "", "route": ROUTE_TRAIN_START},
        CAPABILITY_CONVERSION: {"state": CAPABILITY_UNKNOWN, "detail": "", "route": None},
        CAPABILITY_INFERENCE: {"state": CAPABILITY_UNKNOWN, "detail": "", "route": ROUTE_INFERENCE_CHAT},
    }


def _model_sample(models_payload: Any) -> dict[str, Any] | None:
    """Bounded model-identity sample from ``GET /api/models/``."""
    if not isinstance(models_payload, dict):
        return None
    raw_models = models_payload.get("models", models_payload.get("data"))
    if not isinstance(raw_models, list):
        return None
    identifiers: list[str] = []
    for entry in raw_models:
        if isinstance(entry, dict):
            candidate = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            candidate = entry
        value = str(candidate or "").strip()
        if not value or len(value) > MAX_MODEL_ID_CHARS or _CONTROL_RE.search(value):
            continue
        if value not in identifiers:
            identifiers.append(value)
        if len(identifiers) >= MAX_MODEL_SAMPLE:
            break
    return {"count": len(raw_models), "sample": identifiers}


# ── discovery and connection test ────────────────────────────────────────


async def discover_runtime(
    connection: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Read-only capability discovery. Never starts, stops, or mutates a job."""
    base_url = str(connection.get("base_url") or "")
    parsed = urlsplit(base_url)
    if (
        not base_url
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or _CONTROL_RE.search(base_url)
    ):
        raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
    started = time.monotonic()
    capabilities = _blank_capabilities()
    unsupported: list[str] = []
    resources = {"state": "unknown", "gpus": None, "detail": ""}
    job = {"state": "unknown", "normalized": "unknown", "active": False}
    models: dict[str, Any] | None = None
    runtime_version: str | None = None

    health = await _get(connection, ROUTE_HEALTH, transport=transport, authenticated=False)
    if health.status_code == 404:
        raise UnslothError(STATE_INCOMPATIBLE, INCOMPATIBLE_MESSAGE)
    if health.status_code >= 500:
        raise UnslothError(STATE_ERROR, ERROR_MESSAGE)
    if health.status_code >= 400 or not isinstance(health.payload, dict):
        raise UnslothError(STATE_INCOMPATIBLE, INCOMPATIBLE_MESSAGE)
    runtime_version = _first_text(
        health.payload, ("version", "runtime_version", "app_version", "studio_version")
    )

    if not connection.get("token"):
        raise UnslothError(STATE_UNAUTHORIZED, UNAUTHORIZED_MESSAGE, status_code=401)

    system = await _get(connection, ROUTE_SYSTEM, transport=transport)
    if system.status_code in (401, 403):
        raise UnslothError(STATE_UNAUTHORIZED, UNAUTHORIZED_MESSAGE, status_code=401)
    if system.status_code >= 500:
        raise UnslothError(STATE_ERROR, ERROR_MESSAGE)
    system_payload = (
        system.payload
        if system.status_code == 200 and isinstance(system.payload, dict)
        else None
    )
    if system_payload is not None:
        resources = _resource_summary(system_payload)

    schema = await _get(connection, ROUTE_OPENAPI, transport=transport)
    paths = (
        schema.payload.get("paths")
        if schema.status_code == 200 and isinstance(schema.payload, dict)
        else None
    )
    if isinstance(paths, dict):
        capabilities = _capabilities_from_schema(paths)

    train_status = await _get(connection, ROUTE_TRAIN_STATUS, transport=transport)
    if train_status.status_code == 200 and isinstance(train_status.payload, dict):
        raw_state, normalized, active = _job_state(train_status.payload)
        job = {"state": raw_state, "normalized": normalized, "active": active}
        if not isinstance(paths, dict):
            capabilities[CAPABILITY_TRAINING].update(
                {
                    "state": CAPABILITY_SUPPORTED,
                    "detail": f"{ROUTE_TRAIN_STATUS} answered a read-only probe",
                }
            )
    elif train_status.status_code in (404, 405):
        if not isinstance(paths, dict):
            capabilities[CAPABILITY_TRAINING] = {
                "state": CAPABILITY_UNSUPPORTED,
                "detail": "the runtime exposes no training status route",
                "route": ROUTE_TRAIN_START,
            }
    if not isinstance(paths, dict):
        capabilities[CAPABILITY_CONVERSION].update(
            {
                "state": CAPABILITY_UNKNOWN,
                "detail": "the runtime exposed no OpenAPI schema, so export/convert "
                "routes could not be verified",
            }
        )

    models_response = await _get(connection, ROUTE_MODELS, transport=transport)
    if models_response.status_code == 200:
        models = _model_sample(models_response.payload)

    inaccessible = (
        system_payload is None
        and not isinstance(paths, dict)
        and train_status.status_code in (401, 403)
        and models_response.status_code in (401, 403)
    )
    if inaccessible:
        raise UnslothError(STATE_INCOMPATIBLE, INCOMPATIBLE_MESSAGE)

    training_capability = capabilities[CAPABILITY_TRAINING]
    if training_capability["state"] == CAPABILITY_SUPPORTED:
        if resources["state"] == STATE_INSUFFICIENT:
            training_capability.update(
                {"state": CAPABILITY_UNAVAILABLE, "reason": STATE_INSUFFICIENT}
            )
        elif job["active"]:
            training_capability.update({"state": CAPABILITY_UNAVAILABLE, "reason": STATE_BUSY})

    unsupported = [
        name for name in CAPABILITY_ORDER if capabilities[name]["state"] == CAPABILITY_UNSUPPORTED
    ]

    state = STATE_ONLINE
    message = ONLINE_MESSAGE
    if job["active"]:
        state = STATE_BUSY
        message = BUSY_MESSAGE
    elif resources["state"] == STATE_INSUFFICIENT:
        state = STATE_INSUFFICIENT
        message = INSUFFICIENT_MESSAGE

    latency_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": state == STATE_ONLINE,
        "state": state,
        "reason": "" if state == STATE_ONLINE else state,
        "message": message,
        "contract": CONTRACT_ID,
        "adapter_version": ADAPTER_VERSION,
        "base_url": base_url,
        "token_configured": bool(connection.get("token")),
        "runtime": {
            "state": "online" if state != STATE_OFFLINE else STATE_OFFLINE,
            "version": runtime_version,
            "latency_ms": latency_ms,
        },
        "capabilities": capabilities,
        "unsupported": unsupported,
        "job": job,
        "resources": resources,
        "models": models,
        "model_endpoint": _resolve_model_endpoint_safe(connection.get("model_endpoint_id")),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _failure_payload(connection: dict[str, Any] | None, exc: UnslothError) -> dict[str, Any]:
    base_url = str((connection or {}).get("base_url") or "")
    return {
        "ok": False,
        "state": exc.code,
        "reason": exc.code,
        "message": exc.message,
        "contract": CONTRACT_ID,
        "adapter_version": ADAPTER_VERSION,
        "base_url": base_url,
        "token_configured": bool((connection or {}).get("token")),
        "runtime": {"state": exc.code, "version": None, "latency_ms": None},
        "capabilities": None,
        "unsupported": [],
        "job": {"state": "unknown", "normalized": "unknown", "active": False},
        "resources": {"state": "unknown", "gpus": None, "detail": ""},
        "models": None,
        "model_endpoint": _resolve_model_endpoint_safe(
            (connection or {}).get("model_endpoint_id")
        ),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def test_connection(
    owner: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Health + capability discovery. Read-only; never starts a job."""
    try:
        connection = get_connection(owner)
    except UnslothError as exc:
        if exc.code == STATE_UNCONFIGURED:
            return {"configured": False, **_failure_payload(None, exc)}
        raise
    if not connection["enabled"]:
        failure = _failure_payload(
            connection, UnslothError(STATE_DISABLED, DISABLED_MESSAGE, status_code=409)
        )
        _record_discovery(
            owner,
            connection["id"],
            status=STATE_DISABLED,
            reason=STATE_DISABLED,
            message=DISABLED_MESSAGE,
            capabilities=None,
            runtime_version=None,
            job_state=None,
        )
        return {"configured": True, **failure}
    try:
        result = await discover_runtime(connection, transport=transport)
    except UnslothError as exc:
        result = _failure_payload(connection, exc)
    capabilities = result.get("capabilities")
    job = result.get("job") or {}
    runtime = result.get("runtime") or {}
    _record_discovery(
        owner,
        connection["id"],
        status=result["state"],
        reason=result.get("reason") or "",
        message=result.get("message") or "",
        capabilities=capabilities,
        runtime_version=runtime.get("version"),
        job_state=job.get("normalized"),
        resources=result.get("resources"),
    )
    return {"configured": True, **result}


# ── job control (explicit, reviewed path) ────────────────────────────────
#
# MAD-797: these are the only functions that mutate runtime state. Discovery
# never calls them; their only caller is src/training_jobs.py, which owns the
# preview, confirmation-fingerprint, acknowledgment, and dataset-review gates.

POST_TIMEOUT_SECONDS = 20.0
ROUTE_TRAIN_STOP = "/api/train/stop"
ROUTE_TRAIN_METRICS = "/api/train/metrics"
MAX_JOB_ID_CHARS = 128
MAX_METRICS_FIELDS = 40
MAX_METRICS_STRING_CHARS = 200
MAX_CHECKPOINTS = 50
MAX_CHECKPOINT_PATH_CHARS = 400
MAX_LOG_CHARS = 8000

START_REJECTED_MESSAGE = "The runtime rejected the training start request."
STOP_REJECTED_MESSAGE = "The runtime rejected the stop request."
NO_ACTIVE_JOB_MESSAGE = "The runtime reports no active job to stop."
JOB_ROUTE_UNAVAILABLE_MESSAGE = (
    "The runtime does not expose the training job routes this adapter needs."
)


async def _post(
    connection: dict[str, Any],
    path: str,
    payload: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = POST_TIMEOUT_SECONDS,
) -> _ReadResponse:
    """One bounded, redirect-free POST. Only job control may call this."""
    url = f"{connection['base_url']}{path}"
    headers = {"Accept": "application/json"}
    token = connection.get("token") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if 300 <= response.status_code < 400:
                    raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_RESPONSE_BYTES:
                        raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
                    chunks.append(chunk)
                body = b"".join(chunks)
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                status_code = response.status_code
    except UnslothError:
        raise
    except httpx.TimeoutException as exc:
        raise UnslothError(STATE_OFFLINE, TIMEOUT_MESSAGE) from exc
    except httpx.RequestError as exc:
        raise UnslothError(STATE_OFFLINE, OFFLINE_MESSAGE) from exc
    parsed = None
    if body:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
    return _ReadResponse(
        status_code=status_code,
        payload=parsed,
        media_type=media_type,
        truncated=False,
    )


def _raise_job_status(response: _ReadResponse, *, rejected_message: str) -> dict[str, Any]:
    if response.status_code in (401, 403):
        raise UnslothError(STATE_UNAUTHORIZED, UNAUTHORIZED_MESSAGE, status_code=401)
    if response.status_code == 404 or response.status_code == 405:
        raise UnslothError(
            STATE_INCOMPATIBLE, JOB_ROUTE_UNAVAILABLE_MESSAGE, status_code=502
        )
    if response.status_code == 409:
        raise UnslothError(STATE_BUSY, NO_ACTIVE_JOB_MESSAGE, status_code=409)
    if response.status_code == 507:
        raise UnslothError(STATE_INSUFFICIENT, INSUFFICIENT_MESSAGE, status_code=507)
    if response.status_code >= 400:
        raise UnslothError(STATE_ERROR, rejected_message, status_code=response.status_code)
    return response.payload if isinstance(response.payload, dict) else {}


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > MAX_METRICS_STRING_CHARS or _CONTROL_RE.search(text):
            return None
        try:
            from src.authority_protocol import redact_secret_text

            return redact_secret_text(text)
        except Exception:
            return text
    return None


def _percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:
        number *= 100.0
    return round(max(0.0, min(100.0, number)), 2)


def _progress_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    progress: dict[str, Any] = {}
    for key in ("percent", "progress", "progress_percent", "percentage"):
        percent = _percent(payload.get(key))
        if percent is not None:
            progress["percent"] = percent
            break
    step = payload.get("step", payload.get("current_step"))
    total = payload.get("total_steps", payload.get("max_steps"))
    if isinstance(step, (int, float)) and not isinstance(step, bool):
        progress["step"] = int(step)
    if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
        progress["total_steps"] = int(total)
        if "percent" not in progress and progress.get("step") is not None:
            progress["percent"] = round(min(100.0, progress["step"] / total * 100.0), 2)
    epoch = payload.get("epoch", payload.get("current_epoch"))
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        progress["epoch"] = float(epoch)
    for key in ("loss", "eta_seconds", "elapsed_seconds"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            progress[key] = value
    return progress


def _checkpoint_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("checkpoints")
    if not isinstance(raw, list):
        return []
    checkpoints: list[dict[str, Any]] = []
    for entry in raw[:MAX_CHECKPOINTS]:
        if isinstance(entry, dict):
            checkpoint: dict[str, Any] = {}
            for key in ("id", "name", "step", "path", "created_at"):
                value = _safe_scalar(entry.get(key))
                if value is not None and not (isinstance(value, str) and len(value) > MAX_CHECKPOINT_PATH_CHARS):
                    checkpoint[key] = value
            if checkpoint:
                checkpoints.append(checkpoint)
        elif isinstance(entry, str) and entry.strip() and len(entry) <= MAX_CHECKPOINT_PATH_CHARS:
            checkpoints.append({"id": entry.strip()})
    return checkpoints


def _log_tail(payload: Any, previous: str = "") -> str:
    if not isinstance(payload, dict):
        return str(previous or "")[-MAX_LOG_CHARS:]
    raw = None
    for key in ("logs", "log", "log_tail", "message", "last_message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            raw = value
            break
        if isinstance(value, list) and value:
            raw = "\n".join(str(item) for item in value if isinstance(item, str))
            break
    if raw is None:
        return str(previous or "")[-MAX_LOG_CHARS:]
    try:
        from src.authority_protocol import redact_secret_text

        raw = redact_secret_text(raw)
    except Exception:
        pass
    combined = f"{previous or ''}\n{raw}".strip() if previous else raw
    return combined[-MAX_LOG_CHARS:]


def _metrics_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    source = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload
    metrics: dict[str, Any] = {}
    for key, value in list(source.items())[:MAX_METRICS_FIELDS]:
        name = str(key)[:60]
        scalar = _safe_scalar(value)
        if scalar is not None:
            metrics[name] = scalar
    return metrics


async def fetch_training_status(
    connection: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    previous_logs: str = "",
) -> dict[str, Any]:
    """Read-only job snapshot: state, progress, checkpoints, metrics, logs."""
    response = await _get(connection, ROUTE_TRAIN_STATUS, transport=transport)
    payload = _raise_job_status(response, rejected_message=JOB_ROUTE_UNAVAILABLE_MESSAGE)
    raw_state, normalized, active = _job_state(payload)
    return {
        "raw_state": raw_state,
        "state": normalized,
        "active": active,
        "progress": _progress_summary(payload),
        "checkpoints": _checkpoint_list(payload),
        "logs": _log_tail(payload, previous_logs),
        "metrics": _metrics_summary(payload),
    }


async def fetch_training_metrics(
    connection: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Read-only metrics read, tolerant of runtimes without the route."""
    response = await _get(connection, ROUTE_TRAIN_METRICS, transport=transport)
    if response.status_code in (404, 405):
        return {}
    payload = _raise_job_status(response, rejected_message=JOB_ROUTE_UNAVAILABLE_MESSAGE)
    return _metrics_summary(payload)


async def start_training_job(
    connection: dict[str, Any],
    spec: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST /api/train/start for one already-confirmed spec.

    This is the only start path. Callers must be the reviewed jobs module with
    a matching preview fingerprint and an explicit operator acknowledgment.
    """
    if not isinstance(spec, dict) or not spec:
        raise UnslothError(STATE_INVALID, INVALID_MESSAGE)
    response = await _post(connection, ROUTE_TRAIN_START, spec, transport=transport)
    payload = _raise_job_status(response, rejected_message=START_REJECTED_MESSAGE)
    runtime_job_id = _first_text(payload, ("job_id", "jobId", "run_id", "id"))
    if runtime_job_id and len(runtime_job_id) > MAX_JOB_ID_CHARS:
        runtime_job_id = runtime_job_id[:MAX_JOB_ID_CHARS]
    raw_state, normalized, active = _job_state(payload)
    state = normalized if normalized != "unknown" else "running"
    return {
        "accepted": True,
        "runtime_job_id": runtime_job_id,
        "state": state,
        "raw_state": raw_state,
        "active": active if normalized != "unknown" else True,
    }


async def stop_training_job(
    connection: dict[str, Any],
    *,
    save: bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST /api/train/stop. ``save`` decides whether a checkpoint is retained."""
    response = await _post(
        connection, ROUTE_TRAIN_STOP, {"save": bool(save)}, transport=transport
    )
    if response.status_code in (404, 405):
        raise UnslothError(STATE_INCOMPATIBLE, JOB_ROUTE_UNAVAILABLE_MESSAGE, status_code=502)
    if response.status_code == 409:
        raise UnslothError("no_active_job", NO_ACTIVE_JOB_MESSAGE, status_code=409)
    payload = _raise_job_status(response, rejected_message=STOP_REJECTED_MESSAGE)
    raw_state, normalized, active = _job_state(payload)
    return {
        "state": normalized if normalized != "unknown" else "stopped",
        "raw_state": raw_state,
        "active": active,
    }


def cached_capabilities(owner: str | None) -> dict[str, Any]:
    """The last recorded discovery, without dialing the runtime again."""
    try:
        connection = get_connection(owner)
    except UnslothError as exc:
        if exc.code == STATE_UNCONFIGURED:
            return {
                "configured": False,
                "status": STATE_UNCONFIGURED,
                "checked_at": None,
                "capabilities": None,
                "contract": CONTRACT_ID,
            }
        raise
    return {
        "configured": True,
        "status": connection["status"],
        "checked_at": connection["last_checked_at"],
        "message": connection["last_message"],
        "runtime_version": connection["runtime_version"],
        "job_state": connection["job_state"],
        "capabilities": connection["capabilities"],
        "contract": connection["contract"] or CONTRACT_ID,
        "adapter_version": ADAPTER_VERSION,
    }