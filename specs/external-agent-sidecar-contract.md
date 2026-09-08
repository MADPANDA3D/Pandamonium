# External coding-agent sidecar contract

**Protocol:** `pandamonium.external-agent-sidecar.v1`

**Status:** normative contract implemented by MAD-844 and MAD-845

**Schema:**
[`pandamonium-external-agent-sidecar-v1.schema.json`](schemas/pandamonium-external-agent-sidecar-v1.schema.json)

## Boundary

This contract lets a separately operated coding-agent sidecar become a
Pandamonium Connection and Worker without becoming an authority, credential
store, Workspace registry, transcript store, installer, or UI. It reuses the
Model/Agent/Worker/Workspace/Connection taxonomy and action effects from
[`pandamonium-capability-authorization-contract.md`](pandamonium-capability-authorization-contract.md).

The sidecar is optional and disabled by default. A clean installation has no
sidecar endpoint, auth reference, capability, network request, filesystem scan,
or Workspace access. The operator must explicitly configure every endpoint,
server-side auth reference, allowed Workspace alias, and capability. Runtime
discovery can narrow that configuration; it cannot add to it.

An unconfigured sidecar emits no discovery entity and triggers no probe. The
baseline profile is read-only; task actions are individually opt-in with an
installation-declared canonical effect and are never implied by protocol
compatibility.

No provider name is part of the protocol. Provider and installation labels are
optional presentation metadata on canonical Agent, Worker, or Connection
records, never routing, ownership, or authorization inputs.

## Canonical entity projection

The existing discovery envelope remains authoritative. A configured sidecar is
projected without creating new entity kinds:

| Entity | Projection |
| --- | --- |
| Connection | Operator-configured transport plus an opaque server-side auth reference; discovery reports only whether auth is configured. |
| Worker | Health and effective capabilities after intersecting configuration, live sidecar claims, installation policy, and owner scope. |
| Agent | Optional installation-owned identity hosted by the Worker; changing the provider does not change authority. |
| Workspace | Explicit installation-owned alias. The resolved host path never crosses the protocol or enters discovery. |
| Tool | One exact capability schema and canonical action effect owned by the configured Worker. |

Effective capability is always the intersection below. A model, sidecar,
event, provider, or delegated prompt cannot widen any term.

```text
configured capability
  intersect live compatible capability
  intersect installation policy
  intersect authenticated owner scope
  intersect current Workspace alias
```

### Repository evidence

This design extends existing enforcement seams instead of treating the sidecar
as an isolated trusted service:

| Established fact | Source evidence |
| --- | --- |
| The canonical taxonomy separates Worker, Workspace, Connection, Agent, and Tool ownership. | `specs/pandamonium-capability-authorization-contract.md:53-68` |
| Discovery already requires ownership, health, authenticated scope, delegation, source, and effect-classified actions. | `specs/schemas/pandamonium-discovery-v1.schema.json:18-44`, `specs/schemas/pandamonium-discovery-v1.schema.json:65-130` |
| Worker Workspace configuration is installation-owned, alias-only, bounded, and empty by default. | `src/agent_worker_adapters.py:45-65` |
| Existing bridges resolve bearer material server-side and use bounded HTTP calls, while remote events arrive as untrusted JSON. | `src/agent_worker_adapters.py:203-238`, `src/agent_worker_adapters.py:279-316` |
| The primary authenticated task path binds persisted tasks and reusable worker bindings to owner/session/Worker/Workspace. | `src/jarvis_agent.py:95-186`, `src/jarvis_agent.py:221-303` |
| A separate voice-worker broker applies the same owner/session checks and filters its public task fields. | `src/agent_worker_broker.py:88-164`, `src/agent_worker_broker.py:109-139` |
| Canonical authority owns the eight effects, six separate gates, recursive secret redaction, and resolved Workspace containment checks. | `src/authority_protocol.py:42-68`, `src/authority_protocol.py:195-230`, `src/authority_protocol.py:263-287` |
| JOS-EXT-1 already requires exact origin/window/call correlation, bounded results, teardown, and capability narrowing for browser surfaces. | `specs/jarvis-os-extension-protocol.md:196-223` |
| Network deployments require authentication and HTTPS/private access, and privileged tools and secret-bearing state remain protected. | `SECURITY.md:3-23`, `SECURITY.md:26-36` |

