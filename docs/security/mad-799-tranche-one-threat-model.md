# MAD-799 — Tranche one threat model and exclusion list

Status: **research complete, awaiting Leo's approval** · Date: 2026-09-14
Companion documents: `docs/security/mad-799-tranche-one-selection.md`,
`docs/security/mad-799-tranche-one-evidence.json`

This threat model covers the six proposed read-only plugins
(`syft-sbom`, `grype-vuln`, `osv-deps`, `gitleaks-secrets`, `checkdmarc-dns`,
`cyclonedx-reports`) plus the two conditional candidates (`chainsaw-logs`,
`testssl-tls`) if their conditions are met. It covers the plugin/wrapper/host
boundary, not the internal security of each upstream project.

## 1. Scope and assumptions

- Plugins run as the normal Pandamonium service user. No candidate needs root,
  sudo, raw sockets, kernel/device access, or container sockets.
- Upstream binaries are unmodified and pinned to a full revision; the only new
  code is the MADPANDA3D-owned wrapper and manifest.
- Targets are local workspace paths, one operator-named domain, or one
  operator-owned host:port. No ranges, lists, or third-party targets.
- Findings are untrusted data. They never become instructions, tool calls, or
  authority.
- The existing extension lifecycle, registry, authority protocol, and
  marketplace catalog stay authoritative; nothing in this tranche adds
  authority, a registry, a scanner daemon, or an auto-update path.

## 2. Trust boundaries

```
operator request
   │  (explicit read request authorizes read-only work)
   ▼
Pandamonium agent + authority protocol
   │  (tool schema, permission_mode=read_only, workspace scope)
   ▼
Plugin MCP wrapper (MADPANDA3D code)
   │  (bounded args, redaction, timeouts, no shell interpolation)
   ▼
Pinned upstream binary (unmodified)
   │  (local files, optional declared https endpoints)
   ▼
Target: workspace files / operator domain / operator host:port
```

- **Catalog trust:** Ed25519-signed catalog entries; artifact sha256 + size
  verified before any adapter sees bytes; unknown keys, expiry, revoked
  entries, mutable refs, and digest drift fail closed.
- **Data trust:** scanned content and upstream data (DB/advisories/DNS) are
  untrusted inputs at every stage.
- **Network trust:** only endpoints listed in `data_boundaries.network` may be
  contacted; the catalog preview shows them before installation.

## 3. Assets

| Asset | Why it matters |
|---|---|
| Workspace files and repositories | May contain proprietary code, credentials, and personal data |
| Secrets discovered by `gitleaks-secrets` | Live credentials; highest-impact output of the tranche |
| SBOM and dependency inventory | Reveals internal software supply chain |
| Operator domain names and DNS records | Reveals infrastructure; DNS queries are externally visible |
| Host integrity and service availability | Scanners can exhaust CPU, memory, disk, and network |
| Marketplace trust chain | A tampered catalog or artifact installs code |
| Agent context and chat transcript | Findings can inject text into the model context |
| Audit receipts | Evidence of what ran and against what target |

## 4. Threat table

| # | Threat | Vector | Impact | Control | Residual risk |
|---|---|---|---|---|---|
| T1 | Prompt injection via scanned content | Malicious repo file names, log lines, SBOM fields, or advisory text contain instructions aimed at the agent | Agent executes attacker intent inside operator scope | Wrapper bounds and escapes output; findings are labeled data; redaction before context; existing authority gates still apply to every follow-up action | Low; a model can still be socially influenced by plain text, so findings stay untrusted |
| T2 | Secret exfiltration through findings | `gitleaks`/`grype` output includes live credentials; a shared chat, log, or artifact leaks them | Credential compromise | Mandatory redaction for every agent-visible/logged/stored preview; raw findings owner-scoped; no network egress in gitleaks; retention/removal path | Medium where a secret is already committed to the scanned repo; the finding does not create the exposure |
| T3 | Resource exhaustion or scan bombs | Huge trees, zip bombs, FIFO/device files, symlink loops, deeply nested archives | Host slowdown or denial of service | Static-scan bounds reused at runtime (50,000 files / 512 MB / 10 min); skip special files; do not follow symlinks outside the workspace; per-invocation timeouts | Low; bounds are fail-closed but a 10-minute scan still consumes capacity |
| T4 | Workspace/path escape | Absolute paths, `..`, symlink targets outside the workspace | Unauthorized file read | Wrapper accepts workspace-relative inputs only; resolves real paths and rejects escapes; catalog preview shows read boundaries | Low; wrapper path handling must be fixture-tested |
| T5 | Network egress abuse | A compromised or misused wrapper contacts arbitrary hosts; DB/API URLs are attacker-controlled | Data exfiltration, SSRF, third-party scanning | `data_boundaries.network` https allowlist; wrapper has no arbitrary-URL argument; DB/API endpoints captured from the pinned binary and reviewed; offline mode default where possible | Low–medium for network-active candidates; endpoint pinning is adaptation work |
| T6 | Supply-chain tampering of upstream binaries | Release asset swap, account compromise, unpinned download | Malicious code runs as the service user | Full-revision pin; upstream checksums/signature assets where published (syft, grype, osv-scanner); our catalog sha256 + Ed25519 signature; no runtime binary self-update | Medium for candidates without upstream signatures (cyclonedx-reports, chainsaw, testssl.sh); mitigated only by catalog signing and review at adaptation |
| T7 | Malicious catalog update or version drift | Forged catalog entry, replay, expired key, revoked-but-reinstalled package | Installs malicious plugin version | Existing fail-closed catalog controls: trusted keys, expiry, revocation, immutable refs, digest checks, rollback retained revisions | Low; inherited from the platform contract |
| T8 | Capability/permission confusion | Wrapper claims read-only but writes outside its results directory, or hides a network call | Unreviewed side effects | Manifest `permissions.default: read_only` plus explicit `bounded_write` overrides; catalog preview shows permissions, network, config, dependencies before approval; registry reconciliation rejects undeclared overrides | Low; needs wrapper review and tests per plugin |
| T9 | Dual-use misuse against third parties | `checkdmarc-dns` or `testssl-tls` pointed at domains/hosts the operator does not own; bulk target lists | External scanning from the operator's network; legal/reputational harm | One target per invocation; ownership attestation for network-target tools; no list/range inputs; rate limits and timeouts; no wildcard sweeps or zone transfers | Medium for `testssl-tls` (conditional until gated); low for `checkdmarc-dns` |
| T10 | DNS/network side-channel observation | DoH resolver sees queried domains; target sees probe traffic | Metadata leakage | Document resolver endpoints in the manifest network boundary; prefer operator-configured resolver; no query caching outside plugin data | Accepted; lookups are inherently observable |
| T11 | Privilege escalation via wrapper bugs | Command injection in wrapper argument handling; shell interpolation | Code execution as the service user, possibly wider | No shell=True; argv arrays only; strict schema validation; argv strings bounded like the manifest lifecycle vectors; fixture tests for injection | Low; wrapper is MADPANDA3D-reviewed code |
| T12 | False confidence / missed findings | Stale vulnerability DB, incomplete rule packs, offline mode silently degraded | Operator believes a clean scan means safe | Every report names the DB/rule/version timestamp and offline state; "unknown" is reported as unknown; catalog review advisories list known scanner issues | Medium; scanners are advisory, not assurance |
| T13 | Agent authority confusion | A tool description claims an effect broader or narrower than reality | Wrong approval behavior | Action effect comes from the concrete call and arguments, not the tool name; wrappers declare `read_only` truthfully and the authority protocol enforces the matrix | Low; inherited from the platform contract |
| T14 | GPL/AGPL compliance failure | GPL-family binary distributed without license/source obligations | License violation for the project | GPL candidates stay conditional until the package ships upstream LICENSE + pinned source offer; tranche-one default is Apache-2.0/MIT | Low after packaging review |

