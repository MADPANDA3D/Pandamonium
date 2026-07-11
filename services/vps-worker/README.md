# VPS Codex Forward Operating Base

Mark 6 runs VPS Codex as the unprivileged `jarvis-worker` account. The bridge binds only to the VPS Tailscale address on port `8650` and uses a separate bearer token. It has no sudo or Docker group membership.

Live privileged facts are supplied through a root-owned Unix socket with a fixed read-only action allowlist. Mutating operations are intentionally absent from this slice.

The Codex worker service must remain disabled until `jarvis-worker` completes its own Codex login and the read-only acceptance suite passes.
