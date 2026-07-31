#!/usr/bin/env python3
"""S01 — Exposed device discovery and point enumeration.

Models the reconnaissance that precedes every documented OT intrusion:
find something answering on the Modbus port, work out which unit IDs
respond, then sweep register ranges to build a point map. No process
impact — that is the teaching point. Nothing alarms, nothing moves, and
the operator sees nothing.

ATT&CK for ICS: T0846 Remote System Discovery, T0861 Point & Tag
Identification, T0885 Commonly Used Port.

Simulated range only. See SECURITY.md.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from attacker.common.scope import guard

SCENARIO = "S01 — exposed device discovery and point enumeration"
DEFAULT_PORT = 5502


def probe_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Is anything listening? The cheapest possible first question."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def walk_unit_ids(client: ModbusTcpClient, unit_ids: range, delay: float) -> list[int]:
    """Find which unit IDs answer. Sequential probing is itself a signature."""
    responding = []
    for unit_id in unit_ids:
        try:
            result = client.read_holding_registers(0, count=1, slave=unit_id)
            if not result.isError():
                responding.append(unit_id)
                print(f"  unit id {unit_id:>3} : responds")
        except ModbusException:
            pass
        time.sleep(delay)
    return responding


def sweep_registers(
    client: ModbusTcpClient, unit_id: int, table: str, start: int, count: int, delay: float
) -> dict[int, int]:
    """Read a register range one at a time to find which addresses exist.

    Reading singly rather than in blocks is deliberately noisy — it is what
    an attacker without a point map does, and it produces the exception
    -rate spike the detection looks for.
    """
    readers = {
        "coils": client.read_coils,
        "discrete_inputs": client.read_discrete_inputs,
        "input_registers": client.read_input_registers,
        "holding_registers": client.read_holding_registers,
    }
    read = readers[table]
    found: dict[int, int] = {}
    for addr in range(start, start + count):
        try:
            result = read(addr, count=1, slave=unit_id)
            if not result.isError():
                value = (
                    int(result.bits[0])
                    if table in ("coils", "discrete_inputs")
                    else result.registers[0]
                )
                found[addr] = value
        except ModbusException:
            pass
        time.sleep(delay)
    return found


def run(
    host: str,
    port: int,
    unit_id_max: int,
    sweep_count: int,
    delay: float,
    source_ip: str | None = None,
) -> int:
    target = guard(host, port, SCENARIO)

    print(f"[*] probing {target.connect_host}:{target.port}")
    if not probe_port(target.connect_host, target.port):
        print(f"[!] nothing listening on {target.connect_host}:{target.port}")
        return 1
    print(f"[+] port {target.port} open — something is speaking Modbus TCP here")

    source_address = (source_ip, 0) if source_ip else None
    client = ModbusTcpClient(target.connect_host, port=target.port, source_address=source_address)
    if not client.connect():
        print("[!] could not establish a Modbus session")
        return 1

    try:
        print(f"[*] walking unit ids 0..{unit_id_max}")
        responding = walk_unit_ids(client, range(unit_id_max + 1), delay)
        if not responding:
            print("[!] no unit ids responded")
            return 1
        print(f"[+] {len(responding)} unit id(s) responding: {responding}")

        unit_id = responding[0]
        print(f"[*] sweeping register ranges on unit {unit_id}")
        point_map: dict[str, dict[int, int]] = {}
        for table in ("coils", "discrete_inputs", "input_registers", "holding_registers"):
            found = sweep_registers(client, unit_id, table, 0, sweep_count, delay)
            point_map[table] = found
            print(f"  {table:<18} {len(found):>3} readable address(es)")

        print("\n[+] reconstructed point map:")
        for table, found in point_map.items():
            if not found:
                continue
            print(f"  {table}")
            for addr, value in sorted(found.items()):
                print(f"    [{addr}] = {value}")

        print(
            "\n[i] No process impact. Nothing alarmed, nothing moved.\n"
            "    That is the lesson: quiet activity precedes loud activity."
        )
        return 0
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--unit-id-max", type=int, default=5, help="Highest unit ID to probe")
    parser.add_argument(
        "--sweep-count", type=int, default=8, help="Addresses to sweep per register table"
    )
    parser.add_argument("--delay", type=float, default=0.01, help="Delay between probes, seconds")
    parser.add_argument(
        "--source-ip",
        default=None,
        help="Bind this source address, so the attacker is distinguishable from the HMI",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    return run(
        args.host,
        args.port,
        args.unit_id_max,
        args.sweep_count,
        args.delay,
        args.source_ip,
    )


if __name__ == "__main__":
    sys.exit(main())
