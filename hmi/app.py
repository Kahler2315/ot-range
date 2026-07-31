"""Cedar Hollow HMI — a minimal operator display.

Polls OpenPLC's own Modbus slave interface directly (the same port an
HMI or an attacker would reach in a real deployment) — not process-sim.
Addressing here is OpenPLC's located-variable mapping, not either of the
plc/modbus-map*.yml files: field I/O mirrors land at offset 100 (see
docs/openplc-integration.md), control parameters at low addresses.

Placeholder for FUXA (see docs/architecture.md open question #1) — built
because driving FUXA's SVG-based editor wasn't something this session
could do reliably without visual/screenshot access. Purpose-built here to
be simple enough to verify by reading its output as text.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template
from pymodbus.client import ModbusTcpClient

LOG = logging.getLogger("ot_range.hmi")

OPENPLC_HOST = os.environ.get("OPENPLC_HOST", "openplc")
OPENPLC_PORT = int(os.environ.get("OPENPLC_PORT", "502"))
POLL_INTERVAL_S = float(os.environ.get("HMI_POLL_INTERVAL_S", "1.0"))

# Field I/O, mirrored from process-sim at OpenPLC's slave-device offset
# of 100 (docs/openplc-integration.md). Discrete/coil bit addresses are
# 100*8=800 + index; register addresses are 100 + index.
IR_LT_101 = 100
IR_FT_201 = 101
IR_AIT_301 = 102
IR_IT_101 = 103

DI_LSHH_101 = 800
DI_LSLL_101 = 801
DI_P101_FB = 802
DI_P101_FAULT = 803

COIL_P101_RUN = 800
COIL_V201_OPEN = 801
COIL_CL301_RUN = 802
COIL_ALARM_HORN = 803

# Controller-only — no field wire, OpenPLC's own low addresses.
COIL_MODE_AUTO = 0
HR_SP_LVL_HI = 0
HR_SP_LVL_LO = 1
HR_SP_ALM_HH = 2

# Per-point scale, NOT uniform — matches plc/modbus-map-field.yml exactly
# (LT_101/AIT_301 are x100, FT_201/IT_101 are x10). Caught by hand during
# verification: pump current displayed as 3.1A instead of ~31A because an
# earlier version of this file used one SCALE constant for all four.
SCALE_LEVEL = 100.0
SCALE_FLOW = 10.0
SCALE_CHLORINE = 100.0
SCALE_CURRENT = 10.0
SCALE_SETPOINT = 100.0

app = Flask(__name__)

_state_lock = threading.Lock()
_state: dict = {
    "connected": False,
    "level_pct": None,
    "flow_lpm": None,
    "chlorine_mgl": None,
    "pump_current_a": None,
    "lshh": None,
    "lsll": None,
    "pump_fb": None,
    "pump_fault": None,
    "pump_run": None,
    "valve_open": None,
    "cl_run": None,
    "alarm": None,
    "mode_auto": None,
    "sp_lvl_hi": None,
    "sp_lvl_lo": None,
    "sp_alm_hh": None,
    "last_update": None,
}


def _poll_loop() -> None:
    client = ModbusTcpClient(OPENPLC_HOST, port=OPENPLC_PORT)
    while True:
        try:
            if not client.connected and not client.connect():
                raise ConnectionError(f"could not connect to {OPENPLC_HOST}:{OPENPLC_PORT}")

            ir = client.read_input_registers(IR_LT_101, count=4).registers
            di = client.read_discrete_inputs(DI_LSHH_101, count=4).bits
            co_field = client.read_coils(COIL_P101_RUN, count=4).bits
            co_mode = client.read_coils(COIL_MODE_AUTO, count=1).bits[0]
            hr = client.read_holding_registers(HR_SP_LVL_HI, count=3).registers

            with _state_lock:
                _state.update(
                    connected=True,
                    level_pct=ir[0] / SCALE_LEVEL,
                    flow_lpm=ir[1] / SCALE_FLOW,
                    chlorine_mgl=ir[2] / SCALE_CHLORINE,
                    pump_current_a=ir[3] / SCALE_CURRENT,
                    lshh=bool(di[0]),
                    lsll=bool(di[1]),
                    pump_fb=bool(di[2]),
                    pump_fault=bool(di[3]),
                    pump_run=bool(co_field[0]),
                    valve_open=bool(co_field[1]),
                    cl_run=bool(co_field[2]),
                    alarm=bool(co_field[3]),
                    mode_auto=bool(co_mode),
                    sp_lvl_hi=hr[0] / SCALE_SETPOINT,
                    sp_lvl_lo=hr[1] / SCALE_SETPOINT,
                    sp_alm_hh=hr[2] / SCALE_SETPOINT,
                    last_update=time.time(),
                )
        except Exception as exc:  # noqa: BLE001 -- poll loop must never die
            LOG.warning("poll failed: %s", exc)
            with _state_lock:
                _state["connected"] = False
            client.close()

        time.sleep(POLL_INTERVAL_S)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    with _state_lock:
        return jsonify(dict(_state))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    threading.Thread(target=_poll_loop, daemon=True).start()
    # 0.0.0.0 is correct here, not a loopback-only violation: this binds
    # inside the container's own network namespace so docker-compose can
    # publish/route to it (same reasoning as process_sim/Dockerfile).
    # nosemgrep: python.flask.security.audit.app-run-param-config.avoid_app_run_with_bad_host
    app.run(host="0.0.0.0", port=8090)  # nosec B104


if __name__ == "__main__":
    main()
