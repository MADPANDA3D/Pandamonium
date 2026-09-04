from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import wave
from collections.abc import AsyncIterator, Iterator
from typing import Any


TTS_INFERENCE_LOCK = asyncio.Lock()
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")
SHORT_RESULT_WORDS = 75
SUMMARY_WORDS = 40


def speech_text(text: str, *, preserve_code: bool = False) -> str:
    """Turn display Markdown into natural speech without changing the chat copy."""
    from src.authority_protocol import redact_secret_text

    text = redact_secret_text(text)
    text = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", text, flags=re.IGNORECASE)
    if preserve_code:
        text = re.sub(r"(?:```|~~~)[^\n]*\n?([\s\S]*?)(?:```|~~~)", r"\1", text)
    else:
        text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "", text)
    text = re.sub(r"\(\s*ID\s*[:#]?\s*[A-Za-z0-9_-]{6,}\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b[0-9a-f]{10,}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", html.unescape(text))

    paragraphs = []
    for paragraph in re.split(r"\n\s*\n", text):
        lines = []
        for line in paragraph.splitlines():
            line = re.sub(r"^\s*(?:#{1,6}|>|[-*+]\s+|\d+[.)]\s+)", "", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            line = re.sub(r"[*_~]+", "", line)
            line = " ".join(line.split())
            if line and not re.fullmatch(r"\|?\s*:?-{3,}.*", line):
                lines.append(line.strip(" |").rstrip(" :;,-"))
        if lines:
            paragraphs.append(" ".join(lines))
    return "\n\n".join(paragraphs).strip()


def asks_read_all(text: str) -> bool:
    return bool(re.search(
        r"\b(?:read|speak|say)\s+(?:it\s+all|all(?:\s+of\s+it)?|everything|"
        r"the\s+(?:whole|full)\s+(?:thing|response|result|answer|file|document|report)(?:\s+aloud)?)\b",
        text,
        re.IGNORECASE,
    ))


def speakable_word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text, flags=re.UNICODE))


def _limit_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(" ,;:-") + "."


def structured_speech_kind(text: str) -> str | None:
    value = str(text or "")
    if re.search(r"(?m)^\s*(?:```|~~~)", value):
        return "code"
    if re.search(r"(?m)^\s*\|.+\|\s*$\n\s*\|\s*:?-{3,}", value):
        return "table"
    if value.lstrip().startswith(("{", "[")):
        try:
            if isinstance(json.loads(value), (dict, list)):
                return "structured result"
        except (TypeError, ValueError):
            pass
    if len(re.findall(r"(?m)^\s*(?:\d{4}-\d{2}-\d{2}|\[[A-Z]+\]|(?:DEBUG|INFO|WARN|ERROR)\b)", value)) >= 2:
        return "logs"
    if re.search(r"(?m)^Traceback \(most recent call last\):|^\s*File \".+\", line \d+|\b(?:Exception|Error):", value):
        return "logs"
    if len(re.findall(r"(?m)^\s*[-*+]\s+\[[ xX]\]\s+", value)) >= 2:
        return "checklist"
    if len(re.findall(r"(?m)^\s*#{1,6}\s+", value)) >= 2:
        return "document"
    return None


def result_speech(
    text: str,
    *,
    kind: str,
    label: str = "The tool",
    explicit_read_all: bool = False,
    provided_spoken_text: str | None = None,
    provided_speech_mode: str | None = None,
    handoff_text: str | None = None,
) -> dict[str, str]:
    """Apply the deterministic spoken-result contract without another model call."""
    structured = structured_speech_kind(text)
    cleaned = speech_text(text, preserve_code=explicit_read_all)
    if explicit_read_all:
        return {"spoken_text": cleaned, "speech_mode": "verbatim"}

    if kind == "approval":
        return {"spoken_text": cleaned, "speech_mode": "verbatim"}

    if kind == "conversation" and not structured:
        return {"spoken_text": cleaned, "speech_mode": "verbatim"}

    if kind == "failure":
        if not cleaned or structured:
            cleaned = f"{label} failed. Review the useful error in chat, fix the reported issue, and try again."
        elif not re.search(r"\b(?:try|retry|reconnect|fix|check|review|open|choose|wait)\b", cleaned, re.IGNORECASE):
            cleaned += " Fix the reported issue and try again."
        return {"spoken_text": _limit_words(cleaned, SUMMARY_WORDS), "speech_mode": "error"}

    if structured or handoff_text:
        spoken = speech_text(handoff_text or f"{label} finished. The complete {structured or 'result'} is in chat.")
        return {"spoken_text": _limit_words(spoken, SUMMARY_WORDS), "speech_mode": "handoff"}

    if speakable_word_count(cleaned) <= SHORT_RESULT_WORDS:
        return {"spoken_text": cleaned, "speech_mode": "verbatim"}

    provided = speech_text(provided_spoken_text or "")
    if (
        provided
        and provided_speech_mode in {"summary", "handoff"}
        and speakable_word_count(provided) <= SUMMARY_WORDS
    ):
        return {"spoken_text": provided, "speech_mode": provided_speech_mode}

    spoken = speech_text(handoff_text or f"{label} finished. The complete result is in chat.")
    return {"spoken_text": _limit_words(spoken, SUMMARY_WORDS), "speech_mode": "handoff"}


