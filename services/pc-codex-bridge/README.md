# PC Codex Worker Bridge

This private bridge adapts Codex App Server tasks to the Odysseus worker protocol. It binds to localhost by default, accepts only server-mapped workspace aliases, persists Codex thread IDs, and emits replayable normalized events.

Callers may provide a `thread_title`; the bridge applies it through Codex's native thread naming API so persistent sessions remain visible and consistently named in Codex Desktop.

The runtime bundle is exactly two Python files: copy
`jarvis_codex_bridge.py` and `core/atomic_io.py` into the same install
directory (the latter as `atomic_io.py`). The bridge intentionally remains
usable without importing the rest of the Odysseus application.

PC Codex sessions run directly from the selected allowlisted workspace, so
their persistent App Server threads appear under the correct Codex project.
Public installs expose no workspace or task data until this allowlist is
configured. Worker delegation is strictly read-only; write-capable tasks require the
private worker profile and explicit preapproval. Configure local paths and
optional model overrides in the private service environment:

```env
JARVIS_CODEX_WORKSPACES_JSON='{"project":{"path":"/absolute/path/to/project","display_name":"Project"}}'
JARVIS_CODEX_WORKER_LABEL=Developer
JARVIS_CODEX_MODEL=your-model-id
JARVIS_CODEX_REASONING_EFFORT=high
```

The health contract reports that installation-assigned label plus its Codex
capability. Pandamonium uses the label as owner-facing data and exposes the
installation only while this explicitly configured bridge is reachable.

Pandamonium's project browser defaults every request to read-only. A reversible
workspace edit requires the authenticated operator to select the explicit edit
checkbox and both private mutation switches to be enabled. The selected project
alias is resolved independently by Pandamonium and this bridge; callers cannot
supply a filesystem root.

The PC bridge defaults to `gpt-5.6-terra` with `high` reasoning. The shared VPS bridge keeps its existing App Server defaults unless those variables are explicitly set.

The authenticated catalog routes use Codex App Server's supported `thread/list`
API with an exact allowlisted `cwd` filter. Project-list responses identify approved
roots by logical `workspace:<alias>` references. Authenticated task Details and history
responses include the selected workspace and recorded attachment/output paths after
verifying exact project membership. They never expose bridge credentials or raw
reasoning/tool argument payloads.

## Native conversation history (v1.0.39+)

Update this bridge alongside the Pandamonium app. The authenticated
`GET /v1/catalog/projects/{workspace}/tasks/{thread_id}/history` endpoint uses
App Server `thread/turns/list`, accepts its opaque cursor, and returns five recent
turns by default (maximum ten) in chronological display order. It includes visible
user/assistant messages and recorded activity, with a cursor for earlier turns.
Turn IDs, duration and visible message phases keep commentary grouped separately
from final answers; private reasoning and raw tool arguments are never returned.

Pandamonium preserves the selected native task and model/effort across reload and
resumes that task when sending. Browsing does not start a model turn or replay tools.
Recorded local images are returned as bounded image data after task/project
verification and display as previews. Unavailable files retain an explicit label.
Keep the existing private token, Codex home, project map and service environment
when replacing the two-file bridge bundle. Restart the bridge only when its worker
tasks are idle; keep the previous bundle for rollback. Older bridges continue to
serve the existing catalog and task controls but cannot load native history.

See [Agent gateway](../../docs/agent-gateway.md) for additive MCP configuration,
bidirectional image delivery, native configuration preservation and limits.

## Desktop-owned continuation

For a task already owned by the local Codex Desktop, the bridge uses the desktop's
versioned owner/follower IPC in `$CODEX_HOME/ipc/ipc.sock` (default `~/.codex`).
The socket and peer must belong to the service user, and the directory/socket must
not be writable by other users. Exact allowlisted project membership is checked
before sending. No writer lock is removed and no duplicate thread is created.

Idle tasks receive a new turn with the requested sandbox, model and effort. A
running task receives steering under its existing native settings; model/effort
choices apply to the next turn. The bridge observes sanitized App Server history
and waits for the native completion timestamp, retaining the last answer when
steering produces multiple answers in one turn. Cancellation targets the exact
native turn. An observation timeout does not cancel the desktop's task.

This IPC is an internal desktop compatibility surface, with explicit protocol
versions. An incompatible or disconnected owner fails visibly without automatic
message replay. Check the task in Codex before resending after an uncertain
transport outcome. When there is no desktop owner, the existing App Server
execution path is used. Update both app and bridge; preserve the private service
environment and keep the previous two-file bundle for rollback.

`JARVIS_CODEX_BRIDGE_HOSTS` may contain a comma-separated list of explicit bind addresses for a loopback plus tailnet-only transition. Wildcard binds are rejected. Keep interface addresses in private machine configuration, not reusable source.

Supported artifact markers are limited to text documents inside the selected workspace:

```text
[[ODYSSEUS_ARTIFACT path="relative/path.md" title="Document title"]]
```

Odysseus validates and persists the artifact as a linked Document. The bridge never accepts arbitrary working directories from the model or caller.

## M7 rollback

Set `PANDAMONIUM_CODEX_TASK_EXECUTION_ENABLED=false` in Pandamonium and
`JARVIS_CODEX_EXECUTION_ENABLED=false` on the bridge, then restart only through
the normal operator deployment procedure. Catalog reads remain available while
task create/resume is disabled. If an approved disposable smoke created review
documents, remove only the document IDs cited by that smoke's task audit; do not
remove user projects, Codex threads, task history, or unrelated documents.
