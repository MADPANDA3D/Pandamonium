"""Jarvis live voice session and safe action bridge routes."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from core.constants import DATA_DIR
from core.models import ChatMessage
from src.agent_loop import stream_agent_loop
from src.agent_tools import TOOL_TAGS
from src.agent_worker_adapters import worker_catalog
from src.auth_helpers import effective_user
from src.user_time import clear_user_time_context, now_user_local, set_user_tz_name, set_user_tz_offset
from src.voice_pcm import TTS_INFERENCE_LOCK

VOICE_STATE_FILE = Path(DATA_DIR) / "voice_sessions.json"
ACTION_BRIDGE_URL = os.getenv("ODYSSEUS_ACTION_BRIDGE_URL", "http://127.0.0.1:8010/actions")
JARVIS_OLLAMA_URL = os.getenv("ODYSSEUS_JARVIS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
JARVIS_GENERATE_URL = f"{JARVIS_OLLAMA_URL}/api/generate"
JARVIS_CHAT_URL = f"{JARVIS_OLLAMA_URL}/v1/chat/completions"
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
The active system build is Mark 6 - Jarvis Voice-First Agent Operating System. Unless Leo explicitly mentions scripture, Bible study, or another domain, references to a numbered Mark mean indexed Jarvis architecture builds.
Answer naturally with enough substance for the question: usually one to four short spoken paragraphs, and more when Leo explicitly asks for a deep explanation. Never describe pacing, pauses, or speaking style.
For casual greetings, answer in no more than two sentences. Do not volunteer Leo's location, local time, system status, scheduling options, or a capability menu unless he asks. Do not repeat wording or facts already stated in recent turns.
You coordinate work; you do not pretend to have inspected systems you have not inspected. Use get_runtime_status for runtime or model questions. Use search_jarvis_knowledge for curated background. For latest, current, or business-update requests, use background knowledge and start a read-only pc-codex task to inspect current sources.
Model-initiated delegation is always read-only. Tell Leo briefly that work is running in the background, then let worker events deliver progress and the final result. Never invent worker results, runtime facts, paths, or endpoint details."""

WORKER_LABELS = {
    "pc-codex": "PC Codex",
    "hermes": "Hermes",
    "vps-codex": "VPS Codex",
}
ACTIVE_VOICE_TARGETS = {"jarvis"} | {
    worker for worker, details in worker_catalog().items() if details.get("enabled")
}
VOICE_WORKSPACES = {"business", "home-lab", "project-linux", "vps-ops"}


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
    """One completed spoken payload consumed by one TTS inference."""

    def __init__(self, session_id: str, turn_id: str):
        self.session_id = session_id
        self.turn_id = turn_id
        self.text = ""
        self.finished = False
        self.cancelled = False
        self.error: str | None = None
        self.created_at = time.monotonic()
        self.done = asyncio.Event()

    async def complete(self, text: str) -> None:
        self.text = text.strip()
        self.finished = True
        self.done.set()

    async def fail(self, error: str) -> None:
        self.error = error
        self.finished = True
        self.done.set()

    async def cancel(self) -> None:
        self.cancelled = True
        self.finished = True
        self.done.set()

    async def wait(self) -> str:
        await self.done.wait()
        if self.cancelled:
            raise RuntimeError("Voice playback was interrupted")
        if self.error:
            raise RuntimeError(self.error)
        if not self.text:
            raise RuntimeError("Jarvis produced no spoken response")
        return self.text


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


