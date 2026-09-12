# Extension capability inventory, lazy mount, and static scan contracts

Status: accepted architecture checkpoint for MAD-911 (M13 Extension Intake &
Capability Discovery). MAD-912 persists the inventory, MAD-913 implements the
agent query and lazy mount, MAD-914 implements the static scan pipeline, and
MAD-915 adds the intake UI. Nothing in this document grants execution
authority; the existing registry, installer, authority, and executor stay
authoritative.

## 1. Vocabulary and authority boundary

- **Inventory** — advisory, revision-bound capability metadata for one
  installed extension. Readable while disabled, unengaged, or offline. Never
  executable by itself.
- **Effective capabilities** — the registry's live, enabled, reconciled
  capability set. The only source of mountable schemas for execution.
- **Engagement** — per-session selection of an enabled extension. Unchanged.
- **Scan** — a bounded, static inspection of a pinned repository revision that
  produces findings, a capability preview, and a draft manifest. It never runs
  repository-provided build, install, or lifecycle commands.

The inventory answers "what can this plugin do?" without activating it. It
never answers "run this now"; that path stays
`registry.effective_capabilities -> session engagement -> executor allowlist`.

## 2. Capability inventory v1

Artifact: `specs/schemas/extension-capability-inventory-v1.schema.json`.
Runtime contract: `src/extension_capability_inventory.py`
(`build_capability_inventory`, `validate_capability_inventory`,
`inventory_is_current`, `advisory_capability_items`, `resolve_mount_schemas`).

Envelope fields:

| Field | Meaning |
|---|---|
| `inventory_version` | `jos-extension-capability-inventory.v1` |
| `extension_id`, `extension_version` | Exact installed identity |
| `source_revision` | Immutable 40/64-character revision the inventory describes |
| `manifest_digest` | `sha256:` over canonical JSON of the validated manifest |
| `descriptor` | `inline`, `live_catalog`, `mcp`, `openapi`, or `skill_bundle` |
| `capabilities` | Sorted list of capability records |
| `inventory_digest` | `sha256:` over canonical JSON with `inventory_digest` removed |

Capability records are strict: `name`, `kind` (`tool`, `skill`, `endpoint`),
`permission_mode`, `descriptor`, and exactly one of `schema` (tools) or `skill`
metadata (skill bundles). Tool schemas are normalized by the same strict
normalizer the registry uses; skill ids use the existing skill-id pattern.
Unknown fields fail closed. Names are unique across kinds.

Binding and lifecycle rules:

1. The inventory is created at preview/install from the same reconciled adapter
   output the registry uses, so a lazy mount never re-runs an adapter.
2. `disable()` clears effective capabilities and admitted skills but preserves
   the inventory. The inventory may also be present while enabled; it is not a
   second source of truth for execution.
3. An inventory is **current** only when its `source_revision` and
   `manifest_digest` match the installed record. Revision or manifest changes
   mark it stale until the next preview writes a new inventory.
4. Stale, malformed, digest-mismatched, or tampered inventories are rejected:
   they may still be displayed as "unavailable/stale", but they never produce
   mount schemas.
5. Inventory payloads contain names and metadata only. Secrets, absolute
   paths, owner data, and private endpoints are forbidden; skill `source_path`
   is checkout-relative and `owner_scope` is a bound label.

## 3. Lazy mount protocol

The agent surface is one bounded tool (`manage_extensions` in MAD-913) with
actions:

| Action | Result | Authority |
|---|---|---|
| `list` | Extension identities with enabled state and capability counts | None |
| `inspect <extension_id>` | Advisory inventory items (name, kind, mode, descriptor) | None |
| `mount <names...>` | Validated schemas for the requested tool capabilities, current turn only | Enabled record + engagement + executor allowlist |

Protocol rules:

1. `inspect` reads the inventory only. It never enables an extension, starts an
   MCP process, or widens authority. Output is capped
   (`MAX_MOUNT_CAPABILITIES`/response bounds) and carries no schemas.
2. `mount` resolves schemas through `resolve_mount_schemas` (unknown,
   non-tool, duplicate, or oversized requests fail closed), then requires an
   enabled registry record and the existing browser/executor availability
   checks.
3. Mounted schemas are injected for the current turn only. The next turn's base
   catalog is unchanged. First load must not include any unmounted extension
   schema.
