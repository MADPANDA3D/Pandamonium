# MAD-799 — First read-only cybersecurity package tranche (selection)

Status: **research complete, awaiting Leo's approval** · Date: 2026-09-14 · Lane: M12 security/training research
Evidence artifact: `docs/security/mad-799-tranche-one-evidence.json`
Threat model: `docs/security/mad-799-tranche-one-threat-model.md`

This document selects the first marketplace tranche of defensive, read-only
cybersecurity plugins. It is a selection and threat-model deliverable: **no
package is implemented, downloaded, published, installed, or pointed at any
target by this issue.** Leo approves the final package list before adaptation
begins.

## 1. Decision requested from Leo

| # | Candidate | Category | Recommendation |
|---|---|---|---|
| 1 | `syft-sbom` (Syft v1.51.1) | Inventory / SBOM | **Approve for adaptation** |
| 2 | `grype-vuln` (Grype v0.118.0) | Vulnerability-result matching | **Approve for adaptation** |
| 3 | `osv-deps` (OSV-Scanner v2.6.0) | Dependency/SBOM audit | **Approve for adaptation** |
| 4 | `gitleaks-secrets` (Gitleaks v8.30.1) | Secret scanning | **Approve for adaptation** |
| 5 | `checkdmarc-dns` (checkdmarc 6.0.1) | DNS / email-auth inspection | **Approve for adaptation** |
| 6 | `cyclonedx-reports` (CycloneDX CLI v0.33.1) | Report generation | **Approve for adaptation** |
| 7 | `chainsaw-logs` (Chainsaw v2.16.5) | Log analysis | **Conditional** — approve only with GPL-3.0 packaging |
| 8 | `testssl-tls` (testssl.sh v3.2.4) | TLS inspection | **Conditional** — single-target gate + GPL compliance |
| — | Checkov, KubeLinter, Hadolint | Configuration audit | **Deferred** to tranche two |
| — | Exploitation, credential attacks, persistence, evasion, destructive response, unrestricted scanning | — | **Excluded** (section 7) |

Recommended approval: **candidates 1–6 as tranche one**, 7–8 held until their
conditions are met, config-audit candidates in tranche two.

What approval unlocks: adaptation lanes may pin each upstream release, build
the thin plugin wrapper, and prepare a signed catalog entry. None of that is
authorized by this document.

## 2. How the tranche was selected

Filters applied, in order:

1. **Explicit user job.** Every candidate answers one narrow operator question.
   Cybersecurity never ships as one opaque bundle; each job is its own plugin
   with its own manifest, permissions, and network boundary.
2. **Defensive and read-only.** No candidate modifies its target, performs
   exploitation, or auto-remediates. Report files are the only writes.
3. **Bounded targets.** Local workspace paths, one operator-named domain, or
   one operator-owned host:port. No ranges, lists, or third-party targets.
4. **License.** Tranche one is Apache-2.0/MIT only. GPL-family candidates are
   conditional because the project is AGPL-3.0-or-later and packaging must ship
   upstream license/source obligations; GPL-2.0-only would be incompatible.
5. **Maintenance.** Active repositories with tagged releases and public release
   metadata; no archived projects.
6. **Privilege.** No root, sudo, sudoers, raw sockets, kernel modules, container
   sockets, or device access. All six proposed candidates run as the normal
   Pandamonium service user.
7. **Network effect visibility.** Offline-first where possible; every outbound
   host must be declareable in `data_boundaries.network` and visible in the
   catalog preview before installation.
8. **Data handling.** No credentials in tranche one. Findings that can contain
   secrets (gitleaks, grype JSON) are redacted before agent/UI exposure and
   stored owner-scoped.
9. **Testability.** Each candidate has a public test story: fixture-based
   wrapper tests plus upstream binaries pinned by revision and checksum.

Provenance quality was recorded but not used as the sole gate: syft/grype and
OSV-Scanner publish checksums and signatures/provenance; CycloneDX CLI and
Chainsaw do not, so their publish-time trust rests on the signed catalog.

## 3. Classification vocabulary

The requested classes are recorded per candidate and mapped to the existing
contracts:

