# PC Codex Worker Bridge

This private bridge adapts Codex App Server tasks to the Odysseus worker protocol. It binds to localhost by default, accepts only server-mapped workspace aliases, persists Codex thread IDs, and emits replayable normalized events.

Callers may provide a `thread_title`; the bridge applies it through Codex's native thread naming API so persistent sessions remain visible and consistently named in Codex Desktop.

The runtime bundle is exactly two Python files: copy
`jarvis_codex_bridge.py` and `core/atomic_io.py` into the same install
directory (the latter as `atomic_io.py`). The bridge intentionally remains
usable without importing the rest of the Odysseus application.

PC Codex sessions run from one dedicated interaction workspace instead of the selected source project, so their persistent App Server threads stay grouped away from normal Codex work. The logical workspace selects the source tree Codex may read. Public worker delegation is strictly read-only; write roots and preapproved tasks are rejected. Configure local paths and optional model overrides in the private service environment:

```env
JARVIS_CODEX_WORKSPACES_JSON='{"project":"/absolute/path/to/project"}'
JARVIS_CODEX_INTERACTION_WORKSPACE='/absolute/path/to/worker-interactions'
JARVIS_CODEX_MODEL=your-model-id
JARVIS_CODEX_REASONING_EFFORT=high
```

The PC bridge defaults to `gpt-5.6-terra` with `high` reasoning. The shared VPS bridge keeps its existing App Server defaults unless those variables are explicitly set.

`JARVIS_CODEX_BRIDGE_HOSTS` may contain a comma-separated list of explicit bind addresses for a loopback plus tailnet-only transition. Wildcard binds are rejected. Keep interface addresses in private machine configuration, not reusable source.

Supported artifact markers are limited to text documents inside the selected workspace:

```text
[[ODYSSEUS_ARTIFACT path="relative/path.md" title="Document title"]]
```

Odysseus validates and persists the artifact as a linked Document. The bridge never accepts arbitrary working directories from the model or caller.