async def _select_spoken_text(prompt: str, response_text: str) -> str:
    response_text = response_text.strip()
    if _asks_read_all(prompt) and len(response_text) <= 4000:
        return response_text
    paragraphs = [part for part in re.split(r"\n\s*\n", response_text) if part.strip()]
    if len(response_text) <= 1200 and len(paragraphs) <= 3:
        return response_text

    summary_prompt = (
        "Summarize the response below for spoken playback. Return only two or three short conversational "
        "paragraphs, no markdown tables, code, paths, citations, headings, or preamble. Keep the important "
        "outcome, blocker, and next action. Maximum 1200 characters.\n\nRESPONSE:\n"
        + response_text[:12000]
    )
    payload = {
        "model": JARVIS_MODEL,
        "prompt": summary_prompt,
        "options": {"temperature": 0.1, "num_predict": 320, "num_ctx": 8192},
        "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            result = await client.post(JARVIS_GENERATE_URL, json=payload)
        result.raise_for_status()
        summary = _strip_think_blocks(str(result.json().get("response") or ""))
        if summary:
            return _bounded_spoken_text(summary)
    except Exception as exc:
        logger.warning("Jarvis spoken-summary fallback used: %s", str(exc)[:200])
    return _bounded_spoken_text(response_text)


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
    if re.search(r"\b(business|clients?|marketing|mad\s*panda|campaign|website|crm)\b", text, re.IGNORECASE):
        return "business"
    if re.search(r"\b(project\s+linux|linux\s+(?:desktop|workstation)|hyprland)\b", text, re.IGNORECASE):
        return "project-linux"
    return "home-lab"


def _selected_workspace(text: str, current: str) -> str:
    if re.search(r"\b(business|clients?|marketing|mad\s*panda|campaign|website|crm)\b", text, re.IGNORECASE):
        return "business"
    if re.search(r"\b(project\s+linux|linux\s+(?:desktop|workstation)|hyprland)\b", text, re.IGNORECASE):
        return "project-linux"
    return current


def _delegation_route(text: str) -> tuple[str, str] | None:
    """Map Leo's stable names to fixed workers and server-controlled workspaces."""
    if re.search(r"\b(vps|online server|public server|hosting server|mad\s*panda hosting)\b", text, re.IGNORECASE):
        return "vps-codex", "vps-ops"
    if re.search(r"\bhermes\b", text, re.IGNORECASE):
        return "hermes", "home-lab"
    if re.search(
        r"\b(pc codex|my codex|desktop codex|computer codex)\b|"
        r"\bcodex\s+(?:on|from)\s+my\s+(?:pc|computer)\b|"
        r"\b(?:ask|talk to|speak to|check with)\s+my computer\b",
        text,
        re.IGNORECASE,
    ):
        return "pc-codex", _workspace_for_text(text)
    if re.search(r"\b(project\s+nimbus|nimbus|home cloud|my cloud|the cloud)\b", text, re.IGNORECASE):
        return "pc-codex", "home-lab"
    return None


def _target_switch(text: str) -> str | None:
    switch_phrase = r"\b(?:talk|speak|connect|switch)(?:\s+me)?(?:\s+back)?\s+(?:to|with)\s+"
    if re.search(switch_phrase + r"jarvis\b", text, re.IGNORECASE) or re.search(
        r"\b(?:return|go|come)\s+(?:back\s+)?to\s+jarvis\b",
        text,
        re.IGNORECASE,
    ):
        return "jarvis"
    if not re.search(switch_phrase, text, re.IGNORECASE):
        return None
    delegation = _delegation_route(text)
    return delegation[0] if delegation else None


def _background_delegation(text: str) -> tuple[str, str] | None:
    route = _delegation_route(text)
    if route and re.search(r"\b(?:ask|have|get|tell|send|check)\b", text, re.IGNORECASE):
        return route
    return None


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


def _casual_greeting_reply(voice_session: dict) -> str:
    replies = (
        "I’m doing well, Leo. What are we working on?",
        "Good to hear from you, Leo. What would you like to tackle?",
    )
    recent = {str(turn.get("text") or "") for turn in voice_session.get("turns", [])[-6:]}
    return next((reply for reply in replies if reply not in recent), replies[0])


def _approval_choice(text: str) -> str | None:
    value = " ".join(re.sub(r"[^a-z' ]", " ", text.lower()).split())
    deny = bool(re.search(r"\b(?:deny|decline|reject|don't|do not|no)\b", value))
    approve = bool(re.search(r"\b(?:approve|approved|yes|okay|ok|go ahead|do it|proceed)\b", value))
    if deny == approve:
        return None
    if deny:
        return "deny"
    if approve:
        return "once"
    return None


def _explicit_reply_target(text: str) -> str | None:
    if not re.search(r"\b(?:answer|reply|respond)\b", text, re.IGNORECASE):
        return None
    route = _delegation_route(text)
    return route[0] if route else None


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
            "model": JARVIS_MODEL,
            "transcript_chars": len(text),
            "assistant_chars": len(reply),
            "brain_ms": 0,
            "brain_first_token_ms": 0,
            "num_ctx": VOICE_CONTEXT_LENGTH,
            "num_predict": 0,
            "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
            "guard_reason": guard_reason,
            "task_ids": task_ids,
            **extra,
        },
        "task_ids": task_ids,
    }


