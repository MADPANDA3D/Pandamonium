# Draft upstream RFC packet: small host seams for Voice Orb

> **Posting status:** Public-safe draft for review in the maintained fork. Do not post this packet upstream before the v0.3 demo and release evidence exist. Refresh every dated value in the compatibility section before posting.

## Purpose

Voice Orb is a maintained-fork demonstration of authenticated voice, bounded foreground actions, read-only Calendar context, and user-initiated camera/media controls. This packet proposes four small host seams that are independently useful to Odysseus core. It does **not** propose merging the full fork delta, publishing a plugin wheel, or declaring a stable plugin ABI.

The immediate goal is to make existing in-tree features compose through explicit contracts while Odysseus internals are still changing. External distribution can be reconsidered only after those contracts are stable and the project has made an explicit trust, compatibility, and governance decision.

## Current upstream context

Snapshot verified 2026-07-15 against the canonical [`odysseus-dev/odysseus`](https://github.com/odysseus-dev/odysseus) repository:

| Upstream thread | Current state | Relevance to this packet |
|---|---|---|
| [Extension discussion #4439](https://github.com/odysseus-dev/odysseus/discussions/4439) | Active Ideas discussion; GitHub rendered 9 comments and 32 replies | A recurring thread favors thin, dogfooded seams before external plugins and calls out the danger of freezing unsettled internals; no stable external contract has been accepted. |
| [Plugin-contract PR #4241](https://github.com/odysseus-dev/odysseus/pull/4241) | Open, unmerged, non-draft PR with four commits targeting `dev` | Its dogfood review validates some facade ideas but still identifies unfinished tool/provider dispatch and chat-render boundaries. It is design input, not a released ABI. |
| [Hands-free voice issue #4118](https://github.com/odysseus-dev/odysseus/issues/4118) | Open; `enhancement` and `ready for review`; unassigned; zero comments or linked development | It proposes a continuous STT → chat → TTS loop using existing services. Voice Orb is related prior art, but this packet does not replace or claim that issue. |

The conservative interpretation is: stabilize the smallest core-owned adapters first, dogfood them in-tree, and keep external plugin packaging closed until compatibility and security boundaries are real.

## Maintained-fork status

- Distribution: maintained AGPL-3.0-or-later fork, not an installable plugin.
- Compatibility: the machine-readable record names an exact canonical upstream commit; `plugin_abi` remains `null`.
- Default behavior: normal Odysseus chat remains available with no worker configured.
- Optional surfaces: Voice Orb uses existing owner-scoped model/provider settings; workers are fixed, disabled by default, and read-only.
- Public boundary: source, neutral tests, first-party assets, and setup documentation only. Credentials, operator topology, personal/business data, cloned voices, and unlicensed media are excluded.

This RFC asks upstream to evaluate four contracts, not to adopt the fork's product surface or maintenance burden.

## Bounded demo scope after v0.3

Use a clean, authenticated deployment and neutral demonstration data. The recording should show only the following sequence:

1. Start a linked voice conversation through the current/default Odysseus model.
2. Demonstrate interruption and End Voice stopping microphone and playback.
3. Run the exact foreground actions `open Calendar`, `what view is open?`, `minimize this document`, and `close this document`.
4. Ask one read-only Calendar question and show either fresh synchronized results or the explicit stale-data warning.
5. Say `Open your eyes.`, then separately `What do you see?`, and finally `Close your eyes.` to demonstrate permission, one-frame analysis, and track shutdown.
6. Say `I need something motivational.` to play the checksummed, same-origin, silent demonstration clip, then stop it by switching modes or ending Voice.
7. Optionally show one neutral read-only worker task, clearly separated from the host-seam request.

Do not demonstrate arbitrary DOM control, Calendar mutation, background capture, continuous recording, face recognition, arbitrary media URLs, remote code installation, approval elevation, or any operator-specific infrastructure.

## Threat model

| Boundary / threat | Minimum control demonstrated by the fork | Upstream seam requirement |
|---|---|---|
| Model output attempts arbitrary browser control | Server maps exact phrases to enumerated actions; client validates action ID, payload fields, and allowed target | Registry accepts named actions with strict payload schemas; no selector, script, HTML, or arbitrary URL channel |
| Client state leaks DOM or document content | Snapshot contains bounded logical view names, open/minimized flags, Calendar view/date, and an active document identifier | State providers return versioned JSON primitives under size limits; no raw DOM, HTML, selectors, or generic serialization |
| Cross-user or cross-origin voice request | Interactive authenticated session, same-origin check, owner-scoped linked chat, unknown fields rejected | Host hook preserves existing auth/owner checks and does not accept bearer-token control as an alternate path |
| Calendar text acts as prompt instructions | Read-only owner-scoped sync path, bounded normalized fields, freshness metadata, event text treated as data | Calendar facade exposes only `list_calendars` and bounded `list_events`; mutations stay outside the voice seam |
| Microphone or camera remains active unexpectedly | Browser owns permission; tracks stop on explicit close, End Voice, errors, permission loss, track end, page hide, or hidden-page transition | Lifecycle hook must expose deterministic stop/idle events and must not create a background-capture path |
| Frame or audio is retained unintentionally | One bounded JPEG/PNG frame is analyzed in memory; raw frame bytes are not persisted; raw microphone audio is not intentionally retained | Media hooks must document transit and retention and keep raw capture outside diagnostics, logs, and session state |
| Caller selects arbitrary media or filesystem content | Playback accepts one manifest ID mapped to a canonical same-origin file with MIME, provenance, license, and checksum verification | Static/media hook accepts registered IDs only; no caller URL, filesystem path, or remote script injection |
| “Plugin permissions” are mistaken for isolation | The fork is not a plugin; upstream PR #4241 describes in-process Python as pip-equivalent trust | No external plugin claim until provenance, updates, compatibility, and an explicit execution trust model are settled |
| Optional worker gains write authority | Fixed adapters, explicit configuration, enforced read-only capability, no caller elevation | Worker behavior is not part of this RFC; any future seam must preserve core ownership and least privilege |

The host application, its authenticated owner, and explicitly configured providers remain trusted. Model text, Calendar event text, browser-reported state, media inputs, and optional worker responses are untrusted data.

## Privacy and media lifecycle

| Data | Creation and transit | Retention | Stop / deletion boundary |
|---|---|---|---|
| Microphone audio | User opens Voice Orb and grants permission; audio goes to the selected browser, local, or endpoint STT path | No intentional raw-audio retention by Voice Orb; a local STT temporary file is deleted after transcription | Tracks stop on End Voice, interruption/error paths, page hide, or hidden-page transition |
| Transcript and reply | Stored through normal linked-chat behavior and sent to the configured model/TTS path | Normal chat, voice-session, backup, and retention rules apply | User manages it through normal Odysseus data controls |
| Synthesized speech | Browser TTS stays in the browser; server TTS may use the existing cache | Server-generated audio may remain in the normal TTS cache | Playback stops on interruption or End Voice; normal cache retention applies |
| Camera frame | One explicit describe command captures one JPEG/PNG frame and sends it with the authenticated voice request | Raw bytes remain in browser/request memory and are not written to history, diagnostics, uploads, or caches | Request memory is released after analysis; every track is stopped by the camera lifecycle controls |
| Camera stream | Top-level same-origin page owns the native video element; no audio track is requested | No continuous sampling or recording | `Close your eyes.`, End Voice, error, permission loss, track end, page hide, hidden-page transition, or media-mode switch |
| Built-in clip | Manifest ID resolves to one same-origin, silent, checksummed asset with public provenance/license | Asset is distributed with the release and excluded from service-worker precache | Switching to camera, stopping media, or ending Voice clears playback |
| Client-state snapshot | Collected at request time from named logical views | Not a DOM dump and not retained as a separate browsing-history stream | Snapshot lifetime is the request unless normal chat metadata explicitly records a bounded result |

Provider choice matters: browser speech services and remote STT/TTS/vision endpoints may receive data under their own policies. Voice Orb does not silently select or prewarm a remote provider.

## Minimum host seams

Names below are illustrative, not an ABI commitment.

### 1. Enumerated foreground-action registry

Core modules should be able to register a stable action ID, strict JSON payload schema, and in-tree handler. Streaming consumers dispatch only registered IDs. Start by migrating the existing Calendar-open and document-close/minimize behavior without changing user-visible behavior.

Acceptance boundary:

- fail closed on unknown actions, targets, fields, or oversized payloads;
- keep authorization and foreground-state checks in core;
- expose no generic selector, JavaScript, HTML, URL, or DOM-command escape hatch;
- provide small server and browser contract tests.

### 2. Safe client-state snapshot

Core UI modules should contribute small, versioned state serializers to one snapshot. The host owns collection, field limits, and serialization. A consumer may request named state slices but may not inspect arbitrary page state.

Initial state is sufficient if it covers `active_view`, Calendar open/minimized/view/date, and document open/minimized/opaque ID. Content, selectors, HTML, file paths, tokens, and endpoint details are out of scope.

### 3. Owner-scoped read-only Calendar facade

Expose the already-core Calendar sync/list path through a narrow read facade. It should support only calendar enumeration and a bounded event window, return normalized fields plus freshness/error metadata, enforce owner scope internally, and cap result counts and field lengths.

Creating, editing, deleting, or rescheduling events is explicitly not part of this contribution. Untrusted event text must remain data, not instructions.

### 4. Stable voice/static lifecycle hooks

Document and test the smallest existing hooks needed to compose an in-tree voice surface: an app-owned mount point, stream-complete/TTS-idle signals, deterministic stop/interruption signals, and same-origin static-module loading owned by the application build.

This is not a remote loader. It must not add package discovery, a marketplace, Python entry points, arbitrary CSS/JS injection, or a compatibility promise for external wheels.

## Independent contribution sequence

| Order | Small contribution | Independent value | Explicit non-goal |
|---|---|---|---|
| 1 | Foreground-action registry plus migration of existing Calendar/document actions | Removes duplicated dispatch logic and creates one fail-closed UI-control contract | No Voice Orb UI, plugin manifest, or new action category |
| 2 | Versioned safe client-state snapshot with Calendar/document providers | Gives core features a bounded way to describe foreground state | No DOM/content introspection or telemetry stream |
| 3 | Owner-scoped read-only Calendar voice facade with freshness metadata | Reuses one secure read path across chat/voice callers | No Calendar mutation and no new credential store |
| 4 | Documented voice lifecycle events and app-owned static mount/module hook | Lets in-tree voice experiments reuse STT/TTS and teardown behavior | No plugin wheel, remote installer, discovery UI, or stable external ABI |

Each contribution should target current canonical `dev`, include focused tests and documentation, and be reviewable without any later contribution. If upstream declines one seam, the others should remain viable.

## Exact compatibility evidence

### Preparation snapshot — 2026-07-15

| Evidence | Exact value |
|---|---|
| Canonical repository / branch | [`odysseus-dev/odysseus`](https://github.com/odysseus-dev/odysseus), `dev` |
| Live canonical `dev` head | [`c80462e4621c1a3360e5441843bb83b4691a8766`](https://github.com/odysseus-dev/odysseus/commit/c80462e4621c1a3360e5441843bb83b4691a8766) |
| Fork compatibility record | `v0.2.0-alpha.1`, distribution `maintained-fork`, upstream base `c80462e4621c1a3360e5441843bb83b4691a8766`, `plugin_abi: null` |
| v0.2 integration base used for this draft | `fe8d31f0698975cf11dab8127ee29fa0767196d2` |
| Relationship to canonical base | Base is an ancestor; 15 integration commits; 66 files changed; 7,499 insertions and 96 deletions |
| Declared release platforms | `linux/amd64` and `linux/arm64` |
| v0.2 publication state | Source tag and container digest were not yet published when this snapshot was prepared; they must not be represented as released evidence |
| Local full-suite check | CPython 3.12: 4,657 passed, 3 skipped, 75 warnings |
| Local focused contracts | `test_foreground_control.js`, `test_voice_orb.js`, and `test_voice_orb_media.js` all exited 0 |
| Public/docs hygiene | Scrub self-test passed; full public scrub passed; diff, heading, table, local-link, and canonical-link checks passed |

The dated snapshot is orientation, not the release evidence to post after v0.3.

### Required refresh before posting

- [ ] Replace the preparation commit with the immutable v0.3 source tag and commit.
- [ ] Confirm the live canonical `dev` SHA and rerun the public-delta apply/drift check.
- [ ] Record exact full-suite, focused browser, Node contract, public scrub, dependency, secret-scan, Docker, and Trivy results from the v0.3 release run.
- [ ] Record the multi-architecture manifest digest and per-architecture image digests.
- [ ] Link a sanitized demo captured from the immutable v0.3 build.
- [ ] Recheck #4439, #4241, and #4118 for status or direction changes and update this packet.
- [ ] Remove this posting-status note and any stale preparation-only value.

## Proposed upstream ask

1. Confirm whether the four seams are useful as core-owned, in-tree contracts.
2. Agree that they should land as independent PRs, beginning with the foreground-action registry.
3. Keep external plugin distribution and ABI commitments out of scope until those seams have been dogfooded and upstream explicitly accepts the trust and compatibility model.

That is the whole request. Voice Orb can remain a maintained fork while the smallest generally useful contracts are evaluated on their own merits.
