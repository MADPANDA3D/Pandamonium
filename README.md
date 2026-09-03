<h1 align="center">WhoAmI Platform</h1>

<p align="center">
  <strong>Make the harness yours.</strong><br>
  An open-source, self-hosted AI workspace from MADPANDA3D.
</p>

<p align="center">
  WhoAmI is a fork of <a href="https://github.com/pewdiepie-archdaemon/odysseus">Odysseus</a>.
  It preserves the original project's attribution and AGPL license while adding
  MADPANDA3D's identity, orchestration, voice, and configurable-harness work.
</p>

<p align="center"><sub>Modified by MADPANDA3D beginning July 18, 2026.</sub></p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="docs/voice-orb/README.md">Voice Orb Beta</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="The interface foundation inherited from Odysseus">
</p>

---

## Odysseus Voice Orb beta

This maintained fork adds an authenticated, first-party voice surface, guided setup status, bounded foreground and camera/media controls, optional fixed read-only workers, and explicit admin-only Tailnet model discovery. It is a beta fork distribution, **not an installable Odysseus plugin**; upstream does not yet expose a stable application-plugin contract.

The beta works with no workers configured and uses the current/default Odysseus model unless an operator chooses an explicit voice-model override. `Check voice setup.` returns the same server-generated guidance and structured status used by the authenticated status surface. Start with the [Voice Orb overview](docs/voice-orb/README.md), then read [installation](docs/voice-orb/install.md), [provider setup](docs/voice-orb/providers.md), and the [security model](docs/voice-orb/security.md).

The public fork remains AGPL-3.0-or-later. Private development notes, personal or business data, private network topology, credentials, and assets without clear redistribution rights are intentionally excluded.

## Quick Start

> This repository is the MADPANDA3D fork. The original Odysseus project remains available at [pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus).

```bash
git clone https://github.com/MADPANDA3D/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](docs/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Voice Orb (beta fork)** — interruptible speech, guided setup status, safe view and camera/media controls, optional fixed read-only workers, and explicit admin-only Tailnet model discovery.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Demo

A full hover-to-play tour lives on the landing page: [`docs/index.html`](docs/index.html).

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

WhoAmI is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly. Deployment details are in the [setup guide](docs/setup.md#security-notes).

## Upstream Foundation

<p>
  <a href="https://github.com/pewdiepie-archdaemon/odysseus"><img src="docs/odysseus-wordmark.png" alt="Odysseus, the upstream foundation for WhoAmI" width="180"></a>
</p>

The original project and contributor history remain part of this fork. WhoAmI
is an independent MADPANDA3D modification, not an official Odysseus release.

## Upstream Star History

This chart belongs to the original Odysseus repository and is retained as part
of the project's visible lineage.

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE), [NOTICE](NOTICE), and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
