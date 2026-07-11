# routes/tts_routes.py
"""
TTS API routes — multi-provider (local Kokoro, API endpoint, browser).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
import httpx
import logging
import os

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
        """Relay the configured endpoint's native PCM stream without buffering it."""
        from src.database import ModelEndpoint, SessionLocal

        settings = tts_service._load_settings()
        provider = settings.get("tts_provider", "")
        if not provider.startswith("endpoint:"):
            raise HTTPException(status_code=503, detail={"message": "Streaming TTS requires an endpoint provider"})
        endpoint_id = provider.split(":", 1)[1]
        db = SessionLocal()
        try:
            endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not endpoint:
                raise HTTPException(status_code=503, detail={"message": "TTS endpoint not found"})
            base_url = endpoint.base_url.rstrip("/")
            api_key = endpoint.api_key
        finally:
            db.close()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": request.model or settings.get("tts_model"),
            "input": request.text,
            "voice": request.voice or settings.get("tts_voice"),
            "speed": request.speed if request.speed is not None else settings.get("tts_speed", 1),
            "response_format": "pcm_s16le",
        }
        client = httpx.AsyncClient(timeout=float(os.getenv("ODYSSEUS_TTS_ENDPOINT_TIMEOUT", "180")))
        stream = client.stream("POST", f"{base_url}/audio/speech/stream", json=payload, headers=headers)
        response = await stream.__aenter__()
        if response.status_code >= 400:
            detail = (await response.aread()).decode(errors="replace")[:500]
            await stream.__aexit__(None, None, None)
            await client.aclose()
            raise HTTPException(status_code=502, detail={"message": detail or "Streaming synthesis failed"})

        async def relay():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await stream.__aexit__(None, None, None)
                await client.aclose()

        return StreamingResponse(relay(), media_type="application/x-ndjson")

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
