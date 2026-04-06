#!/usr/bin/env bash
# Agent Radio - start all services in order: Icecast -> Liquidsoap -> brain
set -uo pipefail

# Ignore SIGHUP so children (Liquidsoap, brain) survive SSH disconnects.
# stop.sh uses SIGTERM, not SIGHUP, so this doesn't interfere with shutdown.
trap '' HUP

RADIO_DIR="${RADIO_DIR:-/opt/agent-radio}"
PIDDIR="$RADIO_DIR"
ICECAST_PID="$PIDDIR/icecast.pid"
LIQUIDSOAP_PID="$PIDDIR/liquidsoap.pid"
BRAIN_PID="$PIDDIR/brain.pid"
SOCKET_PATH="/tmp/agent-radio.sock"
SOCKET_TIMEOUT=15

# Read config values for port display
ICECAST_PORT=8000
WEBHOOK_PORT=8001
if command -v python3 &>/dev/null && [ -f "$RADIO_DIR/config.yaml" ]; then
    ICECAST_PORT=$(python3 -c "
import yaml
with open('$RADIO_DIR/config.yaml') as f:
    c = yaml.safe_load(f)
print(c.get('icecast_port', 8000))
" 2>/dev/null || echo 8000)
    WEBHOOK_PORT=$(python3 -c "
import yaml
with open('$RADIO_DIR/config.yaml') as f:
    c = yaml.safe_load(f)
print(c.get('webhook_port', 8001))
" 2>/dev/null || echo 8001)
fi

HOSTNAME=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

full_shutdown() {
    echo ""
    echo "start.sh: received shutdown signal, stopping all services..."
    "$RADIO_DIR/scripts/stop.sh" 2>/dev/null || true
    exit 0
}

pid_alive() {
    [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null
}

# port_status: "running" (our process), "conflict" (other process), "free"
check_port() {
    local port=$1 name=$2 pidfile=$3
    if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
        if pid_alive "$pidfile"; then
            echo "running"
        else
            echo "conflict"
        fi
    else
        echo "free"
    fi
}

die() {
    echo "start.sh: ERROR - $1" >&2
    exit 1
}

# --- Pre-flight checks ---

echo "Agent Radio starting..."
echo ""

# Check for stale PID files
for pidfile in "$ICECAST_PID" "$LIQUIDSOAP_PID" "$BRAIN_PID"; do
    if [ -f "$pidfile" ] && ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        rm -f "$pidfile"
    fi
done

# --- 1. Icecast ---

ICECAST_SKIPPED=false
icecast_status=$(check_port "$ICECAST_PORT" "Icecast" "$ICECAST_PID")

case "$icecast_status" in
    running)
        echo "Icecast already running (pid $(cat "$ICECAST_PID")), skipping"
        ICECAST_SKIPPED=true
        ;;
    conflict)
        # Port in use but no pidfile. Check if it's actually Icecast (started by systemctl).
        ICECAST_REAL_PID=$(pgrep -x icecast2 | head -1 || true)
        if [ -n "$ICECAST_REAL_PID" ]; then
            echo "Icecast already running via systemd (pid $ICECAST_REAL_PID), adopting"
            echo "$ICECAST_REAL_PID" > "$ICECAST_PID"
            ICECAST_SKIPPED=true
        else
            die "port $ICECAST_PORT in use by unknown process. Check: ss -tlnp | grep :$ICECAST_PORT"
        fi
        ;;
    free)
        echo "Starting Icecast on port $ICECAST_PORT..."
        sudo systemctl start icecast2
        sleep 1

        ICECAST_REAL_PID=$(pgrep -x icecast2 | head -1 || true)
        if [ -z "$ICECAST_REAL_PID" ]; then
            die "Icecast failed to start. Check: sudo systemctl status icecast2"
        fi
        echo "$ICECAST_REAL_PID" > "$ICECAST_PID"
        echo "  Icecast started (pid $ICECAST_REAL_PID)"
        ;;
esac

# --- 2. Liquidsoap ---

LIQUIDSOAP_SKIPPED=false
if pid_alive "$LIQUIDSOAP_PID"; then
    echo "Liquidsoap already running (pid $(cat "$LIQUIDSOAP_PID")), skipping"
    LIQUIDSOAP_SKIPPED=true
