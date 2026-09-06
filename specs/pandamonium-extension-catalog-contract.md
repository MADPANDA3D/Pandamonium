# Pandamonium published extension catalog contract

Status: accepted architecture checkpoint for MAD-788; the MAD-790 lifecycle
bridge implements this contract without publishing a package.

## One catalog, installer, and registry

`specs/schemas/pandamonium-extension-catalog-v1.schema.json` is the distribution
envelope around the existing `jos-extension.v1` manifest. It does not create a
second plugin registry. A selected catalog entry becomes an exact input to
`ExtensionLifecycleManager.preview_source`; installation, approval, health,
activation, rollback, disable, and recoverable removal continue through
`src/extension_installer.py` and `src/extension_registry.py`.

The catalog and every artifact digest are signed with Ed25519 keys from the
installation trust store. The catalog signature covers canonical JSON with the
top-level `signature` field removed. An artifact signature covers the ASCII
text `sha256:<lowercase digest>`. Downloaded bytes must match both `size_bytes`
and `sha256` before any adapter sees them. Unknown keys, missing/invalid
signatures, expired catalogs, malformed entries, and digest drift fail closed.

Each entry embeds the exact existing extension manifest and adds only public
distribution facts: summary/categories, SPDX license expression, publisher/key
provenance, checksummed artifact, supported Pandamonium/platform/architecture
range, typed plugin/optional-package dependencies, configuration key
declarations, restart requirement, review state, and security advisories. The embedded manifest remains authoritative for requested
permissions, runtime descriptor, data/network boundaries, health, lifecycle,
rollback retention, and removal/preservation paths.

## Install and update plan

1. Fetch the catalog over the configured marketplace channel. Offline fetch or
   an expired cached catalog cannot produce an install/update plan.
2. Verify the catalog signature against an installation-owned trusted key.
3. Select one exact `(extension_id, version)` entry. Reject revoked or
   incompatible entries.
4. Preview the license, publisher, artifact digest/signature, permissions,
   dependencies, configuration names, restart requirement, health, removal,
   and rollback contract.
5. Download the artifact without executing it; verify its exact size and digest.
6. Pass the manifest's canonical Git URL and full 40/64-character revision to
   the existing extension source preview. `HEAD`, branches, mutable tags,
   `self`, and arbitrary repository scripts are not catalog install inputs.
7. Reconcile the checked-out manifest with the signed entry before the existing
   authority decision can execute. `POST /api/extensions/marketplace/plans`
   now owns this seam and reuses the native lifecycle manager.

An update is another signed entry for the same extension ID at a different
version and immutable revision. The existing `upgrade` preview must show the
same fields plus current/target versions. Failed health or activation retains
the current registry record and revision; rollback selects a retained immutable
revision. Dependency changes, configuration migration, retained data, and
restart requirements remain visible before approval.

## M6 taxonomy boundary

- **Core** is the Pandamonium release and updater lane, not an extension.
- **Connection** owns transport and owner-scoped credential references; it is
  configured through its native integration surface and cannot ship secrets.
- **Plugin** is the only installable catalog entry type. It is one pinned
  `jos-extension.v1` manifest managed by the existing lifecycle and registry.
- **Tool** is a capability owned by Core, a Connection, or a Plugin. It is never
  an independently installed repository and cannot widen its owner's authority.
- **Optional package** is an explicitly typed host/runtime dependency. It is
  preview-only here and is never silently installed or written into the host.

The v1 schema therefore fixes top-level `package_type` to `plugin`; dependency
records distinguish `plugin` from `optional_package`. A Connection, Tool, Core
release, or optional host package presented as a plugin is rejected rather than
routed through the extension installer.

## Secrets and data

Configuration records contain only an uppercase key, purpose, required flag,
and secret flag. Values, defaults, credentials, tokens, private endpoints, and
owner data are forbidden catalog/package content. Secret values stay in the
owner-scoped runtime secret store and are referenced only after installation.
Disable preserves plugin data. Removal previews the manifest's distinct remove
and preserve paths, defaults to retention, and remains recoverable.

## Review, deprecation, revocation, and advisories

Publication requires source/license review, a reproducible artifact, immutable
revision, manifest validation, compatibility proof, permission/data review,
malware/secret scanning, and signatures from trusted catalog and publisher
keys. The signed record names its reviewer and review time.

Deprecation keeps an entry visible with a warning and successor guidance; it
does not silently remove an installed plugin. Revocation changes the signed
record to `revoked`, lists applicable advisories, and blocks new install/update
plans. Installed copies remain visible so the owner can disable, roll back, or
remove them through the existing lifecycle. Critical advisories trigger the
same signed revocation process; neither catalog text nor a plugin may mutate
the local registry by itself. Key compromise removes the key from the local
trust store and republishes the catalog under a replacement trusted key.

## Checkpoint proof and rollback

`tests/test_marketplace_catalog.py` signs a reference-neutral Atlas entry at
runtime, validates the existing manifest and immutable installer input, checks
the JSON schema, and verifies artifact bytes. It also proves that unsigned,
untrusted, expired, mutable-ref, taxonomy-confused, secret-bearing,
incompatible, revoked, offline, duplicate, and tampered cases fail closed.

Rollback is `git revert` of the MAD-788 contract commit. There is no database,
registry, package, credential, source checkout, service, or CT103 state to
restore.
