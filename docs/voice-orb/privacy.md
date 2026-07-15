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

Camera use is user-initiated. `Open your eyes.` requests browser permission and displays the live camera beneath the transparent orb canvas; the browser's camera-use indicator remains authoritative. Camera and clip playback are mutually exclusive.

`What do you see?` and `Describe what you see.` capture one current frame. The request accepts JPEG or PNG only, no larger than 1024 by 576 and approximately 1 MiB decoded. Odysseus validates base64, declared MIME, image signature, and dimensions. The frame exists only in browser/request memory: Voice Orb does not save it, cache it, add it to diagnostics, or log its bytes. Normal history retains only the spoken question, resulting description, and model metadata.

Every camera track stops on `Close your eyes.`, End Voice, voice error, permission loss, track-ended, page hide, hidden-page transition, or a switch to clip playback. A generation token prevents a late permission response from reopening a stopped camera. This release does not continuously sample, record, identify faces, monitor remotely, or retain camera imagery.

## Built-in media

Clip playback accepts a same-origin manifest ID only. v0.2 includes one first-party silent abstract demonstration loop dedicated under CC0 1.0; its source, attribution, license, and SHA-256 checksum are recorded in the media manifest and provenance README. It includes no actor likeness, cloned voice, third-party audio, or copyrighted motivational footage. Large clips are not placed in the service-worker cache.

## Public repository boundary

The public repository includes source, neutral tests, first-party assets, and setup documentation. It excludes private Mark Notes, handovers, personal and client data, private hostnames/IPs/Tailnet names, absolute private paths, credentials, cloned voices, and media without clear redistribution rights.
