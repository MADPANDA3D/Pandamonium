# Security model

Voice Orb adds microphone and optional worker trust boundaries to an already powerful self-hosted application. Authentication stays enabled by default, and the alpha intentionally keeps its public capability set narrow.

## Browser control boundary

The model can emit only enumerated `ui_control` events for Calendar open, document close/minimize, and view-state reporting. Client state contains logical view names, open/minimized flags, Calendar view/date, and active document ID. Selectors, scripts, arbitrary URLs, HTML, and generic DOM commands are rejected.

Voice requests are same-origin, owner-scoped, and authenticated. Unknown request fields are rejected. API bearer tokens are not accepted for voice orchestration.

## Worker boundary

Workers are disabled until explicitly configured. The public alpha accepts `read_only` tasks only, rejects caller-provided approval elevation, uses fixed adapter IDs, and verifies ownership in broker helpers so internal callers cannot bypass route checks. Worker tokens come from files, not public status or request bodies.

Run worker services with least privilege, a dedicated OS account, a read-only execution profile, and a neutral workspace allowlist. Do not mount the Docker socket or writable host directories merely to make a worker pass a health check.

## Network boundary

Keep Odysseus on loopback or behind an authenticated HTTPS reverse proxy/private access gateway. Keep model and worker ports private. Restrict `ALLOWED_ORIGINS`, set `SECURE_COOKIES=true` behind HTTPS, and never enable the development localhost bypass on a shared deployment.

v0.1 performs no Tailnet discovery, port scanning, ACL changes, Funnel configuration, device enrollment, or wildcard-interface binding.

## Release controls

Release tags run syntax checks, full pytest, Compose validation, fake-device browser checks, dependency audit, secret scanning, Docker build, and Trivy before a multi-architecture image is published. GitHub Actions are pinned to commit SHAs. The public scrub rejects private notes, topology, data, secrets, and unlicensed assets.

Report vulnerabilities through `SECURITY.md`; do not post credentials, private logs, private addresses, or proof-of-concept data in a public issue.