These citations describe the controls and owners reused by the independently
implemented adapter. MAD-844 supplies bounded transport and authenticated read
discovery; MAD-845 maps opaque sidecar task references into the existing
canonical task broker and authority records. Host-path actions remain outside
the action schemas, so this implementation adds no sidecar file-opening seam.

## Connection and transport

1. The endpoint is installation configuration, never accepted from a model,
   request argument, sidecar response, redirect, discovery record, or event.
2. Loopback HTTP is allowed only when explicitly configured. Remote endpoints
   require HTTPS with normal certificate and hostname verification. Private,
   link-local, loopback, Unix-socket, or public destinations are each explicit
   installation-policy choices; no address class is inferred or scanned.
3. Redirects are disabled. If a future transport enables them, every hop must
   be revalidated and remain on the exact configured origin.
4. Resolution and connection must bind the same validated address set to resist
   DNS rebinding. URL userinfo, query credentials, fragments, ambiguous ports,
   and non-HTTP schemes are rejected.
5. `auth_ref` is an opaque key into the existing server-side credential store.
   Pandamonium resolves it only at dispatch and places the credential in the
   transport authorization header. The reference and secret are absent from
   wire envelopes, discovery, logs, events, approval previews, errors, and
   browser payloads.
6. `/health` may be unauthenticated only when it returns the schema's bounded
   liveness envelope. Capability discovery and every owner or task operation
   require the configured transport authentication.

## Wire envelopes

Every JSON message validates against the normative schema and carries
`protocol_version`, `envelope`, `message_id`, `request_id`, and `issued_at`.
Authenticated work also binds `owner_ref`, `connection_id`, `worker_ref`, an
optional canonical `agent_ref`, and the exact `workspace_alias`. A
sidecar-created task receives an opaque, provider-neutral
`task_ref`; provider-native process, session, thread, or SDK identifiers remain
inside the sidecar and never become Pandamonium identity or authority inputs.

| Envelope | Required behavior |
| --- | --- |
| `request` | Binds one configured capability, canonical effect, exact bounded arguments, nonce, issue time, and expiry. |
| `response` | Correlates to one request, may return its opaque `task_ref`, and reports only accepted or succeeded bounded data. It never proves authorization by itself. |
| `event` | Adds a unique event ID and non-negative sequence to a request-bound, task-bound, owner-bound, Workspace-bound stream. |
| `health` | Reports only sidecar ID/version, protocol compatibility, bounded status, and check time. |
| `capabilities` | Reports bounded action declarations. Declarations can only narrow the configured catalog and effect policy. |
| `error` | Returns a stable code and bounded safe detail; it never returns raw exceptions, endpoints, paths, auth data, or another owner's identifiers. |
| `cancel` | Targets one request and its opaque task reference, then stops future work. Cancellation is not deletion and does not erase history or artifacts. |

Pandamonium sends health to `GET /health`, capability and catalog/event reads
to `POST /read`, and task action or cancellation envelopes to `POST /actions`.
The governed action names are `task.start`, `task.steer`, `task.reply`,
`task.cancel`, and read-effect `task.status.read`. A configured action is usable
only when the current sidecar declaration agrees on its authorization class;
the stricter configured/live/canonical effect wins.
Callers may supply a bounded canonical `request_id` on create, steer, reply, and
cancel. Retries preserve the derived sidecar request ID; changed arguments with
that identity fail as a replay instead of dispatching a second effect.

Before JSON parsing, either peer rejects a message larger than 65,536 encoded
bytes. A bounded parser then rejects input deeper than 16 containers before
schema or business processing. Strings, arrays, objects, events, and capability
lists retain the smaller schema limits. Binary or large artifact content is out
of band and outside v1.

### Request validation order

Pandamonium and conformant sidecars fail closed in this order:

1. byte and nesting limits;
2. JSON and schema validity, including exact protocol version;
3. bounded clock skew, unexpired request, and single-use nonce/request ID;
4. transport authentication and exact Connection binding;
5. authenticated owner and current session binding;
6. configured Workspace alias and exact current Worker/Agent binding;
7. effective capability and canonical effect intersection;
8. argument schema, relative-path containment, and resolved symlink containment;
9. rate/concurrency budget; then dispatch exactly once.

