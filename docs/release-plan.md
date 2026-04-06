# Agent Radio Release Plan

## Platform Compatibility Matrix

| Component | Linux + NVIDIA GPU | Linux CPU-only | macOS (any) | Docker |
|-----------|-------------------|----------------|-------------|--------|
| Kokoro TTS | CUDA, 50-100x RT | CPU, ~5x RT | ONNX/MLX, ~5-10x RT (Apple Silicon) | CUDA if GPU, CPU otherwise |
| Liquidsoap | apt or opam | apt or opam | **Not in Homebrew. OPAM only (painful).** | Official image: savonet/liquidsoap |
| Icecast | apt install icecast2 | apt install icecast2 | brew install icecast | Community images |
| MusicGen | CUDA, ~5GB VRAM | Not viable | Not viable | CUDA if GPU |
| Overall | Full experience | Full minus MusicGen | **Docker required for Liquidsoap** | Full stack in containers |

**The Liquidsoap problem on macOS:** Liquidsoap is not in Homebrew. The only macOS install path is OPAM (OCaml package manager), which requires installing OCaml, opam, and building from source with manual `CPATH` and `LIBRARY_PATH` configuration. This is not a reasonable ask for most developers. Docker is the only practical path for macOS users.

## Packaging Strategy: Docker-First, Bare-Metal for Linux

Two install paths:

1. **Docker Compose (primary, all platforms):** Single `docker compose up` launches Icecast + Liquidsoap + brain. Works on Linux, macOS, Windows. GPU passthrough optional. This is the path 90% of users take.

2. **Bare-metal install.sh (Linux only):** For operators who want native performance, systemd integration, or don't want Docker overhead. Detects Ubuntu/Debian, installs packages, creates venv, sets up systemd units.

No Windows bare-metal support. No macOS bare-metal support (due to Liquidsoap). Both use Docker.

## Hardware Profiles

### Profile 1: Linux + NVIDIA GPU (our setup, full experience)

**What works:** Everything. Kokoro on CUDA (50ms/clip), MusicGen available, Liquidsoap native, Icecast native, systemd for auto-restart.

**Install path (bare-metal):**
```bash
git clone https://github.com/nmelo/agent-radio.git
cd agent-radio
./install.sh
```

install.sh does:
1. Detect Ubuntu/Debian (fail on other distros with instructions)
2. `apt install icecast2 liquidsoap sox` (or grab Liquidsoap .deb from GitHub releases for latest)
3. Create Python venv, `pip install -e .` (installs kokoro, fastapi, uvicorn, etc.)
4. Copy config.yaml.example to config.yaml, prompt for music directory path
5. Install systemd units for brain and liquidsoap
6. Copy icecast.xml template, set passwords
7. Create /tmp/agent-radio/ directory
8. Print: "Run ./start.sh or systemctl start agent-radio"

**Install path (Docker):**
```bash
git clone https://github.com/nmelo/agent-radio.git
cd agent-radio
cp config.yaml.example config.yaml
# Edit config.yaml: set music_dir, passwords
docker compose --profile gpu up -d
```

### Profile 2: Linux + No GPU (degraded TTS speed)

**What works:** Everything except MusicGen. Kokoro runs on CPU at ~5x realtime (a 10-second announcement takes ~2 seconds to generate). Still within our <10s latency SLA. Music from curated files only.

**What's degraded:**
- TTS latency: ~2s per clip instead of 50ms. Noticeable but acceptable.
- No AI music generation. MusicGen requires GPU.
- torch still installed (CPU-only build), uses ~1-2GB RAM.

**Install path:** Same as Profile 1 but install.sh detects no NVIDIA GPU and:
- Sets `music_ai_enabled: false` in config.yaml
- Installs CPU-only PyTorch: `pip install torch --index-url https://download.pytorch.org/whl/cpu`
- Skips nvidia-container-toolkit for Docker path

### Profile 3: macOS Apple Silicon (Docker required)

**What works:** Full experience via Docker Compose. Kokoro uses ONNX with ARM64 NEON SIMD (~5-10x realtime on M-series). No GPU acceleration (MPS not yet supported by Kokoro). Music from curated files only.

**What's degraded:**
- TTS latency: ~1-2s per clip on M2/M3 Pro. Acceptable.
- No MusicGen (no CUDA).
- Runs in Docker (slight overhead, ~200-400MB RAM for containers).

