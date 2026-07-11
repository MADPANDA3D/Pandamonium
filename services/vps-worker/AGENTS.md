# VPS Operations Workspace

This workspace is the fixed forward operating base for VPS Codex.

- Operate read-only unless Odysseus supplies a scoped approval.
- Never use arbitrary directories, sudo, Docker socket access, or privilege escalation.
- Use `jarvis-vps-observe` for live facts.
- Available observer actions: `health`, `resources`, `ports`, `services`, `journal`, `containers`, `nginx`, and `deployments`.
- `services` and `journal` accept only the observer's server-owned service allowlist.
- Report what the observer actually returned. Do not infer that an operation succeeded.
- File artifacts for Odysseus must remain inside this workspace.
