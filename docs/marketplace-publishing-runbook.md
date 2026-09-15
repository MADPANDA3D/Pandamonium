# Marketplace publication and discovery

MAD-962 connects the developer's **Add to marketplace** action to validation,
signing, GitHub release upload, atomic catalog update, and verified **Published**
readback. Publication is independent of installation in the developer's account.

## Developer action

1. Open **Plugins → Add a new plugin**, scan a source URL, and inspect its purpose,
   capabilities, setup, permissions and technical findings. An installed plugin's
   detail offers the same publication action.
2. Choose the package version (`major.minor.patch`) and select **Add to
   marketplace**. This action authorizes validation and publication; there is no
   second editorial approval. A changed integration needs a new package version.
3. The UI reports package/license checks, disposable runtime validation,
   signing/upload, and catalog readback. Only confirmed readback displays
   **Published**. Failures offer a safe retry and retain their exact stage/error.

The publisher finalizes draft versions and `source.revision: self` from the
server's recorded immutable scan/installation provenance **before testing**.
The resulting archive digest identifies the package. Generated adapters,
references, schemas and executable modes remain in the archive. Marketplace
installation downloads and verifies those exact bytes; it never regenerates the
integration or re-clones upstream.

## Publisher host setup

Ordinary installations need neither GitHub credentials nor a signing key. On the
trusted developer/publisher host, configure:

```sh
export PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE=/secure/publisher/marketplace-signing.pem
```

The existing Ed25519 key must have no group/other permissions and match the
bundled `marketplace/trusted_keys.json` public key. Authenticate the existing `gh`
CLI for `MADPANDA3D/Pandamonium`, with permission to create release assets and
update the dedicated `marketplace` branch. Never rotate the root by downloading
keys from a catalog server. Distribute a reviewed public-key change through the
signed application release before changing the publisher key.

Validation reuses native Skills admission in a temporary SkillsManager, or the
existing isolated executable operation checks. Executable validation requires the
bounded runtime filesystem and cgroup setup from
[mad-960-package-runtimes.md](mad-960-package-runtimes.md). Package processes
receive only their declared configuration through the existing private mechanism;
no signing/GitHub environment, credentials or publisher home is mounted. The key
is loaded only after validation stops. Account configuration, environments,
caches, runtime data and private key files never enter release artifacts.

A missing license, missing configuration/device/service, unsupported runtime,
failed operation or secret finding fails publication with setup guidance. A schema
listing alone is insufficient. Native Skills are validated as instructions and
admitted references, not advertised as executable tools. Native remote/live-only
packages require a prepared isolated adapter with real operation checks before
using this publisher. Compatibility records only the validation host's platform
and architecture, with a minimum Pandamonium version of **1.0.68** for M17 support.

## Shared channel and integrity

- Each archive has a digest-qualified GitHub release tag and filename. Uploads
  never use `--clobber`; existing bytes are downloaded and compared before reuse.
- Draft releases become public only after asset readback. Catalog publication
  follows, so readers never see a catalog pointing at a pending upload.
- The signed catalog lives at `marketplace/catalog.json` on the dedicated
  `marketplace` branch. The GitHub Contents API's current blob SHA is the
  compare-and-swap guard. Conflicts reread and merge the latest entries; distinct
  publications cannot silently erase each other. A same-version content or
  provenance conflict fails instead of overwriting a package.
- The app ships the signed catalog plus the pinned public key. Default discovery
  refreshes the fixed public channel on first use and every 15 minutes. **Refresh
  catalog** requests an immediate refresh. A bad signature, stale response,
  network failure or oversized response cannot replace the cache. The UI identifies
  fallback to the last verified catalog; expired catalogs cannot authorize installs.
- Existing operator-managed `catalog.json` + `trusted_keys.json` pairs remain
  explicitly managed local channels. Remote refresh never replaces those keys.

Failed requests can be retried after restart. The publisher retains up to 32 small,
owner-scoped status receipts, runs one local publication at a time, and uses remote
content identity/CAS across hosts. An interrupted upload remains a reusable draft;
a completed unlisted immutable artifact remains available for retry. Neither is
an unsigned submission requiring manual editorial handling. The legacy submissions
endpoint forwards to publication for compatibility.

## CLI and rollback

The same publisher is available for prepared package automation:

```sh
.venv/bin/python scripts/publish_marketplace.py \
  --package /tmp/prepared/package.tar.gz --revision FULL_UPSTREAM_COMMIT \
  --version 1.0.0
```

Keep the prior signed catalog as a rollback receipt. Restore its entries with a
new signature/timestamp (never replace or delete immutable artifacts):

```sh
.venv/bin/python scripts/publish_marketplace.py --rollback /secure/previous-catalog.json
```

Rollback verifies the snapshot's signature and uses the current blob SHA; a
concurrent write aborts rollback instead of deleting someone else's publication.
The GitHub branch history retains each prior catalog. Installed packages and user
data remain unchanged. Republish a fresh signed catalog before its 90-day expiry.

The lower-level `scripts/build_marketplace_catalog.py` remains available for
building signed catalogs offline. It does not execute package validation and is
not the developer UI's publication path.

## Verification

See [MAD-962 evidence](mad-962-marketplace-publication.md). Release versioning,
CT103 deployment and operator acceptance are owned by MAD-965; this issue does
not claim the existing installed release already has these changes.
