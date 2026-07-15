# Release and container process

## Source compatibility

`voice-orb-v0.1.0-alpha.2` is ported from upstream commit `c80462e4621c1a3360e5441843bb83b4691a8766`. The machine-readable record is `compatibility.json`. The public alpha branch is constructed from that upstream commit; private development history is not published wholesale.

Alpha.1 remains immutable, but its bundled Docker CLI failed the Trivy release
gate and no container image was published. Alpha.2 removes that optional client
from the hardened image and is the supported replacement release.

## CI gates

| Gate | Workflow |
|---|---|
| `compileall`, `node --check`, full pytest, diff check, Compose config | `.github/workflows/ci.yml` and Voice Orb release preflight |
| Fake microphone/camera browser lifecycle | Voice Orb release preflight using `@playwright/test` |
| Pull-request dependency review and advisory audits | `.github/workflows/dependency-review.yml` |
| Dockerfile lint and image build | `.github/workflows/container-scan.yml` and release workflow |
| Trivy image scan | `.github/workflows/container-trivy.yml` and release workflow |
| Full-history upstream scan plus blocking public-delta secret scan | `.github/workflows/secret-scan.yml` and release workflow |
| Workflow lint/security | `.github/workflows/workflow-security.yml` |

The public scrub is also required before tagging. It must fail on private notes, handovers, personal/business data, real private hostnames or addresses, private paths, credentials, cloned voices, or assets without redistribution rights.

## Publish

Create the annotated source tag only from a fully verified alpha commit:

```bash
git tag -a voice-orb-v0.1.0-alpha.2 -m 'Odysseus Voice Orb v0.1.0 alpha.2'
git push origin voice-orb-v0.1.0-alpha.2
```

The tag-triggered release workflow verifies the compatibility record, runs the release gates, builds natively for `linux/amd64` and `linux/arm64`, pushes by digest, and creates one manifest tagged `voice-orb-v0.1.0-alpha.2` in GHCR.

Record the resulting manifest digest in the GitHub release notes. Users should deploy `ghcr.io/madpanda3d/odysseus@sha256:DIGEST`; the tag is a discovery label, while the digest is the immutable pin.

## Nightly drift check

The non-release upstream compatibility workflow reapplies the public alpha delta to current upstream `dev`, then runs bounded syntax and focused tests. It never publishes an image. A failure is a drift signal, not permission to merge upstream changes automatically.