4. Mounted tools execute through the existing extension tool executor and
   authority path; a mounting call never creates a new authority layer,
   background process, or session state beyond the turn.
5. Budget interaction: mounted schemas are ordinary extra tool schemas and pass
   through `cap_tool_schemas` and tool-catalog share limits. If the budget
   cannot hold them, the mount fails closed with a clear error instead of
   trimming silently.
6. Reconciled with MAD-907: built-in local/text catalog mounting and extension
   capability mounting share budget semantics but remain separate mechanisms.

## 4. Static scan contract

Artifact: `specs/schemas/extension-scan-v1.schema.json`. Runtime contract:
`src/extension_capability_inventory.py` (`SCAN_STAGES`, `scan_stage_progress`,
`redact_scan_evidence`, `validate_scan_artifact`).

Stages and progress:

| Stage | Percent | Work |
|---|---|---|
| `fetch` | 10 | Pinned clone/checkout inside the existing installer staging root |
| `classify` | 30 | Repo-class detection from bounded file inventory |
| `extract` | 55 | Capability/entrypoint extraction with evidence paths |
| `audit` | 80 | Dependencies, licenses, findings |
| `report` | 100 | Draft manifest, risk summary, terminal artifact |

Artifact fields: `scan_version`, `source_url`, `source_revision`, `stage`,
`repo_class`, `capabilities[]` (name, kind, descriptor, optional permission
mode, evidence path), `dependencies[]` (ecosystem, name, optional version),
`licenses[]`, `findings[]`, `draft_manifest`, `bounds`, and
`executed_repo_commands`. Every artifact carries an `artifact_digest` over its
canonical JSON with the digest field removed; validation recomputes it before
normalization.

Hard boundaries:

1. **No repository execution.** `executed_repo_commands` must be empty;
   validation rejects any non-empty value. The pinned `git` fetch and size
   walk are intake, not repository commands. The MCP probe remains the single
   explicitly marked runtime-verification path and is only reachable through
   the existing adapter preview.
2. **Bounds.** Static scan stops at 50,000 files, 512 MB, or 10 minutes;
   exceeding a bound fails closed with `extension_scan_bounds_exceeded` on the
   terminal artifact. Partial artifacts keep the failure reason.
3. **Sanitization.** Every finding evidence string is produced by
   `redact_scan_evidence`: bearer strings,
   `token=`/`password=`/`secret=`/`api_key=` assignments, common token
   prefixes, and private-key blocks are replaced before stamping. Validation
   rejects any evidence that is not already redacted and bounded
   (`extension_scan_evidence_not_redacted`), so the stored artifact stays
   digest-stable. Raw secret values must never appear in an artifact, log, or
   UI payload.
4. **Draft manifest.** When present, `draft_manifest` must pass the existing
   strict `validate_extension_manifest`. Scan output is a proposal; install
   still goes through `plans/source`, authority approval, and execute.
5. **Repo classes.** `skill_bundle`, `mcp_server`, `python_cli`, `node_cli`,
   `web_app`, `openapi`, and `unknown`. `unknown` still reports findings but
   cannot produce a validated draft manifest.
6. **Idempotence.** Re-scanning an unchanged revision yields the same artifact
   digest; finding ids are stable per `(category, evidence_path)`.

## 5. Threat model

| Threat | Control |
|---|---|
| Inventory tampering to inject schemas into a disabled extension | Strict fields, digest binding, staleness check, and execution gated on the enabled registry record |
| Secret exposure in scan findings or logs | Mandatory redaction, bounded evidence, no raw content in artifacts |
| Repository scripts executing during intake | Static scan only; `executed_repo_commands` must be empty; MCP probe is explicit and unchanged |
| Prompt injection via scan text or skill metadata | Inventory/scan output is data; descriptions are bounded; findings never invoke tools; agent must still mount and execute through governance |
| Denial of service via giant repos or findings | File/byte/time bounds, item caps, and fail-closed terminal states |
| Digest confusion between revision, manifest, inventory, and scan | Distinct fields and `sha256:` domain separation; each validator recomputes its own digest |
| Silent authority widening through mounting | Mount is per-turn, allowlisted, budgeted, and requires the existing enabled + engaged checks |

## 6. Non-goals

- No second registry, plugin manager, scanner daemon, or MCP client.
- No arbitrary dependency installation or build execution in scan v1.
- No automatic installation from scan output; operator approval stays required.
- No execution authority changes for disabled, stale, or unknown extensions.
