# JOS Generic Web Runtime Host

**Linear:** `MAD-754`, `MAD-762`

Pandamonium now has one reference-neutral adapter for externally managed web
runtimes that publish a live JOS capability catalog. ORACLE uses this contract;
its native tool inventory is not copied into Pandamonium.

## Runtime discovery

`ODYSSEUS_EXTENSION_URLS` is a JSON object keyed by manifest extension ID. A
deployment can therefore bind any installed extension to a trusted HTTPS or
loopback HTTP runtime without putting private hosts, ports, or operator values
in public source.

For a `web` plus `live_catalog` manifest with empty lifecycle vectors, the host:

1. validates the pinned checkout and regular-file web entry point;
2. resolves the manifest's catalog path against the configured runtime URL;
3. requires the catalog to stay on that exact origin;
4. performs a redirect-free, bounded, timed JSON fetch;
5. verifies that the native protocol ID equals the manifest extension ID;
6. reconciles the returned schemas with manifest permissions and the pinned
   revision before registry activation.

Catalog failure, timeout, malformed schemas, permission drift, revision drift,
or capability-name conflict leaves the extension unavailable. If activation
succeeds but registry admission fails, the adapter is deactivated again.

## Agent and client mount

Voice sessions persist `engaged_extensions` by extension ID. The model sees
only the intersection of:

- an enabled capability in the durable extension registry;
- an extension ID engaged in that session; and
- a matching tool name currently advertised by the connected native client.

The registry schema and permission mode remain authoritative. Client state can
prove current availability but cannot add tools or relax policy. Mounted tools
enter the existing P2 context, P4 action, P5 authority, P6 learning, and P7
operational paths with the configured agent ID and actual extension ID.

Browser results return through
`/api/voice/sessions/{session_id}/extensions/{extension_id}/results` and must
match the authenticated owner, extension ID, call ID, and tool name. Unknown
tools, non-object arguments, oversized/mismatched results, timeouts, disabled
extensions, and missing catalogs fail closed. Multi-action turns use the same
bounded agent loop as native Pandamonium tools.

For enabled `web` manifests, the same configured runtime origin resolves the
installed manifest entry point. The browser mounts at most one engaged surface,
accepts messages only from that frame and exact origin, and forwards correlated
results through the route above. Frame loss, config removal, disengagement,
disable, and uninstall clear the surface and its pending client calls. Host
restart rebuilds the available surface list from the durable registry rather
than process-local activation state.

The surface iframe explicitly denies camera, microphone, and display-capture
permission. Extension engagement does not call a media API or imply media
approval; browser media remains a separate explicit browser-scoped action.

## ORACLE compatibility boundary

The ORACLE runner remains supported by a compatibility normalizer inside the
generic surface. It receives its legacy message names while actions and results
continue through the generic control/result paths. ORACLE-specific lifecycle
phrases and the legacy unregistered-catalog fallback remain until a real
installed revision and live deployment separately prove equivalent; they are
not treated as the public extension framework.

## Evidence boundary

Automated tests install a pinned ORACLE-shaped checkout from a temporary local
Git repository, resolve its live catalog through an injected runtime transport,
and prove enable/disable plus catalog removal. A separate acceptance run used
the actual local ORACLE commit `b619e2a`, its 28 native tool schemas, and a
temporary managed root; all 28 tools registered and disabling ORACLE removed
all 28. A differently named extension proves that registry permissions,
session context, and dispatch are keyed by extension ID rather than ORACLE.

The differently named `atlas` browser fixture additionally proves manifest
entry-point mounting, exact frame/origin/extension/tool/call correlation,
single and multi-action results, malformed and oversized result handling,
unavailable frames, timeout, disable, uninstall, and restart recovery. The
MAD-762 source gate passed the browser harness and the complete Pandamonium suite:
5,005 passed, 4 intentional skips, and zero failures.

This is source-level host proof only. ORACLE commit `b619e2a` exists locally;
it has not been pushed, tagged, installed into the persistent managed root, or
deployed to CT103/CT104. ORACLE source and deployed equivalence remain separate
operator-selected gates.
