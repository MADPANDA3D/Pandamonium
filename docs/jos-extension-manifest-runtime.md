# JOS Extension Manifest and Registry Runtime

**Linear:** `MAD-751`

Pandamonium now has the smallest reference-neutral contract needed to admit
extension metadata before any Git installer or generic lifecycle host exists.

## Contract

- `specs/schemas/jos-extension-v1.schema.json` defines the strict
  `jos-extension.v1` manifest shared by ORACLE and differently named fixtures.
- The manifest declares source identity plus an exact revision or the `self`
  binding that the installer replaces with its observed full commit; it declares runtime and data
  boundaries, requests permission modes, references or embeds a capability
  descriptor, and records bounded health/lifecycle/removal/rollback metadata.
- MCP, OpenAPI, and live catalogs stay authoritative. Their schemas are
  resolved by the existing adapter and reconciled instead of copied into the
  manifest.
- Inline schemas are accepted only when the descriptor type is `inline`.

## Registry boundary

`src/extension_registry.py` performs pure validation plus atomic metadata
persistence. Registration requires an exact observed source revision and a
successful health result. Reconciliation rejects unknown security fields,
malformed schemas, duplicate capability names, catalog identity/revision drift,
undeclared permission overrides, and cross-extension name conflicts.

The registry exposes validated schemas, extension IDs, and permission metadata
for the existing agent-loop catalog. It has no install, fetch, shell, lifecycle,
or execute method. Disabling a record clears its effective capabilities and
removes it from engaged context output without affecting another extension or
Pandamonium itself.

## Fixtures and boundaries

The ORACLE fixture references its live catalog endpoint and pinned source. The
Atlas fixture proves the same schema with a different extension ID and an
OpenAPI reference. Focused tests also cover MCP references, no copied schemas,
unavailable health, source-revision drift, registry tampering, and disable
behavior.

No repository was cloned, no lifecycle vector was executed, no endpoint was
fetched, no package or framework was added, no ORACLE-native source changed,
and nothing was deployed.

Verification: 9 focused contract/registry tests and the complete repository
suite of 4,983 passed with 4 intentional skips and 0 failures. Python
compilation, JSON parsing, and `git diff --check` also pass.
