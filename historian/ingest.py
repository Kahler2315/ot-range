"""Cedar Hollow historian ingest.

Polls OpenPLC's own Modbus slave interface — the same one hmi/app.py
reads, and the same one an HMI or an attacker would reach in a real
deployment — and appends one row per poll to Postgres. Addressing is
OpenPLC's located-variable mapping, not either plc/modbus-map*.yml file:
field I/O mirrors land at offset 100, control parameters at low
addresses (see docs/openplc-integration.md).

Deliberately not a subprocess of process_sim or the HMI: a real
historian is its own client of the PLC, on its own poll cadence, and
that's the honest shape to model here too.
"""

from __future__ import annotations

import logging
import os
import time

import psycopg
from pymodbus.client import ModbusTcpClient

LOG = logging.getLogger("ot_range.historian")

OPENPLC_HOST = os.environ.get("OPENPLC_HOST", "openplc")
OPENPLC_PORT = int(os.environ.get("OPENPLC_PORT", "502"))
POLL_INTERVAL_S = float(os.environ.get("HISTORIAN_POLL_INTERVAL_S", "5.0"))
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://historian:historian@postgres/historian")

# Field I/O, mirrored from process-sim at OpenPLC's slave-device offset
# of 100 — see hmi/app.py for the identical mapping and
# docs/openplc-integration.md for where it comes from.
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

COIL_MODE_AUTO = 0

SCALE_LEVEL = 100.0
SCALE_FLOW = 10.0
SCALE_CHLORINE = 100.0
SCALE_CURRENT = 10.0

INSERT_SQL = """
    INSERT INTO process_history
        (level_pct, flow_lpm, chlorine_mgl, pump_current_a,
         pump_run, valve_open, cl_run, lshh, lsll, pump_fault,
         alarm, mode_auto)
    VALUES
        (%(level_pct)s, %(flow_lpm)s, %(chlorine_mgl)s, %(pump_current_a)s,
         %(pump_run)s, %(valve_open)s, %(cl_run)s, %(lshh)s, %(lsll)s,
         %(pump_fault)s, %(alarm)s, %(mode_auto)s)
"""


def read_reading(client: ModbusTcpClient) -> dict:
    """One poll of OpenPLC's slave interface, decoded into a row-shaped
    dict. Raises on connection/protocol failure — the caller decides how
    to handle a failed poll, this function doesn't swallow it."""
    ir = client.read_input_registers(IR_LT_101, count=4).registers
    di = client.read_discrete_inputs(DI_LSHH_101, count=4).bits
    co_field = client.read_coils(COIL_P101_RUN, count=4).bits
    co_mode = client.read_coils(COIL_MODE_AUTO, count=1).bits[0]

    return {
        "level_pct": ir[0] / SCALE_LEVEL,
        "flow_lpm": ir[1] / SCALE_FLOW,
        "chlorine_mgl": ir[2] / SCALE_CHLORINE,
        "pump_current_a": ir[3] / SCALE_CURRENT,
        "lshh": bool(di[0]),
        "lsll": bool(di[1]),
        # di[2] is P101_FB, the pump running feedback — not stored;
        # pump_run (the commanded state) and pump_current_a already
        # cover what the historian needs to say whether the pump ran.
        "pump_fault": bool(di[3]),
        "pump_run": bool(co_field[0]),
        "valve_open": bool(co_field[1]),
        "cl_run": bool(co_field[2]),
        "alarm": bool(co_field[3]),
        "mode_auto": bool(co_mode),
    }


def insert_reading(conn: psycopg.Connection, reading: dict) -> None:
    """Write one reading. psycopg's named-parameter binding only pulls
    the keys INSERT_SQL actually references, so a dict with extra keys
    (or missing ones matched to defaults elsewhere) wouldn't break this
    — but read_reading's output is expected to match the columns
    exactly, exercised end-to-end against a real stack in
    tests/test_historian.py."""
    with conn.cursor() as cur:
        cur.execute(INSERT_SQL, reading)
    conn.commit()


def poll_forever(client: ModbusTcpClient, conn: psycopg.Connection) -> None:
    while True:
        try:
            if not client.connected and not client.connect():
                raise ConnectionError(f"could not connect to {OPENPLC_HOST}:{OPENPLC_PORT}")
            reading = read_reading(client)
            insert_reading(conn, reading)
        except Exception:
            LOG.exception("poll failed")
            client.close()
        time.sleep(POLL_INTERVAL_S)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    LOG.info("connecting to %s", DATABASE_URL.split("@")[-1])
    conn = psycopg.connect(DATABASE_URL, autocommit=False)
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as fh:
        conn.execute(fh.read())
    conn.commit()

    client = ModbusTcpClient(OPENPLC_HOST, port=OPENPLC_PORT)
    LOG.info("polling %s:%d every %ss", OPENPLC_HOST, OPENPLC_PORT, POLL_INTERVAL_S)
    poll_forever(client, conn)


if __name__ == "__main__":
    main()
