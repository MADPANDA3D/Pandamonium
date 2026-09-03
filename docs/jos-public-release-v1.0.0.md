# Jarvis OS v1.0.0 Release Assembly

**Linear:** `MAD-756`

**Distribution home:** `https://github.com/MADPANDA3D/Pandamonium`

**Pandamonium tag:** `jarvis-os-v1.0.0`

**Published release:**
`https://github.com/MADPANDA3D/Pandamonium/releases/tag/jarvis-os-v1.0.0`

**Release commit:** `ee470206b669a119b6740a71c98ae9cba8c23237`

This release uses native Git archives plus SHA-256 checksums. It adds no
packager, installer, dependency, registry, or deployment path. ORACLE and all
other extensions remain optional; a clean Pandamonium install has an empty
extension registry and no private topology.

## Compatible immutable sources

| Component | Maintained source | Upstream lineage | License | Compatible commit | Immutable tag | Release archive |
| --- | --- | --- | --- | --- | --- | --- |
| Pandamonium / Jarvis OS | `MADPANDA3D/Pandamonium` | `pewdiepie-archdaemon/odysseus` | AGPL-3.0-or-later | `ee470206b669a119b6740a71c98ae9cba8c23237` | `jarvis-os-v1.0.0` | `jarvis-os-v1.0.0.tar.gz` |
| ORACLE reference | `MADPANDA3D/ORACLE` | `bilawalsidhu/gods-eye-view` | MIT | `b619e2a17015d0e1c044fb273677b00abccdbede` | `jos-v0.1.0` | `oracle-jos-v0.1.0.tar.gz` |
| Barehands | `MADPANDA3D/barehands` | `jaredrhod/barehands` | AGPL-3.0-or-later | `0ef7c9a2f302f1fefe5b3fd9a56f987f4d8f1cff` | `jos-v0.1.0` | `barehands-jos-v0.1.0.tar.gz` |
| img2threejs | `MADPANDA3D/img2threejs` | `img2threejs/img2threejs` | Apache-2.0 | `54734b5d307876753d0433f489497be5c8c32428` | `jos-v1.5.1-jos.2` | `img2threejs-jos-v1.5.1-jos.2.tar.gz` |
| text-to-cad | `MADPANDA3D/text-to-cad` | `earthtojake/text-to-cad` | MIT | `fd444ccf5805f2c5ac451cc5794cf419a3676ed9` | `jos-v0.4.28-jos.2` | `text-to-cad-jos-v0.4.28-jos.2.tar.gz` |
| Robin | `MADPANDA3D/robin` | `apurvsinghgautam/robin` | MIT | `8d4b4109f6928016f7976472309d2b7336b005b0` | `jos-v2.8.0-jos.2` | `robin-jos-v2.8.0-jos.2.tar.gz` |

Every maintained source preserves its upstream Git history and license file.
Barehands and Pandamonium convey complete corresponding source under the AGPL;
Barehands network use also carries the section 13 source-offer obligation
documented in its `docs/JOS_EXTENSION.md`. The central release does not combine
the projects into one differently licensed work; each archive stays separate.

## Reproducible archive commands

Run these commands from a directory containing the six clean repositories:

```bash
mkdir -p dist/jarvis-os-v1.0.0
git -C Pandamonium archive --format=tar.gz --prefix=jarvis-os-v1.0.0/ jarvis-os-v1.0.0 > dist/jarvis-os-v1.0.0/jarvis-os-v1.0.0.tar.gz
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

## Published artifact receipt

| Asset | SHA-256 |
| --- | --- |
| `jarvis-os-v1.0.0.tar.gz` | `5554b189e69b64e3be2e5b6d60093b03261f15be1951ef6d27ae1a86ffbea370` |
| `oracle-jos-v0.1.0.tar.gz` | `6737993b680293ccc4b2b551e1f95d14a536eab31421f4f611f39e3b7774e7a7` |
| `barehands-jos-v0.1.0.tar.gz` | `aa4114ff6ef00f019d1f65f4f0aaa44671db6c8c71081569f1a1ef127ebed4e2` |
| `img2threejs-jos-v1.5.1-jos.2.tar.gz` | `55656c5749bf90f0f0fb9c7cef6cef042713e4d3baecd45d9fabbeb1d9384fcd` |
| `text-to-cad-jos-v0.4.28-jos.2.tar.gz` | `b9517c17f5f4dd9e3232a4f4944c92820e67c8799f01c7b0aa9e463a61852c39` |
| `robin-jos-v2.8.0-jos.2.tar.gz` | `bed4fb86ca2b226e85df9e3309dea696a6f419fa4b1dc7b47c8ad31cda8752af` |
| `release-manifest.json` | `25977d6629fa45d7e9fda6943c4a870453aff9c62fb93c71ac01eae101dabbd6` |
| `SHA256SUMS` | `5faff172b5b5ea857474b316796e2ee512ac0e1bf0dc558f96ed48743542cce4` |

GitHub's asset digest readback matches every local digest. Rebuilding all six
archives twice from the immutable tags produced byte-identical files.

## Clean-install acceptance

Download the assets from the Pandamonium `jarvis-os-v1.0.0` release, then:

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

The acceptance run downloaded the public assets, verified all checksums,
extracted a fresh source tree, ran `setup.py` with a generic administrator and
new data/extension directories, confirmed no installed extension or private
runtime value, and passed the public-default/schema checks: 11 passed, zero
failed. The release commit's complete Pandamonium gate passed 5,024 tests with 5
intentional skips and zero failures in 123.84 seconds.

## Claim boundary

Passing source-tag and archive installation proves **package-installed**. It
does not prove **live-accepted**. No provider, external data feed, Tor/onion
request, hardware, CT103/CT104 deployment, or persistent installation is part
of this release gate.