## 5. Explicit exclusion list

These classes are barred from tranche one and should stay bar/red-flagged in
the catalog unless a separate, explicitly approved security issue changes the
policy. Representative examples are categories, not an exhaustive tool list.

| Excluded class | Representative examples | Rationale |
|---|---|---|
| Active exploitation | exploitation frameworks, exploit payload generators, weaponized PoC runners, exploit validation against third parties | Can compromise targets; outside defensive scope |
| Credential attacks | brute-force, password cracking, credential stuffing, relay/coercion tooling | Attacks authentication; can lock out or breach accounts |
| Persistence | implant/C2 frameworks, startup or scheduled-task persistence installers | Establishes durable unauthorized access |
| Evasion | AV/EDR bypass, log tampering, obfuscation/packing frameworks | Defeats defensive controls and evidence integrity |
| Destructive response | automatic quarantine/delete, firewall/account/registry mutation, process killing in response to findings | Mutating response causes data loss and requires the separate destructive gate; tranche one never auto-remediates |
| Unrestricted scanning | internet-wide or CIDR-range scanners, mass enumeration, third-party vulnerability scans, target-list driven scanning | Unbounded external effects and legal exposure; only single operator-owned targets are allowed |

Additional selection exclusions: privileged/raw-socket packet capture and host
agents needing kernel/device access (deferred, not merely unselected); any tool
requiring cloud API credentials (tranche one has no secrets); any tool without
a pinned release artifact unless the plugin package pins the source tag itself.

## 6. Required pre-install disclosure

Before installation, the catalog/plan preview must show, from the signed entry
and embedded manifest:

- exactly one job summary and the tool names it adds;
- `permissions.default` and every capability override, with effects in the
  canonical vocabulary (`read` for tranche one);
- read/write path boundaries (workspace-relative only) and the exact network
  allowlist;
- configuration keys with `secret` flags (tranche one: all false) and no values;
- artifact sha256, size, signature, publisher key, and source revision;
- compatibility platforms/architectures and the `restart_required` value;
- review state, reviewer, review time, and any `security_advisories`;
- offline/degraded behavior and the findings retention/removal path.

If any required row cannot be rendered, the plan fails closed.

## 7. Residual risk and monitoring

- Accepted residual risks: T2 (committed secrets already exist), T6 for
  unsigned upstream releases (mitigated by catalog signing and review), T9 for
  `testssl-tls` only if approved with the target gate, T10 (observable DNS).
- Each plugin records an operational receipt per run (target kind, bounds,
  version, DB/rule timestamp) without secret values or raw paths.
- Advisory watch: re-query OSV and GitHub advisories at catalog build time;
  a new high/critical advisory affecting a pinned version blocks publication
  for that package and triggers the revocation path if already published.
- Any new candidate that is mutating, privileged, network-active beyond a
  lookup, or dual-use moderate requires a new threat-model row and Leo's
  explicit approval before selection; this document does not pre-approve it.

## 8. Approval asks

1. Approve candidates 1–6 as tranche one.
2. Decide on `chainsaw-logs` and `testssl-tls` conditions (section 5 of the
   selection document).
3. Reaffirm the exclusion list as the standing bar for the security category.
4. Confirm the target-ownership attestation requirement for any
   network-target tool before its adaptation lane starts.
