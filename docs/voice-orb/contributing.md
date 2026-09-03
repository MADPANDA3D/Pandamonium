# Contributing to Voice Orb

Open Voice Orb changes against the maintained fork's beta branch. Follow the root `CONTRIBUTING.md` and keep each pull request focused.

## Public-scope rules

- Keep AGPL-3.0-or-later headers and attribution intact.
- Never add private Mark Notes, `.whoami`, handovers, personal or client data, private topology, credentials, cloned voices, or unlicensed media.
- Reuse Pandamonium sessions, provider settings, Calendar paths, modals, SSE, and static modules before adding a new API.
- Do not add arbitrary selectors, DOM/script execution, arbitrary URLs, caller-selected Python modules, or source-rewriting installers.
- Keep workers disabled by default and read-only in the public beta.
- Add no production dependency when the browser platform, Python standard library, or an existing dependency covers the requirement.
- Keep camera capture user-initiated and ephemeral. Never add recording, background capture, face recognition, remote viewing, raw-frame persistence, or frame diagnostics.
- Add media only through `static/voice-orb-media.json`, with a canonical same-origin path, explicit provenance/license, and immutable checksum. Arbitrary URLs, actor/JARVIS voice clones, copyrighted clips, and undeclared audio bundles are prohibited.

## Required checks

```bash
git diff --check
python -m compileall -q app.py core routes src services scripts tests
node tests/test_foreground_control.js
node tests/test_voice_orb.js
node tests/test_voice_orb_media.js
node tests/test_voice_orb_setup.js
python scripts/voice_orb_public_scrub.py --self-test
python scripts/voice_orb_public_scrub.py
python -m pytest -q
docker compose config --quiet
npm ci
npx playwright install chromium
npm run test:browser
```

Run Docker build and security scans for release-affecting changes. Browser tests use Chromium fake microphone/camera devices and never request real CI hardware.

## Upstream work

Discuss broad host-contract changes before code. Candidate upstream contributions should be small and independent: an enumerated foreground-action registry, bounded client-state reporting, read-only Calendar voice access, and stable voice/static extension seams.

Track upstream [extension discussion #4439](https://github.com/odysseus-dev/odysseus/discussions/4439), [plugin-contract PR #4241](https://github.com/odysseus-dev/odysseus/pull/4241), and [voice issue #4118](https://github.com/odysseus-dev/odysseus/issues/4118). Do not call this distribution a plugin until upstream ships a stable host ABI.