A retry reuses the request ID and receives the recorded terminal result or an
explicit in-progress/replay result; it never dispatches a duplicate action.
Unknown fields, versions, capabilities, effects, event types, or error codes
fail closed.

## Workspace and path contract

- Only an explicit Workspace alias crosses the boundary. No raw host path,
  editor home, project-tree root, transcript location, SSH identity, container
  mount, or user-home path is returned or accepted.
- A capability that later needs a file reference uses a Workspace-relative
  logical path declared by that capability's exact input schema. Absolute
  paths, traversal, alternate separators that escape the root, device paths,
  and URI-shaped paths are rejected before access.
- The Worker resolves the configured root, resolves every existing parent and
  final symlink, and proves containment immediately before use. It opens the
  resolved target without following a changed symlink where the platform
  supports that guarantee.
- No endpoint performs default scans of editor homes, Workspace trees,
  transcripts, recent projects, drives, mounts, or host files. Catalog and
  transcript reads are explicit, owner-scoped, Workspace-scoped, paginated,
  time-bounded, and size-bounded capabilities.
- Logs, events, errors, discovery, and browser data use the Workspace alias and
  redacted logical references only.

## Event and cancellation rules

An event stream is untrusted sidecar data. Pandamonium accepts only the schema's
event types, monotonically increasing sequence values, unique event IDs, the
current request/task/owner/Connection/Worker/Agent/Workspace tuple, and the configured size and
rate budget. Replayed, skipped-backward, cross-request, terminal-after-terminal,
or post-cancellation events are rejected. Text and metadata are redacted and
bounded before persistence or browser delivery. Tool claims and a `result`
event cannot authorize work or independently prove that an effect occurred.

Cancellation is idempotent. It stops new work and produces one terminal state;
it does not delete task history, artifacts, credentials, configuration, or
Workspace content. Destructive cleanup requires a separate exact action and
gate.

## Canonical effect mapping

The concrete action and arguments determine the effective row. Sidecar-declared
effects may only make the result stricter. Mixed effects use the strictest row.

| Effect | Explicit authenticated request in configured scope | Separate gate |
| --- | --- | --- |
| `read` | Allowed | No |
| `reversible_write` | Allowed | No |
| `destructive_or_difficult_to_recover` | Insufficient | Yes |
| `external_publication_or_communication` | Insufficient | Yes |
| `purchase` | Insufficient | Yes |
| `credential_or_auth_change` | Insufficient | Yes |
| `privilege_expansion` | Insufficient | Yes |
| `outside_workspace_boundary` | Insufficient | Yes |

The six material effects remain separately gated with exact owner, Connection,
Workspace, target, capability, arguments, effect, expiry, and execution
binding. A changed argument, capability, target, Workspace, owner, endpoint,
or sidecar version invalidates the decision. Sidecar output never creates,
approves, broadens, persists, or replays an authority receipt. JOS-EXT-1 browser
surfaces must still yield to the canonical approval UI and correlate any result
to the exact pending call.

## Stable failures

The v1 error codes are `malformed_envelope`, `incompatible_protocol`,
`oversized_payload`, `stale_request`, `replay_detected`, `unauthorized`,
`wrong_owner`, `wrong_workspace`, `capability_disabled`, `path_escape`,
`symlink_escape`, `rate_limited`, `timeout`, `sidecar_unavailable`, and
`internal_error`. Public detail is a short operator-safe description. Raw
exceptions and upstream bodies remain server-side and redacted.

Unavailable health is explicit. Pandamonium does not silently route to another
Worker, wake a host, scan for a replacement, install software, or downgrade the
requested capability.

## Threat model

Assets include owner identity, Workspace content, task history, sidecar and
Pandamonium credentials, authority receipts, event/result integrity, service
availability, and release provenance. Trust boundaries are browser to
Pandamonium, Pandamonium policy to transport, transport to local or remote
sidecar, sidecar to its provider/runtime, and configured Workspace alias to the
resolved filesystem root.

### Attacker model, objectives, and assumptions

