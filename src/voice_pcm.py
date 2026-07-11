from __future__ import annotations

import asyncio
import io
import re
import wave
from collections.abc import Iterator


_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s|$)")
TTS_INFERENCE_LOCK = asyncio.Lock()


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
