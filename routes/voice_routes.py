"""Jarvis live voice session and safe action bridge routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.constants import DATA_DIR
from core.models import ChatMessage
from src.agent_loop import stream_agent_loop
from src.agent_tools import TOOL_TAGS
from src.auth_helpers import effective_user
from src.voice_pcm import TTS_INFERENCE_LOCK, stream_tts_pcm_segment, take_speech_segment

VOICE_STATE_FILE = Path(DATA_DIR) / "voice_sessions.json"
ACTION_BRIDGE_URL = "http://192.168.1.50:8010/actions"
JARVIS_GENERATE_URL = "http://192.168.1.247:11434/api/generate"
JARVIS_CHAT_URL = "http://192.168.1.247:11434/v1/chat/completions"
JARVIS_MODEL = os.getenv("ODYSSEUS_VOICE_MODEL", "qwen3.5-jarvis-v5:latest")
VOICE_NORMAL_NUM_PREDICT = int(os.getenv("ODYSSEUS_VOICE_NUM_PREDICT", "600"))
VOICE_LONG_NUM_PREDICT = int(os.getenv("ODYSSEUS_VOICE_LONG_NUM_PREDICT", "1200"))
VOICE_CONTEXT_LENGTH = int(os.getenv("ODYSSEUS_VOICE_CONTEXT_LENGTH", "32768"))
VOICE_OLLAMA_KEEP_ALIVE = os.getenv("ODYSSEUS_VOICE_OLLAMA_KEEP_ALIVE", "30m")
logger = logging.getLogger(__name__)
_SESSION_MANAGER = None
_SPEECH_TURNS: dict[tuple[str, str], "_SpeechTurn"] = {}

DESKTOP_ACTIONS = {"open_grafana_big_screen", "open_odysseus"}
DEFERRED_ACTIONS = {"start_local_codex_task", "start_hermes_task", "read_task_status"}
SAFE_ACTIONS = DESKTOP_ACTIONS | DEFERRED_ACTIONS
JARVIS_TOOLS = {"get_runtime_status", "start_agent_task", "read_agent_task", "search_jarvis_knowledge"}
VOICE_SYSTEM_PROMPT = """You are Jarvis, Leo's private orchestrator and conversational partner.
The active system build is Mark 5 - Jarvis Agent Mesh And Knowledge. Unless Leo explicitly mentions scripture, Bible study, or another domain, references to Mark 1, Mark 2, Mark 3, Mark 4, or Mark 5 mean indexed Jarvis architecture builds.
Answer naturally with enough substance for the question: usually one to four short spoken paragraphs, and more when Leo explicitly asks for a deep explanation. Never describe pacing, pauses, or speaking style.
You coordinate work; you do not pretend to have inspected systems you have not inspected. Use get_runtime_status for runtime or model questions. Use search_jarvis_knowledge for curated background. For latest, current, or business-update requests, use background knowledge and start a read-only pc-codex task to inspect current sources.
Model-initiated delegation is always read-only. Tell Leo briefly that work is running in the background, then let worker events deliver progress and the final result. Never invent worker results, runtime facts, paths, or endpoint details."""

WORKER_LABELS = {
    "pc-codex": "PC Codex",
    "hermes": "Hermes",
    "vps-codex": "VPS Codex",
}
ACTIVE_VOICE_TARGETS = {"jarvis", "pc-codex"}
VOICE_WORKSPACES = {"business", "home-lab", "project-linux"}


class VoiceSessionCreate(BaseModel):
    mode: str = "jarvis_call"
    chat_session_id: str | None = None


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


class VoiceRespondRequest(BaseModel):
    text: str


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
    """A growing assistant response consumed by one server-owned TTS stream."""

    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.turn_id = turn_id
        self.buffer = ""
        self.finished = False
        self.cancelled = False
        self.error: str | None = None
        self.first = True
        self.target_chars = 280
        self.created_at = time.monotonic()
        self.condition = asyncio.Condition()

    async def append(self, text: str) -> None:
        if not text:
            return
        async with self.condition:
            self.buffer += text
            self.condition.notify_all()

    async def finish(self, error: str | None = None) -> None:
        async with self.condition:
            self.finished = True
            self.error = error
            self.condition.notify_all()

    async def cancel(self) -> None:
        async with self.condition:
            self.cancelled = True
            self.finished = True
            self.condition.notify_all()

    async def next_segment(self) -> str | None:
        async with self.condition:
            while True:
                if self.cancelled:
                    return None
                if self.error:
                    raise RuntimeError(self.error)
                segment, remainder = take_speech_segment(
                    self.buffer,
                    first=self.first,
                    target_chars=self.target_chars,
                    done=self.finished,
                )
                if segment:
                    self.buffer = remainder
                    self.first = False
                    return segment
                if self.finished:
                    return None
                await self.condition.wait()

    def record_block(self, generation_ms: int, audio_ms: int) -> None:
        if audio_ms <= 0:
            return
        ratio = generation_ms / audio_ms
        if ratio > 0.70:
            self.target_chars = min(360, self.target_chars + 40)
        elif ratio < 0.45:
            self.target_chars = max(220, self.target_chars - 20)


def _now() -> int:
    return int(time.time())


def _load_state() -> dict:
    try:
        state = json.loads(VOICE_STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {"sessions": {}, "actions": {}}
    except Exception:
        return {"sessions": {}, "actions": {}}


def _save_state(state: dict) -> None:
    VOICE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VOICE_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(VOICE_STATE_FILE)


def _set_voice_status(session_id: str, status: str, **fields: Any) -> dict:
    state = _load_state()
    session = _session(state, session_id)
    session["status"] = status
    session["updated_at"] = _now()
    session.update(fields)
    _save_state(state)
    return session


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


async def _execute_action(payload: VoiceActionRequest) -> dict:
    action = payload.action.strip()
    if action not in SAFE_ACTIONS:
        raise HTTPException(status_code=403, detail={"message": "action_not_allowed", "action": action})

    task = {
        "task_id": str(uuid.uuid4()),
        "action": action,
        "session_id": payload.session_id,
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
                    return await refresh_task(requested_task_id)
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
                    "leo",
                )
            except Exception as exc:
                task.update({"status": "failed", "reason": str(exc)[:240]})

    return task


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.IGNORECASE).strip()


def _num_predict_for_text(text: str) -> int:
    if re.search(r"\b(detail|detailed|explain|deep dive|walk me through|long answer)\b", text, flags=re.IGNORECASE):
        return VOICE_LONG_NUM_PREDICT
    return VOICE_NORMAL_NUM_PREDICT


def _asks_runtime_status(text: str) -> bool:
    return bool(re.search(r"\b(what|which|identify|runtime|model|architecture|quantization)\b.*\b(model|running|runtime|architecture|quantization)\b", text, re.IGNORECASE))


def _asks_current_business(text: str) -> bool:
    return bool(re.search(r"\b(current|latest|today|recent|updates?|status)\b.*\b(business|clients?|mad panda)\b|\b(business|clients?|mad panda)\b.*\b(current|latest|today|recent|updates?|status)\b", text, re.IGNORECASE))


def _workspace_for_text(text: str) -> str:
    if re.search(r"\b(business|clients?|marketing|mad\s*panda|campaign|website|crm)\b", text, re.IGNORECASE):
        return "business"
    if re.search(r"\b(project\s+linux|linux\s+(?:desktop|workstation)|hyprland)\b", text, re.IGNORECASE):
        return "project-linux"
    return "home-lab"


def _delegation_route(text: str) -> tuple[str, str] | None:
    """Map Leo's stable names to fixed workers and server-controlled workspaces."""
    if re.search(r"\b(vps|online server|public server|hosting server|mad\s*panda hosting)\b", text, re.IGNORECASE):
        return "vps-codex", _workspace_for_text(text)
    if re.search(r"\bhermes\b", text, re.IGNORECASE):
        return "hermes", _workspace_for_text(text)
    if re.search(r"\b(pc codex|my codex|desktop codex|computer codex)\b|\b(?:ask|talk to|speak to|check with)\s+my computer\b", text, re.IGNORECASE):
        return "pc-codex", _workspace_for_text(text)
    if re.search(r"\b(project\s+nimbus|nimbus|home cloud|my cloud|the cloud)\b", text, re.IGNORECASE):
        return "pc-codex", "home-lab"
    return None