async def _dispatch_worker_request(
    chat_session_id: str,
    worker: str,
    workspace: str,
    prompt: str,
    owner: str,
    _voice_session: dict,
) -> tuple[dict, str]:
    from src.jarvis_agent import find_active_task, start_task, task_action

    active = find_active_task(chat_session_id, worker, workspace, owner)
    if active:
        if worker in {"pc-codex", "vps-codex"} and active.get("status") not in {"waiting", "waiting_approval"}:
            try:
                await task_action(
                    active["task_id"],
                    "steer",
                    {"prompt": prompt},
                    persist_user_message=False,
                )
                return active, "steered"
            except Exception as exc:
                logger.info("%s active task rejected steering: %s", worker, str(exc)[:160])
        return active, "busy"

    task = await start_task(
        worker,
        chat_session_id,
        workspace,
        prompt,
        "read_only",
        False,
        owner,
    )
    return task, "blocked" if task.get("status") == "blocked" or not task.get("task_id") else "started"


async def _server_routed_events(chat_session_id: str, text: str, owner: str, voice_session: dict):
    target_switch = _target_switch(text)
    if target_switch:
        workspace = {
            "vps-codex": "vps-ops",
            "hermes": "home-lab",
        }.get(target_switch, _workspace_for_text(text))
        label = WORKER_LABELS.get(target_switch, "Jarvis")
        if target_switch != "jarvis":
            from src.jarvis_agent import worker_statuses

            target_status = (await worker_statuses()).get(target_switch) or {}
            if not target_status.get("enabled"):
                reply = f"{label} is not connected, so I have not switched you or claimed a task is running."
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
                        "guard_reason": f"{target_switch}_not_connected",
                        "task_ids": [],
                    },
                    "task_ids": [],
                }
                return
        reply = "You’re back with Jarvis." if target_switch == "jarvis" else f"You’re connected to {label}. What would you like me to handle?"
        yield {"type": "target_changed", "target": target_switch, "workspace": workspace}
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, f"target_switch_{target_switch}")
        return

    if _is_casual_greeting(text):
        reply = _casual_greeting_reply(voice_session)
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, "casual_greeting")
        return

    delegation = _background_delegation(text)
    bounded_pc_business = bool(
        delegation
        and delegation[0] == "pc-codex"
        and _asks_current_business(text)
    )
    if delegation and not bounded_pc_business:
        worker, workspace = delegation
        label = WORKER_LABELS[worker]
        prompt = f"Leo asked through Jarvis voice. Handle this read-only request and report factual progress and the final result:\n\n{text}"
        try:
            task, action = await _dispatch_worker_request(
                chat_session_id, worker, workspace, prompt, owner, voice_session,
            )
        except Exception as exc:
            logger.warning("Jarvis could not dispatch %s task: %s", worker, str(exc)[:240])
            task, action = {}, "blocked"
        if action == "started":
            reply = f"I’m asking {label} to handle that in the {workspace} workspace. I’ll keep you updated here."
        elif action == "steered":
            reply = f"I’ve passed that follow-up to the active {label} task."
        elif action == "busy":
            reply = f"{label} is still working and could not accept another instruction yet. You can wait, cancel it, or switch agents."
        else:
            reply = f"{label} is not connected, so I could not start the request."
        task_ids = [task["task_id"]] if task.get("task_id") and action != "blocked" else []
        if action in {"started", "steered"}:
            yield {
                "type": "agent_task",
                "task_id": task["task_id"],
                "worker": worker,
                "workspace": workspace,
                "foreground": False,
            }
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, f"delegation_{action}_{worker}", task_ids)
        return

    from src.jarvis_agent import find_active_task, get_task, task_action

    selected_target = str(voice_session.get("target") or "jarvis")
    selected_workspace = _selected_workspace(text, str(voice_session.get("workspace") or "home-lab"))
    active = (
        find_active_task(chat_session_id, selected_target, selected_workspace, owner)
        if selected_target != "jarvis"
        else None
    )
    pending = get_task(str(voice_session.get("active_task_id") or ""))
    if (
        not pending
        or pending.get("session_id") != chat_session_id
        or pending.get("owner") not in (None, owner)
        or not _pending_task_accepts_turn(pending, text, selected_target)
    ):
        pending = active

    if pending and pending.get("status") == "waiting_approval":
        choice = _approval_choice(text)
        if choice:
            try:
                await task_action(
                    pending["task_id"],
                    "approval",
                    {"choice": choice, "spoken_text": text},
                    persist_user_message=False,
                )
                verb = "denied" if choice == "deny" else "approved once"
                reply = f"I {verb} that request for {WORKER_LABELS.get(pending['worker'], 'the worker')}."
                guard = f"worker_approval_{choice}"
            except Exception as exc:
                logger.warning("Voice worker approval failed: %s", str(exc)[:200])
                reply = "I could not submit that approval. The task remains paused."
                guard = "worker_approval_failed"
        else:
            reply = "That task is waiting for approval. Please say approve once or deny."
            guard = "worker_approval_unclear"
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard, [pending["task_id"]])
        return

    if pending and pending.get("status") == "waiting":
        try:
            await task_action(
                pending["task_id"],
                "reply",
                {"answers": _question_answers(pending, text)},
                persist_user_message=False,
            )
            reply = f"I passed your answer to {WORKER_LABELS.get(pending['worker'], 'the worker')}."
            guard = "worker_question_reply"
        except Exception as exc:
            logger.warning("Voice worker reply failed: %s", str(exc)[:200])
            reply = "I could not submit that answer. The task is still waiting for input."
            guard = "worker_question_reply_failed"
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, guard, [pending["task_id"]])
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

    if selected_target != "jarvis":
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
        if action == "started":
            reply = f"{label} is working on that now."
        elif action == "steered":
            reply = f"I passed that follow-up to {label}'s active task."
        elif action == "busy":
            reply = f"{label} is still working and cannot accept another instruction yet. You can wait, cancel it, or switch agents."
        else:
            reply = f"{label} is not connected, so I could not start the request."
        task_ids = [task["task_id"]] if task.get("task_id") and action != "blocked" else []
        if action in {"started", "steered"}:
            yield {
                "type": "agent_task",
                "task_id": task["task_id"],
                "worker": worker,
                "workspace": workspace,
                "foreground": True,
            }
        yield {"type": "assistant_delta", "text": reply}
        yield _server_final_event(text, reply, f"selected_{action}_{worker}", task_ids)
        return

    if _asks_current_business(text):
        prompt = (
            "Give Leo a bounded, read-only Business status check that preserves the exact depth he requested. "
            "Start with the central Business command center and only the newest dated client handovers needed to answer. "
            "Unless Leo explicitly asks for every client or a deep/full report, return at most three verified priorities in 250 words or fewer. "
            "Do not inventory every client, run capability or service discovery, or use external connectors unless Leo explicitly named that source. "
            "Mark stale or unknown facts clearly. Never infer meetings, schedules, workflows, deliverables, or client status. Make no changes.\n\n"
            f"Leo's exact request:\n{text}"
        )
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
    pending_voice_task = False
    if voice_session.get("active_task_id"):
        from src.jarvis_agent import get_task

        pending = get_task(str(voice_session["active_task_id"]))
        if (
            pending
            and pending.get("session_id") == chat_session_id
            and pending.get("owner") in (None, owner)
        ):
            pending_voice_task = _pending_task_accepts_turn(
                pending,
                text,
                str(voice_session.get("target") or "jarvis"),
            )
    if (
        _target_switch(text)
        or _is_casual_greeting(text)
        or _background_delegation(text)
        or _asks_runtime_status(text)
        or _asks_current_business(text)
        or str(voice_session.get("target") or "jarvis") != "jarvis"
        or pending_voice_task
    ):
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