else
    rm -f "$SOCKET_PATH"

    echo "Starting Liquidsoap..."
    liquidsoap "$RADIO_DIR/config/radio.liq" &
    echo $! > "$LIQUIDSOAP_PID"
    echo "  Liquidsoap started (pid $(cat "$LIQUIDSOAP_PID"))"

    echo "  Waiting for Liquidsoap socket (up to ${SOCKET_TIMEOUT}s)..."
    elapsed=0
    while [ ! -S "$SOCKET_PATH" ]; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [ $elapsed -ge $SOCKET_TIMEOUT ]; then
            kill "$(cat "$LIQUIDSOAP_PID")" 2>/dev/null || true
            rm -f "$LIQUIDSOAP_PID"
            die "Liquidsoap socket did not appear after ${SOCKET_TIMEOUT}s"
        fi
        if ! kill -0 "$(cat "$LIQUIDSOAP_PID")" 2>/dev/null; then
            rm -f "$LIQUIDSOAP_PID"
            die "Liquidsoap crashed before socket appeared"
        fi
    done
    echo "  Socket ready at $SOCKET_PATH"
fi

# --- 3. Brain ---

BRAIN_SKIPPED=false
brain_status=$(check_port "$WEBHOOK_PORT" "brain" "$BRAIN_PID")

case "$brain_status" in
    running)
        echo "Brain already running (pid $(cat "$BRAIN_PID")), skipping"
        BRAIN_SKIPPED=true
        ;;
    conflict)
        if [ "$LIQUIDSOAP_SKIPPED" = false ] && [ -f "$LIQUIDSOAP_PID" ]; then
            kill "$(cat "$LIQUIDSOAP_PID")" 2>/dev/null || true
            rm -f "$LIQUIDSOAP_PID"
        fi
        die "port $WEBHOOK_PORT in use but not by brain. Check: ss -tlnp | grep :$WEBHOOK_PORT"
        ;;
    free)
        echo "Starting brain on port $WEBHOOK_PORT..."
        cd "$RADIO_DIR"
        "$RADIO_DIR/venv/bin/python" -m brain &
        echo $! > "$BRAIN_PID"

        brain_wait=0
        while ! ss -tlnp 2>/dev/null | grep -q ":${WEBHOOK_PORT} "; do
            sleep 1
            brain_wait=$((brain_wait + 1))
            if [ $brain_wait -ge 30 ]; then
                kill "$(cat "$BRAIN_PID")" 2>/dev/null || true
                rm -f "$BRAIN_PID"
                die "brain did not bind port $WEBHOOK_PORT after 30s"
            fi
            if ! kill -0 "$(cat "$BRAIN_PID")" 2>/dev/null; then
                rm -f "$BRAIN_PID"
                die "brain crashed on startup"
            fi
        done
        echo "  Brain started (pid $(cat "$BRAIN_PID"))"
        ;;
esac

# --- Ready ---

echo ""
echo "============================================"
echo "  Agent Radio is live!"
echo ""
echo "  Stream:  http://${HOSTNAME}:${ICECAST_PORT}/stream"
echo "  Webhook: http://${HOSTNAME}:${WEBHOOK_PORT}/announce"
echo "  Admin:   http://${HOSTNAME}:${ICECAST_PORT}/admin/"
echo "============================================"
echo ""
echo "Stop with: $RADIO_DIR/scripts/stop.sh"

# --- Monitor ---
# Only SIGINT/SIGTERM to start.sh itself triggers full shutdown.
# Brain exit is logged but does NOT kill Liquidsoap/Icecast (music never stops).

trap full_shutdown INT TERM

# Monitor loop: check brain health, keep Liquidsoap/Icecast running
while true; do
    # Check brain
    if [ -f "$BRAIN_PID" ] && ! kill -0 "$(cat "$BRAIN_PID")" 2>/dev/null; then
        echo "start.sh: WARNING - brain exited. Stream continues. Restart brain or run stop.sh."
        rm -f "$BRAIN_PID"
    fi

    # Check Liquidsoap
    if [ -f "$LIQUIDSOAP_PID" ] && ! kill -0 "$(cat "$LIQUIDSOAP_PID")" 2>/dev/null; then
        echo "start.sh: WARNING - Liquidsoap exited unexpectedly."
        rm -f "$LIQUIDSOAP_PID"
    fi

    sleep 5
done