| Selection class | Meaning | Canonical action effect | Manifest permission |
|---|---|---|---|
| read-only | Never mutates the target; reads data and returns findings | `read` | `read_only` |
| mutating | Changes the target or other systems | any write effect | `bounded_write` or stricter |
| privileged | Needs root/raw sockets/kernel access | `privilege_expansion` gate | `controlled_administrative` |
| network-active | Makes outbound connections at runtime | still `read` when the effect is a lookup; external effect is declared, not gated per-request | `read_only` + network boundary |
| dual-use | Same code can be used offensively with other inputs | n/a | risk note and target gate |

Tranche one contains **zero mutating and zero privileged candidates**. Three
of the six proposed are network-active (grype DB refresh, OSV lookups, DoH
lookups) and three are fully offline (syft, gitleaks, cyclonedx-reports). The
conditional TLS candidate adds a fourth network-active job if approved.

## 4. Proposed tranche one — detail

Common adaptation shape for all six:

- **One plugin per job**, `package_type: plugin`, pinned upstream revision.
- **Thin MCP wrapper** owned by MADPANDA3D; upstream binary unmodified. The
  wrapper exposes one tool per job with a bounded input schema.
- `permissions.default: read_only`; report-writing capabilities override to
  `bounded_write` into a plugin-owned results directory.
- `data_boundaries`: read = workspace-relative inputs; write = plugin results
  directory; network = the exact upstream endpoints when the job needs them.
- `configuration`: non-secret keys only (offline mode, cache path, resolver
  choice). **No secrets in tranche one.**
- `restart_required: none`; rollback `pinned_revision` with retained revisions.
- Catalog `compatibility`: linux + macos, amd64 + arm64. Windows is recorded
  upstream but deferred until Windows install/health tests exist.
- Catalog `license`: the upstream SPDX id; artifact carries our sha256 and
  Ed25519 signature per the publishing runbook.

### 4.1 `syft-sbom` — Syft v1.51.1 (Apache-2.0)

- **User story:** as the operator, I want an SBOM for a workspace folder or
  rootfs tarball so I can see every package and version before I trust it.
- **Classification:** read-only, not mutating, not privileged, offline,
  dual-use low.
- **Maintainer/release:** Anchore; repo pushed 2026-09-11; v1.51.1 2026-08-27;
  9,558 stars; actively released.
- **License:** Apache-2.0 (GitHub metadata + raw LICENSE at tag).
- **Dependencies:** none at runtime (static Go binary).
- **CVE history:** GHSA-jp7v-3587-2956 / CVE-2023-24827 (credential disclosure
  via `SYFT_ATTEST_PASSWORD`) and GHSA-rjcw-vg7j-m9rc / CVE-2026-33481
  (temporary-file cleanup). OSV reports **no advisory affecting v1.51.1**.
  Wrapper rule: never pass secret environment variables; keep temp files inside
  the plugin workspace.
- **Provenance:** release `checksums.txt` plus `.pem`/`.sig` signature pair and
  per-platform SBOM assets.
- **Platforms:** linux amd64/arm64 (+ppc64le/riscv64/s390x), macOS amd64/arm64,
  Windows amd64/arm64.
- **Permissions / external effects:** read workspace files; no network; writes
  the SBOM only at the operator's request.
- **Bounded input:** workspace-relative target only; reuse the static-scan
  bounds (50,000 files / 512 MB / 10 min); no symlink escape; skip special
  files.

### 4.2 `grype-vuln` — Grype v0.118.0 (Apache-2.0)

- **User story:** as the operator, I want the packages in my project checked
  against known vulnerabilities with a severity-ranked local report.
- **Classification:** read-only target, not mutating, not privileged,
  network-active (database refresh only), dual-use low.
- **Maintainer/release:** Anchore; repo pushed 2026-09-11; v0.118.0 2026-08-27;
  12,877 stars.
- **License:** Apache-2.0.
- **Dependencies:** none at runtime; the vulnerability DB is data, not a
  package.
- **CVE history:** GHSA-6gxw-85q2-q646 / CVE-2025-65965 (high, credential
  disclosure in JSON output). OSV reports **no advisory affecting v0.118.0**.
  Reinforces mandatory output redaction.
- **Provenance:** `checksums.txt` plus `.pem`/`.sig` pair.
- **Platforms:** linux amd64/arm64 (+ppc64le/s390x), macOS amd64/arm64,
  Windows amd64.
