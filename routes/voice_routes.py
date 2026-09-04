"""Live voice session and safe action bridge routes."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.constants import DATA_DIR
from core.atomic_io import atomic_write_json
from core.middleware import require_admin
from core.models import ChatMessage
from src.agent_loop import stream_agent_loop
from src.agent_identity import agent_system_prompt, configured_agent_id, configured_agent_name
from src.agent_worker_adapters import WORKER_IDS
from src.agent_worker_broker import worker_statuses
from src.action_protocol import compose_capability_catalog, normalize_action_call, validate_action_call
from src.action_intents import classify_tool_intent
from src.authority_protocol import authority_store, operator_identity
from src.operational_protocol import record_operational_event
from src.agent_tools import TOOL_TAGS
from src.agent_worker_adapters import worker_catalog
from src.auth_helpers import require_user
from src import chat_helpers as _chat_helpers
from src import document_processor as _document_processor
from src.endpoint_resolver import resolve_endpoint, resolve_endpoint_by_id
from src.extension_host import extension_runtime_host
from src.extension_mcp_adapter import execute_mcp_extension_tool, mcp_extension_tool_specs
from src.extension_registry import EXTENSION_ID_PATTERN, ExtensionRegistry
from src.llm_core import llm_call_async
from src.settings import load_settings
from src.tools.calendar import do_read_calendar
from src.user_time import clear_user_time_context, now_user_local, set_user_tz_name, set_user_tz_offset
from src.voice_pcm import TTS_INFERENCE_LOCK, pcm_frames, speech_blocks, speech_text, wav_to_pcm16

VOICE_STATE_FILE = Path(DATA_DIR) / "voice_sessions.json"
ACTION_BRIDGE_URL = os.getenv("ODYSSEUS_ACTION_BRIDGE_URL", "http://127.0.0.1:8010/actions")
ORACLE_PROTOCOL_URL = (
    os.getenv("ODYSSEUS_ORACLE_URL", "").strip()
    or extension_runtime_host.urls.get("oracle", "")
)
VOICE_NORMAL_NUM_PREDICT = int(os.getenv("ODYSSEUS_VOICE_NUM_PREDICT", "1200"))
VOICE_LONG_NUM_PREDICT = int(os.getenv("ODYSSEUS_VOICE_LONG_NUM_PREDICT", "2400"))
VOICE_CONTEXT_LENGTH = int(os.getenv("ODYSSEUS_VOICE_CONTEXT_LENGTH", "32768"))
VOICE_TTS_PREWARM_TIMEOUT_SECONDS = 30.0
VOICE_SERVER_TTS_ERROR = (
    "Voice Orb requires server-generated TTS. "
    "Enable an available local or endpoint TTS provider in Settings."
)
VOICE_EVENT_HEARTBEAT_SECONDS = 5.0
VOICE_FRAME_MAX_BYTES = 1024 * 1024
VOICE_FRAME_MAX_WIDTH = 1024
VOICE_FRAME_MAX_HEIGHT = 576
VOICE_CONTROL_MAX_CHARS = 280
VOICE_ENDPOINT_ID = os.getenv(
    "PANDAMONIUM_VOICE_ENDPOINT_ID",
    os.getenv("ODYSSEUS_VOICE_ENDPOINT_ID", ""),
).strip()
VOICE_MODEL = os.getenv(
    "PANDAMONIUM_VOICE_MODEL",
    os.getenv("ODYSSEUS_VOICE_MODEL", ""),
).strip()
VOICE_SETUP_STATUS_TIMEOUT_SECONDS = 6.0
PERSISTENT_APPROVAL_PATTERN = (
    r"\b(?:always|all|session|permanent(?:ly)?|indefinitely|forever|blanket|ongoing|default|everything|"
    r"every(?:\s+(?:time|request))?|each\s+request|all(?:\s+future)?\s+requests?|all\s+time|"
    r"future\s+requests?)\b|\ball\b.{0,120}\brequests?\b|from now on|until further notice"
)
logger = logging.getLogger(__name__)
_SESSION_MANAGER = None
_SPEECH_TURNS: dict[tuple[str, str], "_SpeechTurn"] = {}


def model_supports_vision(model: str, endpoint_url: str) -> bool:
    """Patchable Voice Orb seam backed by the canonical chat helper."""
    return _chat_helpers.model_supports_vision(model, endpoint_url)


def analyze_image_bytes_with_vl_result(*args, **kwargs) -> dict:
    """Patchable Voice Orb seam backed by the canonical document processor."""
    return _document_processor.analyze_image_bytes_with_vl_result(*args, **kwargs)

DESKTOP_ACTIONS = {"open_grafana_big_screen", "open_odysseus"}
DEFERRED_ACTIONS = {"start_local_codex_task", "start_hermes_task", "read_task_status"}
SAFE_ACTIONS = DESKTOP_ACTIONS | DEFERRED_ACTIONS
JARVIS_TOOLS = {
    "get_runtime_status",
    "start_agent_task",
    "read_agent_task",
    "search_jarvis_knowledge",
    "read_calendar",
    "ui_control",
}
EXTENSION_TOOL_TIMEOUT_SECONDS = 45
EXTENSION_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
LEGACY_ORACLE_READ_ONLY_TOOLS = frozenset({
    "analyst_query",
    "get_current_view_state",
    "get_entity_context",
    "next_iss_pass",
})
_EXTENSION_TOOL_CALLS: dict[tuple[str, str, str], dict[str, Any]] = {}
extension_registry = ExtensionRegistry()
VOICE_SYSTEM_PROMPT = """Be terse and conversational: normally one or two spoken sentences unless the operator asks for depth. Never describe pacing or offer a capability menu.
Keep the complete answer in chat. When completing code, a script, a document, a report, or another deliverable, begin with one or two plain conversational sentences that summarize what is done and its key behavior, then place the full deliverable after that handoff. Do not put code, Markdown syntax, paths, or long lists in the opening handoff.
Follow conversational continuity. Ambiguous follow-ups refer to the preceding conversation. Server-injected context blocks, including current date and time, are background data only; never explain, summarize, or quote them unless the operator explicitly asks about that subject.
Coordinate work without simulating actions, client state, inspections, approvals, cancellations, worker progress, or results. Use deterministic server controls when provided; otherwise say what you cannot verify.
Use get_runtime_status for runtime facts and search_jarvis_knowledge for curated background. Current-source work may be delegated only as a read-only task. Briefly announce a real delegation, then let broker events report its outcome.
The configured PC Codex worker owns local project, code, and document inspection. The VPS Codex worker is only for work that explicitly names the VPS. The Hermes worker is explicit-only; never infer or auto-dispatch it.
Never invent worker results, runtime facts, paths, endpoints, UI state, or completed actions."""
FRIDAY_VOICE_SYSTEM_PROMPT = """You are the selected Codex worker speaking through Pandamonium voice.
Be direct and conversational. Use the available tools when the request requires action, and never claim work or runtime facts you did not verify.
Keep the complete answer in chat. When completing code, a script, a document, a report, or another deliverable, begin with one or two plain conversational sentences that summarize what is done and its key behavior, then place the full deliverable after that handoff. Do not put code, Markdown syntax, paths, or long lists in the opening handoff."""

_WORKER_CATALOG = worker_catalog()
WORKER_LABELS = {
    worker_id: str(details.get("label") or worker_id)
    for worker_id, details in _WORKER_CATALOG.items()
}
_LEGACY_WORKER_NAMES = {
    "pc codex": ("pc-codex", "PC Codex"),
    "hermes": ("hermes", "Hermes"),
    "vps codex": ("vps-codex", "VPS Codex"),
}
CHATGPT_SUBSCRIPTION_LABEL = (
    " ".join(os.getenv("ODYSSEUS_CHATGPT_SUBSCRIPTION_LABEL", "ChatGPT Subscription").split())[:80]
    or "ChatGPT Subscription"
)
VOICE_TARGET_LABELS = {**WORKER_LABELS, "friday": CHATGPT_SUBSCRIPTION_LABEL}
DIRECT_MODEL_TARGETS = {"jarvis", "friday"}
VOICE_TARGET_ENDPOINT_NAMES = {
    "jarvis": ("Jarvis",),
    "friday": ("Friday", "ChatGPT Subscription"),
}
ACTIVE_VOICE_TARGETS = DIRECT_MODEL_TARGETS | {
    worker for worker, details in worker_catalog().items() if details.get("enabled")
}
VOICE_WORKSPACES = {
    workspace
    for details in worker_catalog().values()
    for workspace in details.get("workspaces") or []
}


def _worker_command(text: str) -> tuple[str, str, str, str | None, str | None] | None:
    """Parse the original fixed Voice Orb worker command contract.

    The richer Jarvis dispatcher owns execution; this parser remains available
    for clients and tests that adopted the beta command grammar.
    """
    normalized = re.sub(r"[.!?]+$", "", text.strip(), flags=re.I).strip()
    cancel = re.fullmatch(r"cancel\s+(pc codex|hermes|vps codex)", normalized, flags=re.I)
    if cancel:
        worker, label = _LEGACY_WORKER_NAMES[cancel.group(1).lower()]
        return "cancel", worker, label, None, None
    start = re.fullmatch(
        r"ask\s+(pc codex|hermes|vps codex)(?:\s+in\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63}))?\s+to\s+(.+)",
        normalized,
        flags=re.I | re.S,
    )
    if not start:
        return None
    worker, label = _LEGACY_WORKER_NAMES[start.group(1).lower()]
    return "start", worker, label, start.group(2), start.group(3).strip()


def _operator_vocative() -> str:
    name = os.getenv("ODYSSEUS_OPERATOR_DISPLAY_NAME", "").strip()
    return f", {name}" if re.fullmatch(r"[^\s][^\r\n]{0,63}", name) else ""


class VoiceSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "jarvis_call"
    chat_session_id: str | None = None
    endpoint_id: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=500)


class VoiceTurnCreate(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    text: str
    status: str | None = None
    task_id: str | None = None


class VoiceActionRequest(BaseModel):
    action: str
    session_id: str | None = None
    prompt: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class VoiceCalendarClientState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: bool = False
    minimized: bool = False
    view: Literal["month", "week", "year", "agenda"] | None = None
    date: str | None = Field(default=None, max_length=40)


class VoiceDocumentClientState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open: bool = False
    minimized: bool = False
    id: str | None = Field(default=None, max_length=200)


class VoiceOracleCameraState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    heightM: float = Field(ge=-1000, le=100_000_000)


class VoiceOracleLayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=80)
    name: str = Field(max_length=120)
    enabled: bool = False
    count: int = Field(default=0, ge=0, le=10_000_000)
    error: str | None = Field(default=None, max_length=200)


class VoiceExtensionToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    description: str = Field(max_length=2000)
    parameters: dict[str, Any]


class VoiceExtensionCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(min_length=1, max_length=80)
    version: str = Field(max_length=80)
    tools: list[VoiceExtensionToolSpec] = Field(default_factory=list, max_length=64)


class VoiceExtensionClientState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool = False
    updated_at_ms: int = Field(default=0, ge=0)
    state: dict[str, Any] = Field(default_factory=dict)
    capabilities: VoiceExtensionCapabilities | None = None


class VoiceOracleClientState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool = False
    panel_open: bool = False
    updated_at_ms: int = Field(default=0, ge=0)
    style: Literal["normal", "retro", "surveillance", "thermal", "anime", "noir", "snow"] = "normal"
    camera: VoiceOracleCameraState | None = None
    layers: list[VoiceOracleLayerState] = Field(default_factory=list, max_length=64)
    capabilities: VoiceExtensionCapabilities | None = None


class VoiceClientState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_view: Literal["calendar", "document", "chat"] | None = None
    calendar: VoiceCalendarClientState = Field(default_factory=VoiceCalendarClientState)
    document: VoiceDocumentClientState = Field(default_factory=VoiceDocumentClientState)
    extensions: dict[str, VoiceExtensionClientState] = Field(default_factory=dict, max_length=16)
    oracle: VoiceOracleClientState | None = None


class VoiceFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mime: Literal["image/jpeg", "image/png"]
    data_base64: str = Field(min_length=4, max_length=1_500_000)
    width: int = Field(gt=0, le=VOICE_FRAME_MAX_WIDTH)
    height: int = Field(gt=0, le=VOICE_FRAME_MAX_HEIGHT)


class VoiceRespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    client_state: VoiceClientState | None = None
    frame: VoiceFrame | None = None


class VoiceExtensionToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    result: dict[str, Any]


class VoiceTargetUpdate(BaseModel):
    target: str
    workspace: str = "home-lab"
    task_id: str | None = None
    codex_thread_id: str | None = None


class VoiceDiagnosticCreate(BaseModel):
    label: str = "client_turn"
    timings: dict[str, Any] = Field(default_factory=dict)


class VoicePlaybackUpdate(BaseModel):
    state: Literal["started", "completed", "interrupted", "failed"]
    timings: dict[str, Any] = Field(default_factory=dict)


class _SpeechTurn:
    """One sentence-fed spoken payload consumed by the existing TTS stream."""

    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.turn_id = turn_id
        self.voice: str | None = None
        self.text = ""
        self.raw_text = ""
        self.pending_text = ""
        self.finished = False
        self.cancelled = False
        self.error: str | None = None
        self.created_at = time.monotonic()
        self.done = asyncio.Event()
        self.blocks: asyncio.Queue[str | None] = asyncio.Queue()

    def feed(self, delta: str) -> bool:
        if self.finished or not delta:
            return False
        self.raw_text += delta
        self.pending_text += delta
        matches = list(re.finditer(r"[.!?][\"')\]]*(?=\s|$)", self.pending_text))
        if not matches:
            return False
        cut = matches[-1].end()
        ready = speech_text(self.pending_text[:cut])
        self.pending_text = self.pending_text[cut:].lstrip()
        return self._queue_text(ready)

    def _queue_text(self, text: str) -> bool:
        queued = False
        for block in speech_blocks(text):
            self.blocks.put_nowait(block)
            self.text = f"{self.text} {block}".strip()
            queued = True
        return queued

    async def complete(self, text: str | None = None) -> None:
        if text is not None and not self.raw_text:
            self.raw_text = text
            self.pending_text = text
        self._queue_text(speech_text(self.pending_text))
        self.pending_text = ""
        self.finished = True
        self.done.set()
        self.blocks.put_nowait(None)

    async def fail(self, error: str) -> None:
        self.error = error
        self.finished = True
        self.done.set()
        self.blocks.put_nowait(None)

    async def cancel(self) -> None:
        self.cancelled = True
        self.finished = True
        self.done.set()
        self.blocks.put_nowait(None)

    async def iter_blocks(self):
        while True:
            block = await self.blocks.get()
            if block is None:
                break
            yield block
        if self.cancelled:
            raise RuntimeError("Voice playback was interrupted")
        if self.error:
            raise RuntimeError(self.error)
        if not self.text:
            raise RuntimeError("Jarvis produced no spoken response")

    async def wait(self) -> str:
        await self.done.wait()
        if self.cancelled:
            raise RuntimeError("Voice playback was interrupted")
        if self.error:
            raise RuntimeError(self.error)
        if not self.text:
            raise RuntimeError("Jarvis produced no spoken response")
        return self.text


async def _voice_events_with_heartbeats(
    events: AsyncGenerator[dict[str, Any], None],
) -> AsyncGenerator[dict[str, Any] | None, None]:
    """Keep the browser stream alive while a remote agent is using tools."""
    iterator = events.__aiter__()
    pending = asyncio.create_task(anext(iterator))
    try:
        while True:
            ready, _ = await asyncio.wait((pending,), timeout=VOICE_EVENT_HEARTBEAT_SECONDS)
            if not ready:
                yield None
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield event
            pending = asyncio.create_task(anext(iterator))
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


def _now() -> int:
    return int(time.time())


def _server_tts_readiness(tts_service) -> tuple[bool, str]:
    try:
        settings = load_settings()
    except Exception:
        return False, "disabled"
    provider = settings.get("tts_provider", "disabled")
    server_provider = provider == "local" or (
        isinstance(provider, str)
        and provider.startswith("endpoint:")
        and bool(provider.partition(":")[2].strip())
    )
    if settings.get("tts_enabled") is False or not server_provider or not tts_service:
        return False, str(provider)
    try:
        return bool(tts_service.available), str(provider)
    except Exception:
        return False, str(provider)


def _require_server_tts(tts_service) -> None:
    if not _server_tts_readiness(tts_service)[0]:
        raise HTTPException(
            status_code=503,
            detail={"code": "server_tts_required", "message": VOICE_SERVER_TTS_ERROR},
        )


def _load_state() -> dict:
    try:
        state = json.loads(VOICE_STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {"sessions": {}, "actions": {}}
    except Exception:
        return {"sessions": {}, "actions": {}}


def _save_state(state: dict) -> None:
    atomic_write_json(str(VOICE_STATE_FILE), state, indent=2)


def _set_voice_status(session_id: str, status: str, **fields: Any) -> dict:
    state = _load_state()
    session = _session(state, session_id)
    session["status"] = status
    session["updated_at"] = _now()
    session.update(fields)
    _save_state(state)
    return session


def _set_oracle_protocol_state(
    session_id: str,
    *,
    pending: bool | None = None,
    active: bool | None = None,
) -> dict:
    state = _load_state()
    session = _session(state, session_id)
    if pending is not None:
        session["oracle_protocol_pending"] = pending
    if active is not None:
        session["oracle_protocol_active"] = active
        _set_extension_engaged(session, "oracle", active)
    session["updated_at"] = _now()
    _save_state(state)
    return session


def _set_extension_engaged(session: dict[str, Any], extension_id: str, engaged: bool) -> None:
    current = {
        str(item)
        for item in session.get("engaged_extensions") or []
        if EXTENSION_ID_PATTERN.fullmatch(str(item))
    }
    if engaged:
        current.add(extension_id)
    else:
        current.discard(extension_id)
    session["engaged_extensions"] = sorted(current)


def _engaged_extension_ids(session: dict[str, Any]) -> set[str]:
    engaged = {
        str(item)
        for item in session.get("engaged_extensions") or []
        if EXTENSION_ID_PATTERN.fullmatch(str(item))
    }
    # Backward compatibility for sessions created before generic extension state.
    if session.get("oracle_protocol_active"):
        engaged.add("oracle")
    return engaged


def _extension_surface_configs() -> list[dict[str, str]]:
    """Return enabled installed web surfaces; registry state survives host restarts."""
    try:
        records = extension_registry.snapshot().get("extensions") or {}
    except Exception:
        return []
    surfaces = []
    for extension_id, record in records.items():
        manifest = record.get("manifest") if isinstance(record, dict) else None
        if (
            not record.get("enabled")
            or not isinstance(manifest, dict)
            or (manifest.get("runtime") or {}).get("type") != "web"
        ):
            continue
        try:
            url = extension_runtime_host.surface_url(manifest)
            parsed = urlparse(url)
        except Exception:
            continue
        surfaces.append({
            "extension_id": extension_id,
            "name": str(manifest.get("name") or extension_id)[:200],
            "url": url,
            "origin": f"{parsed.scheme}://{parsed.netloc}",
        })
    return sorted(surfaces, key=lambda item: item["extension_id"])


def _extension_browser_available(session: dict[str, Any], extension_id: str) -> bool:
    if extension_id not in _engaged_extension_ids(session):
        return False
    if any(item["extension_id"] == extension_id for item in _extension_surface_configs()):
        return True
    # Retain the unregistered ORACLE adapter until deployed equivalence is proven.
    return bool(
        extension_id == "oracle"
        and ORACLE_PROTOCOL_URL
        and session.get("oracle_protocol_active")
    )


def _register_speech_turn(session_id: str) -> _SpeechTurn:
    now = time.monotonic()
    for key, stale in list(_SPEECH_TURNS.items()):
        if stale.finished and now - stale.created_at > 600:
            _SPEECH_TURNS.pop(key, None)
    turn_id = str(uuid.uuid4())
    turn = _SpeechTurn(session_id, turn_id)
    _SPEECH_TURNS[(session_id, turn_id)] = turn
    return turn


def _session(state: dict, session_id: str) -> dict:
    session = state.get("sessions", {}).get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail={"message": "Voice session not found"})
    return session


def _owned_session(state: dict, session_id: str, owner: str) -> dict:
    session = _session(state, session_id)
    stored_owner = session.get("owner")
    if stored_owner is None:
        chat_owner = None
        chat_session_id = str(session.get("chat_session_id") or "")
        if chat_session_id and _SESSION_MANAGER:
            try:
                chat_owner = getattr(_SESSION_MANAGER.get_session(chat_session_id), "owner", None)
            except Exception:
                chat_owner = None
        if chat_owner == owner:
            session["owner"] = owner
            _save_state(state)
            stored_owner = owner
        else:
            raise HTTPException(status_code=403, detail={"message": "Voice session has no verified owner"})
    if stored_owner != owner:
        raise HTTPException(status_code=403, detail={"message": "Voice session does not belong to this user"})
    return session


def _owned_voice_session(
    session_id: str,
    owner: str,
    *,
    session_manager=None,
    request: Request | None = None,
) -> dict[str, Any]:
    """Resolve ownership for legacy sessions without allowing first-caller claims."""
    state = _load_state()
    session = _session(state, session_id)
    stored_owner = str(session.get("owner") or "").strip()
    if not stored_owner:
        linked_owner = ""
        chat_session_id = str(session.get("chat_session_id") or "").strip()
        if session_manager is not None and chat_session_id:
            try:
                linked = session_manager.get_session(chat_session_id)
            except (KeyError, AttributeError):
                linked = None
            linked_owner = str(getattr(linked, "owner", "") or "").strip()
        if linked_owner:
            session["owner"] = linked_owner
            session["updated_at"] = _now()
            _save_state(state)
            stored_owner = linked_owner
        else:
            if request is None:
                raise HTTPException(status_code=403, detail="Ownerless voice sessions are admin only")
            require_admin(request)
            return dict(session)
    if stored_owner != str(owner or "").strip():
        raise HTTPException(status_code=403, detail="Voice session belongs to another user")
    return dict(session)


def _resolve_voice_runtime(owner: str, linked_session=None) -> tuple[str, str, dict[str, str]]:
    """Resolve an owner-scoped override, then the linked or default chat model."""
    if VOICE_ENDPOINT_ID:
        resolved = resolve_endpoint_by_id(
            VOICE_ENDPOINT_ID,
            VOICE_MODEL or None,
            owner=owner or None,
        )
        if not resolved:
            raise HTTPException(status_code=503, detail="Configured voice model endpoint is unavailable")
        url, model, headers = resolved
        return url, model, headers or {}
    if linked_session is not None:
        url = str(getattr(linked_session, "endpoint_url", "") or "").strip()
        model = VOICE_MODEL or str(getattr(linked_session, "model", "") or "").strip()
        if url and model:
            return url, model, dict(getattr(linked_session, "headers", {}) or {})
    url, model, headers = resolve_endpoint("default", owner=owner or None)
    model = VOICE_MODEL or str(model or "").strip()
    if not url or not model:
        raise HTTPException(status_code=503, detail="No default chat model is configured")
    return url, model, headers or {}


def _append_turn(session: dict, role: str, text: str, status: str, task_id: str | None = None) -> dict:
    turn = {
        "id": str(uuid.uuid4()),
        "role": role,
        "text": text,
        "status": status,
        "task_id": task_id,
        "created_at": _now(),
    }
    session.setdefault("turns", []).append(turn)
    session["status"] = status
    session["updated_at"] = _now()
    return turn


def _detect_safe_action(text: str) -> str | None:
    lowered = text.lower()
    if "grafana" in lowered and ("open" in lowered or "pull up" in lowered or "show" in lowered):
        if "big screen" in lowered or "screen" in lowered or "dashboard" in lowered or "dash" in lowered:
            return "open_grafana_big_screen"
    if "open odysseus" in lowered or "pull up odysseus" in lowered:
        return "open_odysseus"
    return None


async def _execute_action(payload: VoiceActionRequest, owner: str) -> dict:
    action = payload.action.strip()
    if action not in SAFE_ACTIONS:
        raise HTTPException(status_code=403, detail={"message": "action_not_allowed", "action": action})

    task = {
        "task_id": str(uuid.uuid4()),
        "action": action,
        "session_id": payload.session_id,
        "owner": owner,
        "status": "queued",
        "prompt": payload.prompt,
        "created_at": _now(),
        "updated_at": _now(),
    }

    if action in DESKTOP_ACTIONS:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(ACTION_BRIDGE_URL, json={"action": action, "args": payload.args})
            if response.status_code >= 400:
                task.update({"status": "failed", "bridge_status": response.status_code, "bridge_response": response.text[:500]})
            else:
                bridge_payload = response.json()
                task.update({"status": bridge_payload.get("status", "started"), "bridge_task_id": bridge_payload.get("task_id")})
        except Exception as exc:
            task.update({"status": "failed", "error": str(exc)})
    else:
        from src.jarvis_agent import refresh_task, start_task

        if action == "read_task_status":
            requested_task_id = str(payload.args.get("task_id") or "")
            if not requested_task_id:
                task.update({"status": "blocked", "reason": "task_id_required"})
            else:
                try:
                    return await refresh_task(requested_task_id, owner=owner)
                except KeyError:
                    task.update({"status": "failed", "reason": "task_not_found"})
        else:
            worker = "pc-codex" if action == "start_local_codex_task" else "hermes"
            workspace = str(payload.args.get("workspace") or "home-lab")
            voice_state = _load_state()
            voice_session = (voice_state.get("sessions") or {}).get(payload.session_id or "") or {}
            chat_session_id = str(voice_session.get("chat_session_id") or payload.session_id or "")
            try:
                return await start_task(
                    worker,
                    chat_session_id,
                    workspace,
                    payload.prompt or "Inspect the requested work and report back.",
                    "read_only",
                    False,
                    owner,
                )
            except Exception as exc:
                task.update({"status": "failed", "reason": str(exc)[:240]})

    return task


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.IGNORECASE).strip()


def _set_user_time_from_request(request: Request) -> None:
    clear_user_time_context()
    set_user_tz_offset(request.headers.get("x-tz-offset"))
    set_user_tz_name(request.headers.get("x-tz-name"))


def _asks_read_all(text: str) -> bool:
    return bool(re.search(
        r"\b(?:read|speak|say)\s+(?:it\s+all|all\s+of\s+it|everything|the\s+(?:whole|full)\s+(?:thing|response|answer))\b",
        text,
        re.IGNORECASE,
    ))


def _bounded_spoken_text(text: str, limit: int = 1200) -> str:
    paragraphs = [re.sub(r"\s*\n\s*", " ", part).strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    bounded = "\n\n".join(paragraphs[:3])
    if len(bounded) <= limit:
        return bounded
    cut = bounded.rfind(" ", 0, limit + 1)
    bounded = bounded[:cut if cut > limit // 2 else limit].rstrip(" ,;:-")
    if bounded.endswith((".", "!", "?")) or len(bounded) >= limit:
        return bounded
    return bounded + "."


def _requested_artifact_kind(prompt: str) -> str | None:
    if not re.search(
        r"\b(?:write|draft|create|build|generate|prepare|make|produce|compose|implement|update|edit|revise|rewrite)\b",
        prompt,
        re.IGNORECASE,
    ):
        return None
    kinds = (
        ("script", r"\b(?:script|program)\b"),
        ("code", r"\b(?:code|function|class|module)\b"),
        ("configuration", r"\b(?:config|configuration|dockerfile|compose file)\b"),
        ("query", r"\b(?:query|sql)\b"),
        ("email", r"\bemail\b"),
        ("document", r"\b(?:document|report|paper|proposal|article|post|readme)\b"),
        ("plan", r"\b(?:plan|checklist|workflow)\b"),
        ("table", r"\b(?:table|spreadsheet)\b"),
        ("presentation", r"\b(?:presentation|slide deck|slides)\b"),
    )
    return next((kind for kind, pattern in kinds if re.search(pattern, prompt, re.IGNORECASE)), None)


def _structured_artifact_kind(response_text: str) -> str | None:
    if re.search(r"(?m)^\s*(?:```|~~~)", response_text):
        return "code"
    if re.search(r"(?m)^\s*\|.+\|\s*$\n\s*\|\s*:?-{3,}", response_text):
        return "table"
    if len(re.findall(r"(?m)^\s*[-*+]\s+\[[ xX]\]\s+", response_text)) >= 2:
        return "checklist"
    if len(re.findall(r"(?m)^\s*#{1,6}\s+", response_text)) >= 2:
        return "document"
    return None


def _artifact_spoken_handoff(prompt: str, response_text: str) -> str | None:
    requested_kind = _requested_artifact_kind(prompt)
    kind = requested_kind or _structured_artifact_kind(response_text)
    if not kind:
        return None

    boundary = re.search(
        r"(?m)^\s*(?:```|~~~|#{1,6}\s+|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|\|.+\|\s*$)",
        response_text,
    )
    lead = response_text[:boundary.start()].strip() if boundary else ""
    lead = re.sub(r"`([^`]+)`", r"\1", lead)
    lead = re.sub(r"[*_~]+", "", lead)
    lead = " ".join(lead.split())
    if len(lead) >= 60:
        return _bounded_spoken_text(lead, 420)

    if requested_kind:
        return f"I finished the {requested_kind}. It's in the chat for you to review."
    return f"I put the complete {kind} in the chat for you to review."


async def _select_spoken_text(prompt: str, response_text: str) -> str:
    response_text = response_text.strip()
    if _asks_read_all(prompt):
        return speech_text(response_text)
    artifact_handoff = _artifact_spoken_handoff(prompt, response_text)
    if artifact_handoff:
        return speech_text(artifact_handoff)
    return speech_text(response_text)


async def _spoken_text_for_final(prompt: str, final: dict[str, Any]) -> str:
    response_text = str(final["assistant_text"]).strip()
    return await _select_spoken_text(prompt, response_text)


def _tts_voice_for_final(final: dict[str, Any]) -> str | None:
    character = str((final.get("diagnostics") or {}).get("character_name") or "")
    voices = load_settings().get("tts_agent_voices") or {}
    if not isinstance(voices, dict):
        return None
    voice = voices.get(character)
    return str(voice).strip() if isinstance(voice, str) and voice.strip() else None


def _voice_character_name(voice_session: dict[str, Any]) -> str:
    return VOICE_TARGET_LABELS.get(str(voice_session.get("target") or "jarvis"), configured_agent_name())


def _voice_system_prompt(voice_session: dict[str, Any]) -> str:
    if voice_session.get("target") in {"friday", "pc-codex"}:
        return FRIDAY_VOICE_SYSTEM_PROMPT
    prompt = agent_system_prompt(VOICE_SYSTEM_PROMPT)
    agent_name = configured_agent_name()
    if not voice_session.get("oracle_protocol_active"):
        return prompt + f"\nORACLE protocol is offline. You are {agent_name}; ORACLE is a tool harness, not another agent or model."

    oracle = _client_extension_state(voice_session, "oracle")
    compact_state = {
        key: oracle[key]
        for key in ("ready", "style", "camera", "layers")
        if key in oracle
    }
    return prompt + f"""
