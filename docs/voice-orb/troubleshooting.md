# Troubleshooting

## The Orb says voice mode is unavailable

1. Confirm you are signed in through the browser; Voice Orb does not permit bearer-token voice orchestration.
2. Confirm a normal text chat works with the current/default model.
3. Check `/api/voice/status` while authenticated. It should report bounded STT/TTS readiness without endpoint secrets.
4. Review `docker compose logs --tail=200 odysseus` for error categories. Do not paste unredacted logs into a public issue.

## Microphone permission fails

- Use `http://localhost` or HTTPS; browsers block media APIs in other insecure contexts.
- Allow microphone access for the exact origin in browser site settings.
- Close another application holding the device exclusively.
- End Voice and reopen it after changing permission.

## Camera permission fails or stays pending

- Use `http://localhost` or HTTPS and allow camera access for the exact origin.
- Say the single-purpose command `Open your eyes.`; compound open-and-describe phrases are intentionally unsupported in v0.2.
- If the ideal 1024 by 576 request is overconstrained, Voice Orb retries once with generic video constraints.
- End Voice, hide the page, or say `Close your eyes.` before retrying. A stopped pending request cannot reopen the camera later.
- If the browser reports permission loss or the camera track ends, Voice Orb closes the camera automatically.

If the camera indicator or hardware LED remains active after any stop path, close the tab, revoke site permission, and report the browser/OS and reproduction steps without attaching captured imagery.

## The Orb cannot describe what it sees

Open the camera first, wait for a visible live frame, then use exactly `What do you see?` or `Describe what you see.` Configure and test a Vision model in Settings. The active conversation model is tried first only when vision-capable, followed by the configured Vision model and fallback chain. Failure does not persist or reroute the frame elsewhere.

## The motivational visual does not play

Use exactly `I need something motivational.` Voice Orb plays the local `motivational-abstract` manifest ID, not a URL. Confirm `/static/voice-orb-media.json` and its same-origin WebM return successfully. The bundled demonstration is silent by design; spoken feedback still comes from the configured TTS provider. A checksum or manifest mismatch is a release defect, not a reason to bypass the allowlist.

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
