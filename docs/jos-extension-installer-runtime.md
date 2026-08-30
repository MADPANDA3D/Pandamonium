# JOS Pinned Extension Installer Runtime

**Linear:** `MAD-752`

Odysseus now exposes an approval-gated source-package flow without introducing
a package manager, daemon, dependency, or second tool runner.

## Operator flow

1. `POST /api/extensions/plans/source` accepts `install` or `upgrade`, a
   supported public HTTPS Git URL, and `HEAD`, a branch, tag, or advertised full
   commit.
2. Odysseus resolves the ref to a full immutable revision, checks it out under
   the external managed extension root, validates `jarvis-extension.json`, and
   verifies the declared source and `self`/exact revision binding.
3. The preview returns requested permissions and lifecycle argument vectors and
   creates an exact P5 approval decision. No package or runtime command runs.
4. The existing `/api/authority/decisions/{decision_id}` endpoint records the
   operator approval.
5. `POST /api/extensions/plans/{plan_id}/execute` consumes that approval,
   validates the server-owned P4 call, requires a declared runtime adapter,
   verifies health/catalog, then activates registry metadata.

Enable, disable, rollback, and recoverable uninstall use
`POST /api/extensions/plans/lifecycle` followed by the same approval and execute
steps. `GET /api/extensions` returns bounded package/plan state without internal
staging paths.

## Git and filesystem boundary

- Production source forms are canonical HTTPS repositories on GitHub, GitLab,
  or Codeberg; credentials, query strings, fragments, non-default ports, SSH,
  local paths, private hosts, unsafe refs, and mutable unresolved refs fail
  closed.
- Git runs with argument arrays, no shell, no terminal prompting, no system or
  global Git configuration, and no LFS smudge. Submodules and repository setup
  scripts are never run.
- Checkout size and file count are bounded. The manifest must be a small regular
  file, not a symlink.
- The managed root defaults outside the source checkout and can be set with
  `ODYSSEUS_EXTENSIONS_DIR`. A root inside Odysseus or a broad home/root target
  is rejected.

## Lifecycle and recovery

The only built-in adapter accepts a static web entry point with inline schemas
and empty install/start/stop/remove vectors. Every other runtime stops with a
clear adapter requirement. An adapter is trusted Odysseus code; manifest text
cannot make itself executable.

Catalog registration happens only after pinned checkout, manifest validation,
adapter health/catalog validation, and activation succeed. A failed first
install exposes no catalog. A failed upgrade leaves the previous revision and
catalog active. Old revisions remain available for rollback. Uninstall removes
the active registry record and moves the managed package into a recoverable
`removed` area rather than deleting it.

Every lifecycle plan uses the existing P4 validator, P5 exact approval receipt,
and P7 operational events. Completed plans return their prior result on replay;
enable, disable, same-revision upgrade/rollback, and uninstall are idempotent.

## Boundaries

Tests use temporary local Git repositories through an injected transport map;
they make no internet or private-infrastructure calls. No candidate repository,
ORACLE source, service, container, deployment, or live extension installation
is touched by this source slice.
