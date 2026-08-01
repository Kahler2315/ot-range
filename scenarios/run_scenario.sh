#!/usr/bin/env bash
# Run one scenario end to end: start the plant behind the sensor, generate
# normal HMI traffic, run the attack, then report what the sensor caught.
#
# Usage: scenarios/run_scenario.sh S01|S03|S05
#
# Simulated range only. See SECURITY.md.
set -euo pipefail

SCENARIO="${1:-}"
if [[ -z "$SCENARIO" ]]; then
  echo "usage: $0 S01|S03|S05" >&2
  exit 64
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-.venv/bin/python}"
SIM_PORT="${SIM_PORT:-5502}"
TAP_PORT="${TAP_PORT:-5020}"
HMI_IP="127.0.0.1"
ATTACKER_IP="127.0.0.2"
LOG_DIR="${LOG_DIR:-logs}"
MODBUS_LOG="$LOG_DIR/modbus-${SCENARIO}.log"

case "$SCENARIO" in
  S01) START_LEVEL=55; SPEED=60 ;;
  S03) START_LEVEL=90; SPEED=600 ;;
  S05) START_LEVEL=90; SPEED=600 ;;
  *) echo "unknown scenario: $SCENARIO (expected S01, S03, or S05)" >&2; exit 64 ;;
esac

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

wait_for_port() {
  local port=$1
  for _ in $(seq 1 60); do
    if "$PYTHON" -c "
import socket,sys
try:
    socket.create_connection(('127.0.0.1', $port), timeout=0.2).close()
except OSError:
    sys.exit(1)
" 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  echo "timed out waiting for port $port" >&2
  return 1
}

mkdir -p "$LOG_DIR"
rm -f "$MODBUS_LOG"

echo "[*] starting plant (level=${START_LEVEL}%, speed=${SPEED}x)"
"$PYTHON" -m process_sim.server --port "$SIM_PORT" --speed "$SPEED" \
  --tick 1 --level "$START_LEVEL" >"$LOG_DIR/sim.out" 2>&1 &
PIDS+=($!)
wait_for_port "$SIM_PORT"

echo "[*] starting sensor tap -> $MODBUS_LOG"
"$PYTHON" -m sensor.tap --listen-port "$TAP_PORT" --upstream-port "$SIM_PORT" \
  --log "$MODBUS_LOG" >"$LOG_DIR/tap.out" 2>&1 &
PIDS+=($!)
wait_for_port "$TAP_PORT"

echo "[*] generating normal HMI traffic"
"$PYTHON" -m tools.hmi_poll --port "$TAP_PORT" --source-ip "$HMI_IP" \
  --interval 0.5 --cycles 0 >"$LOG_DIR/hmi.out" 2>&1 &
PIDS+=($!)
sleep 2

echo "[*] running $SCENARIO"
echo
case "$SCENARIO" in
  S01)
    "$PYTHON" -m attacker.s01_recon --port "$TAP_PORT" --source-ip "$ATTACKER_IP" \
      --sweep-count 8 --unit-id-max 5
    ;;
  S03)
    "$PYTHON" -m attacker.s03_unauthorized_command --port "$TAP_PORT" \
      --source-ip "$ATTACKER_IP" --timeout 120 --poll 1.5
    ;;
  S05)
    "$PYTHON" -m attacker.s05_manipulation_of_view --port "$TAP_PORT" \
      --source-ip "$ATTACKER_IP" --timeout 120 --poll 1.5
    ;;
esac

echo
echo "============================================================"
echo " WHAT THE SENSOR CAUGHT"
echo "============================================================"
"$PYTHON" -m sensor.detect "$MODBUS_LOG" || true

echo
echo "[i] full capture: $MODBUS_LOG"
echo "[i] walkthrough : scenarios/*/briefing.md, then answer-key.md"