Realistic attackers control model/task text, sidecar responses/events, a
reachable network path for a configured remote endpoint, or an unprivileged
local process able to address a configured loopback listener. A compromised
optional sidecar or dependency is also in scope. They do not initially control
the authenticated operator, installation configuration, Pandamonium process,
Workspace root, credential store, protected canonical branch, or release key.

The objectives are to keep owner and Workspace isolation exact; keep credentials
server-only; prevent sidecar data from granting authority; preserve event,
result, approval, and cancellation integrity; constrain network destinations;
and keep one faulty or hostile sidecar from exhausting Pandamonium. Local-only
deployment reduces network reachability but does not make a loopback process
trusted. Remote exposure exists only after explicit endpoint configuration.
The localhost sidecar and remote sidecar cases therefore share the same
identity, scope, envelope, and fail-closed requirements.

The scenarios below are threat hypotheses, not confirmed vulnerabilities.
Implemented controls are stated directly; deployment hardening that remains
outside the backend adapter boundary is identified separately.

| ID / priority | Hypothesis, prerequisite, and capability gain | Impact | Current control and required mitigation |
| --- | --- | --- | --- |
| EAS-001 / High | With a configured localhost endpoint, another local process impersonates a sidecar and gains the configured Worker's data/action channel. | Owner task disclosure or unauthorized proposals inside the allowed Workspace. | Existing bridges require server-resolved bearer material (`src/agent_worker_adapters.py:203-207`). Require explicit opt-in, auth on non-health calls, exact Connection identity, least privilege, and no ambient discovery. |
| EAS-002 / High | With a configured remote endpoint or hostile network path, interception or endpoint compromise reads or changes owner work. | Confidentiality and task/result integrity loss. | Repository policy requires authenticated HTTPS/private access (`SECURITY.md:3-23`). Require certificate/hostname verification, exact origin, no redirects, bounded deadlines, and secret-free envelopes. |
| EAS-003 / High | A model, sidecar, redirect, DNS rebinding, or ambiguous URL steers transport to metadata, private, or unintended services. | New network reach and possible credential/service compromise. | The adapter admits only an operator-owned endpoint, validates scheme/userinfo/query/fragment and resolved address class, pins the selected address, and rejects redirects. |
| EAS-004 / High | An observer replays a request, nonce, authority result, cancellation, or terminal event before or after reconnect. | Duplicate effects, revived work, or falsified terminal state. | Authority decisions already expire and bind operator/session state (`src/authority_protocol.py:500-650`); broker events deduplicate IDs (`src/agent_worker_broker.py:176-229`). Add request expiry, single-use nonce, complete tuple binding, idempotent result caching, and monotonic terminal state. |
| EAS-005 / Critical | In a confused deputy attack, a hostile sidecar supplies another owner/Workspace/Connection identity or stronger capability/effect and Pandamonium accepts it as authority. | Cross-owner or cross-Workspace access and potentially privileged action execution. | Broker ownership/session checks and tuple bindings exist (`src/agent_worker_broker.py:88-164`); authority classification uses the strictest canonical effect (`src/authority_protocol.py:310-359`). Revalidate the complete tuple, intersect capabilities, and reject sidecar-supplied authority. |
| EAS-006 / High | A hostile stream forges, reorders, duplicates, oversizes, or emits post-terminal events that appear as trusted chat/voice results. | False operator state, unsafe follow-up decisions, storage/UI exhaustion. | Broker event types, IDs, terminal states, and text are bounded today (`src/agent_worker_broker.py:176-229`). Add remote sequence/request binding, metadata and byte/rate caps, redaction, and correlated result verification. |
| EAS-007 / High | A sidecar or upstream error places tokens, auth references, endpoints, paths, or prompts in discovery, logs, events, errors, or browser data. | Credential theft, private topology disclosure, or owner-data exposure. | Recursive canonical redaction and safe previews exist (`src/authority_protocol.py:195-230`), and worker health maps raw failures to stable reasons (`src/agent_worker_adapters.py:75-83`). Resolve auth server-side, reject secret-bearing fields, redact recursively, and return bounded stable errors. |
| EAS-008 / Critical | An accepted raw, absolute, traversal, alternate-syntax, device, or URI path escapes the configured Workspace. | Arbitrary host-file read/write within the Worker process privilege. | Canonical authority uses resolved containment (`src/authority_protocol.py:263-287`) and Worker configuration exposes aliases only (`src/agent_worker_adapters.py:45-65`). Accept only alias plus schema-declared relative logical paths and prove containment at use. |
| EAS-009 / Critical | A symlink or race changes a checked in-Workspace path into an outside target. | Same host-file capability as direct path escape. | The implemented task-action schemas accept no logical or host path and reject path-shaped arguments before dispatch. Any future file capability must add race-resistant containment separately. |
| EAS-010 / Medium | Oversized/deep JSON, event floods, slow responses, or task floods cause denial of service. | Exhausted memory, sockets, CPU, disk, queues, or browser responsiveness. | Current broker caps visible task lists and event text (`src/agent_worker_broker.py:176-249`), and ordinary bridge calls use timeouts (`src/agent_worker_adapters.py:221-238`). Add pre-parse byte/depth limits, total/idle deadlines, concurrency/rate quotas, bounded queues/history, cancellation, and backpressure. |
| EAS-011 / High | A dependency, SDK, installer, image, or sidecar update is compromised before admission. | Code execution with the sidecar's process/network/Workspace privileges and falsified results. | JOS-EXT-1 requires pinned reviewed sources for extensions (`specs/jarvis-os-extension-protocol.md:65-87`), but this sidecar is not an extension install. Add no default SDK/installer; require pinned artifacts, license/SBOM/vulnerability/signature review, isolation, least privilege, and independent rollback. |

