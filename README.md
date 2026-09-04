<h1 align="center">Pandamonium</h1>

<p align="center">
  <strong>Your self-hosted AI control plane.</strong><br>
  Local and API models, agents, voice, memory, tools, documents, and extensions in one workspace.
</p>

<p align="center"><sub>Maintained by MADPANDA3D.</sub></p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#pandamonium-upgrades">Upgrades</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="docs/voice-orb/README.md">Voice Orb</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <img src="docs/pandamonium-dashboard.png" alt="Pandamonium self-hosted AI workspace">
</p>

## Quick Start

```bash
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` after the containers become healthy. The first
admin password is printed by:

```bash
docker compose logs pandamonium
```

Native Linux, macOS, Windows, GPU, HTTPS, and configuration instructions are
in the [setup guide](docs/setup.md).

## Pandamonium Upgrades

Pandamonium keeps Odysseus's self-hosted workspace foundation and adds the
following maintained platform capabilities.

### Model-neutral identity and chat

- Installation-owned agent name, ID, constitution, and version instead of a
  model-owned persona.
- Guided first-run setup for identity, model endpoints, and integrations.
- Exact runtime model reporting: the agent reports the configured model
  identifier without inventing a vendor or claiming to be GPT.
- One adaptive conversation flow that keeps ordinary chat lightweight and
  brings in tools only when the request needs them.
- Local and API model support through configurable endpoints, with model
  switching that does not replace the agent's identity, memory, or sessions.

### Governed agents, tools, and extensions

- Built-in tools, MCP servers, skills, files, shell, web, and browser-facing
  foreground actions behind owner, permission, approval, and evidence gates.
- Jarvis OS protocol coverage for identity, bounded context, memory provenance,
  action envelopes, authority receipts, learning controls, and operational
  traces. See the [runtime status](docs/jos-protocol-runtime-status.md).
- Generic extension manifests, installed-plugin visibility, pinned Git source
  installation, capability registration, enable/disable lifecycle, and
  rollback without giving extensions authority over the host.
- Scoped client-state and foreground-action bridges for extensions that need
  to interact with the active browser surface.
- ORACLE remains an optional reference extension; clean installations start
  without private extensions, workers, credentials, or topology.

### Knowledge and workspaces

- Owner-scoped long-term memory and document RAG with source provenance,
  compaction, prompt-injection boundaries, and optional Graphify code graphs.
- Owner-scoped Books library with PDF ingestion, page-aware retrieval, source
  attribution, reindexing, deletion, and explicit OCR-needed status.
- Documents, attachments, email, notes, tasks, calendar and CalDAV, gallery,
  Deep Research, model comparison, and the hardware-aware model Cookbook.
- Pandamonium branding, configurable themes, and authenticated preference sync
  across browser sessions.
- Integration inventory distinguishes configured services from services that
  have actually passed a live health check.

### Voice Orb and workers

- Integrated microphone, STT, TTS, interruption, and streamed spoken responses
  that preserve the complete written answer while sanitizing speech-only text.
- Setup diagnostics for the selected model, speech providers, and optional
  worker readiness.
- Explicit, user-initiated camera frames and checksummed same-origin media;
  camera frames are bounded and are not persisted.
- Optional concurrent read-only worker adapters with attributed progress,
  cancellation, session reconstruction, health gates, and disabled-by-default
  configuration.
  See the [Voice Orb documentation](docs/voice-orb/README.md).

### Reliability, deployment, and security

- Context budgets use the model server's effective allocated capacity, with
  bounded tool schemas, trimming, and compaction for smaller local models.
- Automatic memory work runs in the background, and tool-backed turns include
  a guarded final-answer recovery path instead of returning an empty result.
- Owner-scoped authentication, sessions, data, integrations, API tokens,
  backups, and restore paths for shared or proxied installations.
- Docker Compose, native Python, and systemd workflows; canonical
  `pandamonium` CLI, service, package, environment, and GHCR naming.
- Required pytest, browser, syntax, Compose, secret, dependency, workflow, and
  container security checks on the canonical `main` branch.
- Legacy `odysseus` commands and `ODYSSEUS_*` environment variables remain as
  documented compatibility aliases so existing installations can upgrade.

## Command Line

The canonical command is `pandamonium`:

```bash
./scripts/pandamonium help
./scripts/pandamonium backup snapshot
./scripts/pandamonium mcp list
```

The former `odysseus` command names remain as compatibility aliases for
existing installations. New configuration uses `PANDAMONIUM_*` environment
variables; the former `ODYSSEUS_*` names remain accepted during migration.

## Security

Pandamonium exposes powerful local tools. Keep authentication enabled, keep
private data and credentials out of Git, and do not expose raw model or service
ports publicly. See [SECURITY.md](SECURITY.md) and the
[deployment guidance](docs/setup.md#security-notes).

## Star History

<a href="https://www.star-history.com/?repos=MADPANDA3D%2FPandamonium&type=date&legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MADPANDA3D/Pandamonium&type=date&theme=dark&legend=top-left&sealed_token=1p4_3IuUF5yfOSHwneNKxDToQM9CQZ-ZEqbi1EgfQBPHJM7gAqAZkZOJ4WgXmFh8pqsWLxCh_8FfTp1_hIHJ0TJNxxtP9PrLYPCClMr2Qy7Yw92nH6xAA23n6Zp3Rq_ZSIvQ9TnfuLFdUvCG11ITzZ7Co85qN1jWEm1j7RLCwCqVjKENVwjsmEUSYUdC">
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MADPANDA3D/Pandamonium&type=date&legend=top-left&sealed_token=1p4_3IuUF5yfOSHwneNKxDToQM9CQZ-ZEqbi1EgfQBPHJM7gAqAZkZOJ4WgXmFh8pqsWLxCh_8FfTp1_hIHJ0TJNxxtP9PrLYPCClMr2Qy7Yw92nH6xAA23n6Zp3Rq_ZSIvQ9TnfuLFdUvCG11ITzZ7Co85qN1jWEm1j7RLCwCqVjKENVwjsmEUSYUdC">
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MADPANDA3D/Pandamonium&type=date&legend=top-left&sealed_token=1p4_3IuUF5yfOSHwneNKxDToQM9CQZ-ZEqbi1EgfQBPHJM7gAqAZkZOJ4WgXmFh8pqsWLxCh_8FfTp1_hIHJ0TJNxxtP9PrLYPCClMr2Qy7Yw92nH6xAA23n6Zp3Rq_ZSIvQ9TnfuLFdUvCG11ITzZ7Co85qN1jWEm1j7RLCwCqVjKENVwjsmEUSYUdC">
  </picture>
</a>

## Project Lineage

Pandamonium began as a fork of
[Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) and retains the
original project history, contributor attribution, and AGPL license. It is an
independent MADPANDA3D project, not an official Odysseus release. Details are
recorded in [NOTICE](NOTICE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE), [NOTICE](NOTICE), and
[ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
