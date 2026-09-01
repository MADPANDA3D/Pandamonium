# Jarvis OS v1.0.0 Release Assembly

**Linear:** `MAD-756`

**Distribution home:** `https://github.com/MADPANDA3D/odysseus`

**Odysseus tag:** `jarvis-os-v1.0.0`

This release uses native Git archives plus SHA-256 checksums. It adds no
packager, installer, dependency, registry, or deployment path. ORACLE and all
other extensions remain optional; a clean Odysseus install has an empty
extension registry and no private topology.

## Compatible immutable sources

| Component | Maintained source | Upstream lineage | License | Compatible commit | Immutable tag | Release archive |
| --- | --- | --- | --- | --- | --- | --- |
| Odysseus / Jarvis OS | `MADPANDA3D/odysseus` | `pewdiepie-archdaemon/odysseus` | AGPL-3.0-or-later | Resolved by the release tag and attached manifest | `jarvis-os-v1.0.0` | `jarvis-os-v1.0.0.tar.gz` |
| ORACLE reference | `MADPANDA3D/ORACLE` | `bilawalsidhu/gods-eye-view` | MIT | `b619e2a17015d0e1c044fb273677b00abccdbede` | `jos-v0.1.0` | `oracle-jos-v0.1.0.tar.gz` |
| Barehands | `MADPANDA3D/barehands` | `jaredrhod/barehands` | AGPL-3.0-or-later | `0ef7c9a2f302f1fefe5b3fd9a56f987f4d8f1cff` | `jos-v0.1.0` | `barehands-jos-v0.1.0.tar.gz` |
| img2threejs | `MADPANDA3D/img2threejs` | `img2threejs/img2threejs` | Apache-2.0 | `54734b5d307876753d0433f489497be5c8c32428` | `jos-v1.5.1-jos.2` | `img2threejs-jos-v1.5.1-jos.2.tar.gz` |
| text-to-cad | `MADPANDA3D/text-to-cad` | `earthtojake/text-to-cad` | MIT | `fd444ccf5805f2c5ac451cc5794cf419a3676ed9` | `jos-v0.4.28-jos.2` | `text-to-cad-jos-v0.4.28-jos.2.tar.gz` |
| Robin | `MADPANDA3D/robin` | `apurvsinghgautam/robin` | MIT | `8d4b4109f6928016f7976472309d2b7336b005b0` | `jos-v2.8.0-jos.2` | `robin-jos-v2.8.0-jos.2.tar.gz` |

Every maintained source preserves its upstream Git history and license file.
Barehands and Odysseus convey complete corresponding source under the AGPL;
Barehands network use also carries the section 13 source-offer obligation
documented in its `docs/JOS_EXTENSION.md`. The central release does not combine
the projects into one differently licensed work; each archive stays separate.

## Reproducible archive commands

Run these commands from a directory containing the six clean repositories:

```bash
mkdir -p dist/jarvis-os-v1.0.0
git -C odysseus archive --format=tar.gz --prefix=jarvis-os-v1.0.0/ jarvis-os-v1.0.0 > dist/jarvis-os-v1.0.0/jarvis-os-v1.0.0.tar.gz
git -C ORACLE archive --format=tar.gz --prefix=oracle-jos-v0.1.0/ jos-v0.1.0 > dist/jarvis-os-v1.0.0/oracle-jos-v0.1.0.tar.gz
git -C barehands archive --format=tar.gz --prefix=barehands-jos-v0.1.0/ jos-v0.1.0 > dist/jarvis-os-v1.0.0/barehands-jos-v0.1.0.tar.gz
git -C img2threejs archive --format=tar.gz --prefix=img2threejs-jos-v1.5.1-jos.2/ jos-v1.5.1-jos.2 > dist/jarvis-os-v1.0.0/img2threejs-jos-v1.5.1-jos.2.tar.gz
git -C text-to-cad archive --format=tar.gz --prefix=text-to-cad-jos-v0.4.28-jos.2/ jos-v0.4.28-jos.2 > dist/jarvis-os-v1.0.0/text-to-cad-jos-v0.4.28-jos.2.tar.gz
git -C robin archive --format=tar.gz --prefix=robin-jos-v2.8.0-jos.2/ jos-v2.8.0-jos.2 > dist/jarvis-os-v1.0.0/robin-jos-v2.8.0-jos.2.tar.gz
(cd dist/jarvis-os-v1.0.0 && sha256sum *.tar.gz | LC_ALL=C sort -k2 > SHA256SUMS)
```

Running the commands twice from the same tags must produce identical digests.
The release also attaches `release-manifest.json`, which records each tag's
resolved full commit, archive filename, SHA-256 digest, license, maintained
source, and upstream lineage.

## Clean-install acceptance

Download the assets from the Odysseus `jarvis-os-v1.0.0` release, then:

```bash
sha256sum -c SHA256SUMS
tar -xzf jarvis-os-v1.0.0.tar.gz
cd jarvis-os-v1.0.0
python setup.py
python -m pytest -q tests/test_public_release_defaults.py
```

Use a new data directory and generic administrator, identity, constitution,
and loopback model endpoints. Do not set worker-topology or extension values.
The accepted state has no credential in settings, no private path or host, no
worker workspace, and no installed extension. Optional extensions are installed
later only by maintained-source URL plus immutable tag through the documented
approval-gated lifecycle.

## Claim boundary

Passing source-tag and archive installation proves **package-installed**. It
does not prove **live-accepted**. No provider, external data feed, Tor/onion
request, hardware, CT103/CT104 deployment, or persistent installation is part
of this release gate.
