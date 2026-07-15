# Odysseus Voice Orb

Odysseus Voice Orb is an alpha voice and orchestration surface maintained in the MADPANDA3D fork of Odysseus. It keeps normal Odysseus conversation available without workers, while optionally exposing fixed, read-only worker adapters.

This release is a maintained fork, not an installable plugin. Odysseus does not currently provide a stable application-plugin ABI capable of safely hosting the feature. The fork will follow upstream extension work and can be extracted only after a real host contract exists.

## v0.1-alpha scope

- First-party Canvas, CSS, and Web Audio orb with no remote rendering bundle.
- Microphone capture, configured STT and TTS providers, interruption, and conversation through the current/default Odysseus model.
- Exact foreground commands: `open Calendar`, `close this document`, `minimize this document`, and `what view is open?`.
- Optional fixed worker adapters: `pc-codex`, `hermes`, and disabled-by-default `vps-codex`.
- Read-only worker execution, attributed progress, cancellation, and reload reconstruction.

The alpha does not include camera input, arbitrary DOM control, arbitrary worker-module loading, workspace mutation, automatic Tailnet discovery, or an installer that patches an existing Odysseus checkout.

## Documentation

- [Install](install.md)
- [Model, STT, and TTS providers](providers.md)
- [Workers](workers.md)
- [Security](security.md)
- [Privacy and data lifecycle](privacy.md)
- [Troubleshooting](troubleshooting.md)
- [Release and container process](release.md)
- [Contributing](contributing.md)
- [Compatibility record](compatibility.json)

## License and public boundary

The fork is distributed under AGPL-3.0-or-later, matching upstream. Private Mark Notes are documentation outside this repository; credentials, private topology, personal or business data, and unlicensed media are excluded for security and legal reasons.
