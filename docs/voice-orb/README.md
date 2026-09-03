# Pandamonium Voice Orb

Pandamonium Voice Orb is the in-tree voice, media, and orchestration surface. It
keeps normal Pandamonium conversation available without workers, while optionally
exposing fixed, read-only worker adapters.

Voice Orb is not an independently installable plugin. It can be extracted only
after a stable host contract provides the required trust, permission, update, and
compatibility boundaries.

## Releases

| Version | Source tag | Additive scope |
|---|---|---|
| v0.1-alpha.1 | `voice-orb-v0.1.0-alpha.1` | Immutable source release; its container was blocked by the security gate |
| v0.1-alpha.2 | `voice-orb-v0.1.0-alpha.2` | Camera-free Voice Orb with the hardened container remediation |
| v0.2-alpha.1 | `voice-orb-v0.2.0-alpha.1` | User-initiated camera frames and allowlisted local media |
| v0.3.0-beta.1 | `voice-orb-v0.3.0-beta.1` | Guided setup parity and explicit admin-only Tailnet model discovery |

The historical v0.3 release kept the v0.2 camera/media contracts and added the
bounded setup and discovery behavior described below. These tags remain available
for provenance and rollback.

## v0.3.0-beta.1 scope

- First-party Canvas, CSS, and Web Audio orb with no remote rendering bundle.
- Microphone capture, configured STT and TTS providers, interruption, and conversation through the current/default Pandamonium model.
- Exact foreground commands: `open Calendar`, `close this document`, `minimize this document`, and `what view is open?`.
- Optional fixed worker adapters: `pc-codex`, `hermes`, and disabled-by-default `vps-codex`.
- Read-only worker execution, attributed progress, cancellation, and reload reconstruction.
- Exact camera commands: `Open your eyes.`, `What do you see?`, `Describe what you see.`, and `Close your eyes.`.
- Exact local-media command: `I need something motivational.`
- One bounded JPEG or PNG frame, captured only for an explicit describe command and never persisted.
- One checksummed, same-origin, CC0 silent abstract demonstration loop selected by manifest ID.
- A versioned authenticated setup summary for model, STT, TTS, and fixed-worker readiness.
- Exact `Check voice setup.` status/text parity: spoken guidance and the structured response come from the same server snapshot.
- Credentials stay in Settings or mounted token files; setup guidance never speaks credential values or token paths.
- Admin-only Tailnet peer listing returns opaque IDs without probing, followed only by an explicit selected-peer model probe of at most five listed peers.
- Fixed-worker readiness counts only explicitly configured adapters whose bounded health check is ready; Tailnet visibility never implies a healthy worker cluster.

The camera slice does not interpret compound commands such as “Open your eyes and describe what you see.” Voice Orb does not include continuous recording, surveillance, face recognition, arbitrary media URLs, arbitrary DOM control, arbitrary worker-module loading, workspace mutation, automatic or blind Tailnet discovery, agent discovery, or a source-rewriting installer. It never runs `tailscale up`, changes ACLs or Funnel, enrolls devices, or widens bind addresses.

## Documentation

- [Install](install.md)
- [Model, STT, and TTS providers](providers.md)
- [Camera and allowlisted media](media.md)
- [Workers](workers.md)
- [Security](security.md)
- [Privacy and data lifecycle](privacy.md)
- [Troubleshooting](troubleshooting.md)
- [Release and container process](release.md)
- [Contributing](contributing.md)
- [Sanitized demo protocol and release evidence](demo.md)
- [Upstream host-seams evidence packet](upstream-host-seams.md)
- [Compatibility record](compatibility.json)

## License and public boundary

Pandamonium is distributed under AGPL-3.0-or-later, matching upstream. Private
Mark Notes are documentation outside this repository; credentials, private
topology, personal or business data, and unlicensed media are excluded for
security and legal reasons.
