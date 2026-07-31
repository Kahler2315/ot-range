#!/usr/bin/env bash
# End-to-end smoke test: start the real Modbus TCP slave, verify auto
# control cycles the pump at its setpoints, then run an S03 preview
# (unauthorized coil write) and verify it drives the tank to overflow and
# the pump to a deadhead fault. Exits non-zero on any failed assertion.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-.venv/bin/python}"
PORT="${SMOKE_PORT:-15502}"
HOST="127.0.0.1"
SPEED="${SMOKE_SPEED:-300}"
START_LEVEL="${SMOKE_START_LEVEL:-38}"

SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "[smoke] starting Modbus slave on ${HOST}:${PORT} (speed=${SPEED}x, start level=${START_LEVEL}%)"
"$PYTHON" -m process_sim.server --host "$HOST" --port "$PORT" --speed "$SPEED" --tick 1 --level "$START_LEVEL" \
  > /tmp/ot-range-smoke-server.log 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 50); do
  if "$PYTHON" -c "
import socket
s = socket.create_connection(('$HOST', $PORT), timeout=0.2)
s.close()
" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

"$PYTHON" - "$HOST" "$PORT" <<'PYEOF'
import sys
import time

from pymodbus.client import ModbusTcpClient

from common.pointmap import load

host, port = sys.argv[1], int(sys.argv[2])
pm = load()
client = ModbusTcpClient(host, port=port)
if not client.connect():
    print("[smoke] FAIL: could not connect to Modbus slave")
    sys.exit(1)


def read(tag):
    p = pm[tag]
    if p.table == "coils":
        return bool(client.read_coils(p.index, count=1).bits[0])
    if p.table == "discrete_inputs":
        return bool(client.read_discrete_inputs(p.index, count=1).bits[0])
    if p.table == "input_registers":
        return p.decode(client.read_input_registers(p.index, count=1).registers[0])
    if p.table == "holding_registers":
        return p.decode(client.read_holding_registers(p.index, count=1).registers[0])
    raise ValueError(tag)


def write_coil(tag, value):
    client.write_coil(pm[tag].index, value)


def wait_until(predicate, timeout_s, description):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    print(f"[smoke] FAIL: timed out waiting for: {description}")
    sys.exit(1)


print("[smoke] phase 1: auto control — waiting for pump auto-start at SP_LVL_LO")
wait_until(lambda: read("P101_FB") is True, timeout_s=15, description="pump feedback True (auto start)")
print(f"[smoke]   pump started, level={read('LT_101'):.1f}%")

print("[smoke] phase 1: waiting for pump auto-stop at SP_LVL_HI")
wait_until(lambda: read("P101_FB") is False, timeout_s=30, description="pump feedback False (auto stop)")
level_at_stop = read("LT_101")
sp_hi = read("SP_LVL_HI")
print(f"[smoke]   pump stopped, level={level_at_stop:.1f}% (SP_LVL_HI={sp_hi:.1f}%)")
if level_at_stop < sp_hi - 2.0:
    print("[smoke] FAIL: pump stopped well below SP_LVL_HI")
    sys.exit(1)

print("[smoke] phase 2: S03 preview — unauthorized command, mode=manual, pump forced on")
write_coil("MODE_AUTO", False)
write_coil("P101_RUN", True)

wait_until(lambda: read("LT_101") >= 100.0, timeout_s=30, description="tank overflow (LT_101 >= 100%)")
print(f"[smoke]   overflow reached, lshh={read('LSHH_101')}, alarm={read('ALARM_HORN')}")
if not read("LSHH_101"):
    print("[smoke] FAIL: overflowed but LSHH_101 float did not trip")
    sys.exit(1)
if not read("ALARM_HORN"):
    print("[smoke] FAIL: overflowed but ALARM_HORN did not annunciate")
    sys.exit(1)

wait_until(lambda: read("P101_FAULT") is True, timeout_s=30, description="pump deadhead fault")
print(f"[smoke]   pump fault latched, current={read('IT_101'):.1f}A, fb={read('P101_FB')}")
if read("P101_FB"):
    print("[smoke] FAIL: pump feedback still True after fault")
    sys.exit(1)

client.close()
print("[smoke] PASS")
PYEOF
