#!/usr/bin/env python3
"""Stand-in HMI poller.

Polls the plant the way a real HMI would: the same points, on a fixed
interval, forever. Two uses — it generates the known-clean traffic the
detection baseline is learned from, and it provides the steady background
polling that attack traffic has to hide inside.

Replaced by the real FUXA HMI at M2; the polling pattern stays the same.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from pymodbus.client import ModbusTcpClient

from common.pointmap import load as load_pointmap

DEFAULT_HOST = os.environ.get("MODBUS_BIND_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("MODBUS_BIND_PORT", "5502"))

# What an HMI screen for this plant actually needs, and nothing else.
POLLED_TAGS = (
    "LT_101",
    "FT_201",
    "AIT_301",
    "IT_101",
    "LSHH_101",
    "LSLL_101",
    "P101_FB",
    "P101_FAULT",
    "P101_RUN",
    "V201_OPEN",
    "CL301_RUN",
    "ALARM_HORN",
    "MODE_AUTO",
    "SP_LVL_HI",
    "SP_LVL_LO",
    "SP_CL_DOSE",
    "SP_ALM_HH",
    "SP_P101_SPD",
)


def poll_once(client: ModbusTcpClient, pm) -> dict[str, float | bool]:
    values: dict[str, float | bool] = {}
    for tag in POLLED_TAGS:
        p = pm[tag]
        if p.table == "coils":
            values[tag] = bool(client.read_coils(p.index, count=1).bits[0])
        elif p.table == "discrete_inputs":
            values[tag] = bool(client.read_discrete_inputs(p.index, count=1).bits[0])
        elif p.table == "input_registers":
            values[tag] = p.decode(client.read_input_registers(p.index, count=1).registers[0])
        elif p.table == "holding_registers":
            values[tag] = p.decode(client.read_holding_registers(p.index, count=1).registers[0])
    return values


def run(host: str, port: int, source_ip: str | None, interval: float, cycles: int) -> int:
    pm = load_pointmap()
    source_address = (source_ip, 0) if source_ip else None
    client = ModbusTcpClient(host, port=port, source_address=source_address)
    if not client.connect():
        print(f"[!] could not connect to {host}:{port}", file=sys.stderr)
        return 1

    try:
        count = 0
        while cycles <= 0 or count < cycles:
            values = poll_once(client, pm)
            count += 1
            if cycles <= 0 or count == cycles:
                print(
                    f"cycle {count}: LT_101={values['LT_101']:.1f}% "
                    f"P101_FB={values['P101_FB']} ALARM_HORN={values['ALARM_HORN']}",
                    flush=True,
                )
            time.sleep(interval)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Bind this source address, so the sensor can tell the HMI from other clients",
    )
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--cycles", type=int, default=0, help="Poll cycles to run (0 = forever)")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return run(args.host, args.port, args.source_ip, args.interval, args.cycles)


if __name__ == "__main__":
    sys.exit(main())
