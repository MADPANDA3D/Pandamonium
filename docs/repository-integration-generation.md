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
evidence containing the argument name and a matching explicit source type. This
lexical check rejects unrelated quotes; executable validation must still establish
correct behavior. Undocumented/inferred types require further validation instead
of a fabricated typed package. Incidental development skills cannot substitute for an application's
purpose. Repository instructions are untrusted; no repository command runs during
analysis. Native descriptors are reused; core code has no repository-name cases.

## Package contract

Generated Python adapters, Go bridges, JSON descriptors and Markdown live under
`.pandamonium/`; source files cannot be overwritten. The generated manifest uses
an exact upstream URL/revision. `.pandamonium/integration.json` records purpose,
interfaces, setup and validation requirements. The scanner retains a deterministic
`package.tar.gz` and removes its duplicate prepared source tree under the managed data
directory. A package digest distinguishes separate integrations of one upstream
revision. Source-plan installation verifies that archive, manifest and tree digest
and passes the same bytes into the existing installer and authority flow; it does
not reacquire source from Git. Extracted prepared packages can use the existing publisher.

Inline adapters use `python .pandamonium/adapter.py <tool_name>`, a JSON arguments
object on stdin and a JSON result on stdout. Python syntax, declared schemas,
paths and quoted evidence are checked without importing or running the adapter.
Generated tools require `external_side_effect` permission until separately
validated; model text cannot grant read-only execution authority. Runtime
execution/admission for CLI packages uses the [private CLI runner](mad-959-cli-runtime.md).
Long-lived services and interactive runtimes belong to MAD-960.
An unsupported runtime fails at the existing adapter gate and mounts no tools.

**Needs validation** and **Needs setup** are preparation states. Neither means a
generated operation works. Dependencies, devices and external services stay in
requirements; owner configuration has key descriptions and secret/required flags,
never credential values. Runtime data, environments and credentials belong outside
the immutable package tree. No dependencies or lifecycle commands run at intake.

## Bounds and recovery

- At most two active scans; source limits remain 50,000 files / 512 MiB / 10 minutes.
- Starting a scan or reading a package prunes terminal scan data older than 24
  hours, above 32 records, or above 1 GiB of archives, keeping the newest that fit.
  Up to two active scans are excluded; cleanup never targets installed packages.
  An expired package requires a new scan. No periodic service is added.
- At most 2,000 indexed filenames, 160,000 excerpt characters, 24,000 characters
  per selected file, 200,000 response characters and four model calls.
  `find_text` requests an 8,000-character window around a literal symbol outside
  the first excerpt, within the same total budget and source-read bounds.
- At most two repairs after an initial invalid proposal; actionable validation
  errors feed the next attempt. Provider calls have a 90-second timeout, one
  gateway attempt per candidate, and use the existing configured fallback chain.
- Cancel stops model work, prevents package admission and cleans temporary source
  and package files. Git transport remains bounded by its existing command timeout.
- Progress is atomically persisted. Reopening/reloading the tab restores the scan
  ID; owner checks protect readback, cancellation and installation. A server
  restart converts an orphaned running scan into an explicit interrupted failure.
- Confined source symlinks are materialized as ordinary files/directories within
  the scan's byte/file/time bounds. External targets, cycles, nested directory
  links and `.git` targets fail closed. Distribution archives still reject links.
- Secret findings, private files, unsafe links, invalid evidence, malformed schemas,
  generated syntax errors and archive tampering fail closed. Start a new scan after
  repairing source or configuration; there is no silent fallback to invented tools.
  Secret findings retain a path/type and a redaction marker, never the matched value.
  Explicit `source_exclusions` can remove at most 128 optional regular files from
  the disposable package. Required interface evidence and license notices remain;
  the retained source is audited again under the same secret gate. Exclusions are
  preserved in integration metadata and the execution approval preview.

See the [complete 30-entry inventory](mad-963-plugin-inventory.md) for exact
capability coverage, published packages and outstanding setup/license work, and
the [approved M17 plan](repository-plugin-ingestion-plan.md) for holdout and release
acceptance.
