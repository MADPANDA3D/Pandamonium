# Odysseus Voice Orb

Odysseus Voice Orb is an alpha voice, media, and orchestration surface maintained in the MADPANDA3D fork of Odysseus. It keeps normal Odysseus conversation available without workers, while optionally exposing fixed, read-only worker adapters.

This release is a maintained fork, not an installable plugin. Odysseus does not currently provide a stable application-plugin ABI capable of safely hosting the feature. The fork will follow upstream extension work and can be extracted only after a real host contract exists.

## Releases

| Version | Source tag | Additive scope |
|---|---|---|
| v0.1-alpha.1 | `voice-orb-v0.1.0-alpha.1` | Immutable source release; its container was blocked by the security gate |
| v0.1-alpha.2 | `voice-orb-v0.1.0-alpha.2` | Camera-free Voice Orb with the hardened container remediation |
| v0.2-alpha.1 | `voice-orb-v0.2.0-alpha.1` | User-initiated camera frames and allowlisted local media |

v0.2 keeps the v0.1 contracts and adds the camera/media boundary described below. These remain maintained-fork releases, not plugins.

## v0.2-alpha scope

- First-party Canvas, CSS, and Web Audio orb with no remote rendering bundle.
- Microphone capture, configured STT and TTS providers, interruption, and conversation through the current/default Odysseus model.
- Exact foreground commands: `open Calendar`, `close this document`, `minimize this document`, and `what view is open?`.
- Optional fixed worker adapters: `pc-codex`, `hermes`, and disabled-by-default `vps-codex`.
- Read-only worker execution, attributed progress, cancellation, and reload reconstruction.
- Exact camera commands: `Open your eyes.`, `What do you see?`, `Describe what you see.`, and `Close your eyes.`.
- Exact local-media command: `I need something motivational.`
- One bounded JPEG or PNG frame, captured only for an explicit describe command and never persisted.
- One checksummed, same-origin, CC0 silent abstract demonstration loop selected by manifest ID.

The first camera slice does not interpret compound commands such as “Open your eyes and describe what you see.” The alpha does not include continuous recording, surveillance, face recognition, arbitrary media URLs, arbitrary DOM control, arbitrary worker-module loading, workspace mutation, automatic Tailnet discovery, or an installer that patches an existing Odysseus checkout.

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
- [Compatibility record](compatibility.json)

## License and public boundary

The fork is distributed under AGPL-3.0-or-later, matching upstream. Private Mark Notes are documentation outside this repository; credentials, private topology, personal or business data, and unlicensed media are excluded for security and legal reasons.
