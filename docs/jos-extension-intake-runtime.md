# Plugin capability discovery and repository intake

Runtime notes for M13 (MAD-910 umbrella, MAD-911 through MAD-916). This
document covers the operator and agent flows only; the contracts and schemas
live in `specs/extension-capability-inventory-contract.md` and
`specs/schemas/extension-capability-inventory-v1.schema.json` /
`specs/schemas/extension-scan-v1.schema.json`.

## Agent capability query

Ask the agent what an installed plugin can do — no activation required:

- `manage_extensions action=list` lists installed extensions, enabled state,
  runtime, and capability counts.
- `manage_extensions action=inspect extension_id=<id>` returns capability
  names, kinds, permission modes, and descriptor class from the persisted
  inventory. Disabled extensions remain inspectable.
- `manage_extensions action=mount names=[...]` loads the named MCP extension
  tools for the rest of the current request only. Calls execute through the
  existing MCP extension adapter with live catalog reconciliation.

Mount rules: the extension must be enabled, the capability must exist in the
current effective catalog, and only MCP-descriptor tools can mount today.
Browser-surface (web/live-catalog) capabilities need their engaged surface and
are refused at mount time. Mounting grants no new authority and does not
persist beyond the request.

## Repository intake (Add from GitHub)

1. Open **Add Plugins** and paste a public `https://` repository URL plus an
   optional ref (defaults to `HEAD`).
2. **Scan repository** runs a staged, bounded static scan:
   `fetch → classify → extract → audit → report`. Progress replays in the
   panel with the stage list and a percent meter.
3. Review the result: repository class, pinned revision, artifact digest,
   extracted capabilities with evidence paths, findings with severities,
   licenses, dependencies, and the draft manifest.
4. **Review install** builds the existing source plan from the pinned
   revision. When the repository ships its own `jarvis-extension.json` that
   manifest is always used. When it does not, the reviewed draft from the
   stored scan artifact becomes the install manifest (`manifest_origin:
   scan_draft` in the plan) and is materialized into the pinned install so
   lifecycle actions keep working. Approve once and the normal
   authority/execute flow installs the extension; the Plugins sidebar
   refreshes. No repository is expected to carry a JOS-specific manifest for
   the paste-a-link flow to work.

Installed-plugin detail (Add Plugins → Installed) shows declared
`configuration` keys with required/secret flags so the operator knows what a
plugin needs; secret values are never shown and live in Settings/Connections.

Invalid (non-`https`) URLs fail locally without a request. Closing the modal
stops polling. If the repository class yields no draft manifest (for example
an unidentified repository), the install action is not offered.

## Backfill

Extensions installed before the inventory existed rebuild it the next time the
registry reads their record: validated effective capabilities and admitted
skills are converted into a revision-bound inventory without re-running any
adapter. Disabled legacy extensions have no effective metadata to rebuild
from; enabling, upgrading, or reinstalling them writes a fresh inventory.

## Limits and boundaries

- Scans are static. Repository build, install, and lifecycle commands are
  never executed; scan artifacts must carry an empty `executed_repo_commands`.
  The MCP adapter probe remains the single explicit runtime-verification path.
- Scan bounds: 50,000 files, 512 MB, 10 minutes; exceeding a bound fails
  closed.
- Findings evidence is redacted before storage; raw secrets never appear in
  artifacts, logs, or the UI.
- The scan produces a proposal. Installation still requires a source plan and
  explicit approval. Skill-bundle drafts only include skills whose assets are
  importable text under the existing bundle limits; excluded skills are
  reported as a `skill_asset` finding so the review is honest about what will
  be admitted.

## Verification

```
.venv/bin/python -m pytest tests/test_extension_capability_inventory_contract.py \
  tests/test_extension_capability_inventory_persistence.py \
  tests/test_extension_agent_mount.py tests/test_extension_scan.py \
  tests/test_extension_intake_end_to_end.py -q
npx playwright test tests/browser/marketplace-intake.spec.js
```