**Install path:**
```bash
# Prerequisites
brew install docker     # or install Docker Desktop
brew install --cask docker

git clone https://github.com/nmelo/agent-radio.git
cd agent-radio
cp config.yaml.example config.yaml
# Edit config.yaml: set music_dir (will be mounted into container)
docker compose -f deploy/docker/docker-compose.yml up -d
```

Docker Compose handles Icecast, Liquidsoap, and brain. The operator's music directory is bind-mounted into the Liquidsoap container. Dashboard accessible at http://localhost:8001/.

**Alternative for adventurous users:** Install Liquidsoap via OPAM (documented but not recommended):
```bash
brew install opam
opam init
opam install liquidsoap
# Requires CPATH=/opt/homebrew/include LIBRARY_PATH=/opt/homebrew/lib
```
This is documented in an appendix but not the recommended path.

### Profile 4: macOS Intel (Docker required, slower)

**What works:** Same as Apple Silicon but slower. Kokoro on CPU at ~3-5x realtime. Docker containers run natively (no Rosetta needed for x86 images).

**What's degraded:** Everything from Profile 3, plus slower TTS. A 10-second announcement takes ~3-5 seconds to generate. Still within SLA.

**Install path:** Same as Profile 3.

### Profile 5: Docker (any platform)

**What works:** Full stack in containers. GPU passthrough for Linux + NVIDIA. CPU-only everywhere else.

**Why Docker is the primary path:**
- Liquidsoap not in Homebrew (macOS users need it)
- Eliminates system package version conflicts
- Reproducible builds across platforms
- Single `docker compose up` for the whole stack
- GPU passthrough works for Linux+NVIDIA

**What's in the Compose file:**
```yaml
services:
  icecast:
    build: ./docker/icecast
    ports:
      - "${ICECAST_PORT:-8000}:8000"
    volumes:
      - ./config/icecast.xml:/etc/icecast2/icecast.xml:ro
    restart: unless-stopped

  liquidsoap:
    image: savonet/liquidsoap:v2.4.2
    volumes:
      - ./radio.liq:/radio.liq:ro
      - ${MUSIC_DIR:-./music}:/music:ro
      - /tmp/agent-radio:/tmp/agent-radio
    depends_on:
      - icecast
    restart: unless-stopped
    command: liquidsoap /radio.liq

  brain:
    build: .
    ports:
      - "${WEBHOOK_PORT:-8001}:8001"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./dashboard.html:/app/dashboard.html:ro
      - /tmp/agent-radio:/tmp/agent-radio
    depends_on:
      - liquidsoap
    restart: unless-stopped

profiles:
  gpu:
    brain:
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
```

**GPU vs CPU:**
```bash
# With NVIDIA GPU
docker compose -f deploy/docker/docker-compose.yml --profile gpu up -d

# CPU only (macOS, Linux without GPU)
docker compose -f deploy/docker/docker-compose.yml up -d
```

## Docker Details

### Images

| Service | Image | Why |
|---------|-------|-----|
| Icecast | Custom build from `debian:bookworm-slim` + `apt install icecast2` | No widely-adopted official image. Simpler to build our own thin layer. |
| Liquidsoap | `savonet/liquidsoap:v2.4.2` (official) | Official, multi-arch (amd64 + arm64), Debian-based. |
| Brain | Custom build from `python:3.12-slim` | Install our Python deps, copy source. Two-stage build for smaller image. |

### Brain Dockerfile

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY brain.py config.py script_generator.py dashboard.html ./
COPY tts/ ./tts/
COPY music/ ./music/
EXPOSE 8001
CMD ["python", "-m", "brain"]
```

For GPU support, use `nvidia/cuda:12.4-runtime-ubuntu22.04` as base instead of `python:3.12-slim`, then install Python on top.

### Inter-Container Communication

Liquidsoap and brain communicate via Unix socket. In Docker, this is a shared volume:

```yaml
volumes:
  - /tmp/agent-radio:/tmp/agent-radio
```

Both containers mount the same host directory. Brain writes WAV files there, pushes the path to Liquidsoap via socket (also in that directory). Liquidsoap reads the WAV and plays it.

The socket path in config.yaml must match: `/tmp/agent-radio/agent-radio.sock` (inside the shared volume).

### Icecast connects to Liquidsoap

Liquidsoap connects to Icecast as a source client. In Docker, use the service name as hostname:

```liquidsoap
output.icecast(
  host="icecast",    # Docker service name, resolved by Docker DNS
  port=8000,
  ...
)
```

## Bare-Metal Linux: install.sh

### What It Does

```bash
#!/bin/bash
# install.sh - Install Agent Radio on Ubuntu/Debian