- **Permissions / external effects:** reads local artifacts; the only network
  effect is the official DB endpoint; DB refresh is a separate declared action
  and offline mode uses a pre-staged DB.
- **Bounded input:** local directory/SBOM/rootfs tarball only — no container
  socket and no image pulls in tranche one.

### 4.3 `osv-deps` — OSV-Scanner v2.6.0 (Apache-2.0)

- **User story:** as the operator, I want my lockfiles checked against OSV so I
  know which dependencies need an update.
- **Classification:** read-only, not mutating, not privileged, network-active
  (OSV API), dual-use low.
- **Maintainer/release:** Google; repo pushed 2026-09-14; v2.6.0 published
  2026-09-14; 11,022 stars.
- **License:** Apache-2.0.
- **Dependencies:** none at runtime; OSV data is external.
- **CVE history:** none observed in OSV or repository advisories.
- **Provenance:** `SHA256SUMS` plus `multiple.intoto.jsonl` SLSA provenance —
  the strongest provenance in the tranche.
- **Platforms:** linux/macOS/Windows amd64+arm64.
- **Permissions / external effects:** reads lockfiles; queries `api.osv.dev`
  unless offline data is supplied.
- **Bounded input:** workspace-relative manifests/lockfiles; advisory lookups
  are package-coordinate queries, never target probes; offline mode required
  for air-gapped hosts.

### 4.4 `gitleaks-secrets` — Gitleaks v8.30.1 (MIT)

- **User story:** as the operator, I want to check a repo for committed
  credentials before I share it, with file/line locations but not exposed
  secret values in chat.
- **Classification:** read-only, not mutating, not privileged, offline,
  dual-use low. Highest data sensitivity in the tranche.
- **Maintainer/release:** repo pushed 2026-09-09; v8.30.1 tagged 2026-03-21;
  29,294 stars; releases periodic.
- **License:** MIT.
- **Dependencies:** none at runtime (static Go binary).
- **CVE history:** none observed.
- **Provenance:** `checksums.txt` only; catalog signing supplies publish-time
  provenance.
- **Platforms:** linux x64/arm64/armv6/armv7/x32, macOS x64/arm64, Windows
  x64/arm64/x32.
- **Permissions / external effects:** reads workspace files; zero network
  egress; no history rewriting, no remediation.
- **Bounded input:** workspace-relative checkout/directory; findings redacted
  in every agent-visible/logged/stored preview; owner-scoped retention with an
  explicit removal path; fixture tests must prove redaction per rule family.

### 4.5 `checkdmarc-dns` — checkdmarc 6.0.1 (Apache-2.0)

- **User story:** as the operator, I want a bounded check of my own domain's
  SPF/DMARC/DKIM records to find missing or misconfigured email authentication.
- **Classification:** read-only, not mutating, not privileged, network-active
  (DNS-over-HTTPS), dual-use moderate.
- **Maintainer/release:** domainaware; repo pushed 2026-09-04; 6.0.1 published
  2026-09-04; 321 stars, 1 open issue.
- **License:** Apache-2.0 (GitHub + PyPI `license_expression`).
- **Dependencies:** Python >= 3.10 with 10 direct dependencies (cryptography,
  dnspython[doh], expiringdict, httpx, importlib-resources, pem,
  publicsuffixlist, pyleri, requests, xmltodict). This is the largest
  dependency surface in the tranche and must be vendored hash-pinned.
- **CVE history:** none for checkdmarc itself; the dependency tree is the CVE
  surface and must be audited at adaptation.
- **Provenance:** PyPI sha256 wheel digests recorded; PyPI attestation status
  not checked.
- **Platforms:** pure Python (any), proposed catalog linux+macos.
- **Permissions / external effects:** resolves operator-named domains over DoH
  resolvers; no email sending, no mailbox access, no SMTP probing.
- **Bounded input:** one domain per invocation; DoH-only because the manifest
  network boundary expresses https URLs; no zone transfers and no bulk lists.
  This DoH-only constraint is a required adaptation decision, not an upstream
  default.

### 4.6 `cyclonedx-reports` — CycloneDX CLI v0.33.1 (Apache-2.0)

- **User story:** as the operator, I want the SBOMs I already own validated and
  merged into one report.
- **Classification:** read-only inputs, not mutating, not privileged, offline,
  dual-use low.
