"""Disposable, locally generated audio input for actual yt-dlp operation checks."""
import wave

with wave.open("/runtime/check.wav", "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(8000)
    audio.writeframes(b"\0\0" * 800)
