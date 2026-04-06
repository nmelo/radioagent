#!/bin/bash
set -euo pipefail

# install.sh - Install Agent Radio on Ubuntu/Debian bare-metal
# Requires: Ubuntu 22.04+ or Debian 12+
# Usage: sudo ./install.sh

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$INSTALL_DIR/venv"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[info]${NC} $1"; }
ok()    { echo -e "${GREEN}[ok]${NC} $1"; }
warn()  { echo -e "${AMBER}[warn]${NC} $1"; }
fail()  { echo -e "${RED}[error]${NC} $1"; exit 1; }
ask()   { echo -en "${BOLD}$1${NC}"; }

# --- Step 1: Check distro ---
info "Checking distribution..."

if [ ! -f /etc/os-release ]; then
    fail "Cannot detect OS. This installer requires Ubuntu 22.04+ or Debian 12+."
fi

. /etc/os-release

case "$ID" in
    ubuntu|debian)
        ok "Detected $PRETTY_NAME"
        ;;
    *)
        fail "Unsupported distribution: $PRETTY_NAME
Agent Radio bare-metal install requires Ubuntu or Debian.
For other platforms, use Docker: docker compose -f deploy/docker/docker-compose.yml up -d"
        ;;
esac

# Check version minimums
if [ "$ID" = "ubuntu" ]; then
    MAJOR_VER=$(echo "$VERSION_ID" | cut -d. -f1)
    if [ "$MAJOR_VER" -lt 22 ]; then
        fail "Ubuntu 22.04 or later required (detected $VERSION_ID)"
    fi
elif [ "$ID" = "debian" ]; then
    MAJOR_VER=$(echo "$VERSION_ID" | cut -d. -f1)
    if [ "$MAJOR_VER" -lt 12 ]; then
        fail "Debian 12 or later required (detected $VERSION_ID)"
    fi
fi

# --- Check sudo ---
if [ "$EUID" -ne 0 ]; then
    fail "Please run with sudo: sudo ./install.sh"
fi

# Determine the real user (the one who called sudo)
REAL_USER="${SUDO_USER:-$(whoami)}"
REAL_HOME=$(eval echo "~$REAL_USER")

# --- Step 2: Install system packages ---
info "Updating package lists..."
apt-get update -qq

info "Installing system packages: icecast2 liquidsoap sox socat..."

# Prevent icecast2 from prompting during install
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq icecast2 liquidsoap sox socat python3-venv python3-dev > /dev/null 2>&1

ok "System packages installed"

# Check liquidsoap version
LS_VERSION=$(liquidsoap --version 2>/dev/null | head -1 || echo "unknown")
info "Liquidsoap version: $LS_VERSION"

# --- Step 3: Python venv ---
info "Creating Python virtual environment..."

if [ -d "$VENV_DIR" ]; then
    warn "Existing venv found at $VENV_DIR, reusing it"
else
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

ok "Python dependencies installed in $VENV_DIR"

# --- Detect GPU ---
HAS_GPU=false
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    HAS_GPU=true
    ok "NVIDIA GPU detected: $GPU_NAME"
else
    info "No NVIDIA GPU detected (CPU-only mode)"
fi

# --- Step 4: Config generation ---
echo ""
echo -e "${BOLD}Agent Radio Setup${NC}"
echo "────────────────────"

if [ -f "$INSTALL_DIR/config.yaml" ] && [ ! -f "$INSTALL_DIR/config.yaml.bak" ]; then
    warn "config.yaml already exists"
    ask "Overwrite? [y/N]: "
    read -r OVERWRITE
    if [[ ! "$OVERWRITE" =~ ^[Yy]$ ]]; then
        info "Keeping existing config.yaml"
        # Still need the password for icecast.xml
        ICECAST_PW=$(grep 'icecast_password:' "$INSTALL_DIR/config.yaml" | awk '{print $2}' || echo "changeme")
        MUSIC_DIR_PATH=$(grep 'music_dir:' "$INSTALL_DIR/config.yaml" | awk '{print $2}' || echo "$REAL_HOME/Music")
        SKIP_CONFIG=true
    fi
