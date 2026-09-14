# Guided in-app bug reports

**Report a bug** is a discoverable, privacy-reviewed way to file a bug in the
canonical GitHub repository while the problem is still on screen (MAD-856). It
opens from the composer **+** menu (`Report a bug`), the `/bug` slash command
(`/report` and `/feedback` are aliases), or the shared tool-window lifecycle.

On desktop it opens as a compact right-docked tool window (draggable,
resizable, minimizable to a dock chip, and remembered like every other tool
window). On mobile it is a full-width bottom sheet.

## What the reporter does

1. **Describe** — report type (bug / requested fix / product change / security
   vulnerability), a summary, what they were trying to do, expected behavior,
   actual behavior or error, ordered reproduction steps, a workaround, and an
   optional "anything else" field they review themselves.
2. **Evidence** — screenshots by paste, drag/drop, or file picker. Each one is
   previewed, labelled with the step it shows, recorded with the app route it
   was taken on, and can be reordered or removed. The allowlisted diagnostic
   bundle is shown in full and can be excluded with one checkbox.
3. **Review** — the exact public issue title and body, every attachment, the
   diagnostics, the duplicate search results, and a public-visibility warning.
   Nothing is submitted until the reporter ticks the confirmation box.

The whole capture session is a local draft: closing the panel, switching chats,
or reloading the app keeps the text and screenshot list. Screenshot bytes are
validated and stored server-side keyed by the draft id; only small thumbnails
live in the browser.

## What is collected automatically (allowlist)

Built server-side in `src/feedback_diagnostics.py` — nothing else is read:

- app version, revision, installation method, runtime (Python major/minor),
  platform class and release;
- the app route (path and hash only — query strings are dropped);
- browser class, viewport class, locale;
- the current chat session id and an optional request/correlation id;
- the most recent **controlled error classification** from existing
  operational events (category, status, component, timestamp — never raw
  exception text);
- service-health timestamps: the live API liveness check plus the last
  recorded health components.

## What is never collected

No keystrokes, message or document contents, prompts or completions, cookies,
authorization headers, API keys or passwords, console logs, arbitrary server
logs, background or continuous screenshots, unrelated screens, filesystem
reads, or private hostnames/URLs. There is no keylogging or screen recording.

## Redaction

User-typed text is passed through `src/authority_protocol.redact_secret_text`
plus URL-userinfo and credential-query stripping before it is displayed in
review or posted. Automatically collected strings additionally strip local
file paths, private hosts, URLs, and email addresses. The review screen shows
the redacted result, so anything removed is visible before submission.

Screenshots are limited to PNG/JPEG/WebP, validated by magic bytes, capped at
8 MB each / 32 MB total / 8 screenshots, and stored owner-only under
`data/feedback/`.

## Duplicate handling

Before review the server runs a bounded search of open issues in the
configured repository (`is:issue is:open` plus summary keywords, at most five
results). The reporter can continue as a new report or add the evidence as a
comment on a matching existing issue. The search is fail-soft: if GitHub is
unreachable the review still works and says so.

## Security reports

A security-vulnerability report is never posted publicly. Submission is
redirected to the private security-advisory URL (`PANDAMONIUM_FEEDBACK_SECURITY_URL`
or the repository's GitHub advisory page) and the public submit button stays
disabled. This matches the project's coordinated-disclosure policy.

## Submission, retry, and exact-once

Submission goes through the server route `/api/feedback/submit`; the browser
never receives a GitHub credential. Each draft carries an idempotency key that
the server claims before calling GitHub:

- a successful issue is recorded and the URL/number returned;
- a retry with the same key returns the already-created issue instead of
  creating a duplicate;
- a failed call marks the key retryable and preserves the local draft and its
  uploaded screenshots.

Failure copy is honest: unconfigured installations, missing `Issues: write`
permission, rate limits, and GitHub outages each say what happened and what to
do next.

## Operator provisioning (GitHub App, `Issues: write` only)

The live submission path is optional and installation-owned. No credential or
key material ever ships in frontend code.

1. Create a GitHub App for the installation (Settings → Developer settings →
   GitHub Apps → New GitHub App). Any name/homepage is fine; disable the
   webhook.
2. **Repository permissions:** set **Issues: Read and write**. Leave every
   other permission at **No access**. Nothing else (contents, actions,
   members) is needed.
3. Install the App on the canonical repository only
   (`MADPANDA3D/Pandamonium` or the operator's fork/mirror).
4. On the App page, note the **App ID**, generate a **private key** (PEM), and
   copy the **installation ID** from the installation URL
   (`.../installations/<id>`).
5. Configure the server environment (see `.env.example`):
   `PANDAMONIUM_GITHUB_APP_ID`, `PANDAMONIUM_GITHUB_APP_INSTALLATION_ID`, and
   `PANDAMONIUM_GITHUB_APP_PRIVATE_KEY` (inline PEM with `\n` escapes) or
   `PANDAMONIUM_GITHUB_APP_PRIVATE_KEY_FILE` (owner-only file). Optionally set
   `PANDAMONIUM_GITHUB_REPO` and an enterprise `PANDAMONIUM_GITHUB_API_BASE`
   (HTTPS only).
6. Restart Pandamonium. `GET /api/feedback/config` then reports
   `configured: true`; the report panel stops showing the fail-closed notice.

The server builds a short-lived RS256 JWT from the private key, exchanges it
for an installation token, caches the token in memory until shortly before it
expires, and uses it for the duplicate search and issue create/comment calls.
Tokens and the private key are never logged, returned in a payload, or written
to disk.

### Screenshot images in the issue

GitHub's public REST API with `Issues: write` can create issues and comments
but cannot attach binary images to them. The honest options are:

- set `PANDAMONIUM_FEEDBACK_ATTACHMENT_URL_BASE` to an HTTPS base that serves
  the stored `data/feedback/` evidence (for example a small authenticated
  static host the operator controls). The issue body then embeds
  `![label](<base>/<name>)` for each screenshot;
- leave it unset: the issue body carries a bounded evidence manifest (step
  label, route, size, sha256 prefix) and states that images are stored with
  the installation.

Either way, the reviewed screenshot list is part of the public issue and the
review screen shows which form will be used.

## Retention and rollback

- `data/feedback/` holds the validated screenshot bytes and a submission
  index. Operators may delete it at any time; drafts then fail with an honest
  "re-add the screenshot" message.
- Set `feedback_enabled: false` (Settings → or `manage_settings
  {"action":"set","key":"feedback_enabled","value":false}`) to hide the
  workflow. Removing the `PANDAMONIUM_GITHUB_*` configuration disables
  submission while keeping local capture. The feature is additive: removing it
  touches no other subsystem, and Linear is never written to automatically
  (maintainer triage only).

## Tests

- `tests/test_feedback_report.py` — validation, redaction (secret canaries),
  diagnostics allowlist, title/body construction, image sniffing.
- `tests/test_feedback_routes.py` — config/upload/attachment/prepare/submit,
  owner scoping, security diversion, duplicate choice, exact-once and retry,
  no-credential-to-browser assertions.
- `tests/test_github_issues_client.py` — RS256 JWT, token caching, bounded
  search, issue create/comment, honest status mapping, token never logged.
- `tests/browser/mad-856-bug-report.spec.js` — right-dock and mobile surfaces,
  capture + screenshot reorder/remove/label, review + warning + duplicates,
  security diversion, retry with a stable key, navigation persistence,
  keyboard/screen-reader labels.
