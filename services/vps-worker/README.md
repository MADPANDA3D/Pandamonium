# VPS Codex Forward Operating Base

Mark 6 runs VPS Codex as the unprivileged `jarvis-worker` account. The bridge binds only to the VPS Tailscale address on port `8650` and uses a separate bearer token. It has no sudo or Docker group membership.

Live privileged facts are supplied through a root-owned Unix socket with a fixed read-only action allowlist. Mutating operations are intentionally absent from this slice.

The Codex worker service must remain disabled until `jarvis-worker` completes its own Codex login and the read-only acceptance suite passes.

Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor. Install a pinned Codex CLI under `jarvis-worker`'s home and load `apparmor.jarvis-vps-codex-bwrap`; this grants `userns` only to that dedicated Codex package's sandbox helper rather than relaxing the host-wide sysctl.

`jarvis_vps_observer_mcp.py` is the Codex-facing observer surface. It exposes eight annotated read-only tools over stdio and is the only path from sandboxed Codex turns to the root-owned observer socket. Register it as the `jarvis_vps_observer` stdio MCP server in the dedicated worker's Codex config.
