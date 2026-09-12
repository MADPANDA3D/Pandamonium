# JOS Protocol Packs

Versioned, agent-facing operating protocols. Each `*.pack.md` file carries a
small frontmatter manifest and a compact body that is mounted into the agent
system prompt for a turn.

The human-readable contracts live in `specs/jarvis-os-*.md`. Packs are the
rendered, budgeted form the model receives; `tests/test_protocol_registry.py`
guards that the core packs stay present and renderable.

## Manifest keys

| Key | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable protocol id; must match the filename (`<id>.pack.md`) |
| `version` | yes | Protocol version rendered to the model |
| `scope` | yes | `core` (every turn), `duty` (mounted by intent), `extension` |
| `title` | yes | Human title rendered with the id and version |
| `token_budget` | yes | Approximate ceiling for the rendered body |
| `domains` | no | Intent domains that select a duty pack |
| `enforcement` | no | Code owners that enforce the pack's rules |

## Rules

- The constitution in Settings stays the light, operator-editable system
  prompt. Packs are a separate layer and never edit identity.
- Packs are public-safe: no credentials, private names, endpoints, or paths.
- A malformed pack fails visible in protocol diagnostics; it is never silently
  dropped.
- `protocol_layer_enabled=false` restores the exact prior prompt composition.
