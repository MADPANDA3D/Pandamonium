# Install Pandamonium

The supported source is the canonical `main` branch or an immutable
`pandamonium-v*` release tag. Older `voice-orb-v*` tags remain available as
historical rollback points, but they predate the Pandamonium repository identity.

## Source install with Docker Compose

```bash
git clone --branch main --depth 1 https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
```

Open `http://localhost:7000`. Read the generated first-admin password from `docker compose logs pandamonium`, sign in interactively, and replace that password immediately. Keep `AUTH_ENABLED=true` and `LOCALHOST_BYPASS=false` for Docker, LAN, reverse-proxy, and shared installations.

The Voice Orb works without a worker. Configure a normal Pandamonium model first, then choose STT, TTS, and an optional Vision model in Settings. Browser microphone and camera APIs require `localhost` or HTTPS. Camera permission is requested only after the exact `Open your eyes.` command.

## Guided setup and fixed-worker status

While signed in interactively, inspect `GET /api/voice/status` or say exactly
`Check voice setup.` The structured `setup` object, textual guidance, and spoken
reply come from the same server-generated snapshot. Core readiness covers the
voice model, STT, and TTS. The three fixed workers are optional and count as
ready only when explicitly configured and their bounded health check succeeds;
model discovery or Tailnet visibility never implies a healthy worker cluster.

Configure provider credentials in Pandamonium Settings. Configure worker
credentials as restrictive read-only mounted token files using the variables in
`voice-orb.env.example`. Setup guidance reports only a non-secret category that
needs attention; it never speaks credential values, endpoint URLs, private
addresses, or token paths.

## Optional Tailnet model discovery

Tailnet discovery is off by default and available only to an authenticated
administrator. First request `/api/discover?mode=tailnet_peers`; this returns
short-lived opaque peer IDs and performs no model probe. Then explicitly select
between one and five returned IDs with
`/api/discover?mode=tailnet_probe&peer_id=OPAQUE_ID`. Only fixed model-list
targets are checked, and results omit network identity and raw errors.

Voice Orb never runs `tailscale up`, changes ACLs or Funnel, enrolls a device,
discovers agents, performs a blind Tailnet scan, or changes bind addresses.

## Immutable container install

The release workflow publishes both `linux/amd64` and `linux/arm64` to GHCR. Use the manifest digest printed in the release workflow summary, not only the movable repository name:

```bash
export PANDAMONIUM_IMAGE='ghcr.io/madpanda3d/pandamonium@sha256:REPLACE_WITH_RELEASE_DIGEST'
docker pull "$PANDAMONIUM_IMAGE"
docker compose -f docker-compose.yml -f docker/voice-orb-image.yml up -d --no-build
```

The overlay changes only the Pandamonium application image; the base Compose file still defines storage and supporting services. Confirm the running digest with:

```bash
docker image inspect "$PANDAMONIUM_IMAGE" --format '{{json .RepoDigests}}'
```

Do not treat a tag name as a cryptographic pin. The release summary supplies the
image digest for an immutable container installation.

The hardened image does not bundle a Docker CLI or support mounting the host
Docker daemon. Connect to existing model endpoints or use remote workflows
over SSH instead.

## Optional voice overrides

Copy only the settings you need from `docs/voice-orb/voice-orb.env.example` into `.env`. By default, the Orb uses the current/default Pandamonium endpoint and model. No remote or paid model is prewarmed automatically.

## Upgrade and rollback

Back up `data/` before changing versions. Pull or check out an immutable release
tag, run `docker compose config --quiet`, then recreate the app. For a container
rollback, use the corresponding verified immutable image digest from that release
rather than assuming a tag is a cryptographic pin.

Docker Desktop and WSL2 are best-effort. Linux `amd64` and `arm64` are the
release-gated platforms.
