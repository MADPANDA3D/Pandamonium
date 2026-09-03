# In-tree voice lifecycle hooks

Odysseus exposes a small, core-owned browser contract for composing in-tree
voice interfaces with the existing recorder, chat stream, and TTS modules. It
does not expose an external plugin ABI or a remote module loader.

## Mount and modules

The app shell owns `#odysseus-voice-surface-root`. In-tree code may request it
with `getVoiceSurfaceRoot()` and may load only the fixed module IDs returned by
`listVoiceStaticModules()`. The module map contains source-controlled,
same-origin imports; callers cannot provide a URL, path, script, or package.

Adding another module requires an ordinary reviewed source change. Runtime
discovery, arbitrary CSS or JavaScript injection, Python entry points, and
external wheels remain out of scope.

## Lifecycle signal

Subscribe with `subscribeVoiceLifecycle(listener)` or listen for the browser
event `odysseus:voice-lifecycle`. Each frozen payload has `version: 1`, a
bounded event `type`, `source`, `reason`, and an optional opaque `sessionId`.
Unknown events, fields, sources, reasons, and oversized identifiers fail
closed.

Current event types are:

- `capture-started` and `capture-stopped`
- `stream-complete` and `stream-interrupted`
- `tts-started` and `tts-idle`

Signals contain lifecycle metadata only. They do not include transcripts,
audio, DOM content, model output, provider endpoints, credentials, or file
paths. Subscriber failures are isolated from the core recorder/chat/TTS path.

## Compatibility scope

The version describes the payload shape for in-tree consumers at the current
source revision. It is not a promise that an independently installed package
can load against future Odysseus versions. A stable external extension ABI
requires a separate trust, provenance, permission, update, and compatibility
decision.
