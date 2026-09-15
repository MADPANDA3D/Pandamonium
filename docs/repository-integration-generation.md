# Source understanding and prepared integrations

**Add a new plugin** reuses a valid committed `jarvis-extension.json` or admitted
native skill layout first. Otherwise the scanner uses the signed-in owner's
configured Background Tasks, Utility and Default model candidate chain. This is
foreground user work: keeping the page open and polling progress must not delay
model dispatch behind the scheduled-task quiet gate.

The model receives a bounded file index and source excerpts. It may request
additional files, then returns a strict JSON proposal with purpose, exact source
quotes, interface bindings, typed input/output schemas, setup keys, runtime
requirements and a disposable validation recipe. Every argument must have source
evidence. Incidental development skills cannot substitute for an application's
purpose. Repository instructions are untrusted; no repository command runs during
analysis. Native descriptors are reused; core code has no repository-name cases.

## Package contract

Generated Python adapters, JSON descriptors and Markdown live under
`.pandamonium/`; source files cannot be overwritten. The generated manifest uses
an exact upstream URL/revision. `.pandamonium/integration.json` records purpose,
interfaces, setup and validation requirements. The scanner retains the prepared
source tree and deterministic `package.tar.gz` under the scan's managed data
directory. A package digest distinguishes separate integrations of one upstream
revision. Source-plan installation verifies that archive, manifest and tree digest
and passes the same bytes into the existing installer and authority flow; it does
not reacquire source from Git. Prepared trees can use the existing publisher.

Inline adapters use `python .pandamonium/adapter.py <tool_name>`, a JSON arguments
object on stdin and a JSON result on stdout. Python syntax, declared schemas,
paths and quoted evidence are checked without importing or running the adapter.
Generated tools require `external_side_effect` permission until separately
validated; model text cannot grant read-only execution authority. Runtime
execution/admission for CLI and service adapters belongs to MAD-959/MAD-960.
An unsupported runtime fails at the existing adapter gate and mounts no tools.

**Needs validation** and **Needs setup** are preparation states. Neither means a
generated operation works. Dependencies, devices and external services stay in
requirements; owner configuration has key descriptions and secret/required flags,
never credential values. Runtime data, environments and credentials belong outside
the immutable package tree. No dependencies or lifecycle commands run at intake.

## Bounds and recovery

- At most two active scans; source limits remain 50,000 files / 512 MiB / 10 minutes.
- At most 2,000 indexed filenames, 160,000 excerpt characters, 24,000 characters
  per selected file, 200,000 response characters and four model calls.
- At most two repairs after an initial invalid proposal; actionable validation
  errors feed the next attempt. Provider calls have a 90-second timeout, one
  gateway attempt per candidate, and use the existing configured fallback chain.
- Cancel stops model work, prevents package admission and cleans temporary source
  and package files. Git transport remains bounded by its existing command timeout.
- Progress is atomically persisted. Reopening/reloading the tab restores the scan
  ID; owner checks protect readback, cancellation and installation. A server
  restart converts an orphaned running scan into an explicit interrupted failure.
- Secret findings, private files, symlinks, invalid evidence, malformed schemas,
  generated syntax errors and archive tampering fail closed. Start a new scan after
  repairing source or configuration; there is no silent fallback to invented tools.

See the [approved M17 plan](repository-plugin-ingestion-plan.md) for the remaining
runtime, marketplace, inventory and release batons.