1. Check prerequisites (Python 3.12+, sudo access)
2. Detect GPU (nvidia-smi)
3. Install system packages:
   - icecast2 (apt)
   - liquidsoap (apt or .deb from GitHub releases)
   - sox libsox-fmt-all (for audio tools)
   - ffmpeg (for TTS normalization)
4. Create Python venv at ./venv
5. pip install -e . (installs kokoro, fastapi, etc.)
   - If no GPU: pip install torch --index-url .../cpu
6. Generate config.yaml from template:
   - Prompt for music directory (default: ~/Music)
   - Prompt for Icecast password (generate random if blank)
   - Set music_ai_enabled based on GPU detection
7. Set up Icecast:
   - Copy icecast.xml template to /etc/icecast2/icecast.xml
   - Set source password to match config.yaml
8. Install systemd units:
   - agent-radio-brain.service
   - agent-radio-liquidsoap.service
   - Both: Restart=on-failure, RestartSec=5
   - Brain depends on liquidsoap.service
9. Create /tmp/agent-radio/ with correct permissions
10. Print summary: URLs, passwords, next steps
```

### What It Does NOT Do

- Does not start services (operator does `./start.sh` or `systemctl start agent-radio-brain`)
- Does not download music (operator provides their own)
- Does not configure firewall rules (operator's responsibility for LAN access)
- Does not install Docker (bare-metal only)

## Systemd Units (Linux bare-metal)

### agent-radio-liquidsoap.service

```ini
[Unit]
Description=Agent Radio - Liquidsoap Audio Engine
After=icecast2.service
Wants=icecast2.service

[Service]
Type=simple
User=agent-radio
WorkingDirectory=/opt/agent-radio
ExecStart=/usr/bin/liquidsoap /opt/agent-radio/radio.liq
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### agent-radio-brain.service

```ini
[Unit]
Description=Agent Radio - Brain (webhook + TTS + dashboard)
After=agent-radio-liquidsoap.service
Requires=agent-radio-liquidsoap.service

[Service]
Type=simple
User=agent-radio
WorkingDirectory=/opt/agent-radio
ExecStart=/opt/agent-radio/venv/bin/python -m brain
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Management

```bash
# Start everything
sudo systemctl start icecast2 agent-radio-liquidsoap agent-radio-brain

# Stop everything
sudo systemctl stop agent-radio-brain agent-radio-liquidsoap

# Check status
systemctl status agent-radio-brain agent-radio-liquidsoap icecast2

# View logs
journalctl -u agent-radio-brain -f
journalctl -u agent-radio-liquidsoap -f

# Enable auto-start on boot
sudo systemctl enable icecast2 agent-radio-liquidsoap agent-radio-brain
```

## macOS LaunchAgent (for Docker Compose)

For macOS users who want Agent Radio to start on login:

### ~/Library/LaunchAgents/com.agent-radio.plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agent-radio</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/docker</string>
    <string>compose</string>
    <string>-f</string>
    <string>/Users/YOURUSERNAME/agent-radio/deploy/docker/docker-compose.yml</string>
    <string>up</string>
    <string>-d</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/tmp/agent-radio-launch.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/agent-radio-launch.log</string>
</dict>
</plist>
```

Install: `launchctl load ~/Library/LaunchAgents/com.agent-radio.plist`

## Config Generation

### First-Run Experience

When the user runs `./install.sh` or `docker compose up` for the first time with no `config.yaml`:

1. Detect `config.yaml` missing
2. Copy `config.yaml.example` to `config.yaml`
3. If interactive terminal: prompt for key values
4. If non-interactive (Docker, CI): use defaults from example

### Interactive Prompts (install.sh)

```
Agent Radio Setup
-----------------
Music directory [~/Music]: /home/user/Music
Icecast password [auto-generated]: ********
Webhook port [8001]:
TTS voice [af_heart]:
GPU detected: NVIDIA RTX 5080. Enable AI music generation? [y/N]:
```

### Non-Interactive (Docker)

Docker users configure via environment variables in `.env`:

```env
MUSIC_DIR=~/Music
ICECAST_PASSWORD=changeme
WEBHOOK_PORT=8001
ICECAST_PORT=8000
TTS_VOICE=af_heart
```

docker-compose.yml reads these via `${VARIABLE:-default}` syntax.

## Music: Ship Starter Tracks

Ship 3-5 CC0 ambient tracks (~25MB total) so the radio works out of the box. Without music, Liquidsoap has nothing to play and the experience is broken on first run.

### Sources (all CC0/public domain)

