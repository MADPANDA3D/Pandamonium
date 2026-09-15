# Marketplace publishing runbook

How to build, sign, publish, and revoke the Pandamonium extension catalog
(MAD-941). The runtime side of the contract is implemented in
`src/marketplace_catalog.py`; the catalog format is defined in
`specs/pandamonium-extension-catalog-contract.md` and
`specs/schemas/pandamonium-extension-catalog-v1.schema.json`.

## 1. Signing key custody

The catalog and every package artifact are signed with one Ed25519 key. Keep
the private key **outside Git**, readable only by the publisher:

```bash
mkdir -p ~/.config/pandamonium
openssl genpkey -algorithm ED25519 -out ~/.config/pandamonium/marketplace-signing.pem
chmod 600 ~/.config/pandamonium/marketplace-signing.pem
```

The key id (for example `pandamonium-marketplace-2026`) is embedded in the
catalog and in `trusted_keys.json`. Only the public key half is deployed to
installations. Generate `trusted_keys.json` with the build (step 3); never
commit the private key.

## 2. Package list

Each entry in `marketplace/packages.json` pins a public repository and an
immutable tag:

```json
{
  "extension_id": "oracle",
  "source_url": "https://github.com/MADPANDA3D/ORACLE.git",
  "ref": "jos-v0.1.0",
  "summary": "...",
  "categories": ["visualization"],
  "license": "MIT",
  "publisher": {"id": "madpanda3d", "name": "MADPANDA3D", "url": "https://github.com/MADPANDA3D"},
  "compatibility": {
    "pandamonium_min": "1.0.10",
    "pandamonium_max": "1.99.99",
    "platforms": ["linux", "macos"],
    "architectures": ["amd64", "arm64"]
  },
  "restart_required": "none"
}
```

The repository must contain a strict `jarvis-extension.json`; the build
rewrites its `source.revision` to the resolved commit so the catalog entry is
pinned. Configuration declarations from the manifest are mirrored into the
catalog entry (names/flags only, never values).

### Generated integrations (MAD-957)

A generated manifest or adapter does not have to exist upstream. Add
`"prepared_path": "/absolute/path/to/prepared-package"` to the package spec and
set `ref` to the full immutable upstream commit (40 or 64 hexadecimal characters).
The prepared directory must contain `jarvis-extension.json` at its root with
that exact source URL/revision, the selected source files, and generated adapters,
skills and references. The publisher archives that directory without cloning Git.
It produces deterministic bytes for identical contents and executable modes.

Keep this directory separate from the running plugin's environment and data.
Links, Git metadata, `.env` credential files and private-key files are rejected;
configuration declarations and `.env.example` are allowed. These file checks do
not replace the release secret scan. Never copy account configuration into a
prepared tree.

Installations extract the verified archive directly, with bounded file counts
and size, no links or traversal, and exact manifest/source matching. A content
fingerprint binds preview to activation. The artifact SHA-256 identifies the
installed package; upstream revision remains provenance. Rebuilt integrations
at one upstream commit can therefore upgrade and roll back independently.

## 3. Build

```bash
python scripts/build_marketplace_catalog.py \
  --packages marketplace/packages.json \
  --private-key ~/.config/pandamonium/marketplace-signing.pem \
  --key-id pandamonium-marketplace-2026 \
  --artifact-base-url https://github.com/MADPANDA3D/Pandamonium/releases/download/marketplace-v1 \
  --catalog-id pandamonium-community \
  --out dist-marketplace
```

Output: `catalog.json`, `trusted_keys.json`, and one
`pandamonium-plugin-<id>-<version>.tar.gz` per package. The build is static:
it clones the pinned ref, validates the manifest, archives the exact tree
(`git archive`), hashes it, and Ed25519-signs both the artifact digest
(`sha256:<digest>`) and the catalog (canonical JSON without the signature).
No package code is executed.

## 4. Publish artifacts and catalog

Upload the catalog, trusted keys, and package archives together under the
same release tag the build used as `--artifact-base-url`:

```bash
gh release create marketplace-v1 --repo MADPANDA3D/Pandamonium \
  --title "Marketplace catalog v1" \
  --notes "Signed Pandamonium extension catalog (pandamonium-community)." \
  dist-marketplace/catalog.json dist-marketplace/trusted_keys.json dist-marketplace/*.tar.gz
```

The catalog's artifact URLs must match the release asset URLs exactly
(`https://github.com/<owner>/<repo>/releases/download/marketplace-v1/<file>`).

## 5. Deploy to an installation

The marketplace loader reads two files on the host (default
`$PANDAMONIUM_DATA_DIR/marketplace/`):

```bash
install -d -o odysseus -g odysseus /srv/odysseus/data/marketplace
install -o odysseus -g odysseus -m 0644 dist-marketplace/catalog.json /srv/odysseus/data/marketplace/catalog.json
install -o odysseus -g odysseus -m 0644 dist-marketplace/trusted_keys.json /srv/odysseus/data/marketplace/trusted_keys.json
```

No service restart is required: the loader reads the files per request. Verify
the live view reports the catalog as ready and lists the published packages
(Add Plugins → Available), then install one package through the normal
approval flow.

## 6. Revoke, deprecate, and roll back

- **Revoke one package:** rebuild the catalog with that entry removed (or
  `review.status` set to `revoked` in a follow-up entry), re-upload the
  catalog, and redeploy `catalog.json`. Installed copies are unaffected;
  updates and installs fail closed.
- **Revoke a key:** remove the key id from `trusted_keys.json` and redeploy;
  every catalog/artifact signed by it fails verification.
- **Roll back:** keep the previous `catalog.json` and reinstall it; catalog
  files are small and versioned by release tag.
- **Expiry:** catalogs carry `expires_at` (default 90 days). Build a fresh
  catalog before expiry; expired catalogs fail closed with
  `marketplace_catalog_expired`.
