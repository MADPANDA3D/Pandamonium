# Pandamonium Setup Guide

This page keeps the detailed install, deployment, troubleshooting, and configuration notes out of the front README.

## Quick Start

The supported release line is `main`. Pandamonium retains its upstream history
and attribution; the original Odysseus project remains available at
[pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus).

Defaults work out of the box: clone, run, then configure models/search/email
inside **Settings**. Only edit `.env` for deployment-level overrides like
`APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, or a pre-seeded admin password.

On first setup, Pandamonium creates an admin account (`admin` unless
`PANDAMONIUM_ADMIN_USER` is set) and prints a temporary password in the terminal.
For Docker installs, the same line is in `docker compose logs pandamonium`.
Use that for the first login, then change it in **Settings**.

New deployment settings use the `PANDAMONIUM_*` prefix. Existing
`ODYSSEUS_*` settings remain accepted as migration aliases, with a canonical
Pandamonium value taking precedence when both are set.

Contributing? See [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, testing, and
pull request guidelines.

### Docker (recommended)
```bash
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
cp .env.example .env       # optional, but recommended for explicit defaults
docker compose up -d --build
```
To include optional extras in the image (PDF viewer, Office extraction; includes AGPL PyMuPDF), build with `docker compose build --build-arg INSTALL_OPTIONAL=true` before `up`.

Open `http://localhost:7000` when the containers are healthy. Docker Compose
binds the web UI to `127.0.0.1` by default. If the port is taken, set
`APP_PORT=7001` in `.env` and recreate the container. Set `APP_BIND=0.0.0.0`
only when you intentionally want LAN/reverse-proxy access.

