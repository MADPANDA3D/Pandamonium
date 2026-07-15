# Troubleshooting

## The Orb says voice mode is unavailable

1. Confirm you are signed in through the browser; v0.1 does not permit bearer-token voice orchestration.
2. Confirm a normal text chat works with the current/default model.
3. Check `/api/voice/status` while authenticated. It should report bounded STT/TTS readiness without endpoint secrets.
4. Review `docker compose logs --tail=200 odysseus` for error categories. Do not paste unredacted logs into a public issue.

## Microphone permission fails

- Use `http://localhost` or HTTPS; browsers block media APIs in other insecure contexts.
- Allow microphone access for the exact origin in browser site settings.
- Close another application holding the device exclusively.
- End Voice and reopen it after changing permission.

The camera is intentionally unused in v0.1.

## There is text but no speech

Enable and test a TTS provider in Settings. Browser TTS depends on browser/OS voices. Local Kokoro requires its optional dependencies and a compatible GPU. Endpoint TTS must support the OpenAI-compatible `/audio/speech` contract.

## There is no transcript

Enable STT in Settings. Browser STT is not supported equally by every browser. Local Whisper needs `faster-whisper`; endpoint STT must support `/audio/transcriptions`. The existing STT upload size limit still applies.

## A worker is configured but not ready

- Confirm the worker is explicitly enabled and reachable over the intended private path.
- Confirm the token file exists inside the Odysseus process/container and has restrictive permissions.
- Confirm the worker advertises the expected fixed ID and read-only capability.
- Hermes fails closed if it cannot prove enforced read-only operation.
- Do not fix connectivity by exposing an unauthenticated worker on a public or wildcard interface.

## Old UI remains after upgrade

Confirm the running source tag or image digest, then perform a normal reload. If the service worker still serves an older static cache, close other Odysseus tabs, unregister the old service worker in browser developer tools, and reload. Do not delete `data/` to clear a frontend cache.

## Architecture or container failure

The release gate covers Linux `amd64` and `arm64`. Inspect the manifest with `docker buildx imagetools inspect IMAGE@sha256:DIGEST`. Docker Desktop/WSL2 remain best-effort for the alpha.