async def _jarvis_reply(
    chat_session_id: str,
    text: str,
    owner: str,
    voice_session: dict | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    final: dict[str, Any] | None = None
    async for event in _jarvis_events(chat_session_id, text, owner, voice_session or {}):
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
    return f"Jarvis Voice {now_user_local().strftime('%I:%M %p').lstrip('0')}"


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
        async def prewarm_tts() -> tuple[str, bool | None, int | None, str | None]:
            if not tts_service:
                return "unavailable", None, None, None
            try:
                if not tts_service.available:
                    return "unavailable", None, None, None
            except Exception as exc:
                return "failed", False, None, str(exc)[:200]
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
                    audio = await asyncio.wait_for(asyncio.shield(job), timeout=20)
                    return (
                        "warmed" if audio else "failed",
                        bool(audio),
                        int((time.perf_counter() - started) * 1000),
                        None if audio else "TTS prewarm returned no audio",
                    )
                except asyncio.TimeoutError:
                    return "failed", False, 20_000, "TTS prewarm exceeded 20 seconds"
                except Exception as exc:
                    logger.warning("Jarvis TTS prewarm failed: %s", exc)
                    return "failed", False, int((time.perf_counter() - started) * 1000), str(exc)[:200]
            finally:
                if job.done():
                    release_when_done(job)
                else:
                    job.add_done_callback(release_when_done)

        payload = {
            "model": JARVIS_MODEL,
            "prompt": "Reply exactly: ready",
            "options": {"temperature": 0, "num_predict": 1},
            "keep_alive": VOICE_OLLAMA_KEEP_ALIVE,
            "stream": False,
        }
        brain_started = time.perf_counter()
        tts_task = asyncio.create_task(prewarm_tts())
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

        tts_state, tts_ok, tts_ms, tts_error = await tts_task

        return {
            "ok": brain_ok and tts_state != "failed",
            "model": JARVIS_MODEL,
            "brain_ms": brain_ms,
            "brain_error": brain_error,
            "tts_state": tts_state,
            "tts_ok": tts_ok,
            "tts_ms": tts_ms,
            "tts_error": tts_error,
        }

    @router.post("/sessions")
    async def create_voice_session(request: Request, payload: VoiceSessionCreate):
        _set_user_time_from_request(request)
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
        if payload.target != "jarvis":
            from src.jarvis_agent import worker_statuses

            target_status = (await worker_statuses()).get(payload.target) or {}
            if not target_status.get("enabled"):
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
        _set_user_time_from_request(request)
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
                    session,
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
        _set_user_time_from_request(request)
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
                spoken_text = await _select_spoken_text(text, str(final["assistant_text"]))
                final["diagnostics"]["spoken_chars"] = len(spoken_text)
                await speech_turn.complete(spoken_text)
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
                await speech_turn.fail(str(exc)[:240])
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
                    await speech_turn.fail("Jarvis voice response ended before speech was ready")
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

        _set_voice_status(session_id, "buffering", active_audio_turn_id=turn_id)
        try:
            spoken_text = await speech_turn.wait()
            started = time.perf_counter()
            async with TTS_INFERENCE_LOCK:
                audio = await asyncio.to_thread(tts_service.synthesize, spoken_text, False)
            if not audio:
                raise RuntimeError("TTS synthesis failed")
            if speech_turn.cancelled:
                raise RuntimeError("Voice playback was interrupted")
            _set_voice_status(session_id, "speaking", active_audio_turn_id=turn_id)
            state = _load_state()
            current = _session(state, session_id)
            _append_diagnostic(current, {
                "label": "tts",
                "turn_id": turn_id,
                "spoken_chars": len(spoken_text),
                "tts_ms": int((time.perf_counter() - started) * 1000),
                "tts_inferences": 1,
            })
            _save_state(state)
            is_mp3 = audio[:3] == b"ID3" or (len(audio) >= 2 and audio[0] == 0xff and (audio[1] & 0xe0) == 0xe0)
            return Response(
                content=audio,
                media_type="audio/mpeg" if is_mp3 else "audio/wav",
                headers={"Content-Disposition": "inline; filename=jarvis.mp3" if is_mp3 else "inline; filename=jarvis.wav"},
            )
        except Exception as exc:
            _set_voice_status(session_id, "failed", active_audio_turn_id=None)
            raise HTTPException(status_code=409 if speech_turn.cancelled else 502, detail={"message": str(exc)[:240]}) from exc
        finally:
            _SPEECH_TURNS.pop((session_id, turn_id), None)

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
