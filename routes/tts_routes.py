# routes/tts_routes.py
"""TTS API routes."""

import asyncio
import base64
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from src.voice_pcm import TTS_INFERENCE_LOCK, pcm_frames, take_speech_segment, wav_to_pcm16

logger = logging.getLogger(__name__)

class TTSRequest(BaseModel):
    text: str
    format: str = "audio"  # "audio" or "base64"
    model: str | None = None
    voice: str | None = None
    speed: str | float | None = None
    use_cache: bool = True

def setup_tts_routes(tts_service):
    """Setup TTS routes with the provided TTS service"""
    router = APIRouter(prefix="/api/tts", tags=["tts"])

    @router.get("/stats")
    async def get_tts_stats():
        """Get TTS service statistics"""
        try:
            return tts_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get TTS stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/voices")
    async def get_tts_voices():
        """List selectable TTS voices and current voice settings."""
        try:
            return {
                "settings": tts_service._load_settings(),
                "stats": tts_service.get_stats(),
                "voices": tts_service.list_voices(),
                "custom_voice_allowed": True,
            }
        except Exception as e:
            logger.error(f"Failed to get TTS voices: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/synthesize")
    async def synthesize_speech(request: TTSRequest):
        """Synthesize speech from text"""
        try:
            if not tts_service.available:
                raise HTTPException(
                    status_code=503,
                    detail={"message": "TTS service not available"}
                )
            
            if request.format == "base64":
                audio_b64 = tts_service.synthesize_to_base64(
                    request.text,
                    model=request.model,
                    voice=request.voice,
                    speed=request.speed,
                    use_cache=request.use_cache,
                )
                if not audio_b64:
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Synthesis failed"}
                    )
                return {"audio": audio_b64}
            
            else:  # audio format
                audio_data = tts_service.synthesize(
                    request.text,
                    model=request.model,
                    voice=request.voice,
                    speed=request.speed,
                    use_cache=request.use_cache,
                )
                if not audio_data:
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Synthesis failed"}
                    )
                
                # Detect format from magic bytes (MP3: ID3 tag or sync word ff e0+)
                is_mp3 = audio_data[:3] == b'ID3' or (len(audio_data) >= 2 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0)
                mime = "audio/mpeg" if is_mp3 else "audio/wav"
                return Response(
                    content=audio_data,
                    media_type=mime,
                    headers={
                        "Content-Disposition": "inline; filename=speech.mp3" if "mpeg" in mime else "inline; filename=speech.wav"
                    }
                )
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Synthesis error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Synthesis failed: {str(e)}"}
            )

    @router.post("/stream")
    async def stream_speech(request: TTSRequest):
        """Synthesize bounded text blocks and expose one clean PCM stream."""
        text = request.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail={"message": "Speech text is required"})
        if not tts_service.available:
            raise HTTPException(status_code=503, detail={"message": "TTS service not available"})

        async def generate():
            remaining = text
            first = True
            sample_rate = None
            blocks = 0
            generation_ms = 0
            audio_ms = 0
            try:
                while remaining:
                    segment, remaining = take_speech_segment(remaining, first=first, done=True)
                    if not segment:
                        break
                    started = time.perf_counter()
                    async with TTS_INFERENCE_LOCK:
                        audio = await asyncio.to_thread(
                            tts_service.synthesize,
                            segment,
                            False,
                            request.model,
                            request.voice,
                            request.speed,
                        )
                    block_generation_ms = int((time.perf_counter() - started) * 1000)
                    if not audio:
                        raise RuntimeError("TTS synthesis returned no audio")
                    block_rate, pcm = wav_to_pcm16(audio)
                    if sample_rate is None:
                        sample_rate = block_rate
                        yield json.dumps({"type": "start", "sample_rate": sample_rate}) + "\n"
                    elif block_rate != sample_rate:
                        raise RuntimeError("TTS sample rate changed during a speech turn")

                    block_audio_ms = int(len(pcm) / (sample_rate * 2) * 1000)
                    blocks += 1
                    generation_ms += block_generation_ms
                    audio_ms += block_audio_ms
                    yield json.dumps({
                        "type": "block",
                        "index": blocks - 1,
                        "text_chars": len(segment),
                        "generation_ms": block_generation_ms,
                        "audio_ms": block_audio_ms,
                    }) + "\n"
                    for frame in pcm_frames(pcm):
                        yield json.dumps({
                            "type": "audio",
                            "pcm_base64": base64.b64encode(frame).decode("ascii"),
                        }) + "\n"
                    first = False
                yield json.dumps({
                    "type": "done",
                    "blocks": blocks,
                    "generation_ms": generation_ms,
                    "audio_ms": audio_ms,
                }) + "\n"
            except Exception as exc:
                logger.exception("Segmented TTS stream failed")
                yield json.dumps({"type": "error", "error": str(exc)[:240]}) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @router.post("/clear-cache")
    async def clear_tts_cache():
        """Clear TTS cache"""
        try:
            tts_service.clear_cache()
            return {"success": True, "message": "Cache cleared"}
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