def speech_blocks(text: str, *, first_max_chars: int = 280, max_chars: int = 360) -> list[str]:
    """Split final spoken text into paragraph-first Chatterbox-safe blocks."""
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    blocks: list[str] = []

    for paragraph in paragraphs:
        remainder = paragraph
        while remainder:
            hard_limit = first_max_chars if not blocks and len(remainder) > max_chars else max_chars
            if len(remainder) <= hard_limit:
                blocks.append(remainder)
                break

            minimum = max(80, hard_limit // 2)
            sentence_cuts = [
                match.end()
                for match in _SENTENCE_END.finditer(remainder[: hard_limit + 1])
                if match.end() >= minimum
            ]
            cut = sentence_cuts[-1] if sentence_cuts else remainder.rfind(" ", minimum, hard_limit + 1)
            if cut < minimum:
                cut = hard_limit
            blocks.append(remainder[:cut].strip())
            remainder = remainder[cut:].strip()

    return [block for block in blocks if block]


async def stream_tts_pcm_segment(
    tts_service,
    text: str,
    *,
    model: str | None = None,
    voice: str | None = None,
    speed: str | float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Relay one configured endpoint segment through its native PCM stream."""
    import httpx

    from src.database import ModelEndpoint, SessionLocal

    settings = tts_service._load_settings()
    provider = settings.get("tts_provider", "")
    if not isinstance(provider, str) or not provider.startswith("endpoint:"):
        raise RuntimeError("Streaming TTS requires an endpoint provider")

    endpoint_id = provider.split(":", 1)[1]
    db = SessionLocal()
    try:
        endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        if not endpoint:
            raise RuntimeError("TTS endpoint not found")
        base_url = endpoint.base_url.rstrip("/")
        api_key = endpoint.api_key
    finally:
        db.close()

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model or settings.get("tts_model"),
        "input": text,
        "voice": voice or settings.get("tts_voice"),
        "speed": speed if speed is not None else settings.get("tts_speed", 1),
        "response_format": "pcm_s16le",
    }
    timeout = float(os.getenv("ODYSSEUS_TTS_ENDPOINT_TIMEOUT", "180"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{base_url}/audio/speech/stream", json=payload, headers=headers) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode(errors="replace")[:500]
                raise RuntimeError(detail or "Streaming synthesis failed")
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("TTS endpoint returned an invalid stream event") from exc
                if event.get("type") == "error":
                    raise RuntimeError(str(event.get("error") or "VoxCPM streaming failed"))
                yield event


def wav_to_pcm16(audio: bytes) -> tuple[int, bytes]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                raise ValueError("TTS must return mono PCM16 WAV audio")
            sample_rate = source.getframerate()
            if sample_rate <= 0:
                raise ValueError("TTS returned an invalid sample rate")
            return sample_rate, source.readframes(source.getnframes())
    except wave.Error as exc:
        raise ValueError("TTS returned invalid WAV audio") from exc


def pcm_frames(pcm: bytes, frame_bytes: int = 96_000) -> Iterator[bytes]:
    frame_bytes = max(2, frame_bytes - (frame_bytes % 2))
    for offset in range(0, len(pcm), frame_bytes):
        frame = pcm[offset : offset + frame_bytes]
        if frame:
            yield frame
