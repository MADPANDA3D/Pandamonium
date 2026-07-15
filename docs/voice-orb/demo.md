# Sanitized Voice Orb demo protocol

This is the reproducible, public-safe demo for `voice-orb-v0.3.0-beta.1`.
It uses neutral data and exercises only the four host seams proposed in the
[upstream evidence packet](upstream-host-seams.md). It does not expose worker
activity, credentials, Calendar contents, Tailnet topology, or private
deployment details.

## Immutable build

| Evidence | Exact value |
|---|---|
| Source tag | [`voice-orb-v0.3.0-beta.1`](https://github.com/MADPANDA3D/odysseus/releases/tag/voice-orb-v0.3.0-beta.1) |
| Source commit | `f726621efe9d313f6d49bc5eb3c6de4c32316a36` |
| Release workflow | [`29435057533`](https://github.com/MADPANDA3D/odysseus/actions/runs/29435057533) |
| OCI index | `ghcr.io/madpanda3d/odysseus@sha256:816f68c9b5cc4d093abd4be6e015822280d6d269f9ea8821c3c33ce444991017` |

## Clean demo setup

1. Start the immutable image or source tag with authentication enabled.
2. Sign in interactively and use a neutral linked chat.
3. Configure a normal model, STT, TTS, and optional Vision model through
   existing Odysseus settings. Do not speak or display credentials.
4. Use neutral Calendar fixtures if demonstrating the read-only Calendar
   facade. Do not display a real account or event.
5. Leave workers and Tailnet inspection disabled; neither is part of the
   upstream host-seam ask.

## Demonstration sequence

| Step | Exact action | Expected bounded result |
|---|---|---|
| Voice lifecycle | Open Voice Orb, complete one neutral turn, interrupt playback, then End Voice | Microphone/playback stop deterministically and the linked chat remains usable |
| Foreground registry | Say `Open Calendar.` | One enumerated `open_view/calendar` action; no selector or generic DOM channel |
| Client state | Say `What view is open?` | Reply is derived from the versioned logical-view snapshot, not page content |
| Document actions | Say `Minimize this document.`, then `Close this document.` | Only the allowlisted document actions run |
| Calendar facade | Ask one bounded neutral Calendar question | Owner-scoped read-only result includes freshness metadata; failure says freshness could not be confirmed |
| Camera lifecycle | Say `Open your eyes.`, `What do you see?`, then `Close your eyes.` | Permission is user initiated; one bounded frame is analyzed in memory; all tracks stop |
| Local media | Say `I need something motivational.`, then End Voice | Only the checksummed same-origin manifest ID plays and is stopped |

## Verified release results

- Local CPython 3.11 suite: 4,667 passed, 3 skipped.
- Release CPython 3.11 suite: 4,666 passed, 4 skipped, 8 warnings.
- Foreground, voice, media, and setup Node contracts passed.
- Five Chromium fake-device lifecycle cases passed.
- A physical Logitech C920 gate observed the expected device label, captured a
  bounded JPEG, ended every track, returned the controller to idle, and left no
  video-device handle open.
- The public delta reapplied cleanly to the current canonical upstream `dev`
  head, followed by compile, four Node contracts, and 25 focused Python tests.
- Public scrub, dependency audit, full-history secret scan, Compose validation,
  Docker build, blocking HIGH/CRITICAL Trivy scan, and native amd64/arm64
  publication passed.
- Anonymous GHCR tag, immutable-index, amd64, and arm64 GETs returned HTTP
  200; every registry-header digest matched the raw response body.

## Privacy boundary

The demo retains normal text chat history and configured-provider behavior.
It does not intentionally retain raw microphone audio or camera frames. Camera
input is one explicit JPEG/PNG frame in request memory; built-in media resolves
from an allowlisted same-origin manifest ID. Provider policies still apply to
data sent to a configured remote STT, TTS, model, or Vision endpoint.

The demo does not claim continuous recording, surveillance, face recognition,
Calendar mutation, arbitrary browser control, remote media loading, worker
execution, network discovery, or a stable external plugin ABI.

## Reproduce the public gates

```bash
git clone --branch voice-orb-v0.3.0-beta.1 --depth 1 https://github.com/MADPANDA3D/odysseus.git
cd odysseus
python -m pip install -r requirements.txt
npm ci
python -m pytest -q
npm run test:voice-orb
node tests/test_voice_orb_media.js
python scripts/voice_orb_public_scrub.py --self-test
python scripts/voice_orb_public_scrub.py
```

Browser tests use fake media devices in CI. Hardware permission and indicator
behavior must also be checked manually on the browser and camera being shipped.
