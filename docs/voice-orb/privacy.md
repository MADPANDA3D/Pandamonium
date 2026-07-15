# Privacy and data lifecycle

## Microphone audio

Microphone capture starts only after the user opens Voice Orb and grants browser permission. Tracks stop on End Voice, interruption paths, page hide, or when the page becomes hidden.

With browser STT, Odysseus does not upload the recording to its STT route; consult the browser vendor's terms for the Web Speech implementation. With local or endpoint STT, audio is sent to Odysseus for transcription. Local Whisper may use a temporary file that is deleted after transcription. Endpoint STT transmits the audio to the configured provider. Voice Orb does not intentionally persist raw microphone audio or include it in diagnostics.

## Text and synthesized audio

The spoken question and assistant reply are stored as normal linked-chat history and in the bounded voice-session state needed for continuity. Server-side TTS can cache synthesized audio under `data/tts_cache/`; browser TTS does not create that server cache. Normal Odysseus backup and data-retention rules apply to chat history, voice-session state, and TTS cache.

## Browser state and Calendar

View reporting sends only logical identifiers and open/minimized state. Calendar DOM content is not used as authoritative calendar data. Any calendar comparison must use the connected Calendar sync/list path, and the assistant must say when freshness cannot be confirmed.

## Workers

Worker prompts, progress, and results may be retained in owner-scoped broker state so activity can reconstruct after reload. Status surfaces omit endpoint URLs, private addresses, token values, and token paths. A configured external worker still receives the task text; review that worker's own retention policy.

## Camera

v0.1 does not request camera access, attach a video surface, capture frames, or perform visual analysis. Camera support belongs to a separately reviewed v0.2 release with its own permission and deletion tests.

## Public repository boundary

The public repository includes source, neutral tests, first-party assets, and setup documentation. It excludes private Mark Notes, handovers, personal and client data, private hostnames/IPs/Tailnet names, absolute private paths, credentials, cloned voices, and media without clear redistribution rights.
