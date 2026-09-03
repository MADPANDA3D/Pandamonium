# Foreground action contract

Pandamonium browser modules may register one of three version 1 foreground actions:

| Action ID | Core handler |
| --- | --- |
| `open_view:calendar` | Open Calendar |
| `close_view:document` | Close the visible or minimized document |
| `minimize_view:document` | Minimize the visible document |

Streaming consumers pass this exact `ui_control` data object to
`handleUIControl()`:

```json
{
  "ui_event": "foreground_action",
  "version": 1,
  "action": "open_view:calendar",
  "payload": {}
}
```

The registry rejects unknown versions, actions, fields, non-empty payloads,
messages larger than 1 KiB, duplicate registrations, and invocation before a
handler is registered. Registration returns an idempotent unregister function.

Action IDs include the only allowed target and version 1 payloads are empty.
This contract cannot carry a selector, URL, HTML, script, or generic DOM
command. Server-side authorization remains required before emitting an event;
the browser registry is an additional fail-closed boundary, not an auth layer.