fi

if [ "${SKIP_CONFIG:-false}" != "true" ]; then
    # Music directory
    ask "Music directory [$REAL_HOME/Music]: "
    read -r MUSIC_DIR_PATH
    MUSIC_DIR_PATH="${MUSIC_DIR_PATH:-$REAL_HOME/Music}"

    # Expand ~ if present
    MUSIC_DIR_PATH="${MUSIC_DIR_PATH/#\~/$REAL_HOME}"

    if [ ! -d "$MUSIC_DIR_PATH" ]; then
        warn "Directory $MUSIC_DIR_PATH does not exist"
        ask "Create it? [Y/n]: "
        read -r CREATE_DIR
        if [[ ! "$CREATE_DIR" =~ ^[Nn]$ ]]; then
            mkdir -p "$MUSIC_DIR_PATH"
            chown "$REAL_USER:$REAL_USER" "$MUSIC_DIR_PATH"
            ok "Created $MUSIC_DIR_PATH"
        fi
    fi

    # Copy starter music if the music dir is empty
    if [ -d "$INSTALL_DIR/audio/starter-music" ] && [ -d "$MUSIC_DIR_PATH" ]; then
        MUSIC_COUNT=$(find "$MUSIC_DIR_PATH" -maxdepth 1 -name "*.mp3" -o -name "*.ogg" -o -name "*.flac" -o -name "*.wav" 2>/dev/null | wc -l)
        if [ "$MUSIC_COUNT" -eq 0 ]; then
            info "Copying starter music to $MUSIC_DIR_PATH..."
            cp "$INSTALL_DIR"/audio/starter-music/*.mp3 "$MUSIC_DIR_PATH/" 2>/dev/null || true
            chown "$REAL_USER:$REAL_USER" "$MUSIC_DIR_PATH"/*.mp3 2>/dev/null || true
            ok "Starter tracks copied (CC0 public domain)"
        fi
    fi

    # Icecast password
    DEFAULT_PW=$(openssl rand -hex 12 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)
    ask "Icecast source password [$DEFAULT_PW]: "
    read -r ICECAST_PW
    ICECAST_PW="${ICECAST_PW:-$DEFAULT_PW}"

    # Generate config.yaml
    if [ -f "$INSTALL_DIR/config.yaml" ]; then
        cp "$INSTALL_DIR/config.yaml" "$INSTALL_DIR/config.yaml.bak"
        info "Backed up existing config to config.yaml.bak"
    fi

    sed -e "s|music_dir:.*|music_dir: $MUSIC_DIR_PATH|" \
        -e "s|icecast_password:.*|icecast_password: $ICECAST_PW|" \
        -e "s|tones_dir:.*|tones_dir: $INSTALL_DIR/audio/tones|" \
        "$INSTALL_DIR/config/config.yaml.example" > "$INSTALL_DIR/config.yaml"

    # Disable AI music if no GPU
    if [ "$HAS_GPU" = false ]; then
        sed -i 's|music_ai_enabled:.*|music_ai_enabled: false|' "$INSTALL_DIR/config.yaml"
    fi

    ok "config.yaml generated"
fi

# --- Step 5: Icecast XML ---
info "Configuring Icecast..."

ICECAST_XML="/etc/icecast2/icecast.xml"
cat > "$ICECAST_XML" <<ICEXML
<icecast>
    <location>Agent Radio</location>
    <admin>admin@localhost</admin>

    <limits>
        <clients>100</clients>
        <sources>4</sources>
        <queue-size>524288</queue-size>
        <client-timeout>30</client-timeout>
        <header-timeout>15</header-timeout>
        <source-timeout>10</source-timeout>
        <burst-on-connect>1</burst-on-connect>
        <burst-size>65535</burst-size>
    </limits>

    <authentication>
        <source-password>$ICECAST_PW</source-password>
        <relay-password>$ICECAST_PW</relay-password>
        <admin-user>admin</admin-user>
        <admin-password>$ICECAST_PW</admin-password>
    </authentication>

    <hostname>localhost</hostname>

    <listen-socket>
        <port>8000</port>
    </listen-socket>

    <fileserve>1</fileserve>

    <paths>
        <basedir>/usr/share/icecast2</basedir>
        <logdir>/var/log/icecast2</logdir>
        <webroot>/usr/share/icecast2/web</webroot>
        <adminroot>/usr/share/icecast2/admin</adminroot>
    </paths>

    <logging>
        <accesslog>access.log</accesslog>
        <errorlog>error.log</errorlog>
        <loglevel>3</loglevel>
    </logging>

    <security>
        <chroot>0</chroot>
        <changeowner>
            <user>icecast2</user>
            <group>icecast</group>
        </changeowner>
    </security>
</icecast>
ICEXML

# Enable icecast2 to start (Debian/Ubuntu disables it by default)
if [ -f /etc/default/icecast2 ]; then
    sed -i 's/ENABLE=false/ENABLE=true/' /etc/default/icecast2
fi

ok "Icecast configured at $ICECAST_XML"

# --- Step 6: Create tmp directory ---
mkdir -p /tmp/agent-radio
chmod 1777 /tmp/agent-radio
ok "Created /tmp/agent-radio"

# --- Step 7: Systemd units (optional) ---
echo ""
ask "Install systemd service units? [Y/n]: "
read -r INSTALL_SYSTEMD

if [[ ! "$INSTALL_SYSTEMD" =~ ^[Nn]$ ]]; then
    cat > /etc/systemd/system/agent-radio-liquidsoap.service <<UNIT
[Unit]
Description=Agent Radio - Liquidsoap Audio Engine
After=icecast2.service
Wants=icecast2.service

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/liquidsoap $INSTALL_DIR/config/radio.liq
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

    cat > /etc/systemd/system/agent-radio-brain.service <<UNIT
[Unit]
Description=Agent Radio - Brain (webhook + TTS + dashboard)
After=agent-radio-liquidsoap.service
Requires=agent-radio-liquidsoap.service

[Service]
Type=simple
User=$REAL_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/python -m brain
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$INSTALL_DIR/src

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    ok "Systemd units installed"

    ask "Enable services to start on boot? [y/N]: "
    read -r ENABLE_BOOT
    if [[ "$ENABLE_BOOT" =~ ^[Yy]$ ]]; then
        systemctl enable icecast2 agent-radio-liquidsoap agent-radio-brain
        ok "Services enabled for boot"
    fi
fi

# --- Step 8: Summary ---
echo ""
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo -e "${BOLD}  Agent Radio installed${NC}"
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo ""
echo -e "  Stream URL:    ${CYAN}http://localhost:8000/stream${NC}"
echo -e "  Dashboard:     ${CYAN}http://localhost:8001${NC}"
echo -e "  Webhook URL:   ${CYAN}http://localhost:8001/announce${NC}"
echo -e "  Music dir:     ${MUSIC_DIR_PATH}"
echo -e "  Config:        ${INSTALL_DIR}/config.yaml"
echo ""
echo -e "${BOLD}Start manually:${NC}"
echo "  sudo systemctl start icecast2"
echo "  liquidsoap $INSTALL_DIR/config/radio.liq &"
echo "  PYTHONPATH=$INSTALL_DIR/src $VENV_DIR/bin/python -m brain &"
echo ""
if [[ ! "${INSTALL_SYSTEMD:-y}" =~ ^[Nn]$ ]]; then
echo -e "${BOLD}Start with systemd:${NC}"
echo "  sudo systemctl start icecast2 agent-radio-liquidsoap agent-radio-brain"
echo ""
echo -e "${BOLD}View logs:${NC}"
echo "  journalctl -u agent-radio-brain -f"
echo "  journalctl -u agent-radio-liquidsoap -f"
echo ""
fi
echo -e "${BOLD}Test it:${NC}"
echo "  curl -X POST http://localhost:8001/announce \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"detail\": \"Hello from Agent Radio\"}'"
echo ""
