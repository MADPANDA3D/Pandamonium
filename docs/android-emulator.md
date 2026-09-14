# Governed Android emulator / ADB adapter

`android_device` is the bounded, admin-only Android facade for agent integrations
(MAD-838). It discovers the installation's Android SDK, inspects connected
emulators/devices, runs AVD lifecycle operations, and performs explicit
developer actions through a fixed, allowlisted command surface. It never
exposes an arbitrary `adb shell`, a host shell, or a destructive device
operation.

## Boundary

- Execution happens where the adapter runs (a native Pandamonium install on the
  workstation, or any host where the configured SDK is reachable). The server
  keeps its existing workstation-bridge boundary: no workstation filesystem is
  mounted into the app/container, and no Android SDK is required in the base
  image.
- The adapter is a policy + execution core (`src/android_emulator.py`) plus a
  thin tool wrapper (`src/agent_tools/android_tools.py`). All commands are
  argv-only; `shell=True` and `os.system` do not appear in either module.
- The tool is registered like the other governed adapters (schema, RAG
  description, catalog, non-admin blocklist, authority effect, prompt section).
  Removing that registration removes the capability without touching anything
  else.

## Configuration (installation-owned)

Resolution order, highest first:

1. `PANDAMONIUM_ANDROID_SDK_ROOT`
2. `ODYSSEUS_ANDROID_SDK_ROOT`
3. `ANDROID_SDK_ROOT`
4. `ANDROID_HOME`
5. The `android_sdk_root` setting (settable through `manage_settings`)
6. PATH fallback (`shutil.which`) for `adb` / `emulator` / `sdkmanager`

A configured-but-missing directory resolves to "unavailable" instead of being
trusted. With nothing configured, `status` says exactly what to set and every
device action fails closed.

`PANDAMONIUM_ANDROID_AVD_ALLOWLIST` (or the `android_avd_allowlist` setting) is
an optional comma/newline list of AVD names that may be started. Empty means any
*installed* AVD with a valid name may be started. A name outside the allowlist
fails with `avd_not_allowed` even when installed.

## Actions

The agent calls `android_device` with a JSON object; `action` defaults to
`status`.

| Action | Arguments | Notes |
|---|---|---|
| `status` | — | SDK resolution, allowlist, honest unavailable copy. Exit 0 even when unavailable. |
| `devices` | — | Serial, state (`device`/`unauthorized`/`offline`), model, Android/API, boot state; emulator AVD name. |
| `avds` | — | Installed AVDs with allowlist flags. |
| `start` | `avd`, optional `headless` | Installed + allowlisted AVD only. Spawns the emulator detached, returns pid and log path. |
| `stop` | `serial` | `adb -s <serial> emu kill` after confirming the serial is ready. |
| `reboot` | `serial` | `adb -s <serial> reboot`. |
| `wait` | `serial`, optional `timeout` (5–600s, default 120) | Polls `sys.boot_completed`; cancellable with `cancel`; bounded. |
| `install` | `serial`, `apk` | Absolute existing `.apk`; install failure surfaces the bounded package-manager reason. |
| `launch` | `serial`, `package` | Launcher intent via fixed `monkey` argv. |
| `force_stop` | `serial`, `package` | `am force-stop`. |
| `deep_link` | `serial`, `url`, optional `package` | `am start -W -a android.intent.action.VIEW -d <url>`; the URL is validated and device-shell quoted. |
| `screenshot` | `serial` | `exec-out screencap -p`, saved under `data/android_evidence/` with size + sha256. |
| `record` | `serial`, optional `seconds` (1–180, default 15) | `screenrecord` then `pull`, save evidence, remove the device-side file. |
| `logcat` | `serial`, optional `lines` (1–2000, default 200), optional `tag` | Dump (`-d`) only; never follows. |
| `input` | `serial`, `input` = `tap`/`swipe`/`text`/`key` (+ coordinates/text/key) | Coordinates 0–10000; keys from a fixed allowlist; text charset-bounded. |
| `cancel` | `serial` | Signals an in-flight `wait` for that serial; honest no-op when none is running. |

Success results carry `output`, `state`, `source`, `citation`, `limits`, and
`truncated` (plus `evidence` for screenshot/record). Failures return an honest
`code` and message: `unavailable`, `invalid_action`, `serial_required`,
`invalid_serial`, `device_not_found`, `device_unauthorized`, `device_offline`,
`busy_serial`, `timeout`, `cancelled`, `invalid_avd`, `avd_not_installed`,
`avd_not_allowed`, `invalid_package`, `invalid_apk`, `apk_not_found`,
`invalid_url`, `invalid_text`, `invalid_key`, `invalid_coords`, `invalid_tag`,
`install_failed`, `output_too_large`, `command_failed`.

