#!/usr/bin/env bash
#
# Radio Agent installer
#
# One-liner:
#   curl -sSL https://radioagent.live/install.sh | bash
#
# Docker (all platforms) or bare-metal (Linux only).
#
set -euo pipefail

REPO="https://github.com/nmelo/radioagent.git"
INSTALL_DIR="$HOME/radioagent"

# ─────────────────────────────────────────────────────────────────────────────
# Colors & Output
# ─────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

if [ ! -t 1 ]; then
    RED='' GREEN='' YELLOW='' CYAN='' DIM='' BOLD='' RESET=''
fi

info()  { echo -e "  ${CYAN}->${RESET} $1"; }
ok()    { echo -e "  ${GREEN}ok${RESET} $1"; }
warn()  { echo -e "  ${YELLOW}!!${RESET} $1"; }
fail()  { echo -e "  ${RED}xx${RESET} $1" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "  ${BOLD}Radio Agent${RESET} ${DIM}installer${RESET}"
echo -e "  ${DIM}Ambient awareness for AI coding sessions${RESET}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Prerequisites
# ─────────────────────────────────────────────────────────────────────────────

if ! command -v git &>/dev/null; then
    fail "git is required but not installed"
fi

HAS_DOCKER=false
HAS_COMPOSE=false

if command -v docker &>/dev/null; then
    HAS_DOCKER=true
    if docker compose version &>/dev/null 2>&1; then
        HAS_COMPOSE=true
    fi
fi

# Determine install method
METHOD=""
if [ "$HAS_COMPOSE" = true ]; then
    # The CLI binary and compose plugin exist even when the daemon is stopped.
    # Verify the daemon is actually responsive before committing to docker method.
    DAEMON_UP=false
    if command -v timeout &>/dev/null; then
        timeout 5 docker info &>/dev/null 2>&1 && DAEMON_UP=true || true
    else
        docker info &>/dev/null 2>&1 && DAEMON_UP=true || true
    fi
    if [ "$DAEMON_UP" = false ]; then
        fail "Docker is installed but the daemon is not running. Start Docker Desktop and re-run this installer."
    fi
    METHOD="docker"
elif [ "$(uname -s)" = "Linux" ]; then
    METHOD="bare-metal"
else
    echo ""
    fail "Docker not found. On macOS, install Docker Desktop first:
    https://docs.docker.com/desktop/install/mac-install/

    Then re-run this installer."
fi

ok "Install method: ${BOLD}${METHOD}${RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# Clone
# ─────────────────────────────────────────────────────────────────────────────

if [ -d "$INSTALL_DIR" ]; then
    info "Updating existing install at ${BOLD}${INSTALL_DIR}${RESET}..."
    git -C "$INSTALL_DIR" pull --rebase --quiet
    ok "Repository updated"
else
    info "Cloning to ${BOLD}${INSTALL_DIR}${RESET}..."
    git clone --quiet "$REPO" "$INSTALL_DIR"
    ok "Repository cloned"
fi

cd "$INSTALL_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Music directory
# ─────────────────────────────────────────────────────────────────────────────

MUSIC_DIR="$HOME/Music"
if [ ! -d "$MUSIC_DIR" ]; then
    mkdir -p "$MUSIC_DIR"
    info "Created ${MUSIC_DIR} (add .mp3/.ogg/.flac files here)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Docker install
# ─────────────────────────────────────────────────────────────────────────────

install_docker() {
    info "Building containers..."

    # Write .env with default profile and music path (placed alongside compose file)
    cat > "$INSTALL_DIR/deploy/docker/.env" <<EOF
COMPOSE_PROFILES=cpu
MUSIC_DIR=$MUSIC_DIR
EOF

    docker compose -f deploy/docker/docker-compose.yml build --quiet 2>&1 | tail -1
    ok "Containers built"

    info "Starting services..."
    docker compose -f deploy/docker/docker-compose.yml up -d 2>&1 | tail -3
    ok "Services running"

    # Wait for icecast health
    info "Waiting for Icecast..."
    local tries=0
    while [ $tries -lt 15 ]; do
        if docker compose -f deploy/docker/docker-compose.yml ps --format json 2>/dev/null | grep -q '"healthy"'; then
            break
        fi
        sleep 1
        tries=$((tries + 1))
    done

    if [ $tries -ge 15 ]; then
        warn "Icecast health check timed out (services may still be starting)"
    else
        ok "Icecast healthy"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Bare-metal install (Linux only)
# ─────────────────────────────────────────────────────────────────────────────

install_bare_metal() {
    if [ "$EUID" -ne 0 ]; then
        info "Bare-metal install needs root. Re-running with sudo..."
        exec sudo bash "$INSTALL_DIR/scripts/install.sh"
    else
        exec bash "$INSTALL_DIR/scripts/install.sh"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────────────────

case "$METHOD" in
    docker)     install_docker ;;
    bare-metal) install_bare_metal ;;
esac

# ─────────────────────────────────────────────────────────────────────────────
# Detect LAN IP
# ─────────────────────────────────────────────────────────────────────────────

LAN_IP="localhost"
if command -v hostname &>/dev/null; then
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -n "${DETECTED_IP:-}" ]; then
        LAN_IP="$DETECTED_IP"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "  ${GREEN}${BOLD}Radio Agent is running${RESET}"
echo ""
echo -e "  Stream:      ${BOLD}http://${LAN_IP}:8000/stream${RESET}"
echo -e "  Dashboard:   ${BOLD}http://${LAN_IP}:8001${RESET}"
echo -e "  Webhook:     ${BOLD}http://${LAN_IP}:8001/announce${RESET}"
echo -e "  Music:       ${DIM}${MUSIC_DIR}${RESET}"
echo ""
echo -e "  ${BOLD}Test it:${RESET}"
echo -e "    curl -s -X POST http://${LAN_IP}:8001/announce \\"
echo -e "      -H 'Content-Type: application/json' \\"
echo -e "      -d '{\"detail\": \"Radio Agent is live\"}'"
echo ""
echo -e "  ${BOLD}Manage:${RESET}"
echo -e "    cd ${INSTALL_DIR}"
echo -e "    docker compose -f deploy/docker/docker-compose.yml logs -f     ${DIM}# view logs${RESET}"
echo -e "    docker compose -f deploy/docker/docker-compose.yml restart     ${DIM}# restart${RESET}"
echo -e "    docker compose -f deploy/docker/docker-compose.yml down        ${DIM}# stop${RESET}"
echo ""
echo -e "  ${DIM}Docs: https://radioagent.live${RESET}"
echo ""