- **OpenGameArt.org** CC0 ambient collection: ~80 tracks, confirmed public domain
- **Freesound.org** CC0 ambient drones: individual clips, combine into longer tracks
- **Pixabay Music** ambient category: CC0-equivalent license

### Starter Library

Ship in `music/starter/` directory (git-tracked):

| Track | Duration | Size (128kbps MP3) | Description |
|-------|----------|-------------------|-------------|
| ambient_drone_01.mp3 | 5:00 | ~4.7 MB | Soft pad, deep reverb, gentle |
| ambient_drone_02.mp3 | 5:00 | ~4.7 MB | Lo-fi warm analog, tape hiss |
| ambient_drone_03.mp3 | 5:00 | ~4.7 MB | Slow evolving tones, minimal |
| ambient_piano_01.mp3 | 4:00 | ~3.8 MB | Sparse piano, reverb, contemplative |
| ambient_nature_01.mp3 | 5:00 | ~4.7 MB | Rain + gentle synth pad |

Total: ~23 MB. Small enough for git. config.yaml default `music_dir` points to `./music/starter/`.

### Operator Adds Their Own

After install, the operator drops their own ambient files into the music directory. Liquidsoap watches the directory (`reload_mode="watch"`) and picks up new files automatically. The starter tracks mix in with the operator's library.

## README Quick Start

### Docker (any platform)

```bash
git clone https://github.com/nmelo/agent-radio.git
cd agent-radio
cp config.yaml.example config.yaml
docker compose -f deploy/docker/docker-compose.yml up -d

# Open dashboard
open http://localhost:8001

# Test an announcement
curl -X POST http://localhost:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail": "Agent Radio is live"}'
```

### Linux Bare-Metal

```bash
git clone https://github.com/nmelo/agent-radio.git
cd agent-radio
sudo ./install.sh
./start.sh
```

### Verify It Works

```bash
# Stream is live?
curl -sf http://localhost:8000/stream > /dev/null && echo "Stream: OK" || echo "Stream: DOWN"

# Brain is running?
curl -sf http://localhost:8001/now-playing && echo "Brain: OK" || echo "Brain: DOWN"

# Send a test announcement
curl -X POST http://localhost:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail": "Hello from Agent Radio"}'
```

## Release Artifacts

### GitHub Release

Each release includes:
- Source tarball (git archive)
- Docker Compose file (standalone, no git clone needed)
- Platform notes in release description
- Changelog

### Repository Structure Additions

```
agent-radio/
  docker-compose.yml          # Primary install path
  docker/
    brain/Dockerfile          # Brain container
    brain/Dockerfile.gpu      # Brain container with CUDA
    icecast/Dockerfile        # Thin Icecast container
    icecast/icecast.xml       # Default Icecast config
  install.sh                  # Linux bare-metal installer
  config.yaml.example         # Config template
  music/
    starter/                  # CC0 ambient tracks shipped with repo
  systemd/
    agent-radio-brain.service
    agent-radio-liquidsoap.service
  launchd/
    com.agent-radio.plist     # macOS LaunchAgent template
```

## Implementation Order

1. **Docker Compose file** - Get Icecast + Liquidsoap + brain running in containers. Test on Linux and macOS.
2. **Dockerfiles** - Brain (CPU and GPU variants), Icecast thin layer.
3. **Starter music** - Download and curate 3-5 CC0 ambient tracks.
4. **config.yaml.example** - Template with comments and sensible defaults.
5. **install.sh** - Linux bare-metal installer. Detect GPU, install packages, create venv, systemd units.
6. **systemd units** - brain and liquidsoap service files.
7. **LaunchAgent plist** - macOS Docker auto-start.
8. **README rewrite** - Quick start for Docker and bare-metal paths.
9. **GitHub Release workflow** - Tag, changelog, release notes.

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Liquidsoap Docker image is large (~500MB+) | Slow first pull | Document expected download size. Image is cached after first pull. |
| Docker Desktop license on macOS (commercial use) | Legal concern for companies | Document alternatives: colima, podman, orbstack (all free). |
| Kokoro model downloads on first run (~200MB) | Slow first announcement | Pre-download in Dockerfile. Or document the delay. |
| GPU passthrough requires nvidia-container-toolkit | Extra install step on Linux | install.sh detects and installs it. Docker path documents it. |
| Starter music quality varies | Bad first impression | Curate carefully. Test each track sounds good as ambient background. |
| Socket communication across Docker containers | Potential permission issues | Shared volume with consistent UID/GID. Document troubleshooting. |