# Install the v0.1 alpha

The supported source and container tag is `voice-orb-v0.1.0-alpha.2`. It
replaces alpha.1, which remains immutable but did not publish a container
because its bundled Docker CLI failed the release security gate. Verify the
alpha.2 tag exists before relying on these commands.

## Source install with Docker Compose

```bash
git clone --branch voice-orb-v0.1.0-alpha.2 --depth 1 https://github.com/MADPANDA3D/odysseus.git
cd odysseus
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
```

Open `http://localhost:7000`. Read the generated first-admin password from `docker compose logs odysseus`, sign in interactively, and replace that password immediately. Keep `AUTH_ENABLED=true` and `LOCALHOST_BYPASS=false` for Docker, LAN, reverse-proxy, and shared installations.

The Voice Orb works without a worker. Configure a normal Odysseus model first, then choose STT and TTS in Settings. Microphone APIs require `localhost` or HTTPS.

## Immutable container install

The release workflow publishes both `linux/amd64` and `linux/arm64` to GHCR. Use the manifest digest printed in the release workflow summary, not only the movable repository name:

```bash
export ODYSSEUS_IMAGE='ghcr.io/madpanda3d/odysseus@sha256:REPLACE_WITH_RELEASE_DIGEST'
docker pull "$ODYSSEUS_IMAGE"
docker compose -f docker-compose.yml -f docker/voice-orb-image.yml up -d --no-build
```

The overlay changes only the Odysseus application image; the base Compose file still defines storage and supporting services. Confirm the running digest with:

```bash
docker image inspect "$ODYSSEUS_IMAGE" --format '{{json .RepoDigests}}'
```

Do not treat a tag name as a cryptographic pin. The compatibility record names the source tag and upstream base; the release summary supplies the image digest.

The hardened image does not bundle a Docker CLI or support mounting the host
Docker daemon. Connect to existing model endpoints or use remote workflows
over SSH instead.

## Optional voice overrides

Copy only the settings you need from `docs/voice-orb/voice-orb.env.example` into `.env`. By default, the Orb uses the current/default Odysseus endpoint and model. No remote or paid model is prewarmed automatically.

## Upgrade and rollback

Back up `data/` before changing versions. Pull or check out an immutable release tag, run `docker compose config --quiet`, then recreate the app. Roll back by restoring the previous source tag or image digest; v0.1 adds no database migration and its request fields are optional.

Docker Desktop and WSL2 are best-effort for this alpha. Linux `amd64` and `arm64` are the release-gated platforms.
