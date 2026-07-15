# Voice providers

Voice Orb reuses Odysseus provider and owner-scoped endpoint configuration. It does not create a second credential store.

## Conversation model

The default is the model already selected for the linked chat, or the user's current/default Odysseus model for a new voice chat. Operators may set:

- `ODYSSEUS_VOICE_ENDPOINT_ID` to an existing Odysseus endpoint ID.
- `ODYSSEUS_VOICE_MODEL` to an explicit model served by that endpoint.
- `ODYSSEUS_VOICE_PERSONA` to a display name; the public default is `Odysseus`.

An override must resolve through the existing owner-scoped endpoint store. Put credentials in Odysseus Settings or its supported secret configuration, never in the repository.

## Speech to text

Choose one in Settings:

- `browser`: uses the browser Web Speech API when supported. Audio is not uploaded to the Odysseus STT route, but the browser vendor's own behavior and privacy terms apply.
- `local`: uses `faster-whisper` on the Odysseus host. It is optional and loaded only when selected.
- `endpoint:<id>`: sends captured audio to the selected OpenAI-compatible `/audio/transcriptions` endpoint.
- `disabled`: the Orb reports that STT must be enabled before listening.

The existing `ODYSSEUS_STT_MAX_AUDIO_BYTES` upload limit applies to server-side transcription.

## Text to speech

Choose one in Settings:

- `browser`: uses `speechSynthesis` and stores no server-generated audio.
- `local`: uses the optional local Kokoro pipeline when its dependencies and compatible GPU are present.
- `endpoint:<id>`: calls the selected OpenAI-compatible `/audio/speech` endpoint.
- `disabled`: text replies still complete, but no spoken playback is available.

Server-generated TTS may use the existing Odysseus cache under `data/tts_cache/`. See the [privacy lifecycle](privacy.md).

## Vision

Visual description reuses the existing owner-scoped Odysseus model settings. Voice Orb first tries the active conversation model when that endpoint reports or matches a vision-capable model. It then uses the configured Vision model and its existing Vision fallback chain. No remote or paid vision model is selected, prewarmed, or provisioned automatically.

Only the exact `What do you see?` or `Describe what you see.` command sends one current frame. If no vision-capable model succeeds, the Orb says that it could not analyze the frame; it does not silently route the image to an unrelated provider.

## Provider checks

1. Confirm a normal text chat completes with the selected model.
2. Open Settings and test STT and TTS independently.
3. Open Voice Orb and grant microphone permission.
4. If an endpoint fails, check the endpoint's own health and Odysseus logs; the public status response intentionally omits credentials and endpoint URLs.
5. For camera description, enable Vision in Settings and test the selected Vision model with a normal image before using Voice Orb.
