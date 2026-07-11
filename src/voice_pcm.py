from __future__ import annotations

import asyncio
import io
import json
import os
import re
import wave
from collections.abc import AsyncIterator, Iterator
from typing import Any


_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")
TTS_INFERENCE_LOCK = asyncio.Lock()


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


def take_speech_segment(
    text: str,
    *,
    first: bool = False,
    target_chars: int = 280,
    done: bool = False,
) -> tuple[str | None, str]:
    text = text.lstrip()
    if not text:
        return None, ""

    minimum = 80 if first else min(160, target_chars)
    maximum = 220 if first else min(360, max(280, target_chars + 80))
    if not done and len(text) < minimum:
        return None, text

    limit = min(len(text), maximum)
    boundaries = [match.end() for match in _SENTENCE_END.finditer(text[:limit]) if match.end() >= minimum]
    if boundaries:
        cut = min(boundaries, key=lambda value: abs(value - target_chars))
    elif done and len(text) <= maximum:
        cut = len(text)
    elif len(text) > maximum or done:
        cut = text.rfind(" ", minimum, maximum + 1)
        if cut < minimum:
            cut = maximum
    else:
        return None, text

    return text[:cut].strip(), text[cut:].lstrip()


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
