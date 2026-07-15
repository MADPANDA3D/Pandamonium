"""Authenticated live voice conversation routes for the Odysseus Voice Orb."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.atomic_io import atomic_write_json
from core.models import ChatMessage
from src.auth_helpers import require_user
from src.constants import DATA_DIR
from src.endpoint_resolver import resolve_endpoint, resolve_endpoint_by_id
from src.llm_core import stream_llm

logger = logging.getLogger(__name__)

VOICE_STATE_FILE = Path(DATA_DIR) / "voice_sessions.json"
VOICE_PERSONA = re.sub(
    r"[^A-Za-z0-9 ._'\-]",
    "",
    os.getenv("ODYSSEUS_VOICE_PERSONA", "Odysseus").strip(),
)[:50] or "Odysseus"
VOICE_ENDPOINT_ID = os.getenv("ODYSSEUS_VOICE_ENDPOINT_ID", "").strip()
VOICE_MODEL = os.getenv("ODYSSEUS_VOICE_MODEL", "").strip()
_STATE_LOCK = threading.RLock()
_ACTIVE_RESPONSES: dict[str, asyncio.Task] = {}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoiceSessionCreate(_StrictModel):
    chat_session_id: str | None = Field(default=None, max_length=128)


class VoiceCalendarClientState(_StrictModel):
    open: bool = False
    minimized: bool = False
    view: str | None = Field(default=None, max_length=32)
    date: str | None = Field(default=None, max_length=32)


class VoiceDocumentClientState(_StrictModel):
    open: bool = False
    minimized: bool = False
    id: str | None = Field(default=None, max_length=128)


class VoiceClientState(_StrictModel):
    active_view: Literal["chat", "calendar", "document"] | None = None
    calendar: VoiceCalendarClientState = Field(default_factory=VoiceCalendarClientState)
    document: VoiceDocumentClientState = Field(default_factory=VoiceDocumentClientState)


class VoiceRespondRequest(_StrictModel):
    text: str = Field(min_length=1, max_length=12_000)
    client_state: VoiceClientState | None = None


def _now() -> int:
    return int(time.time())


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(VOICE_STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"sessions": {}}
    except FileNotFoundError:
        return {"sessions": {}}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read voice session state: %s", type(exc).__name__)
        return {"sessions": {}}


def _save_state(state: dict[str, Any]) -> None:
    atomic_write_json(str(VOICE_STATE_FILE), state)


def _update_voice_session(session_id: str, **fields: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _load_state()
        session = (state.setdefault("sessions", {})).get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(404, "Voice session not found")
        session.update(fields)
        session["updated_at"] = _now()
        _save_state(state)
        return dict(session)


def _owned_voice_session(session_id: str, owner: str) -> dict[str, Any]:
    with _STATE_LOCK:
        session = (_load_state().get("sessions") or {}).get(session_id)
        if not isinstance(session, dict):
            raise HTTPException(404, "Voice session not found")
        if str(session.get("owner") or "") != str(owner or ""):
            raise HTTPException(403, "Voice session belongs to another user")
        return dict(session)


def _require_same_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(403, "Cross-site voice requests are not allowed")


def _require_chat_session(session_manager, chat_session_id: str, owner: str):
    try:
        session = session_manager.get_session(chat_session_id)
    except KeyError as exc:
        raise HTTPException(404, "Linked chat session not found") from exc
    if owner and str(getattr(session, "owner", "") or "") != owner:
        raise HTTPException(403, "Linked chat session belongs to another user")
    return session


def _resolve_voice_runtime(owner: str, linked_session=None) -> tuple[str, str, dict[str, str]]:
    """Resolve an owner-scoped voice model without inventing a second provider store."""
    if VOICE_ENDPOINT_ID:
        resolved = resolve_endpoint_by_id(VOICE_ENDPOINT_ID, VOICE_MODEL or None, owner=owner or None)
        if not resolved:
            raise HTTPException(503, "Configured voice model endpoint is unavailable")
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
        raise HTTPException(503, "No default chat model is configured")
    return url, model, headers or {}


def _foreground_command(text: str) -> tuple[str, str | None] | None:
    normalized = re.sub(r"[.!?,]+$", "", text.strip().lower()).strip()
    return {
        "open calendar": ("open_view", "calendar"),
        "close this document": ("close_view", "document"),
        "minimize this document": ("minimize_view", "document"),
        "what view is open": ("report_view_state", None),
    }.get(normalized)


def _describe_client_view(client_state: VoiceClientState | None) -> str:
    if client_state is None:
        return "I cannot confirm the current view because the browser did not report its state."
    if client_state.active_view == "calendar" and client_state.calendar.open:
        detail = client_state.calendar.view or "current"
        date = f" for {client_state.calendar.date}" if client_state.calendar.date else ""
        return f"Calendar is open in the {detail} view{date}."
    if client_state.active_view == "document" and client_state.document.open:
        return "A document is open in the foreground."
    minimized: list[str] = []
    if client_state.calendar.minimized:
        minimized.append("Calendar")
    if client_state.document.minimized:
        minimized.append("a document")
    if minimized:
        return f"The chat is in the foreground; {' and '.join(minimized)} is minimized."
    return "The chat is in the foreground."


def _plain_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part).strip()
    return ""


def _voice_messages(chat_session) -> list[dict[str, str]]:
    system = (
        f"You are {VOICE_PERSONA}, the conversational voice interface for Odysseus. "
        "Reply naturally for spoken playback. Use one to four short paragraphs unless the user asks "
        "for more depth. Do not include stage directions, hidden reasoning, or capability menus. "
        "Do not claim to see or control the browser beyond an explicit server-confirmed action."
    )
    selected: list[dict[str, str]] = []
    total = 0
    history = list(getattr(chat_session, "history", None) or getattr(chat_session, "_history", None) or [])
    for message in reversed(history[-60:]):
        role = getattr(message, "role", None)
        content = _plain_message_content(getattr(message, "content", None)).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        content = content[:12_000]
        if total + len(content) > 60_000:
            break
        selected.append({"role": role, "content": content})
        total += len(content)
    selected.reverse()
    return [{"role": "system", "content": system}, *selected]


def _strip_hidden_reasoning(text: str) -> str:
    return re.sub(r"<think(?:ing)?[^>]*>[\s\S]*?</think(?:ing)?>", "", text or "", flags=re.I).strip()


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _stream_piece(chunk: str) -> tuple[list[str], bool]:
    deltas: list[str] = []
    failed = chunk.lstrip().startswith("event: error")
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("error") or event.get("type") == "error":
            failed = True
        delta = event.get("delta")
        if isinstance(delta, str) and delta and not event.get("thinking"):
            deltas.append(delta)
    return deltas, failed


def _append_voice_turn(session_id: str, role: str, text: str, status: str) -> None:
    with _STATE_LOCK:
        state = _load_state()
        session = (state.get("sessions") or {}).get(session_id)
        if not isinstance(session, dict):
            return
        turns = session.setdefault("turns", [])
        turns.append({"role": role, "text": text[:12_000], "status": status, "created_at": _now()})
        session["turns"] = turns[-100:]
        session["status"] = status
        session["updated_at"] = _now()
        _save_state(state)


def setup_voice_routes(session_manager, stt_service=None, tts_service=None) -> APIRouter:
    router = APIRouter(prefix="/api/voice", tags=["voice"])

    @router.get("/status")
    async def voice_status(_owner: str = Depends(require_user)):
        stt = stt_service.get_stats() if stt_service is not None else {"available": False, "provider": "disabled"}
        tts = tts_service.get_stats() if tts_service is not None else {"available": False, "provider": "disabled"}
        return {
            "assistant": VOICE_PERSONA,
            "model_override": VOICE_MODEL or None,
            "endpoint_override_configured": bool(VOICE_ENDPOINT_ID),
            "stt": {"available": bool(stt.get("available")), "provider": stt.get("provider", "disabled")},
            "tts": {
                "available": bool(tts.get("available")),
                "provider": tts.get("provider", "disabled"),
                "voice": tts.get("voice", ""),
                "speed": tts.get("speed", 1),
            },
        }

    @router.post("/sessions")
    async def create_voice_session(
        request: Request,
        payload: VoiceSessionCreate,
        owner: str = Depends(require_user),
    ):
        _require_same_origin(request)
        linked = None
        if payload.chat_session_id:
            linked = _require_chat_session(session_manager, payload.chat_session_id, owner)
        url, model, headers = _resolve_voice_runtime(owner, linked)
        chat_session_id = payload.chat_session_id
        if linked is None:
            chat_session_id = str(uuid.uuid4())
            linked = session_manager.create_session(
                session_id=chat_session_id,
                name=f"{VOICE_PERSONA} Voice",
                endpoint_url=url,
                model=model,
                owner=owner or None,
            )
            linked.headers = dict(headers)
            if headers:
                from routes.session_routes import _persist_session_headers

                _persist_session_headers(chat_session_id, headers)

        session_id = str(uuid.uuid4())
        session = {
            "id": session_id,
            "owner": owner,
            "chat_session_id": chat_session_id,
            "assistant": VOICE_PERSONA,
            "model": model,
            "status": "ready",
            "turns": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with _STATE_LOCK:
            state = _load_state()
            state.setdefault("sessions", {})[session_id] = session
            _save_state(state)
        return {key: session[key] for key in ("id", "chat_session_id", "assistant", "model", "status")}

    @router.get("/sessions/{session_id}")
    async def get_voice_session(session_id: str, owner: str = Depends(require_user)):
        session = _owned_voice_session(session_id, owner)
        return {
            key: session.get(key)
            for key in ("id", "chat_session_id", "assistant", "model", "status", "turns", "created_at", "updated_at")
        }

    @router.post("/sessions/{session_id}/respond")
    async def respond_to_voice_turn(
        session_id: str,
        request: Request,
        payload: VoiceRespondRequest,
        owner: str = Depends(require_user),
    ):
        _require_same_origin(request)
        voice_session = _owned_voice_session(session_id, owner)
        chat = _require_chat_session(session_manager, str(voice_session["chat_session_id"]), owner)
        text = payload.text.strip()
        if not text:
            raise HTTPException(422, "Voice text cannot be empty")
        session_manager.add_message(chat.id, ChatMessage("user", text, metadata={"source": "voice_orb"}))
        _append_voice_turn(session_id, "user", text, "thinking")
        command = _foreground_command(text)

        async def generate():
            current_task = asyncio.current_task()
            if current_task is not None:
                previous = _ACTIVE_RESPONSES.get(session_id)
                if previous is not None and previous is not current_task and not previous.done():
                    previous.cancel()
                _ACTIVE_RESPONSES[session_id] = current_task
            try:
                if command:
                    action, view = command
                    if action == "report_view_state":
                        reply = _describe_client_view(payload.client_state)
                    else:
                        yield _sse({"type": "ui_control", "ui_event": action, "view": view})
                        reply = {
                            ("open_view", "calendar"): "Calendar is open.",
                            ("close_view", "document"): "The document is closed.",
                            ("minimize_view", "document"): "The document is minimized.",
                        }[(action, view)]
                    session_manager.add_message(chat.id, ChatMessage("assistant", reply, metadata={"source": "voice_orb"}))
                    _append_voice_turn(session_id, "assistant", reply, "ready")
                    yield _sse({"type": "final", "text": reply, "model": voice_session.get("model"), "assistant": VOICE_PERSONA})
                    return

                url, model, headers = _resolve_voice_runtime(owner, chat)
                _update_voice_session(session_id, status="thinking", model=model)
                accumulated = ""
                failed = False
                async for chunk in stream_llm(
                    url,
                    model,
                    _voice_messages(chat),
                    headers=headers,
                    max_tokens=900,
                    session_id=chat.id,
                    tool_choice_none=True,
                    workload="foreground",
                ):
                    deltas, piece_failed = _stream_piece(chunk)
                    failed = failed or piece_failed
                    for delta in deltas:
                        accumulated += delta
                        yield _sse({"type": "delta", "text": delta, "model": model})
                reply = _strip_hidden_reasoning(accumulated)[:12_000]
                if failed or not reply:
                    raise RuntimeError("voice_model_failed")
                session_manager.add_message(chat.id, ChatMessage("assistant", reply, metadata={"source": "voice_orb"}))
                _append_voice_turn(session_id, "assistant", reply, "ready")
                yield _sse({"type": "final", "text": reply, "model": model, "assistant": VOICE_PERSONA})
            except asyncio.CancelledError:
                _update_voice_session(session_id, status="interrupted")
                raise
            except Exception as exc:
                logger.warning("Voice response failed: %s", type(exc).__name__)
                _update_voice_session(session_id, status="failed")
                yield _sse({"type": "error", "text": "The voice model could not complete that response."})
            finally:
                if _ACTIVE_RESPONSES.get(session_id) is current_task:
                    _ACTIVE_RESPONSES.pop(session_id, None)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/sessions/{session_id}/interrupt")
    async def interrupt_voice_session(
        session_id: str,
        request: Request,
        owner: str = Depends(require_user),
    ):
        _require_same_origin(request)
        _owned_voice_session(session_id, owner)
        task = _ACTIVE_RESPONSES.get(session_id)
        if task is not None and not task.done():
            task.cancel()
        _update_voice_session(session_id, status="interrupted")
        return {"ok": True, "status": "interrupted"}

    return router