- **Maintainer/release:** CycloneDX project; repo pushed 2026-08-20; v0.33.1
  2026-07-23; 541 stars.
- **License:** Apache-2.0.
- **Dependencies:** none at runtime (.NET self-contained binaries).
- **CVE history:** none observed; NuGet OSV identity is approximate and marked
  unverified.
- **Provenance:** **no release checksums or signatures** — the weakest in the
  tranche. Publish-time trust depends entirely on the signed catalog artifact;
  recommended risk acceptance requires catalog signing and a clean source
  review at adaptation.
- **Platforms:** linux x64/arm64/arm (+musl), macOS x64/arm64, Windows
  x64/x86/arm64.
- **Permissions / external effects:** reads SBOM files; writes reports to a
  plugin-owned path; no network.
- **Bounded input:** file size/element count bounds reuse the static-scan
  bounds; no runtime schema fetching.

## 5. Conditional candidates

| Candidate | Condition before approval | Notes |
|---|---|---|
| `chainsaw-logs` (GPL-3.0) | Plugin package ships upstream LICENCE + pinned source offer. Then it can join tranche one as the log-analysis job. | Offline, read-only, dual-use low; Linux/macOS amd64+arm64; Windows x86_64; no upstream checksums; Sigma rule-pack licensing to audit. |
| `testssl-tls` (GPL-2.0-or-later) | Single host:port per run + target-ownership attestation + rate limits + GPL compliance packaging. | Network-active TLS probing, dual-use moderate; source-only release (no binary assets); host bash+openssl dependency; manifest network boundary may not express host:port targets — the ownership gate may need to live in the wrapper. |

GPL compatibility context: this repository is **AGPL-3.0-or-later**. GPL-3.0
(Chainsaw) and GPL-2.0-or-later (testssl.sh, LICENSE text confirmed to include
the "any later version" option) are compatible with AGPL-3.0 at the
separate-plugin/aggregate boundary when the package conveys the license and
source. A GPL-2.0-only candidate would be rejected.

## 6. Deferred configuration-audit candidates (tranche two research)

| Candidate | Version observed | License | Why deferred |
|---|---|---|---|
| `checkov-iac` (Checkov 3.3.17) | 2026-09-10 | Apache-2.0 | Broad IaC job, but large Python dependency/rule surface needs pinned-wheel and rule-provenance review. |
| `kube-linter-k8s` (KubeLinter v0.8.3) | 2026-03-10 | Apache-2.0 | Good sigstore provenance and static binary, but Kubernetes-only and overlaps the IaC slot. |
| `hadolint-dockerfile` (Hadolint v2.15.1) | 2026-07-31 | GPL-3.0 | Narrow Dockerfile job plus GPL packaging; covered conceptually by the IaC slot in tranche two. |

These were metadata-checked only; CVE, dependency, and rule-provenance audits
are required before any selection (marked unverified in the evidence file).

## 7. Explicit exclusions (tranche one and the catalog)

| Excluded class | Representative examples | Why |
|---|---|---|
| Active exploitation | exploitation frameworks, payload generators, weaponized PoC runners | Can compromise targets; not defensive |
| Credential attacks | brute force, password cracking, credential stuffing, relay/coercion | Attacks authentication |
| Persistence | implant/C2 frameworks, startup/scheduled-task persistence | Establishes unauthorized durable access |
| Evasion | AV/EDR bypass, log tampering, obfuscation frameworks | Defeats defensive controls and evidence |
| Destructive response | auto-quarantine/delete, firewall/account mutation, process killing | Mutating response needs the separate destructive gate; tranche one never auto-remediates |
| Unrestricted scanning | internet-wide/CIDR scanners, mass enumeration, third-party vulnerability scans | Unbounded external effects; only single operator-owned targets are allowed |

Additional exclusions applied during selection: privileged/raw-socket packet
capture and host agents that require kernel or device access (deferred, not
just unselected), any tool requiring cloud API credentials (no secrets in
tranche one), and any package with no pinned release artifact (source-only
projects are allowed only when the plugin package pins the tag itself, as with
testssl.sh).

## 8. Mapping to the M6 capability and marketplace contracts

