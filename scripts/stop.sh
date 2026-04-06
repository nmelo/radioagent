#!/usr/bin/env bash
# Agent Radio - graceful shutdown: brain -> Liquidsoap -> Icecast
set -uo pipefail

RADIO_DIR="${RADIO_DIR:-/opt/agent-radio}"
PIDDIR="$RADIO_DIR"
ICECAST_PID="$PIDDIR/icecast.pid"
LIQUIDSOAP_PID="$PIDDIR/liquidsoap.pid"
BRAIN_PID="$PIDDIR/brain.pid"
SOCKET_PATH="/tmp/agent-radio.sock"
DRAIN_TIMEOUT=10

stopped_any=false

stop_process() {
    local name=$1 pidfile=$2 timeout=${3:-10}

    if [ ! -f "$pidfile" ]; then
        return 0
    fi

    local pid
    pid=$(cat "$pidfile")

    if ! kill -0 "$pid" 2>/dev/null; then
        echo "  $name (pid $pid) already dead, removing stale pidfile"
        rm -f "$pidfile"
        return 0
    fi

    echo "  Stopping $name (pid $pid)..."
    kill -TERM "$pid" 2>/dev/null || true

    local elapsed=0
    while kill -0 "$pid" 2>/dev/null; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [ $elapsed -ge $timeout ]; then
            echo "  $name did not exit after ${timeout}s, sending SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
            break
        fi
    done

    rm -f "$pidfile"
    stopped_any=true
    echo "  $name stopped"
}

echo "Agent Radio shutting down..."
echo ""

# 1. Brain first (stop accepting webhooks, drain in-flight TTS)
stop_process "brain" "$BRAIN_PID" "$DRAIN_TIMEOUT"

# 2. Liquidsoap (finishes current audio, disconnects from Icecast)
stop_process "Liquidsoap" "$LIQUIDSOAP_PID" 5
rm -f "$SOCKET_PATH"

# 3. Icecast last (started via systemctl, so stop via systemctl)
rm -f "$ICECAST_PID"
if pgrep -x icecast2 &>/dev/null; then
    echo "  Stopping Icecast..."
    sudo systemctl stop icecast2 2>/dev/null || sudo killall icecast2 2>/dev/null || true
    stopped_any=true
    echo "  Icecast stopped"
fi

if [ "$stopped_any" = false ]; then
    echo "  Nothing was running."
fi

echo ""
echo "Agent Radio stopped."
