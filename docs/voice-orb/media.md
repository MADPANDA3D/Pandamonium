# Camera and allowlisted media

v0.2 adds browser-owned camera control and one local demonstration clip. It does not add an image-upload endpoint, a generic media player, or continuous camera access.

## Exact commands

| Phrase | Result |
|---|---|
| `Open your eyes.` | Requests camera permission and shows live video under the transparent orb |
| `What do you see?` | Captures and describes one current frame |
| `Describe what you see.` | Captures and describes one current frame |
| `Close your eyes.` | Stops every camera track and clears the video surface |
| `I need something motivational.` | Plays the allowlisted `motivational-abstract` silent loop |

Commands are exact and single-purpose. Say `Open your eyes.` first, then ask for a description. Compound requests such as “Open your eyes and describe what you see” are intentionally unsupported in this slice.

## Camera lifecycle

The browser requests ideal 1024 by 576 video at 30 FPS with `audio:false`, then retries once with generic video after an overconstraint error. A native `<video>` sits below the transparent orb canvas. Camera and clip playback cannot be active together.

All tracks stop on close-eyes, End Voice, voice error, permission loss, track-ended, `pagehide`, hidden-page transition, or mode switch. A generation token invalidates pending permission requests so a late browser response cannot reopen stopped media.

## Single-frame analysis

A describe command may add one JPEG or PNG frame to the existing voice response request. The server enforces valid base64, matching MIME and magic bytes, dimensions no greater than 1024 by 576, and approximately 1 MiB decoded. There is no frame endpoint.

The frame is analyzed in memory. Voice Orb tries the active model when vision-capable, then the configured Vision model and fallback chain. It persists only the spoken question, description, and selected-model metadata—never raw frame bytes, image files, cached frames, or image diagnostics.

## Media provenance

Playback accepts manifest IDs only. `/static/voice-orb-media.json` contains the ID, title, MIME type, same-origin path, tags, license, source, attribution, availability, and SHA-256 checksum. The release scrub rejects unknown fields, paths, MIME types, licenses, checksums, undeclared files, audio bundles, and private frame artifacts under the Voice Orb media directory.

v0.2 ships one original silent abstract WebM generated from FFmpeg gradients and dedicated under CC0 1.0. Its reproduction command is in `/static/media/voice-orb/README.md`. No downloaded footage, copyrighted motivational media, cloned actor/JARVIS voice, ElevenLabs sample, or arbitrary URL is bundled. Neutral cues are synthesized by the first-party Web Audio source distributed under AGPL-3.0-or-later with the fork; no cue audio bundle is redistributed. The service worker does not precache the WebM.
