# Generated CLI runtime — MAD-959

Base: MAD-958 / `0684a32dae382954a17fc94218ca596a89f8542c`.
This is source delivery in M17; release/deployment belongs to MAD-965.

## Contract

A service/inline package supplies a Python entrypoint under `.pandamonium/`,
matching namespaced schemas and `execution` metadata in `integration.json`.
The runner sends the tool name as argv[1], typed JSON arguments on stdin and
accepts typed JSON output. Every operation must have a real validation call;
deterministic calls can require an exact expected result. Setup uses at most
eight argv commands. Preview only reads data; the existing authority flow binds
approval to the package digest and visible setup/check recipe.

On Linux, bubblewrap isolates processes, mounts source read-only and exposes a
private runtime. Host homes, credentials and environment variables are absent.
The runtime requires `bwrap` and `prlimit` (`bubblewrap` and `util-linux` packages).
Host Python/system libraries are read-only. JSON Schema validation uses the
already installed `jsonschema` dependency; refs and unsupported schema constructs
are rejected. Missing required owner configuration prevents admission.

Setup has a cumulative 300-second budget. Calls use the manifest's 1–30-second
health timeout. Input and combined output are each bounded to 64 KiB. Per-process
limits are 8 GiB virtual memory, 256 MiB per file, 256 open descriptors and 300 CPU
seconds. Timeout/cancellation kills the process group and child namespace. These
are process/file bounds, not aggregate cgroup memory or disk quotas. Execution
is serialized; use per-package locks if throughput becomes a problem.
An approved buggy/malicious package can still exhaust host resources. Aggregate
memory/process/storage enforcement is a tracked MAD-960 resource requirement
before catalog/release execution; this source-only step does not claim it.

Network is disabled for an empty declaration; otherwise the sandbox shares host
network connectivity. Destination declarations are descriptive, not a firewall
allowlist. The approval preview states that limit. Package code has no access to
host credentials even when network is enabled.

Dependencies live in an owner/package-digest runtime. User HOME and artifacts
live beside the digest directories so upgrades and rollback retain data.
Validation overlays disposable HOME/artifact directories. Enable reruns checks;
disable/uninstall remove availability and preserve data. Every invocation checks
owner, enabled state, exact package/manifest identity, validation receipt, schema
and mounted revision. A changed package requires remounting. Text and voice use
the same runner; retained approvals carry the original mounted schema/revision
into the continuation turn and still pass current-state validation.

## Real source/package results — 2026-09-15

[Machine-readable source, package and operation receipts](mad-959-cli-corpus.json).
All three runs fetched real pinned GitHub source, built a prepared archive,
approved installation, used native discovery/mount or Skills, then passed
disable/enable/uninstall. Each temporary data directory was removed.

| Package | Actual operations | Limits |
|---|---|---|
| yt-dlp | Inspected and downloaded an actual generated WAV, 1,644 bytes | Direct-media URLs; optional credential-bearing provider modules and test/private-key files are explicitly excluded. Full site coverage is not claimed. |
| PandaFlix | Compiled pinned Go source; parsed episodes 2–4, produced a YouTube playback plan and read an empty disposable watch history | Linux x86_64; private checksum-verified Go 1.26.0; host C compiler/pkg-config/libmpv headers. Playback needs a player/display and is not claimed. |
| Ponytail | Admitted and read all selected SKILL.md bodies through native manage_skills | Instructions remain skills; no fake callable skill tools. |

The reviewed package recipes are under `integrations/repository-packages/`.
Core code has no repository-name dispatch. The scanner reads and validates their
source evidence through the same bounded proposal protocol. The real yt-dlp scan
exposed source credentials and Python-expression false positives. Detection now
distinguishes literal assignments from function calls. Explicit package exclusions
retain the secret gate; the remaining source must still pass it.

Reproduce with an empty disposable runtime (created/removed by the script):

```sh
.venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/yt-dlp/proposal.json
.venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/pandaflix/proposal.json
.venv/bin/python scripts/validate_cli_package.py --url https://github.com/dietrichgebert/ponytail --ref e3ba2aa6f1e6f0bc4d69eb09c9f0d0a93af56156
```

## Agent/browser evidence and limits

A fresh authenticated uvicorn instance at localhost completed the visible URL
scan, exact recipe/exclusion preview, approval and yt-dlp installation. Installed
plugins showed both namespaced capabilities after a service restart. The same
browser-created admin then used the real native agent/authority path to download
the WAV, with a 1,644-byte artifact verified on disk. The retained
[agent result](mad-959-installed-agent.json) records the successful native result
and approval receipt. The model reply transport was deterministic in this
disposable instance; its source fetch, archive, install, validation and calls
were real. The earlier browser connection failure was resolved through the
available computer-use browser interface.

The regression check runs the real agent loop, native discovery and dispatch,
authority approval, then the exact retained-call continuation against a real
isolated process (13 → 26). It also verifies voice dispatch, owner confinement,
typed input/output, timeout/cancellation, output limits, tamper rejection, restart,
same-source package upgrade, failed-upgrade preservation, rollback and retained
user data. Run `pytest tests/test_extension_cli_adapter.py`.

Semantic model replies and the agent's tool-selection frames in these checks are
deterministic fixtures. No external model endpoint was configured locally; these
checks prove actual package/runtime/agent execution, not live-model semantic
accuracy. Broader provider/corpus/held-out acceptance remains in MAD-963/MAD-964.
No personal-account packages, service deployment or public catalog entries changed.

Local verification: **6,446 Python passed / 5 skipped**, 94 focused lifecycle,
intake and authority checks, and 7 Chromium intake/installed checks passed.
Ruff passes the new/scoped modules, with no added diagnostics in the larger
pre-existing route/agent modules; five runtime/intake modules pass scoped mypy.
The plugin-view module retains its existing 27 mypy diagnostics. Node setup-copy
checks, JavaScript syntax, isort and diff hygiene pass. Protected PR CI is the
final publication gate.

Ubuntu CI also installs and loads the distribution's `bwrap-userns-restrict`
profile from [`apparmor-profiles`](https://packages.ubuntu.com/noble-updates/all/apparmor-profiles/filelist).
This permits bubblewrap's namespace setup while denying capabilities to its
children; the global AppArmor/user-namespace restrictions remain enabled.
A real unprivileged namespace preflight runs before pytest. Without a suitable
host profile, Ubuntu can reject setup with `Failed RTM_NEWADDR`; installing the
binary alone is insufficient. No unsandboxed execution fallback is provided.

Rollback: revert this scoped source change while preserving extension runtime data.