Taxonomy boundary (`specs/pandamonium-extension-catalog-contract.md`): every
candidate is a **Plugin**; every job is a **Tool owned by that Plugin**; none
is a Core release, Connection, or independently installed repository; no
optional host package is silently installed. Python dependencies are vendored
inside the plugin artifact, not declared as auto-installed optional packages.

Marketplace catalog mapping (`specs/schemas/pandamonium-extension-catalog-v1.schema.json`):

| Catalog field | Tranche-one value |
|---|---|
| `package_type` | `plugin` |
| `manifest` | `jos-extension.v1` v1 manifest pinned to a 40-char revision |
| `summary` / `categories` | one job summary; `security` plus a job tag (`sbom`, `vulnerability`, `secrets`, `dns`, `reporting`, `logs`) |
| `license` | upstream SPDX id (Apache-2.0/MIT; GPL-family only if conditional candidates pass) |
| `publisher` | MADPANDA3D publisher key id from the runbook trust store |
| `artifact` | plugin tarball with sha256, size, Ed25519 signature |
| `compatibility` | linux+macos, amd64+arm64 (Windows deferred) |
| `dependencies` | empty for tranche one; plugin-internal wrappers are not dependencies |
| `configuration` | non-secret keys only (offline mode, cache path, DoH resolver); no secret:true entries |
| `restart_required` | `none` |
| `review` | `active`, named reviewer, `security_advisories` copied from the CVE history above |

Capability/authorization mapping (`specs/pandamonium-capability-authorization-contract.md`):

- Every job is effect `read` → authorized by the operator's explicit request,
  no separate gate, exactly like other read-only work.
- Report writes are `reversible_write` bounded to the plugin results directory.
- No candidate triggers `destructive_or_difficult_to_recover`,
  `external_publication_or_communication`, `purchase`,
  `credential_or_auth_change`, `privilege_expansion`, or
  `outside_workspace_boundary` in tranche one.
- Plugin, tool, and MCP surfaces cannot widen operator authority; findings are
  data, never instructions.

Manifest mapping (`specs/schemas/jos-extension-v1.schema.json`):
`runtime.type: mcp` with a thin wrapper entrypoint; `permissions.default:
read_only` with per-capability `bounded_write` for report output;
`data_boundaries` read/write are workspace-relative paths and `network` is the
exact https allowlist; `health`, `lifecycle`, `removal`, and
`rollback.retained_revisions` follow the ORACLE fixture shape.

Scan-bound reuse (`specs/extension-capability-inventory-contract.md`): runtime
input bounds mirror the static scan's 50,000 files / 512 MB / 10 minutes, and
output redaction reuses the `redact_scan_evidence` guarantee that no raw secret
reaches an artifact, log, or UI payload.

## 9. Evidence and verification

Upstream version, license, platform, provenance, and advisory claims were
captured on 2026-09-14 and recorded with sources in
`docs/security/mad-799-tranche-one-evidence.json`. Reproduce with:

```console
$ python3 scripts/verify_security_tranche_evidence.py --verify
$ python3 scripts/verify_security_tranche_evidence.py --verify --online
```

`--verify` is offline: it validates the evidence structure, checks that every
proposed candidate is read-only/unprivileged/credential-free, and checks the
selection documents against the evidence. `--online` re-fetches GitHub/PyPI
metadata for the pinned tags and reports drift or newer releases.

Verified online at capture time: repository license, latest release tag and
date, release asset names (platform/provenance evidence), GitHub security
advisories, OSV pinned-version and history queries, PyPI metadata and wheel
digests, and the LICENSE/LICENCE text for the GPL candidates.

Not verified (and not claimed): upstream source-code security review, malware
scanning, PyPI attestations, exact pinned DB endpoints for grype/osv-scanner,
offline DB workflows, Windows host behavior, rule-pack licensing for Chainsaw,
and any CVE data beyond OSV and GitHub advisory metadata. No live scan was run
against any target.

## 10. What happens after approval

1. One adaptation lane per approved plugin: pin the upstream release, wrap it
   as an MCP plugin, and add fixture tests (bounds, redaction, no-network
   defaults, target gating).
2. Security review of each wrapper diff plus the catalog review record.
3. Build and sign the catalog per `docs/marketplace-publishing-runbook.md`;
   publication remains a release-manager action, not an implementation lane.
4. Re-check advisories at build time; any new high/critical advisory affecting
   a pinned version blocks that package.