> **On Apple Silicon (M-series) Macs:** Docker can't reach the Metal GPU, so
> Cookbook serves local models on CPU only. For GPU-accelerated model serving,
> run natively instead — see [Apple Silicon](#apple-silicon) below.

### Native Linux / macOS
```bash
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
Requirements: Python 3.11+. Scanned-PDF OCR requires `tesseract` and `pdftoppm`
(`tesseract-ocr`, `tesseract-ocr-eng`, and `poppler-utils` on Debian/Ubuntu;
`brew install tesseract poppler` on macOS). They are already bundled in the
Docker image. Cookbook also needs `tmux` for background model
downloads and serves. The app itself is lightweight; local model serving is the
heavy part and depends on the model, runtime, GPU, and VRAM, so small hosts can
connect to API or remote model servers instead. Use `--host 0.0.0.0` only when you intentionally want LAN/reverse-proxy access.

The setup guide includes **Connect your gallery**. Open it to scan for distinct
Gallery sources on this device and, when Tailscale is available, online tailnet
devices. Pandamonium currently recognizes two source types: a device folder it
can read and an Immich service it can reach. Each result keeps its own type,
device, location, connection state, and controls; an Immich server is never
collapsed into a PC Pictures folder.

On a native install, Gallery discovers the current OS user's conventional
Pictures folder: XDG `XDG_PICTURES_DIR` on Linux, `~/Pictures` on macOS, and the
Windows Known Folder API. Accessible folders are connected read-only and can be
refreshed, changed, or disabled under **Gallery → Settings → Gallery sources**.
For example, an install running on `pc-codex` reports its Pictures directory as
a **Device folder** on `pc-codex`, while an Immich instance found elsewhere is a
separate **Immich** source on that server. Seeing a remote PC on the tailnet does
not grant filesystem access; its folder must be visible to the running process
through a native install or an explicit read-only mount before it can be offered.
On an authenticated multi-user install, only an administrator can map host
folders; this prevents ordinary accounts from browsing process-readable paths.
Pandamonium indexes metadata and content hashes; it does not upload, rewrite, or
delete source photos, follow symlinks, or scan outside the selected folder.

Docker cannot see host Pictures folders unless you mount them. Add an explicit
read-only bind mount and name only the in-container mount path:

```yaml
services:
  pandamonium:
    volumes:
      - /host/path/to/Pictures:/media/pictures:ro
    environment:
      - PANDAMONIUM_GALLERY_MEDIA_ROOTS=/media/pictures
```

The path must be a real container mount point. An ordinary container directory
is rejected, and an unmounted host filesystem is never implied. Separate
multiple Linux container paths with `:`.

Gallery source discovery on desktop and mobile:

![Gallery source discovery on desktop](images/gallery-local-pictures-desktop.png)

![Gallery source discovery on mobile](images/gallery-local-pictures-mobile.png)

### Connect an Immich source

Choose an **Immich found** result under **Gallery → Settings → Gallery sources**.
Pandamonium checks the standard HTTPS endpoint, port `8443`, and Immich's default
port `2283` across a bounded set of online tailnet devices. Discovery sends no
credentials and does not connect anything automatically. If the service is not
discoverable, use **Connect Immich manually**.

In Immich, open the profile menu, choose **Account Settings → API Keys**, and
create a key named `Pandamonium`. For every Gallery feature currently shipped,
enable only these permissions:

- `album.read` — list Immich albums
- `asset.read` — browse, search, and read photo metadata
- `asset.view` — display thumbnails and previews
- `asset.download` — open, download, or import originals
- `asset.upload` — export local Gallery images to Immich

Leave every other permission disabled. Pandamonium does not update or delete
Immich content. Copy the key once, paste it into Pandamonium, then save and test
the connection. See the
[official Immich user-settings guide](https://docs.immich.app/features/user-settings/).
Pandamonium encrypts the key at rest and proxies Immich metadata, thumbnails,
previews, and downloads server-side, so the browser never receives the
credential.

Choose **Immich** in the Gallery source filter or open an Immich album to browse
and search the remote library. Remote assets and albums stay visibly read-only.
**Import a local copy** stores a bounded copy in Pandamonium; **Export to
Immich** uses Immich's supported upload API. Removing the connection or clearing
its owner-scoped cache removes only Pandamonium metadata and thumbnails and
never deletes an Immich original. If Immich is offline, a matching cached page
can remain visible with an explicit stale/offline state.

The discovered Immich source and its connection controls on desktop and mobile:

![Immich Gallery settings on desktop](images/gallery-immich-settings-desktop.png)

![Immich Gallery settings on mobile](images/gallery-immich-settings-mobile.png)

### Rich chat rendering

Pandamonium renders fenced `mermaid` blocks as responsive diagrams in chat.
Invalid diagram source stays visible with an error message so an agent response
never disappears. Markdown tables remain horizontally scrollable on narrow
screens, and GitHub, Instagram, and Facebook links receive recognizable service
icons without changing their destinations.

![Mermaid diagram, table, and branded links on desktop](images/chat-mermaid-rich-links-desktop.png)

![Mermaid diagram, table, and branded links on mobile](images/chat-mermaid-rich-links-mobile.png)

### Atomic native Linux updates

The fixed footer can install a signed Pandamonium release only when Linux is
using the managed immutable layout below. A normal Git checkout is never
rewritten in place, and a Docker container must be updated from its host.

```text
/opt/pandamonium/
  current -> releases/1.0.11-<commit8>
  releases/<version>-<commit8>/
  venvs/<version>-<commit8>/
/var/lib/pandamonium/data/
/var/backups/pandamonium/
```

Every release artifact has a SHA-256 digest, exact Git commit, requirements
digest, compatibility/migration contract, and Ed25519 signature. The embedded
public key verifies that manifest before any archive is extracted. An update
then performs these steps in order:

1. download and verify the signed manifest and checksummed artifact;
2. stage a new immutable release and reuse or build its virtual environment;
3. create and verify a full data backup, including uploads and documents;
4. restore that backup into a rehearsal directory and run migrations twice;
5. stop the app, run the same idempotent migrations twice on live data, and
   atomically switch the `current` symlink;
6. start the app and verify both `/api/health` and the exact target version;
7. automatically restore the prior symlink and backup if any live step fails.

The authenticated admin UI writes only a request under the external data
directory. Install the root-owned units so systemd performs the privileged
work. Review and adjust every path before enabling them:

```bash
sudo install -m 0644 pandamonium-updater.service /etc/systemd/system/
sudo install -m 0644 pandamonium-updater.path /etc/systemd/system/
sudo install -m 0644 pandamonium-update-recover.service /etc/systemd/system/
sudo install -d -m 0755 /etc/pandamonium
sudoedit /etc/pandamonium/update.env
sudo systemctl daemon-reload
sudo systemctl enable --now pandamonium-updater.path
sudo systemctl enable pandamonium-update-recover.service
```

`/etc/pandamonium/update.env` must be readable only by root and define the same
data path and app port used by the web service:

```ini
PANDAMONIUM_DATA_DIR=/var/lib/pandamonium/data
PANDAMONIUM_UPDATE_ROOT=/opt/pandamonium
PANDAMONIUM_UPDATE_TRIGGER=systemd-path
PANDAMONIUM_UPDATE_SERVICE=pandamonium.service
PANDAMONIUM_UPDATE_BACKUP_DIR=/var/backups/pandamonium
PANDAMONIUM_UPDATE_CONFIG_FILES=/etc/pandamonium/update.env
PANDAMONIUM_UPDATE_CHANNEL=stable
APP_PORT=7000
```

Copy those non-secret values into the app service environment too, then add an
app-service ordering drop-in so boot recovery completes first:

```ini
[Unit]
After=pandamonium-update-recover.service
```

Do not add `Wants=pandamonium-update-recover.service` to the app service. The
enabled recovery unit is ordered before the app at boot; pulling it into every
manual app start can make an updater-initiated restart wait on its own recovery
transaction.

Use `./scripts/pandamonium update check` and `update status` for readback. The
footer's **Check for updates** action never installs by itself; **Update now**
requires a separate confirmation and shows phase, percentage, backup path,
result, and rollback availability. Set `PANDAMONIUM_UPDATE_CHANNEL=prerelease`
only on hosts that intentionally accept prereleases.

The updater keeps the immediately previous immutable release and exact verified
backup. A manual rollback restores both when the signed compatibility contract
requires data restoration. Interrupted live activation is recovered from the
persisted state by `pandamonium-update-recover.service` at boot.

### Apple Silicon
Docker on macOS cannot use the Metal GPU. For GPU-accelerated Cookbook on an
M-series Mac, run Pandamonium natively:

```bash
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
./start-macos.sh
```

It launches at `http://127.0.0.1:7860`. To expose it to your phone over a trusted LAN/VPN such as Tailscale, bind all interfaces:

```bash
PANDAMONIUM_HOST=0.0.0.0 ./start-macos.sh
# then open http://<tailscale-ip>:7860
```

The script also reads `.env` at startup, so `APP_BIND=0.0.0.0` and `APP_PORT`
set there are picked up automatically without a command-line override each run.

Keep `AUTH_ENABLED=true` (the default) before binding outside loopback. Do not
expose this port directly to the public internet. To build a clickable app wrapper:

```bash
./build-macos-app.sh
```

<details>
<summary>Cookbook, GPU, Ollama, and troubleshooting notes</summary>

**Docker bundled services.** Compose starts Pandamonium, ChromaDB, SearXNG, and
ntfy. Pandamonium and the bundled service ports bind to `127.0.0.1` by default, so
they are reachable from the host but not exposed to your LAN/public internet
unless you opt in.

**Cookbook storage in Docker.** Downloads live in `./data/huggingface`
(`~/.cache/huggingface` in the container). Cookbook-installed Python CLIs and
serve engines live in `./data/local` (`~/.local` in the container), so they
survive container recreation.

**Remote servers.** In **Cookbook -> Settings -> Servers**, generate the
Pandamonium SSH key and add the public key to the remote server's
`~/.ssh/authorized_keys`. From the host you can also run:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

**Host Docker access (explicit opt-in).** Default Docker Compose intentionally
does not mount `/var/run/docker.sock`. You can still connect Pandamonium to
existing Ollama, vLLM, and other OpenAI-compatible endpoints without Docker
socket access.

You can still connect Pandamonium to existing Ollama, vLLM, and other
OpenAI-compatible endpoints. Remote server workflows over SSH remain the
preferred management path. If host-daemon control is unavoidable, use a
separate custom image with an independently audited Docker client and accept
that raw socket access effectively grants broad control over the host; that
configuration is outside the supported public alpha.

**Docker GPU overlays.** CPU-only users can skip this section. Cookbook can
only detect GPUs that Docker exposes to the container — if the host runtime or
device passthrough is not configured, Cookbook sees the iGPU, another card, or
CPU instead of your intended GPU.

For NVIDIA, `scripts/check-docker-gpu.sh` diagnoses GPU passthrough and can
optionally install the host runtime or update `.env`.

```bash
# Read-only diagnostic (default — installs nothing, never edits .env):
scripts/check-docker-gpu.sh

# Print OS-specific install commands without running them:
scripts/check-docker-gpu.sh --print-install-commands

# Install NVIDIA Container Toolkit on Ubuntu/Debian (requires sudo):
scripts/check-docker-gpu.sh --install-nvidia-toolkit

# Write COMPOSE_FILE to .env (only when GPU passthrough is confirmed working):
scripts/check-docker-gpu.sh --enable-nvidia-overlay

# Full assisted setup — install toolkit, then enable overlay if passthrough works:
scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
```

Safety notes:
- The app never installs host GPU runtime automatically.
- The app never edits `.env` automatically.
- `.env` is only modified when `--enable-nvidia-overlay` is explicitly passed,
  and only after GPU passthrough succeeds. `--yes` skips prompts but does not
  bypass the passthrough gate.
- `.env.bak.*` backups created by `--enable-nvidia-overlay` are ignored by
  Git and the Docker build context.

To enable manually without the script, add this to `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
```

**AMD / ROCm.** AMD setup is read-only diagnostic plus manual `.env` edit. Run:

```bash
scripts/check-docker-amd-gpu.sh
```

Then add the reported values to `.env`, replacing `RENDER_GID` with your host's
numeric render group id:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

For NVIDIA/AMD GPU support, also read the comments in the selected overlay file: docker/gpu.nvidia.yml or docker/gpu.amd.yml.

**Stack-management UIs (Portainer, Coolify, Dockhand, etc.).** These tools
often accept only a single Compose file and do not reliably honor `COMPOSE_FILE`
or multiple `-f` overlays. CLI users should keep using the `COMPOSE_FILE`
overlay workflow above. For stack UIs, point the stack at one of the standalone
files instead, which bundle the base stack plus the GPU settings:

- `docker-compose.gpu-nvidia.yml` — still requires the NVIDIA Container Toolkit
  on the host.
- `docker-compose.gpu-amd.yml` — still requires host ROCm/kfd/DRI setup, the
  `video`/`render` group membership, and `RENDER_GID` when needed.

The base `docker-compose.yml` plus the `docker/gpu.*.yml` overlays remain the
source of truth; the standalone files mirror them for single-file deployments.

Verify after enabling either overlay:

```bash
docker compose exec pandamonium nvidia-smi -L   # NVIDIA
docker compose exec pandamonium sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # AMD
```

> **GPU passthrough ≠ llama.cpp CUDA.** `nvidia-smi` passing inside the
> container confirms Docker GPU access, but llama.cpp also needs `cudart` and
> the CUDA Toolkit at runtime. If Cookbook logs show `Unable to find cudart
> library`, `Could NOT find CUDAToolkit`, `CUDA Toolkit not found`, or
> tensors/layers assigned to CPU, that is a Cookbook/llama.cpp build issue —
> not a Docker passthrough failure. Reinstall the serve engine via
> **Cookbook → Dependencies** to get a CUDA-enabled build.
>
> The same split applies to AMD/ROCm: seeing `/dev/kfd` and `/dev/dri` inside
> the container confirms device passthrough, not ROCm userspace or a
> ROCm-enabled vLLM/llama.cpp build. `rocm-smi` and `rocminfo` are not expected
> inside the slim Pandamonium image.

**Ollama with Docker.** If Ollama runs on the host, add this endpoint in
Settings:

```text
http://host.docker.internal:11434/v1
```

Ollama must listen outside its own loopback interface:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

This connects Pandamonium in Docker to an Ollama server that is already running on
your host machine; it does not start Ollama inside the container.
`host.docker.internal` is Docker's hostname for the host machine from inside the
container. Cookbook **Serve** is a separate workflow for serving downloaded
models through Pandamonium/llama.cpp, so Windows users with an existing Ollama
install usually only need to add the endpoint in Settings.

**Useful checks.**

```bash
docker compose ps
docker compose logs --tail=120 pandamonium
docker compose logs pandamonium | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**macOS details.** `start-macos.sh` installs Homebrew deps, creates the venv,
runs setup, and starts uvicorn on port `7860` because AirPlay often holds
`7000`. It uses llama.cpp/Ollama for Metal. vLLM/SGLang are CUDA/ROCm-only and
do not run on macOS. MLX-only models are not served by Pandamonium.

</details>

### Native Windows

**One-command launcher** (creates the venv, installs deps, runs setup, starts the
server; safe to re-run):

```powershell
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Or do it by hand:

```powershell
git clone https://github.com/MADPANDA3D/Pandamonium.git
cd Pandamonium
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

If `python` points at an older interpreter, use `py -3.12` (or another installed
3.11+ version) for the venv step.

**Exposing on a LAN/Tailscale (Windows):** the launcher binds to `127.0.0.1` and
does **not** read `APP_BIND` / `PANDAMONIUM_HOST` from `.env`, so editing `.env`
alone leaves the native Windows server on loopback. Pass the launcher's
`-BindHost` flag instead:

```powershell
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -BindHost 0.0.0.0
```

The manual `uvicorn` command takes the same address as `--host 0.0.0.0`. Bind
outside loopback only for a trusted LAN/VPN such as Tailscale: keep
`AUTH_ENABLED=true` and do not expose the port directly to the public internet.

**Requirements:** Python 3.11+. The core app (chat, agent, memory, documents,
email, calendar, deep research) runs fully native. For full **Cookbook** background
model downloads and the agent shell tool, also install
[Git for Windows](https://git-scm.com/download/win) (provides `bash.exe`).
Local GPU *serving* of vLLM/SGLang needs Linux/WSL2; for a local model on Windows,
[Ollama](https://ollama.com/download) is the easiest path — point Pandamonium at
`http://localhost:11434/v1` in Settings.

Open `http://localhost:7000`, log in with the generated admin password,
and configure everything else inside **Settings**.

## Troubleshooting & Advanced Setup

### `chromadb-client` conflicts with embedded ChromaDB
If `chromadb-client` (the lightweight HTTP-only package) is installed alongside the full `chromadb` package, Pandamonium starts but ChromaDB silently falls back to HTTP-only mode and fails.

**Fix:** uninstall `chromadb-client` and force-reinstall the full package:
```bash
./venv/bin/pip uninstall chromadb-client -y
./venv/bin/pip install --force-reinstall chromadb
```

### HTTPS + LAN/Tailscale exposure
To expose Pandamonium on a local network or Tailscale with HTTPS:
1. Change the bind address to `0.0.0.0` in `.env` (`APP_BIND=0.0.0.0` or `PANDAMONIUM_HOST=0.0.0.0`).
2. Generate a locally-trusted cert for your LAN/Tailscale IPs using [mkcert](https://github.com/FiloSottile/mkcert):
   ```bash
   mkcert -install
   mkcert -cert-file cert.pem -key-file key.pem <private-lan-ip> tailscale-ip
   ```
3. Run `uvicorn` with the generated certs:
   ```bash
   python -m uvicorn app:app --host 0.0.0.0 --port 7000 --ssl-certfile=cert.pem --ssl-keyfile=key.pem
   ```
4. Install the `mkcert` CA on any other device you want to access Pandamonium from (e.g., for iOS, email the `rootCA.pem` to yourself, install the profile, and trust it in Certificate Trust Settings).

### Common self-host traps (30-second fixes)
A grab-bag of small gotchas that otherwise turn into long debugging sessions.

- **`AUTH_ENABLED=false` is ignored / you're still forced to log in (Windows).** If you edited `.env` in Notepad it may have saved a UTF-8 **BOM**, turning the first key into `﻿AUTH_ENABLED` so it is never matched. Pandamonium loads `.env` with `encoding="utf-8-sig"` to tolerate a leading BOM, but the safe fix is to re-save `.env` as **UTF-8 without BOM** (VS Code: *Save with Encoding → UTF-8*).
- **macOS: the app isn't at `http://localhost:7000`.** macOS AirPlay Receiver usually holds port `7000`, so the macOS start script serves on **`7860`** instead — open `http://localhost:7860`. To use `7000`, free it (System Settings → General → AirDrop & Handoff → turn off *AirPlay Receiver*) and set `APP_PORT=7000`.
- **Copy buttons do nothing over a plain-HTTP Tailscale/LAN URL.** Browsers only expose the clipboard API (`navigator.clipboard`) on **secure origins** — HTTPS, or `localhost`. Over `http://100.x.y.z:7860` it is blocked. Serve over HTTPS (see *HTTPS + LAN/Tailscale exposure* above); `localhost` is exempt, so copy still works on the host itself.
- **Self-hosted ntfy reminders don't reach your phone.** Two things: (1) the bundled ntfy binds to loopback by default — to reach it from your phone set `NTFY_BIND` to your host/Tailscale IP and `NTFY_BASE_URL` to the same server URL in `.env`, then recreate the ntfy container (see the `NTFY_*` block in `.env.example`); (2) in the ntfy **Android** app, subscribe to the topic with **Instant delivery** enabled — non-`ntfy.sh` servers don't get instant push otherwise.
- **Local mail (Dovecot) login fails: "Plaintext authentication disallowed on non-encrypted connections."** Your IMAP/SMTP server is refusing cleartext auth over an unencrypted link. Prefer enabling TLS on the mail server; on a trusted LAN only, you can allow cleartext (Dovecot: `disable_plaintext_auth = no`).
- **Calendar/contacts (Radicale) won't sync.** Point Pandamonium at the **full collection URL** with its trailing slash — e.g. `http://host:5232/<user>/<collection-id>/` — not just the server root. Radicale shows this address for each calendar/address book in its web UI.

### Optional Dependencies
`requirements-optional.txt` contains packages that unlock extra features. It is not installed by default.

| Package | Feature unlocked |
|---------|-----------------|
| `faster-whisper` | Local speech-to-text (microphone -> text) via the "local" STT provider. |
| `ddgs` | DuckDuckGo as a search provider option. |
| `PyMuPDF` | PDF page rendering in the side viewer panel and form-filling. (Note: AGPL-3.0) |
| `markitdown` | Office/EPUB document text extraction (converts .docx/.xlsx/.pptx/.xls/.epub to Markdown). |

### Faster, reproducible installs with uv (optional)
[uv](https://docs.astral.sh/uv/) works as a drop-in replacement for the
venv + pip steps in the native install guides, no project changes are needed but this change results in faster installs along with a lockfile for reproducible environments. After [installing `uv`](https://docs.astral.sh/uv/getting-started/installation/), use:

```bash
uv venv venv --python 3.13
uv pip install -r requirements.txt
# then continue as usual: python setup.py, uvicorn, ...
```

`requirements.txt` is intentionally unpinned, so two installs at different times can produce different package versions. If you want a reproducible environment (e.g. across your own machines, or to roll back after a bad upgrade), snapshot and restore exact versions with:

```bash
uv pip compile requirements.txt -o requirements.lock   # snapshot current resolution
uv pip sync requirements.lock                          # reproduce it exactly later
```

`requirements.lock` is gitignored and platform-specific (compile it on the OS you deploy to). Regenerate it deliberately when you want to take upgrades. The plain `uv pip install -r requirements.txt` keeps following the unpinned requirements like pip does.

### Outlook / Office 365 email
Pandamonium email accounts currently use IMAP/SMTP username-password auth. Outlook
and Microsoft 365 generally require OAuth instead, so normal Microsoft mailbox
passwords will fail. See [docs/email-outlook.md](docs/email-outlook.md) for the
current limitation and the planned integration direction.

## Security Notes
Pandamonium is a self-hosted workspace with powerful local tools: shell access, file uploads, model downloads, web research, email/calendar integrations, and API tokens. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Use `SECURE_COOKIES=true` when Pandamonium is served through HTTPS by a trusted reverse proxy or private access gateway.
- Do not expose it directly to the public internet without HTTPS and a trusted reverse proxy or private access layer.
- Keep `.env`, `data/`, `logs/`, databases, uploads, generated media, backups, auth/session files, API keys, and model/provider tokens out of Git and private shares. They are ignored by default.
- Review `data/auth.json` after first boot: disable open signup unless you intentionally want it, make only your own account admin, and keep demo/test accounts non-admin.
- Non-admin users do not get shell/Python/file read/write by default, and admin-only routes/tools such as MCP management, API tokens, webhooks, model/cookbook serving, backup/vault, and app settings are admin-gated. Other features are controlled by per-user privileges, so review each user's privileges before exposing a deployment.
- Rotate any API keys or tokens that were ever pasted into a shared chat, demo, screenshot, or log.
- If you enable API tokens or webhooks, create separate tokens per integration and delete unused ones.
- Prefer binding manual development runs to `127.0.0.1`; bind to `0.0.0.0` only when you intentionally want LAN/reverse-proxy access.
- Keep ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, databases, and raw model/provider APIs internal-only. Expose only the authenticated Pandamonium web/API entrypoint through your trusted proxy or private access layer.
- Before publishing a fork, run `git status --short` and confirm no private files from `.env`, `data/`, `logs/`, uploads, backups, or local databases are staged.

### Private or proxied deployments
Pandamonium serves plain HTTP on its app port. Docker Compose binds Pandamonium and the bundled services to `127.0.0.1` by default, so a typical production/private setup is:

1. Keep Pandamonium on localhost, for example `127.0.0.1:7000`.
2. Terminate HTTPS at a trusted reverse proxy or private access gateway.
3. Put the authenticated Pandamonium web/API entrypoint behind that layer.
4. Keep raw service and model ports internal-only.

Cloudflare Access, Tailscale, Caddy, nginx, and Traefik can all fit this pattern; none are required by Pandamonium. If your access layer reaches Pandamonium on the same host, proxy to `http://127.0.0.1:7000` and keep `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, and `SECURE_COOKIES=true`.
`ALLOWED_ORIGINS` lists exact permitted origins for cross-origin browser/API clients; ordinary same-origin reverse-proxy access usually does not need a special CORS entry.

Common internal-only ports from the default docs/compose setup:

| Port | Service |
|---|---|
| `7000` | Pandamonium raw app port |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | ChromaDB host port for manual/compose access |
| `11434` | Ollama |
| `8000-8020` | Common local model/provider APIs |

## Configuration
Most setup is done inside the app with `/setup` or **Settings**. Use `.env`
for deployment-level defaults and secrets you want present before first boot.
Key settings:

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server (e.g. `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | -- | Optional OpenAI key. Prefer adding providers in the app unless pre-seeding. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL. Docker overrides this to `http://searxng:8080`. |
| `SEARXNG_SECRET` | generated on first Docker boot | Optional SearXNG cookie/CSRF secret. Leave blank unless you need to pin it. |
| `APP_BIND` | `127.0.0.1` | Docker Compose host bind address for the web UI. Use `0.0.0.0` only for intentional LAN/reverse-proxy access. |
| `APP_PORT` | `7000` | Docker Compose host port for the web UI. |
| `APP_DATA_DIR` | `./data` | Docker Compose host directory for application data volumes. |
| `APP_LOGS_DIR` | `./logs` | Docker Compose host directory for application logs. |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for loopback requests. Keep false for shared/network deployments. |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | Comma-separated exact permitted origins for cross-origin browser/API clients. |
| `SECURE_COOKIES` | `false` | Set true when serving Pandamonium through HTTPS at a trusted proxy or private access gateway. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | `localhost` | ChromaDB host for vector memory. Docker overrides this to `chromadb`. |
| `CHROMADB_PORT` | `8100` | ChromaDB port for manual host runs. Docker overrides this to `8000`. |
| `EMBEDDING_URL` | -- | OpenAI-compatible embeddings endpoint |
| `QDRANT_URL` | -- | Optional Qdrant projection endpoint; unset keeps the projection disabled |
| `QDRANT_API_KEY` | -- | Optional Qdrant API key |
| `PANDAMONIUM_QDRANT_MEMORY_COLLECTION` | `odysseus_memory` | Approved personal-memory projection; legacy `JARVIS_` name remains accepted |
| `PANDAMONIUM_QDRANT_DOCUMENT_COLLECTION` | `odysseus_documents` | Canonical-document projection; legacy `JARVIS_` name remains accepted |
| `PANDAMONIUM_QDRANT_WIKI_COLLECTION` | `odysseus_wiki` | Generated-wiki projection; legacy `JARVIS_` name remains accepted |
| `PANDAMONIUM_QDRANT_READS_ENABLED` | `false` | Promote Qdrant reads only after live parity checks; legacy `JARVIS_` name remains accepted |
| `PANDAMONIUM_GRAPHIFY_ROOTS` | -- | Optional JSON map of explicit repository/output roots; no startup or workspace scan occurs |
| `PANDAMONIUM_CHAT_UPLOAD_MAX_BYTES` | `10485760` | Chat/agent attachment cap in bytes. Raise for larger local PDFs or text documents. |
| `PANDAMONIUM_GALLERY_UPLOAD_MAX_BYTES` | `104857600` | Gallery image upload cap in bytes (100 MB). |
| `PANDAMONIUM_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES` | `26214400` | Gallery transform input cap in bytes (25 MB). |
| `PANDAMONIUM_GALLERY_MEDIA_ROOTS` | -- | Explicit in-container read-only Gallery mount paths. Native installs discover the OS Pictures folder instead. |
| `PANDAMONIUM_GALLERY_SCAN_LIMIT` | `10000` | Maximum supported files inspected per Gallery source refresh. |
| `PANDAMONIUM_MEMORY_IMPORT_MAX_BYTES` | `10485760` | Memory import file cap in bytes (10 MB). |
| `PANDAMONIUM_PERSONAL_UPLOAD_MAX_BYTES` | `26214400` | Personal document upload cap in bytes (25 MB). |
| `PANDAMONIUM_EMAIL_COMPOSE_UPLOAD_MAX_BYTES` | `26214400` | Email compose attachment cap in bytes (25 MB). |
| `PANDAMONIUM_STT_MAX_AUDIO_BYTES` | `26214400` | Speech-to-text audio cap in bytes (25 MB). |
| `PANDAMONIUM_ICS_MAX_BYTES` | `10485760` | Calendar `.ics` import cap in bytes (10 MB). |

All upload-limit vars are validated (must be a positive integer) and optional; an invalid value fails fast at startup.

### Built-in MCP servers (optional setup)

Pandamonium auto-registers a few built-in MCP servers at startup. The npx-based ones (currently the browser server, `@playwright/mcp`) only start when their npm package is already in the local npx cache. If a package isn't cached, that server is skipped with a startup log message explaining what to do, so a fresh install does not block on a multi-minute npm download or hang if Playwright system deps are missing.

To enable the browser MCP (page navigation, screenshots, vision), run once:

```bash
npx -y @playwright/mcp@latest --version
```

That installs `@playwright/mcp` plus Playwright (~300MB total). Restart Pandamonium and the server will register at startup.

## Architecture
```
app.py                   # FastAPI entry point
core/      auth, database, middleware, constants
src/       llm_core, agent_loop, agent_tools, chat_processor, search/
routes/    chat, session, document, memory, model … endpoints
services/  docs, memory, search, hwfit (Cookbook) …
static/    index.html + app.js + style.css + js/ (modular front-end)
docs/      landing page (index.html) + preview clips
```

## Data
All user data lives in `data/` (gitignored): `app.db` (sessions, messages, documents),
`memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/`, `settings.json`.

To back up or restore everything in `data/`, see the
[Backup & Restore guide](backup-restore.md).