## Security contract

- **Explicit serial targeting.** Every device action requires a serial that must
  appear in `adb devices` and be in the `device` state. Unauthorized, offline,
  and unknown serials fail closed with actionable copy.
- **One serial, one critical section.** Concurrent commands against the same
  serial are rejected with `busy_serial` rather than racing. `cancel` is
  deliberately lock-free so it can stop an in-flight `wait`.
- **Allowlisted argument schemas.** Free-text leaves (URL, input text, logcat
  tag) are charset-validated and POSIX-quoted for the device shell. Serials,
  AVD names, package names, keys, and coordinates are pattern- or range-bound.
- **Bounded everything.** Hard timeouts per action, hard output ceilings
  (64 KiB text/listing, 24 MiB screenshot, 256 MiB recording), kill on timeout
  or caller cancellation, and screen recordings are size-checked before saving.
- **No destructive surface.** `uninstall`, `clear-data`, `wipe`, `pm`, and raw
  `shell` are not implemented at all — an unknown action returns
  `invalid_action`. Credential entry and Play Store automation remain out of
  scope.
- **Audit.** Every attempt appends a redacted, bounded JSONL entry to
  `data/android_audit.jsonl` (action, state, serial, actor, bounds/detail).
  Secret-shaped stderr is redacted before it reaches a result message.
- **Admin-only.** The tool is in `NON_ADMIN_BLOCKED_TOOLS`, blocked in plan
  mode, and classified by the authority gate: inventory/wait/logcat/screenshot
  are reads; lifecycle/install/launch/deep-link/input are reversible writes.

## Operator acceptance (headed, on the workstation emulator)

This is the live-acceptance half of MAD-838; it must run where the emulator is
installed. Fixture tests already prove command construction and fail-closed
behavior.

1. Start the SDK and confirm discovery: ask the agent to run
   `android_device {"action": "status"}`; the SDK root and `adb` path must match
   the workstation install.
2. Confirm inventory: `android_device {"action": "devices"}` lists the running
   emulator with model, Android/API version, boot state, and (for an emulator)
   the AVD name.
3. Start an AVD: `android_device {"action": "start", "avd": "<AVD>"}`, then
   `android_device {"action": "wait", "serial": "<emulator-5554>", "timeout": 180}`.
   The pill/transcript must show the target citation and the boot wait must end
   with `state: ready`.
4. Install and exercise an app: `android_device {"action": "install", "serial": "...", "apk": "/abs/app-debug.apk"}`,
   `{"action": "launch", "package": "com.example.app"}`, and
   `{"action": "force_stop", "package": "com.example.app"}`.
5. Capture evidence: `{"action": "screenshot", "serial": "..."}` and
   `{"action": "record", "serial": "...", "seconds": 5}`; confirm the evidence
   files exist under `data/android_evidence/` and are named in the result.
6. Input + deep link: `{"action": "deep_link", "serial": "...", "url": "https://example.com"}`,
   then an explicit `input` tap/key; confirm the emulator reacts.
7. Failure honesty: unplug/deny the device and confirm `device_unauthorized`;
   run `{"action": "screenshot", "serial": "bogus"}` and confirm
   `device_not_found`; start a `wait` and call `{"action": "cancel", "serial": "..."}`.
8. Stop cleanly: `{"action": "stop", "serial": "..."}`; the emulator process must
   exit. CT103 data, credentials, and unrelated services must remain untouched
   throughout.

## Rollback

Disable the tool in Settings → Built-in Tools or via
`manage_settings {"action": "disable_tool", "tool": "android_device"}`; the
adapter then returns "disabled by user" and nothing else changes. Removing the
registration (handler, `TOOL_TAGS`, schema, index/catalog entry, blocklist,
authority branch, prompt section) removes the capability entirely. The adapter
never modifies the SDK, AVDs, or the workstation bridge configuration, so
rollback has nothing else to undo.

## Follow-up slice (not in this PR)

The governed control plane above is transport-agnostic: `AndroidCommand`
values are pure data. The remaining work for a CT103-hosted install is a small
workstation-bridge transport that executes those plans on the workstation
(where adb/emulator live) and streams their bounded results back, plus the
matching capability registration in the bridge protocol. That transport is a
separate, independently reviewable change; this PR deliberately does not add a
new bridge dependency.