def _target_switch(text: str) -> str | None:
    if re.search(r"\b(back|return|switch|talk|speak)\b.*\bjarvis\b", text, re.IGNORECASE):
        return "jarvis"
    delegation = _delegation_route(text)
    return delegation[0] if delegation else None


def _pure_target_switch(text: str) -> bool:
    return bool(re.fullmatch(
        r"\s*(?:hey\s+jarvis[,\s]*)?(?:i\s+(?:need|want|would\s+like)\s+to\s+)?"
        r"(?:talk|speak|switch|connect|return|go\s+back)(?:\s+me)?(?:\s+back)?\s+(?:to\s+)?"
        r"(?:my\s+)?(?:pc\s+codex|desktop\s+codex|computer\s+codex|codex|jarvis)"
        r"(?:\s+please)?[.!?]*\s*",
        text,
        re.IGNORECASE,
    ))


def _validated_thread_id(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": "Invalid Codex thread ID"}) from exc


async def _server_routed_events(chat_session_id: str, text: str, owner: str, voice_session: dict):
    target_switch = _target_switch(text)
    if target_switch and _pure_target_switch(text):
        workspace = _workspace_for_text(text)
        label = WORKER_LABELS.get(target_switch, "Jarvis")
        reply = "You’re back with Jarvis." if target_switch == "jarvis" else f"You’re connected to {label}. What would you like me to handle?"
        yield {"type": "target_changed", "target": target_switch, "workspace": workspace}
        yield {"type": "assistant_delta", "text": reply}
        yield {
            "type": "final",
            "assistant_text": reply,
            "diagnostics": {
                "model": JARVIS_MODEL,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "brain_ms": 0,
                "brain_first_token_ms": 0,
                "num_ctx": VOICE_CONTEXT_LENGTH,
                "num_predict": 0,
                "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
                "guard_reason": f"target_switch_{target_switch}",
                "task_ids": [],
            },
            "task_ids": [],
        }
        return
    if _asks_runtime_status(text):
        from src.jarvis_agent import runtime_status

        runtime = await runtime_status()
        reply = (
            f"I am Jarvis, running on {runtime.get('brain_model')}. "
            f"The server reports a {runtime.get('architecture')} architecture with {runtime.get('parameter_size')} parameters, "
            f"{runtime.get('quantization')} quantization, and a {runtime.get('context')}-token context allocation. "
            f"My voice is {runtime.get('tts_model')} through {runtime.get('tts_provider')}, using {runtime.get('tts_voice')}."
        )
        yield {"type": "assistant_delta", "text": reply}
        yield {
            "type": "final",
            "assistant_text": reply,
            "diagnostics": {
                "model": JARVIS_MODEL,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "brain_ms": 0,
                "num_ctx": runtime.get("context"),
                "num_predict": 0,
                "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
                "guard_reason": "server_runtime_status",
                "runtime": runtime,
                "task_ids": [],
            },
            "task_ids": [],
        }
        return
    delegation = _delegation_route(text)
    if delegation:
        from src.jarvis_agent import start_task

        worker, workspace = delegation
        yield {"type": "target_changed", "target": worker, "workspace": workspace}
        task = await start_task(
            worker,
            chat_session_id,
            workspace,
            f"Leo asked through Jarvis voice. Handle this read-only request and report factual progress and the final result:\n\n{text}",
            "read_only",
            False,
            owner,
            codex_thread_id=voice_session.get("codex_thread_id") if worker == "pc-codex" else None,
        )
        label = WORKER_LABELS[worker]
        if task.get("status") == "blocked":
            reply = f"{label} is registered, but its private worker connection is not enabled yet. I have not pretended to run the request."
            task_ids: list[str] = []
            guard_reason = f"{worker}_not_connected"
        else:
            reply = f"I’m handing that to {label} in the {workspace} workspace. Its real progress and result will come back through this chat."
            task_ids = [task["task_id"]]
            guard_reason = f"delegated_{worker}"
            yield {"type": "agent_task", "task_id": task["task_id"], "worker": worker, "workspace": workspace}
        yield {"type": "assistant_delta", "text": reply}
        yield {
            "type": "final",
            "assistant_text": reply,
            "diagnostics": {
                "model": JARVIS_MODEL,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "brain_ms": 0,
                "num_ctx": VOICE_CONTEXT_LENGTH,
                "num_predict": 0,
                "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
                "guard_reason": guard_reason,
                "task_ids": task_ids,
            },
            "task_ids": task_ids,
        }
        return
    if _asks_current_business(text):
        from src.jarvis_agent import search_knowledge, start_task

        background = search_knowledge(text, owner=owner, limit=6)
        citations = []
        for row in background.get("results") or []:
            citations.append(f"- {row.get('source')}: {str(row.get('text') or '')[:900]}")
        prompt = (
            "Read-only current business update for Leo. Inspect the live Business workspace and any connected read-only systems available to you. "
            "Use the retrieved background below only as orientation, verify current state from live sources, separate updates by client, and cite dated source paths. "
            "Return a useful executive summary with blockers and next actions.\n\nLeo asked:\n"
            f"{text}\n\nRetrieved background:\n" + "\n".join(citations)
        )
        task = await start_task(
            "pc-codex", chat_session_id, "business", prompt, "read_only", False, owner,
            codex_thread_id=voice_session.get("codex_thread_id"),
        )
        reply = "I’m checking the current business files and live sources now. I’ll speak the useful milestones and the final update as they arrive."
        yield {"type": "target_changed", "target": "pc-codex", "workspace": "business"}
        yield {"type": "agent_task", "task_id": task["task_id"], "worker": "pc-codex", "workspace": "business"}
        yield {"type": "assistant_delta", "text": reply}
        yield {
            "type": "final",
            "assistant_text": reply,
            "diagnostics": {
                "model": JARVIS_MODEL,
                "transcript_chars": len(text),
                "assistant_chars": len(reply),
                "brain_ms": 0,
                "num_ctx": VOICE_CONTEXT_LENGTH,
                "num_predict": 0,
                "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
                "guard_reason": "current_business_delegation",
                "rag_sources": [row.get("source") for row in background.get("results") or []],
                "task_ids": [task["task_id"]],
            },
            "task_ids": [task["task_id"]],
        }


async def _jarvis_events(chat_session_id: str, text: str, owner: str, voice_session: dict):
    if not chat_session_id:
        raise RuntimeError("voice_chat_session_missing")
    chat_session = _SESSION_MANAGER.get_session(chat_session_id) if _SESSION_MANAGER else None
    if not chat_session:
        raise RuntimeError("voice_chat_session_not_found")
    if _asks_runtime_status(text) or _asks_current_business(text) or _delegation_route(text) or _target_switch(text):
        async for event in _server_routed_events(chat_session_id, text, owner, voice_session):
            yield event
        return
    messages = [{"role": "system", "content": VOICE_SYSTEM_PROMPT}, *chat_session.get_context_messages()]
    full_response = ""
    task_ids: list[str] = []
    started = time.perf_counter()
    first_token_ms: int | None = None
    metrics: dict[str, Any] = {}
    async for chunk in stream_agent_loop(
        JARVIS_CHAT_URL,
        JARVIS_MODEL,
        messages,
        temperature=0.35,
        max_tokens=_num_predict_for_text(text),
        max_rounds=8,
        max_tool_calls=6,
        context_length=VOICE_CONTEXT_LENGTH,
        session_id=chat_session_id,
        disabled_tools=set(TOOL_TAGS) - JARVIS_TOOLS,
        owner=owner,
        relevant_tools=JARVIS_TOOLS,
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
        if "delta" in data and not data.get("thinking"):
            delta = str(data.get("delta") or "")
            if delta and first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            full_response += delta
            if delta:
                yield {"type": "assistant_delta", "text": delta}
        elif data.get("type") == "tool_output" and data.get("tool") == "start_agent_task":
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
        "model": JARVIS_MODEL,
        "transcript_chars": len(text),
        "assistant_chars": len(reply),
        "brain_ms": int((time.perf_counter() - started) * 1000),
        "brain_first_token_ms": first_token_ms,
        "num_ctx": VOICE_CONTEXT_LENGTH,
        "num_predict": _num_predict_for_text(text),
        "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
        "guard_reason": None,
        "agent_metrics": metrics,
        "task_ids": task_ids,
    }
    yield {"type": "final", "assistant_text": reply, "diagnostics": diagnostics, "task_ids": task_ids}


async def _jarvis_reply(chat_session_id: str, text: str, owner: str) -> tuple[str, dict[str, Any], list[str]]:
    final: dict[str, Any] | None = None
    async for event in _jarvis_events(chat_session_id, text, owner, {}):
        if event.get("type") == "final":
            final = event
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
    return f"Jarvis Voice {datetime.now().strftime('%I:%M %p').lstrip('0')}"


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


def setup_voice_routes(session_manager=None, tts_service=None):
    global _SESSION_MANAGER
    _SESSION_MANAGER = session_manager
    router = APIRouter(prefix="/api/voice", tags=["voice"])

    @router.get("/status")
    async def voice_status():
        try:
            from src.settings import load_settings

            settings = load_settings()
            tts_provider = settings.get("tts_provider", "browser")
        except Exception:
            tts_provider = "browser"
        return {
            "mode": "jarvis_call",
            "activation": "call_button",
            "interruption": "stop_and_redirect",
            "stores_raw_audio": False,
            "stt_endpoint": "pc-whisper-stt",
            "brain_endpoint": "jarvis-ollama-local",
            "voice_model": JARVIS_MODEL,
            "tts_provider": tts_provider,
            "fast_rtc_mounted": False,
            "action_bridge": ACTION_BRIDGE_URL,
            "safe_actions": sorted(SAFE_ACTIONS),
        }

    @router.post("/prewarm")
    async def prewarm_voice_brain():
        payload = {
            "model": JARVIS_MODEL,
            "prompt": "Reply exactly: ready",
            "options": {"temperature": 0, "num_predict": 1},
            "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
            "stream": False,
        }
        brain_started = time.perf_counter()
        tts_started = time.perf_counter()
        tts_task = (
            asyncio.create_task(asyncio.to_thread(tts_service.synthesize, "Ready.", False))
            if tts_service
            else None
        )
        brain_ok = False
        brain_error = None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(JARVIS_GENERATE_URL, json=payload)
            response.raise_for_status()
            brain_ok = True
        except Exception as exc:
            logger.warning("Jarvis voice prewarm failed: %s", exc)
            brain_error = str(exc)[:200]
        brain_ms = int((time.perf_counter() - brain_started) * 1000)

        tts_ok = None
        tts_error = None
        if tts_task:
            try:
                tts_ok = bool(await tts_task)
            except Exception as exc:
                logger.warning("Jarvis TTS prewarm failed: %s", exc)
                tts_ok = False
                tts_error = str(exc)[:200]

        return {
            "ok": brain_ok and tts_ok is not False,
            "model": JARVIS_MODEL,
            "brain_ms": brain_ms,
            "brain_error": brain_error,
            "tts_ok": tts_ok,
            "tts_ms": int((time.perf_counter() - tts_started) * 1000) if tts_task else None,
            "tts_error": tts_error,
        }

    @router.post("/sessions")
    async def create_voice_session(request: Request, payload: VoiceSessionCreate):
        state = _load_state()
        session_id = str(uuid.uuid4())
        chat_session_id = payload.chat_session_id.strip() if payload.chat_session_id else None
        if session_manager:
            if chat_session_id:
                try:
                    session_manager.get_session(chat_session_id)
                except Exception:
                    chat_session_id = None
            if not chat_session_id:
                chat_session_id = str(uuid.uuid4())
                session_manager.create_session(
                    session_id=chat_session_id,
                    name=_chat_session_name(),
                    endpoint_url=JARVIS_CHAT_URL,
                    model=JARVIS_MODEL,
                    owner=effective_user(request),
                )
        session = {
            "id": session_id,
            "chat_session_id": chat_session_id,
            "mode": payload.mode,
            "status": "listening",
            "created_at": _now(),
            "updated_at": _now(),
            "turns": [],
            "tasks": [],
            "target": "jarvis",
            "workspace": "home-lab",
            "active_task_id": None,
            "codex_thread_id": None,
            "stores_raw_audio": False,
        }
        state.setdefault("sessions", {})[session_id] = session
        _save_state(state)
        return session

    @router.get("/sessions/{session_id}")
    async def get_voice_session(session_id: str):
        return _session(_load_state(), session_id)

    @router.post("/sessions/{session_id}/target")
    async def update_voice_target(session_id: str, payload: VoiceTargetUpdate):
        if payload.target not in ACTIVE_VOICE_TARGETS:
            raise HTTPException(status_code=409, detail={"message": "Voice worker is not connected"})
        if payload.workspace not in VOICE_WORKSPACES:
            raise HTTPException(status_code=400, detail={"message": "Unknown voice workspace"})
        state = _load_state()
        session = _session(state, session_id)
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
    async def add_voice_turn(session_id: str, payload: VoiceTurnCreate):
        state = _load_state()
        session = _session(state, session_id)
        turn = _append_turn(session, payload.role, payload.text, payload.status or "recorded", payload.task_id)
        _save_state(state)
        return turn

    @router.post("/sessions/{session_id}/respond")
    async def respond_to_voice_turn(session_id: str, payload: VoiceRespondRequest, request: Request):
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail={"message": "Voice turn text is required"})

        state = _load_state()
        session = _session(state, session_id)
        user_turn = _append_turn(session, "user", text, "thinking")
        _append_chat_message(session_manager, session, "user", text, voice_turn_id=user_turn["id"], voice_status="thinking")
        _save_state(state)

        task = None
        diagnostics: dict[str, Any]
        action = _detect_safe_action(text)
        if action:
            task = await _execute_action(VoiceActionRequest(action=action, session_id=session_id, prompt=text))
            reply = "Running that in the background, sir."
            diagnostics = {
                "model": JARVIS_MODEL,
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
                    effective_user(request),
                )
            except Exception as exc:
                session["status"] = "failed"
                session["updated_at"] = _now()
                _append_diagnostic(session, {
                    "model": JARVIS_MODEL,
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
        assistant_turn = _append_turn(session, "assistant", reply, "speaking", task["task_id"] if task else None)
        _append_chat_message(
            session_manager,
            session,
            "assistant",
            reply,
            voice_turn_id=assistant_turn["id"],
            voice_status="speaking",
            task_id=task["task_id"] if task else None,
            diagnostics=diagnostics,
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
    async def stream_voice_response(session_id: str, payload: VoiceRespondRequest, request: Request):
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail={"message": "Voice turn text is required"})
        owner = effective_user(request)
        state = _load_state()
        session = _session(state, session_id)
        user_turn = _append_turn(session, "user", text, "thinking")
        _append_chat_message(session_manager, session, "user", text, voice_turn_id=user_turn["id"], voice_status="thinking")
        speech_turn = _register_speech_turn(session_id)
        session["active_audio_turn_id"] = speech_turn.turn_id
        _save_state(state)
        chat_session_id = str(session.get("chat_session_id") or "")

        async def generate():
            try:
                final: dict[str, Any] | None = None
                yield f"data: {json.dumps({'type': 'state', 'state': 'thinking'})}\n\n"
                yield f"data: {json.dumps({'type': 'audio_ready', 'turn_id': speech_turn.turn_id})}\n\n"
                async for event in _jarvis_events(chat_session_id, text, owner, session):
                    if event.get("type") == "assistant_delta":
                        await speech_turn.append(str(event.get("text") or ""))
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
                    if event.get("type") == "final":
                        final = event
                    yield f"data: {json.dumps(event)}\n\n"
                if not final:
                    raise RuntimeError("Jarvis voice model returned no final event")
                await speech_turn.finish()
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
                )
                _append_diagnostic(current, final["diagnostics"])
                _save_state(current_state)
            except Exception as exc:
                await speech_turn.finish(str(exc)[:240])
                current_state = _load_state()
                current = _session(current_state, session_id)
                current["status"] = "failed"
                _append_diagnostic(current, {
                    "model": JARVIS_MODEL,
                    "transcript_chars": len(text),
                    "assistant_chars": 0,
                    "guard_reason": "brain_failure",
                    "error": str(exc)[:240],
                })
                _save_state(current_state)
                yield f"data: {json.dumps({'type': 'error', 'text': str(exc)[:240]})}\n\n"
            finally:
                if not speech_turn.finished:
                    await speech_turn.finish()
                yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/sessions/{session_id}/turns/{turn_id}/audio")
    async def stream_voice_turn_audio(session_id: str, turn_id: str):
        _session(_load_state(), session_id)
        speech_turn = _SPEECH_TURNS.get((session_id, turn_id))
        if not speech_turn:
            raise HTTPException(status_code=404, detail={"message": "Voice audio turn not found"})
        if not tts_service or not tts_service.available:
            raise HTTPException(status_code=503, detail={"message": "TTS service not available"})

        async def generate_audio():
            sample_rate: int | None = None
            generation_ms = 0
            audio_ms = 0
            blocks = 0
            _set_voice_status(session_id, "buffering", active_audio_turn_id=turn_id)
            try:
                while True:
                    segment = await speech_turn.next_segment()
                    if segment is None:
                        break
                    block_generation_ms = 0
                    block_audio_ms = 0
                    async with TTS_INFERENCE_LOCK:
                        async for event in stream_tts_pcm_segment(tts_service, segment):
                            event_type = event.get("type")
                            if event_type == "start":
                                block_rate = int(event.get("sample_rate") or 0)
                                if not block_rate:
                                    raise RuntimeError("TTS returned an invalid sample rate")
                                if sample_rate is None:
                                    sample_rate = block_rate
                                    yield json.dumps({"type": "start", "sample_rate": sample_rate}) + "\n"
                                elif block_rate != sample_rate:
                                    raise RuntimeError("TTS sample rate changed during a voice turn")
                                yield json.dumps({
                                    "type": "block",
                                    "index": blocks,
                                    "text_chars": len(segment),
                                    "next_target_chars": speech_turn.target_chars,
                                }) + "\n"
                            elif event_type == "audio":
                                yield json.dumps(event, separators=(",", ":")) + "\n"
                            elif event_type == "done":
                                block_generation_ms = int(event.get("generation_ms") or 0)
                                block_audio_ms = int(event.get("audio_ms") or 0)
                    speech_turn.record_block(block_generation_ms, block_audio_ms)
                    generation_ms += block_generation_ms
                    audio_ms += block_audio_ms
                    _set_voice_status(session_id, "speaking", active_audio_turn_id=turn_id)
                    blocks += 1
                yield json.dumps({
                    "type": "done",
                    "blocks": blocks,
                    "generation_ms": generation_ms,
                    "audio_ms": audio_ms,
                    "interrupted": speech_turn.cancelled,
                }) + "\n"
            except Exception as exc:
                logger.exception("Jarvis speech turn %s failed", turn_id)
                _set_voice_status(session_id, "failed", active_audio_turn_id=None)
                yield json.dumps({"type": "error", "error": str(exc)[:240]}) + "\n"
            finally:
                _SPEECH_TURNS.pop((session_id, turn_id), None)

        return StreamingResponse(generate_audio(), media_type="application/x-ndjson")

    @router.post("/sessions/{session_id}/turns/{turn_id}/playback")
    async def update_voice_playback(session_id: str, turn_id: str, payload: VoicePlaybackUpdate):
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
    async def add_voice_diagnostic(session_id: str, payload: VoiceDiagnosticCreate):
        state = _load_state()
        session = _session(state, session_id)
        _append_diagnostic(session, {
            "label": payload.label[:80],
            "client": True,
            **_clean_client_timings(payload.timings),
        })
        session["updated_at"] = _now()
        _save_state(state)
        return {"ok": True}

    @router.post("/sessions/{session_id}/interrupt")
    async def interrupt_voice_session(session_id: str):
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
    async def run_voice_action(payload: VoiceActionRequest):
        task = await _execute_action(payload)

        state = _load_state()
        state.setdefault("actions", {})[task["task_id"]] = task
        if payload.session_id and payload.session_id in state.get("sessions", {}):
            session = state["sessions"][payload.session_id]
            session.setdefault("tasks", []).append(task)
            session["updated_at"] = _now()
        _save_state(state)
        return task

    @router.get("/actions/{task_id}")
    async def get_voice_action(task_id: str):
        action = _load_state().get("actions", {}).get(task_id)
        if not action:
            raise HTTPException(status_code=404, detail={"message": "Voice action not found"})
        return action

    return router