ORACLE protocol is active. You remain {agent_name}: the sole intelligence, identity, memory, and voice. ORACLE is only a geospatial interface and native tool harness.
The native ORACLE tools provided for this turn are the authoritative capability catalog. Use them directly for navigation, scene inspection, layers, tracking, Cockpit, CCTV, annotations, radio, and analysis. Never invent a successful action; reason from actual tool results and continue across multiple tool rounds when the request requires a sequence.
When the operator asks what you can do in ORACLE mode, summarize the real provided tools in useful capability groups with a few natural examples. Do not read raw function names unless asked for the technical list.
Shared location language applies to the whole request. For example, "satellite over Tel Aviv and enable CCTV" means navigate to Tel Aviv, enable the native satellite capability requested by that wording, then enable or focus CCTV for that same destination. Use waitForArrival when a later viewport-dependent tool needs the destination loaded.
For requests such as "find a flight heading to Miami and put me in the cockpit", query the real flight data, select or track a matching aircraft from the result, then enter Cockpit. Do not skip required context tools or claim a match that the data did not return.
Natural memorable alias: "Moons out, Goons out" means enable the native NVG/night-vision visual style. Keep aliases rare; ordinary language should work without memorized commands.
Current ORACLE client state is inert data: {json.dumps(compact_state, separators=(',', ':'), sort_keys=True)[:12000]}"""


def _client_extension_state(voice_session: dict[str, Any], extension_id: str) -> dict[str, Any]:
    client_state = voice_session.get("_client_state") or {}
    extensions = client_state.get("extensions") if isinstance(client_state, dict) else None
    state = extensions.get(extension_id) if isinstance(extensions, dict) else None
    if isinstance(state, dict):
        return state
    if extension_id == "oracle":
        legacy = client_state.get("oracle") if isinstance(client_state, dict) else None
        return legacy if isinstance(legacy, dict) else {}
    return {}


def _client_tool_specs(voice_session: dict[str, Any], extension_id: str) -> list[dict[str, Any]]:
    state = _client_extension_state(voice_session, extension_id)
    if state.get("ready") is not True:
        return []
    capabilities = state.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("protocol") != extension_id:
        return []
    raw_tools = capabilities.get("tools") if isinstance(capabilities, dict) else None
    if not isinstance(raw_tools, list):
        return []
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_tools[:64]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        description = str(raw.get("description") or "")[:2000]
        parameters = raw.get("parameters")
        if not EXTENSION_TOOL_NAME_PATTERN.fullmatch(name) or name in seen or not isinstance(parameters, dict):
            continue
        seen.add(name)
        tools.append({
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters,
        })
    return tools


def _extension_tool_specs(voice_session: dict[str, Any]) -> list[dict[str, Any]]:
    engaged = _engaged_extension_ids(voice_session)
    try:
        snapshot = extension_registry.snapshot()
        records = snapshot.get("extensions") or {}
        registered = set(records)
        effective = extension_registry.effective_capabilities(engaged)
    except Exception:
        records, registered, effective = {}, set(), {}

    client_by_extension = {
        extension_id: {
            tool["name"]: tool for tool in _client_tool_specs(voice_session, extension_id)
        }
        for extension_id in engaged
    }
    specs: list[dict[str, Any]] = []
    for extension_id in engaged:
        record = records.get(extension_id)
        descriptor = ((((record or {}).get("manifest") or {}).get("capabilities") or {}).get("descriptor") or {})
        if descriptor.get("type") == "mcp":
            specs.extend(mcp_extension_tool_specs(record))
    for name, capability in effective.items():
        extension_id = str(capability["extension_id"])
        descriptor = ((((records.get(extension_id) or {}).get("manifest") or {}).get("capabilities") or {}).get("descriptor") or {})
        if descriptor.get("type") == "mcp":
            continue
        if name not in client_by_extension.get(extension_id, {}):
            continue
        function = capability["schema"]["function"]
        specs.append({
            "type": "function",
            "name": name,
            "description": function.get("description", ""),
            "parameters": function["parameters"],
            "extension_id": extension_id,
            "permission_mode": capability["permission_mode"],
        })

    # Keep the pre-registry ORACLE bridge working until a real installed revision
    # proves equivalent; a registered-but-disabled extension never falls back.
    if "oracle" in engaged and "oracle" not in registered:
        specs.extend({
            **tool,
            "extension_id": "oracle",
            "permission_mode": (
                "read_only"
                if tool["name"] in LEGACY_ORACLE_READ_ONLY_TOOLS
                else "external_side_effect"
            ),
        } for tool in client_by_extension.get("oracle", {}).values())
    return specs


def _extension_tool_schemas(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tool_specs
    ]


def _extension_context(
    voice_session: dict[str, Any], tool_specs: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    names: dict[str, list[str]] = {}
    for tool in tool_specs:
        extension_id = tool["extension_id"]
        names.setdefault(extension_id, []).append(tool["name"])
    return {
        extension_id: {
            "engaged": True,
            "state_mounted": True,
            "tool_count": len(tool_names),
            "tool_names": tool_names,
        }
        for extension_id, tool_names in names.items()
    }


def _extension_tool_executor(
    voice_session: dict[str, Any], owner: str, tool_specs: list[dict[str, Any]]
):
    allowed = {tool["name"]: tool for tool in tool_specs}
    voice_session_id = str(voice_session.get("id") or "")

    async def execute(block, progress_cb):
        spec = allowed.get(block.tool_type)
        if not spec:
            return None
        extension_id = spec["extension_id"]
        label = extension_id.upper()
        try:
            arguments = json.loads(block.content or "{}")
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            return (
                f"{label} {block.tool_type}",
                {"ok": False, "action": block.tool_type, "error": f"{label} tool arguments must be a JSON object"},
            )
        if spec.get("mcp_qualified_name"):
            try:
                record = extension_registry.snapshot()["extensions"].get(extension_id)
            except Exception:
                record = None
            if extension_id not in _engaged_extension_ids(voice_session) or not record:
                return (
                    f"{label} {block.tool_type}",
                    {"error": "extension_mcp_capability_unavailable", "exit_code": 1},
                )
            result = await execute_mcp_extension_tool(
                record, block.tool_type, arguments
            )
            return f"{label} {block.tool_type}", result
        if not voice_session_id:
            return (
                f"{label} {block.tool_type}",
                {"ok": False, "action": block.tool_type, "error": f"{label} voice session is unavailable"},
            )
        if not _extension_browser_available(voice_session, extension_id):
            return (
                f"{label} {block.tool_type}",
                {"ok": False, "action": block.tool_type, "error": f"{label} browser surface is unavailable"},
            )

        call_id = f"extension_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        key = (voice_session_id, extension_id, call_id)
        _EXTENSION_TOOL_CALLS[key] = {
            "future": future,
            "owner": owner,
            "tool": block.tool_type,
        }
        try:
            await progress_cb({
                "extension_call": {
                    "call_id": call_id,
                    "extension_id": extension_id,
                    "tool": block.tool_type,
                    "arguments": arguments,
                },
            })
            result = await asyncio.wait_for(future, timeout=EXTENSION_TOOL_TIMEOUT_SECONDS)
            return f"{label} {block.tool_type}", result
        except asyncio.TimeoutError:
            return (
                f"{label} {block.tool_type}",
                {"ok": False, "action": block.tool_type, "error": f"{label} did not return a tool result in time"},
            )
        finally:
            _EXTENSION_TOOL_CALLS.pop(key, None)

    return execute


def prepare_text_extension_bridge(
    session_id: str,
    chat_session_id: str,
    owner: str,
    extension_id: str,
    client_state: dict[str, Any],
) -> dict[str, Any]:
    """Mount a validated browser extension surface into one text-chat turn."""
    if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise HTTPException(status_code=400, detail={"message": "Invalid extension ID"})
    state = _load_state()
    session = _owned_session(state, session_id, owner)
    if str(session.get("chat_session_id") or "") != str(chat_session_id or ""):
        raise HTTPException(status_code=409, detail={"message": "Extension bridge is linked to a different chat"})

    validated = VoiceClientState.model_validate(client_state).model_dump(exclude_none=True)
    extension_state = (validated.get("extensions") or {}).get(extension_id)
    if not isinstance(extension_state, dict) and extension_id == "oracle":
        extension_state = validated.get("oracle")
    capabilities = extension_state.get("capabilities") if isinstance(extension_state, dict) else None
    if (
        not isinstance(extension_state, dict)
        or extension_state.get("ready") is not True
        or not isinstance(capabilities, dict)
        or capabilities.get("protocol") != extension_id
    ):
        raise HTTPException(
            status_code=409,
            detail={"message": f"{extension_id.upper()} browser tool catalog is not ready"},
        )

    session["_client_state"] = validated
    _set_extension_engaged(session, extension_id, True)
    if extension_id == "oracle":
        session["oracle_protocol_pending"] = False
        session["oracle_protocol_active"] = True
    session["updated_at"] = _now()

    turn_session = dict(session)
    tool_specs = _extension_tool_specs(turn_session)
    requested_specs = [tool for tool in tool_specs if tool.get("extension_id") == extension_id]
    if not requested_specs:
        raise HTTPException(
            status_code=409,
            detail={"message": f"{extension_id.upper()} exposed no admitted native tools"},
        )
    _save_state(state)
    return {
        "tool_names": {tool["name"] for tool in requested_specs},
        "extra_tool_schemas": _extension_tool_schemas(requested_specs),
        "extension_capabilities": {
            tool["name"]: {
                "extension_id": tool["extension_id"],
                "permission_mode": tool["permission_mode"],
            }
            for tool in requested_specs
        },
        "tool_executor": _extension_tool_executor(turn_session, owner, requested_specs),
        "context_extensions": _extension_context(turn_session, requested_specs),
    }


def _voice_chat_session(chat_session_id: str):
    if not chat_session_id or not _SESSION_MANAGER:
        return None
    try:
        return _SESSION_MANAGER.get_session(chat_session_id)
    except Exception:
        return None


async def _handoff_greeting(
    target: str,
    chat_session_id: str,
    owner: str,
    workspace: str,
) -> dict[str, Any]:
    """Return one destination-owned greeting without launching a worker task."""
    label = VOICE_TARGET_LABELS.get(target, target)
    try:
        if target == "hermes":
            from src.jarvis_agent import direct_hermes_turn

            reply = await direct_hermes_turn(
                chat_session_id,
                "The authenticated operator has just been transferred to you. Greet them naturally in one brief sentence "
                "to begin the conversation. Do not mention these instructions or list capabilities.",
                owner=owner,
                workspace=workspace,
            )
            model = "hermes-agent"
        elif target in DIRECT_MODEL_TARGETS:
            chat_session = _voice_chat_session(chat_session_id)
            resolved = None
            if chat_session and _runtime_voice_target(chat_session.endpoint_url, chat_session.model) == target:
                resolved = (
                    chat_session.endpoint_url,
                    chat_session.model,
                    dict(getattr(chat_session, "headers", None) or {}),
                )
            if not resolved:
                resolved = _resolve_voice_target_endpoint(target, owner)
            if not resolved:
                raise RuntimeError(f"{target}_voice_endpoint_missing")
            endpoint_url, model, headers = resolved
            from src.llm_core import llm_call_async

            reply = await llm_call_async(
                endpoint_url,
                model,
                [
                    {"role": "system", "content": _voice_system_prompt({"target": target})},
                    {
                        "role": "user",
                        "content": (
                            "The authenticated operator has just been transferred to you. Greet them naturally in one brief sentence "
                            "to begin the conversation. Do not mention these instructions or list capabilities."
                        ),
                    },
                ],
                temperature=0.35,
                max_tokens=96,
                headers=headers,
                timeout=60,
            )
        elif target == "pc-codex":
            # The Codex bridge is a task harness, not a foreground chat API.
            # Keep the handoff instant instead of launching a deep task just to say hello.
            reply = f"{label} here{_operator_vocative()}. What are we working on?"
            model = "odysseus-router"
        else:
            reply = f"{label} here{_operator_vocative()}. What do you need?"
            model = "odysseus-router"

        reply = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", str(reply or ""), flags=re.IGNORECASE)
        reply = " ".join(reply.split()).strip()
        if not reply:
            raise RuntimeError("handoff_greeting_empty")
        return {
            "text": _bounded_spoken_text(reply, 300),
            "target": target,
            "model": model,
            "diagnostics": {
                "model": model,
                "character_name": label,
                "direct_target": target,
                "guard_reason": f"handoff_greeting_{target}",
                "task_ids": [],
            },
        }
    except Exception as exc:
        logger.warning("%s handoff greeting failed: %s", label, str(exc)[:200])
        reply = f"{label} is selected, but the automatic greeting did not complete. You can speak now."
        return {
            "text": reply,
            "target": target,
            "model": "Pandamonium",
            "diagnostics": {
                "model": "odysseus-router",
                "character_name": "Pandamonium",
                "direct_target": target,
                "guard_reason": f"handoff_greeting_{target}_failed",
                "task_ids": [],
            },
        }


def _decorate_voice_final(final: dict[str, Any], voice_session: dict[str, Any]) -> dict[str, Any]:
    diagnostics = final.setdefault("diagnostics", {})
    target = str(voice_session.get("target") or "jarvis")
    diagnostics.setdefault("character_name", _voice_character_name(voice_session))
    if target == "friday":
        diagnostics.setdefault("direct_target", "friday")
    return final


def _num_predict_for_text(text: str) -> int:
    if re.search(r"\b(detail|detailed|explain|deep dive|walk me through|long answer)\b", text, flags=re.IGNORECASE):
        return VOICE_LONG_NUM_PREDICT
    return VOICE_NORMAL_NUM_PREDICT


def _asks_runtime_status(text: str) -> bool:
    return bool(re.search(r"\b(what|which|identify|runtime|model|architecture|quantization)\b.*\b(model|running|runtime|architecture|quantization)\b", text, re.IGNORECASE))


def _asks_current_business(text: str) -> bool:
    normalized = " ".join(text.replace("’", "'").split())
    subject = r"(?:(?:the|my|our)\s+business|business|mad\s+panda(?:\s*3d)?|(?:my|our|the|all)\s+clients|clients)"
    second_subject = rf"(?:\s*,?\s*(?:with|across|for)\s+{subject})?"
    request_end = (
        second_subject
        + r"(?:\s+(?:right\s+now|today|currently|lately|please))*\s*(?:[?.!]|$)"
        + r"(?:\s*(?:just\s+)?(?:a\s+)?(?:quick|brief|short)\s+"
        + r"(?:update|rundown|check(?:-?in)?)(?:\s*,\s*nothing\s+(?:too\s+)?"
        + r"(?:deep|detailed|extensive))?\s*(?:[?.!]|$))?"
    )
    patterns = (
        rf"\bwhat(?:'s|s|\s+is)\s+up\s+with\s+{subject}\b{request_end}",
        rf"\bhow(?:'s|\s+is|\s+are)\s+{subject}\s+(?:doing|going|running|looking)\b{request_end}",
        rf"\bhow(?:'s|\s+is|\s+are)\s+{subject}\b{request_end}",
        rf"\bhow(?:'s|\s+is|\s+are)\s+(?:things|everything)(?:\s+(?:doing|going|running|looking))?\s+(?:with|across|for)\s+{subject}\b{request_end}",
        rf"\bwhat(?:'s|\s+is)\s+(?:happening|going\s+on)\s+(?:with|across|for)\s+{subject}\b{request_end}",
        rf"\bwhere\s+(?:do|does)\s+.*?\bstand\s+(?:with|on|across)\s+{subject}\b{request_end}",
        rf"\banything\s+new\s+(?:with|on|across)\s+{subject}\b{request_end}",
        rf"\b(?:check(?:ing)?\s+in|rundown)\s+(?:with|on|of|for)\s+{subject}\b{request_end}",
        rf"\b(?:current|latest|recent)\s+{subject}\s+(?:updates?|status|rundown)\b{request_end}",
        rf"\b{subject}\s+(?:updates?|status|rundown)\b{request_end}",
        rf"\b(?:updates?|status|rundown)\s+(?:with|on|of|for)\s+{subject}\b{request_end}",
        rf"\bwhat(?:'s|\s+is)\s+the\s+(?:latest|status)\s+(?:with|on|for)\s+{subject}\b{request_end}",
    )
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns)


def _workspace_for_text(text: str) -> str:
    if "business" in VOICE_WORKSPACES and re.search(r"\b(business|clients?|marketing|campaign|crm)\b", text, re.IGNORECASE):
        return "business"
    if "project-linux" in VOICE_WORKSPACES and re.search(r"\b(project\s+linux|linux\s+(?:desktop|workstation)|hyprland)\b", text, re.IGNORECASE):
        return "project-linux"
    if "home-lab" in VOICE_WORKSPACES and re.search(
        r"\b(home\s*lab|jarvis|odysseus|proxmox|truenas|project\s+nimbus|nimbus|mark\s*\d+(?:\.\d+)?)\b",
        text,
        re.IGNORECASE,
    ):
        return "home-lab"
    if "madpanda3d" in VOICE_WORKSPACES:
        return "madpanda3d"
    return sorted(VOICE_WORKSPACES)[0] if VOICE_WORKSPACES else "workspace"


def _selected_workspace(text: str, current: str) -> str:
    if "business" in VOICE_WORKSPACES and re.search(r"\b(business|clients?|marketing|campaign|crm)\b", text, re.IGNORECASE):
        return "business"
    if "project-linux" in VOICE_WORKSPACES and re.search(r"\b(project\s+linux|linux\s+(?:desktop|workstation)|hyprland)\b", text, re.IGNORECASE):
        return "project-linux"
    if "home-lab" in VOICE_WORKSPACES and re.search(
        r"\b(home\s*lab|jarvis|odysseus|proxmox|truenas|project\s+nimbus|nimbus|mark\s*\d+(?:\.\d+)?)\b",
        text,
        re.IGNORECASE,
    ):
        return "home-lab"
    if "madpanda3d" in VOICE_WORKSPACES and re.search(r"\b(madpanda3d|all\s+projects|across\s+(?:all\s+)?projects|company[-\s]wide|cross[-\s]domain)\b", text, re.IGNORECASE):
        return "madpanda3d"
    return current


def _delegation_route(text: str) -> tuple[str, str] | None:
    """Map configured voice aliases to fixed workers and server-controlled workspaces."""
    if re.search(r"\b(vps|online server|public server|hosting server|mad\s*panda hosting)\b", text, re.IGNORECASE):
        return "vps-codex", "vps-ops"
    if re.search(r"\b(?:hermes|gordon)\b", text, re.IGNORECASE):
        return "hermes", "home-lab"
    if re.search(
        r"\b(pc code(?:x|cs)|my codex|desktop codex|computer codex)\b|"
        r"\bcodex\s+(?:on|from)\s+my\s+(?:pc|computer)\b|"
        r"\b(?:ask|talk to|speak to|check with)\s+my computer\b",
        text,
        re.IGNORECASE,
    ):
        return "pc-codex", _workspace_for_text(text)
    if re.search(r"\b(project\s+nimbus|nimbus|home cloud|my cloud|the cloud)\b", text, re.IGNORECASE):
        return "pc-codex", "home-lab"
    return None


_NAMED_WORKER_ALIASES = (
    ("vps-codex", r"(?:vps(?:\s+codex)?|online\s+server|public\s+server|hosting\s+server|mad\s*panda\s+hosting)"),
    ("hermes", r"(?:hermes|gordon)"),
    (
        "pc-codex",
        r"(?:friday|pc\s+code(?:x|cs)|my\s+(?:pc(?:\s+codex)?|codex|computer)|desktop\s+codex|computer\s+codex|"
        r"codex\s+(?:on|from)\s+my\s+(?:pc|computer)|my\s+computer|project\s+nimbus|nimbus|"
        r"home\s+cloud|my\s+cloud|the\s+cloud)",
    ),
    ("jarvis", r"jarvis"),
)


def _runtime_voice_target(endpoint_url: str, model: str) -> str:
    if str(model or "").strip().lower() == "hermes-agent":
        return "hermes"
    if "chatgpt.com/backend-api/codex" in str(endpoint_url or "").lower():
        return "friday"
    return "jarvis"


def _voice_origin_target(voice_session: dict[str, Any], chat_session: Any = None) -> str:
    saved = str(voice_session.get("origin_target") or "")
    if saved in ACTIVE_VOICE_TARGETS:
        return saved
    return _runtime_voice_target(
        getattr(chat_session, "endpoint_url", ""),
        getattr(chat_session, "model", ""),
    )


def _resolve_voice_target_endpoint(target: str, owner: str) -> tuple[str, str, dict] | None:
    if target == "jarvis":
        resolved = resolve_endpoint("default", owner=owner)
        if resolved and resolved[0] and resolved[1]:
            return resolved
    names = VOICE_TARGET_ENDPOINT_NAMES.get(target) or ()
    if not names:
        return None
    from core.database import ModelEndpoint, SessionLocal
    from src.auth_helpers import owner_filter

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        rows = query.all()
        for name in names:
            matches = [row for row in rows if str(row.name or "").casefold() == name.casefold()]
            if owner:
                matches.sort(key=lambda row: row.owner != owner)
            if matches:
                resolved = resolve_endpoint_by_id(matches[0].id, owner=owner)
                if resolved:
                    return resolved
    finally:
        db.close()
    return None


def _named_worker_route_after(text: str, offset: int) -> tuple[str, str] | None:
    tail = text[offset:]
    for worker, alias in _NAMED_WORKER_ALIASES:
        if not re.match(rf"\s*(?:the\s+)?{alias}\b", tail, re.IGNORECASE):
            continue
        workspace = (
            "vps-ops"
            if worker == "vps-codex"
            else "home-lab"
            if worker in {"hermes", "jarvis"}
            else _workspace_for_text(text)
        )
        return worker, workspace
    return None


def _target_switch(text: str) -> str | None:
    command_text = _voice_command_words(text)
    direct_command = re.search(
        r"^\s*(?:(?:hey|hi|hello|okay|ok|please|jarvis|gordon|hermes|friday)\b[\s,.:;-]*)*"
        r"(?:(?:do\s+me\s+(?:a\s+)?favor|do\s+us\s+(?:a\s+)?favor)\b[\s,.:;-]*)?"
        r"(?:(?:(?:can|could|would|will)\s+you|(?:can|could|may)\s+i|let\s+me)\s+)?(?:please\s+)?"
        r"(?:(?:talk|speak|connect|switch|transfer)(?:\s+me)?(?:\s+back)?\s+(?:to|with)|"
        r"be\s+transferred(?:\s+back)?\s+to|be\s+put\s+(?:through\s+to|on\s+the\s+phone\s+with)|"
        r"put\s+me\s+(?:through\s+to|on\s+the\s+phone\s+with))\s+",
        command_text,
        re.IGNORECASE,
    )
    first_person_request = re.search(
        r"\b(?:i\s+(?:want|need|would\s+like)|i['’]d\s+like)\s+to\s+(?:now\s+)?"
        r"(?:(?:talk|speak|connect|switch|transfer)(?:\s+me)?(?:\s+back)?\s+(?:to|with)|"
        r"be\s+transferred(?:\s+back)?\s+to|be\s+put\s+(?:through\s+to|on\s+the\s+phone\s+with))\s+",
        command_text,
        re.IGNORECASE,
    )
    jarvis_return = re.search(
        r"^\s*(?:(?:okay|ok|please)\b[\s,.:;-]*)*(?:return|go|come)\s+(?:back\s+)?to\s+jarvis\b",
        command_text,
        re.IGNORECASE,
    )
    switch_request = direct_command or first_person_request
    if not (switch_request or jarvis_return):
        return None
    if jarvis_return:
        return "jarvis"
    route = _named_worker_route_after(command_text, switch_request.end())
    return route[0] if route else None


def _jarvis_vocative(text: str) -> bool:
    """Recognize direct address to Jarvis without matching quoted references."""
    return bool(re.search(
        r"^\s*(?:(?:hey|hi|hello|okay|ok|thanks|thank\s+you|beautiful|great|good\s+(?:morning|afternoon|evening))"
        r"[\s,.:;!-]+)?jarvis\b",
        text,
        re.IGNORECASE,
    ))


def _background_delegations(text: str) -> list[tuple[str, str]]:
    matches: list[tuple[int, tuple[str, str]]] = []
    for request in re.finditer(
        r"\b(?:(?:ask|have|get|tell|let|check(?:\s+with)?)\s+|"
        r"send\s+(?:(?:a|the)\s+message(?:\s+over)?\s+to\s+)?|"
        r"shoot(?:\s+(?:a|the))?\s+message(?:\s+over)?\s+to\s+|"
        r"reach\s+out\s+to\s+)",
        text,
        re.IGNORECASE,
    ):
        route = _named_worker_route_after(text, request.end())
        if route and route[0] != "jarvis":
            matches.append((request.start(), route))
    if _is_document_open_request(text) and not any(route[0] == "pc-codex" for _, route in matches):
        document_request = re.search(r"\b(?:open(?:\s+up)?|show|pull\s+up|load)\b", text, re.IGNORECASE)
        document_position = document_request.start() if document_request else -1
        if not matches or document_position < min(position for position, _ in matches):
            matches.append((document_position, ("pc-codex", _workspace_for_text(text))))

    routes: list[tuple[str, str]] = []
    seen_workers: set[str] = set()
    for _, route in sorted(matches, key=lambda match: match[0]):
        if route[0] in seen_workers:
            continue
        seen_workers.add(route[0])
        routes.append(route)
    return routes


def _background_delegation(text: str) -> tuple[str, str] | None:
    routes = _background_delegations(text)
    return routes[0] if routes else None


def _selected_pc_codex_task_request(text: str) -> bool:
    """Keep selected Friday conversational unless the operator clearly requests work."""
    value = _voice_command_words(text)
    return bool(re.match(
        r"^(?:(?:task|job)(?: for)? friday(?: to| with)? )?"
        r"(?:analy[sz]e|audit|build|change|check|compare|create|debug|deploy|diagnose|edit|"
        r"find|fix|implement|inspect|investigate|load|open|patch|pull|push|read|restart|review|"
        r"run|search|start|stop|test|update|verify|write)\b",
        value,
        re.IGNORECASE,
    ))


def _is_document_open_request(text: str) -> bool:
    return bool(
        re.search(r"\b(?:open(?:\s+up)?|show|pull\s+up|load)\b", text, re.IGNORECASE)
        and re.search(
            r"\b(?:documents?|documentation|files?|notes?|mark\s+\d+(?:\.\d+)?)\b",
            text,
            re.IGNORECASE,
        )
    )


def _voice_words(text: str) -> str:
    value = text.lower().replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", value).split())


def _voice_command_words(text: str) -> str:
    value = _voice_words(text)
    prefixes = (
        r"^(?:(?:hey|okay|ok|please|all right|alright)\s+)*(?:(?:jarvis|friday|gordon|hermes)\s+)?",
        r"^(?:(?:great|good|nice) work)\s+",
        r"^(?:can|could|would|will) you (?:please )?",
        r"^(?:i (?:want|need|would like)|i'd like)(?: you)? to (?:now )?(?:please )?",
        r"^(?:actually )?do me (?:a )?favor(?: and)? (?:please )?",
        r"^actually ",
        r"^(?:go ahead and|please) ",
    )
    for _ in range(3):
        previous = value
        for pattern in prefixes:
            value = re.sub(pattern, "", value)
        if value == previous:
            break
    return re.sub(r"\s+please$", "", value).strip()


def _normalized_voice_control(text: str) -> str:
    """Normalize bounded Whisper filler without widening the browser control surface."""
    if not text or len(text) > VOICE_CONTROL_MAX_CHARS:
        return ""
    return _voice_command_words(text)


def _voice_control_intent(text: str) -> tuple[str, str, str | None] | None:
    value = _normalized_voice_control(text)
    if not value or re.search(r"\b(?:don't|do not|never|not)\b", value):
        return None
    # Leading filler containing "and" is stripped above; any remaining connector
    # would make this more than one browser-owned action.
    if re.search(r"\b(?:and|then|also)\b", value):
        return None

    document_suffix = (
        r"(?: (?:that|which) is (?:showing|open)(?: you know)?(?: this is)?(?: right now)?"
        r"| (?:showing|open)(?: right now)?)?"
        r"(?: so (?:that )?it (?:goes away|is gone))?"
    )
    if re.fullmatch(r"(?:open|opens|show|shows|pull up)(?: the| my)? calendar", value):
        return "foreground", "open_view", "calendar"
    if re.fullmatch(
        rf"(?:close|dismiss)(?: the)?(?: this| my| current| active)? document{document_suffix}",
        value,
    ):
        return "foreground", "close_view", "document"
    if re.fullmatch(
        rf"(?:minimize|hide|put away)(?: the)?(?: this| my| current| active)? document{document_suffix}",
        value,
    ):
        return "foreground", "minimize_view", "document"
    if re.fullmatch(
        r"(?:what|which)(?: view| window| panel)(?: is|'s)(?: currently)? (?:open|active)(?: right now)?|"
        r"(?:what|which)(?: view| window| panel) do i have open(?: right now)?|"
        r"what am i (?:looking at|viewing)|report(?: the)? current view",
        value,
    ):
        return "foreground", "report_view_state", None
    media = {
        "open your eyes": "camera_open",
        "open eyes": "camera_open",
        "open the camera": "camera_open",
        "what do you see": "camera_describe",
        "describe what you see": "camera_describe",
        "describe the camera": "camera_describe",
        "close your eyes": "camera_close",
        "close eyes": "camera_close",
        "close the camera": "camera_close",
        "i need something motivational": "media_motivation",
        "need something motivational": "media_motivation",
        "i want something motivational": "media_motivation",
        "want something motivational": "media_motivation",
        "show me something motivational": "media_motivation",
        "play something motivational": "media_motivation",
    }.get(value)
    return ("media", media, None) if media else None


def _oracle_protocol_intent(text: str, voice_session: dict) -> str | None:
    """Parse the two ORACLE controls and their bounded confirmation exchange."""
    value = _normalized_voice_control(text)
    if not value:
        return None

    pending = bool(voice_session.get("oracle_protocol_pending"))
    active = bool(voice_session.get("oracle_protocol_active"))
    if pending:
        if re.fullmatch(
            r"(?:yes(?: sir|(?: the)? oracle(?: protocol)?)?|correct|exactly|affirmative|that's right|that is right|engage it|do it)",
            value,
        ) or re.fullmatch(
            r"yes(?: i(?:'m| am) talking about| i mean| that is| it's| it is)?(?: the)? oracle(?: protocol)?",
            value,
        ):
            return "engage"
        if re.fullmatch(r"(?:no|no sir|negative|not now|never mind|nevermind)", value):
            return "decline"

    if re.search(r"\b(?:don't|do not|never|not)\b", value):
        return None
    if re.fullmatch(
        r"(?:engage|activate|open|show|launch)(?: the)? oracle(?: protocol)?",
        value,
    ):
        return "engage"
    if re.fullmatch(
        r"(?:shut down|shutdown|disengage|deactivate|close|hide)(?: the)? oracle(?: protocol)?",
        value,
    ):
        return "shutdown"
    if active and re.fullmatch(
        r"(?:shut down|shutdown|disengage|deactivate|close|hide)(?: the)? protocol",
        value,
    ):
        return "shutdown"
    if re.fullmatch(
        r"(?:buddy )?(?:i|we) (?:(?:might|may|could) need|could use|need) (?:some )?eyes in the sky",
        value,
    ):
        return "suggest"
    return None


def _foreground_command(text: str) -> tuple[str, str | None] | None:
    """Return one narrow, browser-owned foreground action."""
    intent = _voice_control_intent(text)
    if intent and intent[0] == "foreground":
        return intent[1], intent[2]
    return None


_CALENDAR_READ_REASONS = frozenset({
    "calendar lookup request",
    "calendar lookup question",
    "calendar availability question",
    "calendar agenda question",
    "next calendar item question",
})
_CALENDAR_MUTATION_RE = re.compile(
    r"\b(?:add|create|recreate|reschedule|book|put|delete|remove|cancel)\b"
    r"|\bschedule\s+(?:a|an|the|my)\s+(?:event|meeting|appointment|call)\b",
    re.I,
)
_CALENDAR_LIST_RE = re.compile(
    r"\b(?:list|show)\b.{0,80}\bcalendars\b"
    r"|\b(?:what|which)\b.{0,80}\bcalendars\b.{0,80}\b(?:connected|available|configured|have)\b",
    re.I,
)
_CALENDAR_COMPARE_RE = re.compile(
    r"\b(?:compare|comparison)\b.{0,120}\b(?:calendar|schedule|events?|meetings?|appointments?)\b",
    re.I,
)


def _calendar_read_args(text: str, now: datetime | None = None) -> dict[str, str] | None:
    """Return one bounded read request for explicit Calendar lookup intent."""
    if _CALENDAR_MUTATION_RE.search(text):
        return None
    if _CALENDAR_LIST_RE.search(text):
        return {"action": "list_calendars"}
    intent = classify_tool_intent(text)
    is_read = intent.category == "calendar" and intent.reason in _CALENDAR_READ_REASONS
    if not is_read and not _CALENDAR_COMPARE_RE.search(text):
        return None
    local_now = (now or now_user_local()).replace(tzinfo=None, second=0, microsecond=0)
    today = local_now.replace(hour=0, minute=0)
    lower = text.lower()
    if "today" in lower and "tomorrow" in lower:
        start, end = today, today + timedelta(days=2)
    elif "tomorrow" in lower:
        start, end = today + timedelta(days=1), today + timedelta(days=2)
    elif "today" in lower:
        start, end = today, today + timedelta(days=1)
    elif "this week" in lower:
        start = today
        end = today + timedelta(days=max(1, 7 - today.weekday()))
    else:
        start = local_now if re.search(r"\b(?:next|upcoming|when)\b", lower) else today
        end = start + timedelta(days=14)
    return {"action": "list_events", "start": start.isoformat(), "end": end.isoformat()}


def _setup_status_command(text: str) -> bool:
    """Match one exact, read-only setup check and reject compound requests."""
    normalized = re.sub(r"[.!?]+$", "", text.strip().lower()).strip()
    return normalized == "check voice setup"


def _safe_service_stats(service: Any, label: str) -> dict[str, Any]:
    if service is None:
        return {"available": False, "provider": "disabled"}
    try:
        value = service.get_stats()
    except Exception as exc:
        logger.warning("%s status unavailable: %s", label, type(exc).__name__)
        return {"available": False, "provider": "disabled"}
    return value if isinstance(value, dict) else {"available": False, "provider": "disabled"}


def _setup_provider_kind(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if not provider or provider == "disabled":
        return "disabled"
    if provider.startswith("endpoint:"):
        return "endpoint"
    if provider in {"browser", "local"}:
        return provider
    return "configured"


def _setup_voice_name(value: Any) -> str:
    name = " ".join(str(value or "").split())[:80]
    return name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._'-]{0,79}", name) else ""


def _setup_voice_speed(value: Any) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    return speed if 0.25 <= speed <= 4 else 1.0


def _setup_logical_names(value: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    names = {
        str(item).strip()
        for item in value
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", str(item).strip())
    }
    return sorted(names)[:limit]


async def _voice_status_snapshot(owner: str, stt_service: Any, tts_service: Any) -> dict[str, Any]:
    """Build one redacted setup snapshot shared by HTTP status and voice."""
    stt = _safe_service_stats(stt_service, "STT")
    tts = _safe_service_stats(tts_service, "TTS")
    model_configured = False
    try:
        endpoint_url, model, _headers = _resolve_voice_runtime(owner)
        model_configured = bool(endpoint_url and model)
    except Exception as exc:
        logger.warning("Voice model status unavailable: %s", type(exc).__name__)
    try:
        raw_workers = await asyncio.wait_for(
            worker_statuses(),
            timeout=VOICE_SETUP_STATUS_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("Voice worker status unavailable: %s", type(exc).__name__)
        raw_workers = {}
    if not isinstance(raw_workers, dict):
        raw_workers = {}
    workers: list[dict[str, Any]] = []
    for worker_id in WORKER_IDS:
        details = raw_workers.get(worker_id)
        details = details if isinstance(details, dict) else {}
        configured = bool(details.get("configured"))
        ready = bool(configured and details.get("ready"))
        workers.append({
            "id": worker_id,
            "configured": configured,
            "ready": ready,
            "status": "ready" if ready else ("unavailable" if configured else "not_configured"),
            "capabilities": _setup_logical_names(details.get("capabilities")),
        })
    stt_available = bool(stt.get("available"))
    tts_available = bool(tts.get("available"))
    core_ready = model_configured and stt_available and tts_available
    guidance = ["Core voice setup is ready." if core_ready else "Voice setup needs attention."]
    if not core_ready:
        if not model_configured:
            guidance.append("Configure an available chat model for Voice Orb.")
        if not stt_available:
            guidance.append("Enable an available speech-to-text provider.")
        if not tts_available:
            guidance.append("Enable an available text-to-speech provider.")
    ready_workers = sum(1 for worker in workers if worker["ready"])
    if ready_workers:
        guidance.append(f"{ready_workers} of {len(workers)} optional fixed read-only workers are ready.")
    else:
        guidance.append("Optional fixed read-only workers are not ready.")
    setup = {
        "version": 1,
        "command": "Check voice setup.",
        "core_ready": core_ready,
        "model": {
            "configured": model_configured,
            "selection": (
                "endpoint_override"
                if VOICE_ENDPOINT_ID
                else ("model_override" if VOICE_MODEL else "default")
            ),
        },
        "speech_to_text": {
            "available": stt_available,
            "provider": _setup_provider_kind(stt.get("provider")),
        },
        "text_to_speech": {
            "available": tts_available,
            "provider": _setup_provider_kind(tts.get("provider")),
            "voice": _setup_voice_name(tts.get("voice")),
        },
        "workers": {
            "optional": True,
            "ready_count": ready_workers,
            "total": len(workers),
            "items": workers,
        },
        "guidance": guidance,
        "text": " ".join(guidance),
    }
    return {
        "assistant": configured_agent_name(),
        "model_override": VOICE_MODEL or None,
        "endpoint_override_configured": bool(VOICE_ENDPOINT_ID),
        "stt": {
            "available": stt_available,
            "provider": _setup_provider_kind(stt.get("provider")),
        },
        "tts": {
            "available": tts_available,
            "provider": _setup_provider_kind(tts.get("provider")),
            "voice": _setup_voice_name(tts.get("voice")),
            "speed": _setup_voice_speed(tts.get("speed", 1)),
        },
        "setup": setup,
    }


def _media_command(text: str) -> str | None:
    """Return one narrow, single-purpose camera/media action."""
    intent = _voice_control_intent(text)
    return intent[1] if intent and intent[0] == "media" else None


def _unsupported_voice_control(text: str) -> bool:
    """Keep near-known, unsafe, or compound browser controls out of the LLM."""
    if _voice_control_intent(text) or _is_document_open_request(text):
        return False
    value = _normalized_voice_control(text)
    detection_value = value or _voice_words(text)
    raw = text.lower().replace("’", "'")
    known_target = bool(re.search(
        r"\b(?:calendar|documents?|eyes|camera|motivational|oracle|protocol|current view|window|panel)\b",
        raw,
    ))
    arbitrary_browser_target = bool(re.search(
        r"(?:https?://|\b(?:browser|page|settings|menu|button|tab|script|selector)\b)",
        raw,
    ))
    command_like = bool(re.match(
        r"(?:open|opens|show|shows|pull up|close|dismiss|minimize|hide|put away|report|run|execute|click|navigate)\b",
        detection_value,
    ))
    control_word = bool(re.search(
        r"\b(?:open|opens|show|shows|close|dismiss|minimize|hide|describe|play|need|want|report|active)\b",
        detection_value,
    ))
    return (known_target and control_word) or (arbitrary_browser_target and command_like)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None


def _decode_voice_frame(frame: VoiceFrame) -> dict[str, Any]:
    try:
        data = base64.b64decode(frame.data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail={"message": "Invalid voice frame encoding"}) from exc
    if not data or len(data) > VOICE_FRAME_MAX_BYTES:
        raise HTTPException(status_code=422, detail={"message": "Voice frame exceeds the 1 MiB limit"})

    if frame.mime == "image/png":
        if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
            dimensions = None
        else:
            dimensions = (
                int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"),
            )
    else:
        dimensions = _jpeg_dimensions(data)

    if not dimensions:
        raise HTTPException(status_code=422, detail={"message": "Voice frame type does not match its image bytes"})
    width, height = dimensions
    if width > VOICE_FRAME_MAX_WIDTH or height > VOICE_FRAME_MAX_HEIGHT:
        raise HTTPException(status_code=422, detail={"message": "Voice frame dimensions exceed 1024 by 576"})
    if (width, height) != (frame.width, frame.height):
        raise HTTPException(status_code=422, detail={"message": "Voice frame dimensions do not match its image bytes"})
    return {"bytes": data, "mime": frame.mime, "width": width, "height": height}


def _describe_client_view(client_state: dict[str, Any] | None) -> str:
    state = client_state or {}
    active = state.get("active_view")
    calendar = state.get("calendar") if isinstance(state.get("calendar"), dict) else {}
    document = state.get("document") if isinstance(state.get("document"), dict) else {}

    if active == "calendar" and calendar.get("open"):
        view = str(calendar.get("view") or "month")
        date = str(calendar.get("date") or "").strip()
        suffix = f", centered on {date}" if date else ""
        return f"Calendar is the active view in {view} view{suffix}."
    if active == "document" and (document.get("open") or document.get("minimized")):
        doc_id = str(document.get("id") or "").strip()
        suffix = f" The active document ID is {doc_id}." if doc_id else ""
        return f"The document workspace is the active view.{suffix}"
    if calendar.get("minimized"):
        return "The main chat is active, and Calendar is minimized."
    if document.get("minimized"):
        return "The main chat is active, and a document is minimized."
    if active == "chat":
        return "The main chat is the active view."
    return "I cannot confirm the current Pandamonium view from this turn."


def _is_worker_retry_request(text: str) -> bool:
    value = " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())
    return bool(re.fullmatch(
        r"(?:(?:okay|ok|well|all right|so)\s+)*(?:please\s+)?"
        r"(?:(?:ask|tell|have|get)\s+(?:him|her|it|them)\s+to\s+)?"
        r"(?:do|try|run|open)\s+(?:it|that)\s+again(?:\s+please)?",
        value,
    ))


def _is_casual_greeting(text: str) -> bool:
    value = text.lower().replace("’", "'")
    value = re.sub(r"\bjarvis\b|\by'?alls?\b", " ", value)
    value = " ".join(re.sub(r"[^a-z' ]", " ", value).split())
    return bool(re.fullmatch(
        r"(?:hi|hello|hey|good (?:morning|afternoon|evening))(?: there)?"
        r"(?: how (?:are )?you(?: doing)?| how(?:'s| is) it going)?|"
        r"how (?:are )?you(?: doing)?|how(?:'s| is) it going|what(?:'s| is) up",
        value,
    ))


def _casual_greeting_reply(text: str, voice_session: dict) -> str:
    explicit_band = re.search(r"\bgood\s+(morning|afternoon|evening)\b", text, re.IGNORECASE)
    if explicit_band:
        band = explicit_band.group(1).lower()
        return f"Good {band}{_operator_vocative()}. What are we working on?"
    replies = (
        f"I’m doing well{_operator_vocative()}. What are we working on?",
        f"Good to hear from you{_operator_vocative()}. What would you like to tackle?",
    )
    recent = {str(turn.get("text") or "") for turn in voice_session.get("turns", [])[-6:]}
    return next((reply for reply in replies if reply not in recent), replies[0])


def _approval_choice(text: str) -> str | None:
    if not text or len(text) > VOICE_CONTROL_MAX_CHARS:
        return None
    value = " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())
    if re.search(PERSISTENT_APPROVAL_PATTERN, value):
        return None
    if re.search(r"\byes\b", value) and re.search(r"\bno\b", value):
        return None
    deny = bool(re.search(r"\b(?:deny|decline|reject|don't|do not|no)\b", value))
    approve = bool(re.search(r"\b(?:approve|approved)\b", value))
    if deny == approve:
        return None
    if deny:
        return "deny"
    if approve:
        return "once"
    return None


def _explicit_reply_target(text: str) -> str | None:
    if not text or len(text) > VOICE_CONTROL_MAX_CHARS:
        return None
    request = re.search(r"\b(?:answer|reply|respond)(?:\s+to)?\s+", text, re.IGNORECASE)
    if not request:
        return None
    route = _named_worker_route_after(text, request.end())
    return route[0] if route else None


def _named_task_workers(text: str) -> list[str]:
    workers = []
    for worker, alias in _NAMED_WORKER_ALIASES:
        if worker != "jarvis" and re.search(rf"\b(?:the\s+)?{alias}\b", text, re.IGNORECASE):
            workers.append(worker)
    return workers


_TASK_WORKSPACE_PATTERNS = (
    ("vps-ops", r"\bvps[\s-]+ops\b"),
    ("project-linux", r"\bproject[\s-]+linux\b"),
    ("home-lab", r"\bhome[\s-]+lab\b"),
    ("madpanda3d", r"\b(?:madpanda3d|mad\s+panda\s+3d)\b"),
    ("business", r"\bbusiness\b"),
)


def _spoken_task_workspaces(text: str) -> list[str]:
    return [workspace for workspace, pattern in _TASK_WORKSPACE_PATTERNS if re.search(pattern, text, re.IGNORECASE)]


def _task_control_intent(text: str) -> tuple[str, str | None, str | None] | None:
    """Parse one complete task command; descriptive or partial prose stays conversational."""
    command = _voice_command_words(text)
    workers = _named_task_workers(text)
    worker = workers[0] if len(workers) == 1 else None
    workspaces = _spoken_task_workspaces(text)
    worker_pattern = "(?:" + "|".join(
        alias for candidate, alias in _NAMED_WORKER_ALIASES if candidate != "jarvis"
    ) + ")"
    workspace_pattern = r"(?:business|home[\s-]+lab|project[\s-]+linux|madpanda3d|mad\s+panda\s+3d|vps[\s-]+ops)"
    persistent_prefix = bool(re.match(
        r"^(?:(?:for (?:this|the) session|from now on|until further notice)|"
        r"(?:always|permanently|forever|indefinitely)) approve\b",
        command,
    ))
    negative_wrapper = bool(
        re.match(
            r"^(?:i (?:do not|don't|never) (?:want|need)(?: you)? to|(?:do not|don't|never)) "
            r"(?:cancel|stop|approve|deny|decline|reject)\b",
            command,
        )
        or re.match(r"^do nothing\b.*\b(?:tasks?|requests?|runs?|jobs?)\b", command)
        or re.match(
            r"^i (?:want|need) (?:none|nothing)\b.*\b(?:cancell?(?:ed|ing)?|stopp?ed|approve(?:d)?)\b",
            command,
        )
    )
    cancel_prefix = bool(re.match(r"^(?:cancel|stop)\b", command))
    approval_prefix = bool(re.match(
        r"^(?:(?:yes|no) )?(?:approve|deny|decline|reject)\b",
        command,
    ))
    named_reply = bool(worker and any(
        re.match(rf"^(?:answer|reply|respond)(?: to)? (?:the )?{alias}\b", command)
        for candidate, alias in _NAMED_WORKER_ALIASES
        if candidate == worker
    ))
    unnamed_reply = bool(re.match(
        r"^(?:answer|reply|respond)(?: to)? (?:that|the) (?:worker )?question\b",
        command,
    ))
    stand_down = bool(worker and any(
        re.fullmatch(
            rf"(?:tell|ask) (?:the )?{alias} (?:to )?stand down"
            rf"(?: (?:in|from) (?:the )?{workspace_pattern}(?: workspace)?)?"
            r"(?: for me| right now| now)?",
            command,
        )
        for candidate, alias in _NAMED_WORKER_ALIASES
        if candidate == worker
    ))
    tell_reply = bool(worker and any(
        re.match(rf"^tell (?:the )?{alias} (?:the answer is|use|choose|select)\b", command)
        for candidate, alias in _NAMED_WORKER_ALIASES
        if candidate == worker
    ))
    command_like = (
        negative_wrapper
        or persistent_prefix
        or cancel_prefix
        or approval_prefix
        or named_reply
        or unnamed_reply
        or stand_down
        or tell_reply
    )
    if len(text) > VOICE_CONTROL_MAX_CHARS:
        return ("rejected", worker, None) if command_like else None
    if negative_wrapper:
        return "rejected", worker, None
    if not command_like:
        return None

    negative = bool(re.search(r"\b(?:don't|do not|never|not|none|nothing|neither|nor|zero)\b", command))
    if re.search(r"\bno\b", command) and not re.match(r"^no (?:deny|decline|reject)\b", command):
        negative = True
    if (cancel_prefix or approval_prefix) and negative:
        return "rejected", worker, None
    persistent_approval = bool(
        re.search(r"\bapprove(?:d)?\b", command)
        and re.search(PERSISTENT_APPROVAL_PATTERN, command)
    )
    if persistent_prefix or (approval_prefix and persistent_approval):
        return "persistent_approval", worker, None
    if len(workers) > 1 or len(workspaces) > 1:
        return "invalid", worker, None

    workspace_suffix = rf"(?: (?:in|from) (?:the )?{workspace_pattern}(?: workspace)?)?"
    polite_suffix = r"(?: for me| right now| now)?"
    cancel_target = (
        rf"(?:"
        rf"(?:the )?{worker_pattern} (?:the )?{workspace_pattern} (?:task|request|run|job)"
        rf"|"
        rf"(?:the )?{worker_pattern}(?: (?:task|request|run|job))?"
        rf"|(?:the )?(?:task|request|run|job)(?: (?:for|from|on) (?:the )?{worker_pattern})?"
        rf"|(?:the )?{workspace_pattern} (?:task|request|run|job)"
        rf"|(?:it|that|this)(?: (?:task|request|run|job))?"
        rf")"
    )
    if cancel_prefix:
        if re.fullmatch(rf"(?:cancel|stop) {cancel_target}{workspace_suffix}{polite_suffix}", command):
            return "cancel", worker, None
        return "invalid", worker, None
    if stand_down:
        return "cancel", worker, None

    if approval_prefix:
        approval_command = re.sub(r"^(?:yes|no) ", "", command)
        approval_target = (
            rf"(?:"
            rf"(?:the )?{worker_pattern}(?: (?:request|approval))?"
            rf"|(?:the )?{workspace_pattern} (?:request|approval)"
            rf"|(?:the|that|this) (?:request|approval)"
            rf"|(?:it|that|this)"
            rf")?"
        )
        if not re.fullmatch(
            rf"(?:approve|deny|decline|reject)(?: {approval_target})?(?: once)?{workspace_suffix}{polite_suffix}",
            approval_command,
        ):
            return "invalid", worker, None
        return "approval", worker, _approval_choice(text)

    if named_reply or unnamed_reply:
        controls = re.findall(r"\b(?:cancel|stop|approve|deny|decline|reject|answer|reply|respond)\b", command)
        if len(workers) > 1 or len(set(controls)) > 1:
            return "invalid", worker, None
        return "reply", worker, text
    if tell_reply:
        if len(workers) > 1 or re.search(r"\b(?:cancel|stop|approve|deny|decline|reject)\b", command):
            return "invalid", worker, None
        return "reply", worker, text
    return "invalid", worker, None


def _select_broker_task(
    chat_session_id: str,
    owner: str,
    action: str,
    named_worker: str | None,
    spoken_workspace: str | None,
) -> tuple[dict | None, str]:
    from src.jarvis_agent import list_active_tasks

    statuses = {
        "approval": {"waiting_approval"},
        "reply": {"waiting"},
        "cancel": {"queued", "running", "waiting", "waiting_approval"},
    }[action]
    tasks = list_active_tasks(
        chat_session_id,
        owner,
        worker=named_worker,
        workspace=spoken_workspace,
        statuses=statuses,
    )
    if not tasks:
        return None, "missing"
    if len(tasks) > 1:
        return None, "ambiguous"
    return tasks[0], "found"


async def _run_task_control(
    chat_session_id: str,
    text: str,
    owner: str,
    voice_session: dict,
    *,
    selected_reply: bool = False,
) -> tuple[str, str, list[str]] | None:
    intent = _task_control_intent(text)
    workspaces = _spoken_task_workspaces(text)
    spoken_workspace = workspaces[0] if len(workspaces) == 1 else None
    selected_task = None
    if not intent:
        selected_target = str(voice_session.get("target") or "jarvis")
        if not selected_reply or selected_target not in WORKER_LABELS:
            return None
        try:
            selected_task, selection = _select_broker_task(
                chat_session_id, owner, "reply", selected_target, spoken_workspace,
            )
        except RuntimeError:
            return None
        if selection == "missing":
            return None
        if selection == "ambiguous":
            return (
                "More than one worker question matches. Use the matching task card.",
                "worker_reply_ambiguous",
                [],
            )
        intent = ("reply", selected_target, text)
    action, named_worker, value = intent
    if action == "rejected":
        return "I did not run that task control.", "worker_control_rejected", []
    if action == "persistent_approval":
        return (
            "Voice can only approve one broker-authorized request once, or deny it. I did not grant persistent approval.",
            "worker_approval_persistent_refused",
            [],
        )
    if action == "invalid":
        return "Please give me one task control at a time.", "worker_control_compound", []

    if selected_task is None:
        task, selection = _select_broker_task(
            chat_session_id,
            owner,
            action,
            named_worker,
            spoken_workspace,
        )
    else:
        task, selection = selected_task, "found"
    label = WORKER_LABELS.get(named_worker or "", "worker")
    if selection == "missing":
        return (
            f"I could not find an eligible active {label} task in this chat.",
            f"worker_{action}_missing",
            [],
        )
    if selection == "ambiguous":
        return (
            "More than one task matches. Name the worker and workspace, or use the matching task card.",
            f"worker_{action}_ambiguous",
            [],
        )

    assert task is not None
    task_id = str(task["task_id"])
    label = WORKER_LABELS.get(str(task.get("worker") or ""), "the worker")
    from src.jarvis_agent import task_action

    if action == "approval":
        if value not in {"once", "deny"}:
            return (
                "Say approve once or deny. Voice cannot grant session or permanent approval.",
                "worker_approval_unclear",
                [task_id],
            )
        if value == "once" and not (
            task.get("permission_mode") == "workspace_write" and task.get("approved") is True
        ):
            return (
                f"That {label} task is not broker-authorized for writes, so voice can only deny the request.",
                "worker_approval_not_authorized",
                [task_id],
            )
        try:
            await task_action(
                task_id,
                "approval",
                {"choice": value, "spoken_text": text},
                persist_user_message=False,
                owner=owner,
            )
        except Exception as exc:
            logger.warning("Voice worker approval failed: %s", str(exc)[:200])
            return "I could not submit that approval; the task remains paused.", "worker_approval_failed", [task_id]
        verb = "denied" if value == "deny" else "approved once"
        return f"I {verb} the {label} request.", f"worker_approval_{value}", [task_id]

    if action == "cancel":
        try:
            updated = await task_action(
                task_id,
                "cancel",
                persist_user_message=False,
                owner=owner,
            )
        except Exception as exc:
            logger.warning("Voice worker cancellation failed: %s", str(exc)[:200])
            return (
                f"I could not request cancellation for {label}; it may still be running.",
                "worker_cancel_failed",
                [task_id],
            )
        if updated.get("status") == "cancelled":
            return f"The {label} task is cancelled.", "worker_cancelled", [task_id]
        return (
            f"Cancellation requested for {label}. I’ll report the terminal state when the broker receives it.",
            "worker_cancel_requested",
            [task_id],
        )

    answer = str(value or text)
    if ":" in answer and answer.split(":", 1)[1].strip():
        answer = answer.split(":", 1)[1].strip()
    try:
        await task_action(
            task_id,
            "reply",
            {"answers": _question_answers(task, answer)},
            persist_user_message=False,
            owner=owner,
        )
    except Exception as exc:
        logger.warning("Voice worker reply failed: %s", str(exc)[:200])
        return "I could not submit that answer; the task is still waiting.", "worker_question_reply_failed", [task_id]
    return f"I passed your answer to {label}.", "worker_question_reply", [task_id]


def _pending_task_accepts_turn(task: dict | None, text: str, selected_target: str) -> bool:
    if not task or task.get("status") not in {"waiting", "waiting_approval"}:
        return False
    worker = str(task.get("worker") or "")
    if selected_target == worker:
        return True
    if selected_target != "jarvis":
        return False
    if task.get("status") == "waiting_approval":
        return _approval_choice(text) is not None
    return _explicit_reply_target(text) == worker


def _question_answers(task: dict, text: str) -> dict[str, str]:
    question_event = next(
        (event for event in reversed(task.get("events", [])) if event.get("type") == "question"),
        {},
    )
    questions = (question_event.get("metadata") or {}).get("questions") or []
    ids = [str(question.get("id") or "") for question in questions if question.get("id")]
    return {question_id: text for question_id in ids} or {"voice": text}


def _validated_thread_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": "Invalid Codex thread ID"}) from exc


def _server_final_event(text: str, reply: str, guard_reason: str, task_ids: list[str] | None = None, **extra) -> dict:
    task_ids = task_ids or []
    return {
        "type": "final",
        "assistant_text": reply,
        "diagnostics": {
            "model": "odysseus-router",
            "transcript_chars": len(text),
            "assistant_chars": len(reply),
            "brain_ms": 0,
            "brain_first_token_ms": 0,
            "num_ctx": VOICE_CONTEXT_LENGTH,
            "num_predict": 0,
            "inference": False,
            "guard_reason": guard_reason,
            "task_ids": task_ids,
            **extra,
        },
        "task_ids": task_ids,
    }


def _business_status_prompt(text: str) -> str:
    return (
        "Give the operator a bounded, read-only Business status check that preserves the exact requested depth. "
        "Start with the central Business command center and only the newest dated client handovers needed to answer. "
        "Unless the operator explicitly asks for every client or a deep/full report, return at most three verified priorities in 250 words or fewer. "
        "Do not inventory every client, run capability or service discovery, or use external connectors unless the operator explicitly named that source. "
        "Mark stale or unknown facts clearly. Never infer meetings, schedules, workflows, deliverables, or client status. Make no changes.\n\n"
        f"The operator's exact request:\n{text}"
    )


def _prior_voice_exchange(voice_session: dict) -> str:
    turns = [
        turn for turn in voice_session.get("turns", [])
        if isinstance(turn, dict)
        and turn.get("role") in {"user", "assistant"}
        and isinstance(turn.get("text"), str)
    ]
    assistant_index = next(
        (index for index in range(len(turns) - 1, -1, -1) if turns[index]["role"] == "assistant"),
        None,
    )
    if assistant_index is None:
        return "none"
    user = next(
        (turns[index]["text"].strip() for index in range(assistant_index - 1, -1, -1) if turns[index]["role"] == "user"),
        "",
    )
    assistant = turns[assistant_index]["text"].strip()
    return f"Operator: {user[:950]}\nAssistant: {assistant[:950]}"[:2000]


def _logical_client_state(voice_session: dict) -> dict[str, Any]:
    source = voice_session.get("_client_state")
    if not isinstance(source, dict):
        return {}
    state: dict[str, Any] = {}
    if source.get("active_view") in {"calendar", "document", "chat"}:
        state["active_view"] = source["active_view"]
    for name in ("calendar", "document"):
        raw = source.get(name)
        if not isinstance(raw, dict):
            continue
        allowed = {key: raw[key] for key in ("open", "minimized") if isinstance(raw.get(key), bool)}
        if name == "calendar":
            if raw.get("view") in {"month", "week", "year", "agenda"}:
                allowed["view"] = raw["view"]
            if isinstance(raw.get("date"), str):
                allowed["date"] = raw["date"][:40]
        elif isinstance(raw.get("id"), str):
            allowed["id"] = raw["id"][:200]
        state[name] = allowed
    return state


def _worker_context_envelope(
    worker: str,
    workspace: str,
    permission_mode: str,
    exact_request: str,
    voice_session: dict,
    task_instructions: str | None = None,
) -> str:
    request = str(exact_request or "").strip()[:4000]
    instructions = str(task_instructions or "").strip()[:4000]
    prior = _prior_voice_exchange(voice_session)
    client_state = json.dumps(_logical_client_state(voice_session), separators=(",", ":"), sort_keys=True)
    instruction_block = f"task_instructions(<=4000):\n{instructions}\n" if instructions and instructions != request else ""
    return (
        "[JARVIS_CONTEXT v1]\n"
        f"worker={worker}; workspace={workspace}; permission={permission_mode}\n"
        f"exact_request(<=4000):\n{request}\n"
        f"{instruction_block}"
        f"prior_exchange(<=2000):\n{prior}\n"
        f"client_state={client_state}\n"
        "rules: branch=assigned worker/workspace only; evidence=verify facts and mark unknowns; "
        "output=concise factual progress/final; never simulate actions or other workers."
    )


async def _dispatch_worker_request(
    chat_session_id: str,
    worker: str,
    workspace: str,
    prompt: str,
    owner: str,
    voice_session: dict,
) -> tuple[dict, str]:
    from src.jarvis_agent import find_active_task, start_task, task_action

    permission_mode = "read_only"
    exact_request = str(voice_session.get("_exact_request") or prompt)
    prompt = _worker_context_envelope(
        worker,
        workspace,
        permission_mode,
        exact_request,
        voice_session,
        task_instructions=prompt,
    )
    active = find_active_task(chat_session_id, worker, workspace, owner)
    request_id = str(voice_session.get("_protocol_request_id") or uuid.uuid4())
    call = normalize_action_call(
        request_id=request_id,
        call_id=str(uuid.uuid4()),
        agent_id=configured_agent_id(),
        actor="odysseus:voice-router",
        capability_version="",
        name="start_agent_task",
        arguments={
            "action": "steer" if active else "start",
            "worker": worker,
            "workspace": workspace,
            "permission_mode": permission_mode,
            "prompt": prompt,
        },
        target="worker",
        authority_ref=None,
    )
    catalog = compose_capability_catalog(fallback_names={"start_agent_task"})
    call["capability_version"] = catalog["version"]
    record_operational_event(
        request_id=request_id,
        session_id=chat_session_id,
        call_id=call["call_id"],
        operator_id=operator_identity(owner),
        actor=call["actor"],
        component=call["target"],
        event_type="started",
        status="requested",
        metadata={"capability": call["name"]},
    )
    validation_error = validate_action_call(call, catalog)
    if validation_error:
        raise PermissionError(validation_error["category"])
    decision = authority_store.decide(
        call,
        operator_id=operator_identity(owner),
        session_id=chat_session_id,
    )
    call["authority_ref"] = decision["decision_id"]
    record_operational_event(
        request_id=request_id,
        session_id=chat_session_id,
        call_id=call["call_id"],
        operator_id=operator_identity(owner),
        actor="odysseus:authority",
        component="control_plane",
        event_type="approval",
        status={"allow": "authorized", "deny": "denied"}.get(decision["decision"], "approval_required"),
        evidence_refs=[{"decision_id": decision["decision_id"]}],
        metadata={
            "permission_mode": decision["permission_mode"],
            "action_effect": decision["action_effect"],
            "gate_reason": decision["gate_reason"],
            "policy_basis": decision["policy_basis"],
        },
    )
    if decision["decision"] != "allow":
        raise PermissionError("worker_dispatch_not_authorized")
    record_operational_event(
        request_id=request_id,
        session_id=chat_session_id,
        call_id=call["call_id"],
        operator_id=operator_identity(owner),
        actor=call["actor"],
        component=call["target"],
        event_type="progress",
        status="executed",
        evidence_refs=[{"decision_id": decision["decision_id"]}],
        metadata={"capability": call["name"]},
    )

    started = time.monotonic()
    action = "blocked"
    task: dict[str, Any] = {}
    try:
        if active:
            if worker in {"pc-codex", "vps-codex"} and active.get("status") not in {"waiting", "waiting_approval"}:
                try:
                    await task_action(
                        active["task_id"],
                        "steer",
                        {"prompt": prompt},
                        persist_user_message=False,
                        owner=owner,
                    )
                    task, action = active, "steered"
                except Exception as exc:
                    logger.info("%s active task rejected steering: %s", worker, str(exc)[:160])
            if not task:
                task, action = active, "busy"
        else:
            task = await start_task(
                worker,
                chat_session_id,
                workspace,
                prompt,
                permission_mode,
                False,
                owner,
            )
            action = "blocked" if task.get("status") == "blocked" or not task.get("task_id") else "started"
    except Exception as exc:
        record_operational_event(
            request_id=request_id, session_id=chat_session_id, call_id=call["call_id"],
            operator_id=operator_identity(owner), actor=call["actor"], component="worker",
            event_type="result", status="failed", duration=time.monotonic() - started, error=exc,
            metadata={"capability": call["name"], "worker": worker, "workspace": workspace},
        )
        raise
    record_operational_event(
        request_id=request_id,
        session_id=chat_session_id,
        task_id=str(task.get("task_id") or "") or None,
        call_id=call["call_id"],
        operator_id=operator_identity(owner),
        actor=call["actor"],
        component="worker",
        event_type="result",
        status="succeeded" if action in {"started", "steered"} else "degraded",
        duration=time.monotonic() - started,
        evidence_refs=[{"task_id": task.get("task_id"), "worker": worker, "workspace": workspace}],
        metadata={"capability": call["name"], "dispatch_action": action},
    )
    return task, action


async def _foreground_worker_result(
    task_id: str,
    owner: str,
    *,
    timeout: float = 90,
    poll_seconds: float = 0.25,
) -> tuple[str, str]:
    """Wait for one selected worker turn without turning it into a background-only reply."""
    from src.jarvis_agent import get_task

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = get_task(task_id) or {}
        status = str(task.get("status") or "")
        if status == "completed":
            result_event = next(
                (event for event in reversed(task.get("events") or []) if event.get("type") == "result"),
                {},
            )
            reply = str(result_event.get("spoken_text") or task.get("result") or "").strip()
            return status, reply
        if status in {"failed", "cancelled", "blocked"}:
            return status, ""
        await asyncio.sleep(poll_seconds)
    return "timeout", ""


async def _server_routed_events(chat_session_id: str, text: str, owner: str, voice_session: dict):
    voice_session.setdefault("_protocol_request_id", str(uuid.uuid4()))
    voice_session["_exact_request"] = text
    if _setup_status_command(text):
        status = await _voice_status_snapshot(
            owner,
            voice_session.get("_stt_service"),
            voice_session.get("_tts_service"),
        )
        setup = status["setup"]
        reply = setup["text"]
        yield {"type": "assistant_delta", "text": reply}
        final = _server_final_event(text, reply, "voice_setup_status")
        final["setup"] = setup
        yield final
        return

    oracle_intent = _oracle_protocol_intent(text, voice_session)
    if oracle_intent:
        active = bool(voice_session.get("oracle_protocol_active"))
        ui_event = None
        if oracle_intent == "suggest":
            pending = True
            reply = "Did you mean the ORACLE protocol, sir?"
            guard = "oracle_protocol_confirmation"
        elif oracle_intent == "decline":
            pending = False
            reply = "Very good, sir. ORACLE remains offline."
            guard = "oracle_protocol_declined"
        elif oracle_intent == "engage" and not ORACLE_PROTOCOL_URL:
            pending = False
            reply = "The ORACLE protocol is not configured on this Pandamonium host."
            guard = "oracle_protocol_unavailable"
        elif oracle_intent == "engage" and active:
            pending = False
            reply = "ORACLE is already online, sir."
            guard = "oracle_protocol_already_active"
        elif oracle_intent == "engage":
            pending = False
            active = True
            ui_event = "oracle_protocol_engage"
            reply = "ORACLE protocol engaged. You now have eyes in the sky, sir."
            guard = "oracle_protocol_engaged"
        elif not active:
            pending = False
            reply = "ORACLE is already offline, sir."
            guard = "oracle_protocol_already_offline"
        else:
            pending = False
            active = False
            ui_event = "oracle_protocol_shutdown"
            reply = "ORACLE protocol offline, sir."
            guard = "oracle_protocol_shutdown"

        voice_session["oracle_protocol_pending"] = pending
        voice_session["oracle_protocol_active"] = active
        _set_extension_engaged(voice_session, "oracle", active)
        voice_session_id = str(voice_session.get("id") or "")
        if voice_session_id:
            _set_oracle_protocol_state(voice_session_id, pending=pending, active=active)
        if ui_event:
            yield {"type": "ui_control", "ui_event": ui_event}
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard)
        return

    media = _media_command(text)
    if media:
        vision_model = ""
        if media == "camera_describe":
            frame = voice_session.get("_frame")
            if not isinstance(frame, dict) or not isinstance(frame.get("bytes"), bytes):
                reply = "My camera is not open, so I cannot see anything yet."
            else:
                chat_session = _voice_chat_session(chat_session_id)
                session_model = str(getattr(chat_session, "model", "") or "")
                session_endpoint = str(getattr(chat_session, "endpoint_url", "") or "")
                session_headers = dict(getattr(chat_session, "headers", {}) or {})
                preferred_candidate = (
                    (session_endpoint, session_model, session_headers)
                    if model_supports_vision(session_model, session_endpoint)
                    else None
                )
                result = await asyncio.to_thread(
                    analyze_image_bytes_with_vl_result,
                    frame["bytes"],
                    frame["mime"],
                    owner,
                    preferred_candidate,
                )
                description = str(result.get("text") or "").strip()
                vision_model = str(result.get("model") or "")
                if not description or description.startswith("["):
                    reply = "I could not analyze the camera frame with a vision-capable model."
                else:
                    reply = description
        else:
            event = {
                "camera_open": {"type": "ui_control", "ui_event": "camera_open"},
                "camera_close": {"type": "ui_control", "ui_event": "camera_close"},
                "media_motivation": {
                    "type": "ui_control",
                    "ui_event": "media_play",
                    "media_id": "motivational-abstract",
                },
            }[media]
            yield event
            reply = {
                "camera_open": "Opening my eyes.",
                "camera_close": "Closing my eyes.",
                "media_motivation": "Playing the built-in motivational visual.",
            }[media]
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(
            text,
            reply,
            media,
            vision_model=vision_model,
        )
        return

    foreground = _foreground_command(text)
    if foreground:
        action, view = foreground
        client_state = voice_session.get("_client_state")
        if action == "report_view_state":
            reply = _describe_client_view(client_state)
        else:
            document_state = (
                client_state.get("document")
                if isinstance(client_state, dict) and isinstance(client_state.get("document"), dict)
                else {}
            )
            if action in {"close_view", "minimize_view"} and not isinstance(client_state, dict):
                reply = "I cannot confirm an active document from this turn, so I did not change the view."
            elif action == "minimize_view" and document_state.get("minimized"):
                reply = "The document is already minimized."
            elif action in {"close_view", "minimize_view"} and not (
                document_state.get("open") or document_state.get("minimized")
            ):
                reply = "There is no active document to close." if action == "close_view" else "There is no active document to minimize."
            else:
                yield {"type": "ui_control", "ui_event": action, "view": view}
                reply = {
                    "open_view": "Opening Calendar.",
                    "close_view": "Closing the active document.",
                    "minimize_view": "Minimizing the active document.",
                }[action]
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, f"foreground_{action}")
        return

    calendar_args = _calendar_read_args(text)
    if calendar_args:
        result = await do_read_calendar(json.dumps(calendar_args), owner=owner)
        freshness = str(result.get("calendar_freshness") or "")
        warning = (
            "Calendar freshness could not be confirmed, so this answer uses the last synchronized copy. "
            if freshness and freshness != "fresh"
            else ""
        )
        if result.get("exit_code") not in {None, 0}:
            reply = warning + "I could not read your Calendar data."
        else:
            reply = warning + str(result.get("response") or "No Calendar results were returned.").strip()
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, "calendar_read")
        return

    task_control = await _run_task_control(chat_session_id, text, owner, voice_session)
    if task_control:
        reply, guard, task_ids = task_control
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard, task_ids)
        return

    if _unsupported_voice_control(text):
        reply = (
            "I did not run that control. I can handle one allowlisted Calendar, document, camera, "
            "or built-in motivational action at a time."
        )
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, "unsupported_voice_control")
        return

    selected_target = str(voice_session.get("target") or "jarvis")
    requested_target_switch = _target_switch(text)
    direct_jarvis_return = (
        selected_target != "jarvis"
        and not requested_target_switch
        and _jarvis_vocative(text)
    )
    direct_jarvis_delegations = _background_delegations(text) if direct_jarvis_return else []
    target_switch = requested_target_switch or ("jarvis" if direct_jarvis_return and not direct_jarvis_delegations else None)
    if direct_jarvis_return and direct_jarvis_delegations:
        selected_target = "jarvis"
        yield {"type": "target_changed", "target": "jarvis", "workspace": _workspace_for_text(text)}
    if target_switch:
        workspace = {
            "vps-codex": "vps-ops",
            "hermes": "home-lab",
        }.get(target_switch, _workspace_for_text(text))
        label = VOICE_TARGET_LABELS.get(target_switch, "Jarvis")
        chat_session = _SESSION_MANAGER.get_session(chat_session_id) if _SESSION_MANAGER else None
        origin_target = _voice_origin_target(voice_session, chat_session)
        target_connected = True
        if target_switch in DIRECT_MODEL_TARGETS and target_switch != origin_target:
            target_connected = bool(_resolve_voice_target_endpoint(target_switch, owner))
        elif target_switch not in DIRECT_MODEL_TARGETS:
            from src.jarvis_agent import worker_statuses

            target_status = (await worker_statuses()).get(target_switch) or {}
            target_connected = bool(target_status.get("enabled"))
        if not target_connected:
            reply = f"{label} is not connected, so I have not switched you or claimed a task is running."
            yield {"type": "assistant_delta", "text": reply}
            yield {
                "type": "final",
                "assistant_text": reply,
                "diagnostics": {
                    "model": "odysseus-router",
                    "transcript_chars": len(text),
                    "assistant_chars": len(reply),
                    "brain_ms": 0,
                    "brain_first_token_ms": 0,
                    "num_ctx": VOICE_CONTEXT_LENGTH,
                    "num_predict": 0,
                    "guard_reason": f"{target_switch}_not_connected",
                    "task_ids": [],
                },
                "task_ids": [],
            }
            return
        reply = (
            _casual_greeting_reply(text, voice_session)
            if target_switch == "jarvis" and _is_casual_greeting(text)
            else "You’re back with Jarvis."
            if target_switch == "jarvis"
            else f"Transferring you to {label} now—one moment, please."
        )
        if target_switch == "jarvis":
            yield {"type": "target_changed", "target": target_switch, "workspace": workspace}
            yield {"type": "assistant_delta", "text": reply}
        else:
            yield {"type": "assistant_delta", "text": reply}
            yield {"type": "target_changed", "target": target_switch, "workspace": workspace}
        yield {"type": "handoff_greeting", "target": target_switch, "workspace": workspace}
        yield _server_final_event(text, reply, f"target_switch_{target_switch}")
        return

    if selected_target == "hermes":
        try:
            from src.jarvis_agent import direct_hermes_turn

            reply = await direct_hermes_turn(
                chat_session_id,
                text,
                owner=owner,
                workspace="home-lab",
            )
        except Exception as exc:
            label = WORKER_LABELS.get("hermes", "Hermes")
            logger.warning("Direct Hermes turn failed: %s", str(exc)[:200])
            reply = f"{label} is unavailable, so I did not send that through Jarvis."
            yield {"type": "assistant_delta", "text": reply, "model": "Pandamonium"}
            yield _server_final_event(
                text,
                reply,
                "direct_hermes_unavailable",
                direct_target="hermes",
                character_name="Pandamonium",
                model="odysseus-router",
            )
            return
        label = WORKER_LABELS.get("hermes", "Hermes")
        yield {"type": "assistant_delta", "text": reply, "model": label}
        yield _server_final_event(
            text,
            reply,
            "direct_hermes",
            direct_target="hermes",
            character_name=label,
            model="hermes-agent",
        )
        return

    if selected_target == "jarvis" and _is_casual_greeting(text):
        reply = _casual_greeting_reply(text, voice_session)
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, "casual_greeting")
        return

    delegations = direct_jarvis_delegations or _background_delegations(text)
    retry_task = None
    retry_requested = not delegations and _is_worker_retry_request(text)
    if retry_requested:
        from src.jarvis_agent import require_task_owner

        for snapshot in reversed(voice_session.get("tasks") or []):
            try:
                candidate = require_task_owner(str(snapshot.get("task_id") or ""), owner)
            except (KeyError, PermissionError):
                continue
            if (
                candidate
                and candidate.get("session_id") == chat_session_id
                and candidate.get("status") in {"completed", "failed", "cancelled", "blocked"}
                and candidate.get("worker") in WORKER_LABELS
                and candidate.get("prompt")
            ):
                retry_task = candidate
                delegations = [(candidate["worker"], candidate.get("workspace") or "home-lab")]
                break
        if not retry_task:
            reply = "I don’t have a completed worker request in this voice session to retry. Tell me which worker and request you mean."
            yield {"type": "assistant_delta", "text": reply}
            yield _server_final_event(text, reply, "retry_task_missing")
            return
    document_open = _is_document_open_request(text) or bool(
        retry_task and "ODYSSEUS_ARTIFACT" in str(retry_task.get("prompt") or "")
    )
    if delegations:
        compound = len(delegations) > 1
        dispatches = []
        for worker, workspace in delegations:
            label = WORKER_LABELS[worker]
            scope = (
                f"This is the {label} branch of a compound Jarvis request. Handle only the work explicitly "
                f"assigned to {label}; ignore instructions for other named workers and do not claim you contacted them.\n\n"
                if compound
                else ""
            )
            if retry_task:
                prompt = str(retry_task["prompt"])
            elif worker == "pc-codex" and _asks_current_business(text):
                prompt = scope + _business_status_prompt(text)
            else:
                prompt = (
                    f"{scope}The authenticated operator asked through voice. Handle this read-only request and report factual "
                    f"progress and the final result:\n\n{text}"
                )
            if document_open and worker == "pc-codex" and "ODYSSEUS_ARTIFACT" not in prompt:
                prompt += (
                    "\n\nPandamonium is the default destination. Verify the exact text document, then open it "
                    "with the required ODYSSEUS_ARTIFACT marker. Do not use a desktop opener."
                )
            dispatches.append((worker, workspace, prompt))

        outcomes = await asyncio.gather(*(
            _dispatch_worker_request(chat_session_id, worker, workspace, prompt, owner, voice_session)
            for worker, workspace, prompt in dispatches
        ), return_exceptions=True)
        results = []
        for (worker, workspace, _prompt), outcome in zip(dispatches, outcomes):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning("Jarvis could not dispatch %s task: %s", worker, str(outcome)[:240])
                task, action = {}, "blocked"
            else:
                task, action = outcome
            results.append((worker, workspace, task, action))

        task_ids = [
            task["task_id"] for _, _, task, action in results
            if task.get("task_id") and action != "blocked"
        ]
        for worker, workspace, task, action in results:
            if action in {"started", "steered"}:
                yield {
                    "type": "agent_task",
                    "task_id": task["task_id"],
                    "worker": worker,
                    "workspace": workspace,
                    "foreground": False,
                }

        if not compound:
            worker, workspace, task, action = results[0]
            label = WORKER_LABELS[worker]
            if worker == "pc-codex" and _asks_current_business(text):
                if action == "blocked":
                    reply = "PC Codex is not connected, so I could not start the live business inspection. I have not claimed that it is running."
                    guard_reason = "pc-codex_not_connected"
                elif action == "busy":
                    reply = "PC Codex is still working and could not accept another instruction yet. You can wait or cancel that task."
                    guard_reason = "current_business_busy"
                else:
                    reply = (
                        "I’m not current enough to answer that reliably, so I’m asking PC Codex to check the live Business sources now."
                        if action == "started"
                        else "I’m not current enough to answer that reliably, so I passed the request to the active PC Codex task."
                    )
                    guard_reason = f"current_business_{action}"
            elif action == "started":
                reply = (
                    f"I’m asking {label} to retry that request. I’ll keep you updated here."
                    if retry_task
                    else f"I’m asking {label} to open that in Pandamonium. I’ll keep you updated here."
                    if document_open and worker == "pc-codex"
                    else f"I’m asking {label} to handle that in the {workspace} workspace. I’ll keep you updated here."
                )
            elif action == "steered":
                reply = f"I’ve passed that follow-up to the active {label} task."
            elif action == "busy":
                reply = f"{label} is still working and could not accept another instruction yet. You can wait, cancel it, or switch agents."
            else:
                reply = f"{label} is not connected, so I could not start the request."
            if not (worker == "pc-codex" and _asks_current_business(text)):
                guard_reason = f"delegation_{action}_{worker}"
        else:
            replies = []
            for worker, _workspace, _task, action in results:
                label = WORKER_LABELS[worker]
                if action == "started":
                    replies.append(
                        f"{label} is opening the document in Pandamonium."
                        if worker == "pc-codex" and document_open
                        else f"{label} is handling its part in the background."
                    )
                elif action == "steered":
                    replies.append(f"I passed {label}'s part to its active task.")
                elif action == "busy":
                    replies.append(f"{label} is busy and could not accept its part yet.")
                else:
                    replies.append(f"{label} is not connected, so its part did not start.")
            if any(action in {"started", "steered"} for _, _, _, action in results):
                replies.append("I’ll keep you updated here.")
            reply = " ".join(replies)
            guard_reason = "delegation_multi_" + "_".join(
                f"{worker}_{action}" for worker, _, _, action in results
            )

        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard_reason, task_ids)
        return

    selected_workspace = _selected_workspace(text, str(voice_session.get("workspace") or "home-lab"))

    selected_reply = await _run_task_control(
        chat_session_id,
        text,
        owner,
        voice_session,
        selected_reply=True,
    )
    if selected_reply:
        reply, guard, task_ids = selected_reply
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard, task_ids)
        return

    if _asks_runtime_status(text):
        chat_session = _voice_chat_session(chat_session_id)
        model = str(getattr(chat_session, "model", "") or "unknown model")
        character = _voice_character_name(voice_session)
        settings = load_settings()
        provider = str(settings.get("tts_provider") or "disabled")
        voice = _tts_voice_for_final({"diagnostics": {"character_name": character}}) or str(settings.get("tts_voice") or "default")
        reply = (
            f"I am {character}, using {model} for this voice call. "
            f"Speech is rendered through {provider}, using {voice}."
        )
        yield {"type": "assistant_delta", "text": reply}
        yield {
            "type": "final",
            "assistant_text": reply,
            "diagnostics": {
                "model": model,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "brain_ms": 0,
                "num_ctx": VOICE_CONTEXT_LENGTH,
                "num_predict": 0,
                "guard_reason": "server_runtime_status",
                "character_name": character,
                "task_ids": [],
            },
            "task_ids": [],
        }
        return

    if selected_target not in DIRECT_MODEL_TARGETS:
        worker = selected_target
        workspace = "vps-ops" if worker == "vps-codex" else ("home-lab" if worker == "hermes" else selected_workspace)
        label = WORKER_LABELS.get(worker, "Worker")
        try:
            task, action = await _dispatch_worker_request(
                chat_session_id, worker, workspace, text, owner, voice_session,
            )
        except Exception as exc:
            logger.warning("Jarvis could not dispatch selected %s task: %s", worker, str(exc)[:240])
            task, action = {}, "blocked"
        task_ids = [task["task_id"]] if task.get("task_id") and action != "blocked" else []
        if action in {"started", "steered"}:
            yield {
                "type": "agent_task",
                "task_id": task["task_id"],
                "worker": worker,
                "workspace": workspace,
                "foreground": True,
            }
        foreground_status = ""
        foreground_reply = ""
        if worker == "pc-codex" and action in {"started", "steered"} and task_ids:
            foreground_status, foreground_reply = await _foreground_worker_result(task_ids[0], owner)
        if foreground_status == "completed" and foreground_reply:
            reply = foreground_reply
            action = "completed"
        elif foreground_status in {"failed", "cancelled", "blocked"}:
            reply = f"{label} could not complete that request. The task details are in the chat."
            action = foreground_status
        elif action == "started":
            reply = f"{label} is still working. I’ll deliver the result here when it finishes."
        elif action == "steered":
            reply = f"I passed that follow-up to {label}'s active task."
        elif action == "busy":
            reply = f"{label} is still working and cannot accept another instruction yet. You can wait, cancel it, or switch agents."
        else:
            reply = f"{label} is not connected, so I could not start the request."
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, f"selected_{action}_{worker}", task_ids)
        return

    if _asks_current_business(text):
        prompt = _business_status_prompt(text)
        try:
            task, action = await _dispatch_worker_request(
                chat_session_id, "pc-codex", "business", prompt, owner, voice_session,
            )
        except Exception as exc:
            logger.warning("Jarvis could not start current-business task: %s", str(exc)[:240])
            task, action = {}, "blocked"
        if action == "blocked":
            reply = "PC Codex is not connected, so I could not start the live business inspection. I have not claimed that it is running."
            task_ids = []
            guard_reason = "pc-codex_not_connected"
        elif action == "busy":
            reply = "PC Codex is still working and could not accept another instruction yet. You can wait or cancel that task."
            task_ids = [task["task_id"]]
            guard_reason = "current_business_busy"
        else:
            reply = (
                "I’m not current enough to answer that reliably, so I’m asking PC Codex to check the live Business sources now."
                if action == "started"
                else "I’m not current enough to answer that reliably, so I passed the request to the active PC Codex task."
            )
            task_ids = [task["task_id"]]
            guard_reason = f"current_business_{action}"
            yield {
                "type": "agent_task",
                "task_id": task["task_id"],
                "worker": "pc-codex",
                "workspace": "business",
                "foreground": False,
            }
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(
            text,
            reply,
            guard_reason,
            task_ids,
        )
        return


async def _jarvis_events(chat_session_id: str, text: str, owner: str, voice_session: dict):
    if not chat_session_id:
        raise RuntimeError("voice_chat_session_missing")
    chat_session = _SESSION_MANAGER.get_session(chat_session_id) if _SESSION_MANAGER else None
    if not chat_session:
        raise RuntimeError("voice_chat_session_not_found")
    voice_session["_protocol_request_id"] = str(uuid.uuid4())
    selected_target = str(voice_session.get("target") or "jarvis")
    origin_target = _voice_origin_target(voice_session, chat_session)
    selected_pc_codex_task = (
        selected_target == "pc-codex" and _selected_pc_codex_task_request(text)
    )
    if (
        _media_command(text)
        or _foreground_command(text)
        or _calendar_read_args(text)
        or _setup_status_command(text)
        or _unsupported_voice_control(text)
        or _task_control_intent(text)
        or _target_switch(text)
        or _asks_runtime_status(text)
        or (
            selected_target not in DIRECT_MODEL_TARGETS
            and selected_target != "pc-codex"
        )
        or selected_pc_codex_task
        or (
            selected_target == "jarvis"
            and (
                _is_casual_greeting(text)
                or _background_delegation(text)
                or _asks_current_business(text)
            )
        )
    ):
        async for event in _server_routed_events(chat_session_id, text, owner, voice_session):
            yield event
        return
    endpoint_url = chat_session.endpoint_url
    model = chat_session.model
    headers = getattr(chat_session, "headers", None)
    if VOICE_ENDPOINT_ID or VOICE_MODEL:
        endpoint_url, model, headers = _resolve_voice_runtime(owner, chat_session)
    if selected_target != origin_target and selected_target != "pc-codex":
        resolved = _resolve_voice_target_endpoint(selected_target, owner)
        if not resolved:
            label = VOICE_TARGET_LABELS.get(selected_target, selected_target)
            raise RuntimeError(f"{label} voice endpoint is not connected")
        endpoint_url, model, headers = resolved
    context_messages = chat_session.get_context_messages()
    messages = [{"role": "system", "content": _voice_system_prompt(voice_session)}, *context_messages]
    full_response = ""
    task_ids: list[str] = []
    extension_tools_used: list[str] = []
    pending_authority: dict[str, Any] | None = None
    started = time.perf_counter()
    first_token_ms: int | None = None
    metrics: dict[str, Any] = {}
    voice_tools = (
        JARVIS_TOOLS - {"start_agent_task", "read_agent_task"}
        if selected_target == "pc-codex"
        else JARVIS_TOOLS
    )
    extension_specs = _extension_tool_specs(voice_session) if selected_target == "jarvis" else []
    extension_names = {tool["name"] for tool in extension_specs}
    extension_schemas = _extension_tool_schemas(extension_specs)
    extension_context = _extension_context(voice_session, extension_specs)
    if extension_names:
        voice_tools = voice_tools | extension_names
    async for chunk in stream_agent_loop(
        endpoint_url,
        model,
        messages,
        headers=headers,
        temperature=0.35,
        max_tokens=_num_predict_for_text(text),
        max_rounds=8,
        max_tool_calls=6,
        context_length=VOICE_CONTEXT_LENGTH,
        session_id=chat_session_id,
        disabled_tools=set(TOOL_TAGS) - voice_tools,
        owner=owner,
        relevant_tools=voice_tools,
        extra_tool_schemas=extension_schemas,
        extension_capabilities={
            tool["name"]: {
                "extension_id": tool["extension_id"],
                "permission_mode": tool["permission_mode"],
            }
            for tool in extension_specs
        },
        tool_executor=(
            _extension_tool_executor(voice_session, owner, extension_specs)
            if extension_specs
            else None
        ),
        context_extensions=extension_context,
    ):
        if not chunk.startswith("data: "):
            continue
        payload = chunk[6:].strip()
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "authority_approval_required" and isinstance(data.get("data"), dict):
            pending_authority = data["data"]
            capability = str((pending_authority.get("capability") or {}).get("name") or "this action")
            summary = f"Approval required for {capability}. I opened the exact decision in chat."
            full_response = summary
            if first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            yield {"type": "authority_approval_required", "data": pending_authority}
            yield {"type": "assistant_delta", "text": summary}
        elif data.get("type") == "ui_control" and isinstance(data.get("data"), dict):
            control = dict(data["data"])
            ui_event = str(control.get("ui_event") or "")
            if ui_event in {"oracle_protocol_engage", "oracle_protocol_shutdown"}:
                active = ui_event == "oracle_protocol_engage"
                voice_session["oracle_protocol_pending"] = False
                voice_session["oracle_protocol_active"] = active
                _set_extension_engaged(voice_session, "oracle", active)
                voice_session_id = str(voice_session.get("id") or "")
                if voice_session_id:
                    _set_oracle_protocol_state(voice_session_id, pending=False, active=active)
            yield {"type": "ui_control", **control}
        elif "delta" in data and not data.get("thinking"):
            if pending_authority:
                continue
            delta = str(data.get("delta") or "")
            if delta and first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            full_response += delta
            if delta:
                yield {"type": "assistant_delta", "text": delta}
        elif data.get("type") == "tool_progress" and isinstance(data.get("extension_call"), dict):
            call = data["extension_call"]
            yield {
                "type": "ui_control",
                "ui_event": "extension_protocol_command",
                "call_id": str(call.get("call_id") or ""),
                "extension_id": str(call.get("extension_id") or ""),
                "tool": str(call.get("tool") or ""),
                "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                "server_managed": True,
            }
        elif data.get("type") == "tool_output":
            tool = str(data.get("tool") or "")
            if tool in extension_names:
                extension_tools_used.append(tool)
            if tool == "start_agent_task":
                try:
                    tool_data = json.loads(str(data.get("output") or "{}"))
                    task_id = str(tool_data.get("task_id") or "")
                    if task_id and task_id not in task_ids:
                        task_ids.append(task_id)
                        yield {"type": "agent_task", "task_id": task_id, "worker": tool_data.get("worker")}
                except json.JSONDecodeError:
                    pass
        elif data.get("type") == "metrics":
            metrics = data.get("data") or {}
    reply = _strip_think_blocks(full_response).strip()
    if not reply:
        raise RuntimeError("Jarvis voice model returned empty content")
    diagnostics = {
        "model": model,
        "transcript_chars": len(text),
        "assistant_chars": len(reply),
        "brain_ms": int((time.perf_counter() - started) * 1000),
        "brain_first_token_ms": first_token_ms,
        "num_ctx": VOICE_CONTEXT_LENGTH,
        "num_predict": _num_predict_for_text(text),
        "inference": True,
        "guard_reason": (
            "authority_approval_required" if pending_authority
            else "friday_conversation" if selected_target == "pc-codex"
            else "extension_native_tools" if extension_tools_used
            else None
        ),
        "agent_metrics": metrics,
        "character_name": _voice_character_name(voice_session),
        "task_ids": task_ids,
    }
    if selected_target in {"friday", "pc-codex"}:
        diagnostics["direct_target"] = "friday"
    yield {"type": "final", "assistant_text": reply, "diagnostics": diagnostics, "task_ids": task_ids}


async def _jarvis_reply(
    chat_session_id: str,
    text: str,
    owner: str,
    voice_session: dict | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    final: dict[str, Any] | None = None
    async for event in _jarvis_events(chat_session_id, text, owner, voice_session or {}):
        if event.get("type") == "final":
            final = _decorate_voice_final(event, voice_session or {})
    if not final:
        raise RuntimeError("Jarvis voice model returned no final event")
    return final["assistant_text"], final["diagnostics"], final.get("task_ids") or []


def _append_diagnostic(session: dict, diagnostic: dict[str, Any]) -> None:
    diagnostic = {**diagnostic, "created_at": _now()}
    diagnostics = session.setdefault("diagnostics", [])
    diagnostics.append(diagnostic)
    del diagnostics[:-50]


def _clean_client_timings(timings: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in timings.items():
        if not isinstance(key, str) or len(key) > 80:
            continue
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = round(float(value), 2)
        elif isinstance(value, str):
            cleaned[key] = value[:120]
    return cleaned


def _chat_session_name() -> str:
    return f"{configured_agent_name()} Voice {now_user_local().strftime('%I:%M %p').lstrip('0')}"


def _append_chat_message(session_manager, session: dict, role: str, text: str, **metadata) -> None:
    chat_session_id = session.get("chat_session_id")
    if not session_manager or not chat_session_id or not text:
        return
    safe_metadata = {
        "source": "jarvis_voice",
        "voice_session_id": session.get("id"),
        **{k: v for k, v in metadata.items() if v is not None},
    }
    try:
        session_manager.add_message(chat_session_id, ChatMessage(role, text, metadata=safe_metadata))
    except Exception as exc:
        logger.warning("Failed to append Jarvis voice turn to chat session %s: %s", chat_session_id, exc)


def _assistant_identity_metadata(diagnostics: dict[str, Any]) -> dict[str, str]:
    character_name = str(diagnostics.get("character_name") or "").strip()
    direct_target = str(diagnostics.get("direct_target") or "").strip()
    if not character_name or not direct_target:
        return {}
    return {
        "source": "direct_worker_voice",
        "character_name": character_name,
        "target": direct_target,
    }


def setup_voice_routes(session_manager=None, stt_service=None, tts_service=None):
    global _SESSION_MANAGER
    # Preserve the established two-positional-argument call shape where the
    # second argument is TTS, while allowing the full app to pass STT and TTS.
    if tts_service is None and stt_service is not None:
        tts_service = stt_service
        stt_service = None
    _SESSION_MANAGER = session_manager
    router = APIRouter(prefix="/api/voice", tags=["voice"])

    @router.get("/status")
    async def voice_status(_owner: str = Depends(require_user)):
        server_tts_ready, tts_provider = _server_tts_readiness(tts_service)
        setup_status = await _voice_status_snapshot(_owner, stt_service, tts_service)
        return {
            **setup_status,
            "mode": "jarvis_call",
            "activation": "call_button",
            "interruption": "stop_and_redirect",
            "stores_raw_audio": False,
            "stt_endpoint": "pc-whisper-stt",
            "brain_endpoint": "selected-chat-session",
            "voice_model": None,
            "tts_provider": tts_provider,
            "server_tts_ready": server_tts_ready,
            "server_tts_error": None if server_tts_ready else VOICE_SERVER_TTS_ERROR,
            "fast_rtc_mounted": False,
            "action_bridge": ACTION_BRIDGE_URL,
            "safe_actions": sorted(SAFE_ACTIONS),
        }

    @router.get("/oracle-config")
    async def oracle_config(_owner: str = Depends(require_user)):
        return {
            "oracle_protocol_url": ORACLE_PROTOCOL_URL,
            "extension_surfaces": _extension_surface_configs(),
        }

    @router.post("/prewarm")
    async def prewarm_voice_brain(_owner: str = Depends(require_user)):
        _require_server_tts(tts_service)

        async def prewarm_tts() -> tuple[str, bool | None, int | None, str | None]:
            if TTS_INFERENCE_LOCK.locked():
                return "busy", None, None, None

            await TTS_INFERENCE_LOCK.acquire()
            started = time.perf_counter()
            job = asyncio.create_task(asyncio.to_thread(tts_service.synthesize, "Ready.", False))

            def release_when_done(done: asyncio.Task) -> None:
                try:
                    done.exception()
                except (asyncio.CancelledError, Exception):
                    pass
                if TTS_INFERENCE_LOCK.locked():
                    TTS_INFERENCE_LOCK.release()

            try:
                try:
                    audio = await asyncio.wait_for(
                        asyncio.shield(job),
                        timeout=VOICE_TTS_PREWARM_TIMEOUT_SECONDS,
                    )
                    return (
                        "warmed" if audio else "failed",
                        bool(audio),
                        int((time.perf_counter() - started) * 1000),
                        None if audio else "TTS prewarm returned no audio",
                    )
                except asyncio.TimeoutError:
                    timeout_ms = int(VOICE_TTS_PREWARM_TIMEOUT_SECONDS * 1000)
                    return "failed", False, timeout_ms, f"TTS prewarm exceeded {timeout_ms // 1000} seconds"
                except Exception as exc:
                    logger.warning("Jarvis TTS prewarm failed: %s", exc)
                    return "failed", False, int((time.perf_counter() - started) * 1000), str(exc)[:200]
            finally:
                if job.done():
                    release_when_done(job)
                else:
                    job.add_done_callback(release_when_done)

        tts_task = asyncio.create_task(prewarm_tts())
        tts_state, tts_ok, tts_ms, tts_error = await tts_task

        return {
            "ok": tts_state != "failed",
            "brain_state": "selected-chat-session",
            "tts_state": tts_state,
            "tts_ok": tts_ok,
            "tts_ms": tts_ms,
            "tts_error": tts_error,
        }

    @router.post("/sessions")
    async def create_voice_session(
        request: Request,
        payload: VoiceSessionCreate,
        owner: str = Depends(require_user),
    ):
        _require_server_tts(tts_service)
        _set_user_time_from_request(request)
        state = _load_state()
        session_id = str(uuid.uuid4())
        chat_session_id = payload.chat_session_id.strip() if payload.chat_session_id else None
        runtime_endpoint_url = ""
        runtime_model = ""
        if session_manager:
            if chat_session_id:
                try:
                    linked = session_manager.get_session(chat_session_id)
                    linked_owner = getattr(linked, "owner", None)
                    if linked_owner != owner and not (not owner and linked_owner is None):
                        raise HTTPException(status_code=403, detail={"message": "Chat session does not belong to this user"})
                    runtime_endpoint_url = str(getattr(linked, "endpoint_url", "") or "")
                    runtime_model = str(getattr(linked, "model", "") or "")
                except HTTPException:
                    raise
                except Exception:
                    chat_session_id = None
            if not chat_session_id:
                resolved = (
                    resolve_endpoint_by_id(payload.endpoint_id, payload.model, owner=owner)
                    if payload.endpoint_id
                    else resolve_endpoint("default", owner=owner)
                )
                if not resolved or not resolved[0] or not resolved[1]:
                    raise HTTPException(
                        status_code=409,
                        detail={"message": "Select a configured model before starting voice"},
                    )
                endpoint_url, model, headers = resolved
                runtime_endpoint_url = endpoint_url
                runtime_model = model
                chat_session_id = str(uuid.uuid4())
                created_session = session_manager.create_session(
                    session_id=chat_session_id,
                    name=_chat_session_name(),
                    endpoint_url=endpoint_url,
                    model=model,
                    owner=owner,
                )
                if created_session is not None:
                    created_session.headers = headers or {}
                    if headers:
                        from routes.session_routes import _persist_session_headers

                        _persist_session_headers(chat_session_id, headers)
        session = {
            "id": session_id,
            "owner": owner,
            "chat_session_id": chat_session_id,
            "mode": payload.mode,
            "status": "listening",
            "created_at": _now(),
            "updated_at": _now(),
            "turns": [],
            "tasks": [],
            "target": _runtime_voice_target(runtime_endpoint_url, runtime_model),
            "origin_target": _runtime_voice_target(runtime_endpoint_url, runtime_model),
            "workspace": "home-lab",
            "active_task_id": None,
            "codex_thread_id": None,
            "stores_raw_audio": False,
            "oracle_protocol_url": ORACLE_PROTOCOL_URL,
            "oracle_protocol_pending": False,
            "oracle_protocol_active": False,
            "engaged_extensions": [],
        }
        state.setdefault("sessions", {})[session_id] = session
        _save_state(state)
        return {**session, "extension_surfaces": _extension_surface_configs()}

    @router.get("/sessions/{session_id}")
    async def get_voice_session(
        session_id: str,
        request: Request,
        owner: str = Depends(require_user),
    ):
        return {
            **_owned_voice_session(
                session_id,
                owner,
                session_manager=session_manager,
                request=request,
            ),
            "extension_surfaces": _extension_surface_configs(),
        }

    def accept_extension_tool_result(
        session_id: str,
        extension_id: str,
        payload: VoiceExtensionToolResult,
        owner: str,
    ):
        session = _owned_session(_load_state(), session_id, owner)
        pending = _EXTENSION_TOOL_CALLS.get((session_id, extension_id, payload.call_id))
        label = extension_id.upper()
        if not pending or pending.get("owner") != owner:
            raise HTTPException(status_code=404, detail={"message": f"{label} tool call not found"})
        if not _extension_browser_available(session, extension_id):
            raise HTTPException(status_code=409, detail={"message": f"{label} browser surface is no longer available"})
        if pending.get("tool") != payload.tool:
            raise HTTPException(status_code=409, detail={"message": f"{label} tool result does not match pending call"})
        if len(json.dumps(payload.result, ensure_ascii=True).encode("utf-8")) > 1_000_000:
            raise HTTPException(status_code=413, detail={"message": f"{label} tool result is too large"})
        future = pending.get("future")
        if not isinstance(future, asyncio.Future) or future.done():
            raise HTTPException(status_code=409, detail={"message": f"{label} tool call is no longer pending"})
        future.set_result(payload.result)
        return {"accepted": True}

    @router.post("/sessions/{session_id}/extensions/{extension_id}/results")
    async def submit_extension_tool_result(
        session_id: str,
        extension_id: str,
        payload: VoiceExtensionToolResult,
        owner: str = Depends(require_user),
    ):
        if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
            raise HTTPException(status_code=400, detail={"message": "Invalid extension ID"})
        return accept_extension_tool_result(session_id, extension_id, payload, owner)

    @router.post("/sessions/{session_id}/oracle-results")
    async def submit_oracle_tool_result(
        session_id: str,
        payload: VoiceExtensionToolResult,
        owner: str = Depends(require_user),
    ):
        return accept_extension_tool_result(session_id, "oracle", payload, owner)

    @router.post("/sessions/{session_id}/target")
    async def update_voice_target(
        session_id: str,
        payload: VoiceTargetUpdate,
        owner: str = Depends(require_user),
    ):
        if payload.target not in ACTIVE_VOICE_TARGETS:
            raise HTTPException(status_code=409, detail={"message": "Voice worker is not connected"})
        if payload.target not in DIRECT_MODEL_TARGETS:
            from src.jarvis_agent import worker_statuses

            target_status = (await worker_statuses()).get(payload.target) or {}
            if not target_status.get("enabled"):
                raise HTTPException(status_code=409, detail={"message": "Voice worker is not connected"})
        if payload.workspace not in VOICE_WORKSPACES:
            raise HTTPException(status_code=400, detail={"message": "Unknown voice workspace"})
        state = _load_state()
        session = _owned_session(state, session_id, owner)
        linked = session_manager.get_session(session.get("chat_session_id")) if session_manager else None
        origin_target = _voice_origin_target(session, linked)
        if payload.target in DIRECT_MODEL_TARGETS and payload.target != origin_target:
            if not _resolve_voice_target_endpoint(payload.target, owner):
                raise HTTPException(status_code=409, detail={"message": "Voice worker is not connected"})
        session["target"] = payload.target
        session["workspace"] = payload.workspace
        if payload.task_id is not None:
            session["active_task_id"] = payload.task_id or None
        if payload.codex_thread_id is not None:
            session["codex_thread_id"] = _validated_thread_id(payload.codex_thread_id)
        session["updated_at"] = _now()
        _save_state(state)
        return {
            "target": session["target"],
            "workspace": session["workspace"],
            "active_task_id": session.get("active_task_id"),
            "codex_thread_id": session.get("codex_thread_id"),
        }

    @router.post("/sessions/{session_id}/turns")
    async def add_voice_turn(
        session_id: str,
        payload: VoiceTurnCreate,
        owner: str = Depends(require_user),
    ):
        state = _load_state()
        session = _owned_session(state, session_id, owner)
        turn = _append_turn(session, payload.role, payload.text, payload.status or "recorded", payload.task_id)
        _save_state(state)
        return turn

    @router.post("/sessions/{session_id}/respond")
    async def respond_to_voice_turn(
        session_id: str,
        payload: VoiceRespondRequest,
        request: Request,
        owner: str = Depends(require_user),
    ):
        _set_user_time_from_request(request)
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail={"message": "Voice turn text is required"})

        state = _load_state()
        session = _owned_session(state, session_id, owner)
        _require_server_tts(tts_service)
        turn_session = dict(session)
        turn_session["_stt_service"] = stt_service
        turn_session["_tts_service"] = tts_service
        if payload.client_state:
            turn_session["_client_state"] = payload.client_state.model_dump(exclude_none=True)
        if payload.frame:
            turn_session["_frame"] = _decode_voice_frame(payload.frame)
        user_turn = _append_turn(session, "user", text, "thinking")
        _append_chat_message(session_manager, session, "user", text, voice_turn_id=user_turn["id"], voice_status="thinking")
        _save_state(state)

        task = None
        diagnostics: dict[str, Any]
        linked_chat = _voice_chat_session(str(session.get("chat_session_id") or ""))
        linked_model = str(getattr(linked_chat, "model", "") or "odysseus-router")
        action = _detect_safe_action(text)
        if action:
            task = await _execute_action(
                VoiceActionRequest(action=action, session_id=session_id, prompt=text),
                owner,
            )
            reply = "Running that in the background, sir."
            diagnostics = {
                "model": linked_model,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "guard_reason": "safe_action",
                "action": action,
            }
        else:
            try:
                reply, diagnostics, agent_task_ids = await _jarvis_reply(
                    str(session.get("chat_session_id") or ""),
                    text,
                    owner,
                    turn_session,
                )
            except Exception as exc:
                session["status"] = "failed"
                session["updated_at"] = _now()
                _append_diagnostic(session, {
                    "model": linked_model,
                    "transcript_chars": len(text),
                    "assistant_chars": 0,
                    "guard_reason": "brain_failure",
                    "error": str(exc)[:240],
                })
                _save_state(state)
                raise HTTPException(status_code=502, detail={"message": "Jarvis brain request failed", "error": str(exc)})

        state = _load_state()
        session = _session(state, session_id)
        if not action:
            from src.jarvis_agent import get_task

            for task_id in agent_task_ids:
                agent_task = get_task(task_id)
                if agent_task and not any(row.get("task_id") == task_id for row in session.setdefault("tasks", [])):
                    session["tasks"].append(agent_task)
        if task:
            state.setdefault("actions", {})[task["task_id"]] = task
            session.setdefault("tasks", []).append(task)
        linked_task_id = task["task_id"] if task else (agent_task_ids[0] if not action and agent_task_ids else None)
        assistant_turn = _append_turn(session, "assistant", reply, "speaking", linked_task_id)
        _append_chat_message(
            session_manager,
            session,
            "assistant",
            reply,
            voice_turn_id=assistant_turn["id"],
            voice_status="speaking",
            task_id=linked_task_id,
            diagnostics=diagnostics,
            **_assistant_identity_metadata(diagnostics),
        )
        _append_diagnostic(session, diagnostics)
        session["status"] = "speaking"
        _save_state(state)
        return {
            "session_id": session_id,
            "status": "speaking",
            "assistant_text": reply,
            "assistant_turn": assistant_turn,
            "diagnostics": diagnostics,
            "task": task,
            "agent_task_ids": [] if action else agent_task_ids,
        }

    @router.post("/sessions/{session_id}/respond/stream")
    async def stream_voice_response(
        session_id: str,
        payload: VoiceRespondRequest,
        request: Request,
        owner: str = Depends(require_user),
    ):
        _set_user_time_from_request(request)
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail={"message": "Voice turn text is required"})
        state = _load_state()
        session = _owned_session(state, session_id, owner)
        _require_server_tts(tts_service)
        turn_session = dict(session)
        turn_session["_stt_service"] = stt_service
        turn_session["_tts_service"] = tts_service
        if payload.client_state:
            turn_session["_client_state"] = payload.client_state.model_dump(exclude_none=True)
        if payload.frame:
            turn_session["_frame"] = _decode_voice_frame(payload.frame)
        user_turn = _append_turn(session, "user", text, "thinking")
        _append_chat_message(session_manager, session, "user", text, voice_turn_id=user_turn["id"], voice_status="thinking")
        speech_turn = _register_speech_turn(session_id)
        speech_turn.voice = _tts_voice_for_final({
            "diagnostics": {"character_name": _voice_character_name(turn_session)},
        })
        session["active_audio_turn_id"] = speech_turn.turn_id
        _save_state(state)
        chat_session_id = str(session.get("chat_session_id") or "")

        async def generate():
            try:
                final: dict[str, Any] | None = None
                handoff_greetings: list[dict[str, Any]] = []
                audio_announced = False
                stream_speech = _requested_artifact_kind(text) is None
                yield f"data: {json.dumps({'type': 'state', 'state': 'thinking'})}\n\n"
                async for event in _voice_events_with_heartbeats(
                    _jarvis_events(chat_session_id, text, owner, turn_session)
                ):
                    if event is None:
                        yield ": heartbeat\n\n"
                        continue
                    if event.get("type") in {"target_changed", "agent_task"}:
                        event_state = _load_state()
                        event_session = _session(event_state, session_id)
                        if event.get("type") == "target_changed":
                            event_session["target"] = event.get("target", "jarvis")
                            event_session["workspace"] = event.get("workspace", "home-lab")
                        else:
                            event_session["active_task_id"] = event.get("task_id")
                        event_session["updated_at"] = _now()
                        _save_state(event_state)
                    if event.get("type") == "handoff_greeting":
                        greeting_task = asyncio.create_task(_handoff_greeting(
                            str(event.get("target") or "jarvis"),
                            chat_session_id,
                            owner,
                            str(event.get("workspace") or "home-lab"),
                        ))
                        while True:
                            ready, _ = await asyncio.wait(
                                (greeting_task,),
                                timeout=VOICE_EVENT_HEARTBEAT_SECONDS,
                            )
                            if ready:
                                break
                            yield ": heartbeat\n\n"
                        greeting = greeting_task.result()
                        handoff_greetings.append(greeting)
                        handoff_event = {
                            'type': 'assistant_handoff',
                            'text': greeting['text'],
                            'target': greeting['target'],
                            'model': greeting['model'],
                        }
                        yield f"data: {json.dumps(handoff_event)}\n\n"
                        continue
                    if event.get("type") == "final":
                        final = _decorate_voice_final(event, turn_session)
                        event = final
                    if event.get("type") == "assistant_delta" and stream_speech:
                        if speech_turn.feed(str(event.get("text") or "")) and not audio_announced:
                            audio_announced = True
                            yield f"data: {json.dumps({'type': 'audio_ready', 'turn_id': speech_turn.turn_id})}\n\n"
                    yield f"data: {json.dumps(event)}\n\n"
                if not final:
                    raise RuntimeError("Jarvis voice model returned no final event")
                speech_turn.voice = _tts_voice_for_final(final)
                if stream_speech:
                    final_text = str(final["assistant_text"])
                    if not speech_turn.raw_text:
                        speech_turn.feed(final_text)
                    elif final_text.startswith(speech_turn.raw_text):
                        speech_turn.feed(final_text[len(speech_turn.raw_text):])
                    await speech_turn.complete()
                else:
                    await speech_turn.complete(await _spoken_text_for_final(text, final))
                spoken_text = speech_turn.text
                final["diagnostics"]["spoken_chars"] = len(spoken_text)
                if not audio_announced:
                    yield f"data: {json.dumps({'type': 'audio_ready', 'turn_id': speech_turn.turn_id})}\n\n"
                current_state = _load_state()
                current = _session(current_state, session_id)
                task_ids = final.get("task_ids") or []
                from src.jarvis_agent import get_task

                for task_id in task_ids:
                    agent_task = get_task(task_id)
                    if agent_task and not any(row.get("task_id") == task_id for row in current.setdefault("tasks", [])):
                        current["tasks"].append(agent_task)
                assistant_turn = _append_turn(
                    current,
                    "assistant",
                    final["assistant_text"],
                    "speaking",
                    task_ids[0] if task_ids else None,
                )
                _append_chat_message(
                    session_manager,
                    current,
                    "assistant",
                    final["assistant_text"],
                    voice_turn_id=assistant_turn["id"],
                    voice_status="speaking",
                    task_id=task_ids[0] if task_ids else None,
                    diagnostics=final["diagnostics"],
                    **_assistant_identity_metadata(final["diagnostics"]),
                )
                _append_diagnostic(current, final["diagnostics"])
                _save_state(current_state)
                for greeting in handoff_greetings:
                    greeting_text = str(greeting["text"])
                    greeting_diagnostics = dict(greeting["diagnostics"])
                    greeting_diagnostics["assistant_chars"] = len(greeting_text)
                    greeting_diagnostics["spoken_chars"] = len(greeting_text)
                    greeting_turn = _register_speech_turn(session_id)
                    greeting_turn.voice = _tts_voice_for_final({"diagnostics": greeting_diagnostics})
                    await greeting_turn.complete(greeting_text)
                    greeting_audio_event = {
                        'type': 'audio_ready',
                        'turn_id': greeting_turn.turn_id,
                        'target': greeting['target'],
                    }
                    yield f"data: {json.dumps(greeting_audio_event)}\n\n"
                    greeting_state = _load_state()
                    greeting_session = _session(greeting_state, session_id)
                    saved_greeting_turn = _append_turn(
                        greeting_session,
                        "assistant",
                        greeting_text,
                        "speaking",
                    )
                    _append_chat_message(
                        session_manager,
                        greeting_session,
                        "assistant",
                        greeting_text,
                        voice_turn_id=saved_greeting_turn["id"],
                        voice_status="speaking",
                        diagnostics=greeting_diagnostics,
                        **_assistant_identity_metadata(greeting_diagnostics),
                    )
                    _append_diagnostic(greeting_session, greeting_diagnostics)
                    _save_state(greeting_state)
            except Exception as exc:
                await speech_turn.fail(str(exc)[:240])
                current_state = _load_state()
                current = _session(current_state, session_id)
                current["status"] = "failed"
                _append_diagnostic(current, {
                    "model": str(getattr(_voice_chat_session(chat_session_id), "model", "") or "odysseus-router"),
                    "transcript_chars": len(text),
                    "assistant_chars": 0,
                    "guard_reason": "brain_failure",
                    "error": str(exc)[:240],
                })
                _save_state(current_state)
                yield f"data: {json.dumps({'type': 'error', 'text': str(exc)[:240]})}\n\n"
            finally:
                if not speech_turn.finished:
                    await speech_turn.fail("Jarvis voice response ended before speech was ready")
                yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/sessions/{session_id}/turns/{turn_id}/audio")
    async def stream_voice_turn_audio(
        session_id: str,
        turn_id: str,
        owner: str = Depends(require_user),
    ):
        _owned_session(_load_state(), session_id, owner)
        speech_turn = _SPEECH_TURNS.get((session_id, turn_id))
        if not speech_turn:
            raise HTTPException(status_code=404, detail={"message": "Voice audio turn not found"})
        _require_server_tts(tts_service)

        _set_voice_status(session_id, "buffering", active_audio_turn_id=turn_id)

        async def generate_audio():
            started = time.perf_counter()
            generation_ms = 0
            audio_ms = 0
            block_count = 0
            spoken_text = ""
            try:
                sample_rate: int | None = None
                async for block in speech_turn.iter_blocks():
                    async with TTS_INFERENCE_LOCK:
                        if speech_turn.cancelled:
                            raise RuntimeError("Voice playback was interrupted")
                        block_started = time.perf_counter()
                        audio = await asyncio.to_thread(
                            tts_service.synthesize,
                            block,
                            False,
                            voice=speech_turn.voice,
                        )
                        block_generation_ms = int((time.perf_counter() - block_started) * 1000)
                        if speech_turn.cancelled:
                            raise RuntimeError("Voice playback was interrupted")
                        if not audio:
                            raise RuntimeError("TTS synthesis failed")
                        block_rate, pcm = wav_to_pcm16(audio)
                        if sample_rate is None:
                            sample_rate = block_rate
                            yield json.dumps({"type": "start", "sample_rate": sample_rate}) + "\n"
                        elif block_rate != sample_rate:
                            raise RuntimeError("TTS sample rate changed during a voice turn")

                        block_audio_ms = int(len(pcm) / (sample_rate * 2) * 1000)
                        yield json.dumps({
                            "type": "block",
                            "index": block_count,
                            "text_chars": len(block),
                            "generation_ms": block_generation_ms,
                            "audio_ms": block_audio_ms,
                        }, separators=(",", ":")) + "\n"
                        for frame in pcm_frames(pcm):
                            yield json.dumps({
                                "type": "audio",
                                "pcm_base64": base64.b64encode(frame).decode("ascii"),
                            }, separators=(",", ":")) + "\n"

                        generation_ms += block_generation_ms
                        audio_ms += block_audio_ms
                        block_count += 1
                        if speech_turn.cancelled:
                            raise RuntimeError("Voice playback was interrupted")
                        _set_voice_status(session_id, "speaking", active_audio_turn_id=turn_id)

                spoken_text = speech_turn.text

                yield json.dumps({
                    "type": "done",
                    "blocks": block_count,
                    "generation_ms": generation_ms,
                    "audio_ms": audio_ms,
                }, separators=(",", ":")) + "\n"

                state = _load_state()
                current = _session(state, session_id)
                _append_diagnostic(current, {
                    "label": "tts",
                    "turn_id": turn_id,
                    "spoken_chars": len(spoken_text),
                    "tts_ms": int((time.perf_counter() - started) * 1000),
                    "tts_inferences": block_count,
                })
                _save_state(state)
            except Exception as exc:
                if speech_turn.cancelled:
                    logger.info("Jarvis speech turn %s was interrupted", turn_id)
                    _set_voice_status(session_id, "interrupted", active_audio_turn_id=None)
                else:
                    logger.exception("Jarvis speech turn %s failed", turn_id)
                    _set_voice_status(session_id, "failed", active_audio_turn_id=None)
                yield json.dumps({"type": "error", "error": str(exc)[:240]}) + "\n"
            finally:
                _SPEECH_TURNS.pop((session_id, turn_id), None)

        return StreamingResponse(
            generate_audio(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/sessions/{session_id}/turns/{turn_id}/playback")
    async def update_voice_playback(
        session_id: str,
        turn_id: str,
        payload: VoicePlaybackUpdate,
        owner: str = Depends(require_user),
    ):
        _owned_session(_load_state(), session_id, owner)
        status_for_state = {
            "started": "speaking",
            "completed": "ready",
            "interrupted": "interrupted",
            "failed": "failed",
        }
        session = _set_voice_status(
            session_id,
            status_for_state[payload.state],
            active_audio_turn_id=None if payload.state != "started" else turn_id,
        )
        if payload.timings:
            _append_diagnostic(session, {
                "label": "playback",
                "turn_id": turn_id,
                "playback_state": payload.state,
                "client": True,
                **_clean_client_timings(payload.timings),
            })
            state = _load_state()
            state["sessions"][session_id] = session
            _save_state(state)
        return {"session_id": session_id, "turn_id": turn_id, "status": session["status"]}

    @router.post("/sessions/{session_id}/diagnostics")
    async def add_voice_diagnostic(
        session_id: str,
        payload: VoiceDiagnosticCreate,
        owner: str = Depends(require_user),
    ):
        state = _load_state()
        session = _owned_session(state, session_id, owner)
        _append_diagnostic(session, {
            "label": payload.label[:80],
            "client": True,
            **_clean_client_timings(payload.timings),
        })
        session["updated_at"] = _now()
        _save_state(state)
        return {"ok": True}

    @router.post("/sessions/{session_id}/interrupt")
    async def interrupt_voice_session(session_id: str, owner: str = Depends(require_user)):
        _owned_session(_load_state(), session_id, owner)
        for (voice_session_id, _turn_id), speech_turn in list(_SPEECH_TURNS.items()):
            if voice_session_id == session_id:
                await speech_turn.cancel()
        state = _load_state()
        session = _session(state, session_id)
        session["status"] = "interrupted"
        session["updated_at"] = _now()
        session.setdefault("turns", []).append(
            {
                "id": str(uuid.uuid4()),
                "role": "system",
                "text": "Playback interrupted; next speech becomes the next turn.",
                "status": "interrupted",
                "created_at": _now(),
            }
        )
        _save_state(state)
        return {"session_id": session_id, "status": "interrupted"}

    @router.post("/actions")
    async def run_voice_action(payload: VoiceActionRequest, owner: str = Depends(require_user)):
        state = _load_state()
        if payload.session_id:
            _owned_session(state, payload.session_id, owner)
        task = await _execute_action(payload, owner)
        state = _load_state()
        state.setdefault("actions", {})[task["task_id"]] = task
        if payload.session_id and payload.session_id in state.get("sessions", {}):
            session = _owned_session(state, payload.session_id, owner)
            session.setdefault("tasks", []).append(task)
            session["updated_at"] = _now()
        _save_state(state)
        return task

    @router.get("/actions/{task_id}")
    async def get_voice_action(task_id: str, owner: str = Depends(require_user)):
        action = _load_state().get("actions", {}).get(task_id)
        if not action:
            raise HTTPException(status_code=404, detail={"message": "Voice action not found"})
        if action.get("owner") != owner:
            raise HTTPException(status_code=403, detail={"message": "Voice action does not belong to this user"})
        return action

    return router
