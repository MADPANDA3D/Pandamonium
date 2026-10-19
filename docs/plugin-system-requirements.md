# Plugin system requirements

Generated service/CLI packages run inside a bubblewrap sandbox. That sandbox
needs host capabilities, and a plugin store must not make the operator read docs
to discover them. The app now detects what a package needs, reports it in the
install preview, and can install it with one privileged run.

## Capability contract

Third-party packages may only **name capabilities** from the first-party catalog
in `src/system_requirements.py`. They can never supply package names or commands,
so an untrusted manifest cannot turn the installer into arbitrary root execution.

| Capability | What it provides |
|---|---|
| `sandbox.bubblewrap` | bubblewrap (`bwrap`) |
| `sandbox.prlimit` | util-linux (`prlimit`) |
| `systemd.user` | systemd user manager + cgroup v2 controls |
| `runtime.filesystem` | dedicated bounded filesystem at `ODYSSEUS_EXTENSION_RUNTIME_ROOT` |
| `build.c_toolchain` | C toolchain for packages that compile native code |
| `build.pkg_config` | pkg-config |
| `media.libmpv` | libmpv development files |

A package declares capabilities under `host_requirements.capabilities` in its
manifest or `.pandamonium/integration.json`. `service`, `cli` and `mcp` runtimes
always add the sandbox foundation. An unknown capability fails closed.

## Detection and preview

`host_requirements_summary()` is attached to every plan, so the Marketplace
preview shows a **System requirements** block with the present/missing
capabilities, the exact allowlisted packages for the detected distro, and
whether root is required. If requirements are missing, "Approve once" stays
disabled until they are satisfied.

## Provisioning (native Linux)

`POST /api/extensions/system-requirements` (admin-only) installs the missing
capabilities. The web UI opens a small sudo window, collects the password once,
and:

* pipes it to `sudo -S` on **stdin only** — never argv, environment, logs, or the
  response payload;
* installs only catalog-derived, allowlisted packages;
* creates, formats, mounts and persists the bounded
  `ODYSSEUS_EXTENSION_RUNTIME_ROOT` filesystem, then chowns it to the app user;
* runs `sudo -k` to clear cached credentials;
* records an operational audit event with the capabilities and packages.

The password is used for that run and discarded.

## Docker

A stock container cannot mount a filesystem or run systemd, so this cannot be
done from inside it. The Marketplace marks sandbox-required packages as
incompatible with container installs and tells the operator to install
Pandamonium natively. Simple web/skills plugins keep working in Docker.

## Deferred hardening

The provisioning route is admin-gated, audited, and bound to allowlisted
capabilities. Moving it behind the authority decision ledger (a distinct
separate gate that "Approve for me"/"Full access" can never auto-approve) is the
next step; see MAD-1010.