Residual risk remains installation-owned: an explicitly allowed remote or
private endpoint and its sidecar runtime can still be compromised. Contain it
with a dedicated identity, narrowly scoped credentials, OS/container limits,
network egress policy, monitoring, rotation, and disable/revert rollback. Do
not place the sidecar in Pandamonium's trust boundary merely for convenience.

### Severity calibration

- **Critical:** unauthenticated execution, cross-owner access, or path/symlink
  escape that adds arbitrary host read/write. A valid authenticated operator
  deliberately requesting an in-scope action is not the same impact.
- **High:** theft/replay of a scoped sidecar credential, SSRF into a sensitive
  service, transport compromise, or forged result integrity with a configured
  endpoint. Exact origin, owner, Workspace, and effect checks can reduce
  reachability but not impact.
- **Medium:** authenticated queue/event denial of service or misleading bounded
  progress that cannot cross owner/Workspace/effect boundaries.
- **Low:** bounded liveness/version metadata exposure or an explicit unavailable
  state with no owner data, authority, or fallback routing.

Compromise of the operator account, trusted installation configuration,
Pandamonium host, or protected release keys is outside this sidecar boundary
because those actors already possess broader authority. It remains relevant to
deployment hardening, not evidence that the sidecar grants a new capability.

## Conformance and provenance

[`external-agent-sidecar-v1.json`](../tests/fixtures/external-agent-sidecar-v1.json)
contains valid envelopes plus mocked valid, stale, malformed, unauthorized,
oversized, wrong-owner, wrong-Workspace, unavailable, and replay cases.
`tests/test_external_agent_sidecar_contract.py` validates the schema, fixtures,
effect parity, protocol neutrality, limits, and required threat controls.
`tests/test_external_agent_governed_actions.py` validates stable task/action
identities, exact owner/Connection/Worker/Workspace bindings, all six separate
effect gates, changed-argument and replay denial, reconnect/status/event
mapping, cancellation preservation, and durable safe failures.

This design is independently reconstructed from the MAD-840 intake and current
canonical contracts. Product-concept credit: Twenty4SevenLabs/Pandamonium,
frozen `cursor-bridge` source head
`7220f7cc9cbee26cd94697bd7a6a3d0ef001b66d`. No contributor source, Git
history, package, asset, configuration, credential, or runtime behavior is
imported.

Explicit exclusions are a bundled provider SDK, key-storage implementation,
remote installer, hardcoded node/IP/port, read-write editor-home mount,
transcript scraping, UI, fork merge, data migration, and CT103 action. Rollback
is one PR revert; no data, credential, deployment, sidecar, or configured
database mutation is required.
