# Read-only Calendar tool

`read_calendar` is the bounded, admin-only Calendar facade for agent and voice integrations. It uses the authenticated owner from the tool execution context and supports only:

- `list_calendars`
- `list_events` with explicit ISO `start` and `end` values

The tool attempts an owner-scoped CalDAV pull before reading the local owner-scoped cache. That pull may refresh cached CalDAV rows, but the tool never creates, updates, or deletes a user event. If the pull fails or CalDAV is not configured, the cached read still succeeds with `calendar_freshness="sync_failed"` and a response beginning `Calendar freshness could not be confirmed; cached owner-scoped data follows.` Sync error details are not exposed. Because the refresh can update the local cache, `read_calendar` is disabled in plan mode even though its user-facing Calendar operations are read-only.

`max_results` defaults to 50 and cannot exceed 100. Event ranges must be ordered and cannot exceed 366 days. Returned text fields are treated as untrusted tool data and capped at 500 characters, the formatted response is capped at 20,000 characters, and truncation is reported through `data_truncated` and `response_truncated`.

Example:

```json
{
  "action": "list_events",
  "start": "2026-07-15T00:00:00",
  "end": "2026-07-16T00:00:00",
  "max_results": 25
}
```

Use `manage_calendar` for event creation, updates, and deletion. `read_calendar` has no dependency on a specific UI or voice implementation.
